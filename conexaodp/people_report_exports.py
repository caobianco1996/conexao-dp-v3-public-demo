"""Private, bounded background exports of filtered local reports to XLSX."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs
from uuid import uuid4

from .database import connect
from .people_analytics import get_data
from .people_reports import ADMIN_COLUMN_KEYS, SENSITIVE

ROOT = Path(__file__).resolve().parents[1]
TTL = 10 * 60


def create_workbook(data, output, *, preview_dir=None):
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    node = os.environ.get("V3_NODE") or shutil.which("node") or str(bundled / "bin/node.exe")
    modules = Path(os.environ.get("V3_ARTIFACT_MODULES") or bundled / "node_modules")
    if not Path(node).is_file() or not (modules / "@oai/artifact-tool/package.json").is_file():
        raise RuntimeError("O motor de Excel não está disponível nesta instalação. Configure V3_NODE e V3_ARTIFACT_MODULES.")
    environment = os.environ.copy()
    environment["V3_ARTIFACT_MODULES"] = str(modules)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = [node, str(ROOT / "scripts/export_people_report.mjs"), str(output)]
    if preview_dir:
        command.extend(["--preview", str(preview_dir)])
    result = subprocess.run(command, input=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                            cwd=ROOT, timeout=180, creationflags=flags)
    if result.returncode or not Path(output).is_file():
        # Never reflect workbook contents, process output or personal data in errors.
        raise RuntimeError("Não foi possível gerar a planilha Excel. Tente emitir novamente.")


class ReportExports:
    def __init__(self, database):
        self.database = Path(database)
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.jobs = {}

    def _cleanup(self):
        now = time.time()
        for key in list(self.jobs):
            if self.jobs[key]["expires"] < now:
                del self.jobs[key]

    def start(self, user, query):
        if not isinstance(query, str) or len(query) > 32768:
            raise ValueError("A seleção de filtros é inválida ou muito extensa.")
        params = parse_qs(query, keep_blank_values=True)
        report = (params.get("report") or [""])[0]
        if not report:
            raise ValueError("Escolha um relatório para exportar.")
        requires_admin = report in SENSITIVE or bool(set(params.get("reportColumns", [])) & ADMIN_COLUMN_KEYS)
        from .permissions import can
        if not can(user, 'analytics.export') or requires_admin and not can(user, 'sensitive.view'):
            raise PermissionError("Este relatório exige acesso administrativo.")
        with self.lock:
            self._cleanup()
            for key, job in self.jobs.items():
                if job["owner"] == user["id"] and job["state"] == "preparing":
                    if job["query"] == query:
                        return {"id": key, "state": job["state"]}
                    raise ValueError("Aguarde a exportação atual terminar antes de gerar outra planilha.")
            if len(self.jobs) >= 24 or not self.slots.acquire(blocking=False):
                raise ValueError("Há outras planilhas sendo preparadas. Tente novamente em instantes.")
            key = uuid4().hex
            self.jobs[key] = {"owner": int(user["id"]), "report": report, "query": query, "requires_admin": requires_admin,
                              "state": "preparing", "expires": time.time() + TTL, "error": "",
                              "access": (user.get('accessProfile', user['role']), user.get('accessScope', 'ALL'), tuple(user.get('permissions', [])))}
        threading.Thread(target=self._run, args=(key, params), daemon=True, name="V3-relatorio-excel").start()
        return {"id": key, "state": "preparing"}

    def _run(self, key, params):
        try:
            with self.lock:
                owner = self.jobs[key]["owner"]
            with connect(self.database) as db:
                db.execute("BEGIN")
                user = db.execute("SELECT id,role FROM users WHERE id=? AND active=1", (owner,)).fetchone()
                if not user:
                    raise PermissionError("A conta não tem mais acesso a este relatório.")
                from .permissions import enrich_user, can
                user = enrich_user(db, user)
                if not can(user, 'analytics.export'):
                    raise PermissionError('A permissão de exportação foi retirada desta conta.')
                data = get_data(db, params, user=user, report_export_all=True)
            report = data["reports"]["selected"]
            if not report or report["pending"]:
                raise ValueError(report["pending"] if report else "Escolha um relatório válido.")
            if report["total"] > 1_048_570:
                raise ValueError("A seleção ultrapassa o limite de linhas do Excel. Reduza a faixa ou selecione empresas.")
            folder = ROOT / "dados/private-exports"
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="xlsx-", dir=folder) as temporary:
                path = Path(temporary) / "relatorio.xlsx"
                create_workbook(data, path)
                content = path.read_bytes()
            with self.lock:
                if key in self.jobs:
                    self.jobs[key].update(state="ready", content=content, rows=report["total"],
                                          filename=f"relatorio_{report['id']}_{data['filters']['end']}.xlsx",
                                          expires=time.time() + TTL)
        except (ValueError, PermissionError, RuntimeError) as error:
            with self.lock:
                if key in self.jobs: self.jobs[key].update(state="error", error=str(error))
        except Exception:
            with self.lock:
                if key in self.jobs: self.jobs[key].update(state="error", error="A exportação não foi concluída. Tente novamente com a seleção desejada.")
        finally:
            self.slots.release()

    def get(self, key, user, *, download=False):
        with self.lock:
            self._cleanup()
            job = self.jobs.get(key)
            if not job or job["owner"] != user["id"]:
                raise LookupError("A exportação expirou ou não pertence a esta conta. Gere o arquivo novamente.")
            from .permissions import can
            access = (user.get('accessProfile', user['role']), user.get('accessScope', 'ALL'), tuple(user.get('permissions', [])))
            if job.get('access') != access:
                raise PermissionError('Seu acesso mudou. Gere novamente o relatório com as permissões atuais.')
            if not can(user, 'analytics.export') or job["requires_admin"] and not can(user, 'sensitive.view'):
                raise PermissionError("Este relatório exige acesso administrativo.")
            if download:
                if job["state"] != "ready": raise ValueError("O arquivo Excel ainda não está pronto.")
                return job["filename"], job["content"]
            return {"id": key, "state": job["state"], "error": job["error"],
                    "rows": job.get("rows"), "filename": job.get("filename")}
