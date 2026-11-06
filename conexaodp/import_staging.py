"""Prévia e aplicação explícita do layout CSV legado da V2.

Este contrato local não representa uma exportação oficial do BITTI/SIB.
Arquivos incompletos nunca desligam pessoas ausentes nem substituem a fotografia.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime


FLAGS = {
    "funcionarios_ativos.csv": "in_active_export",
    "funcionarios_desligados.csv": "in_terminated_export",
    "admitidos_periodo.csv": "in_admitted_export",
}
REQUIRED_COLUMNS = ("Código", "Nome", "Cliente", "Departamento", "Função", "Tipo de contrato", "Admissão", "Desligamento", "Status")
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
FINGERPRINT_FIELDS = ("registration", "name", "company", "department", "job_title", "contract_type",
                      "admission_date", "termination_date", "termination_reason_code", "termination_reason",
                      "in_active_export", "in_terminated_export", "in_admitted_export")


class StalePreviewError(ValueError):
    """The source or destination changed after an operator saw the preview."""


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fingerprint(row) -> str:
    return _hash({key: row[key] for key in FINGERPRINT_FIELDS}) if row else ""


def _date(value: str, field: str, errors: list[str]) -> str | None:
    if not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        errors.append(f"{field}: data inválida (use dd/mm/aaaa).")
        return None


def parse_v2_csv(source_name: str, content: bytes, company_scope: str) -> list[dict]:
    if source_name not in FLAGS:
        raise ValueError("Selecione um dos três nomes de exportação CSV da V2 conhecidos.")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("O CSV deve ter conteúdo e no máximo 10 MB.")
    if not company_scope or len(company_scope) > 160:
        raise ValueError("Informe a empresa exata deste lote.")
    try:
        text = content.decode("utf-8-sig", "strict")
    except UnicodeError as error:
        raise ValueError("O CSV deve estar em UTF-8. Converta o arquivo antes de importar.") from error
    if "\x00" in text:
        raise ValueError("O CSV contém bytes nulos e não pode ser importado.")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";", strict=True)
    columns = reader.fieldnames or []
    if len(columns) != len(set(columns)) or any(column not in columns for column in REQUIRED_COLUMNS):
        raise ValueError("O cabeçalho não corresponde ao layout CSV conhecido da V2.")
    result = []
    seen = set()
    try:
        for line_number, raw in enumerate(reader, start=2):
            if len(result) >= MAX_ROWS:
                raise ValueError("O arquivo excede 5 mil linhas; divida por empresa e período.")
            errors = []
            if None in raw:
                errors.append("Há colunas extras nesta linha.")
            values = {column: str(raw.get(column) or "").strip() for column in columns}
            registration, name, company = values["Código"], values["Nome"], values["Cliente"]
            admission = _date(values["Admissão"], "Admissão", errors)
            termination = _date(values["Desligamento"], "Desligamento", errors)
            if not registration or not name or not company or not admission:
                errors.append("Matrícula, nome, empresa e admissão são obrigatórios.")
            if company != company_scope:
                errors.append("A empresa da linha difere do escopo escolhido para o lote.")
            if termination and admission and termination < admission:
                errors.append("Desligamento anterior à admissão.")
            if source_name == "funcionarios_ativos.csv" and termination:
                errors.append("Um registro do arquivo de ativos tem desligamento.")
            if source_name == "funcionarios_desligados.csv" and not termination:
                errors.append("Um registro do arquivo de desligados não tem data de saída.")
            identity = (registration, company, admission)
            if identity in seen:
                errors.append("Matrícula, empresa e admissão repetidas neste lote.")
            seen.add(identity)
            normalized = {
                "registration": registration[:80], "name": name[:160], "company": company[:160],
                "department": values["Departamento"][:160], "job_title": values["Função"][:160],
                "contract_type": values["Tipo de contrato"][:80], "admission_date": admission,
                "termination_date": termination,
                "termination_reason_code": values.get("Código do motivo", "")[:80],
                "termination_reason": values.get("Descrição do motivo", "")[:160],
            }
            if len(registration) > 80 or len(name) > 160 or len(company) > 160:
                errors.append("Matrícula, nome ou empresa ultrapassam o limite do cadastro.")
            external_key = hashlib.sha256("\x1f".join((registration, name, company, admission or "")).encode()).hexdigest()
            result.append({"line_number": line_number, "external_key": external_key,
                           "normalized": normalized, "errors": errors})
    except csv.Error as error:
        raise ValueError("O arquivo CSV está malformado.") from error
    if not result:
        raise ValueError("O arquivo CSV não contém registros.")
    return result


def stage_v2_csv(db, source_name: str, content: bytes, company_scope: str, actor_id: int) -> dict:
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor["role"] != "ADMIN":
        raise PermissionError("Somente Administrador pode preparar importações.")
    rows = parse_v2_csv(source_name, content, company_scope)
    digest = hashlib.sha256(content).hexdigest()
    existing_batch = db.execute(
        "SELECT id FROM local_import_batches WHERE source_kind='V2_CSV' AND source_name=? AND source_sha256=? AND company_scope=?",
        (source_name, digest, company_scope),
    ).fetchone()
    if existing_batch:
        return preview_batch(db, int(existing_batch["id"]))
    for item in rows:
        normalized = item["normalized"]
        candidates = db.execute(
            """SELECT * FROM v2_employments WHERE registration=? AND company=? AND admission_date=?""",
            (normalized["registration"], company_scope, normalized["admission_date"]),
        ).fetchall()
        exact = next((row for row in candidates if row["external_key"] == item["external_key"]), None)
        if candidates and (len(candidates) != 1 or not exact):
            item["errors"].append("Vínculo existente com identidade ambígua; revisão manual necessária.")
        item["expected_hash"] = _fingerprint(exact)
        if item["errors"]:
            item["action"] = "REVIEW"
        elif exact:
            flag = FLAGS[source_name]
            changed = any(normalized[key] and normalized[key] != exact[key] for key in
                          ("department", "job_title", "contract_type", "termination_date", "termination_reason_code", "termination_reason"))
            item["action"] = "UPDATE" if changed or not exact[flag] else "NOOP"
        else:
            item["action"] = "NEW"
    batch_id = int(db.execute(
        """INSERT INTO local_import_batches(source_kind,source_name,source_sha256,company_scope,row_count,error_count,created_by)
           VALUES('V2_CSV',?,?,?,?,?,?)""",
        (source_name, digest, company_scope, len(rows), sum(bool(item["errors"]) for item in rows), actor_id),
    ).lastrowid)
    db.executemany(
        """INSERT INTO local_import_items(batch_id,line_number,external_key,normalized_json,errors_json,expected_hash,action)
           VALUES(?,?,?,?,?,?,?)""",
        [(batch_id, item["line_number"], item["external_key"],
          json.dumps(item["normalized"], ensure_ascii=False), json.dumps(item["errors"], ensure_ascii=False),
          item["expected_hash"], item["action"]) for item in rows],
    )
    return preview_batch(db, batch_id)


def preview_batch(db, batch_id: int) -> dict:
    batch = db.execute("SELECT * FROM local_import_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise LookupError("Lote de importação não encontrado.")
    items = [dict(row) for row in db.execute(
        "SELECT line_number,external_key,normalized_json,errors_json,expected_hash,action,applied_at FROM local_import_items WHERE batch_id=? ORDER BY line_number",
        (batch_id,),
    )]
    for item in items:
        item["normalized"] = json.loads(item.pop("normalized_json"))
        item["errors"] = json.loads(item.pop("errors_json"))
        item.pop("expected_hash", None)
    return {"batch": dict(batch), "items": items,
            "canApply": batch["status"] == "STAGED" and batch["error_count"] == 0}


def apply_batch(db, batch_id: int, actor_id: int) -> dict:
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor["role"] != "ADMIN":
        raise PermissionError("Somente Administrador pode aprovar importações.")
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    batch = db.execute("SELECT * FROM local_import_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise LookupError("Lote de importação não encontrado.")
    if batch["status"] == "APPLIED":
        return preview_batch(db, batch_id)
    if batch["status"] != "STAGED" or batch["error_count"]:
        raise ValueError("Lote rejeitado ou com conflitos não pode ser aplicado.")
    items = db.execute("SELECT * FROM local_import_items WHERE batch_id=? ORDER BY line_number", (batch_id,)).fetchall()
    db.execute("SAVEPOINT apply_import")
    try:
        for item in items:
            data = json.loads(item["normalized_json"])
            current = db.execute("SELECT * FROM v2_employments WHERE external_key=?", (item["external_key"],)).fetchone()
            candidates = db.execute(
                "SELECT external_key FROM v2_employments WHERE registration=? AND company=? AND admission_date=?",
                (data["registration"], batch["company_scope"], data["admission_date"]),
            ).fetchall()
            if _fingerprint(current) != item["expected_hash"] or any(row[0] != item["external_key"] for row in candidates):
                raise StalePreviewError(f"A linha {item['line_number']} mudou desde a prévia. Prepare um novo lote.")
            before = {key: current[key] for key in FINGERPRINT_FIELDS} if current else {}
            if item["action"] == "NEW":
                db.execute(
                    """INSERT INTO v2_employments(external_key,registration,name,company,department,job_title,contract_type,
                       admission_date,termination_date,termination_reason_code,termination_reason)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["external_key"], *(data[key] for key in (
                        "registration", "name", "company", "department", "job_title", "contract_type",
                        "admission_date", "termination_date", "termination_reason_code", "termination_reason"))),
                )
            if item["action"] in ("NEW", "UPDATE"):
                db.execute(
                    """UPDATE v2_employments SET department=COALESCE(NULLIF(?,''),department),
                       job_title=COALESCE(NULLIF(?,''),job_title),contract_type=COALESCE(NULLIF(?,''),contract_type),
                       termination_date=COALESCE(?,termination_date),
                       termination_reason_code=COALESCE(NULLIF(?,''),termination_reason_code),
                       termination_reason=COALESCE(NULLIF(?,''),termination_reason)
                       WHERE external_key=?""",
                    (data["department"], data["job_title"], data["contract_type"], data["termination_date"],
                     data["termination_reason_code"], data["termination_reason"], item["external_key"]),
                )
            db.execute(f"UPDATE v2_employments SET {FLAGS[batch['source_name']]}=1 WHERE external_key=?", (item["external_key"],))
            after_row = db.execute("SELECT * FROM v2_employments WHERE external_key=?", (item["external_key"],)).fetchone()
            after = {key: after_row[key] for key in FINGERPRINT_FIELDS}
            db.execute("UPDATE local_import_items SET before_json=?,after_json=?,applied_at=CURRENT_TIMESTAMP WHERE id=?",
                       (json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), item["id"]))
        db.execute("INSERT OR IGNORE INTO v2_import_log(source_name,source_sha256,row_count) VALUES(?,?,?)",
                   (batch["source_name"], batch["source_sha256"], batch["row_count"]))
        db.execute("UPDATE local_import_batches SET status='APPLIED',approved_by=?,applied_at=CURRENT_TIMESTAMP WHERE id=?",
                   (actor_id, batch_id))
        db.execute("RELEASE SAVEPOINT apply_import")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT apply_import")
        db.execute("RELEASE SAVEPOINT apply_import")
        raise
    return preview_batch(db, batch_id)


def reject_batch(db, batch_id: int, actor_id: int) -> dict:
    actor = db.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
    if not actor or actor["role"] != "ADMIN":
        raise PermissionError("Somente Administrador pode rejeitar importações.")
    updated = db.execute("UPDATE local_import_batches SET status='REJECTED',approved_by=? WHERE id=? AND status='STAGED'",
                         (actor_id, batch_id))
    if updated.rowcount != 1:
        raise ValueError("O lote não está pendente para rejeição.")
    return preview_batch(db, batch_id)
