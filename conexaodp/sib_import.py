"""Importação SIB por clique, com checkpoint e identidade própria da origem."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_sib_import_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    cycle TEXT NOT NULL,
    source_url TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    backup_path TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sib_records (
    dataset TEXT NOT NULL CHECK(dataset IN ('companies','employments')),
    source_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    cycle TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(dataset,source_id)
);
CREATE INDEX IF NOT EXISTS local_sib_records_cycle ON local_sib_records(cycle,dataset);
-- Keep the last-import lookup off the large JSON payload pages.
CREATE INDEX IF NOT EXISTS local_sib_records_imported_at ON local_sib_records(imported_at);
CREATE INDEX IF NOT EXISTS local_sib_records_employment_fields ON local_sib_records(
 source_id,json_extract(raw_json,'$.VIN_FUN'),json_extract(raw_json,'$.CODIGO_ESTAGIARIO_FUN'),
 json_extract(raw_json,'$.DEP_FUN'),json_extract(raw_json,'$.COD_FUN_FUN')
) WHERE dataset='employments';
CREATE TABLE IF NOT EXISTS local_sib_import_pages (
    cycle TEXT NOT NULL,
    dataset TEXT NOT NULL,
    cursor_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(cycle,dataset,cursor_hash)
);
"""
DATASETS = ("companies", "employments")
IMPORT_LOCK = threading.Lock()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _progress():
    return {key: {"count": 0, "total": None, "done": False, "cursor": "",
                  "snapshot": "", "mapping": ""} for key in DATASETS}


def summary(db):
    row = db.execute("SELECT * FROM local_sib_import_state WHERE id=1").fetchone()
    progress = json.loads(row["progress_json"]) if row else _progress()
    return {"started": bool(row), "done": all(progress[key]["done"] for key in DATASETS),
            "updatedAt": row["updated_at"] if row else "",
            "datasets": {key: {field: progress[key][field] for field in ("count", "total", "done")}
                         for key in DATASETS},
            "catalogCounts": {key: db.execute("SELECT COUNT(*) FROM local_sib_records WHERE dataset=?", (key,)).fetchone()[0]
                              for key in DATASETS}}


def source_records(db, dataset):
    if dataset != 'employments':
        for row in db.execute("SELECT source_id,data_json FROM local_sib_records WHERE dataset=? ORDER BY source_id", (dataset,)):
            yield row['source_id'], json.loads(row['data_json'])
        return
    from .sib_record_mapping import employment_fields
    types = dict(db.execute('SELECT code,label FROM local_sib_contract_types'))
    from .sib_presence import overlays
    presence = overlays(db)
    # Extract only the needed codes; never transfer complete raw HR records to the projection.
    for row in db.execute("""SELECT source_id,data_json,json_extract(raw_json,'$.VIN_FUN') contract_code,
        json_extract(raw_json,'$.CODIGO_ESTAGIARIO_FUN') intern_code,json_extract(raw_json,'$.DEP_FUN') department,
        json_extract(raw_json,'$.COD_FUN_FUN') job_code,json_extract(raw_json,'$.SIG_FUN_FUN') job_prefix
        FROM local_sib_records INDEXED BY local_sib_records_employment_fields
        WHERE dataset='employments' ORDER BY source_id"""):
        data = json.loads(row['data_json'])
        raw = {'VIN_FUN':row['contract_code'],'CODIGO_ESTAGIARIO_FUN':row['intern_code'],
               'DEP_FUN':row['department'],'COD_FUN_FUN':row['job_code'],'SIG_FUN_FUN':row['job_prefix']}
        source_parts = row['source_id'].split(':')
        if len(source_parts) == 3:
            raw.update(EMP_FUN=source_parts[0], FIL_FUN=source_parts[1], COD_FUN=source_parts[2])
        data.update(employment_fields(data, raw, types))
        data.update(presence.get(row['source_id'], {}))
        yield row['source_id'], data


def _admin(db, actor_id):
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor["role"] != "ADMIN":
        raise PermissionError("Somente Administrador pode importar cadastros do SIB.")


def prepare(db, source_url, actor_id, *, restart=False):
    _admin(db, actor_id)
    row = db.execute("SELECT * FROM local_sib_import_state WHERE id=1").fetchone()
    if row and not restart:
        if row["source_url"] != source_url:
            raise ValueError("A URL da Bridge mudou. Inicie uma nova leitura para usar essa origem.")
        return dict(row)
    db.execute("""INSERT INTO local_sib_import_state(id,cycle,source_url,progress_json,updated_by)
        VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cycle=excluded.cycle,
        source_url=excluded.source_url,progress_json=excluded.progress_json,backup_path='',
        updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
        (uuid4().hex, source_url, _json(_progress()), actor_id))
    return dict(db.execute("SELECT * FROM local_sib_import_state WHERE id=1").fetchone())


def _validate_page(state, dataset, page):
    if (not isinstance(page, dict) or page.get("contract") != "sib-catalog-v1"
            or page.get("scope") != "all" or page.get("read_only") is not True or page.get("dataset") != dataset):
        raise ValueError("A Bridge não confirmou o contrato de listagem completa dos cadastros.")
    total, done, items = page.get("total"), page.get("done"), page.get("items")
    if type(total) is not int or total < 0 or type(done) is not bool or not isinstance(items, list) or len(items) > 50:
        raise ValueError("A página SIB retornou contagem ou formato inválido.")
    snapshot, mapping, cursor = page.get("snapshot_id"), page.get("mapping_hash"), page.get("next_cursor")
    if (not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{32}", snapshot)
            or not isinstance(mapping, str) or not re.fullmatch(r"[a-f0-9]{64}", mapping)
            or not isinstance(cursor, str) or len(cursor) > 8192
            or bool(cursor) == done or (not done and not items) or (cursor and cursor == state["cursor"])):
        raise ValueError("A paginação SIB não permite continuar com segurança.")
    if state["snapshot"] and (snapshot != state["snapshot"] or mapping != state["mapping"] or total != state["total"]):
        raise ValueError("A origem mudou durante a consulta. Inicie uma nova leitura; os lotes já trazidos foram preservados.")
    count = state["count"] + len(items)
    if count > total or (done and count != total) or (not done and count >= total):
        raise ValueError("A contagem da página difere do total informado pelo SIB.")
    rows, seen = [], set()
    pattern = r"-?\d{1,10}" if dataset == "companies" else r"-?\d{1,10}:-?\d{1,10}:-?\d{1,10}"
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Cadastro SIB em formato inválido.")
        identifier, data, raw = item.get("source_id"), item.get("data"), item.get("raw")
        if (not isinstance(identifier, str) or not re.fullmatch(pattern, identifier)
                or identifier in seen or not isinstance(data, dict) or not isinstance(raw, dict)
                or data.get("sourceId") != identifier):
            raise ValueError("O lote contém identidade SIB ausente, duplicada ou incompatível.")
        seen.add(identifier)
        rows.append((identifier, _json(data), _json(raw)))
    return rows, {"count": count, "total": total, "done": done, "cursor": cursor,
                  "snapshot": snapshot, "mapping": mapping}


def apply_page(db, prepared, dataset, page, actor_id):
    """Commit records and checkpoint together; repeated/concurrent pages cannot skip data."""
    _admin(db, actor_id)
    current = db.execute("SELECT * FROM local_sib_import_state WHERE id=1").fetchone()
    if (not current or current["cycle"] != prepared["cycle"]
            or current["progress_json"] != prepared["progress_json"]):
        raise ValueError("Outro lote já avançou a leitura. Atualize a tela antes de continuar.")
    progress = json.loads(current["progress_json"])
    pending = next((key for key in DATASETS if not progress[key]["done"]), None)
    if dataset != pending:
        raise ValueError("Este lote não corresponde à etapa atual da leitura SIB.")
    rows, updated = _validate_page(progress[dataset], dataset, page)
    if dataset == 'employments':
        from .sib_record_mapping import employment_fields
        types = dict(db.execute('SELECT code,label FROM local_sib_contract_types'))
        normalized = []
        for identifier, encoded, original in rows:
            data = json.loads(encoded)
            data.update(employment_fields(data, json.loads(original), types))
            normalized.append((identifier, _json(data), original))
        rows = normalized
        from .sib_gap_recovery import same_identity
        for identifier, encoded, _ in rows:
            previous = db.execute("SELECT data_json,json_extract(raw_json,'$.VIN_FUN') modality FROM local_sib_records WHERE dataset='employments' AND source_id=?", (identifier,)).fetchone()
            if previous:
                stored = json.loads(previous['data_json'])
                stored['contractTypeCode'] = stored.get('contractTypeCode') or previous['modality']
                if not same_identity(stored, json.loads(encoded)):
                    raise ValueError('O catálogo reutilizou uma identificação. A ficha anterior foi preservada; confira a divergência antes de importar esta página.')
    cursor_hash = hashlib.sha256(progress[dataset]["cursor"].encode()).hexdigest()
    if db.execute("SELECT 1 FROM local_sib_import_pages WHERE cycle=? AND dataset=? AND cursor_hash=?",
                  (current["cycle"], dataset, cursor_hash)).fetchone():
        raise ValueError("A Bridge repetiu uma página já importada.")
    if updated["cursor"]:
        next_hash = hashlib.sha256(updated["cursor"].encode()).hexdigest()
        if db.execute("SELECT 1 FROM local_sib_import_pages WHERE cycle=? AND dataset=? AND cursor_hash=?",
                      (current["cycle"], dataset, next_hash)).fetchone():
            raise ValueError("A Bridge retornou um ciclo de paginação.")
    for identifier, _, _ in rows:
        if db.execute("SELECT 1 FROM local_sib_records WHERE dataset=? AND source_id=? AND cycle=?",
                      (dataset, identifier, current["cycle"])).fetchone():
            raise ValueError("A Bridge repetiu um cadastro entre páginas. O lote foi cancelado.")
    # A savepoint makes this page atomic even when the caller catches an error.
    db.execute("SAVEPOINT sib_page")
    try:
        db.executemany("""INSERT INTO local_sib_records(dataset,source_id,data_json,raw_json,cycle)
            VALUES(?,?,?,?,?) ON CONFLICT(dataset,source_id) DO UPDATE SET
            data_json=excluded.data_json,raw_json=excluded.raw_json,cycle=excluded.cycle,imported_at=CURRENT_TIMESTAMP""",
            [(dataset, identifier, data, raw, current["cycle"]) for identifier, data, raw in rows])
        if dataset == 'employments':
            from .sib_presence import observe
            for identifier, encoded, _ in rows:
                observe(db, identifier, json.loads(encoded), url=current['source_url'])
        db.execute("INSERT INTO local_sib_import_pages(cycle,dataset,cursor_hash,row_count) VALUES(?,?,?,?)",
                   (current["cycle"], dataset, cursor_hash, len(rows)))
        progress[dataset] = updated
        db.execute("UPDATE local_sib_import_state SET progress_json=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
                   (_json(progress), actor_id))
        db.execute("RELEASE SAVEPOINT sib_page")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT sib_page")
        db.execute("RELEASE SAVEPOINT sib_page")
        raise
    return summary(db)


def import_next(database: Path, client, actor_id: int, *, restart=False):
    from .database import connect
    if not IMPORT_LOCK.acquire(blocking=False):
        raise ValueError("Já há um lote SIB em consulta. Aguarde a resposta.")
    try:
        with connect(database) as db:
            prepared = prepare(db, client.config.base_url, actor_id, restart=restart)
        progress = json.loads(prepared["progress_json"])
        dataset = next((key for key in DATASETS if not progress[key]["done"]), None)
        if dataset is None:
            with connect(database) as db:
                return {"ok": True, "import": summary(db), "message": "A leitura deste ciclo já terminou."}
        page = client.get_catalog_page(dataset, cursor=progress[dataset]["cursor"])
        _validate_page(progress[dataset], dataset, page)
        if not prepared["backup_path"]:
            folder = database.resolve().parent / "backups"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"antes_sib_{datetime.now():%Y%m%d_%H%M%S}_{prepared['cycle']}.sqlite3"
            with connect(database) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
            with connect(database) as db:
                db.execute("UPDATE local_sib_import_state SET backup_path=? WHERE id=1 AND cycle=?", (str(target), prepared["cycle"]))
        with connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            result = apply_page(db, prepared, dataset, page, actor_id)
            count = len(page["items"])
            db.execute("""INSERT INTO local_integration_events(integration,action,outcome,result_count,summary,actor_id)
                VALUES('SIB','IMPORTACAO_LOTE','OK',?,?,?)""",
                (count, f"Lote de {dataset} trazido para os Cadastros V3; {count} registro(s).", actor_id))
        return {"ok": True, "import": result, "count": count,
                "message": f"{count} cadastro(s) trazido(s). " + ("Leitura completa neste ciclo." if result["done"] else "Progresso salvo; você pode continuar depois.")}
    finally:
        IMPORT_LOCK.release()
