"""Private, manually curated documents shared by registry and DP workflows.

SIB files belong to an exact source employment and admission. Source data stays
read-only; archiving a local file never removes the original or its audit trail.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = {
    'OUTRO': 'Outro documento', 'CONTRATO': 'Contrato de trabalho',
    'FICHA_CADASTRAL': 'Ficha cadastral', 'IDENTIFICACAO': 'Identificação',
    'COMPROVANTE': 'Comprovante', 'RESCISAO': 'Rescisão',
    'AFASTAMENTO': 'Afastamento', 'PRORROGACAO': 'Prorrogação contratual',
    'EMPRESA': 'Documento da empresa',
}


def migrate(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(local_registry_documents)')}
    additions = {
        'source_person_key': "TEXT NOT NULL DEFAULT ''", 'owner_key': "TEXT NOT NULL DEFAULT ''",
        'company_key': "TEXT NOT NULL DEFAULT ''", 'subject_view': "TEXT NOT NULL DEFAULT ''",
        'subject_key': "TEXT NOT NULL DEFAULT ''", 'document_type': "TEXT NOT NULL DEFAULT 'OUTRO'",
        'description': "TEXT NOT NULL DEFAULT ''", 'document_date': "TEXT NOT NULL DEFAULT ''",
        'origin': "TEXT NOT NULL DEFAULT 'RESCISAO'", 'archived_at': "TEXT NOT NULL DEFAULT ''",
        'archived_by': 'INTEGER REFERENCES users(id)', 'updated_at': "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f'ALTER TABLE local_registry_documents ADD COLUMN {name} {definition}')
    db.execute("""UPDATE local_registry_documents SET owner_key=CASE
        WHEN source_person_key<>'' THEN source_person_key WHEN person_id IS NOT NULL THEN 'm:'||person_id ELSE '' END
        WHERE owner_key=''""")
    db.execute("CREATE INDEX IF NOT EXISTS registry_documents_owner ON local_registry_documents(owner_key,archived_at,id)")
    db.execute("CREATE INDEX IF NOT EXISTS registry_documents_digest ON local_registry_documents(owner_key,sha256)")
    db.execute("""CREATE TABLE IF NOT EXISTS local_registry_document_events (
        id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES local_registry_documents(id),
        action TEXT NOT NULL, actor_id INTEGER REFERENCES users(id), details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")


def _permission(user, name):
    if user is not None:
        from .permissions import can
        if not can(user, name):
            raise PermissionError('Seu perfil não permite esta ação no prontuário.')


def _subject(db, user, view, key):
    if user is not None:
        from .permissions import require_subject
        require_subject(db, user, view, key)


def resolve_owner(db, view, key, user=None):
    """Resolve a verified registry key, never an owner supplied by the browser."""
    _permission(user, 'documents.view')
    view, key = str(view or ''), str(key or '')[:200]
    if view not in ('pessoas', 'vinculos', 'empresas'):
        raise ValueError('Abra uma pessoa, um vínculo ou uma empresa para acessar o prontuário.')
    _subject(db, user, view, key)
    base = {'personId': None, 'sourcePersonKey': '', 'companyKey': '', 'subjectView': view, 'subjectKey': key}
    if view == 'empresas':
        from .companies import catalog
        company = next((item for item in catalog(db) if item['key'] == key), None)
        if not company:
            raise LookupError('Empresa não encontrada.')
        owner = 'company:' + key
        if key.startswith('sib:'):
            from .sib_operations import identity
            owner = 'company:sib:' + identity(db, key[4:], '')
        return {**base, 'ownerKey': owner, 'companyKey': key, 'label': company['name']}
    if key.startswith('sib:') and not key.startswith('sib:p:'):
        row = db.execute("SELECT data_json FROM local_sib_records WHERE dataset='employments' AND source_id=?", (key[4:],)).fetchone()
        if not row:
            raise LookupError('Vínculo SIB não encontrado.')
        data = json.loads(row[0])
        admission = str(data.get('admissionDate') or '')[:10]
        try:
            date.fromisoformat(admission)
        except ValueError:
            raise ValueError('O vínculo precisa ter admissão confirmada para guardar arquivos sem misturar recontratações.') from None
        from .sib_operations import identity
        owner = 'sib:' + identity(db, key[4:], admission)
        return {**base, 'ownerKey': owner, 'sourcePersonKey': owner,
                'companyKey': 'sib:' + str(data.get('companyId') or ''),
                'label': str(data.get('name') or 'Cadastro SIB'), 'admissionDate': admission}
    if key.startswith('local:') and key[6:].isdigit():
        row = db.execute('SELECT person_key FROM registry_employments WHERE id=?', (int(key[6:]),)).fetchone()
        if not row:
            raise LookupError('Vínculo local não encontrado.')
        person = resolve_owner(db, 'pessoas', row[0], user=user)
        return {**person, 'subjectView': view, 'subjectKey': key}
    if key.startswith('m:') and key[2:].isdigit():
        _subject(db, user, 'pessoas', key)
        row = db.execute('SELECT id,name FROM registry_people WHERE id=?', (int(key[2:]),)).fetchone()
        if not row:
            raise LookupError('Pessoa não encontrada.')
        return {**base, 'ownerKey': key, 'personId': row['id'], 'label': row['name']}
    # Historical V2 people retain a distinct owner. SIB person groups must use
    # the selected matrícula so files never cross source identities/admissions.
    from .registry import project
    people, employments, _ = project(db)
    if key.startswith('sib:p:'):
        raise ValueError('Abra a matrícula desejada na aba Pessoas para anexar o documento ao vínculo correto.')
    if key.startswith('v:'):
        link = next((item for item in employments if item['key'] == key), None)
        if link:
            key = link['person_key']
    person = people.get(key)
    if not person:
        raise LookupError('Pessoa não encontrada.')
    _subject(db, user, 'pessoas', key)
    return {**base, 'ownerKey': key, 'sourcePersonKey': key, 'label': person['name']}


def _owner_clause(owner):
    key = str(owner['ownerKey'])
    # Existing operational uploads may be inserted without the new owner_key.
    if owner.get('personId'):
        return '(owner_key=? OR (owner_key=\'\' AND person_id=?))', [key, int(owner['personId'])]
    return '(owner_key=? OR (owner_key=\'\' AND source_person_key=?))', [key, key]


def _public(row):
    item = dict(row)
    return {key: item.get(key) for key in ('id', 'filename', 'mime_type', 'size_bytes', 'sha256',
            'document_type', 'description', 'document_date', 'origin', 'created_at', 'updated_at', 'archived_at')} | {
        'documentTypeLabel': DOCUMENT_TYPES.get(item.get('document_type'), item.get('document_type') or 'Outro documento'),
        'downloadUrl': f"/api/ui/person-documents/{item['id']}",
    }


def list_documents(db, owner, user=None, include_archived=False):
    _permission(user, 'documents.view')
    _subject(db, user, owner['subjectView'], owner['subjectKey'])
    where, args = _owner_clause(owner)
    if not include_archived:
        where += " AND archived_at=''"
    rows = db.execute(f'SELECT * FROM local_registry_documents WHERE {where} ORDER BY id DESC', args)
    return [_public(row) for row in rows]


def document_history(db, owner, user=None):
    _permission(user, 'documents.view')
    _subject(db, user, owner['subjectView'], owner['subjectKey'])
    where, args = _owner_clause(owner)
    rows = db.execute(f"""SELECT e.action,e.details_json,e.created_at,u.name actor,d.filename
        FROM local_registry_document_events e JOIN local_registry_documents d ON d.id=e.document_id
        LEFT JOIN users u ON u.id=e.actor_id WHERE d.id IN
        (SELECT id FROM local_registry_documents WHERE {where}) ORDER BY e.id DESC LIMIT 30""", args)
    return [{'action': row['action'], 'createdAt': row['created_at'], 'actor': row['actor'] or 'Sistema',
             'filename': row['filename'], 'details': json.loads(row['details_json'])} for row in rows]


def _metadata(metadata):
    data = dict(metadata or {})
    kind = str(data.get('documentType') or 'OUTRO').strip().upper()
    if kind not in DOCUMENT_TYPES:
        raise ValueError('Selecione um tipo de documento válido.')
    description = ' '.join(str(data.get('description') or '').split())[:500]
    document_date = str(data.get('documentDate') or '')[:10]
    if document_date:
        try:
            date.fromisoformat(document_date)
        except ValueError:
            raise ValueError('Informe uma data válida para o documento.') from None
    origin = str(data.get('origin') or 'MANUAL').strip().upper()[:40]
    return kind, description, document_date, origin


def _event(db, document_id, action, actor_id, **details):
    db.execute('INSERT INTO local_registry_document_events(document_id,action,actor_id,details_json) VALUES(?,?,?,?)',
               (document_id, action, actor_id, json.dumps(details, ensure_ascii=False)))


def add_document(db, owner, actor_id, filename, mime_type, raw, storage_dir, metadata=None):
    """Internal workflow entry point: caller owns authorization and transaction."""
    if not owner.get('ownerKey') or not owner.get('subjectKey') or not owner.get('subjectView'):
        raise ValueError('Proprietário do documento não foi confirmado.')
    from .rescissions import _validate_document
    name, mime = _validate_document(filename, mime_type, raw)
    kind, description, document_date, origin = _metadata(metadata)
    digest = hashlib.sha256(raw).hexdigest()
    # Take a write lock before the duplicate read so concurrent requests cannot
    # both create a physical copy. The caller commits or rolls back the batch.
    db.execute('UPDATE local_registry_documents SET id=id WHERE id=-1')
    where, args = _owner_clause(owner)
    existing = db.execute(f'SELECT * FROM local_registry_documents WHERE {where} AND sha256=? ORDER BY id LIMIT 1', [*args, digest]).fetchone()
    if existing:
        if existing['archived_at']:
            db.execute("UPDATE local_registry_documents SET archived_at='',archived_by=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (existing['id'],))
            _event(db, existing['id'], 'RESTAURADO_POR_REIMPORTACAO', actor_id)
        result = _public(db.execute('SELECT * FROM local_registry_documents WHERE id=?', (existing['id'],)).fetchone())
        return {**result, 'created': False, 'message': 'Este arquivo já pertence ao prontuário e foi reutilizado.'}
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_key = secrets.token_hex(24)
    target = storage_dir / storage_key
    try:
        with target.open('xb') as output:
            output.write(raw)
        cursor = db.execute("""INSERT INTO local_registry_documents
            (person_id,source_person_key,owner_key,company_key,subject_view,subject_key,storage_key,filename,mime_type,size_bytes,
             sha256,uploaded_by,document_type,description,document_date,origin,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (owner.get('personId'), owner.get('sourcePersonKey') or '', owner['ownerKey'], owner.get('companyKey') or '',
             owner['subjectView'], owner['subjectKey'], storage_key, name, mime, len(raw), digest, actor_id,
             kind, description, document_date, origin))
        document_id = int(cursor.lastrowid)
        _event(db, document_id, 'ANEXADO', actor_id, filename=name, sha256=digest, origin=origin)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {**_public(db.execute('SELECT * FROM local_registry_documents WHERE id=?', (document_id,)).fetchone()),
            'created': True, 'message': 'Documento salvo no prontuário.'}


def upload(db, view, key, actor_id, filename, mime_type, raw, storage_dir, metadata=None, user=None):
    _permission(user, 'documents.upload')
    owner = resolve_owner(db, view, key, user=user)
    return add_document(db, owner, actor_id, filename, mime_type, raw, storage_dir, {**(metadata or {}), 'origin': 'MANUAL'})


def _authorize_document(db, row, user):
    _permission(user, 'documents.view')
    if user is None:
        return
    from .permissions import allowed_company, scope
    if scope(user) != 'ALL':
        company_key = row['company_key']
        if not company_key:
            historical = db.execute("""SELECT json_extract(c.source_snapshot_json,'$.company_key')
                FROM local_rescission_requirements q JOIN local_rescission_cases c ON c.id=q.case_id
                WHERE q.registry_document_id=? AND c.source_system='SIB' LIMIT 1""", (row['id'],)).fetchone()
            company_key = historical[0] if historical else ''
        if company_key and not allowed_company(db, user, company_key=company_key):
            raise PermissionError('O prontuário deste documento está fora do seu recorte de acesso.')
    owner_key = str(row['owner_key'] or row['source_person_key'] or '')
    if owner_key.startswith('sib:'):
        parts = owner_key.split(':')
        if len(parts) == 6:
            key = 'sib:' + ':'.join(parts[2:5])
            current = db.execute("SELECT data_json FROM local_sib_records WHERE dataset='employments' AND source_id=?", (key[4:],)).fetchone()
            from .permissions import scope
            from .sib_operations import identity
            if not current or 'sib:' + identity(db, key[4:], str(json.loads(current[0]).get('admissionDate') or '')) != owner_key:
                if scope(user) != 'ALL':
                    raise PermissionError('O vínculo atual é diferente do proprietário histórico deste documento. Solicite a conferência de uma conta com acesso completo.')
                return
    if row['subject_view'] and row['subject_key']:
        view = 'pessoas' if str(row['owner_key'] or '').startswith(('m:', 'p:')) else row['subject_view']
        key = row['owner_key'] if view == 'pessoas' and str(row['owner_key'] or '').startswith(('m:', 'p:')) else row['subject_key']
        _subject(db, user, view, key)
        return
    if row['person_id']:
        _subject(db, user, 'pessoas', f"m:{row['person_id']}")
        return
    # Legacy source documents have an exact identity in source_person_key.
    source = str(row['source_person_key'] or '')
    if source.startswith('sib:'):
        parts = source.split(':')
        if len(parts) == 6:
            key = 'sib:' + ':'.join(parts[2:5])
            _subject(db, user, 'pessoas', key)
            return
    raise PermissionError('A identidade deste documento precisa ser conferida antes do acesso.')


def get_document(db, document_id, storage_dir, user=None):
    row = db.execute('SELECT * FROM local_registry_documents WHERE id=?', (int(document_id),)).fetchone()
    if not row:
        raise LookupError('Documento do prontuário não encontrado.')
    _authorize_document(db, row, user)
    root = Path(storage_dir).resolve()
    target = (root / row['storage_key']).resolve()
    if target.parent != root or not target.is_file():
        raise LookupError('Arquivo do documento indisponível.')
    return dict(row), target


def mutate(db, payload, actor_id, user=None):
    _permission(user, 'documents.upload')
    row = db.execute('SELECT * FROM local_registry_documents WHERE id=?', (int(payload.get('documentId') or 0),)).fetchone()
    if not row:
        raise LookupError('Documento do prontuário não encontrado.')
    _authorize_document(db, row, user)
    action = str(payload.get('action') or '')
    if action in ('archive', 'restore'):
        reason = ' '.join(str(payload.get('reason') or '').split())[:500]
        if action == 'archive' and len(reason) < 8:
            raise ValueError('Informe uma justificativa de pelo menos 8 caracteres para arquivar.')
        db.execute("UPDATE local_registry_documents SET archived_at=CASE WHEN ?='archive' THEN CURRENT_TIMESTAMP ELSE '' END,archived_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (action, actor_id if action == 'archive' else None, row['id']))
        _event(db, row['id'], 'ARQUIVADO' if action == 'archive' else 'RESTAURADO', actor_id, reason=reason)
    elif action == 'metadata':
        kind, description, document_date, _ = _metadata(payload)
        db.execute('UPDATE local_registry_documents SET document_type=?,description=?,document_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (kind, description, document_date, row['id']))
        _event(db, row['id'], 'METADADOS_ALTERADOS', actor_id, previous={key: row[key] for key in ('document_type', 'description', 'document_date')}, documentType=kind, description=description, documentDate=document_date)
    else:
        raise ValueError('Ação de documento não reconhecida.')
    return {'ok': True, 'document': _public(db.execute('SELECT * FROM local_registry_documents WHERE id=?', (row['id'],)).fetchone()), 'message': 'Alteração registrada no prontuário.'}
