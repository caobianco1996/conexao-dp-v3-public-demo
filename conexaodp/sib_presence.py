"""Source membership, vacancies and cancellations without replaying numeric recovery.

Only a complete validated identity inventory can suggest an exclusion. Individual
scoped reads then confirm it; transport failures never count as missing records.
"""
from __future__ import annotations

import json
import re
import time
from uuid import uuid4

from .integrations import IntegrationError


def connect(database):
    # This module contributes schema while database.py is still importing.
    from .database import connect as open_database
    return open_database(database)

INTERVAL = 300
DELAYS = (300, 900, 3600)
SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_presence_state (
 source_url TEXT PRIMARY KEY, snapshot TEXT NOT NULL DEFAULT '',
 cursor TEXT NOT NULL DEFAULT '', mapping TEXT NOT NULL DEFAULT '',
 checked INTEGER NOT NULL DEFAULT 0, total INTEGER,
 last_key TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL DEFAULT 0,
 completed_at REAL NOT NULL DEFAULT 0, next_due REAL NOT NULL DEFAULT 0,
 supported INTEGER NOT NULL DEFAULT -1, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS local_sib_presence (
 source_url TEXT NOT NULL, source_id TEXT NOT NULL,
 modality TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'PRESENT',
 first_seen REAL NOT NULL DEFAULT 0, last_seen REAL NOT NULL DEFAULT 0,
 missing_since REAL NOT NULL DEFAULT 0, checks INTEGER NOT NULL DEFAULT 0,
 next_check REAL NOT NULL DEFAULT 0, changed_at REAL NOT NULL DEFAULT 0,
 cancellation INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(source_url,source_id)
);
CREATE INDEX IF NOT EXISTS sib_presence_due ON local_sib_presence(source_url,status,next_check);
CREATE TABLE IF NOT EXISTS local_sib_presence_seen (
 source_url TEXT NOT NULL, source_id TEXT NOT NULL, modality TEXT NOT NULL,
 PRIMARY KEY(source_url,source_id)
);
CREATE TABLE IF NOT EXISTS local_sib_presence_jobs (
 source_url TEXT NOT NULL, source_id TEXT NOT NULL, modality TEXT NOT NULL,
 next_due REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(source_url,source_id)
);
CREATE TABLE IF NOT EXISTS local_sib_vacancies (
 source_url TEXT NOT NULL, source_id TEXT NOT NULL,
 first_checked REAL NOT NULL, last_checked REAL NOT NULL,
 end_number INTEGER NOT NULL, quantity INTEGER NOT NULL,
 filled_at REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(source_url,source_id)
);
CREATE TABLE IF NOT EXISTS local_sib_presence_events (
 id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, source_id TEXT NOT NULL,
 event TEXT NOT NULL, at REAL NOT NULL, actor_id INTEGER REFERENCES users(id)
);
"""


def source(db):
    row = db.execute('SELECT sib_base_url FROM local_integration_settings WHERE id=1').fetchone()
    return row[0] if row else ''


def _event(db, url, key, event, now, actor=None):
    db.execute('INSERT INTO local_sib_presence_events(source_url,source_id,event,at,actor_id) VALUES(?,?,?,?,?)',
               (url, key, event, now, actor))


def observe(db, key, data, *, url=None, now=None):
    """A successfully mapped, identity-checked full fiche is positive evidence."""
    url, now = url or source(db), time.time() if now is None else now
    if not url:
        return
    old = db.execute('SELECT status,cancellation FROM local_sib_presence WHERE source_url=? AND source_id=?', (url, key)).fetchone()
    if old and (old['status'] in ('MISSING', 'EXCLUDED', 'DIVERGENT') or old['cancellation']):
        _event(db, url, key, 'REAPPEARED', now)
        _queue_operations(db, key)
    db.execute("""INSERT INTO local_sib_presence(source_url,source_id,modality,first_seen,last_seen,changed_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(source_url,source_id) DO UPDATE SET
        modality=excluded.modality,status='PRESENT',last_seen=excluded.last_seen,
        missing_since=0,checks=0,next_check=0,cancellation=0,error='',
        changed_at=CASE WHEN status<>'PRESENT' OR cancellation=1 THEN excluded.changed_at ELSE changed_at END""",
        (url, key, str(data.get('contractTypeCode') or ''), now, now, now))
    if not old:
        _fill_vacancy(db, url, key, now)


def _fill_vacancy(db, url, key, now):
    parts = key.split(':')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return
    prefix, number = ':'.join(parts[:2]) + ':', int(parts[2])
    rows = db.execute("""SELECT * FROM local_sib_vacancies WHERE source_url=? AND filled_at=0
        AND source_id>=? AND source_id<? AND CAST(substr(source_id,?) AS INTEGER)<=? AND end_number>=?""",
        (url, prefix, prefix+'~', len(prefix)+1, number, number)).fetchall()
    for row in rows:
        first = int(row['source_id'].split(':')[-1])
        db.execute('DELETE FROM local_sib_vacancies WHERE source_url=? AND source_id=?', (url, row['source_id']))
        for start, end in ((first, number-1), (number+1, row['end_number'])):
            if start <= end:
                db.execute('INSERT INTO local_sib_vacancies(source_url,source_id,first_checked,last_checked,end_number,quantity) VALUES(?,?,?,?,?,?)',
                           (url, prefix+str(start), row['first_checked'], row['last_checked'], end, end-start+1))
        db.execute('INSERT OR REPLACE INTO local_sib_vacancies(source_url,source_id,first_checked,last_checked,end_number,quantity,filled_at) VALUES(?,?,?,?,?,?,?)',
                   (url, key, row['first_checked'], now, number, 1, now))
        _event(db, url, key, 'VACANCY_FILLED', now)


def overlays(db):
    return {r['source_id']: {'sibPresence': r['status'], 'contractCancelled': bool(r['cancellation']),
                             'sibLastSeen': r['last_seen'], 'sibMissingSince': r['missing_since']}
            for r in db.execute("SELECT * FROM local_sib_presence WHERE source_url=? AND (status<>'PRESENT' OR cancellation=1)", (source(db),))}


def ready(db):
    from .sib_number_recovery import closure, summary
    row = db.execute('SELECT source_url,catalog_last_completed_at FROM local_sib_discovery_state WHERE id=1').fetchone()
    return bool(row and row['source_url'] == source(db) and
        (closure(db, row['source_url']) or row['catalog_last_completed_at'] and summary(db, row['source_url']).get('complete')))


def due(db, url, now):
    if not ready(db):
        return False
    state = db.execute('SELECT * FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone()
    return bool(not state or state['next_due'] <= now
        or db.execute("SELECT 1 FROM local_sib_presence WHERE source_url=? AND status='MISSING' AND next_check<=? LIMIT 1", (url, now)).fetchone()
        or db.execute('SELECT 1 FROM local_sib_presence_jobs WHERE source_url=? AND next_due<=? LIMIT 1', (url, now)).fetchone())


def _seed(db, url):
    if db.execute('SELECT 1 FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone():
        return
    db.execute('INSERT OR IGNORE INTO local_sib_presence_state(source_url) VALUES(?)', (url,))
    db.execute("""INSERT OR IGNORE INTO local_sib_presence(source_url,source_id,modality,first_seen,last_seen,changed_at)
        SELECT ?,source_id,CAST(COALESCE(json_extract(data_json,'$.contractTypeCode'),json_extract(raw_json,'$.VIN_FUN'),'') AS TEXT),
        CAST(strftime('%s',imported_at) AS REAL),CAST(strftime('%s',imported_at) AS REAL),CAST(strftime('%s',imported_at) AS REAL)
        FROM local_sib_records WHERE dataset='employments'""", (url,))


def _mark_missing(db, url, key, now):
    row = db.execute('SELECT * FROM local_sib_presence WHERE source_url=? AND source_id=?', (url, key)).fetchone()
    if row and row['status'] in ('PRESENT', 'AWAITING_FICHE'):
        db.execute("UPDATE local_sib_presence SET status='MISSING',missing_since=?,checks=0,next_check=?,changed_at=?,error='' WHERE source_url=? AND source_id=?",
                   (now, now + DELAYS[0], now, url, key))
        _event(db, url, key, 'MISSING', now)


def _finish_inventory(db, url, now, started):
    # New fiches imported after inventory start cannot be declared missing by it.
    for row in db.execute("""SELECT p.source_id FROM local_sib_presence p
        WHERE p.source_url=? AND p.status IN ('PRESENT','AWAITING_FICHE') AND p.last_seen<=?
        AND NOT EXISTS(SELECT 1 FROM local_sib_presence_seen s WHERE s.source_url=p.source_url AND s.source_id=p.source_id)""", (url, started)).fetchall():
        _mark_missing(db, url, row[0], now)
    db.execute('''DELETE FROM local_sib_presence_jobs WHERE source_url=? AND NOT EXISTS
        (SELECT 1 FROM local_sib_presence_seen s WHERE s.source_url=local_sib_presence_jobs.source_url
         AND s.source_id=local_sib_presence_jobs.source_id)''', (url,))
    # Describe holes only in observed active physical numbering sequences, below
    # their confirmed maximum. Known deleted or conflicting identities aren't vacancies.
    from .sib_discovery import _sequence_profiles
    profiles = _sequence_profiles(db)
    branches = {(p['employer'], p['branch']) for p in profiles if p['active']}
    all_keys = {r[0] for r in db.execute('SELECT source_id FROM local_sib_presence_seen WHERE source_url=? UNION SELECT source_id FROM local_sib_presence WHERE source_url=?', (url, url))}
    previous = [dict(r) for r in db.execute('SELECT * FROM local_sib_vacancies WHERE source_url=? AND filled_at=0', (url,))]
    db.execute('DELETE FROM local_sib_vacancies WHERE source_url=? AND filled_at=0', (url,))
    for employer, branch in branches:
        prefix = f'{employer}:{branch}:'
        numbers = {int(k[len(prefix):]) for k in all_keys if k.startswith(prefix) and k[len(prefix):].isdigit()}
        if not numbers:
            continue
        # Store intervals, not one database row per absent number. A sparse
        # branch with a high code cannot create millions of vacancy records.
        start = 1
        old_ranges = sorted((int(r['source_id'].split(':')[-1]), r['end_number'], r['first_checked'])
                            for r in previous if r['source_id'].startswith(prefix))
        old_index = 0
        for number in sorted(n for n in numbers if n <= 999999):
            if start < number:
                while old_index < len(old_ranges) and old_ranges[old_index][1] < start:
                    old_index += 1
                first_checked = (old_ranges[old_index][2] if old_index < len(old_ranges)
                    and old_ranges[old_index][0] <= start <= old_ranges[old_index][1] else now)
                db.execute('INSERT INTO local_sib_vacancies(source_url,source_id,first_checked,last_checked,end_number,quantity) VALUES(?,?,?,?,?,?)',
                           (url, prefix+str(start), first_checked, now, number-1, number-start))
            start = number+1
    db.execute("UPDATE local_sib_presence_state SET snapshot='',cursor='',mapping='',last_key='',completed_at=?,next_due=?,error='',supported=1 WHERE source_url=?", (now, now + INTERVAL, url))


def _inventory(database, client, renew, token=None):
    url = client.config.base_url
    request_started = time.time()
    with connect(database) as db:
        _seed(db, url)
        state = dict(db.execute('SELECT * FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone())
    page = client.get_identity_page(cursor=state['cursor'])
    items = page.get('items')
    if (page.get('contract') != 'sib-identities-v1' or page.get('dataset') != 'employments'
            or page.get('scope') != 'all' or page.get('read_only') is not True
            or not isinstance(items, list) or len(items) > 1000
            or type(page.get('total')) is not int or not 0 <= page['total'] <= 1000000
            or type(page.get('done')) is not bool
            or not isinstance(page.get('snapshot_id'), str) or not page['snapshot_id']
            or not isinstance(page.get('mapping_hash'), str) or not page['mapping_hash']):
        raise ValueError('Inventário de identificadores SIB inválido. Nenhuma exclusão foi presumida.')
    if state['snapshot'] and any(page[field] != state[column] for field, column in
            (('snapshot_id', 'snapshot'), ('mapping_hash', 'mapping'), ('total', 'total'))):
        raise ValueError('O inventário mudou durante a conferência. A leitura será reiniciada.')
    next_cursor = page.get('next_cursor')
    if (not isinstance(next_cursor, str) or len(next_cursor) > 8192
            or (page['done'] and next_cursor) or (not page['done'] and (not next_cursor or not items or next_cursor == state['cursor']))):
        raise ValueError('Continuação do inventário SIB inválida.')
    previous = tuple(map(int, state['last_key'].split(':'))) if state['last_key'] else None
    clean = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Item inválido no inventário SIB.')
        key, modality = item.get('source_id', ''), str(item.get('contract_type_code') or '')
        if not isinstance(key, str) or not re.fullmatch(r'\d{1,10}:\d{1,10}:\d{1,10}', key) or not re.fullmatch(r'\d{0,3}', modality):
            raise ValueError('Identificador ou modalidade inválidos no inventário SIB.')
        order = tuple(map(int, key.split(':')))
        if key != ':'.join(map(str, order)) or (previous is not None and order <= previous):
            raise ValueError('O inventário repetiu ou desordenou identidades.')
        previous = order
        clean.append((key, modality))
    checked = (state['checked'] if state['snapshot'] else 0) + len(clean)
    if checked > page['total'] or (page['done'] and checked != page['total']):
        raise ValueError('O inventário SIB não confirmou todos os identificadores.')
    now = time.time()
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
        monitor = db.execute('SELECT enabled,lease_token FROM local_sib_discovery_state WHERE id=1 AND source_url=?', (url,)).fetchone()
        if (not settings or settings[0] != url or not settings[1] or not monitor or not monitor[0]
                or (token and monitor['lease_token'] != token)):
            return
        # Save a finished response even if the fresh-ID clock became due while
        # it was in flight. The next renew yields before another source read.
        started = state['started_at'] if state['snapshot'] else request_started
        if not state['snapshot']:
            db.execute('DELETE FROM local_sib_presence_seen WHERE source_url=?', (url,))
        for key, modality in clean:
            db.execute('INSERT INTO local_sib_presence_seen VALUES(?,?,?)', (url, key, modality))
            old = db.execute('SELECT modality,status FROM local_sib_presence WHERE source_url=? AND source_id=?', (url, key)).fetchone()
            if old and old['status'] == 'PRESENT' and old['modality'] == modality:
                db.execute('UPDATE local_sib_presence SET last_seen=? WHERE source_url=? AND source_id=?', (now, url, key))
            elif old and old['status'] == 'DIVERGENT':
                # Reused identities remain visible for a human decision; they
                # must not trigger endless attempts to overwrite the old fiche.
                continue
            elif old and old['status'] == 'PRESENT' and old['modality'] and old['modality'] != modality:
                db.execute("UPDATE local_sib_presence SET status='DIVERGENT',changed_at=?,error='O catálogo passou a informar outra modalidade de vínculo; confira a identidade antes de substituir a ficha.' WHERE source_url=? AND source_id=?", (now, url, key))
                _event(db, url, key, 'DIVERGENT', now)
                _queue_operations(db, key)
            else:
                if not old:
                    db.execute("INSERT INTO local_sib_presence(source_url,source_id,modality,status,first_seen,last_seen,changed_at) VALUES(?,?,?,'AWAITING_FICHE',?,?,?)", (url, key, modality, now, now, now))
                    _fill_vacancy(db, url, key, now)
                db.execute('INSERT INTO local_sib_presence_jobs(source_url,source_id,modality) VALUES(?,?,?) ON CONFLICT(source_url,source_id) DO UPDATE SET modality=excluded.modality', (url, key, modality))
        db.execute("""UPDATE local_sib_presence_state SET snapshot=?,cursor=?,mapping=?,checked=?,total=?,last_key=?,started_at=?,supported=1,error='' WHERE source_url=?""",
                   (page['snapshot_id'], next_cursor, page['mapping_hash'], checked, page['total'], clean[-1][0] if clean else '', started, url))
        if page['done']:
            _finish_inventory(db, url, now, started)


def _read_job(database, client, job, *, missing=False):
    from .sib_gap_recovery import same_identity
    from .sib_record_mapping import map_raw_employee
    from .sib_scan import _upsert
    url, key = client.config.base_url, job['source_id']
    employer, branch, number = key.split(':')
    try:
        payload = client.get_employee_raw(number, employer_code=employer, branch_code=branch,
                                          contract_type_code=job['modality'])
        employment, company = map_raw_employee(payload, number)
        if employment['source_id'] != key or str(employment['data'].get('contractTypeCode') or '') != job['modality']:
            raise ValueError('A identidade retornada não corresponde à solicitada.')
        with connect(database) as db:
            db.execute('BEGIN IMMEDIATE')
            settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
            state = db.execute('SELECT enabled FROM local_sib_discovery_state WHERE id=1 AND source_url=?', (url,)).fetchone()
            if not settings or not settings['sib_active'] or settings['sib_base_url'] != url or not state or not state[0]:
                return
            old = db.execute("SELECT data_json,cycle,json_extract(raw_json,'$.VIN_FUN') modality FROM local_sib_records WHERE dataset='employments' AND source_id=?", (key,)).fetchone()
            stored = json.loads(old['data_json']) if old else {}
            if old:
                stored['contractTypeCode'] = stored.get('contractTypeCode') or old['modality']
            if old and not same_identity(stored, employment['data']):
                db.execute("UPDATE local_sib_presence SET status='DIVERGENT',changed_at=?,error='Identidade reutilizada no SIB; ficha anterior preservada.' WHERE source_url=? AND source_id=?", (time.time(), url, key))
                db.execute('DELETE FROM local_sib_presence_jobs WHERE source_url=? AND source_id=?', (url, key))
                _event(db, url, key, 'DIVERGENT', time.time())
                return
            _upsert(db, 'employments', employment, old['cycle'] if old else 'membership-' + uuid4().hex)
            if company:
                _upsert(db, 'companies', company, old['cycle'] if old else 'membership')
            observe(db, key, employment['data'], url=url)
            db.execute('DELETE FROM local_sib_presence_jobs WHERE source_url=? AND source_id=?', (url, key))
            if not old:
                db.execute('UPDATE local_sib_discovery_state SET imported=imported+1 WHERE id=1 AND source_url=?', (url,))
        return
    except IntegrationError as error:
        # A missing route or authorization failure isn't an absent employee.
        if error.status_code == 404 and missing:
            now = time.time()
            with connect(database) as db:
                settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
                monitor = db.execute('SELECT enabled FROM local_sib_discovery_state WHERE id=1 AND source_url=?', (url,)).fetchone()
                if not settings or settings[0] != url or not settings[1] or not monitor or not monitor[0]:
                    return
                state = db.execute('SELECT * FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone()
                unseen = not db.execute('SELECT 1 FROM local_sib_presence_seen WHERE source_url=? AND source_id=?', (url, key)).fetchone()
                if not state or state['supported'] != 1 or not state['completed_at'] or state['snapshot'] or not unseen:
                    raise ValueError('O catálogo não confirmou a ausência desta identidade.')
                current = db.execute('SELECT * FROM local_sib_presence WHERE source_url=? AND source_id=?', (url, key)).fetchone()
                if current and current['status'] == 'MISSING' and current['next_check'] <= now:
                    checks = current['checks'] + 1
                    excluded = checks >= len(DELAYS)
                    db.execute("UPDATE local_sib_presence SET checks=?,status=?,next_check=?,changed_at=?,error='' WHERE source_url=? AND source_id=?",
                        (checks, 'EXCLUDED' if excluded else 'MISSING', 0 if excluded else now + DELAYS[checks] - DELAYS[checks-1], now, url, key))
                    if excluded:
                        _event(db, url, key, 'EXCLUDED', now)
                        _queue_operations(db, key)
            return
        raise


def _queue_operations(db, key):
    db.execute('INSERT OR IGNORE INTO local_sib_operation_jobs(source_id) VALUES(?)', (key,))


def run_unit(database, client, renew, token=None):
    """One bounded background unit, under the discovery worker's shared lease."""
    url, now = client.config.base_url, time.time()
    job = None
    try:
        with connect(database) as db:
            _seed(db, url)
            job = db.execute("SELECT *,1 missing FROM local_sib_presence WHERE source_url=? AND status='MISSING' AND next_check<=? ORDER BY next_check LIMIT 1", (url, now)).fetchone()
            if not job:
                job = db.execute('SELECT *,0 missing FROM local_sib_presence_jobs WHERE source_url=? AND next_due<=? ORDER BY next_due,source_id LIMIT 1', (url, now)).fetchone()
            state = db.execute('SELECT * FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone()
            if state['next_due'] <= now:
                # Complete the lightweight inventory even if many fiches are
                # waiting; failed imports cannot starve source membership checks.
                job = None
        renew()
        if job:
            _read_job(database, client, dict(job), missing=bool(job['missing']))
        elif state['next_due'] <= now:
            deadline = time.monotonic() + 15
            for _ in range(5):
                renew()
                _inventory(database, client, renew, token)
                with connect(database) as db:
                    current = db.execute('SELECT snapshot,next_due FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone()
                if not current['snapshot'] or time.monotonic() >= deadline:
                    break
    except (IntegrationError, ValueError) as error:
        with connect(database) as db:
            if job:
                table, column = ('local_sib_presence', 'next_check') if job['missing'] else ('local_sib_presence_jobs', 'next_due')
                db.execute(f'UPDATE {table} SET error=?,{column}=? WHERE source_url=? AND source_id=?', (str(error)[:250], now + 60, url, job['source_id']))
                if not job['missing']:
                    db.execute("UPDATE local_sib_presence SET error=?,next_check=? WHERE source_url=? AND source_id=? AND status='AWAITING_FICHE'", (str(error)[:250], now+60, url, job['source_id']))
            else:
                unsupported = isinstance(error, IntegrationError) and error.status_code in (404, 405, 501)
                db.execute("UPDATE local_sib_presence_state SET snapshot='',cursor='',last_key='',checked=0,total=NULL,error=?,next_due=?,supported=CASE WHEN ? THEN 0 ELSE supported END WHERE source_url=?",
                           (str(error)[:250], now + (3600 if unsupported else 60), int(unsupported), url))


def summary(db):
    url = source(db)
    state = db.execute('SELECT * FROM local_sib_presence_state WHERE source_url=?', (url,)).fetchone()
    counts = {r[0]: r[1] for r in db.execute('SELECT status,COUNT(*) FROM local_sib_presence WHERE source_url=? GROUP BY status', (url,))}
    vacancies = db.execute('SELECT COALESCE(SUM(quantity),0) FROM local_sib_vacancies WHERE source_url=? AND filled_at=0', (url,)).fetchone()[0]
    return dict(ready=ready(db), intervalSeconds=INTERVAL, supported=None if not state or state['supported'] < 0 else bool(state['supported']),
        lastCompletedAt=state['completed_at'] if state else 0, nextDueAt=state['next_due'] if state else 0,
        checked=state['checked'] if state else 0, total=state['total'] if state else None,
        running=bool(state and state['snapshot']), error=state['error'] if state else '',
        vacant=vacancies, missing=counts.get('MISSING', 0), excluded=counts.get('EXCLUDED', 0), divergent=counts.get('DIVERGENT', 0),
        pending=db.execute('SELECT COUNT(*) FROM local_sib_presence_jobs WHERE source_url=?', (url,)).fetchone()[0])


def list_occurrences(db, *, status='MISSING', page=1, query=''):
    url, size = source(db), 12
    if status not in {'MISSING', 'EXCLUDED', 'VACANT', 'DIVERGENT', 'HISTORY', 'AWAITING_FICHE'}:
        raise ValueError('Situação inválida.')
    page, query = max(1, int(page)), str(query).strip()[:120]
    if status == 'VACANT':
        ranges = []
        for row in db.execute('SELECT * FROM local_sib_vacancies WHERE source_url=? AND filled_at=0', (url,)):
            employer, branch, number = row['source_id'].split(':')
            if query and query not in row['source_id'] and not (query.isdigit() and int(number) <= int(query) <= row['end_number']):
                continue
            ranges.append((employer, branch, int(number), dict(row)))
        groups = [dict(source_id=r['source_id'], registration_end=r['end_number'], quantity=r['quantity'],
                       status='VACANT', changed_at=r['first_checked'], last_seen=r['last_checked'],
                       next_check=0, checks=0, cancellation=0, error='', name='', modality='')
                  for _, _, _, r in sorted(ranges, key=lambda r: (int(r[0]), int(r[1]), r[2]))]
        total = len(groups)
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        return dict(items=groups[(page-1)*size:page*size], total=total, page=page, pageCount=pages, summary=summary(db))
    if status == 'HISTORY':
        table, where, args = 'local_sib_presence_events p', 'p.source_url=?', [url]
        columns, order = "p.source_id,p.event status,p.at changed_at,0 last_seen,0 next_check,0 checks,0 cancellation,'' error", 'p.id DESC'
    elif status == 'VACANT':
        table, where, args = 'local_sib_vacancies p', 'p.source_url=? AND p.filled_at=0', [url]
        columns, order = "p.source_id,'VACANT' status,p.first_checked changed_at,p.last_checked last_seen,0 next_check,0 checks,0 cancellation,'' error", 'p.source_id'
    else:
        table, where, args = 'local_sib_presence p', 'p.source_url=? AND p.status=?', [url, status]
        columns, order = 'p.source_id,p.status,p.changed_at,p.last_seen,p.next_check,p.checks,p.cancellation,p.error', 'p.changed_at DESC,p.source_id'
    join = " LEFT JOIN local_sib_records r ON r.dataset='employments' AND r.source_id=p.source_id"
    if query:
        where += " AND (p.source_id LIKE ? OR json_extract(r.data_json,'$.name') LIKE ?)"
        args.extend([f'%{query}%', f'%{query}%'])
    total = db.execute(f'SELECT COUNT(*) FROM {table}{join} WHERE {where}', args).fetchone()[0]
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    items = [dict(r) for r in db.execute(f"SELECT {columns},json_extract(r.data_json,'$.name') name,json_extract(r.data_json,'$.contractType') modality FROM {table}{join} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))]
    return dict(items=items, total=total, page=page, pageCount=pages, summary=summary(db))


def confirm_cancellation(db, actor_id, key):
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor[0] != 'ADMIN':
        raise PermissionError('Somente Administrador pode confirmar cancelamento.')
    url = source(db)
    row = db.execute("SELECT * FROM local_sib_presence WHERE source_url=? AND source_id=? AND status='EXCLUDED'", (url, key)).fetchone()
    if not row:
        raise ValueError('A exclusão precisa estar confirmada antes de informar o cancelamento.')
    if not row['cancellation']:
        now = time.time()
        db.execute('UPDATE local_sib_presence SET cancellation=1,changed_at=? WHERE source_url=? AND source_id=?', (now, url, key))
        _event(db, url, key, 'CANCELLED_BEFORE_START', now, actor_id)
        _queue_operations(db, key)
    return {'ok': True, 'message': 'Cancelamento antes do início confirmado. Histórico preservado; contrato retirado dos indicadores e filas operacionais.'}
