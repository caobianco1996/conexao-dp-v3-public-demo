"""Busca retomável por matrículas usando somente as rotas já existentes da Bridge."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from .sib_runtime import lease_active, worker_token

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_scan_state (
 id INTEGER PRIMARY KEY CHECK(id=1), cycle TEXT NOT NULL, source_url TEXT NOT NULL,
 start_id INTEGER NOT NULL, end_id INTEGER NOT NULL, next_id INTEGER NOT NULL,
 running INTEGER NOT NULL DEFAULT 0, checked INTEGER NOT NULL DEFAULT 0,
 imported INTEGER NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0,
 ambiguous INTEGER NOT NULL DEFAULT 0, pending_json TEXT NOT NULL DEFAULT '[]',
 error TEXT NOT NULL DEFAULT '', lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 continuous INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0,
 backup_path TEXT NOT NULL DEFAULT '', actor_id INTEGER NOT NULL REFERENCES users(id),
 empty_streak INTEGER NOT NULL DEFAULT 0, auto_stopped INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_scan_issues (
 cycle TEXT NOT NULL, payroll_id TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(cycle,payroll_id)
);
CREATE TABLE IF NOT EXISTS local_sib_scan_coverage (
 source_url TEXT NOT NULL, start_id INTEGER NOT NULL, end_id INTEGER NOT NULL,
 PRIMARY KEY(source_url,start_id,end_id)
);
CREATE TABLE IF NOT EXISTS local_sib_scan_active_batch (
 id INTEGER PRIMARY KEY CHECK(id=1), source_url TEXT NOT NULL,
 start_id INTEGER NOT NULL, end_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS local_sib_records_registration
 ON local_sib_records(dataset,json_extract(data_json,'$.registration'));
"""
LEASE_SECONDS = 180
AUTO_STOP_EMPTY_STREAK = 100


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _remember_missing_id(db, source_url, payroll):
    """Keep confirmed empty IDs for the post-scan monitor to revisit."""
    db.execute("""INSERT OR IGNORE INTO local_sib_discovery_gaps(source_url,payroll_id)
        VALUES(?,?)""", (source_url, str(payroll)))


def _row(db):
    return db.execute("SELECT * FROM local_sib_scan_state WHERE id=1").fetchone()


def _covered_end(db, source_url, number):
    row = db.execute("""SELECT MAX(end_id) FROM local_sib_scan_coverage
        WHERE source_url=? AND start_id<=? AND end_id>=?""", (source_url, number, number)).fetchone()
    return row[0] if row else None


def _next_covered_start(db, source_url, first, last):
    row = db.execute("""SELECT MIN(start_id) FROM local_sib_scan_coverage
        WHERE source_url=? AND start_id>? AND start_id<=?""", (source_url, first, last)).fetchone()
    return row[0] if row else None


def summary(db):
    row = _row(db)
    inflight = bool(row and lease_active(row["lease_token"], row["lease_until"]))
    pending = json.loads(row["pending_json"]) if row else []
    done = bool(row and row["next_id"] > row["end_id"] and not pending)
    reasons = {item["reason"]: item["total"] for item in db.execute(
        "SELECT reason,COUNT(*) AS total FROM local_sib_scan_issues WHERE cycle=? GROUP BY reason", (row["cycle"],))} if row else {}
    inconclusive = reasons.get("vinculo_ambiguo", 0)
    auto_resume = bool(row and row["continuous"] and not done)
    return {"started": bool(row), "running": bool(row and row["running"] and inflight),
            "autoResume": auto_resume, "retrying": bool(auto_resume and row["retry_at"] > 0),
            "retryAt": row["retry_at"] if row else 0, "retryCount": row["retry_count"] if row else 0,
            "inFlight": inflight, "done": done, "coverage": "range",
            "startId": row["start_id"] if row else 0, "endId": row["end_id"] if row else 999999,
            "nextId": row["next_id"] if row else 0,
            "checked": row["checked"] if row else 0, "imported": row["imported"] if row else 0,
            "missing": row["missing"] if row else 0, "ambiguous": sum(reasons.values()),
            "emptyStreak": row["empty_streak"] if row else 0,
            "autoStopped": bool(row and row["auto_stopped"]),
            "emptyStopThreshold": AUTO_STOP_EMPTY_STREAK,
            "inconclusive": inconclusive, "reviewRequired": sum(reasons.values()) - inconclusive,
            "pending": len(pending), "error": row["error"] if row else "",
            "updatedAt": row["updated_at"] if row else "",
            "companies": db.execute("SELECT COUNT(*) FROM local_sib_records WHERE dataset='companies'").fetchone()[0],
            "records": db.execute("SELECT COUNT(*) FROM local_sib_records WHERE dataset='employments'").fetchone()[0],
            "ambiguousIds": [r[0] for r in db.execute("SELECT payroll_id FROM local_sib_scan_issues WHERE cycle=? ORDER BY CAST(payroll_id AS INTEGER) LIMIT 50", (row["cycle"],))] if row else [],
            "inconclusiveIds": [r[0] for r in db.execute("SELECT payroll_id FROM local_sib_scan_issues WHERE cycle=? AND reason='vinculo_ambiguo' ORDER BY CAST(payroll_id AS INTEGER) LIMIT 50", (row["cycle"],))] if row else [],
            "reviewIds": [r[0] for r in db.execute("SELECT payroll_id FROM local_sib_scan_issues WHERE cycle=? AND reason<>'vinculo_ambiguo' ORDER BY CAST(payroll_id AS INTEGER) LIMIT 50", (row["cycle"],))] if row else []}


def _admin(db, actor_id):
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor["role"] != "ADMIN":
        raise PermissionError("Somente Administrador pode trazer cadastros do SIB.")


def _bounds(start, end):
    if isinstance(start, bool) or isinstance(end, bool):
        raise ValueError("Informe a faixa numérica de matrículas.")
    if not str(start).isdigit() or not str(end).isdigit():
        raise ValueError("Informe a faixa numérica de matrículas.")
    first, last = int(start), int(end)
    if not 0 <= first <= last <= 999999:
        raise ValueError("A faixa de matrículas deve ficar entre 0 e 999999.")
    return first, last


def control(database, client, actor_id, action, *, start_id=0, end_id=999999, recovery=False):
    """Return immediately; a single worker owns a renewable lease in SQLite."""
    from .database import connect
    if action not in {"start", "pause", "resume", "next", "reset"}:
        raise ValueError("Comando de importação inválido.")
    token = ""
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        _admin(db, actor_id)
        row = _row(db)
        inflight = bool(row and lease_active(row["lease_token"], row["lease_until"]))
        if recovery and (not row or not row["continuous"] or inflight or row["retry_at"] > time.time()
                         or (row["next_id"] > row["end_id"] and not json.loads(row["pending_json"]))):
            return None
        if action == "pause":
            if row:
                db.execute("UPDATE local_sib_scan_state SET running=0,continuous=0,retry_at=0,error='',updated_at=CURRENT_TIMESTAMP WHERE id=1")
            return {"ok": True, "scan": summary(db), "message": "Parada solicitada. A consulta em andamento será salva antes de parar."}
        issue_job = db.execute("SELECT token,lease_until FROM local_sib_issue_job WHERE id=1").fetchone()
        if issue_job and lease_active(issue_job["token"], issue_job["lease_until"]):
            raise ValueError("Pare o tratamento de ocorrências antes de iniciar a busca de cadastros.")
        refresh = db.execute("SELECT lease_token,lease_until FROM local_sib_refresh_state WHERE id=1").fetchone()
        if refresh and lease_active(refresh["lease_token"], refresh["lease_until"]):
            raise ValueError("Aguarde a atualização dos cadastros já trazidos antes de iniciar outra busca.")
        discovery = db.execute("SELECT lease_token,lease_until FROM local_sib_discovery_state WHERE id=1").fetchone()
        if discovery and lease_active(discovery["lease_token"], discovery["lease_until"]):
            raise ValueError("Aguarde a consulta incremental de novos cadastros antes de iniciar outra busca.")
        settings = db.execute("SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1").fetchone()
        if not settings or not settings["sib_active"] or settings["sib_base_url"] != client.config.base_url:
            raise ValueError("A configuração SIB mudou. Atualize a tela antes de continuar a busca.")
        if inflight:
            raise ValueError("Há uma consulta em andamento. Pare e aguarde antes de iniciar outra faixa.")
        if row and action != "reset" and row["source_url"] != client.config.base_url:
            raise ValueError("A URL da Bridge mudou. Inicie uma nova faixa para usar essa origem.")
        if not row or action == "reset":
            first, last = _bounds(start_id, end_id)
            db.execute("""INSERT INTO local_sib_scan_state(id,cycle,source_url,start_id,end_id,next_id,actor_id)
                VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cycle=excluded.cycle,
                source_url=excluded.source_url,start_id=excluded.start_id,end_id=excluded.end_id,next_id=excluded.next_id,
                running=0,checked=0,imported=0,missing=0,ambiguous=0,pending_json='[]',error='',
                lease_token='',lease_until=0,continuous=0,retry_count=0,retry_at=0,
                backup_path='',empty_streak=0,auto_stopped=0,actor_id=excluded.actor_id,updated_at=CURRENT_TIMESTAMP""",
                (uuid4().hex, client.config.base_url, first, last, first, actor_id))
            row = _row(db)
            db.execute("DELETE FROM local_sib_scan_active_batch WHERE id=1")
        if action == "reset":
            return {"ok": True, "scan": summary(db), "message": "Nova faixa preparada. Os cadastros já trazidos foram preservados."}
        if row["next_id"] > row["end_id"] and not json.loads(row["pending_json"]):
            return {"ok": True, "scan": summary(db), "message": "Esta faixa já foi consultada. Você pode iniciar uma nova faixa."}
        token = worker_token()
        retry_count = row["retry_count"] if recovery else 0
        db.execute("""UPDATE local_sib_scan_state SET running=1,error='',continuous=?,retry_count=?,retry_at=0,
                   lease_token=?,lease_until=?,actor_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1""",
                   (int(action != "next"), retry_count, token, time.time() + LEASE_SECONDS, actor_id))
        result = summary(db)
    worker = threading.Thread(target=_worker, args=(Path(database), client, actor_id, token, action == "next"), daemon=True, name="SIB-matriculas")
    try:
        worker.start()
    except Exception:
        with connect(database) as db:
            db.execute("UPDATE local_sib_scan_state SET running=0,lease_token='',lease_until=0 WHERE lease_token=?", (token,))
        raise
    return {"ok": True, "scan": result, "message": "Consulta iniciada em lotes de até 50 matrículas. Você pode parar e continuar depois."}


def resume_due(database, client):
    """Restore the user's continuous run after a network failure or restart."""
    from .database import connect
    with connect(database) as db:
        row = _row(db)
        if not row or not row["continuous"]:
            return False
        actor_id = row["actor_id"]
    return bool(control(database, client, actor_id, "resume", recovery=True))


def _owned(db, token):
    row = _row(db)
    if not row or row["lease_token"] != token:
        raise RuntimeError("A execução desta busca foi substituída.")
    return row


def _renew(db, token):
    db.execute("UPDATE local_sib_scan_state SET lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?", (time.time() + LEASE_SECONDS, token))


def _ensure_backup(database, token):
    from .database import connect
    with connect(database) as db:
        row = _owned(db, token)
        if row["backup_path"]:
            return
        cycle = row["cycle"]
    folder = database.resolve().parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"antes_busca_sib_{datetime.now():%Y%m%d_%H%M%S}_{cycle}.sqlite3"
    with connect(database) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    with connect(database) as db:
        db.execute("UPDATE local_sib_scan_state SET backup_path=? WHERE lease_token=?", (str(target), token))
        _renew(db, token)


def _upsert(db, dataset, item, cycle):
    if dataset == 'employments':
        from .sib_record_mapping import classified_employee
        item = classified_employee(db, item)
        from .sib_gap_recovery import same_identity
        previous = db.execute("SELECT data_json,json_extract(raw_json,'$.VIN_FUN') modality FROM local_sib_records WHERE dataset='employments' AND source_id=?", (item['source_id'],)).fetchone()
        if previous:
            stored = json.loads(previous['data_json'])
            stored['contractTypeCode'] = stored.get('contractTypeCode') or previous['modality']
            if not same_identity(stored, item['data']):
                raise ValueError('O SIB reutilizou a identificação para outro vínculo. A ficha anterior foi preservada para conferência.')
    db.execute("""INSERT INTO local_sib_records(dataset,source_id,data_json,raw_json,cycle)
        VALUES(?,?,?,?,?) ON CONFLICT(dataset,source_id) DO UPDATE SET data_json=excluded.data_json,
        raw_json=excluded.raw_json,cycle=excluded.cycle,imported_at=CURRENT_TIMESTAMP""",
        (dataset, item["source_id"], _json(item["data"]), _json(item["raw"]), cycle))
    if dataset == 'employments':
        from .sib_presence import observe
        observe(db, item['source_id'], item['data'])


def _worker(database, client, actor_id, token, once):
    from .database import connect
    from .sib_record_mapping import map_raw_employee
    from .integrations import IntegrationError, SibClient
    pages = 0
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="SIB-ficha")
    prefetched = {}
    try:
        with connect(database) as db:
            attempt = _owned(db, token)["retry_count"]
        if attempt and isinstance(client, SibClient):
            retry_timeout = min(60, int(client.config.timeout_seconds) * (1 + min(attempt, 2)))
            client = SibClient(replace(client.config, timeout_seconds=retry_timeout), transport=client.transport)
        _ensure_backup(database, token)
        # The periodic verifier yields when it sees this scan's saved intent.
        # Wait for its current read to finish before issuing new requests.
        while True:
            with connect(database) as db:
                if not _owned(db, token)["running"]:
                    return
                auto = db.execute("SELECT lease_token,lease_until FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
                if not auto or not lease_active(auto["lease_token"], auto["lease_until"]):
                    break
                _renew(db, token)
            time.sleep(0.25)
        while True:
            with connect(database) as db:
                row = _owned(db, token)
                _admin(db, actor_id)
                if not row["running"]:
                    break
                pending = json.loads(row["pending_json"])
                cycle = row["cycle"]
                if not pending and row["empty_streak"] >= AUTO_STOP_EMPTY_STREAK:
                    # A long confirmed tail of missing IDs is a safer end marker
                    # than stopping at the first gap. Keep the completed range
                    # honest by ending it at the last ID actually traversed.
                    db.execute("""UPDATE local_sib_scan_state SET end_id=next_id-1,
                        continuous=0,auto_stopped=1,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?""", (token,))
                    break
                if pending and all(_covered_end(db, row["source_url"], int(value.removeprefix("check:"))) is not None
                                   for value in pending):
                    # An older range already finished these IDs. Its records
                    # remain in V3; do not repeat its pending raw lookups.
                    pending = []
                    db.execute("UPDATE local_sib_scan_state SET pending_json='[]',empty_streak=0 WHERE lease_token=?", (token,))
                if not pending and (row["next_id"] > row["end_id"] or (once and pages)):
                    break
                first, last = row["next_id"], min(row["next_id"] + 49, row["end_id"])
                covered_to = _covered_end(db, row["source_url"], first) if not pending else None
                if not pending and covered_to is None:
                    next_covered = _next_covered_start(db, row["source_url"], first, last)
                    if next_covered is not None:
                        last = next_covered - 1
            if covered_to is not None:
                with connect(database) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = _owned(db, token)
                    advance_to = min(covered_to + 1, row["end_id"] + 1)
                    db.execute("UPDATE local_sib_scan_state SET next_id=?,checked=checked+? WHERE lease_token=?",
                               (advance_to, advance_to - first, token))
                    db.execute("UPDATE local_sib_scan_state SET empty_streak=0 WHERE lease_token=?", (token,))
                    _renew(db, token)
                continue
            if not pending:
                candidates = [str(number) for number in range(first, last + 1)]
                # A saved registration alone does not prove this numeric ID was
                # fully checked: an intern and another employment can share it.
                # Only a completed range from this origin is safe to skip.
                results = client.get_scan_batch(candidates)
                with connect(database) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = _owned(db, token)
                    _admin(db, actor_id)
                    db.execute("UPDATE local_sib_scan_state SET retry_count=0,error='' WHERE lease_token=?", (token,))
                    db.execute("""INSERT INTO local_sib_scan_active_batch(id,source_url,start_id,end_id)
                        VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
                        start_id=excluded.start_id,end_id=excluded.end_id""",
                        (row["source_url"], first, last))
                    pending, missing, ambiguous, missing_ids = [], 0, 0, []
                    needs_verification = any(item.get("error") == "vinculo_ambiguo" for item in results)
                    by_id = {str(item["id_folha"]): item for item in results}
                    # An ambiguous batch can omit a valid row when the source
                    # limit is consumed by duplicates. Never use it as proof of
                    # consecutive missing IDs.
                    empty_streak = 0 if needs_verification else int(row["empty_streak"])
                    if not needs_verification:
                        for candidate in candidates:
                            result = by_id[candidate]
                            if result.get("error") in ("vinculo_ambiguo", "identidade_nao_corresponde"):
                                empty_streak = 0
                            elif result.get("error"):
                                raise RuntimeError("A Bridge não concluiu o lote. O progresso anterior foi preservado; tente continuar.")
                            elif result.get("data") is None:
                                empty_streak += 1
                            else:
                                empty_streak = 0
                    for item in results:
                        reason = item.get("error")
                        if reason in ("vinculo_ambiguo", "identidade_nao_corresponde"):
                            ambiguous += 1
                            db.execute("INSERT OR REPLACE INTO local_sib_scan_issues(cycle,payroll_id,reason) VALUES(?,?,?)", (cycle, str(item["id_folha"]), reason))
                            # A batch code is not proof of duplicated people. Try the
                            # individual read before leaving the occurrence unresolved.
                            pending.append(str(item["id_folha"]))
                        elif reason:
                            raise RuntimeError("A Bridge não concluiu o lote. O progresso anterior foi preservado; tente continuar.")
                        elif item.get("data") is None:
                            # Original Bridge caps a batch at 200 source rows. Duplicates
                            # can consume that cap; confirm its apparent misses separately.
                            if needs_verification:
                                pending.append("check:" + str(item["id_folha"]))
                            else:
                                missing += 1
                                missing_ids.append(str(item["id_folha"]))
                        else:
                            pending.append(str(item["id_folha"]))
                    db.execute("UPDATE local_sib_scan_state SET next_id=?,checked=checked+?,missing=missing+?,ambiguous=ambiguous+?,pending_json=?,empty_streak=? WHERE lease_token=?",
                               (last + 1, last - first + 1, missing, ambiguous, _json(pending), empty_streak, token))
                    for payroll in missing_ids:
                        _remember_missing_id(db, row["source_url"], payroll)
                    _renew(db, token)
                pages += 1
            else:
                pages = max(pages, 1)
            while pending:
                with connect(database) as db:
                    row = _owned(db, token)
                    _admin(db, actor_id)
                    if not row["running"]:
                        break
                    _renew(db, token)
                # One network worker keeps the serial Bridge at one request.
                # Queue the next read so it can run while this thread maps and
                # commits the current response. Unconsumed IDs remain pending.
                for candidate in pending[:2]:
                    if candidate.startswith("check:"):
                        break
                    if candidate not in prefetched:
                        prefetched[candidate] = executor.submit(client.get_employee_raw, candidate)
                payroll = pending[0]
                if payroll.startswith("check:"):
                    payroll = payroll[6:]
                    verification = client.get_scan_batch([payroll])[0]
                    reason = verification.get("error")
                    if reason and reason not in ("vinculo_ambiguo", "identidade_nao_corresponde"):
                        raise RuntimeError("A conferência de uma matrícula não foi concluída.")
                    with connect(database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = _owned(db, token)
                        _admin(db, actor_id)
                        if reason:
                            db.execute("INSERT OR REPLACE INTO local_sib_scan_issues(cycle,payroll_id,reason) VALUES(?,?,?)", (cycle, payroll, reason))
                            db.execute("UPDATE local_sib_scan_state SET ambiguous=ambiguous+1 WHERE lease_token=?", (token,))
                            pending = pending[1:]
                        elif verification.get("data") is None:
                            db.execute("UPDATE local_sib_scan_state SET missing=missing+1 WHERE lease_token=?", (token,))
                            _remember_missing_id(db, row["source_url"], payroll)
                            pending = pending[1:]
                        else:
                            pending[0] = payroll
                        db.execute("UPDATE local_sib_scan_state SET pending_json=? WHERE lease_token=?", (_json(pending), token))
                        _renew(db, token)
                    continue
                try:
                    payload = prefetched.pop(payroll).result()
                except IntegrationError as error:
                    if error.status_code not in (404, 500):
                        raise
                    with connect(database) as db:
                        if not _owned(db, token)["running"]:
                            raise
                        _renew(db, token)
                    verification = client.get_scan_batch([payroll])[0]
                    reason = verification.get("error")
                    if (error.status_code == 404 and verification.get("data") is None
                            and reason in (None, "vinculo_ambiguo")):
                        with connect(database) as db:
                            db.execute("BEGIN IMMEDIATE")
                            row = _owned(db, token)
                            _admin(db, actor_id)
                            db.execute("DELETE FROM local_sib_scan_issues WHERE cycle=? AND payroll_id=?", (cycle, payroll))
                            pending = pending[1:]
                            _remember_missing_id(db, row["source_url"], payroll)
                            db.execute("UPDATE local_sib_scan_state SET missing=missing+1,pending_json=? WHERE lease_token=?", (_json(pending), token))
                            _renew(db, token)
                        continue
                    if reason not in ("vinculo_ambiguo", "identidade_nao_corresponde") and verification.get("data") is not None:
                        raise error
                    if reason and reason not in ("vinculo_ambiguo", "identidade_nao_corresponde"):
                        raise error
                    with connect(database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        _owned(db, token)
                        _admin(db, actor_id)
                        db.execute("INSERT OR REPLACE INTO local_sib_scan_issues(cycle,payroll_id,reason) VALUES(?,?,?)", (cycle, payroll, reason or "removido_apos_consulta"))
                        pending = pending[1:]
                        db.execute("UPDATE local_sib_scan_state SET ambiguous=ambiguous+1,pending_json=? WHERE lease_token=?", (_json(pending), token))
                        _renew(db, token)
                    continue
                try:
                    employment, company = map_raw_employee(payload, payroll)
                except ValueError as error:
                    # A malformed source record must not block the remaining
                    # IDs. Keep its diagnosis locally without inventing a name
                    # or treating an incomplete response as an absent person.
                    no_name = str(error) == "O cadastro SIB não contém nome de funcionário."
                    code = "FICHA_SEM_NOME" if no_name else "FICHA_INVALIDA"
                    message = ("A Bridge retornou uma ficha sem nome de funcionário. A ficha não foi importada."
                               if no_name else "A Bridge retornou uma ficha incompleta ou com identidade incompatível. A ficha não foi importada.")
                    evidence = {"origin": "Bridge · consulta individual", "httpStatus": 200,
                                "code": code, "message": message, "duplicateConfirmed": False}
                    with connect(database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = _owned(db, token)
                        _admin(db, actor_id)
                        if json.loads(row["pending_json"]) != pending:
                            raise RuntimeError("O progresso mudou durante a consulta. Atualize a tela para continuar.")
                        db.execute("INSERT OR REPLACE INTO local_sib_scan_issues(cycle,payroll_id,reason) VALUES(?,?,?)",
                                   (cycle, payroll, code))
                        db.execute("""INSERT INTO local_sib_issue_attempts(cycle,payroll_id,action,outcome,http_status,code,message,actor_id)
                            VALUES(?,?,'VALIDACAO_FICHA','INVALIDA',200,?,?,?)""",
                                   (cycle, payroll, code, message, actor_id))
                        attempts = db.execute("SELECT COUNT(*) FROM local_sib_issue_attempts WHERE cycle=? AND payroll_id=?",
                                              (cycle, payroll)).fetchone()[0]
                        db.execute("""INSERT INTO local_sib_issue_reviews(cycle,payroll_id,status,reason_code,evidence_json,attempts)
                            VALUES(?,?,'NEEDS_SOURCE',?,?,?) ON CONFLICT(cycle,payroll_id) DO UPDATE SET
                            status='NEEDS_SOURCE',reason_code=excluded.reason_code,evidence_json=excluded.evidence_json,
                            attempts=excluded.attempts,updated_at=CURRENT_TIMESTAMP""",
                                   (cycle, payroll, code, _json(evidence), attempts))
                        pending = pending[1:]
                        db.execute("UPDATE local_sib_scan_state SET pending_json=?,retry_count=0,error='' WHERE lease_token=?",
                                   (_json(pending), token))
                        _renew(db, token)
                    continue
                with connect(database) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = _owned(db, token)
                    _admin(db, actor_id)
                    if json.loads(row["pending_json"]) != pending:
                        raise RuntimeError("O progresso mudou durante a consulta. Atualize a tela para continuar.")
                    _upsert(db, "employments", employment, cycle)
                    db.execute("UPDATE local_sib_scan_state SET retry_count=0,error='' WHERE lease_token=?", (token,))
                    if company:
                        _upsert(db, "companies", company, cycle)
                    db.execute("DELETE FROM local_sib_scan_issues WHERE cycle=? AND payroll_id=?", (cycle, payroll))
                    pending = pending[1:]
                    db.execute("UPDATE local_sib_scan_state SET imported=imported+1,pending_json=? WHERE lease_token=?", (_json(pending), token))
                    _renew(db, token)
            with connect(database) as db:
                row = _owned(db, token)
                if not pending:
                    active_batch = db.execute("SELECT source_url,start_id,end_id FROM local_sib_scan_active_batch WHERE id=1").fetchone()
                    if active_batch and active_batch["source_url"] == row["source_url"]:
                        db.execute("INSERT OR IGNORE INTO local_sib_scan_coverage(source_url,start_id,end_id) VALUES(?,?,?)",
                                   (active_batch["source_url"], active_batch["start_id"], active_batch["end_id"]))
                        db.execute("DELETE FROM local_sib_scan_active_batch WHERE id=1")
                    db.execute("""INSERT INTO local_integration_events(integration,action,outcome,result_count,summary,actor_id)
                        VALUES('SIB','BUSCA_MATRICULAS','OK',0,'Lote de até 50 matrículas consultado. Cadastros encontrados salvos na V3.',?)""", (actor_id,))
                if not row["running"] or once:
                    break
            time.sleep(0.1)
    except Exception as error:
        # Never place source payloads, credentials or arbitrary driver messages in the browser.
        logging.getLogger(__name__).error('Falha na busca SIB: %s %s\n%s',
            type(error).__name__, getattr(error, 'sqlite_errorname', ''), ''.join(traceback.format_tb(error.__traceback__)))
        message = str(error) if isinstance(error, (ValueError, PermissionError, IntegrationError)) else "A busca foi pausada após uma falha. Os cadastros salvos foram preservados; use Continuar."
        try:
            with connect(database) as db:
                row = _owned(db, token)
                if not row["running"] and not row["continuous"]:
                    message = ""
                busy = (isinstance(error, sqlite3.OperationalError)
                        and (getattr(error, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))
                retry = ((isinstance(error, IntegrationError) and error.retryable or busy)
                         and not once and row["continuous"] and row["running"])
                if busy and retry:
                    message = 'Aguardando liberação do banco local. A consulta retomará automaticamente do ponto salvo.'
                count = row["retry_count"] + 1 if retry else 0
                due = time.time() + min(60, 5 * 2 ** min(count - 1, 4)) if retry else 0
                db.execute("""UPDATE local_sib_scan_state SET error=?,continuous=?,retry_count=?,retry_at=?
                    WHERE lease_token=?""", (message[:300], int(bool(retry)), count, due, token))
        except sqlite3.DatabaseError:
            pass
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        with connect(database) as db:
            row = _row(db)
            if row and row["lease_token"] == token and row["next_id"] > row["end_id"] and not json.loads(row["pending_json"]):
                db.execute("UPDATE local_sib_scan_state SET continuous=0,retry_count=0,retry_at=0,error='' WHERE lease_token=?", (token,))
            db.execute("UPDATE local_sib_scan_state SET running=0,lease_token='',lease_until=0,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?", (token,))
