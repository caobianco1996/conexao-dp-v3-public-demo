"""Project confirmed SIB records into operational queues without extra Bridge reads."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, timedelta

from .integrations import _identity_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_operation_state (
 id INTEGER PRIMARY KEY CHECK(id=1), seeded INTEGER NOT NULL DEFAULT 0,
 checked INTEGER NOT NULL DEFAULT 0, day TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS local_sib_operation_jobs (
 source_id TEXT PRIMARY KEY, error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 next_due REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_operation_snapshots (
 source_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS sib_operations_jobs_due ON local_sib_operation_jobs(next_due,source_id);
CREATE TABLE IF NOT EXISTS local_sib_operation_history (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, previous_json TEXT, current_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS sib_operation_history_source ON local_sib_operation_history(source_id,id);
CREATE TABLE IF NOT EXISTS local_sib_contract_types (
 code TEXT PRIMARY KEY, label TEXT NOT NULL, confirmed_by INTEGER NOT NULL REFERENCES users(id),
 confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS sib_operations_insert AFTER INSERT ON local_sib_records WHEN NEW.dataset='employments'
 BEGIN INSERT INTO local_sib_operation_jobs(source_id) VALUES(NEW.source_id)
 ON CONFLICT(source_id) DO UPDATE SET error='',next_due=0,attempts=0,updated_at=CURRENT_TIMESTAMP; END;
CREATE TRIGGER IF NOT EXISTS sib_operations_update AFTER UPDATE OF data_json,raw_json ON local_sib_records
 WHEN NEW.dataset='employments' AND (OLD.data_json<>NEW.data_json OR OLD.raw_json<>NEW.raw_json)
 BEGIN INSERT INTO local_sib_operation_jobs(source_id) VALUES(NEW.source_id)
 ON CONFLICT(source_id) DO UPDATE SET error='',next_due=0,attempts=0,updated_at=CURRENT_TIMESTAMP; END;
CREATE TRIGGER IF NOT EXISTS sib_operations_delete AFTER DELETE ON local_sib_records WHEN OLD.dataset='employments'
 BEGIN INSERT INTO local_sib_operation_jobs(source_id) VALUES(OLD.source_id)
 ON CONFLICT(source_id) DO UPDATE SET error='',next_due=0,attempts=0; END;
CREATE TRIGGER IF NOT EXISTS sib_operations_company AFTER UPDATE OF data_json ON local_sib_records WHEN NEW.dataset='companies' AND OLD.data_json<>NEW.data_json
 BEGIN INSERT INTO local_sib_operation_jobs(source_id) SELECT source_id FROM local_sib_records WHERE dataset='employments' AND CAST(json_extract(data_json,'$.companyId') AS TEXT)=NEW.source_id
 ON CONFLICT(source_id) DO UPDATE SET error='',next_due=0,attempts=0; END;
CREATE TRIGGER IF NOT EXISTS sib_operations_origin AFTER UPDATE OF sib_base_url ON local_integration_settings WHEN OLD.sib_base_url<>NEW.sib_base_url
 BEGIN UPDATE local_rescission_cases SET source_review=1,source_message='Origem SIB alterada. Aguarde a conferência da identidade.' WHERE source_system='SIB';
 UPDATE local_temporary_contracts SET active=0,source_state='REVIEW',source_message='Origem SIB alterada.' WHERE source_system='SIB';
 INSERT INTO local_sib_operation_jobs(source_id) SELECT source_id FROM local_sib_records WHERE dataset='employments'
 ON CONFLICT(source_id) DO UPDATE SET error='',next_due=0,attempts=0; END;
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def migrate(db):
    """Retain IDs, child FKs and files while allowing SIB-backed cases/documents."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    replacements = []
    for table, column in (('local_rescission_cases', 'employment_id'), ('local_registry_documents', 'person_id')):
        if table not in tables:
            continue
        info = next(row for row in db.execute(f'PRAGMA table_info({table})') if row['name'] == column)
        if not info['notnull']:
            continue
        original = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        ddl = re.sub(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?["`\[]?' + table + r'["`\]]?', 'CREATE TABLE ' + table + '_source_upgrade', original, count=1, flags=re.I)
        ddl = re.sub(r'\b' + column + r'\s+INTEGER\s+NOT\s+NULL', column + ' INTEGER', ddl, count=1, flags=re.I)
        replacements.append((table, ddl))
    if replacements:
        previous_legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA legacy_alter_table=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            for table, ddl in replacements:
                db.execute(ddl)
                columns = ','.join('"' + row['name'] + '"' for row in db.execute(f'PRAGMA table_info({table})'))
                db.execute(f'INSERT INTO {table}_source_upgrade({columns}) SELECT {columns} FROM {table}')
                db.execute(f'DROP TABLE {table}')
                db.execute(f'ALTER TABLE {table}_source_upgrade RENAME TO {table}')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('A migração de operações encontrou referências inconsistentes e foi revertida.')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute(f'PRAGMA legacy_alter_table={int(previous_legacy)}')


def add_columns(db):
    changes = {
        'local_rescission_cases': {'source_system': "TEXT NOT NULL DEFAULT 'REGISTRY'", 'source_id': "TEXT NOT NULL DEFAULT ''", 'source_snapshot_json': "TEXT NOT NULL DEFAULT '{}'", 'source_review': 'INTEGER NOT NULL DEFAULT 0', 'source_message': "TEXT NOT NULL DEFAULT ''"},
        'local_registry_documents': {'source_person_key': "TEXT NOT NULL DEFAULT ''"},
        'local_temporary_contracts': {'source_state': "TEXT NOT NULL DEFAULT ''", 'source_message': "TEXT NOT NULL DEFAULT ''", 'proposed_due_date': "TEXT NOT NULL DEFAULT ''"},
    }
    for table, fields in changes.items():
        columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for name, ddl in fields.items():
            if name not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS rescission_sib_identity ON local_rescission_cases(source_system,source_id) WHERE source_system='SIB'")
    db.execute('CREATE INDEX IF NOT EXISTS registry_source_documents ON local_registry_documents(source_person_key,id)')
    db.execute("CREATE INDEX IF NOT EXISTS rescission_source_employment ON local_rescission_cases(json_extract(source_snapshot_json,'$.employmentKey')) WHERE source_system='SIB'")
    db.execute("CREATE INDEX IF NOT EXISTS sib_operation_identity ON local_sib_operation_snapshots(json_extract(snapshot_json,'$.identity'))")


def contract_type(db, data, raw):
    from .sib_record_mapping import employment_fields
    return employment_fields(data, raw, dict(db.execute('SELECT code,label FROM local_sib_contract_types')))['contractType']


def identity(db, source_id, admission):
    origin = db.execute('SELECT sib_base_url FROM local_integration_settings WHERE id=1').fetchone()
    scope = hashlib.sha256(str(origin[0] if origin else '').encode()).hexdigest()[:16]
    return f'{scope}:{source_id}:{admission}'


def _snapshot(db, row):
    data, raw = json.loads(row['data_json']), json.loads(row['raw_json'])
    parts = row['source_id'].split(':')
    if len(parts) != 3 or any(not part.isdigit() for part in parts) or str(data.get('registration') or '') != parts[-1]:
        raise ValueError('Identidade SIB sem empresa, filial e matrícula correspondentes.')
    admission, dismissal = _identity_date(data.get('admissionDate')), _identity_date(data.get('dismissalDate') or data.get('terminationDate'))
    if (not admission or data.get('activityDatesValid') is False
            or ((data.get('dismissalDate') or data.get('terminationDate')) and not dismissal)
            or (dismissal and dismissal < admission)):
        raise ValueError('Datas do vínculo inválidas ou anteriores à admissão. A fila anterior foi preservada.')
    company_id = str(data.get('companyId') or '')
    company_row = db.execute("SELECT data_json FROM local_sib_records WHERE dataset='companies' AND source_id=?", (company_id,)).fetchone()
    company = json.loads(company_row[0]) if company_row else {}
    kind = contract_type(db, data, raw)
    from .sib_record_mapping import employment_fields
    job_title = employment_fields(data, raw).get('jobTitle') or ''
    return {'employmentKey': 'sib:' + row['source_id'], 'identity': identity(db, row['source_id'], admission),
            'sourceState': 'VALID', 'person_key': 'sib:' + identity(db, row['source_id'], admission),
            'name': str(data.get('name') or ''), 'cpf': str(data.get('cpf') or ''), 'email': str(data.get('email') or ''),
            'whatsapp': str(data.get('phone') or ''), 'registration': parts[-1], 'company': str(company.get('name') or ('Empresa SIB ' + company_id)),
            'company_key': 'sib:' + company_id, 'unit': str(data.get('department') or ''), 'contract_type': kind,
            'admission_date': admission, 'termination_date': dismissal, 'first_due_date': _identity_date(data.get('firstDueDate')),
            'second_due_date': _identity_date(data.get('secondDueDate')), 'contract_code': str(raw['VIN_FUN']).strip() if raw.get('VIN_FUN') is not None else '',
            'dismissal_reason_code': str(data.get('dismissalReasonCode') or ''), 'dismissal_reason': str(data.get('dismissalReason') or ''),
            'worker_function': str(job_title or raw.get('DESCRICAO_FUNCAO') or ''),
            'work_location': str(raw.get('LOC_TRAB_FUN') or ''), 'complementary_demand': str(raw.get('DESC_MOT_ADM_FUN') or ''),
            'tomadora_name': str(company.get('name') or ''), 'tomadora_cnpj': str(company.get('cnpj') or ''),
            'tomadora_address': ', '.join(str(company.get(key) or '') for key in ('address', 'city', 'state') if company.get(key)),
            'source_updated_at': row['imported_at']}


def _mark_review(db, source_id, message):
    for case in db.execute("SELECT id,source_snapshot_json FROM local_rescission_cases WHERE source_system='SIB' AND json_extract(source_snapshot_json,'$.employmentKey')=?", ('sib:' + source_id,)).fetchall():
        data = json.loads(case['source_snapshot_json']); data['sourceState'] = 'REVIEW'
        db.execute('UPDATE local_rescission_cases SET source_review=1,source_message=?,source_snapshot_json=? WHERE id=?', (message, _json(data), case['id']))
    db.execute("UPDATE local_temporary_contracts SET active=0,source_state='REVIEW',source_message=? WHERE source_system='SIB' AND json_extract((SELECT snapshot_json FROM local_sib_operation_snapshots WHERE source_id=?),'$.identity')=source_id", (message, source_id))


def _sync_case(db, item, actor, today):
    from .rescissions import REQUIREMENTS, _event, _set_case_status
    existing = db.execute("SELECT * FROM local_rescission_cases WHERE source_system='SIB' AND source_id=?", (item['identity'],)).fetchone()
    if not item['termination_date'] or item['termination_date'] > today.isoformat():
        if existing:
            _mark_review(db, item['employmentKey'][4:], 'O SIB retirou ou alterou o desligamento para uma data futura. Confira antes de continuar.')
        return
    encoded = _json(item)
    deadline = (date.fromisoformat(item['termination_date']) + timedelta(days=10)).isoformat()
    if existing:
        if existing['source_snapshot_json'] == encoded:
            return
        previous = json.loads(existing['source_snapshot_json'])
        material = any(previous.get(key) != item.get(key) for key in ('cpf', 'name', 'company_key', 'admission_date', 'termination_date', 'dismissal_reason_code', 'dismissal_reason')) or bool(previous.get('contract_type') and previous['contract_type'] != item['contract_type'])
        review = bool(existing['source_review'] or material)
        db.execute("UPDATE local_rescission_cases SET dismissal_date=?,deadline_date=?,reason_code=?,reason_label=?,source_snapshot_json=?,source_review=?,source_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (item['termination_date'], deadline, item['dismissal_reason_code'], item['dismissal_reason'], encoded, int(review), 'O SIB alterou informações do desligamento. Confira os documentos antes de liberar o caso.' if review else '', existing['id']))
        if material:
            _event(db, existing['id'], 'SIB_DESLIGAMENTO_ALTERADO', actor, previous=previous, current=item)
        if 'TEMPOR' in item['contract_type'].upper():
            for code in ('EXAME_DEMISSIONAL','SEGURO_DESEMPREGO'):
                changed = db.execute("UPDATE local_rescission_requirements SET status='DISPENSADO',exception_reason='Dispensa automática conforme fluxo SIC para contrato temporário.',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND code=? AND status='PENDENTE'", (actor,existing['id'],code))
                if changed.rowcount:
                    _event(db,existing['id'],'SIB_DOCUMENTO_DISPENSADO_POR_TIPO',actor,code=code)
        return
    case_id = db.execute("""INSERT INTO local_rescission_cases(employment_id,dismissal_date,deadline_date,reason_code,reason_label,created_by,source_system,source_id,source_snapshot_json)
       VALUES(NULL,?,?,?,?,?,'SIB',?,?)""", (item['termination_date'], deadline, item['dismissal_reason_code'], item['dismissal_reason'], actor, item['identity'], encoded)).lastrowid
    for code, label, mandatory in REQUIREMENTS:
        optional = not mandatory
        exempt = 'TEMPOR' in item['contract_type'].upper() and code in ('EXAME_DEMISSIONAL', 'SEGURO_DESEMPREGO')
        reason = 'Documento interno do DP.' if optional else 'Dispensa automática conforme fluxo SIC para contrato temporário.' if exempt else ''
        db.execute("INSERT INTO local_rescission_requirements(case_id,code,label,mandatory,status,exception_reason,updated_by) VALUES(?,?,?,?,?,?,?)", (case_id, code, label, int(mandatory), 'DISPENSADO' if optional or exempt else 'PENDENTE', reason, actor))
    _set_case_status(db, case_id, actor)
    _event(db, case_id, 'SIB_RESCISAO_IDENTIFICADA', actor, dismissal_date=item['termination_date'], deadline_date=deadline, employment_key=item['employmentKey'])


def _sync_extension(db, item, today):
    key, first, second = item['identity'], item['first_due_date'], item['second_due_date']
    temporary = 'TEMPOR' in item['contract_type'].upper()
    active = bool(not item['termination_date'] and item['admission_date'] <= today.isoformat())
    state, message = 'READY', ''
    if not temporary:
        state, message = 'REVIEW', f"Tipo de contrato não confirmado como temporário (código SIB {item['contract_code'] or 'não informado'})."
        confirmed = db.execute('SELECT label FROM local_sib_contract_types WHERE code=?', (item['contract_code'],)).fetchone()
        if confirmed and confirmed[0] == item['contract_type']:
            active = False
            message = f"Vínculo {item['contract_type']} confirmado. Não integra a fila de contratos temporários."
    elif not first or first < item['admission_date'] or (second and second <= first):
        state, message = 'REVIEW', 'Vencimentos ausentes ou inconsistentes; confira a origem.'
    elif second:
        state, message = 'EXTENDED', 'O SIB já informa o segundo vencimento. Não abrir outra prorrogação.'
    elif first < today.isoformat():
        state, message = 'EXPIRED', 'Primeiro vencimento ultrapassado. O SIC bloqueia a preparação retroativa.'
    elif first > (today + timedelta(days=30)).isoformat():
        state, message = 'SCHEDULED', 'Entra na janela de preparação 30 dias antes do primeiro vencimento.'
    if item['termination_date']:
        state, message = 'TERMINATED', 'Desligamento informado na origem; não abrir nova prorrogação.'
    if not first and not temporary:
        db.execute("UPDATE local_temporary_contracts SET active=0,source_state='REVIEW',source_message='O SIB não confirma mais o tipo temporário e o primeiro vencimento.' WHERE source_system='SIB' AND source_id=?", (key,))
        return
    proposed = (date.fromisoformat(first) + timedelta(days=90)).isoformat() if first and state in ('READY', 'SCHEDULED', 'EXPIRED') else ''
    # Historical requests remain attached to their original first-expiration key.
    db.execute("""INSERT INTO local_temporary_contracts(source_system,source_id,person_name,registration,company_key,company_name,contract_type,active,admission_date,first_due_date,second_due_date,cpf,email,whatsapp,source_updated_at,source_state,source_message,proposed_due_date,tomadora_name,tomadora_cnpj,tomadora_address,worker_function,work_location,complementary_demand)
       VALUES('SIB',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(source_system,source_id) DO UPDATE SET person_name=excluded.person_name,company_name=excluded.company_name,
       contract_type=excluded.contract_type,active=excluded.active,first_due_date=excluded.first_due_date,second_due_date=excluded.second_due_date,
       cpf=excluded.cpf,email=excluded.email,whatsapp=excluded.whatsapp,source_updated_at=excluded.source_updated_at,
       source_state=excluded.source_state,source_message=excluded.source_message,proposed_due_date=excluded.proposed_due_date,
       tomadora_name=excluded.tomadora_name,tomadora_cnpj=excluded.tomadora_cnpj,tomadora_address=excluded.tomadora_address,
       worker_function=excluded.worker_function,work_location=excluded.work_location,complementary_demand=excluded.complementary_demand""",
       (key, item['name'], item['registration'], item['company_key'], item['company'], item['contract_type'], int(active), item['admission_date'], first, second,
        item['cpf'], item['email'], item['whatsapp'], item['source_updated_at'], state, message, proposed,
        item['tomadora_name'], item['tomadora_cnpj'], item['tomadora_address'], item['worker_function'], item['work_location'], item['complementary_demand']))


def run_due(database, *, batch_size=500, today=None):
    from .database import connect
    today = today or date.today()
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        actor = db.execute("SELECT id FROM users WHERE active=1 AND role='ADMIN' ORDER BY id LIMIT 1").fetchone()
        if not actor:
            return 0
        db.execute('INSERT OR IGNORE INTO local_sib_operation_state(id) VALUES(1)')
        if not db.execute('SELECT seeded FROM local_sib_operation_state WHERE id=1').fetchone()[0]:
            db.execute("INSERT OR IGNORE INTO local_sib_operation_jobs(source_id) SELECT source_id FROM local_sib_records WHERE dataset='employments'")
            db.execute('UPDATE local_sib_operation_state SET seeded=1 WHERE id=1')
        if db.execute('SELECT day FROM local_sib_operation_state WHERE id=1').fetchone()[0] != today.isoformat():
            db.execute("INSERT OR IGNORE INTO local_sib_operation_jobs(source_id) SELECT source_id FROM local_sib_records WHERE dataset='employments' AND (COALESCE(json_extract(data_json,'$.dismissalDate'),'')='' OR json_extract(data_json,'$.dismissalDate')>=?)", ((today-timedelta(days=1)).isoformat(),))
            db.execute('UPDATE local_sib_operation_state SET day=? WHERE id=1', (today.isoformat(),))
        jobs = db.execute("""SELECT j.* FROM local_sib_operation_jobs j LEFT JOIN local_sib_records r ON r.dataset='employments' AND r.source_id=j.source_id
          WHERE j.next_due<=? ORDER BY j.next_due,CASE WHEN COALESCE(json_extract(r.data_json,'$.dismissalDate'),'')='' THEN 0 ELSE 1 END,
          json_extract(r.data_json,'$.dismissalDate') DESC,j.source_id LIMIT ?""", (time.time(), batch_size)).fetchall()
        processed, deadline = 0, time.monotonic() + 2
        for job in jobs:
            if processed and time.monotonic() >= deadline:
                break
            processed += 1
            source_id = job['source_id']
            row = db.execute("SELECT * FROM local_sib_records WHERE dataset='employments' AND source_id=?", (source_id,)).fetchone()
            try:
                if not row:
                    raise ValueError('Ficha não disponível na origem local. Histórico preservado para conferência.')
                from .sib_presence import source as presence_source
                presence = db.execute('SELECT status,cancellation FROM local_sib_presence WHERE source_url=? AND source_id=?', (presence_source(db), source_id)).fetchone()
                if presence and (presence['status'] in ('EXCLUDED', 'DIVERGENT') or presence['cancellation']):
                    message = ('Contrato cancelado antes do início.' if presence['cancellation'] else
                        'Identidade divergente no SIB. Confira antes de continuar.' if presence['status'] == 'DIVERGENT' else
                        'Cadastro excluído no SIB. Histórico preservado para conferência.')
                    _mark_review(db, source_id, message)
                    snapshot = db.execute('SELECT snapshot_json FROM local_sib_operation_snapshots WHERE source_id=?', (source_id,)).fetchone()
                    if snapshot:
                        db.execute("UPDATE local_temporary_contracts SET active=0,source_state='REVIEW',source_message=? WHERE source_system='SIB' AND source_id=?", (message, json.loads(snapshot[0])['identity']))
                    db.execute('DELETE FROM local_sib_operation_jobs WHERE source_id=?', (source_id,))
                    continue
                item = _snapshot(db, row)
                # Refresh clock-based eligibility even when the source did not change.
                encoded = _json(item)
                digest = hashlib.sha256(_json({key: value for key, value in item.items() if key != 'source_updated_at'}).encode()).hexdigest()
                previous = db.execute('SELECT * FROM local_sib_operation_snapshots WHERE source_id=?', (source_id,)).fetchone()
                if previous and json.loads(previous['snapshot_json'])['identity'] != item['identity']:
                    _mark_review(db, source_id, 'A origem reutilizou a identificação para outra admissão. O vínculo anterior foi preservado.')
                    db.execute("UPDATE local_temporary_contracts SET active=0,source_state='REVIEW',source_message='Outra admissão identificada na origem.' WHERE source_system='SIB' AND source_id=?", (json.loads(previous['snapshot_json'])['identity'],))
                _sync_case(db, item, actor[0], today)
                _sync_extension(db, item, today)
                if not previous or previous['content_hash'] != digest:
                    db.execute('INSERT INTO local_sib_operation_history(source_id,previous_json,current_json) VALUES(?,?,?)', (source_id, previous['snapshot_json'] if previous else None, encoded))
                    db.execute("INSERT INTO local_sib_operation_snapshots(source_id,content_hash,snapshot_json) VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET content_hash=excluded.content_hash,snapshot_json=excluded.snapshot_json,updated_at=CURRENT_TIMESTAMP", (source_id, digest, encoded))
                db.execute('DELETE FROM local_sib_operation_jobs WHERE source_id=?', (source_id,))
            except ValueError as error:
                message = str(error)[:260]
                _mark_review(db, source_id, message)
                db.execute('UPDATE local_sib_operation_jobs SET error=?,attempts=attempts+1,next_due=? WHERE source_id=?', (message, time.time()+120, source_id))
        db.execute("UPDATE local_sib_operation_state SET checked=checked+?,updated_at=CURRENT_TIMESTAMP,error='' WHERE id=1", (processed,))
        return processed


def summary(db):
    row = db.execute('SELECT * FROM local_sib_operation_state WHERE id=1').fetchone()
    return {'pending': db.execute("SELECT COUNT(*) FROM local_sib_operation_jobs WHERE error='' ").fetchone()[0],
            'review': db.execute("SELECT COUNT(*) FROM local_sib_operation_jobs WHERE error<>''").fetchone()[0],
            'processed': db.execute('SELECT COUNT(*) FROM local_sib_operation_snapshots').fetchone()[0],
            'updatedAt': row['updated_at'] if row else '',
            'rescissions': db.execute("SELECT COUNT(*) FROM local_rescission_cases WHERE source_system='SIB'").fetchone()[0],
            'extensions': db.execute("SELECT COUNT(*) FROM local_temporary_contracts WHERE source_system='SIB' AND active=1").fetchone()[0]}
