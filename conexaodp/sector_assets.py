"""Administrator-only access to the encrypted source workbook and its images."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile

from . import sector


@lru_cache(maxsize=2)
def _source_bytes(path, modified, size):
    return sector.unprotect_bytes(Path(path).read_text(encoding='ascii'))


def original(db, source_id, user, private_root):
    sector._admin(user)
    row = db.execute('SELECT filename,metadata_json FROM local_sector_sources WHERE id=?',
                     (sector._identifier(source_id),)).fetchone()
    if not row:
        raise ValueError('A origem solicitada não foi encontrada.')
    metadata = json.loads(row['metadata_json'])
    path = Path(metadata.get('encryptedSourcePath') or '').resolve()
    root = Path(private_root).resolve()
    if not path.is_relative_to(root) or path.suffix != '.dpapi' or not path.is_file():
        raise ValueError('O arquivo de origem protegido não está disponível.')
    stat = path.stat()
    return row['filename'], _source_bytes(str(path),stat.st_mtime_ns,stat.st_size)


def image(db, source_id, name, user, private_root):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:png|jpe?g|gif|webp)',name,re.I):
        raise ValueError('Esta captura não está disponível para visualização.')
    _, data = original(db,source_id,user,private_root)
    with ZipFile(BytesIO(data)) as archive:
        member = 'xl/media/'+name
        if member not in archive.namelist() or archive.getinfo(member).file_size > 8_000_000:
            raise ValueError('A captura solicitada não foi encontrada.')
        raw = archive.read(member)
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif raw.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
    elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':mime='image/webp'
    else:raise ValueError('O formato da captura não é suportado.')
    return raw,mime


def enrich_processes(db, rows, user):
    try:
        sources={row['process_id']:json.loads(row['content_json']) for row in db.execute('SELECT process_id,content_json FROM local_sector_process_enrichment')}
    except Exception as error:
        import sqlite3
        if isinstance(error,sqlite3.OperationalError) and 'no such table' in str(error):return rows
        raise
    for item in rows:
        source = sources.get(item['id'])
        if not source:
            continue
        public = {**source, 'sections':[{**section,'images':section.get('images',[]) if user['role']=='ADMIN' else []} for section in source['sections']]}
        public['canViewOriginal'] = user['role']=='ADMIN'
        item['workbook'] = public
    return rows


def history_links(db, keys, user, *, company_key=''):
    """Return dossier links for explicit identities, without opening payloads."""
    try:
        sector._authenticated(user)
    except PermissionError:
        return []
    if not sector._can_sensitive(user):
        return []
    keys = sorted({key for key in (keys or []) if isinstance(key, str) and key and len(key) <= 180})
    # Keep the exact company identity supplied by the registry. Names and bare
    # employee numbers are not sufficient evidence for historical association.
    company_key = company_key if isinstance(company_key, str) and len(company_key) <= 180 else ''
    rows = {}
    try:
        fields = 'id,kind,title,category,source_sheet,source_range,sensitive,company_key,person_key'
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            query = f"SELECT {fields} FROM local_sector_items WHERE kind='HISTORY' AND person_key IN ({','.join('?' for _ in chunk)})"
            rows.update((row['id'], row) for row in db.execute(query, chunk))
        if company_key:
            query = f"SELECT {fields} FROM local_sector_items WHERE kind='HISTORY' AND company_key=?"
            rows.update((row['id'], row) for row in db.execute(query, (company_key,)))
    except Exception as error:
        import sqlite3
        if isinstance(error,sqlite3.OperationalError) and 'no such table' in str(error):return []
        raise
    return [{'id':row['id'],'title':row['title'],'category':row['category'],'href':f"/rotinas/setor?kind=HISTORY&item={row['id']}"}
            for row in sorted(rows.values(), key=lambda value: value['id']) if sector._visible(db,row,user)]
