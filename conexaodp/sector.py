"""Sector reference library, permanent responsibilities and protected access data.

Import helpers are transactional: callers own commit/rollback and provenance.
They preserve manual responsibility overrides when the same source is imported
again. This module never creates cases, calendar occurrences or user accounts.
"""
from __future__ import annotations

import base64
import json
import re
import unicodedata
from urllib.parse import urlencode

from . import integration_secrets, permissions

PAGE_SIZE = 25
KINDS = ('ACTIVITY', 'REFERENCE', 'HISTORY')
SECRET_PREFIX = 'dpapi:sector:v1:'
MAX_SECRET_BYTES = 32 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sector_sources (
 id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
 label TEXT NOT NULL, filename TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '',
 metadata_json TEXT NOT NULL DEFAULT '{}', created_by INTEGER REFERENCES users(id),
 imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sector_items (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES local_sector_sources(id),
 source_key TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('ACTIVITY','REFERENCE','HISTORY')),
 title TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', source_sheet TEXT NOT NULL DEFAULT '',
 source_range TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}',
 encrypted_payload TEXT NOT NULL DEFAULT '',
 manual_json TEXT NOT NULL DEFAULT '{}', sensitive INTEGER NOT NULL DEFAULT 0 CHECK(sensitive IN (0,1)),
 company_key TEXT NOT NULL DEFAULT '', person_key TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id), updated_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_id,source_key)
);
CREATE INDEX IF NOT EXISTS local_sector_items_kind ON local_sector_items(source_id,kind,category,id);
CREATE TABLE IF NOT EXISTS local_sector_credentials (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES local_sector_sources(id),
 source_key TEXT NOT NULL, label TEXT NOT NULL, source_sheet TEXT NOT NULL DEFAULT '',
 source_range TEXT NOT NULL DEFAULT '', encrypted_payload TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id), updated_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_id,source_key)
);
CREATE TABLE IF NOT EXISTS local_sector_credential_audit (
 id INTEGER PRIMARY KEY, credential_id INTEGER NOT NULL REFERENCES local_sector_credentials(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sector_item_events (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES local_sector_items(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _get(value, key, default=None):
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return default


def _authenticated(user):
    if not user or not _get(user, 'id') or _get(user, 'active', 1) in (0, False):
        raise PermissionError('Entre na V3 para consultar os dados do setor.')
    if (_get(user, 'role') not in ('ADMIN', 'GESTOR')
            and not permissions.can(user, 'registry.manage')):
        raise PermissionError('Seu perfil não permite consultar a organização do setor.')


def _admin(user):
    _authenticated(user)
    if _get(user, 'role') != 'ADMIN':
        raise PermissionError('Somente administradores podem consultar o cofre de acessos.')


def _can_manage(user):
    return _get(user, 'role') in ('ADMIN', 'GESTOR')


def _can_sensitive(user):
    return _get(user, 'role') == 'ADMIN' or permissions.can(user, 'sensitive.view')


def _text(value, maximum, *, required=False):
    value = str(value or '').strip()
    if (required and not value) or len(value) > maximum or '\0' in value:
        raise ValueError('Confira os campos informados para o cadastro do setor.')
    return value


def _identifier(value):
    if isinstance(value, bool) or not str(value or '').isdigit() or len(str(value)) > 18 or int(value) < 1:
        raise ValueError('Selecione um registro válido do setor.')
    return int(value)


def protect_bytes(value: bytes) -> str:
    """Protect a document/media payload with the current Windows user's DPAPI."""
    if not isinstance(value, bytes) or len(value) > MAX_SECRET_BYTES:
        raise ValueError('O conteúdo protegido deve ter até 32 MB.')
    try:
        encrypted = integration_secrets._transform(value, decrypt=False)
    except Exception:
        raise ValueError('O Windows não conseguiu proteger o conteúdo. Nada foi salvo em texto aberto.') from None
    return SECRET_PREFIX + base64.b64encode(encrypted).decode('ascii')


def unprotect_bytes(ciphertext: str) -> bytes:
    try:
        if not isinstance(ciphertext, str) or not ciphertext.startswith(SECRET_PREFIX):
            raise ValueError()
        raw = base64.b64decode(ciphertext[len(SECRET_PREFIX):], validate=True)
        if len(raw) > MAX_SECRET_BYTES + 65536:
            raise ValueError()
        result = integration_secrets._transform(raw, decrypt=True)
        if len(result) > MAX_SECRET_BYTES:
            raise ValueError()
        return result
    except Exception:
        raise ValueError('Não foi possível abrir o conteúdo protegido neste Windows.') from None


def protect_payload(payload) -> str:
    try:
        raw = _json(payload).encode('utf-8')
    except (TypeError, ValueError):
        raise ValueError('O conteúdo a proteger está em um formato inválido.') from None
    return protect_bytes(raw)


def unprotect_payload(ciphertext: str):
    try:
        return json.loads(unprotect_bytes(ciphertext).decode('utf-8'))
    except (UnicodeError, TypeError, ValueError):
        raise ValueError('Não foi possível abrir os dados protegidos do setor.') from None


def upsert_source(db, source_key, *, label, filename='', sha256='', metadata=None, actor_id=None):
    key, label = _text(source_key, 300, required=True), _text(label, 240, required=True)
    filename, digest = _text(filename, 300), _text(sha256, 128)
    metadata_json = _json(metadata or {})
    db.execute('''INSERT INTO local_sector_sources(source_key,label,filename,sha256,metadata_json,created_by)
        VALUES(?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET label=excluded.label,
        filename=excluded.filename,sha256=excluded.sha256,metadata_json=excluded.metadata_json,
        imported_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP''',
        (key, label, filename, digest, metadata_json, actor_id))
    return db.execute('SELECT id FROM local_sector_sources WHERE source_key=?', (key,)).fetchone()[0]


def _medical(payload):
    """Importers should mark sensitive sources explicitly; known medical fields
    also guard against an accidental missing import flag.
    """
    if isinstance(payload, dict):
        if payload.get('sensitive') is True or payload.get('medical') is True:
            return True
        medical_headers = {'cid', 'diagnostico', 'diagnostico medico', 'doenca', 'tratamento',
                           'motivo do afastamento', 'motivo afastamento', 'laudo medico'}
        if any(_norm(key) in medical_headers for key in payload):
            return True
        columns = payload.get('columns') or []
        if isinstance(columns, list) and any(_norm(column) in medical_headers for column in columns if isinstance(column, str)):
            return True
        return any(_medical(value) for value in payload.values() if isinstance(value, (dict, list)))
    return isinstance(payload, list) and any(_medical(value) for value in payload if isinstance(value, (dict, list)))


def upsert_item(db, source_id, source_key, *, kind, title, category='', source_sheet='', source_range='',
                payload=None, sensitive=False, company_key='', person_key='', actor_id=None):
    if kind not in KINDS or not isinstance(payload or {}, dict):
        raise ValueError('Selecione atividade, referência ou histórico com conteúdo válido.')
    key, title = _text(source_key, 400, required=True), _text(title, 300, required=True)
    payload = payload or {}
    protected = bool(sensitive or kind == 'HISTORY' or _medical(payload))
    encrypted = protect_payload(payload) if protected else ''
    db.execute('''INSERT INTO local_sector_items(source_id,source_key,kind,title,category,source_sheet,
        source_range,payload_json,encrypted_payload,sensitive,company_key,person_key,created_by)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,source_key) DO UPDATE SET
        kind=excluded.kind,title=excluded.title,category=excluded.category,source_sheet=excluded.source_sheet,
        source_range=excluded.source_range,payload_json=excluded.payload_json,
        encrypted_payload=excluded.encrypted_payload,sensitive=excluded.sensitive,
        company_key=excluded.company_key,person_key=excluded.person_key,updated_at=CURRENT_TIMESTAMP''',
        (source_id, key, kind, title, _text(category, 150), _text(source_sheet, 180),
         _text(source_range, 120), '{}' if protected else _json(payload), encrypted, int(protected),
         _text(company_key, 180), _text(person_key, 180), actor_id))
    return db.execute('SELECT id FROM local_sector_items WHERE source_id=? AND source_key=?',
                      (source_id, key)).fetchone()[0]


def upsert_credential(db, source_id, source_key, *, label, payload, source_sheet='', source_range='', actor_id=None):
    if not isinstance(payload, dict):
        raise ValueError('Os dados do acesso devem estar organizados em campos.')
    encrypted = protect_payload(payload)
    key, label = _text(source_key, 400, required=True), _text(label, 240, required=True)
    db.execute('''INSERT INTO local_sector_credentials(source_id,source_key,label,source_sheet,source_range,
        encrypted_payload,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,source_key) DO UPDATE SET label=excluded.label,
        source_sheet=excluded.source_sheet,source_range=excluded.source_range,
        encrypted_payload=excluded.encrypted_payload,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
        (source_id, key, label, _text(source_sheet, 180), _text(source_range, 120), encrypted, actor_id, actor_id))
    return db.execute('SELECT id FROM local_sector_credentials WHERE source_id=? AND source_key=?',
                      (source_id, key)).fetchone()[0]


def _norm(value):
    value = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ' '.join(''.join(ch for ch in value if not unicodedata.combining(ch)).split())


def _param(params, key, default=''):
    value = (params or {}).get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    return str(value or default)[:300]


def _visible(db, row, user):
    if row['kind'] == 'HISTORY' or row['sensitive']:
        if not _can_sensitive(user):
            return False
        # A scoped account cannot establish whether an unmatched history belongs
        # to its population. A sensitive-data grant alone cannot remove scope.
        if (not row['company_key'] and not row['person_key']
                and _get(user, 'role') != 'ADMIN' and permissions.scope(user) != 'ALL'):
            return False
    if row['company_key'] and not permissions.allowed_company(db, user, company_key=row['company_key']):
        return False
    if row['person_key'] and not permissions.can_access_subject(db, user, 'pessoas', row['person_key']):
        return False
    return True


def _source(row):
    metadata = json.loads(row['metadata_json'])
    # Protected-file locations are backend implementation data. The restricted
    # download route resolves them after its own administrator check.
    metadata = {key: value for key, value in metadata.items()
                if not (key.lower().startswith('encrypted') or key.lower().endswith('path'))} if isinstance(metadata, dict) else {}
    return {'id': row['id'], 'label': row['label'], 'filename': row['filename'],
            'importedAt': row['imported_at'], 'metadata': metadata}


def _labels(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:200] for item in value if isinstance(item, (str, int)) and str(item).strip()))[:60]


def _payload(row):
    encrypted = _get(row, 'encrypted_payload', '')
    payload = unprotect_payload(encrypted) if encrypted else json.loads(row['payload_json'])
    if not isinstance(payload, dict):
        raise ValueError('O conteúdo deste registro está em um formato inválido.')
    manual = json.loads(row['manual_json'])
    return {**payload, **manual}


def _links(payload):
    links = payload.get('destinationLinks') or []
    result = []
    for value in links if isinstance(links, list) else []:
        if isinstance(value, str):
            value = {'label': 'Abrir ferramenta', 'href': value}
        if not isinstance(value, dict):
            continue
        href = str(value.get('href') or '')
        if (href.startswith('/') and not href.startswith('//') and '\\' not in href
                and not re.search(r'[\x00-\x20]', href)
                and href.split('?', 1)[0].startswith(('/central-dp/', '/rotinas/', '/cadastros/', '/analytics/'))):
            result.append({'label': str(value.get('label') or 'Abrir ferramenta')[:100], 'href': href[:1000]})
    return result[:15]


def _summary(row):
    # Search/list requests must never open protected history or access fields.
    payload = json.loads(row['manual_json']) if row['sensitive'] or row['kind'] == 'HISTORY' else _payload(row)
    responsible = _labels(payload.get('responsibleLabels', payload.get('responsibles', payload.get('responsible', []))))
    links = _links(payload)
    if row['kind'] == 'ACTIVITY':
        links += [{'label': 'Criar caso', 'href': '/central-dp/casos?' + urlencode({'create': '1', 'sector_item': row['id']}) + '#novo'},
                  {'label': 'Calendário DP', 'href': '/rotinas/calendario'}]
    return {'id': row['id'], 'kind': row['kind'], 'title': row['title'], 'category': row['category'],
            'sourceSheet': row['source_sheet'], 'sourceRange': row['source_range'],
            'responsibleLabels': responsible, 'periodicity': str(payload.get('periodicity') or payload.get('frequency') or ''),
            'complexity': str(payload.get('complexity') or ''), 'destinationLinks': links,
            'sensitive': bool(row['sensitive'] or row['kind'] == 'HISTORY'),
            'manuallyEdited': bool(json.loads(row['manual_json'])), 'updatedAt': row['updated_at']}


def overview(db, params, user):
    _authenticated(user)
    requested_source = _param(params, 'source')
    source = db.execute('SELECT * FROM local_sector_sources WHERE id=?', (requested_source,)).fetchone() if requested_source else None
    if not source:
        source = db.execute('SELECT * FROM local_sector_sources ORDER BY updated_at DESC,id DESC LIMIT 1').fetchone()
    rows = db.execute('''SELECT id,source_id,source_key,kind,title,category,source_sheet,source_range,
        payload_json,manual_json,sensitive,company_key,person_key,updated_at
        FROM local_sector_items WHERE source_id=? ORDER BY title COLLATE NOCASE,id''',
                       (source['id'],)).fetchall() if source else []
    visible = [row for row in rows if _visible(db, row, user)]
    counts = {key: sum(row['kind'] == key for row in visible) for key in KINDS}
    query, kind, category = _norm(_param(params, 'q')), _param(params, 'kind').upper(), _param(params, 'category')
    categories = sorted({row['category'] for row in visible if row['category'] and (not kind or row['kind'] == kind)}, key=_norm)
    items = []
    for row in visible:
        item = _summary(row)
        text = ' '.join([item['title'], item['category'], item['sourceSheet'], *item['responsibleLabels'], item['periodicity']])
        if ((kind and row['kind'] != kind) or (category and row['category'] != category)
                or (query and query not in _norm(text))):
            continue
        items.append(item)
    total = len(items)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page_text = _param(params, 'page', '1')
    page = min(pages, max(1, int(page_text))) if page_text.isdigit() else 1
    credentials = []
    credential_total, credential_page, credential_pages = 0, 1, 1
    if _get(user, 'role') == 'ADMIN' and source:
        credentials = [{'id': row['id'], 'label': row['label'], 'sourceSheet': row['source_sheet']}
            for row in db.execute('SELECT id,label,source_sheet FROM local_sector_credentials WHERE source_id=? ORDER BY label COLLATE NOCASE,id', (source['id'],))]
        if query:
            credentials = [row for row in credentials if query in _norm(row['label'] + ' ' + row['sourceSheet'])]
        credential_total = len(credentials)
        credential_pages = max(1, (credential_total + PAGE_SIZE - 1) // PAGE_SIZE)
        requested_page = _param(params, 'credentialPage', '1')
        credential_page = min(credential_pages, max(1, int(requested_page))) if requested_page.isdigit() else 1
        credentials = credentials[(credential_page - 1) * PAGE_SIZE:credential_page * PAGE_SIZE]
    return {'items': items[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], 'total': total,
            'page': page, 'pageSize': PAGE_SIZE, 'pages': pages, 'categories': categories,
            'counts': counts, 'credentials': credentials, 'source': _source(source) if source else None,
            'credentialTotal': credential_total, 'credentialPage': credential_page, 'credentialPages': credential_pages,
            'canManage': _can_manage(user), 'canReveal': _get(user, 'role') == 'ADMIN'}


def _page_payload(payload, params):
    """Page table rows across the main table and all additional sections."""
    tables = [(payload, payload.get('rows'))]
    sections = payload.get('sections') or []
    tables += [(section, section.get('rows')) for section in sections
               if isinstance(section, dict)] if isinstance(sections, list) else []
    total = sum(len(rows) for _, rows in tables if isinstance(rows, list))
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    requested = _param(params, 'rowPage', _param(params, 'page', '1'))
    page = min(pages, max(1, int(requested))) if requested.isdigit() else 1
    first, end, offset = (page - 1) * PAGE_SIZE, page * PAGE_SIZE, 0
    for table, rows in tables:
        if not isinstance(rows, list):
            continue
        local_first, local_end = max(0, first - offset), min(len(rows), end - offset)
        table['rows'] = rows[local_first:local_end] if local_end > local_first else []
        offset += len(rows)
    return payload, {'rowPage': page, 'rowPageSize': PAGE_SIZE, 'rowPages': pages, 'rowTotal': total}


def detail(db, item_id, user, params=None):
    _authenticated(user)
    row = db.execute('SELECT * FROM local_sector_items WHERE id=?', (_identifier(item_id),)).fetchone()
    if not row or not _visible(db, row, user):
        raise PermissionError('Este conteúdo não está disponível para seu perfil.')
    source = db.execute('SELECT * FROM local_sector_sources WHERE id=?', (row['source_id'],)).fetchone()
    payload, pagination = _page_payload(_payload(row), params)
    return {**_summary(row), 'payload': payload, 'source': _source(source),
            'canManage': _can_manage(user), **pagination}


def mutate(db, payload, user):
    _authenticated(user)
    if not _can_manage(user):
        raise PermissionError('Somente administradores e gestores podem organizar as responsabilidades do setor.')
    if not isinstance(payload, dict) or payload.get('action', 'update_activity') not in ('update_activity', 'reset_activity'):
        raise ValueError('Ação inválida para a matriz de atividades.')
    row = db.execute('SELECT * FROM local_sector_items WHERE id=?', (_identifier(payload.get('id')),)).fetchone()
    if not row or row['kind'] != 'ACTIVITY' or not _visible(db, row, user):
        raise PermissionError('Esta atividade não está disponível para edição.')
    if payload.get('action') == 'reset_activity':
        manual = {}
    else:
        manual = json.loads(row['manual_json'])
        if 'responsibleLabels' in payload:
            labels = payload['responsibleLabels']
            if not isinstance(labels, list) or len(labels) > 60 or any(not isinstance(x, str) or len(x) > 200 for x in labels):
                raise ValueError('Informe os responsáveis como uma lista de nomes ou rótulos.')
            manual['responsibleLabels'] = _labels(labels)
        if 'periodicity' in payload:
            manual['periodicity'] = _text(payload['periodicity'], 300)
        if 'complexity' in payload:
            manual['complexity'] = _text(payload['complexity'], 120)
    db.execute('UPDATE local_sector_items SET manual_json=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (_json(manual), user['id'], row['id']))
    db.execute('INSERT INTO local_sector_item_events(item_id,actor_id,action) VALUES(?,?,?)',
               (row['id'], user['id'], 'RESET_TO_SOURCE' if payload.get('action') == 'reset_activity' else 'RESPONSIBILITIES_UPDATED'))
    return {'ok': True, 'item': detail(db, row['id'], user),
            'message': 'Matriz de responsabilidades atualizada. As execuções continuam em Casos e no Calendário DP.'}


def reveal_credential(db, credential_id, user):
    _admin(user)
    row = db.execute('SELECT id,label,source_sheet,source_range,encrypted_payload FROM local_sector_credentials WHERE id=?',
                      (_identifier(credential_id),)).fetchone()
    if not row:
        raise ValueError('O acesso solicitado não foi encontrado.')
    try:
        fields = unprotect_payload(row['encrypted_payload'])
        if not isinstance(fields, dict):
            raise ValueError()
    except ValueError:
        db.execute("INSERT INTO local_sector_credential_audit(credential_id,actor_id,action) VALUES(?,?,'REVEAL_FAILED')",
                   (row['id'], user['id']))
        raise ValueError('Não foi possível abrir este acesso protegido neste Windows.') from None
    db.execute("INSERT INTO local_sector_credential_audit(credential_id,actor_id,action) VALUES(?,?,'REVEAL')",
               (row['id'], user['id']))
    return {'id': row['id'], 'label': row['label'], 'sourceSheet': row['source_sheet'],
            'sourceRange': row['source_range'], 'fields': fields}
