"""Read-only SIB absence sync. Unconfirmed identity or missing responses never mean zero."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date

from .integrations import IntegrationError, _identity_date
from .sib_runtime import lease_active, worker_token

INTERVAL = 120
ARCHIVE_INTERVAL = 86400
BATCH_SIZE = 50
SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_absence_state (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 1,
 source_url TEXT NOT NULL DEFAULT '', lease_token TEXT NOT NULL DEFAULT '',
 lease_until REAL NOT NULL DEFAULT 0, next_due_at REAL NOT NULL DEFAULT 0,
 batch_supported INTEGER NOT NULL DEFAULT 1, last_checked_at REAL NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_absence_checks (
 source_url TEXT NOT NULL, payroll_id TEXT NOT NULL, identity_signature TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', checked_at REAL NOT NULL DEFAULT 0,
 due_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 resolved_keys_json TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(source_url,payroll_id)
);
CREATE INDEX IF NOT EXISTS sib_absence_due ON local_sib_absence_checks(source_url,due_at,checked_at);
CREATE TABLE IF NOT EXISTS local_sib_absences (
 id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, payroll_id TEXT NOT NULL,
 sequence TEXT NOT NULL, origin_key TEXT NOT NULL, employment_key TEXT NOT NULL DEFAULT '',
 start_date TEXT NOT NULL, end_date TEXT NOT NULL DEFAULT '',
 reason_code TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 reconciliation TEXT NOT NULL, source_missing INTEGER NOT NULL DEFAULT 0,
 data_json TEXT NOT NULL, first_seen_at REAL NOT NULL, checked_at REAL NOT NULL,
 changed_at REAL NOT NULL,
 UNIQUE(source_url,payroll_id,sequence,origin_key)
);
CREATE INDEX IF NOT EXISTS sib_absence_link ON local_sib_absences(source_url,employment_key,start_date,end_date);
CREATE TABLE IF NOT EXISTS local_sib_absence_history (
 id INTEGER PRIMARY KEY, absence_id INTEGER NOT NULL REFERENCES local_sib_absences(id),
 previous_json TEXT, current_json TEXT NOT NULL, changed_at REAL NOT NULL
);
"""
from .absence_workflow import SCHEMA as FOLLOWUP_SCHEMA
SCHEMA += FOLLOWUP_SCHEMA


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def source(db):
    row = db.execute("SELECT sib_base_url FROM local_integration_settings WHERE id=1").fetchone()
    return row[0] if row else ''


def candidates(db):
    result = {}
    from .sib_presence import overlays
    presence = overlays(db)
    for row in db.execute("SELECT source_id,data_json FROM local_sib_records WHERE dataset='employments'"):
        data = json.loads(row['data_json'])
        membership = presence.get(row['source_id'], {})
        if membership.get('sibPresence') == 'EXCLUDED' or membership.get('contractCancelled'):
            continue
        payroll = str(data.get('registration') or '')
        if re.fullmatch(r'\d{1,10}', payroll):
            result.setdefault(str(int(payroll)), []).append({**data, 'key': 'sib:' + row['source_id']})
    return result


def _seed(db, url, known):
    for payroll, items in known.items():
        signature = hashlib.sha256(_json(sorted((item['key'], item.get('cpf'), item.get('admissionDate'), item.get('companyId'), item.get('dismissalDate')) for item in items)).encode()).hexdigest()
        db.execute("""INSERT INTO local_sib_absence_checks(source_url,payroll_id,identity_signature)
          VALUES(?,?,?) ON CONFLICT(source_url,payroll_id) DO UPDATE SET
          identity_signature=excluded.identity_signature,status='PENDING',due_at=0,attempts=0,resolved_keys_json='[]'
          WHERE identity_signature<>excluded.identity_signature""", (url, payroll, signature))


def _base_match(items, response):
    if not response or response.get('error') or not response.get('data'):
        return None
    employee = response['data']['employee']
    cpf = re.sub(r'\D', '', str(employee.get('cpf') or '')).zfill(11)
    admission = _identity_date(employee.get('admission_date'))
    company = str(employee.get('client_code') or '')
    if len(cpf) != 11 or not cpf.strip('0') or not admission or not company:
        return None
    matches = [item for item in items if re.sub(r'\D', '', str(item.get('cpf') or '')).zfill(11) == cpf
               and item.get('admissionDate') == admission and str(item.get('companyId') or '') == company]
    return matches[0] if len(matches) == 1 else None


def _resolve(items, row, base):
    employer, branch = row.get('employerCode'), row.get('branchCode')
    if employer and branch:
        matches = [item for item in items if str(item.get('employerCode')) == employer and str(item.get('branchCode')) == branch]
        match = matches[0] if len(matches) == 1 else None
    else:
        match = base
    if not match:
        return ''
    # A historical event preceding the current admission may belong to a rehire.
    if not match.get('admissionDate') or row['startDate'] < match['admissionDate']:
        return ''
    if match.get('dismissalDate') and row['startDate'] > match['dismissalDate']:
        return ''
    return match['key']


def _validated(payroll, rows):
    result, seen = [], set()
    for row in rows:
        if (str(row.get('idFolha')) != payroll or not re.fullmatch(r'[\w.-]{1,80}', row.get('id', ''))
                or not _identity_date(row.get('startDate')) or row.get('invalidEndDate')
                or (row.get('endDate') and row['endDate'] < row['startDate'])):
            raise ValueError('Afastamento sem identificação ou com datas inválidas; a última leitura foi preservada.')
        origin = ':'.join((row.get('employerCode') or '', row.get('branchCode') or ''))
        key = (row['id'], origin)
        if key in seen:
            raise ValueError('A Bridge repetiu uma sequência de afastamento; é necessário conferir a origem.')
        seen.add(key)
        result.append((row, origin))
    return result


def _save(db, url, payroll, rows, items, canonical, now):
    validated = _validated(payroll, rows)
    base = _base_match(items, canonical)
    unresolved = False
    present = set()
    for row, origin in validated:
        key = _resolve(items, row, base)
        unresolved |= not bool(key)
        data = {**row, 'employmentKey': key, 'sourceMissing': False}
        encoded = _json(data)
        previous = db.execute("SELECT * FROM local_sib_absences WHERE source_url=? AND payroll_id=? AND sequence=? AND origin_key=?", (url, payroll, row['id'], origin)).fetchone()
        if previous:
            absence_id = previous['id']
            if previous['data_json'] != encoded or previous['source_missing']:
                db.execute("INSERT INTO local_sib_absence_history(absence_id,previous_json,current_json,changed_at) VALUES(?,?,?,?)", (absence_id, previous['data_json'], encoded, now))
            db.execute("""UPDATE local_sib_absences SET employment_key=?,start_date=?,end_date=?,reason_code=?,reason=?,
              reconciliation=?,source_missing=0,data_json=?,checked_at=?,changed_at=CASE WHEN data_json<>? OR source_missing=1 THEN ? ELSE changed_at END WHERE id=?""",
              (key, row['startDate'], row['endDate'], row.get('reasonCode', ''), row.get('reason', ''), 'MATCHED' if key else 'REVIEW', encoded, now, encoded, now, absence_id))
        else:
            absence_id = db.execute("""INSERT INTO local_sib_absences(source_url,payroll_id,sequence,origin_key,employment_key,start_date,end_date,reason_code,reason,reconciliation,data_json,first_seen_at,checked_at,changed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (url, payroll, row['id'], origin, key, row['startDate'], row['endDate'], row.get('reasonCode', ''), row.get('reason', ''), 'MATCHED' if key else 'REVIEW', encoded, now, now, now)).lastrowid
            db.execute("INSERT INTO local_sib_absence_history(absence_id,current_json,changed_at) VALUES(?,?,?)", (absence_id, encoded, now))
        present.add(absence_id)
    # Disappearance is recorded for review. It does not manufacture an end date.
    for previous in db.execute("SELECT * FROM local_sib_absences WHERE source_url=? AND payroll_id=? AND source_missing=0", (url, payroll)).fetchall():
        if previous['id'] not in present:
            unresolved = True
            encoded = _json({**json.loads(previous['data_json']), 'sourceMissing': True})
            db.execute("UPDATE local_sib_absences SET source_missing=1,data_json=?,changed_at=?,checked_at=? WHERE id=?", (encoded, now, now, previous['id']))
            db.execute("INSERT INTO local_sib_absence_history(absence_id,previous_json,current_json,changed_at) VALUES(?,?,?,?)", (previous['id'], previous['data_json'], encoded, now))
    unresolved |= bool(db.execute('SELECT 1 FROM local_sib_absences WHERE source_url=? AND payroll_id=? AND source_missing=1 LIMIT 1', (url, payroll)).fetchone())
    status = 'OK' if base and not unresolved else 'AMBIGUOUS'
    live = any(not item.get('dismissalDate') or item['dismissalDate'] >= date.today().isoformat() for item in items)
    opened = any(not row.get('endDate') or row['endDate'] >= date.today().isoformat() for row, _ in validated)
    due = now + (INTERVAL if live or opened or status != 'OK' else ARCHIVE_INTERVAL)
    db.execute("""UPDATE local_sib_absence_checks SET status=?,checked_at=?,due_at=?,attempts=0,
       resolved_keys_json=?,error=? WHERE source_url=? AND payroll_id=?""",
       (status, now, due, _json([base['key']] if base and not unresolved else []), '' if status == 'OK' else 'Vínculo ou retorno não confirmado. Confira a identidade e o histórico da origem.', url, payroll))
    from .absence_workflow import refresh_alerts
    refresh_alerts(db, absence_ids=present)


def _failed(db, url, payroll, message, now):
    row = db.execute("SELECT attempts FROM local_sib_absence_checks WHERE source_url=? AND payroll_id=?", (url, payroll)).fetchone()
    delay = min(900, 60 * 2 ** min(int(row[0] or 0), 4))
    db.execute("""UPDATE local_sib_absence_checks SET status='ERROR',error=?,due_at=?,attempts=attempts+1
      WHERE source_url=? AND payroll_id=?""", (message[:240], now + delay, url, payroll))


def run_due(database, client, *, now=None, stop_event=None):
    from .database import connect
    from .sib_auto_refresh import _scan_busy
    now = time.time() if now is None else now
    url = client.config.base_url
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO local_sib_absence_state(id) VALUES(1)')
        state = db.execute('SELECT * FROM local_sib_absence_state WHERE id=1').fetchone()
        if not state['enabled'] or _scan_busy(db) or lease_active(state['lease_token'], state['lease_until']) or (state['source_url'] == url and state['next_due_at'] > now):
            return False
        known = candidates(db)
        _seed(db, url, known)
        supported = bool(state['batch_supported']) if state['source_url'] == url else True
        due = [dict(row) for row in db.execute("SELECT * FROM local_sib_absence_checks WHERE source_url=? AND due_at<=? ORDER BY due_at,payroll_id", (url, now)) if row['payroll_id'] in known]
        # Reserve room for active/open checks while the first historical sweep runs.
        hot = [row for row in due if row['checked_at'] and row['due_at'] - row['checked_at'] <= INTERVAL]
        fresh = sorted((row for row in due if not row['checked_at']), key=lambda row: (not any(not item.get('dismissalDate') for item in known[row['payroll_id']]), int(row['payroll_id'])))
        ordered = hot[:10] + fresh + due
        ids = list(dict.fromkeys(row['payroll_id'] for row in ordered))[:BATCH_SIZE if supported else 1]
        if not ids:
            return False
        token = worker_token()
        # Batch request + identity batch are bounded to at most two 60s calls.
        db.execute("UPDATE local_sib_absence_state SET source_url=?,lease_token=?,lease_until=?,batch_supported=?,error='' WHERE id=1", (url, token, time.time() + 180, int(supported)))
    try:
        if stop_event and stop_event.is_set():
            return False
        try:
            results = client.get_absences_batch(ids) if supported else [dict(id_folha=identifier, data=client.get_absences(identifier), error=None) for identifier in ids]
        except IntegrationError as error:
            if error.status_code in (404, 405, 501) and supported:
                with connect(database) as db:
                    db.execute("UPDATE local_sib_absence_state SET batch_supported=0,next_due_at=?,error=? WHERE lease_token=?", (now + 10, 'Esta Bridge não oferece lote de afastamentos. A leitura individual será usada.', token))
                return True
            raise
        identity = {item['id_folha']: item for item in client.get_scan_batch(ids)}
        by_id = {item['id_folha']: item for item in results}
        with connect(database) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM local_sib_absence_state WHERE id=1').fetchone()
            if current['lease_token'] != token or source(db) != url:
                return False
            # Re-read identities: an import or local source correction can finish during I/O.
            current_known = candidates(db)
            for payroll in ids:
                response = by_id.get(payroll)
                if not response or response.get('error') or not isinstance(response.get('data'), list):
                    _failed(db, url, payroll, 'A consulta não foi confirmada. Os últimos dados foram preservados.', now)
                    continue
                try:
                    _save(db, url, payroll, response['data'], current_known.get(payroll, []), identity.get(payroll), now)
                except ValueError as error:
                    _failed(db, url, payroll, str(error), now)
            db.execute("UPDATE local_sib_absence_state SET last_checked_at=?,next_due_at=0,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?", (now, token))
    except Exception as error:
        message = 'A Bridge não confirmou a consulta. Nova tentativa automática; os dados anteriores foram preservados.'
        with connect(database) as db:
            for payroll in ids:
                _failed(db, url, payroll, message, now)
            db.execute("UPDATE local_sib_absence_state SET error=?,next_due_at=?,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?", (message, now + 60, token))
    finally:
        with connect(database) as db:
            db.execute("UPDATE local_sib_absence_state SET lease_token='',lease_until=0 WHERE lease_token=?", (token,))
    return True


def control(db, action):
    if action not in ('start', 'pause', 'refresh'):
        raise ValueError('Comando de afastamentos inválido.')
    db.execute('INSERT OR IGNORE INTO local_sib_absence_state(id) VALUES(1)')
    db.execute("UPDATE local_sib_absence_state SET enabled=?,next_due_at=0,updated_at=CURRENT_TIMESTAMP WHERE id=1", (int(action != 'pause'),))
    if action == 'refresh':
        db.execute('UPDATE local_sib_absence_checks SET due_at=0 WHERE source_url=?', (source(db),))


def summary(db):
    url = source(db)
    state = db.execute('SELECT * FROM local_sib_absence_state WHERE id=1').fetchone()
    counts = {row[0]: row[1] for row in db.execute('SELECT status,COUNT(*) FROM local_sib_absence_checks WHERE source_url=? GROUP BY status', (url,))}
    total = db.execute("SELECT COUNT(DISTINCT json_extract(data_json,'$.registration')) FROM local_sib_records WHERE dataset='employments'").fetchone()[0]
    from .sib_auto_refresh import _scan_busy
    return {'enabled': bool(not state or state['enabled']), 'running': bool(state and lease_active(state['lease_token'], state['lease_until'])),
            'waiting': _scan_busy(db), 'total': total, 'checked': sum(counts.get(key, 0) for key in ('OK', 'AMBIGUOUS')),
            'confirmed': counts.get('OK', 0), 'errors': counts.get('ERROR', 0), 'ambiguous': counts.get('AMBIGUOUS', 0),
            'pending': max(0, total - sum(counts.values())) + counts.get('PENDING', 0),
            'intervalSeconds': INTERVAL, 'archiveIntervalSeconds': ARCHIVE_INTERVAL,
            'batchSize': BATCH_SIZE if not state or state['batch_supported'] else 1,
            'lastCheckedAt': state['last_checked_at'] if state else 0, 'error': state['error'] if state else ''}


def _effective_end_date(row):
    source_end = _identity_date(row['end_date'] or '')
    dismissal = _identity_date(row['dismissal_date'] or '') if 'dismissal_date' in row.keys() else ''
    start = _identity_date(row['start_date'] or '')
    if dismissal and start and dismissal < start:
        return source_end, False, True
    if dismissal and (not source_end or dismissal < source_end):
        return dismissal, True, False
    return source_end, False, False


def status_on(row, cutoff):
    if not row['employment_key'] or row['source_missing']:
        return 'review'
    effective_end, _, inconsistent = _effective_end_date(row)
    if inconsistent:
        return 'review'
    if row['start_date'] > cutoff:
        return 'future'
    return 'closed' if effective_end and effective_end < cutoff else 'active'


def records(db, *, keys=None, cutoff=None):
    cutoff = cutoff or date.today().isoformat()
    result = []
    for row in db.execute('''SELECT a.*,json_extract(e.data_json,'$.dismissalDate') AS dismissal_date
        FROM local_sib_absences a LEFT JOIN local_sib_records e
          ON e.dataset='employments' AND e.source_id=substr(a.employment_key,5)
        WHERE a.source_url=? ORDER BY a.start_date DESC,a.id DESC''', (source(db),)):
        if keys is not None and row['employment_key'] not in keys:
            continue
        result.append({**dict(row), 'status': status_on(row, cutoff)})
    return result


def coverage(db, keys):
    confirmed, checked = set(), []
    for row in db.execute("SELECT * FROM local_sib_absence_checks WHERE source_url=? AND status='OK'", (source(db),)):
        matched = set(json.loads(row['resolved_keys_json'])) & keys
        if matched:
            confirmed.update(matched)
            checked.append(row['checked_at'])
    return {'total': len(keys), 'confirmed': len(confirmed), 'unknown': len(keys - confirmed), 'oldestCheckedAt': min(checked, default=0)}


def analytics(db, keys, cutoff):
    keys = {key for key in keys if key.startswith('sib:')}
    rows = records(db, keys=keys, cutoff=cutoff)
    active = [row for row in rows if row['status'] == 'active']
    return {**coverage(db, keys), 'active': len({row['employment_key'] for row in active}),
            'events': len(active), 'review': sum(row['status'] == 'review' for row in rows), 'cutoff': cutoff}


def for_employments(db, keys):
    keys = {key for key in keys if key.startswith('sib:')}
    rows = records(db, keys=keys)
    return {'coverage': coverage(db, keys), 'records': [public_record(row) for row in rows]}


def public_record(row):
    end_date, inferred_from_dismissal, _ = _effective_end_date(row)
    return {'id': row['id'], 'sequence': row['sequence'], 'registration': row['payroll_id'], 'employmentKey': row['employment_key'],
            'startDate': row['start_date'], 'endDate': end_date or '', 'sourceEndDate': row['end_date'],
            'endDateFromDismissal': inferred_from_dismissal, 'reasonCode': row['reason_code'],
            'reason': row['reason'], 'status': row['status'], 'checkedAt': row['checked_at'], 'sourceMissing': bool(row['source_missing'])}


def page_data(db, parameters, user):
    from .registry import normalize
    def param(key, default=''):
        value = parameters.get(key, [default])
        return str(value[0] if isinstance(value, list) else value)[:160]
    cutoff = _identity_date(param('cutoff')) or date.today().isoformat()
    from .permissions import can, allowed_company, scope
    if not can(user, 'absences.view'):
        raise PermissionError('Seu perfil não permite consultar afastamentos.')
    known = candidates(db)
    known = {payroll: [item for item in items if allowed_company(db, user,
             company_key='sib:' + str(item.get('companyId') or ''), code=item.get('companyId'), cnpj=item.get('takerCnpj'))]
             for payroll, items in known.items()}
    known = {payroll: items for payroll, items in known.items() if items}
    names = {item['key']: item for items in known.values() for item in items}
    companies = {row['source_id']: json.loads(row['data_json']).get('name') or '' for row in db.execute("SELECT source_id,data_json FROM local_sib_records WHERE dataset='companies'")}
    query, selected_status = normalize(param('q').strip()), param('status', 'all')
    visible_keys = set(names)
    all_rows = [row for row in records(db, cutoff=cutoff) if row['employment_key'] in visible_keys
                or (not row['employment_key'] and scope(user) == 'ALL')]
    from .absence_workflow import attention_ids
    attention = attention_ids(db, user, param('followup'))
    filtered = []
    totals = {key: 0 for key in ('all', 'active', 'closed', 'future', 'review')}
    for row in all_rows:
        person = names.get(row['employment_key'], {})
        company_name = companies.get(str(person.get('companyId') or ''), '')
        if query and query not in normalize(f"{row['payroll_id']} {person.get('name', '')} {row['reason']} {person.get('companyId', '')} {company_name}"):
            continue
        if attention is not None and row['id'] not in attention:
            continue
        totals['all'] += 1
        totals[row['status']] += 1
        if selected_status in ('all', row['status']):
            filtered.append({**public_record(row), 'name': person.get('name') or 'Vínculo em conferência', 'companyCode': person.get('companyId') or '', 'company': company_name})
    page = max(1, min(int(param('page', '1')) if param('page', '1').isdigit() else 1, max(1, (len(filtered) + 24)//25)))
    detail = None
    if param('id').isdigit():
        match = next((row for row in all_rows if row['id'] == int(param('id'))), None)
        if match:
            detail = {**public_record(match), 'history': [dict(row) for row in db.execute('SELECT previous_json,current_json,changed_at FROM local_sib_absence_history WHERE absence_id=? ORDER BY id DESC LIMIT 20', (match['id'],))]}
            if not can(user, 'sensitive.view'):
                for event in detail['history']:
                    for field in ('previous_json', 'current_json'):
                        if event[field]:
                            snapshot = json.loads(event[field])
                            snapshot.pop('metadata', None)
                            event[field] = _json(snapshot)
    visible_payroll = set(known)
    issue_rows = [row for row in db.execute("SELECT * FROM local_sib_absence_checks WHERE source_url=? AND status IN ('ERROR','AMBIGUOUS') ORDER BY due_at,payroll_id", (source(db),))
                  if row['payroll_id'] in visible_payroll]
    issue_total = len(issue_rows)
    issue_page = max(1, min(int(param('issuePage', '1')) if param('issuePage', '1').isdigit() else 1, max(1, (issue_total+9)//10)))
    issues = [{'registration': row['payroll_id'], 'status': row['status'], 'error': row['error'], 'lastConfirmedAt': row['checked_at'], 'retryAt': row['due_at']}
              for row in issue_rows[(issue_page-1)*10:issue_page*10]]
    summary_data = summary(db)
    if scope(user) != 'ALL':
        checked = [row for row in db.execute('SELECT payroll_id,status FROM local_sib_absence_checks WHERE source_url=?', (source(db),)) if row['payroll_id'] in visible_payroll]
        summary_data.update(total=len(visible_payroll), checked=sum(row['status'] in ('OK', 'AMBIGUOUS') for row in checked),
                            confirmed=sum(row['status'] == 'OK' for row in checked), errors=sum(row['status'] == 'ERROR' for row in checked),
                            ambiguous=sum(row['status'] == 'AMBIGUOUS' for row in checked),
                            pending=max(0, len(visible_payroll)-len(checked))+sum(row['status'] == 'PENDING' for row in checked))
    result = {'records': filtered[(page-1)*25:page*25], 'total': len(filtered), 'counts': totals, 'page': page, 'pageSize': 25,
            'issues': issues, 'issueTotal': issue_total, 'issuePage': issue_page,
            'summary': summary_data, 'detail': detail, 'cutoff': cutoff, 'csrf': user['csrf_token']}
    from .absence_workflow import enrich_page
    return enrich_page(db, result, user)
