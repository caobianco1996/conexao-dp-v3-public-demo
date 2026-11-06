"""Tratamento auditável das consultas SIB que não foram individualizadas."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

from .integrations import IntegrationError
from .sib_scan import _admin, _upsert
from .sib_record_mapping import map_raw_employee
from .sib_runtime import lease_active, worker_token

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_issue_reviews (
 cycle TEXT NOT NULL, payroll_id TEXT NOT NULL, status TEXT NOT NULL,
 reason_code TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', identity_json TEXT NOT NULL DEFAULT '{}',
 attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(cycle,payroll_id)
);
CREATE TABLE IF NOT EXISTS local_sib_issue_attempts (
 id INTEGER PRIMARY KEY, cycle TEXT NOT NULL, payroll_id TEXT NOT NULL,
 action TEXT NOT NULL, outcome TEXT NOT NULL, http_status INTEGER NOT NULL,
 code TEXT NOT NULL, message TEXT NOT NULL, request_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 actor_id INTEGER NOT NULL REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS sib_issue_attempt_lookup ON local_sib_issue_attempts(cycle,payroll_id,id);
CREATE TABLE IF NOT EXISTS local_sib_issue_job (
 id INTEGER PRIMARY KEY CHECK(id=1), cycle TEXT NOT NULL, token TEXT NOT NULL,
 running INTEGER NOT NULL, lease_until REAL NOT NULL, queue_json TEXT NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
 resolved INTEGER NOT NULL DEFAULT 0, identified INTEGER NOT NULL DEFAULT 0,
 unresolved INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 actor_id INTEGER NOT NULL REFERENCES users(id)
);
"""
LABELS = {"OPEN": "Aguardando diagnóstico", "NEEDS_SOURCE": "Consulta inconclusiva",
          "IDENTIFIED": "Identidade localizada; ficha pendente", "RESOLVED": "Cadastro importado",
          "NOT_FOUND": "Ausência confirmada", "ERROR": "Falha de comunicação"}
LEASE_SECONDS = 180


class _PauseRequested(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _cycle(db):
    row = db.execute("SELECT cycle,source_url FROM local_sib_scan_state WHERE id=1").fetchone()
    return row


def job_summary(db):
    row = db.execute("SELECT * FROM local_sib_issue_job WHERE id=1").fetchone()
    active = bool(row and lease_active(row["token"], row["lease_until"]))
    return {"running": bool(active and row["running"]), "inFlight": active,
            **{key: row[key] if row else 0 for key in ("processed", "total", "resolved", "identified", "unresolved")},
            "error": row["error"] if row else ""}


def _view(payroll, reason, review=None):
    status = review["status"] if review else "OPEN"
    code = review["reason_code"] if review else reason
    evidence = json.loads(review["evidence_json"]) if review else {
        "origin": "Bridge · consulta agrupada", "httpStatus": 200,
        "code": reason, "message": "A Bridge não retornou uma ficha. Esse código, sozinho, não distingue ausência de cadastro de mais de um resultado.", "duplicateConfirmed": False}
    return {"payrollId": payroll, "status": status, "statusLabel": LABELS.get(status, status),
            "reasonCode": code, "reasonLabel": evidence.get("message", "Consulta inconclusiva."),
            "confirmed": status in ("RESOLVED", "NOT_FOUND"), "attempts": review["attempts"] if review else 0,
            "lastAttemptAt": review["updated_at"] if review else "", "source": evidence.get("origin", "Bridge"),
            "evidence": evidence, "identity": json.loads(review["identity_json"]) if review else {}, "canImport": False}


def list_issues(db, *, page=1, status="open", query=""):
    context = _cycle(db)
    if not context:
        return {"items": [], "page": 1, "pages": 1, "pageSize": 50, "total": 0, "job": job_summary(db)}
    original = {r["payroll_id"]: r["reason"] for r in db.execute("SELECT * FROM local_sib_scan_issues WHERE cycle=?", (context["cycle"],))}
    reviews = {r["payroll_id"]: r for r in db.execute("SELECT * FROM local_sib_issue_reviews WHERE cycle=?", (context["cycle"],))}
    items = []
    for payroll in sorted(set(original) | set(reviews), key=lambda value: int(value)):
        if query and payroll != query:
            continue
        item = _view(payroll, original.get(payroll, ""), reviews.get(payroll))
        resolved = item["status"] in ("RESOLVED", "NOT_FOUND")
        if (status == "resolved" and not resolved) or (status == "open" and resolved):
            continue
        # Personal data and full diagnostics are returned only by the detail endpoint.
        items.append({key: item[key] for key in ("payrollId", "status", "statusLabel", "reasonCode", "reasonLabel", "confirmed", "attempts", "lastAttemptAt")})
    pages = max(1, math.ceil(len(items) / 50))
    page = min(max(int(page), 1), pages)
    return {"items": items[(page-1)*50:page*50], "page": page, "pages": pages, "pageSize": 50, "total": len(items), "job": job_summary(db)}


def detail(db, payroll):
    if not str(payroll).isdigit() or len(str(payroll)) > 6:
        raise ValueError("Informe uma matrícula válida.")
    payroll = str(int(payroll))
    context = _cycle(db)
    if not context:
        raise LookupError("Não há ocorrências nesta leitura.")
    original = db.execute("SELECT reason FROM local_sib_scan_issues WHERE cycle=? AND payroll_id=?", (context["cycle"], payroll)).fetchone()
    review = db.execute("SELECT * FROM local_sib_issue_reviews WHERE cycle=? AND payroll_id=?", (context["cycle"], payroll)).fetchone()
    if not original and not review:
        raise LookupError("A matrícula não está na lista de ocorrências desta leitura.")
    history = [dict(row) for row in db.execute("SELECT created_at AS createdAt,action,outcome,http_status AS httpStatus,code,message,request_id AS requestId FROM local_sib_issue_attempts WHERE cycle=? AND payroll_id=? ORDER BY id DESC LIMIT 30", (context["cycle"], payroll))]
    return {"item": _view(payroll, original[0] if original else "", review), "history": history}


def _context(db, client, actor_id):
    _admin(db, actor_id)
    context = _cycle(db)
    settings = db.execute("SELECT sib_active,sib_base_url FROM local_integration_settings WHERE id=1").fetchone()
    if not context or not settings or not settings["sib_active"] or context["source_url"] != client.config.base_url or settings["sib_base_url"] != client.config.base_url:
        raise ValueError("A origem SIB mudou. Atualize a tela antes de tratar a ocorrência.")
    return context


def control(database, client, actor_id, payload):
    from .database import connect, backup_database
    action = payload.get("action")
    if action not in ("retry", "retry-open", "pause", "identify"):
        raise ValueError("Ação de tratamento inválida.")
    token = worker_token()
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        _admin(db, actor_id)
        if action == "pause":
            db.execute("UPDATE local_sib_issue_job SET running=0 WHERE id=1")
            return {"ok": True, "message": "Parada solicitada; a consulta ativa será registrada.", "job": job_summary(db)}
        context = _context(db, client, actor_id)
        if job_summary(db)["inFlight"]:
            raise ValueError("Aguarde a consulta em andamento ou pare o tratamento atual.")
        scan = db.execute("SELECT continuous,lease_token,lease_until FROM local_sib_scan_state WHERE id=1").fetchone()
        if scan and (scan["continuous"] or lease_active(scan["lease_token"], scan["lease_until"])):
            raise ValueError("Pare a busca de cadastros antes de reconsultar as ocorrências.")
        discovery = db.execute("SELECT lease_token,lease_until FROM local_sib_discovery_state WHERE id=1").fetchone()
        if discovery and lease_active(discovery["lease_token"], discovery["lease_until"]):
            raise ValueError("Aguarde a consulta incremental de novos cadastros antes de reconsultar as ocorrências.")
        refresh = db.execute("SELECT lease_token,lease_until FROM local_sib_refresh_state WHERE id=1").fetchone()
        if refresh and lease_active(refresh["lease_token"], refresh["lease_until"]):
            raise ValueError("Aguarde a atualização dos cadastros antes de reconsultar as ocorrências.")
        if action == "retry-open":
            ids = [r[0] for r in db.execute("""WITH ids AS (
                SELECT payroll_id FROM local_sib_scan_issues WHERE cycle=? UNION
                SELECT payroll_id FROM local_sib_issue_reviews WHERE cycle=?)
                SELECT i.payroll_id FROM ids i LEFT JOIN local_sib_issue_reviews r ON r.payroll_id=i.payroll_id AND r.cycle=?
                WHERE COALESCE(r.status,'OPEN') NOT IN ('RESOLVED','NOT_FOUND')
                ORDER BY COALESCE(r.updated_at,''),CAST(i.payroll_id AS INTEGER) LIMIT 50""", (context["cycle"],)*3)]
        else:
            ids = [detail(db, str(payload.get("payrollId") or ""))["item"]["payrollId"]]
        if not ids:
            return {"ok": True, "message": "Não há ocorrências abertas para reconsultar.", "job": job_summary(db)}
        identity = {key: str(payload.get(key) or "") for key in ("cpf", "admissionDate", "takerCnpj")} if action == "identify" else None
        db.execute("""INSERT INTO local_sib_issue_job(id,cycle,token,running,lease_until,queue_json,total,actor_id)
            VALUES(1,?,?,1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cycle=excluded.cycle,token=excluded.token,
            running=1,lease_until=excluded.lease_until,queue_json=excluded.queue_json,processed=0,total=excluded.total,
            resolved=0,identified=0,unresolved=0,error='',actor_id=excluded.actor_id""",
            (context["cycle"], token, time.time()+LEASE_SECONDS, _json(ids), len(ids), actor_id))
    try:
        backup_database(database, "antes-tratar-consultas-sib")
        thread = threading.Thread(target=_worker, args=(Path(database), client, actor_id, token, identity), daemon=True, name="SIB-ocorrencias")
        thread.start()
    except Exception:
        with connect(database) as db:
            db.execute("UPDATE local_sib_issue_job SET running=0,token='',lease_until=0 WHERE token=?", (token,))
        raise
    with connect(database) as db:
        return {"ok": True, "message": "Tratamento iniciado. Cada resposta e tentativa ficará disponível nesta tela.", "job": job_summary(db)}


def _owned(db, token):
    row = db.execute("SELECT * FROM local_sib_issue_job WHERE id=1 AND token=?", (token,)).fetchone()
    if not row:
        raise RuntimeError("Este tratamento foi substituído.")
    return row


def _attempt(db, cycle, payroll, actor, action, outcome, status, code, message, request_id=""):
    db.execute("INSERT INTO local_sib_issue_attempts(cycle,payroll_id,action,outcome,http_status,code,message,actor_id,request_id) VALUES(?,?,?,?,?,?,?,?,?)",
               (cycle, payroll, action, outcome, int(status), code[:80], message[:300], actor, request_id[:80]))


def _renew(database, token, client):
    from .database import connect
    with connect(database) as db:
        job = _owned(db, token)
        if not job["running"]:
            raise _PauseRequested()
        if _context(db, client, job["actor_id"])["cycle"] != job["cycle"]:
            raise ValueError("O ciclo mudou durante a consulta.")
        db.execute("UPDATE local_sib_issue_job SET lease_until=? WHERE token=?", (time.time()+LEASE_SECONDS, token))


def _save_attempt(database, token, payroll, attempt):
    from .database import connect
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        job = _owned(db, token)
        _admin(db, job["actor_id"])
        if _cycle(db)["cycle"] != job["cycle"]:
            raise ValueError("O ciclo mudou durante a consulta.")
        _attempt(db, job["cycle"], payroll, job["actor_id"], *attempt)
        evidence = {"origin": "Bridge · "+attempt[0], "httpStatus": attempt[2], "code": attempt[3],
                    "message": attempt[4], "requestId": attempt[5] if len(attempt)>5 else "", "duplicateConfirmed": False}
        db.execute("""INSERT INTO local_sib_issue_reviews(cycle,payroll_id,status,reason_code,evidence_json,attempts)
            VALUES(?,?,'OPEN',?,?,1) ON CONFLICT(cycle,payroll_id) DO UPDATE SET
            evidence_json=excluded.evidence_json,attempts=local_sib_issue_reviews.attempts+1,updated_at=CURRENT_TIMESTAMP""",
            (job["cycle"], payroll, attempt[3], _json(evidence)))


def _identity(payload):
    from .integrations import _identity_date
    employee, company = payload.get("employee", {}), payload.get("client") or {}
    cpf = str(employee.get("cpf") or "")
    return {"name": str(employee.get("name") or ""), "cpfMasked": ("***.***.***-"+cpf[-2:]) if cpf else "",
            "company": str(company.get("legal_name") or ""), "admissionDate": _identity_date(employee.get("admission_date"))}


def _diagnose(database, client, actor, token, payroll, manual_identity):
    from .database import connect
    from .sib_identity_hints import find_identity_hint
    attempts, identity, items = [], {}, None
    def remember(attempt):
        attempts.append(attempt)
        _save_attempt(database, token, payroll, attempt)
    with connect(database) as db:
        job = _owned(db, token)
        context = _context(db, client, actor)
        if job["cycle"] != context["cycle"]:
            raise ValueError("O ciclo SIB mudou durante o tratamento.")
        hint = find_identity_hint(db, payroll)
    manually_confirmed = None
    if manual_identity:
        _renew(database, token, client)
        try:
            manually_confirmed = client.identify_employee(payroll, cpf=manual_identity["cpf"], admission_date=manual_identity["admissionDate"], taker_cnpj=manual_identity["takerCnpj"])
            remember(("IDENTIFICACAO_MANUAL", "OK", 200, "IDENTIDADE_CONFIRMADA", "CPF, admissão e CNPJ completo confirmados na Bridge."))
        except (ValueError, IntegrationError) as error:
            remember(("IDENTIFICACAO_MANUAL", "ERRO", getattr(error, "status_code", 0), "IDENTIDADE_NAO_CONFIRMADA", str(error), getattr(error, "request_id", "")))
            raise
    try:
        _renew(database, token, client)
        payload = client.get_employee_raw(payroll)
        items = map_raw_employee(payload, payroll)
        if manually_confirmed:
            from .integrations import _identity_date
            expected_cpf = re.sub(r"\D", "", manual_identity["cpf"])
            expected_cnpj = re.sub(r"\D", "", manual_identity["takerCnpj"])
            if (items[0]["data"]["cpf"] != expected_cpf or items[0]["data"]["admissionDate"] != _identity_date(manual_identity["admissionDate"])
                    or not items[1] or items[1]["data"]["cnpj"] != expected_cnpj):
                raise ValueError("A ficha completa não corresponde à identidade informada. Nenhum cadastro foi importado.")
        remember(("CONSULTA_DIRETA", "OK", 200, "REGISTRO_UNICO", "A consulta individual devolveu uma ficha com chave de origem completa."))
        identity = _identity(payload)
        state, code, message = "RESOLVED", "REGISTRO_IMPORTADO", "Ficha completa importada para Pessoas, Empresas e Vínculos."
    except IntegrationError as error:
        remember(("CONSULTA_DIRETA", "ERRO", error.status_code, "HTTP_"+str(error.status_code), str(error), getattr(error, "request_id", "")))
        if error.status_code not in (404, 500):
            state, code, message = "ERROR", "FALHA_CONSULTA", str(error)
        else:
            _renew(database, token, client)
            row = client.get_scan_batch([payroll])[0]
            reason = row.get("error")
            remember(("LOTE_DE_UM_ID", "RETORNO", 200, reason or ("ENCONTRADO" if row.get("data") else "NAO_ENCONTRADO"), "Resposta do endpoint de consulta em lote para uma única matrícula."))
            if error.status_code == 404 and row.get("data") is None and reason in (None, "vinculo_ambiguo"):
                state, code, message = "NOT_FOUND", "AUSENCIA_CONFIRMADA", "A consulta direta e o lote individual não localizaram este número. Nenhuma pessoa foi excluída."
            else:
                state, code, message = "NEEDS_SOURCE", reason or "FICHA_INDISPONIVEL", "A Bridge não individualizou a ficha. A duplicidade de pessoas não foi confirmada."
                if row.get("data"):
                    identity = _identity(row["data"])
                proposed = manual_identity or ({"cpf": hint["cpf"], "admissionDate": hint["admission_date"], "takerCnpj": hint["taker_cnpj"]} if hint else None)
                if proposed:
                    try:
                        _renew(database, token, client)
                        if manually_confirmed:
                            identified = manually_confirmed
                        else:
                            identified = client.identify_employee(payroll, cpf=proposed["cpf"], admission_date=proposed["admissionDate"], taker_cnpj=proposed["takerCnpj"])
                            remember(("IDENTIFICACAO_EXATA", "OK", 200, "IDENTIDADE_CONFIRMADA", "CPF, admissão e CNPJ completo correspondem a uma única resposta da Bridge."))
                        identity = _identity(identified)
                        state, code, message = "IDENTIFIED", "FICHA_COMPLETA_INDISPONIVEL", "Identidade localizada por CPF, admissão e CNPJ. A Bridge não forneceu a chave e a ficha completa; o cadastro não foi forçado."
                    except (ValueError, IntegrationError) as problem:
                        remember(("IDENTIFICACAO_EXATA", "ERRO", getattr(problem, "status_code", 0), "IDENTIDADE_NAO_CONFIRMADA", str(problem), getattr(problem, "request_id", "")))
    evidence = {"origin": "Bridge · reconsulta individual", "code": code, "message": message,
                "httpStatus": attempts[-1][2], "requestId": next((attempt[5] for attempt in reversed(attempts) if len(attempt)>5 and attempt[5]), ""), "duplicateConfirmed": False}
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        job = _owned(db, token)
        if _context(db, client, actor)["cycle"] != job["cycle"]:
            raise ValueError("O ciclo mudou durante o tratamento.")
        cycle = job["cycle"]
        if items:
            _upsert(db, "employments", items[0], cycle)
            if items[1]:
                _upsert(db, "companies", items[1], cycle)
        if state in ("RESOLVED", "NOT_FOUND"):
            db.execute("DELETE FROM local_sib_scan_issues WHERE cycle=? AND payroll_id=?", (cycle, payroll))
        attempt_count = db.execute("SELECT COUNT(*) FROM local_sib_issue_attempts WHERE cycle=? AND payroll_id=?", (cycle, payroll)).fetchone()[0]
        db.execute("""INSERT INTO local_sib_issue_reviews(cycle,payroll_id,status,reason_code,evidence_json,identity_json,attempts)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(cycle,payroll_id) DO UPDATE SET status=excluded.status,
            reason_code=excluded.reason_code,evidence_json=excluded.evidence_json,identity_json=excluded.identity_json,
            attempts=excluded.attempts,updated_at=CURRENT_TIMESTAMP""",
            (cycle, payroll, state, code, _json(evidence), _json(identity), attempt_count))
        queue = json.loads(job["queue_json"])
        if not queue or queue[0] != payroll:
            raise ValueError("A fila mudou durante o tratamento.")
        db.execute("UPDATE local_sib_issue_job SET queue_json=?,processed=processed+1,resolved=resolved+?,identified=identified+?,unresolved=unresolved+?,lease_until=? WHERE token=?",
            (_json(queue[1:]), int(state in ("RESOLVED", "NOT_FOUND")), int(state == "IDENTIFIED"), int(state not in ("RESOLVED", "NOT_FOUND", "IDENTIFIED")), time.time()+LEASE_SECONDS, token))
    if state == "ERROR":
        raise IntegrationError(message)


def _worker(database, client, actor, token, identity):
    from .database import connect
    try:
        while True:
            with connect(database) as db:
                row = _owned(db, token)
                _context(db, client, actor)
                queue = json.loads(row["queue_json"])
                if not row["running"] or not queue:
                    break
                db.execute("UPDATE local_sib_issue_job SET lease_until=? WHERE token=?", (time.time()+LEASE_SECONDS, token))
            try:
                _diagnose(database, client, actor, token, queue[0], identity)
            except _PauseRequested:
                break
            except Exception as error:
                with connect(database) as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = _owned(db, token)
                    remaining = json.loads(current["queue_json"])
                    if remaining and remaining[0] == queue[0]:
                        _admin(db, actor)
                        if _cycle(db)["cycle"] != current["cycle"]:
                            raise ValueError("O ciclo mudou durante o tratamento.")
                        message = str(error) if isinstance(error, (ValueError, IntegrationError)) else "A consulta não foi concluída. As respostas anteriores foram preservadas."
                        evidence = {"origin": "V3 · diagnóstico", "code": "CONSULTA_INCONCLUSIVA", "message": message[:300], "httpStatus": getattr(error, "status_code", 0), "requestId": getattr(error, "request_id", ""), "duplicateConfirmed": False}
                        _attempt(db, current["cycle"], queue[0], actor, "DIAGNOSTICO", "ERRO", getattr(error, "status_code", 0), "CONSULTA_INCONCLUSIVA", message, getattr(error, "request_id", ""))
                        db.execute("""INSERT INTO local_sib_issue_reviews(cycle,payroll_id,status,reason_code,evidence_json,attempts)
                            VALUES(?,?,'ERROR','CONSULTA_INCONCLUSIVA',?,1) ON CONFLICT(cycle,payroll_id) DO UPDATE SET
                            status='ERROR',reason_code='CONSULTA_INCONCLUSIVA',evidence_json=excluded.evidence_json,
                            attempts=local_sib_issue_reviews.attempts+1,updated_at=CURRENT_TIMESTAMP""", (current["cycle"], queue[0], _json(evidence)))
                        db.execute("UPDATE local_sib_issue_job SET queue_json=?,processed=processed+1,unresolved=unresolved+1 WHERE token=?", (_json(remaining[1:]), token))
                raise
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, PermissionError, IntegrationError)) else "O tratamento foi pausado. As tentativas anteriores foram preservadas."
        with connect(database) as db:
            db.execute("UPDATE local_sib_issue_job SET error=? WHERE token=?", (message[:300], token))
    finally:
        with connect(database) as db:
            db.execute("UPDATE local_sib_issue_job SET running=0,token='',lease_until=0 WHERE token=?", (token,))
