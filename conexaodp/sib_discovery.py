"""Near-real-time discovery of newly assigned SIB payroll IDs."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import integrations
from .sib_runtime import lease_active, worker_token
from . import sib_number_recovery as number_recovery


INTERVAL_SECONDS = 60
FRESH_IDS_PER_CYCLE = 50
LOOKBACK_IDS = 20
PROBE_IDS = 1000
LEASE_SECONDS = 180
RAW_READS_PER_TURN = 5
TURN_SECONDS = 25
MAX_GAP_RETRY_SECONDS = 21600

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_discovery_state (
 id INTEGER PRIMARY KEY CHECK(id=1), source_url TEXT NOT NULL DEFAULT '',
 actor_id INTEGER REFERENCES users(id), enabled INTEGER NOT NULL DEFAULT 0,
 cursor INTEGER NOT NULL DEFAULT 0, intern_cursor INTEGER NOT NULL DEFAULT 0,
 probe_offset INTEGER NOT NULL DEFAULT 0,
 running INTEGER NOT NULL DEFAULT 0,
 checked INTEGER NOT NULL DEFAULT 0, imported INTEGER NOT NULL DEFAULT 0,
 missing INTEGER NOT NULL DEFAULT 0, ambiguous INTEGER NOT NULL DEFAULT 0,
 last_cycle_at REAL NOT NULL DEFAULT 0, next_due_at REAL NOT NULL DEFAULT 0,
 queue_json TEXT NOT NULL DEFAULT '[]', queue_index INTEGER NOT NULL DEFAULT 0,
 activity TEXT NOT NULL DEFAULT '', current_registration TEXT NOT NULL DEFAULT '',
 current_batch_json TEXT NOT NULL DEFAULT '[]',
 gap_ids_json TEXT NOT NULL DEFAULT '[]',
 monitor_version INTEGER NOT NULL DEFAULT 0, queue_kind TEXT NOT NULL DEFAULT '',
 queue_lane TEXT NOT NULL DEFAULT '', fresh_due_at REAL NOT NULL DEFAULT 0,
 fresh_last_at REAL NOT NULL DEFAULT 0,
 catalog_supported INTEGER NOT NULL DEFAULT -1, catalog_progress_json TEXT NOT NULL DEFAULT '{}',
 catalog_next_at REAL NOT NULL DEFAULT 0, catalog_last_completed_at REAL NOT NULL DEFAULT 0,
 catalog_error TEXT NOT NULL DEFAULT '',
 lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_discovery_gaps (
 source_url TEXT NOT NULL, payroll_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_checked_at REAL NOT NULL DEFAULT 0, next_check_at REAL NOT NULL DEFAULT 0,
 last_reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(source_url,payroll_id)
);
CREATE INDEX IF NOT EXISTS local_sib_discovery_gaps_due
 ON local_sib_discovery_gaps(source_url,status,next_check_at,last_checked_at);
CREATE TABLE IF NOT EXISTS local_sib_discovery_gap_seed (
 source_url TEXT PRIMARY KEY, coverage_count INTEGER NOT NULL DEFAULT 0,
 seeded_at REAL NOT NULL DEFAULT 0
);
"""
SCHEMA += number_recovery.SCHEMA
from . import sib_presence
SCHEMA += sib_presence.SCHEMA


def _row(db):
    return db.execute("SELECT * FROM local_sib_discovery_state WHERE id=1").fetchone()


def _scan_done(db):
    row = db.execute("SELECT * FROM local_sib_scan_state WHERE id=1").fetchone()
    return bool(row and row["next_id"] > row["end_id"]
                and not json.loads(row["pending_json"])
                and not row["running"]
                and not lease_active(row["lease_token"], row["lease_until"]))


def _sequence_profiles(db):
    """Different branches/modalities can have independent numbering sequences."""
    from .registry import sib_activity
    groups = {}
    # Read just sequence metadata. Loading complete fiches and resolving job
    # descriptions here held the writer lock during each fresh-ID window.
    rows = db.execute("""SELECT source_id,
        json_extract(data_json,'$.registration') registration,
        COALESCE(NULLIF(json_extract(data_json,'$.contractTypeCode'),''),
                 json_extract(raw_json,'$.VIN_FUN')) code,
        json_extract(data_json,'$.contractType') kind,
        json_extract(data_json,'$.admissionDate') admission,
        json_extract(data_json,'$.dismissalDate') dismissal,
        json_extract(data_json,'$.activityDatesValid') dates_valid
        FROM local_sib_records INDEXED BY local_sib_records_employment_fields
        WHERE dataset='employments'""")
    for row in rows:
        parts = row['source_id'].split(':')
        registration = str(row['registration'] or '')
        if len(parts) != 3 or not registration.isdigit():
            continue
        kind, code = str(row['kind'] or ''), str(row['code'] if row['code'] is not None else '')
        key = (parts[0], parts[1], code or kind)
        profile = groups.setdefault(key, {'namespace': ':'.join(key), 'kind': kind, 'code': code,
            'employer': parts[0], 'branch': parts[1], 'maximum': 0, 'latestAdmission': '', 'active': False})
        profile['maximum'] = max(profile['maximum'], int(registration))
        profile['latestAdmission'] = max(profile['latestAdmission'], str(row['admission'] or ''))
        profile['active'] = profile['active'] or sib_activity({
            'admissionDate': row['admission'], 'dismissalDate': row['dismissal'],
            'activityDatesValid': row['dates_valid'] != 0})[0] is True
    return list(groups.values())


def _profile_highwater(profiles, *, interns=False):
    choices = [item for item in profiles if (item.get('code') == '15' or item['kind'] == 'Estágio') == interns]
    if interns and choices:
        # An obsolete internship sequence can contain a higher number than the
        # current branch. It must not hide the next internship near 400.
        active = [item for item in choices if item['active']]
        choices = active or [max(choices, key=lambda item: (item['latestAdmission'], item['maximum']))]
    return max((item['maximum'] for item in choices), default=0)


def _highwater(db, *, interns=False):
    return _profile_highwater(_sequence_profiles(db), interns=interns)


def remember_gap(db, source_url, payroll, reason):
    """Keep identity failures recoverable even when this number is already saved."""
    db.execute("""INSERT INTO local_sib_discovery_gaps(source_url,payroll_id,last_reason)
        VALUES(?,?,?) ON CONFLICT(source_url,payroll_id) DO UPDATE SET
        status='PENDING',last_reason=excluded.last_reason,
        next_check_at=CASE WHEN local_sib_discovery_gaps.status='FOUND' THEN 0
                          ELSE local_sib_discovery_gaps.next_check_at END""",
        (source_url, str(payroll), str(reason)[:80]))


def _mark_gap_retry(db, source_url, payroll, reason):
    row = db.execute("SELECT attempts FROM local_sib_discovery_gaps WHERE source_url=? AND payroll_id=?",
                     (source_url, payroll)).fetchone()
    # Old empty IDs must not consume the near-real-time discovery budget.
    # New-ID windows are still polled each minute independently of this backoff.
    delay = min(MAX_GAP_RETRY_SECONDS, INTERVAL_SECONDS * 2 ** min(int(row[0] or 0), 9)) if row else INTERVAL_SECONDS
    db.execute("""UPDATE local_sib_discovery_gaps SET attempts=attempts+1,
        last_checked_at=?,next_check_at=?,last_reason=?
        WHERE source_url=? AND payroll_id=? AND status='PENDING'""",
        (time.time(), time.time() + delay, str(reason or "NAO_ENCONTRADO")[:80], source_url, payroll))


def _mark_gap_found(db, source_url, payroll, reason="FICHA_LOCALIZADA"):
    db.execute("""UPDATE local_sib_discovery_gaps SET status='FOUND',attempts=attempts+1,
        last_checked_at=?,next_check_at=0,last_reason=?
        WHERE source_url=? AND payroll_id=? AND status='PENDING'""",
        (time.time(), str(reason)[:80], source_url, payroll))


def _record_progress(db, token, *, imported=0, missing=0, ambiguous=0):
    """Commit each confirmed outcome with its record, gap or review entry."""
    current = _row(db)
    if not current or current['lease_token'] != token:
        raise _YieldTurn()
    if current['queue_kind'] == 'recovery':
        queue = json.loads(current['queue_json'])
        number_recovery.advance(db, current['queue_lane'], queue[current['queue_index']],
                                imported=imported, unresolved=ambiguous)
    db.execute("""UPDATE local_sib_discovery_state SET checked=checked+1,
        imported=imported+?,missing=missing+?,ambiguous=ambiguous+?,queue_index=queue_index+1,
        lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?""",
        (imported, missing, ambiguous, time.time() + LEASE_SECONDS, token))


def _matches_known(canonical, records):
    """Avoid raw reads for known IDs unless the Bridge shows a different person."""
    name = " ".join(str(canonical.get("name") or "").split()).casefold()
    cpf = "".join(char for char in str(canonical.get("cpf") or "") if char.isdigit())
    admission = str(canonical.get("admissionDate") or "")[:10]
    dismissal = str(canonical.get("dismissalDate") or "")[:10]
    for stored in records:
        known_name = " ".join(str(stored.get("name") or "").split()).casefold()
        known_cpf = "".join(char for char in str(stored.get("cpf") or "") if char.isdigit())
        checks = ((name, known_name), (cpf, known_cpf),
                  (admission, str(stored.get("admissionDate") or "")[:10]),
                  (dismissal, str(stored.get("dismissalDate") or "")[:10]))
        if all(not left or not right or left == right for left, right in checks):
            return True
    return False


def _other_busy(db):
    scan = db.execute("SELECT continuous,lease_token,lease_until FROM local_sib_scan_state WHERE id=1").fetchone()
    refresh = db.execute("SELECT lease_token,lease_until FROM local_sib_refresh_state WHERE id=1").fetchone()
    automatic = db.execute("SELECT lease_token,lease_until FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
    issues = db.execute("SELECT token,lease_until FROM local_sib_issue_job WHERE id=1").fetchone()
    absences = db.execute("SELECT lease_token,lease_until FROM local_sib_absence_state WHERE id=1").fetchone()
    return bool((scan and (scan["continuous"] or lease_active(scan["lease_token"], scan["lease_until"])))
                or (refresh and lease_active(refresh["lease_token"], refresh["lease_until"]))
                or (automatic and lease_active(automatic["lease_token"], automatic["lease_until"]))
                or (issues and lease_active(issues["token"], issues["lease_until"]))
                or (absences and lease_active(absences["lease_token"], absences["lease_until"])))


def summary(db):
    row = _row(db)
    scan_done = _scan_done(db)
    queue = json.loads(row['queue_json']) if row else []
    queue_index = min(len(queue), max(0, int(row['queue_index'] or 0))) if row else 0
    current_batch = json.loads(row['current_batch_json'] or '[]') if row else []
    waiting_for_other = bool(row and row['enabled'] and not row['running'] and _other_busy(db))
    catalog_progress = json.loads(row['catalog_progress_json'] or '{}') if row else {}
    catalog_dataset = next((key for key in ('employments', 'companies')
                            if not catalog_progress.get(key, {}).get('done', False)), '')
    catalog_state = catalog_progress.get(catalog_dataset, {}) if catalog_dataset else {}
    gap_stats = db.execute("""SELECT
        SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) pending,
        SUM(CASE WHEN status='FOUND' THEN 1 ELSE 0 END) recovered,
        SUM(attempts) attempts,MAX(last_checked_at) last_checked,
        SUM(CASE WHEN status='PENDING' AND last_reason IN ('NAO_ENCONTRADO','AUSENCIA_CONFIRMADA') THEN 1 ELSE 0 END) empty,
        SUM(CASE WHEN status='PENDING' AND last_reason NOT IN ('NAO_ENCONTRADO','AUSENCIA_CONFIRMADA') THEN 1 ELSE 0 END) review
        FROM local_sib_discovery_gaps
        WHERE source_url=(SELECT source_url FROM local_sib_scan_state WHERE id=1)""").fetchone()
    gaps = {"gapPending": int(gap_stats["pending"] or 0),
            "gapRecovered": int(gap_stats["recovered"] or 0),
            "gapAttempts": int(gap_stats["attempts"] or 0),
            "gapLastCheckedAt": float(gap_stats["last_checked"] or 0),
            "gapEmpty": int(gap_stats["empty"] or 0), "gapReview": int(gap_stats["review"] or 0),
            "catalogSupported": None if not row or row['catalog_supported'] < 0 else bool(row['catalog_supported']),
            "catalogError": row['catalog_error'] if row else '',
            "catalogLastCompletedAt": row['catalog_last_completed_at'] if row else 0,
            "windowTotal": len(queue), "windowChecked": queue_index,
            "windowRemaining": max(0, len(queue) - queue_index),
            "activity": row['activity'] if row else '',
            "currentRegistration": row['current_registration'] if row else '',
            "currentBatchCount": len(current_batch),
            "currentIds": (current_batch[:8] if row and row['running']
                           and row['activity'] in {'scan_batch', 'confirm_identity'} and current_batch
                           else [] if row and row['running']
                           else queue[queue_index:queue_index + 8]),
            "catalogDataset": catalog_dataset,
            "catalogChecked": int(catalog_state.get('count') or 0),
            "catalogTotal": catalog_state.get('total'),
            "waitingForOther": waiting_for_other,
            "queueKind": row['queue_kind'] if row else '',
            "freshLastCheckedAt": row['fresh_last_at'] if row else 0,
            "freshNextDueAt": row['fresh_due_at'] if row else 0,
            "recovery": number_recovery.summary(db, row['source_url'] if row else ''),
            "presence": sib_presence.summary(db)}
    if not row:
        return {"enabled": False, "ready": scan_done, "running": False,
                "inFlight": False,
                "intervalSeconds": INTERVAL_SECONDS, "lookbackIds": LOOKBACK_IDS,
                "freshIdsPerCycle": FRESH_IDS_PER_CYCLE,
                "windowTotal": 0, "windowChecked": 0, "windowRemaining": 0,
                "activity": "", "currentRegistration": "", "currentBatchCount": 0,
                "currentIds": [], "catalogDataset": "", "catalogChecked": 0,
                "catalogTotal": None, "waitingForOther": False,
                "cursor": min(1000000, _highwater(db) + 1) if scan_done else 0,
                "internCursor": min(1000000, _highwater(db, interns=True) + 1) if scan_done else 0,
                "checked": 0, "imported": 0, "missing": 0, "ambiguous": 0,
                "lastCycleAt": 0, "nextDueAt": 0, "error": "",
                **gaps}
    return {"enabled": bool(row["enabled"]), "ready": scan_done,
            "running": bool(row["running"] and lease_active(row["lease_token"], row["lease_until"])),
            "inFlight": bool(lease_active(row["lease_token"], row["lease_until"])),
            "intervalSeconds": INTERVAL_SECONDS, "lookbackIds": LOOKBACK_IDS,
            "freshIdsPerCycle": FRESH_IDS_PER_CYCLE, "cursor": row["cursor"],
            "internCursor": row["intern_cursor"],
            "checked": row["checked"], "imported": row["imported"],
            "missing": row["missing"], "ambiguous": row["ambiguous"],
            "lastCycleAt": row["last_cycle_at"], "nextDueAt": row["next_due_at"],
            "error": row["error"], **gaps}


def _ensure(database: Path, source_url: str, actor_id: int) -> None:
    from .database import connect
    with connect(database) as db:
        if not _scan_done(db):
            return
        row = _row(db)
        if row and row["source_url"] == source_url:
            if row['monitor_version'] < 2:
                if lease_active(row['lease_token'], row['lease_until']):
                    return
                profiles = _sequence_profiles(db)
                number_recovery.configure(db, source_url,
                    employees=_profile_highwater(profiles), interns=_profile_highwater(profiles, interns=True))
                db.execute("""UPDATE local_sib_discovery_state SET monitor_version=2,
                    queue_json='[]',queue_index=0,gap_ids_json='[]',queue_kind='',queue_lane='',
                    fresh_due_at=0,next_due_at=0,cursor=?,intern_cursor=? WHERE id=1""",
                    (_profile_highwater(profiles) + 1, _profile_highwater(profiles, interns=True) + 1))
            return
        db.execute("""INSERT INTO local_sib_discovery_state
            (id,source_url,actor_id,enabled,cursor,intern_cursor,next_due_at)
            VALUES(1,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,actor_id=excluded.actor_id,enabled=1,
            cursor=excluded.cursor,intern_cursor=excluded.intern_cursor,
            running=0,checked=0,imported=0,missing=0,ambiguous=0,probe_offset=0,
            queue_json='[]',queue_index=0,gap_ids_json='[]',catalog_supported=-1,
            catalog_progress_json='{}',catalog_next_at=0,catalog_last_completed_at=0,catalog_error='',
            last_cycle_at=0,next_due_at=excluded.next_due_at,lease_token='',lease_until=0,error='',
            updated_at=CURRENT_TIMESTAMP""",
            (source_url, actor_id, min(1000000, _highwater(db) + 1),
             min(1000000, _highwater(db, interns=True) + 1), time.time()))
        profiles = _sequence_profiles(db)
        number_recovery.configure(db, source_url,
            employees=_profile_highwater(profiles), interns=_profile_highwater(profiles, interns=True))
        db.execute("UPDATE local_sib_discovery_state SET monitor_version=2,queue_kind='',queue_lane='',fresh_due_at=0,fresh_last_at=0 WHERE id=1")


def control(database, client, actor_id: int, action: str):
    """Pause or resume the automatically scheduled discovery pass."""
    from .database import connect
    if action not in {"pause", "resume", "prioritize_interns", "close_initial"}:
        raise ValueError("Comando de descoberta SIB inválido.")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
        if not actor or actor["role"] != "ADMIN":
            raise PermissionError("Somente Administrador pode controlar a descoberta SIB.")
        row = db.execute("SELECT * FROM local_sib_discovery_state WHERE id=1").fetchone()
        if action == 'close_initial':
            settings = db.execute('SELECT sib_base_url FROM local_integration_settings WHERE id=1').fetchone()
            if not row or not settings or row['source_url'] != settings['sib_base_url']:
                raise ValueError('Não há recuperação inicial para a origem SIB configurada.')
            number_recovery.close_initial(db, row['source_url'], actor_id)
            return {'ok': True, 'discovery': summary(db),
                    'message': 'Recuperação inicial encerrada. Novas matrículas e atualização de fichas seguem suas configurações.'}
        if action == "pause":
            if row:
                db.execute("UPDATE local_sib_discovery_state SET enabled=0,running=0,updated_at=CURRENT_TIMESTAMP WHERE id=1")
            return {"ok": True, "discovery": summary(db), "message": "Monitoramento de novos cadastros pausado."}
        if action == 'prioritize_interns':
            if not client or not _scan_done(db) or not row or row['source_url'] != client.config.base_url:
                raise ValueError('Conclua a busca geral na Bridge configurada antes de priorizar os estágios.')
            settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
            if not settings or not settings['sib_active'] or settings['sib_base_url'] != client.config.base_url:
                raise ValueError('A configuração SIB mudou. Atualize a tela antes de continuar.')
            number_recovery.prioritize_interns(db, client.config.base_url)
            return {'ok': True, 'discovery': summary(db),
                    'message': 'Estagiários priorizados até concluir suas três passagens. As demais consultas voltarão automaticamente.'}
        if row and lease_active(row["lease_token"], row["lease_until"]):
            raise ValueError("Aguarde o lote de descoberta ativo terminar antes de retomar o monitoramento.")
        if not client or not _scan_done(db):
            raise ValueError("Conclua a consulta geral do SIB antes de ativar o monitoramento de novos cadastros.")
        scan_source = db.execute("SELECT source_url FROM local_sib_scan_state WHERE id=1").fetchone()
        if not scan_source or scan_source["source_url"] != client.config.base_url:
            raise ValueError("Faça a consulta geral na Bridge configurada antes de ativar o monitoramento.")
        if row and row['monitor_version'] >= 2 and row['source_url'] == client.config.base_url:
            highwater, intern_highwater = row['cursor'], row['intern_cursor']
        else:
            profiles = _sequence_profiles(db)
            highwater = min(1000000, _profile_highwater(profiles) + 1)
            intern_highwater = min(1000000, _profile_highwater(profiles, interns=True) + 1)
        db.execute("""INSERT INTO local_sib_discovery_state
            (id,source_url,actor_id,enabled,cursor,intern_cursor,next_due_at)
            VALUES(1,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,actor_id=excluded.actor_id,enabled=1,
            cursor=excluded.cursor,intern_cursor=excluded.intern_cursor,
            next_due_at=?,catalog_next_at=0,error='',updated_at=CURRENT_TIMESTAMP""",
            (client.config.base_url, actor_id, highwater, intern_highwater, time.time(), time.time()))
        return {"ok": True, "discovery": summary(db), "message": "Monitoramento de novos cadastros SIB retomado."}


def run_due(database: Path, client, *, now=None, stop_event=None, one_batch=False, fresh_only=False) -> bool:
    from .sib_discovery_worker import run_due as run_worker
    return run_worker(database, client, now=now, stop_event=stop_event,
                      one_batch=one_batch, fresh_only=fresh_only)


class _YieldTurn(Exception):
    pass
