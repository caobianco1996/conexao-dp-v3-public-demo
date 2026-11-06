"""Local follow-up of confirmed SIB absences; never writes to the payroll source."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from .integrations import _identity_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_absence_followup_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), alert_days_json TEXT NOT NULL DEFAULT '[15,30,45,60,90]',
 proximity_days INTEGER NOT NULL DEFAULT 5, prolonged_days INTEGER NOT NULL DEFAULT 60,
 accident_level TEXT NOT NULL DEFAULT 'CRITICAL', prolonged_level TEXT NOT NULL DEFAULT 'HIGH',
 recurrence_level TEXT NOT NULL DEFAULT 'HIGH', retroactive_level TEXT NOT NULL DEFAULT 'ATTENTION',
 enabled INTEGER NOT NULL DEFAULT 1, last_refresh_at REAL NOT NULL DEFAULT 0,
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_absence_alerts (
 id INTEGER PRIMARY KEY, absence_id INTEGER NOT NULL REFERENCES local_sib_absences(id),
 source_identity TEXT NOT NULL, rule_key TEXT NOT NULL, title TEXT NOT NULL,
 expected_date TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'ATTENTION',
 status TEXT NOT NULL DEFAULT 'PENDING', responsible_id INTEGER REFERENCES users(id),
 note TEXT NOT NULL DEFAULT '', automatic INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(absence_id,source_identity,rule_key)
);
CREATE INDEX IF NOT EXISTS local_absence_alerts_due ON local_absence_alerts(status,due_date,absence_id);
CREATE TABLE IF NOT EXISTS local_absence_followup_events (
 id INTEGER PRIMARY KEY, absence_id INTEGER NOT NULL REFERENCES local_sib_absences(id),
 source_identity TEXT NOT NULL, alert_id INTEGER REFERENCES local_absence_alerts(id),
 action TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', actor_id INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_absence_events_owner ON local_absence_followup_events(absence_id,id DESC);
CREATE TABLE IF NOT EXISTS local_absence_documents (
 absence_id INTEGER NOT NULL REFERENCES local_sib_absences(id), source_identity TEXT NOT NULL,
 document_id INTEGER NOT NULL REFERENCES local_registry_documents(id), linked_by INTEGER NOT NULL REFERENCES users(id),
 linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(absence_id,source_identity,document_id)
);
CREATE TABLE IF NOT EXISTS local_absence_deliveries (
 id INTEGER PRIMARY KEY, absence_id INTEGER NOT NULL REFERENCES local_sib_absences(id), source_identity TEXT NOT NULL,
 request_key TEXT NOT NULL UNIQUE, snapshot_hash TEXT NOT NULL, status TEXT NOT NULL,
 target TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL, documents_json TEXT NOT NULL DEFAULT '[]',
 protocol TEXT NOT NULL UNIQUE, error_message TEXT NOT NULL DEFAULT '',
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT NOT NULL DEFAULT '', receipt_reference TEXT NOT NULL DEFAULT '',
 receipt_by INTEGER REFERENCES users(id), receipt_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS local_absence_deliveries_owner ON local_absence_deliveries(absence_id,id DESC);
"""

LEVEL_LABELS = {'NORMAL': 'Normal', 'ATTENTION': 'Atenção', 'HIGH': 'Importante', 'CRITICAL': 'Crítico'}
LEVEL_RANK = {key: index for index, key in enumerate(LEVEL_LABELS)}
STATUS_LABELS = {'PENDING': 'Pendente', 'POSTPONED': 'Prorrogada', 'DONE': 'Concluída', 'CANCELLED': 'Cancelada'}
META_FIELDS = {
    'cid': 'CID', 'cid_correlacionado': 'CID correlacionado', 'medico': 'Médico',
    'acidente_trabalho': 'Acidente de trabalho', 'descricao_tipo_acidente_transito': 'Acidente de trânsito',
    'mesmo_motivo_60_dias': 'Recorrência informada pelo SIB', 'detalhamento': 'Detalhamento',
    'observacao': 'Observação', 'observacao_fim': 'Observação do encerramento',
    'data_alteracao_motivo': 'Alteração do motivo', 'origem_alteracao': 'Origem da alteração',
    'efeito_retroativo': 'Efeito retroativo', 'descricao_motivo_anterior': 'Motivo anterior',
}


def _today():
    return datetime.now(timezone(timedelta(hours=-3))).date()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _text(value, limit=1000):
    return ' '.join(str(value or '').split())[:limit]


def _flag(value):
    return str(value or '').strip().upper() in ('1', 'S', 'SIM', 'TRUE', 'Y', 'YES')


def _can(user, permission):
    from .permissions import can
    return can(user, permission)


def _event(db, absence_id, owner, action, actor_id=None, alert_id=None, **details):
    db.execute('INSERT INTO local_absence_followup_events(absence_id,source_identity,alert_id,action,details_json,actor_id) VALUES(?,?,?,?,?,?)',
               (absence_id, owner, alert_id, action, _json(details), actor_id))


def settings(db):
    row = db.execute('SELECT * FROM local_absence_followup_settings WHERE id=1').fetchone()
    if not row:
        return {'alertDays': [15, 30, 45, 60, 90], 'proximityDays': 5, 'prolongedDays': 60,
                'accidentLevel': 'CRITICAL', 'prolongedLevel': 'HIGH', 'recurrenceLevel': 'HIGH',
                'retroactiveLevel': 'ATTENTION', 'enabled': True}
    return {'alertDays': json.loads(row['alert_days_json']), 'proximityDays': row['proximity_days'],
            'prolongedDays': row['prolonged_days'], 'accidentLevel': row['accident_level'],
            'prolongedLevel': row['prolonged_level'], 'recurrenceLevel': row['recurrence_level'],
            'retroactiveLevel': row['retroactive_level'], 'enabled': bool(row['enabled'])}


def save_settings(db, payload, user):
    if not _can(user, 'absences.manage'):
        raise PermissionError('Seu perfil não pode alterar as regras de acompanhamento.')
    raw_days = payload.get('alertDays', [])
    if isinstance(raw_days, str):
        raw_days = [part.strip() for part in raw_days.replace(';', ',').split(',') if part.strip()]
    if not isinstance(raw_days, list) or len(raw_days) > 30:
        raise ValueError('Informe até 30 marcos de acompanhamento em dias.')
    try:
        if any(isinstance(value, bool) for value in raw_days):
            raise ValueError()
        days = sorted(set(int(value) for value in raw_days))
        proximity, prolonged = int(payload.get('proximityDays', 5)), int(payload.get('prolongedDays', 60))
    except (TypeError, ValueError):
        raise ValueError('Informe dias inteiros válidos.')
    if any(not 1 <= value <= 3650 for value in days) or not 0 <= proximity <= 90 or not 0 <= prolonged <= 3650:
        raise ValueError('Confira os marcos (1 a 3650 dias), a proximidade (0 a 90) e o período prolongado (0 a 3650).')
    levels = [str(payload.get(key) or '').upper() for key in ('accidentLevel', 'prolongedLevel', 'recurrenceLevel', 'retroactiveLevel')]
    if any(value not in LEVEL_LABELS for value in levels):
        raise ValueError('Escolha níveis de atenção válidos.')
    db.execute('INSERT OR IGNORE INTO local_absence_followup_settings(id) VALUES(1)')
    db.execute('''UPDATE local_absence_followup_settings SET alert_days_json=?,proximity_days=?,prolonged_days=?,
        accident_level=?,prolonged_level=?,recurrence_level=?,retroactive_level=?,enabled=?,last_refresh_at=0,
        updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1''',
        (_json(days), proximity, prolonged, *levels, int(bool(payload.get('enabled', True))), user['id']))
    refresh_alerts(db, force=True)
    return {'ok': True, 'message': 'Regras internas de acompanhamento atualizadas.', 'settings': settings(db)}


def context(db, absence_id, user=None, *, writable=True):
    from .sib_absences import source
    row = db.execute('SELECT * FROM local_sib_absences WHERE id=?', (int(absence_id),)).fetchone()
    if not row or row['source_url'] != source(db):
        raise LookupError('Afastamento não encontrado nesta conexão SIB.')
    if user is not None:
        if not _can(user, 'absences.view'):
            raise PermissionError('Seu perfil não permite consultar afastamentos.')
        from .permissions import require_subject, scope
        if row['employment_key']:
            require_subject(db, user, 'pessoas', row['employment_key'])
        elif scope(user) != 'ALL':
            raise PermissionError('Este afastamento ainda não possui uma identidade confirmada no seu recorte de acesso.')
    employment = db.execute("SELECT data_json FROM local_sib_records WHERE dataset='employments' AND source_id=?", (row['employment_key'][4:],)).fetchone() if row['employment_key'].startswith('sib:') else None
    data = json.loads(employment[0]) if employment else {}
    admission = _identity_date(data.get('admissionDate'))
    dismissal = _identity_date(data.get('dismissalDate') or data.get('terminationDate'))
    valid = bool(employment and admission and row['start_date'] >= admission and (not dismissal or row['start_date'] <= dismissal)
                 and not row['source_missing'] and row['reconciliation'] == 'MATCHED')
    if writable and not valid:
        raise ValueError('A identidade ou o período precisa ser confirmado pelo SIB antes de registrar providências, documentos ou envios.')
    from .sib_operations import identity
    owner = 'sib:' + identity(db, row['employment_key'][4:], admission) if employment and admission else ''
    fingerprint = hashlib.sha256(_json([owner, row['data_json'], row['source_missing']]).encode()).hexdigest()
    return dict(row), data, owner, fingerprint, valid


def _rules(row, policy, today):
    start = date.fromisoformat(row['start_date'])
    end = date.fromisoformat(row['end_date']) if row['end_date'] else today
    elapsed = max(0, (min(end, today) - start).days + 1)
    metadata = json.loads(row['data_json']).get('metadata', {})
    rules = []
    for threshold in policy['alertDays']:
        if elapsed + policy['proximityDays'] >= threshold:
            rules.append((f'DAYS_{threshold}', f'Acompanhar {threshold} dias de afastamento', (start + timedelta(days=threshold - 1)).isoformat(), 'ATTENTION'))
    if _flag(metadata.get('acidente_trabalho')):
        rules.append(('ACCIDENT', 'Acompanhar acidente de trabalho informado pelo SIB', row['start_date'], policy['accidentLevel']))
    if policy['prolongedDays'] and elapsed >= policy['prolongedDays']:
        rules.append(('PROLONGED', 'Acompanhar afastamento prolongado', (start + timedelta(days=policy['prolongedDays'] - 1)).isoformat(), policy['prolongedLevel']))
    if _flag(metadata.get('mesmo_motivo_60_dias')):
        rules.append(('RECURRENCE', 'Conferir recorrência informada pelo SIB', row['start_date'], policy['recurrenceLevel']))
    if _flag(metadata.get('efeito_retroativo')):
        rules.append(('RETROACTIVE', 'Conferir alteração retroativa informada pelo SIB', _identity_date(metadata.get('data_alteracao_motivo')) or row['start_date'], policy['retroactiveLevel']))
    return rules


def refresh_alerts(db, today=None, *, force=False, absence_ids=None):
    """Generate local tasks for current confirmed absences, keeping human decisions intact."""
    from .sib_absences import source, status_on
    today = today or _today()
    policy = settings(db)
    if not policy['enabled']:
        return 0
    state = db.execute('SELECT last_refresh_at FROM local_absence_followup_settings WHERE id=1').fetchone()
    if absence_ids is None and not force and state and time.time() - state[0] < 60:
        return 0
    args = [source(db), today.isoformat(), today.isoformat()]
    clause = ''
    if absence_ids is not None:
        ids = list(dict.fromkeys(int(value) for value in absence_ids))[:200]
        if not ids:
            return 0
        clause = ' AND id IN (' + ','.join('?' for _ in ids) + ')'
        args.extend(ids)
    rows = db.execute('''SELECT a.*,json_extract(e.data_json,'$.dismissalDate') AS dismissal_date
        FROM local_sib_absences a LEFT JOIN local_sib_records e
          ON e.dataset='employments' AND e.source_id=substr(a.employment_key,5)
        WHERE a.source_url=? AND a.start_date<=? AND (a.end_date='' OR a.end_date>=?)
          AND a.source_missing=0 AND a.reconciliation='MATCHED' ''' + clause, args).fetchall()
    created = 0
    for raw in rows:
        if status_on(raw, today.isoformat()) != 'active':
            continue
        try:
            row, _, owner, _, valid = context(db, raw['id'], writable=False)
        except (ValueError, LookupError):
            continue
        if not valid:
            continue
        for key, title, due, level in _rules(row, policy, today):
            cursor = db.execute('''INSERT OR IGNORE INTO local_absence_alerts
                (absence_id,source_identity,rule_key,title,expected_date,due_date,level,automatic)
                VALUES(?,?,?,?,?,?,?,1)''', (row['id'], owner, key, title, due, due, level))
            if cursor.rowcount:
                created += 1
                _event(db, row['id'], owner, 'AUTO_ALERT_CREATED', alert_id=cursor.lastrowid, title=title, dueDate=due, level=level)
            else:
                existing = db.execute('SELECT * FROM local_absence_alerts WHERE absence_id=? AND source_identity=? AND rule_key=?', (row['id'], owner, key)).fetchone()
                if existing and existing['status'] in ('PENDING', 'POSTPONED') and (existing['level'] != level or existing['expected_date'] != due):
                    # A human postponement survives recalculation of the source date.
                    next_due = due if existing['status'] == 'PENDING' and existing['due_date'] == existing['expected_date'] else existing['due_date']
                    db.execute('UPDATE local_absence_alerts SET expected_date=?,due_date=?,level=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (due, next_due, level, existing['id']))
                    _event(db, row['id'], owner, 'AUTO_ALERT_UPDATED', alert_id=existing['id'], previousDue=existing['due_date'], dueDate=next_due, level=level)
    if absence_ids is None:
        db.execute('INSERT OR IGNORE INTO local_absence_followup_settings(id) VALUES(1)')
        db.execute('UPDATE local_absence_followup_settings SET last_refresh_at=? WHERE id=1', (time.time(),))
    return created


def run_due(database):
    from .database import connect
    with connect(database) as db:
        refresh_alerts(db)


def _alert_public(row, today, owner):
    item = dict(row)
    item.update(statusLabel=STATUS_LABELS.get(item['status'], item['status']), levelLabel=LEVEL_LABELS.get(item['level'], item['level']),
                overdue=item['status'] in ('PENDING', 'POSTPONED') and item['due_date'] < today,
                currentIdentity=item['source_identity'] == owner)
    return item


def attention_ids(db, user, selection):
    """Small operational queue, scoped before absence totals and pagination."""
    if selection not in ('pending', 'overdue', 'critical'):
        return None
    from .permissions import allowed_company
    today, result, identities = _today().isoformat(), set(), {}
    from .sib_absences import status_on
    for row in db.execute('''SELECT al.*,a.employment_key,a.start_date,a.end_date,a.source_missing,
        json_extract(e.data_json,'$.dismissalDate') dismissal_date,
        json_extract(e.data_json,'$.companyId') company_code
        FROM local_absence_alerts al JOIN local_sib_absences a ON a.id=al.absence_id
        JOIN local_sib_records e ON e.dataset='employments' AND e.source_id=substr(a.employment_key,5)
        WHERE al.status IN ('PENDING','POSTPONED') AND a.source_url=(SELECT sib_base_url FROM local_integration_settings WHERE id=1)'''):
        if status_on(row, today) != 'active':
            continue
        if not allowed_company(db, user, company_key='sib:' + str(row['company_code'] or ''), code=row['company_code']):
            continue
        if selection == 'overdue' and row['due_date'] >= today:
            continue
        if selection == 'critical' and row['level'] != 'CRITICAL':
            continue
        if row['absence_id'] not in identities:
            identities[row['absence_id']] = context(db, row['absence_id'], writable=False)[2]
        if row['source_identity'] == identities[row['absence_id']]:
            result.add(row['absence_id'])
    return result


def enrich_page(db, result, user):
    today = _today().isoformat()
    policy = settings(db)
    can_manage = _can(user, 'absences.manage')
    visible = []
    from .permissions import allowed_company
    identities = {}
    for row in db.execute('''SELECT al.*,u.name responsible_name,a.employment_key,a.source_url,
         a.start_date,a.end_date,a.source_missing,
         json_extract(e.data_json,'$.dismissalDate') dismissal_date,
         json_extract(e.data_json,'$.companyId') company_code FROM local_absence_alerts al
         JOIN local_sib_absences a ON a.id=al.absence_id LEFT JOIN users u ON u.id=al.responsible_id
         LEFT JOIN local_sib_records e ON e.dataset='employments' AND e.source_id=substr(a.employment_key,5)
         WHERE a.source_url=(SELECT sib_base_url FROM local_integration_settings WHERE id=1)'''):
        if row['employment_key'] and allowed_company(db, user, company_key='sib:' + str(row['company_code'] or ''), code=row['company_code']):
            visible.append(row)
            if row['absence_id'] not in identities:
                identities[row['absence_id']] = context(db, row['absence_id'], writable=False)[2]
    from .sib_absences import status_on
    pending = [row for row in visible if row['status'] in ('PENDING', 'POSTPONED')
               and row['source_identity'] == identities.get(row['absence_id'])
               and status_on(row, today) == 'active']
    by_absence, levels = {}, {}
    for alert in pending:
        by_absence.setdefault(alert['absence_id'], []).append(alert)
        if LEVEL_RANK.get(alert['level'], 0) > LEVEL_RANK.get(levels.get(alert['absence_id'], 'NORMAL'), 0):
            levels[alert['absence_id']] = alert['level']
    for record in result['records']:
        tasks = by_absence.get(record['id'], [])
        level = levels.get(record['id'], 'NORMAL')
        record.update(pendingAlerts=len(tasks), overdueAlerts=sum(row['due_date'] < today for row in tasks),
                      nextAlert=min((row['due_date'] for row in tasks), default=''), criticality=level,
                      criticalityLabel=LEVEL_LABELS[level])
    result.update(followupSettings=policy if can_manage else None,
                  followupSummary={'pending': len(pending), 'overdue': sum(row['due_date'] < today for row in pending),
                                   'critical': sum(value == 'CRITICAL' for value in levels.values())},
                  canManage=can_manage, canUpload=_can(user, 'documents.upload'), canSend=_can(user, 'communications.send'),
                  canManageSync=_can(user, 'integrations.manage'),
                  canDocuments=_can(user, 'documents.view'),
                  responsibles=[dict(row) for row in db.execute('SELECT id,name FROM users WHERE active=1 ORDER BY name COLLATE NOCASE')] if can_manage else [])
    detail = result.get('detail')
    if detail:
        row, employee, owner, fingerprint, valid = context(db, detail['id'], user, writable=False)
        metadata = json.loads(row['data_json']).get('metadata', {})
        detail.update(contextToken=fingerprint, actionsAvailable=valid, email=str(employee.get('email') or ''),
                      metadata=[{'label': label, 'value': str(metadata[key])} for key, label in META_FIELDS.items() if metadata.get(key) not in (None, '')] if _can(user, 'sensitive.view') else [],
                      alerts=[_alert_public(value, today, owner) for value in visible if value['absence_id'] == detail['id']],
                      followupHistory=[dict(value) for value in db.execute('''SELECT e.*,u.name actor_name FROM local_absence_followup_events e
                        LEFT JOIN users u ON u.id=e.actor_id WHERE e.absence_id=? AND e.source_identity=? ORDER BY e.id DESC LIMIT 30''', (detail['id'], owner))],
                      deliveries=[dict(value) for value in db.execute('SELECT * FROM local_absence_deliveries WHERE absence_id=? AND source_identity=? ORDER BY id DESC LIMIT 15', (detail['id'], owner))])
        if owner and _can(user, 'documents.view'):
            from .registry_documents import list_documents, resolve_owner
            document_owner = resolve_owner(db, 'pessoas', row['employment_key'], user=user)
            detail['availableDocuments'] = list_documents(db, document_owner, user=user)
            linked = {value[0] for value in db.execute('SELECT document_id FROM local_absence_documents WHERE absence_id=? AND source_identity=?', (detail['id'], owner))}
            detail['documentIds'] = sorted(linked & {int(document['id']) for document in detail['availableDocuments']})
        else:
            detail.update(availableDocuments=[], documentIds=[])
    return result


def mutate(db, payload, user):
    action = str(payload.get('action') or '')
    required = 'documents.upload' if action == 'link_document' else 'communications.send' if action in ('confirm_receipt', 'resolve_delivery') else 'absences.manage'
    if not _can(user, required):
        raise PermissionError('Seu perfil não permite esta ação no acompanhamento do afastamento.')
    if action == 'settings':
        return save_settings(db, payload, user)
    row, _, owner, fingerprint, _ = context(db, int(payload.get('absenceId') or 0), user)
    if str(payload.get('contextToken') or '') != fingerprint:
        raise ValueError('Os dados do afastamento mudaram. Atualize a tela antes de continuar.')
    responsible = payload.get('responsibleId')
    try:
        responsible = int(responsible) if responsible not in (None, '') else None
    except (TypeError, ValueError):
        raise ValueError('Escolha um responsável válido.')
    if responsible is not None and not db.execute('SELECT 1 FROM users WHERE id=? AND active=1', (responsible,)).fetchone():
        raise ValueError('O responsável selecionado está inativo ou não existe.')
    note = _text(payload.get('note'))
    due = _identity_date(payload.get('dueDate'))
    if action == 'create_alert':
        title = _text(payload.get('title'), 180)
        level = str(payload.get('level') or 'ATTENTION')
        if len(title) < 3 or not due or level not in LEVEL_LABELS:
            raise ValueError('Informe título, prazo e nível de atenção válidos.')
        alert_id = db.execute('''INSERT INTO local_absence_alerts(absence_id,source_identity,rule_key,title,expected_date,due_date,level,responsible_id,note,created_by,updated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (row['id'], owner, 'MANUAL_' + secrets.token_hex(12), title, due, due, level, responsible, note, user['id'], user['id'])).lastrowid
        _event(db, row['id'], owner, 'ALERT_CREATED', user['id'], alert_id, title=title, dueDate=due, responsibleId=responsible, note=note)
    elif action in ('complete_alert', 'postpone_alert', 'reopen_alert', 'cancel_alert', 'save_alert'):
        alert = db.execute('SELECT * FROM local_absence_alerts WHERE id=? AND absence_id=? AND source_identity=?', (int(payload.get('alertId') or 0), row['id'], owner)).fetchone()
        if not alert:
            raise LookupError('Providência não encontrada neste vínculo.')
        status = {'complete_alert': 'DONE', 'postpone_alert': 'POSTPONED', 'reopen_alert': 'PENDING', 'cancel_alert': 'CANCELLED'}.get(action, alert['status'])
        if action in ('complete_alert', 'postpone_alert') and alert['status'] not in ('PENDING', 'POSTPONED'):
            raise ValueError('Reabra a providência antes de concluir ou prorrogar novamente.')
        if action == 'reopen_alert' and alert['status'] not in ('DONE', 'CANCELLED'):
            raise ValueError('Esta providência já está aberta.')
        if action in ('postpone_alert', 'cancel_alert', 'reopen_alert') and len(note) < 5:
            raise ValueError('Registre uma justificativa com pelo menos 5 caracteres.')
        if action == 'postpone_alert' and (not due or due <= alert['due_date']):
            raise ValueError('A prorrogação precisa de um prazo posterior ao atual.')
        if action == 'reopen_alert' and not due:
            raise ValueError('Informe o novo prazo para reabrir a providência.')
        next_due = due or alert['due_date']
        next_responsible = responsible if 'responsibleId' in payload else alert['responsible_id']
        db.execute('UPDATE local_absence_alerts SET status=?,due_date=?,responsible_id=?,note=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (status, next_due, next_responsible, note or alert['note'], user['id'], alert['id']))
        _event(db, row['id'], owner, action.upper(), user['id'], alert['id'], before=alert['status'], status=status,
               previousDue=alert['due_date'], dueDate=next_due, responsibleId=next_responsible, note=note)
    elif action == 'link_document':
        link_document(db, row['id'], int(payload.get('documentId') or 0), user)
    elif action == 'confirm_receipt':
        if not _can(user, 'communications.send'):
            raise PermissionError('Seu perfil não pode registrar recebimento de comunicações.')
        delivery = db.execute("SELECT * FROM local_absence_deliveries WHERE id=? AND absence_id=? AND source_identity=? AND status='SENT'", (int(payload.get('deliveryId') or 0), row['id'], owner)).fetchone()
        if not delivery or len(note) < 5:
            raise ValueError('Escolha um envio concluído e informe a referência de recebimento.')
        db.execute('UPDATE local_absence_deliveries SET receipt_reference=?,receipt_by=?,receipt_at=CURRENT_TIMESTAMP WHERE id=?', (note[:500], user['id'], delivery['id']))
        _event(db, row['id'], owner, 'RECEIPT_CONFIRMED', user['id'], deliveryId=delivery['id'], reference=note[:500])
    elif action == 'resolve_delivery':
        if not _can(user, 'communications.send'):
            raise PermissionError('Seu perfil não pode conferir o resultado de comunicações.')
        delivery = db.execute("SELECT * FROM local_absence_deliveries WHERE id=? AND absence_id=? AND source_identity=? AND status IN ('SENDING','UNCERTAIN')", (int(payload.get('deliveryId') or 0), row['id'], owner)).fetchone()
        outcome = str(payload.get('outcome') or '')
        if not delivery or len(note) < 8 or outcome not in ('SENT', 'FAILED'):
            raise ValueError('Informe o resultado conferido e a evidência com pelo menos 8 caracteres.')
        if delivery['status'] == 'SENDING':
            elapsed = db.execute("SELECT (julianday('now')-julianday(?))*86400", (delivery['created_at'],)).fetchone()[0]
            if elapsed is None or elapsed < 180:
                raise ValueError('O envio ainda pode estar ativo. Aguarde antes de registrar a conferência.')
        db.execute('UPDATE local_absence_deliveries SET status=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE id=?', (outcome, 'Conferência manual: ' + note, delivery['id']))
        _event(db, row['id'], owner, 'DELIVERY_RESOLVED', user['id'], deliveryId=delivery['id'], status=outcome, reference=note)
    else:
        raise ValueError('Ação de acompanhamento inválida.')
    return {'ok': True, 'message': 'Acompanhamento registrado no histórico do afastamento.'}


def link_document(db, absence_id, document_id, user):
    if not _can(user, 'documents.upload'):
        raise PermissionError('Seu perfil não pode vincular documentos ao afastamento.')
    row, _, owner, _, _ = context(db, absence_id, user)
    from .registry_documents import resolve_owner, list_documents
    allowed = list_documents(db, resolve_owner(db, 'pessoas', row['employment_key'], user=user), user=user)
    if not any(int(document['id']) == int(document_id) for document in allowed):
        raise ValueError('O documento precisa pertencer ao prontuário deste vínculo.')
    cursor = db.execute('INSERT OR IGNORE INTO local_absence_documents(absence_id,source_identity,document_id,linked_by) VALUES(?,?,?,?)', (absence_id, owner, document_id, user['id']))
    if cursor.rowcount:
        _event(db, absence_id, owner, 'DOCUMENT_LINKED', user['id'], documentId=document_id)


def delivery_payload(db, absence_id, user, document_ids=None):
    if not _can(user, 'communications.send'):
        raise PermissionError('Seu perfil não pode enviar comunicações.')
    row, employee, owner, fingerprint, _ = context(db, absence_id, user)
    target = str(employee.get('email') or '').strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', target) or len(target) > 254:
        raise ValueError('O vínculo precisa ter um e-mail válido no cadastro antes do envio.')
    try:
        ids = list(dict.fromkeys(int(value) for value in (document_ids or [])))
    except (TypeError, ValueError):
        raise ValueError('Escolha documentos válidos do prontuário.')
    if len(ids) > 20:
        raise ValueError('Selecione no máximo 20 documentos.')
    from .registry_documents import list_documents, resolve_owner
    available = {int(value['id']): value for value in list_documents(db, resolve_owner(db, 'pessoas', row['employment_key'], user=user), user=user)} if ids else {}
    if any(value not in available for value in ids):
        raise ValueError('A seleção contém documento de outro prontuário ou indisponível.')
    return {'absence': row, 'employee': employee, 'owner': owner, 'snapshotHash': fingerprint,
            'target': target, 'documentIds': ids, 'documents': [available[value] for value in ids]}


def begin_delivery(db, absence_id, payload, user):
    context_data = delivery_payload(db, absence_id, user, payload.get('documentIds'))
    if str(payload.get('contextToken') or '') != context_data['snapshotHash']:
        raise ValueError('O afastamento mudou. Atualize a tela antes de enviar.')
    request_key = str(payload.get('requestKey') or '')
    subject = _text(payload.get('subject'), 180)
    message = str(payload.get('message') or '').strip()[:10000]
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,120}', request_key) or len(subject) < 3 or len(message) < 10:
        raise ValueError('Informe assunto, mensagem e identificação da tentativa válidos.')
    previous = db.execute('SELECT * FROM local_absence_deliveries WHERE request_key=?', (request_key,)).fetchone()
    if previous:
        if previous['absence_id'] != int(absence_id) or previous['source_identity'] != context_data['owner']:
            raise ValueError('A identificação desta tentativa já pertence a outra comunicação.')
        if previous['status'] == 'SENT':
            return {**context_data, 'alreadySent': True, 'deliveryId': previous['id'], 'protocol': previous['protocol']}
        raise ValueError('Esta tentativa já foi registrada. Confira o histórico antes de iniciar outra.')
    if db.execute("SELECT 1 FROM local_absence_deliveries WHERE absence_id=? AND source_identity=? AND status IN ('SENDING','UNCERTAIN')", (absence_id, context_data['owner'])).fetchone():
        raise ValueError('Há envio em andamento ou de resultado incerto. Confira o histórico antes de reenviar.')
    protocol = f'AF-{absence_id}-{secrets.token_hex(8).upper()}'
    cursor = db.execute('''INSERT INTO local_absence_deliveries(absence_id,source_identity,request_key,snapshot_hash,status,target,subject,message,documents_json,protocol,created_by)
        VALUES(?,?,?,?,'SENDING',?,?,?,?,?,?)''', (absence_id, context_data['owner'], request_key, context_data['snapshotHash'],
        context_data['target'], subject, message, _json(context_data['documentIds']), protocol, user['id']))
    _event(db, absence_id, context_data['owner'], 'EMAIL_STARTED', user['id'], deliveryId=cursor.lastrowid, protocol=protocol)
    return {**context_data, 'alreadySent': False, 'deliveryId': cursor.lastrowid, 'protocol': protocol,
            'subject': subject, 'message': message}


def finish_delivery(db, delivery_id, *, sent=False, uncertain=False, error='', actor_id=None):
    delivery = db.execute('SELECT * FROM local_absence_deliveries WHERE id=?', (delivery_id,)).fetchone()
    if not delivery:
        raise LookupError('Tentativa de comunicação não encontrada.')
    if delivery['status'] != 'SENDING':
        return
    status = 'SENT' if sent else 'UNCERTAIN' if uncertain else 'FAILED'
    db.execute('UPDATE local_absence_deliveries SET status=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE id=?', (status, _text(error, 500), delivery_id))
    _event(db, delivery['absence_id'], delivery['source_identity'], 'EMAIL_' + status, actor_id,
           deliveryId=delivery_id, protocol=delivery['protocol'], error=_text(error, 500))
