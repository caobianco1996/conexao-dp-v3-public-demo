"""A finite, checkpointed repair of historical numeric registration ranges."""
from __future__ import annotations

import json
import sqlite3
import time

PASSES = 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_number_recovery_state (
 id INTEGER PRIMARY KEY CHECK(id=1), source_url TEXT NOT NULL,
 ranges_json TEXT NOT NULL DEFAULT '[]', last_lane TEXT NOT NULL DEFAULT '',
 background_turn INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0,
 started_at REAL NOT NULL DEFAULT 0, completed_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS local_sib_number_recovery_priority (
 id INTEGER PRIMARY KEY CHECK(id=1), source_url TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 0, previous_enabled INTEGER NOT NULL DEFAULT 1,
 profiles_json TEXT NOT NULL DEFAULT '[]',
 started_at REAL NOT NULL DEFAULT 0, finished_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS local_sib_initial_recovery_closure (
 source_url TEXT PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
 closed_at REAL NOT NULL, reason TEXT NOT NULL
);
"""


def closure(db, source_url):
    try:
        row = db.execute('SELECT closed_at,reason FROM local_sib_initial_recovery_closure WHERE source_url=?',
                         (source_url,)).fetchone()
    except sqlite3.OperationalError as error:
        if 'no such table' not in str(error):
            raise
        return None
    return dict(row) if row else None


def close_initial(db, source_url, actor_id):
    """Retire historical sweeps by request without pretending they completed."""
    now = time.time()
    db.execute('''INSERT OR IGNORE INTO local_sib_initial_recovery_closure
        (source_url,actor_id,closed_at,reason) VALUES(?,?,?,?)''',
        (source_url, actor_id, now, 'Encerrada por solicitação do responsável após conferência da base.'))
    db.execute('''UPDATE local_sib_number_recovery_priority SET active=0,finished_at=?
        WHERE source_url=? AND active=1''', (now, source_url))


def priority_active(db, source_url):
    try:
        row = db.execute('SELECT source_url,active FROM local_sib_number_recovery_priority WHERE id=1').fetchone()
    except sqlite3.OperationalError as error:
        if 'no such table' not in str(error):
            raise
        return False
    return bool(row and row['active'] and row['source_url'] == source_url)


def prioritize_interns(db, source_url):
    if closure(db, source_url):
        raise ValueError('A recuperação inicial foi encerrada por solicitação do responsável.')
    state = db.execute('SELECT * FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    interns = next((item for item in json.loads(state['ranges_json']) if item['key'] == 'interns'), None) if state and state['source_url'] == source_url else None
    if not interns or interns['completedPasses'] >= PASSES:
        raise ValueError('A recuperação inicial de estágios não tem passagens pendentes.')
    if priority_active(db, source_url):
        return
    from .sib_gap_recovery import _recovery_profiles
    profiles = [{'employer': employer, 'branch': branch, 'modality': modality, **value}
                for (employer, branch, modality), value in _recovery_profiles(db).items() if modality == '15']
    if not profiles:
        raise ValueError('Não há filial de estágios confirmada para iniciar a recuperação.')
    discovery = db.execute('SELECT enabled FROM local_sib_discovery_state WHERE id=1').fetchone()
    db.execute('''INSERT INTO local_sib_number_recovery_priority
        (id,source_url,active,previous_enabled,profiles_json,started_at,finished_at) VALUES(1,?,1,?,?,?,0)
        ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,active=1,
        previous_enabled=excluded.previous_enabled,profiles_json=excluded.profiles_json,
        started_at=excluded.started_at,finished_at=0''',
        (source_url, int(bool(discovery and discovery[0])), json.dumps(profiles), time.time()))
    db.execute("UPDATE local_sib_discovery_state SET enabled=1,next_due_at=0,error='' WHERE id=1")
    db.execute('UPDATE local_sib_number_recovery_state SET retry_at=0 WHERE id=1')


def priority_profiles(db):
    row = db.execute('SELECT profiles_json FROM local_sib_number_recovery_priority WHERE id=1 AND active=1').fetchone()
    return {(item['employer'], item['branch'], item['modality']): {'maximum': item['maximum']}
            for item in json.loads(row[0])} if row else {}


def configure(db, source_url, *, employees, interns):
    """Capture ceilings once. New hires cannot extend an in-progress repair."""
    row = db.execute('SELECT * FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    if row and row['source_url'] == source_url:
        return
    ranges = []
    for key, label, upper in (('interns', 'Estágios', interns), ('employees', 'Funcionários', employees)):
        upper = max(0, min(999999, int(upper)))
        if upper:
            ranges.append(dict(key=key, label=label, upper=upper, next=upper,
                               completedPasses=0, checked=0, imported=0, unresolved=0))
    now = time.time()
    db.execute('''INSERT INTO local_sib_number_recovery_state(id,source_url,ranges_json,started_at,completed_at)
        VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
        ranges_json=excluded.ranges_json,last_lane='',background_turn=0,
        started_at=excluded.started_at,completed_at=excluded.completed_at''',
        (source_url, json.dumps(ranges), now, 0 if ranges else now))


def next_batch(db, *, size=50, only_lane=''):
    row = db.execute('SELECT * FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    if not row or row['completed_at'] or closure(db, row['source_url']):
        return '', []
    available = [item for item in json.loads(row['ranges_json']) if item['completedPasses'] < PASSES
                 and (not only_lane or item['key'] == only_lane)]
    if not available:
        return '', []
    lane = next((item for item in available if item['key'] != row['last_lane']), available[0])
    db.execute('UPDATE local_sib_number_recovery_state SET last_lane=? WHERE id=1', (lane['key'],))
    return lane['key'], [str(value) for value in range(lane['next'], max(0, lane['next'] - size), -1)]


def advance(db, lane, payroll, *, imported=0, unresolved=0):
    """Save the repair cursor in the same transaction as its confirmed outcome."""
    row = db.execute('SELECT * FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    ranges = json.loads(row['ranges_json'])
    current = next(item for item in ranges if item['key'] == lane)
    if current['completedPasses'] >= PASSES or current['next'] != int(payroll):
        raise ValueError('O ponto salvo da recuperação mudou; a consulta será retomada.')
    current['checked'] += 1
    current['imported'] += imported
    current['unresolved'] += unresolved
    current['next'] -= 1
    if current['next'] < 1:
        current['completedPasses'] += 1
        current['next'] = current['upper'] if current['completedPasses'] < PASSES else 0
    done = all(item['completedPasses'] == PASSES for item in ranges)
    db.execute('UPDATE local_sib_number_recovery_state SET ranges_json=?,completed_at=? WHERE id=1',
               (json.dumps(ranges), time.time() if done else 0))
    if lane == 'interns' and current['completedPasses'] == PASSES and priority_active(db, row['source_url']):
        # Other routines retained their enabled flags and saved cursors. Dropping
        # the temporary mode lets the scheduler resume them on its next turn.
        db.execute('''UPDATE local_sib_discovery_state SET enabled=(
            SELECT previous_enabled FROM local_sib_number_recovery_priority WHERE id=1),
            fresh_due_at=0 WHERE id=1''')
        db.execute('UPDATE local_sib_number_recovery_priority SET active=0,finished_at=? WHERE id=1', (time.time(),))


def catalog_turn(db):
    row = db.execute('SELECT background_turn FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    turn = int(row[0] if row else 0)
    db.execute('UPDATE local_sib_number_recovery_state SET background_turn=background_turn+1 WHERE id=1')
    return turn % 2 == 1


def summary(db, source_url):
    row = db.execute('SELECT * FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    if not row or row['source_url'] != source_url:
        return dict(configured=False, active=False, complete=False, passes=PASSES, ranges=[], checked=0, total=0)
    ranges = [{**item, 'total': item['upper'] * PASSES,
               'pass': min(PASSES, item['completedPasses'] + 1),
               'complete': item['completedPasses'] == PASSES} for item in json.loads(row['ranges_json'])]
    closed = closure(db, source_url)
    return dict(configured=True, active=not bool(row['completed_at'] or closed), complete=bool(row['completed_at']),
                closed=bool(closed), closedAt=closed['closed_at'] if closed else 0,
                closedReason=closed['reason'] if closed else '',
                passes=PASSES, ranges=ranges, checked=sum(item['checked'] for item in ranges),
                total=sum(item['total'] for item in ranges),
                imported=sum(item['imported'] for item in ranges),
                unresolved=sum(item['unresolved'] for item in ranges),
                startedAt=row['started_at'], completedAt=row['completed_at'],
                priorityLane='interns' if priority_active(db, source_url) else '')
