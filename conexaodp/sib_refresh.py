"""Retomable refresh of SIB records already stored in the local V3 catalog."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

from .integrations import IntegrationError
from .sib_record_mapping import map_raw_employee, classified_employee
from .sib_runtime import lease_active, worker_token
from .sib_scan import _admin, _json, _upsert

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_refresh_state (
 id INTEGER PRIMARY KEY CHECK(id=1), source_url TEXT NOT NULL,
 queue_json TEXT NOT NULL, next_index INTEGER NOT NULL DEFAULT 0,
 changed INTEGER NOT NULL DEFAULT 0, unresolved INTEGER NOT NULL DEFAULT 0,
 running INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 actor_id INTEGER NOT NULL REFERENCES users(id),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_change_history (
 id INTEGER PRIMARY KEY,
 source_url TEXT NOT NULL,
 cycle_started_at REAL NOT NULL DEFAULT 0,
 dataset TEXT NOT NULL,
 source_id TEXT NOT NULL,
 registration TEXT NOT NULL DEFAULT '',
 record_name TEXT NOT NULL DEFAULT '',
 company_name TEXT NOT NULL DEFAULT '',
 branch_code TEXT NOT NULL DEFAULT '',
 changes_json TEXT NOT NULL DEFAULT '[]',
 changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS sib_change_history_recent ON local_sib_change_history(id DESC);
CREATE INDEX IF NOT EXISTS sib_change_history_registration ON local_sib_change_history(registration,id DESC);
"""
LEASE_SECONDS = 180
CHANGE_HISTORY_LIMIT = 10000
_PRIVATE_CHANGE_FIELD = re.compile(
    r"CPF|BANK|ACCOUNT|IBAN|CTA|PIS|NIS|EMAIL|PHONE|TEL|PASSWORD|SECRET|TOKEN|API.?KEY|DOCUMENT|(?:^|_)RG(?:_|$)",
    re.IGNORECASE,
)


def _row(db):
    return db.execute("SELECT * FROM local_sib_refresh_state WHERE id=1").fetchone()


def _refresh_eligible(data, *, today=None):
    """Keep recurring ficha refresh limited to links that are still active."""
    today = today or date.today()
    dismissal = str(data.get("dismissalDate") or "").strip()
    if dismissal:
        try:
            dismissed_on = date.fromisoformat(dismissal[:10])
        except ValueError:
            return False
        # A known termination date takes precedence over a stale active flag.
        return dismissed_on > today
    if data.get("active") is True:
        return True
    return False


def summary(db):
    row = _row(db)
    total = len(json.loads(row["queue_json"])) if row else 0
    in_flight = bool(row and lease_active(row["lease_token"], row["lease_until"]))
    return {"started": bool(row), "running": bool(row and row["running"] and in_flight),
            "inFlight": in_flight, "done": bool(row and row["next_index"] >= total),
            "total": total, "checked": row["next_index"] if row else 0,
            "changed": row["changed"] if row else 0,
            "unresolved": row["unresolved"] if row else 0,
            "error": row["error"] if row else "",
            "updatedAt": row["updated_at"] if row else ""}


def _queue(db):
    result = []
    from .sib_presence import overlays
    membership = overlays(db)
    for row in db.execute("""SELECT source_id,data_json,json_extract(raw_json,'$.VIN_FUN') modality
        FROM local_sib_records WHERE dataset='employments' ORDER BY source_id"""):
        if membership.get(row['source_id'], {}).get('sibPresence') == 'EXCLUDED':
            continue
        try:
            data = json.loads(row["data_json"])
            if not _refresh_eligible(data):
                continue
            registration = str(data.get("registration") or "")
        except (TypeError, ValueError):
            continue
        if re.fullmatch(r"\d{1,10}", registration):
            parts = row['source_id'].split(':')
            result.append({"sourceId": row["source_id"], "registration": registration,
                           "employerCode": parts[0] if len(parts) == 3 else '',
                           "branchCode": parts[1] if len(parts) == 3 else '',
                           "contractTypeCode": str(row["modality"]) if row["modality"] is not None else ""})
    return result


def _field_changes(before_data, after_data, before_raw, after_raw):
    changes = []
    for scope, before, after in (("data", before_data or {}, after_data or {}),
                                 ("raw", before_raw or {}, after_raw or {})):
        for key in sorted(set(before) | set(after)):
            if _PRIVATE_CHANGE_FIELD.search(str(key)):
                continue
            old, new = before.get(key), after.get(key)
            if old != new:
                changes.append({"field": f"{scope}.{key}", "before": old, "after": new})
    return changes


def _record_change(db, *, source_url, dataset, source_id, registration, record_name,
                   company_name, branch_code, before_data, after_data, before_raw, after_raw):
    changes = _field_changes(before_data, after_data, before_raw, after_raw)
    if not changes:
        return
    cycle = db.execute("SELECT cycle_started_at FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
    db.execute("""INSERT INTO local_sib_change_history
        (source_url,cycle_started_at,dataset,source_id,registration,record_name,company_name,branch_code,changes_json)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (source_url, float(cycle[0] or 0) if cycle else 0, dataset, str(source_id), str(registration or ""),
         str(record_name or ""), str(company_name or ""), str(branch_code or ""), _json(changes)))
    db.execute("""DELETE FROM local_sib_change_history WHERE id NOT IN
        (SELECT id FROM local_sib_change_history ORDER BY id DESC LIMIT ?)""", (CHANGE_HISTORY_LIMIT,))


def list_changes(db, *, page=1, query=""):
    page_size = 15
    page = max(1, min(int(page), 1000000))
    query = str(query or "").strip()[:120]
    where, args = "", []
    if query:
        where = "WHERE registration LIKE ? OR source_id LIKE ? OR record_name LIKE ? OR company_name LIKE ?"
        term = f"%{query}%"
        args.extend((term, term, term, term))
    total = int(db.execute(f"SELECT COUNT(*) FROM local_sib_change_history {where}", args).fetchone()[0])
    items = []
    for row in db.execute(f"""SELECT id,dataset,source_id,registration,record_name,company_name,branch_code,
            changes_json,changed_at FROM local_sib_change_history {where}
            ORDER BY id DESC LIMIT ? OFFSET ?""", (*args, page_size, (page - 1) * page_size)):
        item = dict(row)
        try:
            item["changes"] = json.loads(item.pop("changes_json"))
        except (TypeError, ValueError):
            item["changes"] = []
            item.pop("changes_json", None)
        items.append(item)
    return {"items": items, "total": total, "page": page, "pageSize": page_size,
            "pageCount": max(1, (total + page_size - 1) // page_size)}


def refresh_one(database, client, item, *, source_url):
    """Read one full SIB record and update only the matching local identity."""
    from .database import connect

    try:
        payload = client.get_employee_raw(item['registration'], contract_type_code=item.get('contractTypeCode', ''),
                                          branch_code=item.get('branchCode', ''), employer_code=item.get('employerCode', ''))
        employment, company = map_raw_employee(payload, item["registration"])
        if employment["source_id"] != item["sourceId"]:
            return 0, 1
    except IntegrationError as error:
        if error.status_code not in (404, 409, 500):
            raise
        return 0, 1
    except ValueError:
        return 0, 1

    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        settings = db.execute("SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1").fetchone()
        if not settings or not settings["sib_active"] or settings["sib_base_url"] != source_url:
            raise ValueError("A origem SIB mudou durante a atualização.")
        previous = db.execute("SELECT data_json,raw_json,cycle FROM local_sib_records WHERE dataset='employments' AND source_id=?",
                              (item["sourceId"],)).fetchone()
        if not previous:
            return 0, 1
        from .sib_gap_recovery import same_identity
        previous_data = json.loads(previous["data_json"])
        previous_raw = json.loads(previous["raw_json"])
        stored = dict(previous_data)
        stored['contractTypeCode'] = stored.get('contractTypeCode') or json.loads(previous['raw_json']).get('VIN_FUN')
        if not same_identity(stored, employment['data']):
            # Reused numbering must not silently overwrite another person.
            return 0, 1
        employment = classified_employee(db, employment)
        changed = int(previous["data_json"] != _json(employment["data"])
                      or previous["raw_json"] != _json(employment["raw"]))
        if changed:
            parts = item["sourceId"].split(":")
            company_name = str((company or {}).get("data", {}).get("name") or "")
            _record_change(db, source_url=source_url, dataset="employment", source_id=item["sourceId"],
                registration=item["registration"], record_name=employment["data"].get("name"),
                company_name=company_name, branch_code=parts[1] if len(parts) == 3 else "",
                before_data=previous_data, after_data=employment["data"],
                before_raw=previous_raw, after_raw=employment["raw"])
            _upsert(db, "employments", employment, previous["cycle"])
        else:
            from .sib_presence import observe
            observe(db, item['sourceId'], employment['data'], url=source_url)
        if company:
            original_company = db.execute("SELECT cycle,data_json,raw_json FROM local_sib_records WHERE dataset='companies' AND source_id=?",
                                          (company["source_id"],)).fetchone()
            original_company_data = json.loads(original_company["data_json"]) if original_company else {}
            original_company_raw = json.loads(original_company["raw_json"]) if original_company else {}
            company_changed = (not original_company
                               or original_company["data_json"] != _json(company["data"])
                               or original_company["raw_json"] != _json(company["raw"]))
            if company_changed:
                _record_change(db, source_url=source_url, dataset="company", source_id=company["source_id"],
                    registration="", record_name=company["data"].get("name"),
                    company_name=company["data"].get("name"), branch_code="",
                    before_data=original_company_data, after_data=company["data"],
                    before_raw=original_company_raw, after_raw=company["raw"])
                _upsert(db, "companies", company,
                        original_company["cycle"] if original_company else previous["cycle"])
            changed = int(changed or company_changed)
        return changed, 0


def control(database, client, actor_id, action):
    """Start, pause or resume a read-only Bridge refresh with a saved cursor."""
    from .database import connect, backup_database
    if action not in ("start", "resume", "pause"):
        raise ValueError("Comando de atualização SIB inválido.")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        _admin(db, actor_id)
        row = _row(db)
        if action == "pause":
            if row:
                db.execute("UPDATE local_sib_refresh_state SET running=0,updated_at=CURRENT_TIMESTAMP WHERE id=1")
            return {"ok": True, "refresh": summary(db), "message": "Parada solicitada. O progresso da atualização foi preservado."}
        scan = db.execute("SELECT continuous,lease_token,lease_until FROM local_sib_scan_state WHERE id=1").fetchone()
        discovery = db.execute("SELECT lease_token,lease_until FROM local_sib_discovery_state WHERE id=1").fetchone()
        issue = db.execute("SELECT token,lease_until FROM local_sib_issue_job WHERE id=1").fetchone()
        if ((scan and (scan["continuous"] or lease_active(scan["lease_token"], scan["lease_until"])))
                or (discovery and lease_active(discovery["lease_token"], discovery["lease_until"]))
                or (issue and lease_active(issue["token"], issue["lease_until"]))):
            raise ValueError("Aguarde a consulta SIB atual terminar antes de atualizar cadastros já trazidos.")
        if row and lease_active(row["lease_token"], row["lease_until"]):
            raise ValueError("Já existe uma atualização de cadastros em andamento.")
        settings = db.execute("SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1").fetchone()
        if not settings or not settings["sib_active"] or settings["sib_base_url"] != client.config.base_url:
            raise ValueError("A configuração SIB mudou. Atualize a tela antes de continuar.")
        if action == "start":
            queue = _queue(db)
            if not queue:
                return {"ok": True, "refresh": summary(db), "message": "Ainda não há cadastros SIB trazidos para atualizar."}
            db.execute("""INSERT INTO local_sib_refresh_state(id,source_url,queue_json,actor_id)
                VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
                source_url=excluded.source_url,queue_json=excluded.queue_json,next_index=0,
                changed=0,unresolved=0,running=0,error='',lease_token='',lease_until=0,
                actor_id=excluded.actor_id,updated_at=CURRENT_TIMESTAMP""",
                (client.config.base_url, _json(queue), actor_id))
        elif not row or row["source_url"] != client.config.base_url:
            raise ValueError("A origem SIB mudou. Inicie uma nova atualização dos cadastros.")
        elif summary(db)["done"]:
            return {"ok": True, "refresh": summary(db), "message": "Atualização concluída. Você pode iniciá-la novamente quando precisar."}
        token = worker_token()
        db.execute("UPDATE local_sib_refresh_state SET running=1,error='',lease_token=?,lease_until=?,actor_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
                   (token, time.time() + LEASE_SECONDS, actor_id))
        result = summary(db)
    try:
        if action == "start":
            backup_database(database, "antes-atualizar-cadastros-sib")
        thread = threading.Thread(target=_worker, args=(Path(database), client, actor_id, token),
                                  daemon=True, name="SIB-atualizacao")
        thread.start()
    except Exception:
        with connect(database) as db:
            db.execute("UPDATE local_sib_refresh_state SET running=0,lease_token='',lease_until=0 WHERE lease_token=?", (token,))
        raise
    return {"ok": True, "refresh": result,
            "message": "Atualização dos cadastros já trazidos iniciada. É possível parar e continuar depois."}


def _worker(database, client, actor_id, token):
    from .database import connect
    try:
        while True:
            with connect(database) as db:
                row = _row(db)
                if not row or row["lease_token"] != token:
                    raise RuntimeError("Esta atualização foi substituída.")
                _admin(db, actor_id)
                queue = json.loads(row["queue_json"])
                index = row["next_index"]
                if not row["running"] or index >= len(queue):
                    break
                item = queue[index]
                db.execute("UPDATE local_sib_refresh_state SET lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?",
                           (time.time() + LEASE_SECONDS, token))
            unresolved = False
            employment = company = None
            try:
                payload = client.get_employee_raw(item['registration'], contract_type_code=item.get('contractTypeCode', ''),
                                                  branch_code=item.get('branchCode', ''), employer_code=item.get('employerCode', ''))
                employment, company = map_raw_employee(payload, item["registration"])
                if employment["source_id"] != item["sourceId"]:
                    unresolved = True
            except IntegrationError as error:
                if error.status_code not in (404, 409, 500):
                    raise
                unresolved = True
            except ValueError:
                unresolved = True
            with connect(database) as db:
                db.execute("BEGIN IMMEDIATE")
                row = _row(db)
                if not row or row["lease_token"] != token or row["next_index"] != index:
                    raise RuntimeError("O progresso da atualização mudou.")
                _admin(db, actor_id)
                changed = 0
                if not unresolved:
                    previous = db.execute("SELECT data_json,raw_json,cycle FROM local_sib_records WHERE dataset='employments' AND source_id=?",
                                          (item["sourceId"],)).fetchone()
                    from .sib_gap_recovery import same_identity
                    previous_data = json.loads(previous['data_json']) if previous else {}
                    previous_raw = json.loads(previous['raw_json']) if previous else {}
                    history_before_data = dict(previous_data)
                    if previous:
                        previous_data['contractTypeCode'] = previous_data.get('contractTypeCode') or json.loads(previous['raw_json']).get('VIN_FUN')
                    if not previous or not same_identity(previous_data, employment['data']):
                        unresolved = True
                    else:
                        employment = classified_employee(db, employment)
                        changed = int(previous["data_json"] != _json(employment["data"])
                                      or previous["raw_json"] != _json(employment["raw"]))
                        if changed:
                            parts = item["sourceId"].split(":")
                            _record_change(db, source_url=client.config.base_url, dataset="employment",
                                source_id=item["sourceId"], registration=item["registration"],
                                record_name=employment["data"].get("name"),
                                company_name=(company or {}).get("data", {}).get("name"),
                                branch_code=parts[1] if len(parts) == 3 else "",
                                before_data=history_before_data, after_data=employment["data"],
                                before_raw=previous_raw, after_raw=employment["raw"])
                        _upsert(db, "employments", employment, previous["cycle"])
                        if company:
                            original_company = db.execute("SELECT cycle,data_json,raw_json FROM local_sib_records WHERE dataset='companies' AND source_id=?",
                                                          (company["source_id"],)).fetchone()
                            original_company_data = json.loads(original_company["data_json"]) if original_company else {}
                            original_company_raw = json.loads(original_company["raw_json"]) if original_company else {}
                            company_changed = (not original_company
                                or original_company["data_json"] != _json(company["data"])
                                or original_company["raw_json"] != _json(company["raw"]))
                            if company_changed:
                                _record_change(db, source_url=client.config.base_url, dataset="company",
                                    source_id=company["source_id"], registration="",
                                    record_name=company["data"].get("name"), company_name=company["data"].get("name"),
                                    branch_code="", before_data=original_company_data, after_data=company["data"],
                                    before_raw=original_company_raw, after_raw=company["raw"])
                            changed = int(changed or not original_company
                                          or original_company["data_json"] != _json(company["data"])
                                          or original_company["raw_json"] != _json(company["raw"]))
                            _upsert(db, "companies", company, original_company["cycle"] if original_company else previous["cycle"])
                if unresolved:
                    from .sib_discovery import remember_gap
                    remember_gap(db, client.config.base_url, item['registration'], 'FICHA_NAO_CONFIRMADA')
                db.execute("""UPDATE local_sib_refresh_state SET next_index=next_index+1,
                    changed=changed+?,unresolved=unresolved+?,lease_until=?,updated_at=CURRENT_TIMESTAMP
                    WHERE lease_token=?""", (changed, int(unresolved), time.time() + LEASE_SECONDS, token))
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, PermissionError, IntegrationError)) else "A atualização foi pausada após uma falha. Use Continuar."
        try:
            with connect(database) as db:
                db.execute("UPDATE local_sib_refresh_state SET error=? WHERE lease_token=?", (message[:300], token))
        except sqlite3.DatabaseError:
            pass
    finally:
        with connect(database) as db:
            db.execute("UPDATE local_sib_refresh_state SET running=0,lease_token='',lease_until=0,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?", (token,))
