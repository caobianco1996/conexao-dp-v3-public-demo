"""Permissões por operação e recorte de trabalhadores, sempre aplicadas no servidor."""
from __future__ import annotations

import json
import re
import sqlite3

PERMISSIONS = {
    'registry.view': 'Consultar pessoas e empresas', 'registry.manage': 'Editar cadastros locais',
    'documents.view': 'Consultar documentos', 'documents.upload': 'Anexar e organizar documentos',
    'absences.view': 'Consultar afastamentos', 'absences.manage': 'Gerenciar providências dos afastamentos',
    'extensions.view': 'Consultar prorrogações', 'extensions.manage': 'Preparar e operar prorrogações',
    'rescissions.view': 'Consultar rescisões', 'rescissions.manage': 'Gerenciar rescisões',
    'communications.send': 'Enviar comunicações aos trabalhadores',
    'analytics.view': 'Consultar indicadores', 'analytics.export': 'Exportar relatórios',
    'sensitive.view': 'Consultar campos sensíveis', 'access.manage': 'Administrar contas e permissões',
    'integrations.manage': 'Configurar integrações e canais',
}
READ = {'registry.view', 'documents.view', 'absences.view', 'extensions.view', 'rescissions.view', 'analytics.view'}
DEFAULTS = {
    'ADMIN': set(PERMISSIONS),
    'GESTOR': set(PERMISSIONS) - {'access.manage', 'integrations.manage'},
    'OPERADOR': READ | {'registry.manage', 'documents.upload', 'absences.manage', 'extensions.manage',
                         'rescissions.manage', 'communications.send', 'analytics.export'},
    'CONSULTA': READ,
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS local_access_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), internal_codes_json TEXT NOT NULL DEFAULT '["1"]',
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO local_access_settings(id) VALUES(1);
CREATE TABLE IF NOT EXISTS local_user_access (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 profile TEXT NOT NULL CHECK(profile IN ('ADMIN','GESTOR','OPERADOR','CONSULTA')),
 population TEXT NOT NULL CHECK(population IN ('ALL','EXTERNAL','INTERNAL')),
 grants_json TEXT NOT NULL DEFAULT '[]', denies_json TEXT NOT NULL DEFAULT '[]',
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_access_events (
 id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL REFERENCES users(id), user_id INTEGER REFERENCES users(id),
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate(db):
    db.executescript(SCHEMA)


def _get(value, key, default=None):
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return default


def _code(value):
    text = str(value or '').strip()
    return str(int(text)) if text.isdigit() else text


def _digits(value):
    return re.sub(r'\D', '', str(value or ''))


def enrich_user(db, user):
    if user is None:
        return None
    result = dict(user)
    try:
        access = db.execute('SELECT * FROM local_user_access WHERE user_id=?', (result['id'],)).fetchone()
        settings = db.execute('SELECT internal_codes_json FROM local_access_settings WHERE id=1').fetchone()
    except sqlite3.OperationalError:
        access, settings = None, None
    base_role = result['role']
    profile = access['profile'] if access else base_role
    permissions = set(DEFAULTS.get(profile, set()))
    if access:
        permissions.update(set(json.loads(access['grants_json'])) & set(PERMISSIONS))
        permissions.difference_update(json.loads(access['denies_json']))
    # An operation-specific grant never creates an administrator implicitly.
    if profile != 'ADMIN':
        permissions.discard('access.manage')
        permissions.discard('integrations.manage')
    result.update(baseRole=base_role, role=profile, accessProfile=profile,
                  permissions=sorted(permissions), accessScope=access['population'] if access else
                  ('ALL' if profile in ('ADMIN', 'GESTOR') else 'EXTERNAL'),
                  internalCodes=[_code(x) for x in json.loads(settings[0] if settings else '["1"]')])
    directory, names, internal_cnpjs = {}, {}, set()
    if result['accessScope'] != 'ALL':
        for row in db.execute("SELECT source_id,data_json FROM local_sib_records WHERE dataset='companies'"):
            data = json.loads(row['data_json'])
            code = _code(data.get('code') or row['source_id'])
            record = {'code': code, 'cnpj': _digits(data.get('cnpj')), 'name': data.get('name') or ''}
            directory['sib:' + row['source_id']] = record
            names[record['name'].casefold()] = record
            if code in result['internalCodes'] and len(record['cnpj']) == 14:
                internal_cnpjs.add(record['cnpj'])
    result['_companyDirectory'], result['_companyNames'] = directory, names
    result['_internalCnpjs'] = sorted(internal_cnpjs)
    return result


def can(user, permission):
    if user is None:
        return False
    explicit = _get(user, 'permissions')
    return permission in (explicit if explicit is not None else DEFAULTS.get(_get(user, 'role'), set()))


def scope(user):
    return _get(user, 'accessScope', 'ALL' if _get(user, 'role') in ('ADMIN', 'GESTOR') else 'EXTERNAL')


def allowed_company(db, user, company_key='', cnpj='', code='', name=''):
    if user is None:
        return True  # Trusted background projections do not serve a user's request.
    population = scope(user)
    if population == 'ALL':
        return True
    directory = _get(user, '_companyDirectory', {})
    record = directory.get(str(company_key)) or _get(user, '_companyNames', {}).get(str(name or company_key).casefold()) or {}
    actual_code = _code(code or record.get('code'))
    actual_cnpj = _digits(cnpj or record.get('cnpj'))
    if not actual_code:
        match = re.match(r'^\s*(\d+)\s*[-·]', str(name or company_key))
        actual_code = _code(match[1]) if match else ''
    if not actual_code and str(company_key).startswith('sib:'):
        actual_code = _code(str(company_key)[4:])
    known = bool(actual_code or len(actual_cnpj) == 14)
    internal = actual_code in _get(user, 'internalCodes', ['1']) or actual_cnpj in _get(user, '_internalCnpjs', [])
    return bool(known and (internal if population == 'INTERNAL' else not internal))


def can_access_subject(db, user, view, key):
    if user is None or scope(user) == 'ALL':
        return True
    key = str(key or '')
    if view == 'empresas':
        if key.startswith('sib:'):
            return allowed_company(db, user, company_key=key)
        from .companies import catalog, profile
        company = next((item for item in catalog(db) if item['key'] == key), None)
        if not company:
            return False
        data, _ = profile(db, key)
        return allowed_company(db, user, company_key=key, code=company.get('code'), name=company.get('name'),
                               cnpj=data.get('cnpj') if isinstance(data, dict) else '')
    if key.startswith('sib:') and not key.startswith('sib:p:'):
        row = db.execute("SELECT data_json FROM local_sib_records WHERE dataset='employments' AND source_id=?", (key[4:],)).fetchone()
        if not row:
            return False
        data = json.loads(row[0])
        return allowed_company(db, user, company_key='sib:' + str(data.get('companyId') or ''),
                               code=data.get('companyId'), cnpj=data.get('takerCnpj'))
    if key.startswith('local:') and key[6:].isdigit():
        row = db.execute('SELECT company FROM registry_employments WHERE id=?', (int(key[6:]),)).fetchone()
        return bool(row and allowed_company(db, user, name=row[0]))
    if key.startswith('v:'):
        row = db.execute('SELECT company FROM v2_employments WHERE external_key=?', (key[2:],)).fetchone()
        return bool(row and allowed_company(db, user, name=row[0]))
    from .registry import project
    people, employments, _ = project(db)
    person = people.get(key)
    matches = [allowed_company(db, user, company_key=item.get('company_key', ''), name=item.get('company', ''))
               for item in person['employments']] if person else []
    return bool(matches and (any(matches) if view == 'vinculos' else all(matches)))


def require_subject(db, user, view, key):
    if not can_access_subject(db, user, view, key):
        raise PermissionError('Este cadastro está fora do seu recorte de acesso.')


def case_allowed(db, user, case):
    if scope(user) == 'ALL':
        return True
    data = dict(case)
    if data.get('source_system') == 'SIB':
        snapshot = json.loads(data.get('source_snapshot_json') or '{}')
        return allowed_company(db, user, company_key=snapshot.get('company_key') or '',
                               code=snapshot.get('company_code') or '', name=snapshot.get('company') or data.get('company', ''))
    return allowed_company(db, user, name=data.get('company', ''))


def required_permission(path, write=False):
    if path.startswith(('/api/ui/sector','/rotinas/setor')):
        return 'registry.manage'
    if path == '/api/access/self':
        return ''
    if path.startswith('/api/access') or path.startswith('/administracao/'):
        return 'access.manage'
    if path.startswith('/api/ui/registry-documents'):
        return 'documents.upload' if write else 'documents.view'
    if path.startswith('/api/ui/person-documents/'):
        return 'documents.view'
    if path.startswith(('/api/ui/absences', '/central-dp/afastamentos')):
        return 'absences.manage' if write else 'absences.view'
    if path.startswith(('/api/ui/rescissions', '/central-dp/rescisoes')):
        return 'rescissions.manage' if write else 'rescissions.view'
    if path.startswith(('/api/ui/temporary-extensions', '/central-dp/prorrogacoes')):
        return 'extensions.manage' if write else 'extensions.view'
    if path.startswith(('/api/ui/registry', '/cadastros/')):
        return 'registry.manage' if write else 'registry.view'
    if path.startswith(('/api/people-reports', '/api/ui/people-reports')):
        return 'analytics.export'
    if path.startswith(('/api/ui/people-analytics', '/analytics/', '/analytics-pro/')):
        return 'analytics.view'
    if path.startswith(('/api/integrations', '/api/clicksign', '/api/email/settings', '/api/zpro')):
        return 'integrations.manage'
    return ''


def page_data(db, user):
    if not can(user, 'access.manage'):
        raise PermissionError('Somente administradores podem gerenciar permissões.')
    accounts = []
    for row in db.execute('SELECT id,name,email,role,active FROM users ORDER BY name COLLATE NOCASE,id'):
        item = enrich_user(db, row)
        accounts.append({key: item[key] for key in ('id', 'name', 'email', 'active', 'accessProfile', 'accessScope', 'permissions')})
    settings = db.execute('SELECT internal_codes_json FROM local_access_settings WHERE id=1').fetchone()
    return {'users': accounts, 'profiles': list(DEFAULTS), 'permissionOptions': PERMISSIONS,
            'defaults': {key: sorted(value) for key, value in DEFAULTS.items()},
            'internalCompanyCodes': json.loads(settings[0]) if settings else ['1'], 'csrf': user['csrf_token'], 'userId': user['id']}


def save(db, user, payload):
    if not can(user, 'access.manage'):
        raise PermissionError('Somente administradores podem gerenciar permissões.')
    target_id = int(payload.get('userId') or 0)
    target = db.execute('SELECT id,role FROM users WHERE id=? AND active=1', (target_id,)).fetchone()
    profile = str(payload.get('profile') or '')
    population = str(payload.get('population') or '')
    if not target or profile not in DEFAULTS or population not in ('ALL', 'EXTERNAL', 'INTERNAL'):
        raise ValueError('Selecione uma conta ativa, um perfil e um recorte válidos.')
    if profile == 'ADMIN' and population != 'ALL':
        raise ValueError('Administradores gerenciam toda a base. Use Gestor, Operador ou Consulta para limitar os funcionários acessíveis.')
    grants = sorted(set(payload.get('permissions') or []) & set(PERMISSIONS))
    if target_id == int(user['id']) and (profile != 'ADMIN' or population != 'ALL' or 'access.manage' not in grants):
        raise ValueError('Sua própria conta deve manter o perfil administrador, acesso completo e gestão de permissões.')
    old = db.execute('SELECT * FROM local_user_access WHERE user_id=?', (target_id,)).fetchone()
    before = dict(old) if old else {'profile': target['role']}
    if profile != 'ADMIN':
        grants = [key for key in grants if key not in ('access.manage', 'integrations.manage')]
    denies = sorted(set(PERMISSIONS) - set(grants))
    db.execute('UPDATE users SET role=? WHERE id=?', ('OPERADOR' if profile == 'CONSULTA' else profile, target_id))
    db.execute('''INSERT INTO local_user_access(user_id,profile,population,grants_json,denies_json,updated_by)
        VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET profile=excluded.profile,population=excluded.population,
        grants_json=excluded.grants_json,denies_json=excluded.denies_json,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
        (target_id, profile, population, json.dumps(grants), json.dumps(denies), user['id']))
    after = {'profile': profile, 'population': population, 'permissions': grants}
    db.execute('INSERT INTO local_access_events(actor_id,user_id,before_json,after_json) VALUES(?,?,?,?)',
               (user['id'], target_id, json.dumps(before), json.dumps(after)))
    return page_data(db, user)
