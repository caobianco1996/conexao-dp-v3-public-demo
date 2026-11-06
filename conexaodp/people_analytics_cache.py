"""Prepared analytics base, shared across requests and retained across restarts."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
import zlib

VERSION = 1
REFRESH_SECONDS = 15
_lock = threading.Lock()
_entries = OrderedDict()
_responses = OrderedDict()
_log = logging.getLogger(__name__)


def install(db):
    """Track source changes, without invalidating on scheduler heartbeat writes."""
    db.execute("""CREATE TABLE IF NOT EXISTS local_people_analytics_revision (
        id INTEGER PRIMARY KEY CHECK(id=1), base INTEGER NOT NULL DEFAULT 0,
        details INTEGER NOT NULL DEFAULT 0)""")
    db.execute("INSERT OR IGNORE INTO local_people_analytics_revision(id) VALUES(1)")
    db.execute("""CREATE TABLE IF NOT EXISTS local_people_analytics_snapshot (
        id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
        revision INTEGER NOT NULL, day TEXT NOT NULL, prepared_at REAL NOT NULL,
        payload BLOB NOT NULL)""")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sources = {
        'local_sib_records': ('base', ('source_id', 'dataset', 'data_json', 'raw_json')),
        'local_sib_contract_types': ('base', None),
        'local_sib_presence': ('base', ('source_url', 'source_id', 'status', 'cancellation')),
        'local_integration_settings': ('base', ('sib_base_url',)),
        'v2_employments': ('base', None), 'registry_reviews': ('base', None),
        'registry_people': ('base', None), 'registry_employments': ('base', None),
        'local_clients': ('base', None), 'v2_source_rows': ('base', None),
        'local_sib_absences': ('details', None), 'local_sib_absence_checks': ('details', None),
    }
    for table, (revision, columns) in sources.items():
        if table not in tables:
            continue
        if columns is None:
            columns = tuple(row[1] for row in db.execute(f'PRAGMA table_info({table})'))
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            guard = ''
            if action == 'UPDATE':
                guard = ' OR '.join(f'OLD."{column}" IS NOT NEW."{column}"' for column in columns)
            if table == 'v2_source_rows':
                guards = [f"{alias}.table_name IN ('clients','client_company_profiles')"
                          for alias in (('OLD', 'NEW') if action == 'UPDATE' else
                                        ('OLD',) if action == 'DELETE' else ('NEW',))]
                scope = '(' + ' OR '.join(guards) + ')'
                guard = f'({guard}) AND {scope}' if guard else scope
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS pa_cache_{table}_{action.lower()}
                AFTER {action} ON {table} {('WHEN ' + guard) if guard else ''}
                BEGIN UPDATE local_people_analytics_revision SET {revision}={revision}+1 WHERE id=1; END""")


def _today():
    return datetime.now(timezone(timedelta(hours=-3))).date().isoformat()


def _identity(db):
    path = next((row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not path or not db.execute("SELECT 1 FROM sqlite_master WHERE name='local_people_analytics_revision'").fetchone():
        return None
    return str(Path(path).resolve())


class _Entry:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.rows = None
        self.companies = None
        self.revision = -1
        self.day = ''
        self.prepared_at = 0
        self.building = False
        self.error = ''
        self.attempted_at = 0


def _publish(entry, rows, companies, revision, day, prepared_at):
    with entry.lock:
        entry.rows, entry.companies = rows, companies
        entry.revision, entry.day, entry.prepared_at = revision, day, prepared_at
        entry.building, entry.error = False, ''
        entry.ready.set()


def _persist(path, entry):
    from .database import connect
    try:
        with entry.lock:
            revision, day, prepared_at = entry.revision, entry.day, entry.prepared_at
            payload = json.dumps([entry.rows, entry.companies], ensure_ascii=False,
                                 separators=(',', ':'), default=lambda value: value.isoformat()).encode('utf-8')
        compressed = zlib.compress(payload, level=1)
        with connect(Path(path)) as db:
            db.execute("""INSERT INTO local_people_analytics_snapshot VALUES(1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version,revision=excluded.revision,
                day=excluded.day,prepared_at=excluded.prepared_at,payload=excluded.payload
                WHERE excluded.prepared_at>=local_people_analytics_snapshot.prepared_at""",
                (VERSION, revision, day, prepared_at, compressed))
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        _log.warning('Não foi possível salvar a base preparada do People Analytics.', exc_info=True)


def _rebuild(path, entry, builder):
    from .database import connect
    try:
        with connect(Path(path)) as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT base FROM local_people_analytics_revision WHERE id=1').fetchone()[0]
            rows, companies = builder(db)
        _publish(entry, rows, companies, revision, _today(), time.time())
        _persist(path, entry)
    except Exception:
        with entry.lock:
            entry.building = False
            entry.error = 'Não foi possível atualizar a base preparada. Tente atualizar os indicadores novamente.'
            entry.ready.set()
        _log.warning('Não foi possível preparar o People Analytics.', exc_info=True)


def load(db, builder, *, force=False):
    path = _identity(db)
    if path is None:
        rows, companies = builder(db)
        return rows, companies, None
    revision, details = db.execute('SELECT base,details FROM local_people_analytics_revision WHERE id=1').fetchone()
    today, now = _today(), time.time()
    with _lock:
        entry = _entries.get(path)
        if entry is None:
            entry = _entries[path] = _Entry()
        _entries.move_to_end(path)
        while len(_entries) > 4:
            _entries.popitem(last=False)
    build_here = False
    with entry.lock:
        if entry.rows is None and not entry.building:
            # A change of day must not discard an otherwise usable saved base.
            # The dashboard computes the cutoff from today's date, and refreshes
            # the normalized source in the background using the checks below.
            saved = db.execute('SELECT * FROM local_people_analytics_snapshot WHERE id=1 AND version=?',
                               (VERSION,)).fetchone()
            if saved:
                try:
                    rows, companies = json.loads(zlib.decompress(saved['payload']))
                    if not isinstance(rows, list) or not isinstance(companies, dict) or any(not isinstance(row, dict) for row in rows):
                        raise ValueError('Invalid prepared analytics base')
                    for row in rows:
                        for field in ('admission', 'termination'):
                            row[field] = date.fromisoformat(row[field]) if row.get(field) else None
                    entry.rows, entry.companies = rows, companies
                    entry.revision, entry.day = saved['revision'], saved['day']
                    entry.prepared_at = saved['prepared_at']
                    entry.ready.set()
                except (ValueError, TypeError, KeyError, zlib.error):
                    pass  # A damaged generated snapshot can always be rebuilt.
        if entry.rows is None:
            if not entry.building:
                entry.building = build_here = True
                entry.ready.clear()
        elif not entry.building and (force or entry.revision != revision or entry.day != today):
            if force or now - max(entry.prepared_at, entry.attempted_at) >= REFRESH_SECONDS:
                entry.building, entry.error, entry.attempted_at = True, '', now
                threading.Thread(target=_rebuild, args=(path, entry, builder), daemon=True,
                                 name='people-analytics-prepare').start()
    if build_here:
        try:
            rows, companies = builder(db)
            _publish(entry, rows, companies, revision, today, time.time())
            threading.Thread(target=_persist, args=(path, entry), daemon=True,
                             name='people-analytics-save').start()
        except Exception:
            with entry.lock:
                entry.building = False
                entry.ready.set()
            raise
    elif entry.rows is None:
        if not entry.ready.wait(12) or entry.rows is None:
            raise sqlite3.OperationalError('A base de indicadores ainda está sendo preparada.')
    with entry.lock:
        metadata = {'snapshotAt': datetime.fromtimestamp(entry.prepared_at, timezone.utc).isoformat(),
                    'snapshotRefreshing': bool(entry.building or (entry.revision != revision or entry.day != today)
                                               and not entry.error), 'snapshotError': entry.error}
        # Callers update company counters and reports may enrich individual rows.
        rows = [dict(row) for row in entry.rows]
        companies = {key: dict(value) for key, value in entry.companies.items()}
        token = (path, VERSION, entry.revision, entry.prepared_at, details, today)
    return rows, companies, (token, metadata)


def response_key(snapshot, params, user):
    if snapshot is None:
        return None
    identity = json.dumps(dict(user) if user is not None else None, sort_keys=True, default=str)
    query = json.dumps({key: value for key, value in params.items() if key != 'refresh'}, sort_keys=True)
    return snapshot[0], hashlib.sha256(identity.encode()).digest(), query


def get_response(key):
    if key is None:
        return None
    with _lock:
        result = _responses.get(key)
        if result is not None:
            _responses.move_to_end(key)
        return result


def save_response(key, result):
    if key is None:
        return
    with _lock:
        _responses[key] = result
        _responses.move_to_end(key)
        while len(_responses) > 16:
            _responses.popitem(last=False)


def warm(database):
    """Prepare before the first page request, reusing a saved base when possible."""
    def prepare():
        from .database import connect
        from .people_analytics import _load
        try:
            with connect(database) as db:
                db.execute('BEGIN')
                load(db, _load)
        except (OSError, sqlite3.DatabaseError):
            _log.warning('A preparação inicial dos indicadores será repetida ao abrir o painel.')
    threading.Thread(target=prepare, daemon=True, name='people-analytics-warm').start()
