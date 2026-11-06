"""Importação aditiva das exportações CSV da V2 publicada."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .database import backup_database, connect, initialize

FILES = {
    "funcionarios_ativos.csv": "in_active_export",
    "funcionarios_desligados.csv": "in_terminated_export",
    "admitidos_periodo.csv": "in_admitted_export",
}


def _iso(value: str) -> str | None:
    value = value.strip()
    if not value or value == "—":
        return None
    return datetime.strptime(value, "%d/%m/%Y").date().isoformat()


def import_exports(database: Path, directory: Path) -> dict[str, int]:
    """Importa linhas sem substituir o banco ou remover registros existentes."""
    initialize(database)
    paths = [(directory / name, flag) for name, flag in FILES.items()]
    missing = [str(path) for path, _ in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Arquivos ausentes: " + ", ".join(missing))
    # A cópia SQLite é consistente mesmo se o servidor estiver conectado em WAL.
    backup_database(database, "antes-importacao")
    counts: dict[str, int] = {}
    with connect(database) as db:
        for path, flag in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if db.execute("SELECT 1 FROM v2_import_log WHERE source_name=? AND source_sha256=?", (path.name, digest)).fetchone():
                counts[path.name] = 0
                continue
            count = 0
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle, delimiter=";"):
                    registration = row["Código"].strip()
                    name = row["Nome"].strip()
                    company = row["Cliente"].strip()
                    admission = _iso(row["Admissão"])
                    termination = _iso(row["Desligamento"])
                    if not name or not company:
                        continue
                    key = hashlib.sha256("\x1f".join((registration, name, company, admission or "")).encode()).hexdigest()
                    db.execute(
                        f"""INSERT INTO v2_employments
                        (external_key,registration,name,company,department,job_title,contract_type,
                         admission_date,termination_date,termination_reason_code,termination_reason,{flag})
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,1)
                        ON CONFLICT(external_key) DO UPDATE SET
                            {flag}=1,
                            termination_date=COALESCE(excluded.termination_date,v2_employments.termination_date),
                            termination_reason_code=CASE WHEN excluded.termination_reason_code!='' THEN excluded.termination_reason_code ELSE v2_employments.termination_reason_code END,
                            termination_reason=CASE WHEN excluded.termination_reason!='' THEN excluded.termination_reason ELSE v2_employments.termination_reason END""",
                        (key, registration, name, company, row["Departamento"].strip(), row["Função"].strip(),
                         row["Tipo de contrato"].strip(), admission, termination,
                         row.get("Código do motivo", "").strip(), row.get("Descrição do motivo", "").strip()),
                    )
                    count += 1
            db.execute("INSERT INTO v2_import_log(source_name,source_sha256,row_count) VALUES(?,?,?)", (path.name,digest,count))
            counts[path.name] = count
    return counts


def import_mirror(database: Path, directory: Path) -> dict[str, int]:
    """Adiciona snapshots das tabelas operacionais da V2 sem excluir dados locais."""
    initialize(database)
    counts: dict[str, int] = {}
    with connect(database) as db:
        for path in sorted(directory.glob("*.jsonl")):
            count = 0
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    source_id = str(row.get("id") or row.get("client_id") or hashlib.sha256(line.encode()).hexdigest())
                    db.execute("INSERT INTO v2_source_rows(table_name,source_id,payload) VALUES(?,?,?) "
                               "ON CONFLICT(table_name,source_id) DO UPDATE SET payload=excluded.payload",
                               (path.stem, source_id, json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
                    count += 1
            counts[path.stem] = count
    return counts
