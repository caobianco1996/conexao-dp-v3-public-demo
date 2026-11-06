"""Continuous, read-only Bridge checks of SIB registrations already in V3."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import integration_secrets, integrations
from .sib_record_mapping import _date
from .sib_refresh import _queue, refresh_one
from .sib_runtime import lease_active, worker_token


INTERVAL_SECONDS = 0
EMPTY_QUEUE_SECONDS = 30
ERROR_RETRY_SECONDS = 60
RAW_SAMPLES_PER_BATCH = 2
LEASE_SECONDS = 180
SCHEDULER_TURN_SECONDS = 15
SCHEDULER_BATCH_SIZE = 10
PRIORITY_TURN_SECONDS = 45
PRIORITY_IDLE_SECONDS = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_auto_refresh_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 source_url TEXT NOT NULL DEFAULT '', queue_json TEXT NOT NULL DEFAULT '[]',
 next_index INTEGER NOT NULL DEFAULT 0, raw_cursor INTEGER NOT NULL DEFAULT 0,
 raw_targets_json TEXT NOT NULL DEFAULT '[]', checked INTEGER NOT NULL DEFAULT 0,
 changed INTEGER NOT NULL DEFAULT 0, unresolved INTEGER NOT NULL DEFAULT 0,
 cycle_started_at REAL NOT NULL DEFAULT 0, last_completed_at REAL NOT NULL DEFAULT 0,
 next_due_at REAL NOT NULL DEFAULT 0, running INTEGER NOT NULL DEFAULT 0,
 lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 enabled INTEGER NOT NULL DEFAULT 1,
 error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS local_sib_refresh_priority_state (
 id INTEGER PRIMARY KEY CHECK(id=1), active INTEGER NOT NULL DEFAULT 0,
 previous_scan_continuous INTEGER NOT NULL DEFAULT 0,
 previous_discovery_enabled INTEGER NOT NULL DEFAULT 0,
 previous_absence_enabled INTEGER NOT NULL DEFAULT 0,
 started_at REAL NOT NULL DEFAULT 0, finished_at REAL NOT NULL DEFAULT 0
);
"""


def _row(db):
    return db.execute("SELECT * FROM local_sib_auto_refresh_state WHERE id=1").fetchone()


def _lease_state(db):
    # The saved queue can contain tens of thousands of items. Checks performed
    # between individual requests need only these small control fields.
    return db.execute("SELECT enabled,lease_token,next_index,raw_cursor FROM local_sib_auto_refresh_state WHERE id=1").fetchone()


def _priority_active(db):
    row = db.execute("SELECT active FROM local_sib_refresh_priority_state WHERE id=1").fetchone()
    return bool(row and row[0])


def _finish_priority(db, now=None):
    """Restore the other SIB routines after the prioritized ficha sweep."""
    state = db.execute("SELECT * FROM local_sib_refresh_priority_state WHERE id=1 AND active=1").fetchone()
    if not state:
        return False
    now = time.time() if now is None else now
    db.execute("UPDATE local_sib_discovery_state SET enabled=? WHERE id=1",
               (state["previous_discovery_enabled"],))
    db.execute("UPDATE local_sib_absence_state SET enabled=? WHERE id=1",
               (state["previous_absence_enabled"],))
    db.execute("UPDATE local_sib_scan_state SET continuous=? WHERE id=1",
               (state["previous_scan_continuous"],))
    db.execute("UPDATE local_sib_refresh_priority_state SET active=0,finished_at=? WHERE id=1", (now,))
    return True


def summary(db):
    row = _row(db)
    if not row:
        return {"enabled": True, "continuous": True, "intervalSeconds": INTERVAL_SECONDS, "running": False,
                "stopping": False, "total": 0, "checked": 0, "changed": 0, "unresolved": 0,
                "lastCompletedAt": 0, "nextDueAt": 0, "error": ""}
    enabled = bool(row["enabled"])
    running = bool(row["running"] and lease_active(row["lease_token"], row["lease_until"]))
    return {"enabled": enabled, "continuous": enabled, "intervalSeconds": INTERVAL_SECONDS,
            "running": running, "stopping": bool(not enabled and running),
            "priorityMode": _priority_active(db),
            "total": len(json.loads(row["queue_json"])), "checked": row["checked"],
            "changed": row["changed"], "unresolved": row["unresolved"],
            "lastCompletedAt": row["last_completed_at"], "nextDueAt": row["next_due_at"],
            "error": row["error"]}


def control(database: Path, client, actor_id: int, action: str):
    """Pause or resume the continuous saved-record sweep without losing its cursor."""
    from .database import connect

    if action not in {"pause", "resume", "prioritize"}:
        raise ValueError("Comando da atualização automática SIB inválido.")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
        if not actor or actor["role"] != "ADMIN":
            raise PermissionError("Somente Administrador pode controlar a atualização automática do SIB.")
        row = _row(db)
        now = time.time()
        if action == "pause":
            if row:
                running = bool(row["running"] and lease_active(row["lease_token"], row["lease_until"]))
                db.execute("""UPDATE local_sib_auto_refresh_state SET enabled=0,
                    running=CASE WHEN ? THEN running ELSE 0 END,
                    lease_token=CASE WHEN ? THEN lease_token ELSE '' END,
                    lease_until=CASE WHEN ? THEN lease_until ELSE 0 END
                    WHERE id=1""", (int(running), int(running), int(running)))
            else:
                settings = db.execute("SELECT sib_base_url FROM local_integration_settings WHERE id=1").fetchone()
                db.execute("INSERT INTO local_sib_auto_refresh_state(id,source_url,enabled) VALUES(1,?,0)",
                           (settings["sib_base_url"] if settings else "",))
            result = summary(db)
            message = ("Parada solicitada. O lote em andamento será concluído e o progresso ficará salvo."
                       if result["stopping"] else "Atualização pausada. O progresso ficou salvo; novas matrículas e lacunas podem ser consultadas.")
            return {"ok": True, "autoRefresh": result, "message": message}

        if not client:
            raise ValueError("Configure e habilite a conexão SIB antes de retomar a atualização.")
        settings = db.execute("SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1").fetchone()
        if not settings or not settings["sib_active"] or settings["sib_base_url"] != client.config.base_url:
            raise ValueError("A configuração SIB mudou. Atualize a tela antes de continuar.")
        if action == "prioritize":
            if _priority_active(db):
                return {"ok": True, "autoRefresh": summary(db),
                        "message": "A atualização de fichas já está priorizada; as outras rotinas serão reativadas ao concluir a rodada."}
            discovery = db.execute("SELECT enabled FROM local_sib_discovery_state WHERE id=1").fetchone()
            absences = db.execute("SELECT enabled FROM local_sib_absence_state WHERE id=1").fetchone()
            scan = db.execute("SELECT continuous FROM local_sib_scan_state WHERE id=1").fetchone()
            db.execute("""INSERT INTO local_sib_refresh_priority_state
                (id,active,previous_scan_continuous,previous_discovery_enabled,previous_absence_enabled,started_at,finished_at)
                VALUES(1,1,?,?,?, ?,0) ON CONFLICT(id) DO UPDATE SET active=1,
                previous_scan_continuous=excluded.previous_scan_continuous,
                previous_discovery_enabled=excluded.previous_discovery_enabled,
                previous_absence_enabled=excluded.previous_absence_enabled,
                started_at=excluded.started_at,finished_at=0""",
                (int(bool(scan and scan[0])), int(bool(discovery and discovery[0])),
                 int(bool(absences and absences[0])), time.time()))
            db.execute("UPDATE local_sib_discovery_state SET enabled=0 WHERE id=1")
            db.execute("UPDATE local_sib_absence_state SET enabled=0 WHERE id=1")
            db.execute("UPDATE local_sib_scan_state SET continuous=0 WHERE id=1")
            if row:
                db.execute("UPDATE local_sib_auto_refresh_state SET enabled=1,next_due_at=0,error='' WHERE id=1")
            else:
                db.execute("INSERT INTO local_sib_auto_refresh_state(id,source_url,enabled,next_due_at) VALUES(1,?,1,0)",
                           (client.config.base_url,))
            return {"ok": True, "autoRefresh": summary(db),
                    "message": "Atualização de fichas priorizada. Busca de novas matrículas, recuperação e afastamentos serão retomados ao concluir a rodada atual."}
        if row and lease_active(row["lease_token"], row["lease_until"]):
            raise ValueError("Aguarde o lote em andamento encerrar antes de retomar a atualização.")
        if row:
            db.execute("""UPDATE local_sib_auto_refresh_state SET enabled=1,running=0,
                lease_token='',lease_until=0,next_due_at=0,error='' WHERE id=1""")
        else:
            db.execute("""INSERT INTO local_sib_auto_refresh_state(id,source_url,enabled,next_due_at)
                VALUES(1,?,1,0)""", (client.config.base_url,))
        return {"ok": True, "autoRefresh": summary(db),
                "message": "Atualização automática retomada do ponto salvo."}


def _different(canonical, stored, company, company_row):
    """Compare only fields confirmed by the batch API; missing keys mean unknown."""
    checks = (
        ("name", "name", lambda value: str(value or "").strip()),
        ("cpf", "cpf", lambda value: "".join(char for char in str(value or "") if char.isdigit()).zfill(11)),
        ("admission_date", "admissionDate", _date),
        ("termination_date", "dismissalDate", _date),
        ("client_code", "companyId", lambda value: str(value or "").strip()),
        ("contract_end_date", "firstDueDate", _date),
        ("extension_date", "secondDueDate", _date),
    )
    for source_key, local_key, normalize in checks:
        if source_key in canonical and canonical[source_key] is not None:
            if normalize(canonical[source_key]) != normalize(stored.get(local_key)):
                return True
    if company and company_row and company.get("legal_name") is not None:
        if str(company["legal_name"]).strip() != str(company_row.get("name") or "").strip():
            return True
    return False


def _client(db):
    row = db.execute("SELECT * FROM local_integration_settings WHERE id=1").fetchone()
    if not row or not row["sib_active"] or not row["sib_api_key_protected"]:
        return None
    config = integrations.SibConfig(
        base_url=row["sib_base_url"],
        api_key=integration_secrets.unprotect_secret(row["sib_api_key_protected"]),
        timeout_seconds=int(row["sib_timeout_seconds"]),
        cf_client_id=integration_secrets.unprotect_secret(row["sib_cf_client_id_protected"]),
        cf_client_secret=integration_secrets.unprotect_secret(row["sib_cf_client_secret_protected"]),
        ca_bundle_path=row["sib_ca_bundle_path"],
    )
    return integrations.SibClient(config)


def _scan_busy(db):
    scan = db.execute("SELECT continuous,lease_token,lease_until FROM local_sib_scan_state WHERE id=1").fetchone()
    refresh = db.execute("SELECT lease_token,lease_until FROM local_sib_refresh_state WHERE id=1").fetchone()
    issues = db.execute("SELECT token,lease_until FROM local_sib_issue_job WHERE id=1").fetchone()
    discovery = db.execute("SELECT lease_token,lease_until FROM local_sib_discovery_state WHERE id=1").fetchone()
    absences = db.execute("SELECT lease_token,lease_until FROM local_sib_absence_state WHERE id=1").fetchone()
    return bool((scan and (scan["continuous"] or lease_active(scan["lease_token"], scan["lease_until"])))
                or (refresh and lease_active(refresh["lease_token"], refresh["lease_until"]))
                or (discovery and lease_active(discovery["lease_token"], discovery["lease_until"]))
                or (issues and lease_active(issues["token"], issues["lease_until"]))
                or (absences and lease_active(absences["lease_token"], absences["lease_until"])))


def _claim(db, source_url, now):
    """Claim one due sweep, or continue a sweep interrupted by server restart."""
    db.execute("BEGIN IMMEDIATE")
    if _scan_busy(db):
        return None
    row = _row(db)
    if row and not row["enabled"]:
        return None
    if row and lease_active(row["lease_token"], row["lease_until"]):
        return None
    if row and row["error"] and now < row["next_due_at"]:
        return None
    if (row and _priority_active(db) and row["source_url"] == source_url
            and row["next_index"] >= len(json.loads(row["queue_json"]))):
        # Recover the completion edge if the process restarted after its last
        # checkpoint but before it could restore the temporarily paused jobs.
        _finish_priority(db, now)
        return None
    if row and row["source_url"] == source_url and row["next_index"] < len(json.loads(row["queue_json"])):
        # During the one-time prioritized sweep, preserve the original full
        # queue so even terminated records get their final job-title update.
        # Later sweeps reconcile against the active-only queue.
        saved_queue = json.loads(row['queue_json'])
        queue = saved_queue
        if not _priority_active(db):
            current = {item['sourceId']: item for item in _queue(db)}
            processed = saved_queue[:row['next_index']]
            pending = []
            seen = {item['sourceId'] for item in processed}
            for item in saved_queue[row['next_index']:]:
                source_id = item['sourceId']
                if source_id in current and source_id not in seen:
                    pending.append(current[source_id])
                    seen.add(source_id)
            pending.extend(item for source_id, item in current.items() if source_id not in seen)
            queue = processed + pending
            if queue != saved_queue:
                db.execute('UPDATE local_sib_auto_refresh_state SET queue_json=?,raw_targets_json=? WHERE id=1',
                           (json.dumps(queue), json.dumps(_raw_targets(queue, row['raw_cursor']))))
        if row['next_index'] >= len(queue):
            return None
    else:
        if row and not json.loads(row["queue_json"]) and now < row["next_due_at"]:
            return None
        queue = _queue(db)
        if not queue:
            db.execute("""INSERT INTO local_sib_auto_refresh_state(id,source_url,next_due_at)
                VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
                queue_json='[]',next_index=0,checked=0,next_due_at=excluded.next_due_at,error=''""",
                (source_url, now + EMPTY_QUEUE_SECONDS))
            _finish_priority(db, now)
            return None
        raw_cursor = row["raw_cursor"] if row and row["source_url"] == source_url else 0
        # Spread raw checks across the entire queue, then rotate within each
        # batch on subsequent sweeps. Twenty checks per 32,000-record sweep
        # would leave fields absent from the canonical API stale for too long.
        targets = _raw_targets(queue, raw_cursor)
        db.execute("""INSERT INTO local_sib_auto_refresh_state
            (id,source_url,queue_json,raw_cursor,raw_targets_json,cycle_started_at)
            VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,queue_json=excluded.queue_json,next_index=0,
            raw_cursor=excluded.raw_cursor,raw_targets_json=excluded.raw_targets_json,
            checked=0,changed=0,unresolved=0,cycle_started_at=excluded.cycle_started_at,error=''""",
            (source_url, json.dumps(queue), raw_cursor, json.dumps(targets), now))
    token = worker_token()
    db.execute("""UPDATE local_sib_auto_refresh_state SET running=1,error='',lease_token=?,lease_until=? WHERE id=1""",
               (token, now + LEASE_SECONDS))
    return token


def _raw_targets(queue, cursor):
    targets = []
    for start in range(0, len(queue), 50):
        batch = queue[start:start + 50]
        targets.extend(batch[(cursor + offset) % len(batch)]['sourceId']
                       for offset in range(min(RAW_SAMPLES_PER_BATCH, len(batch))))
    return targets


def _renew(db, token):
    db.execute("UPDATE local_sib_auto_refresh_state SET lease_until=? WHERE lease_token=?",
               (time.time() + LEASE_SECONDS, token))


def _fresh_due(db, source_url):
    """Yield to the independent fresh-ID clock, including during a ficha batch."""
    try:
        row = db.execute("SELECT enabled,source_url,fresh_due_at FROM local_sib_discovery_state WHERE id=1").fetchone()
    except sqlite3.OperationalError as error:
        # Old databases may be opened before the additive discovery migration.
        if 'no such column: fresh_due_at' in str(error):
            return False
        raise
    return bool(row and row["enabled"]
                and row["source_url"] == source_url
                and float(row["fresh_due_at"] or 0) <= time.time())


def _checkpoint(db, token, index, changed, unresolved):
    """Advance only a finished item, together with all of its counters."""
    db.execute("BEGIN IMMEDIATE")
    result = db.execute("""UPDATE local_sib_auto_refresh_state SET next_index=next_index+1,
        checked=checked+1,changed=changed+?,unresolved=unresolved+?
        WHERE lease_token=? AND next_index=?""", (changed, unresolved, token, index))
    return result.rowcount == 1


def run_due(database: Path, client, *, now=None, stop_event=None, one_batch=False,
            budget_seconds=None):
    """Continue the saved sweep, yielding between finished items when requested.

    A Bridge request already in flight must finish, so the budget limits the
    next request rather than interrupting a read and losing its result.
    """
    from .database import connect

    now = time.time() if now is None else now
    deadline = (time.monotonic() + max(0, budget_seconds)
                if budget_seconds is not None else None)
    source_url = client.config.base_url

    def stopped():
        return bool(stop_event and stop_event.is_set())

    def budget_spent():
        return deadline is not None and time.monotonic() >= deadline

    with connect(database) as db:
        if _fresh_due(db, source_url) or stopped() or budget_spent():
            return False
        token = _claim(db, source_url, now)
    if not token:
        return False
    error_message = ""
    try:
        with connect(database) as db:
            saved = _row(db)
            if not saved or saved['lease_token'] != token:
                return True
            queue = json.loads(saved['queue_json'])
            samples = set(json.loads(saved['raw_targets_json']))
        while not stopped():
            with connect(database) as db:
                if _scan_busy(db):
                    break
                row = _lease_state(db)
                if not row or row["lease_token"] != token:
                    return True
                if not row["enabled"]:
                    break
                index = row["next_index"]
                if index >= len(queue):
                    db.execute("""UPDATE local_sib_auto_refresh_state SET running=0,lease_token='',lease_until=0,
                        last_completed_at=?,next_due_at=?,raw_cursor=?,error='' WHERE lease_token=?""",
                        (time.time(), time.time(),
                         (row["raw_cursor"] + RAW_SAMPLES_PER_BATCH) % 50, token))
                    _finish_priority(db)
                    return True
                if budget_spent() or _fresh_due(db, source_url):
                    break
                priority_mode = _priority_active(db)
                batch_size = 50 if priority_mode or budget_seconds is None else SCHEDULER_BATCH_SIZE
                batch = queue[index:index + batch_size]
                placeholders = ','.join('?' for _ in batch)
                stored_records = {stored['source_id']: json.loads(stored['data_json']) for stored in db.execute(
                    f"SELECT source_id,data_json FROM local_sib_records WHERE dataset='employments' AND source_id IN ({placeholders})",
                    [item['sourceId'] for item in batch])}
                direct_raw = {item['sourceId'] for item in batch
                              if item['sourceId'] in stored_records and (item['sourceId'] in samples
                              or (priority_mode and not str(stored_records[item['sourceId']].get('jobTitle') or '').strip()))}
                _renew(db, token)
            # A full read already verifies the composite identity. Asking for a
            # number-only summary first duplicates that read and can reject a
            # valid internship when its number is reused in another branch.
            registrations = list(dict.fromkeys(item["registration"] for item in batch if item['sourceId'] not in direct_raw))
            results = ({item["id_folha"]: item for item in client.get_scan_batch(registrations)}
                       if registrations else {})
            processed = 0
            for offset, item in enumerate(batch):
                if stopped():
                    break
                with connect(database) as db:
                    row = _lease_state(db)
                    if (_scan_busy(db) or not row or row["lease_token"] != token
                            or not row["enabled"] or _fresh_due(db, source_url)):
                        break
                changed = unresolved = 0
                with connect(database) as db:
                    removed = db.execute("SELECT 1 FROM local_sib_presence WHERE source_url=? AND source_id=? AND status='EXCLUDED'", (source_url, item['sourceId'])).fetchone()
                    if removed:
                        _checkpoint(db, token, index + offset, 0, 0)
                if removed:
                    processed += 1
                    continue
                response = results.get(item["registration"], {})
                data = response.get("data")
                needs_raw = item['sourceId'] in direct_raw
                if not needs_raw and (response.get("error") or not data):
                    unresolved = 1
                    with connect(database) as db:
                        from .sib_discovery import remember_gap
                        remember_gap(db, source_url, item["registration"],
                                     response.get("error") or "FICHA_NAO_CONFIRMADA")
                elif not needs_raw:
                    with connect(database) as db:
                        stored = stored_records.get(item['sourceId'])
                        company_row = (db.execute("SELECT data_json FROM local_sib_records WHERE dataset='companies' AND source_id=?",
                                                 (stored.get("companyId") or "",)).fetchone() if stored else None)
                        local_company = json.loads(company_row["data_json"]) if company_row else None
                    needs_raw = stored is not None and _different(data["employee"], stored, data.get("client"), local_company)
                if needs_raw:
                    # Do not renew a raw-read lease when fresh IDs are due.
                    # Finish at least one item after a slow batch response;
                    # otherwise a short budget could repeatedly fetch it.
                    if budget_spent() and processed:
                        break
                    with connect(database) as db:
                        row = _lease_state(db)
                        if (stopped() or _scan_busy(db) or _fresh_due(db, source_url)
                                or not row or row["lease_token"] != token or not row["enabled"]):
                            break
                        _renew(db, token)
                    changed, unresolved = refresh_one(database, client, item, source_url=source_url)
                    if unresolved:
                        with connect(database) as db:
                            from .sib_discovery import remember_gap
                            remember_gap(db, source_url, item["registration"], "FICHA_NAO_CONFIRMADA")
                with connect(database) as db:
                    if not _checkpoint(db, token, index + offset, changed, unresolved):
                        return True
                processed += 1
            if processed < len(batch):
                break
            if one_batch and index + len(batch) < len(queue):
                break
    except Exception as error:
        error_message = str(error) if isinstance(error, (integrations.IntegrationError, ValueError)) else "A verificação automática foi interrompida; a próxima tentativa retomará o lote."
    finally:
        with connect(database) as db:
            db.execute("""UPDATE local_sib_auto_refresh_state SET running=0,lease_token='',lease_until=0,error=?,
                next_due_at=CASE WHEN ?<>'' THEN ? ELSE next_due_at END WHERE lease_token=?""",
                       (error_message[:300], error_message, time.time() + ERROR_RETRY_SECONDS, token))
    return True


class Scheduler:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="SIB-verificacao-automatica")

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _run(self):
        from .database import connect

        background_turn = 0
        while not self.stop_event.is_set():
            fresh_due = False
            priority_progress = False
            try:
                from . import sib_operations
                sib_operations.run_due(self.database)
                with connect(self.database) as db:
                    client = _client(db)
                    state = db.execute("SELECT enabled,checked,error,next_due_at FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
                    priority = bool(state and state['enabled'] and _priority_active(db))
                    from . import sib_number_recovery
                    intern_priority = bool(client and sib_number_recovery.priority_active(db, client.config.base_url))
                if client:
                    if intern_priority:
                        from . import sib_discovery
                        with connect(self.database) as db:
                            before = db.execute('SELECT checked FROM local_sib_discovery_state WHERE id=1').fetchone()
                        sib_discovery.run_due(self.database, client, stop_event=self.stop_event, one_batch=True)
                        with connect(self.database) as db:
                            after = db.execute('SELECT enabled,checked,error FROM local_sib_discovery_state WHERE id=1').fetchone()
                            priority_progress = bool(before and after and after['enabled'] and not after['error']
                                and after['checked'] > before['checked']
                                and sib_number_recovery.priority_active(db, client.config.base_url))
                    elif priority:
                        # Other Bridge routines are explicitly paused for this
                        # sweep, so do not spend two of every three turns on them.
                        # Errors still keep their saved retry deadline.
                        with connect(self.database) as db:
                            fresh_due = _fresh_due(db, client.config.base_url)
                        if fresh_due and not self.stop_event.is_set():
                            # A manual resume may re-enable discovery during a
                            # prioritized sweep. Serve its due clock so neither
                            # routine remains blocked waiting for the other.
                            from . import sib_discovery
                            sib_discovery.run_due(self.database, client, stop_event=self.stop_event,
                                                  one_batch=True, fresh_only=True)
                            with connect(self.database) as db:
                                fresh_due = _fresh_due(db, client.config.base_url)
                        if not fresh_due and not self.stop_event.is_set() and (not state['error'] or state['next_due_at'] <= time.time()):
                            run_due(self.database, client, stop_event=self.stop_event, one_batch=False,
                                    budget_seconds=PRIORITY_TURN_SECONDS)
                            with connect(self.database) as db:
                                after = db.execute("SELECT enabled,checked,error,next_due_at FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
                                priority_progress = bool(after and after['enabled'] and not after['error']
                                    and after['checked'] > state['checked'] and after['next_due_at'] <= time.time()
                                    and _priority_active(db) and not self.stop_event.is_set())
                    else:
                        from .sib_scan import resume_due
                        from . import sib_discovery
                        resume_due(self.database, client)
                        sib_discovery.run_due(self.database, client, stop_event=self.stop_event,
                                              one_batch=True, fresh_only=True)
                        with connect(self.database) as db:
                            fresh_due = _fresh_due(db, client.config.base_url)
                        if not fresh_due and not self.stop_event.is_set():
                            # Only one background unit runs before fresh IDs get
                            # another chance. Continuous fichas keep their cursor.
                            slot = background_turn % 3
                            background_turn += 1
                            if slot == 0:
                                run_due(self.database, client, stop_event=self.stop_event, one_batch=True,
                                        budget_seconds=SCHEDULER_TURN_SECONDS)
                            elif slot == 1:
                                sib_discovery.run_due(self.database, client, stop_event=self.stop_event, one_batch=True)
                            else:
                                from . import sib_absences
                                sib_absences.run_due(self.database, client, stop_event=self.stop_event)
                        if not self.stop_event.is_set():
                            sib_discovery.run_due(self.database, client, stop_event=self.stop_event,
                                                  one_batch=True, fresh_only=True)
            except (sqlite3.DatabaseError, OSError, ValueError, integrations.IntegrationError):
                # Configuration may be edited while the server remains open.
                pass
            self.stop_event.wait(PRIORITY_IDLE_SECONDS if priority_progress else 2 if fresh_due else 5)
