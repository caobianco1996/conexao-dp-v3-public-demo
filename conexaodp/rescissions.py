"""Fluxo local e auditável de documentação de rescisões.

O cadastro permanece no SQLite da V3; o envio real por SMTP é tratado no
servidor e não substitui a conferência jurídica ou o recebimento confirmado.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import re
import secrets
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path


REQUIREMENTS = (
    ("TRCT", "Termo de Rescisão do Contrato de Trabalho (TRCT)", True),
    ("PAGAMENTO", "Comprovante de pagamento das verbas rescisórias", True),
    ("FICHA_FINANCEIRA", "Ficha financeira · arquivo interno", False),
    ("EXAME_DEMISSIONAL", "Exame médico demissional", True),
    ("FGTS", "FGTS / comprovante de movimentação", True),
    ("SEGURO_DESEMPREGO", "Requerimento do seguro-desemprego, quando aplicável", True),
)
CHANNELS = {"EMAIL", "WHATSAPP", "AMBOS"}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
}
REASON_LABELS = {
    "PEDIDO_EMPREGADO": "A pedido do empregado",
    "SEM_JUSTA_CAUSA": "Dispensa sem justa causa",
    "JUSTA_CAUSA": "Dispensa por justa causa",
    "TERMINO_CONTRATO": "Término de contrato",
    "ACORDO": "Rescisão por acordo",
    "FALECIMENTO": "Falecimento",
    "OUTRO": "Outro motivo",
}


def _event(db, case_id: int, event_type: str, actor_id: int, **details) -> None:
    db.execute(
        "INSERT INTO local_rescission_events(case_id,event_type,actor_id,details_json) VALUES(?,?,?,?)",
        (case_id, event_type, actor_id, json.dumps(details, ensure_ascii=False)),
    )


def _set_case_status(db, case_id: int, actor_id: int) -> str:
    case = db.execute("SELECT status,is_test FROM local_rescission_cases WHERE id=?", (case_id,)).fetchone()
    if not case:
        raise LookupError("Rescisão não encontrada.")
    status = str(case["status"] or "")
    if status in ("CONCLUIDO", "CANCELADO"):
        return status
    if case["is_test"]:
        pending = db.execute(
            "SELECT count(*) FROM local_rescission_requirements WHERE case_id=? AND code='TRCT' AND mandatory=1 AND status='PENDENTE'",
            (case_id,),
        ).fetchone()[0]
    else:
        pending = db.execute(
            "SELECT count(*) FROM local_rescission_requirements WHERE case_id=? AND mandatory=1 AND status='PENDENTE'",
            (case_id,),
        ).fetchone()[0]
    status = "EM_DOCUMENTACAO" if pending else "PRONTO_PARA_ENVIO"
    db.execute("UPDATE local_rescission_cases SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, case_id))
    return status


def ensure_case(db, employment_id: int, actor_id: int, *, is_test: bool | None = None) -> int | None:
    """Abre/atualiza um caso quando o vínculo tem data válida de desligamento."""
    row = db.execute(
        """SELECT e.id,e.person_key,e.company,e.registration,e.contract_type,e.admission_date,
                  e.termination_date,e.dismissal_reason_code,e.dismissal_reason,e.is_test,p.is_test person_is_test
           FROM registry_employments e JOIN registry_people p ON p.id=CAST(substr(e.person_key,3) AS INTEGER)
           WHERE e.id=? AND e.person_key LIKE 'm:%'""",
        (int(employment_id),),
    ).fetchone()
    if not row or not row["termination_date"]:
        return None
    try:
        dismissal = date.fromisoformat(str(row["termination_date"])[:10])
        admission = date.fromisoformat(str(row["admission_date"])[:10])
    except ValueError as exc:
        raise ValueError("Corrija as datas de admissão e desligamento antes de iniciar a rescisão.") from exc
    if dismissal < admission:
        raise ValueError("A data de desligamento não pode ser anterior à admissão.")
    deadline = dismissal + timedelta(days=10)
    test_record = bool(row["is_test"] or row["person_is_test"]) if is_test is None else bool(is_test)
    existing = db.execute("SELECT id FROM local_rescission_cases WHERE employment_id=?", (employment_id,)).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        case_id = int(existing["id"])
        db.execute(
            """UPDATE local_rescission_cases SET dismissal_date=?,deadline_date=?,reason_code=?,reason_label=?,
               is_test=?,updated_at=? WHERE id=?""",
            (dismissal.isoformat(), deadline.isoformat(), row["dismissal_reason_code"], row["dismissal_reason"] or REASON_LABELS.get(row["dismissal_reason_code"], ""),
             int(test_record), now, case_id),
        )
    else:
        case_id = int(db.execute(
            """INSERT INTO local_rescission_cases(employment_id,dismissal_date,deadline_date,reason_code,reason_label,
               is_test,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (employment_id, dismissal.isoformat(), deadline.isoformat(), row["dismissal_reason_code"],
             row["dismissal_reason"] or REASON_LABELS.get(row["dismissal_reason_code"], ""), int(test_record), actor_id, now, now),
        ).lastrowid)
        for code, label, mandatory in (REQUIREMENTS[:1] if test_record else REQUIREMENTS):
            status, reason = "PENDENTE", ""
            if not mandatory:
                status, reason = "DISPENSADO", "Documento interno do DP; não será enviado ao trabalhador."
            if code in {"EXAME_DEMISSIONAL", "SEGURO_DESEMPREGO"} and "TEMPOR" in str(row["contract_type"] or "").upper():
                status, reason = "DISPENSADO", "Dispensa automática na simulação para contrato temporário."
            db.execute(
                """INSERT INTO local_rescission_requirements(case_id,code,label,mandatory,status,exception_reason,updated_by)
                   VALUES(?,?,?,?,?,?,?)""",
                (case_id, code, label, int(mandatory), status, reason, actor_id),
            )
        _event(db, case_id, "RESCISAO_INICIADA", actor_id, dismissal_date=dismissal.isoformat(),
               deadline_date=deadline.isoformat(), is_test=test_record)
    _set_case_status(db, case_id, actor_id)
    return case_id


def _person_id(person_key: str | None) -> int | None:
    match = re.fullmatch(r"m:(\d+)", str(person_key or ""))
    return int(match.group(1)) if match else None


def _validate_document(filename: str, mime_type: str, raw: bytes) -> tuple[str, str]:
    safe_name = Path(str(filename or "").replace("\\", "/").replace("\x00", "")).name.strip()[:180]
    extension = Path(safe_name).suffix.lower()
    allowed_mimes = DOCUMENT_TYPES.get(extension)
    if not safe_name or not allowed_mimes:
        raise ValueError("Anexe PDF, DOC, DOCX, PNG ou JPG.")
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("O arquivo deve ter conteúdo e no máximo 10 MB.")
    detected_mime = str(mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream").split(";")[0].strip().lower()
    if detected_mime not in allowed_mimes and detected_mime != "application/octet-stream":
        raise ValueError("O tipo informado não corresponde à extensão do arquivo.")
    signatures = {
        ".pdf": raw.startswith(b"%PDF-"),
        ".png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": raw.startswith(b"\xff\xd8\xff"),
        ".jpeg": raw.startswith(b"\xff\xd8\xff"),
        ".doc": raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".docx": raw.startswith(b"PK\x03\x04"),
    }
    if not signatures[extension]:
        raise ValueError("O conteúdo do arquivo não corresponde ao formato declarado.")
    if extension == ".docx":
        _validate_docx_archive(raw)
    return safe_name, next(iter(allowed_mimes))


def _validate_docx_archive(raw: bytes) -> None:
    """Confere estrutura OOXML sem extrair o ZIP para o disco."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 500:
                raise ValueError("O DOCX tem uma estrutura inválida ou excessiva.")
            names = set()
            total = 0
            for entry in entries:
                name = entry.filename
                parts = name.rstrip("/").split("/")
                if (not name or name.startswith("/") or "\\" in name or
                        any(part in ("", ".", "..") for part in parts) or
                        (entry.external_attr >> 16) & 0o170000 == 0o120000 or
                        entry.flag_bits & 0x1):
                    raise ValueError("O DOCX contém caminhos ou entradas não permitidos.")
                if name in names:
                    raise ValueError("O DOCX contém arquivos repetidos.")
                names.add(name)
                total += entry.file_size
                if (entry.file_size > 20 * 1024 * 1024 or total > 40 * 1024 * 1024 or
                        entry.file_size > 2 * 1024 * 1024 and entry.file_size > entry.compress_size * 100):
                    raise ValueError("O DOCX excede o limite de expansão permitido.")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("O arquivo não contém um documento Word válido.")
            archive.read("[Content_Types].xml")
            archive.read("word/document.xml")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError("O arquivo DOCX está corrompido.") from error


def _case_row(db, case_id: int):
    row = db.execute("SELECT r.*,e.person_key,e.registration,e.company,e.unit,e.contract_type,e.admission_date,e.termination_date,e.dismissal_reason_code,p.name,p.cpf,p.email,p.whatsapp FROM local_rescission_cases r LEFT JOIN registry_employments e ON e.id=r.employment_id LEFT JOIN registry_people p ON e.person_key LIKE 'm:%' AND p.id=CAST(substr(e.person_key,3) AS INTEGER) WHERE r.id=?", (int(case_id),)).fetchone()
    if not row:
        return None
    item = dict(row)
    if item['source_system'] == 'SIB':
        source = json.loads(item['source_snapshot_json'])
        for key in ('person_key','registration','company','unit','contract_type','admission_date','termination_date','dismissal_reason_code','name','cpf','email','whatsapp'):
            item[key] = source.get(key) or ''
        item['employment_key'] = source.get('employmentKey') or ''
    else:
        item['employment_key'] = 'local:' + str(item['employment_id'])
    return item


def _document_owner(item):
    return (None, str(json.loads(item['source_snapshot_json']).get('person_key') or '')) if item.get('source_system') == 'SIB' else (_person_id(item.get('person_key')), '')


def _source_not_pending(db, item):
    if item.get('source_system') == 'SIB':
        source = str(item.get('employment_key') or '')[4:]
        if db.execute('SELECT 1 FROM local_sib_operation_jobs WHERE source_id=?', (source,)).fetchone():
            raise ValueError('O SIB tem uma atualização pendente neste vínculo. Aguarde o processamento antes de continuar.')


def _source_ready(db, item):
    _source_not_pending(db, item)
    if item.get('source_review'):
        raise ValueError('Confira a alteração recebida do SIB e libere o caso antes de alterar documentos ou enviar.')


def list_cases(db, today: date, *, search: str = '', company: str = '', reason: str = '',
               phase: str = '', date_from: str = '', date_to: str = '', page: int = 1, page_size: int = 25, user=None) -> dict:
    # Filter and aggregate before pagination; historical SIB imports can contain tens of thousands of cases.
    for value in (date_from, date_to):
        if value: date.fromisoformat(value)
    base = """SELECT r.*,CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.name') ELSE p.name END name,
      CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.cpf') ELSE p.cpf END cpf,
      CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.registration') ELSE e.registration END registration,
      CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.company') ELSE e.company END company,
      CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.unit') ELSE e.unit END unit,
      CASE WHEN r.source_system='SIB' THEN json_extract(r.source_snapshot_json,'$.company_key') ELSE '' END access_company_key
      FROM local_rescission_cases r LEFT JOIN registry_employments e ON e.id=r.employment_id
      LEFT JOIN registry_people p ON e.person_key LIKE 'm:%' AND p.id=CAST(substr(e.person_key,3) AS INTEGER)"""
    where, args = ['1=1'], []
    from .permissions import allowed_company, scope
    if user is not None and scope(user) != 'ALL':
        db.create_function('v3_company_allowed', 2, lambda key, name: int(allowed_company(db, user, company_key=key or '', name=name or '')))
        where.append('v3_company_allowed(access_company_key,company)=1')
    scoped = '('+base+') WHERE '+' AND '.join(where)
    if search:
        where.append("instr(lower(coalesce(name,'')||' '||coalesce(registration,'')||' '||coalesce(cpf,'')||' '||coalesce(company,'')),lower(?))>0"); args.append(search)
    for field, value, op in (('company',company,'='),('dismissal_date',date_from,'>='),('dismissal_date',date_to,'<=')):
        if value: where.append(field+op+'?'); args.append(value)
    if reason:
        try:
            reason_identity = json.loads(reason)
        except (TypeError, ValueError):
            reason_identity = None
        if (isinstance(reason_identity, list) and len(reason_identity) == 2
                and all(isinstance(value, str) for value in reason_identity)):
            where.append('(reason_code=? AND reason_label=?)')
            args.extend(reason_identity)
        else:
            # Accept older clients that still send only the source reason code.
            where.append('reason_code=?')
            args.append(reason)
    filtered = '('+base+') WHERE '+' AND '.join(where)
    stats = db.execute("SELECT COUNT(*) total,SUM(status='CONCLUIDO') completed,SUM(status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND source_review=0) pending,SUM(source_review=1) review,SUM(status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date<?) overdue,SUM(status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date BETWEEN ? AND ?) urgent,SUM(status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date BETWEEN ? AND ?) noPrazo FROM "+filtered, (today.isoformat(),today.isoformat(),(today+timedelta(days=2)).isoformat(),(today+timedelta(days=3)).isoformat(),(today+timedelta(days=10)).isoformat(),*args)).fetchone()
    clauses = {'CONCLUIDOS':"status='CONCLUIDO'", 'PENDENTES':"status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND source_review=0", 'CONFERIR':"source_review=1", 'VENCIDOS':"status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date<'"+today.isoformat()+"'", 'URGENTES':"status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date BETWEEN '"+today.isoformat()+"' AND '"+(today+timedelta(days=2)).isoformat()+"'", 'NO_PRAZO':"status NOT IN ('CONCLUIDO','CANCELADO') AND source_review=0 AND deadline_date BETWEEN '"+(today+timedelta(days=3)).isoformat()+"' AND '"+(today+timedelta(days=10)).isoformat()+"'"}
    if phase in clauses: filtered += ' AND ('+clauses[phase]+')'
    total = db.execute('SELECT COUNT(*) FROM '+filtered,args).fetchone()[0]
    page = max(1,min(int(page),max(1,(total+page_size-1)//page_size)))
    ids = db.execute("SELECT id FROM "+filtered+" ORDER BY CASE WHEN source_review=1 THEN 1 WHEN status IN ('CONCLUIDO','CANCELADO') THEN 2 ELSE 0 END,deadline_date ASC,id ASC LIMIT ? OFFSET ?", (*args,page_size,(page-1)*page_size)).fetchall()
    cases = []
    for selected in ids:
        item = _case_row(db, selected[0])
        days = (date.fromisoformat(item['deadline_date']) - today).days
        item['days_remaining'] = days
        counts = db.execute("SELECT SUM(mandatory=1 AND status='PENDENTE') pending,SUM(status IN ('ANEXADO','ENTREGUE')) attached FROM local_rescission_requirements WHERE case_id=?"+(" AND code='TRCT'" if item['is_test'] else ''), (item['id'],)).fetchone()
        item['pending_documents'],item['attached_documents'] = int(counts[0] or 0),int(counts[1] or 0)
        item['real_delivery_count'] = db.execute("SELECT COUNT(*) FROM local_rescission_real_delivery_events WHERE case_id=? AND status='ENVIADO'",(item['id'],)).fetchone()[0]
        item['delivery_count'] = item['real_delivery_count']+db.execute("SELECT COUNT(*) FROM local_rescission_delivery_events WHERE case_id=? AND status='SIMULADO'",(item['id'],)).fetchone()[0]
        item['deadline_state'] = 'VENCIDO' if days<0 else 'URGENTE' if days<=2 else 'NO_PRAZO'
        item['operational_phase'] = 'CONFERIR' if item['source_review'] else item['status'] if item['status'] in ('CONCLUIDO','CANCELADO') else item['deadline_state']
        cases.append(item)
    if user is not None:
        from .permissions import can
        for item in cases:
            if not can(user, 'sensitive.view'):
                cpf = re.sub(r'\D', '', str(item.get('cpf') or ''))
                item['cpf'] = '***.'+cpf[3:6]+'.***-**' if len(cpf)==11 else 'Não disponível'
                item.pop('source_snapshot_json', None)
    zpro = db.execute('SELECT active,api_token_encrypted FROM local_zpro_settings WHERE id=1').fetchone()
    from .sib_operations import summary
    return {'zproConfigured':bool(zpro and zpro[0] and zpro[1]),'today':today.isoformat(),'items':cases,'total':total,'page':page,'pageSize':page_size,
            'summary':{key:int(stats[key] or 0) for key in stats.keys()},'sync':summary(db),
            'companies':[row[0] for row in db.execute('SELECT DISTINCT company FROM '+scoped+" AND company<>'' ORDER BY company COLLATE NOCASE")],
            'reasons':[{'code':row[0],'label':row[1]} for row in db.execute("SELECT DISTINCT reason_code,reason_label FROM "+scoped+" AND reason_code<>'' ORDER BY reason_label")]}


def get_case(db, case_id: int, today: date, *, user=None) -> dict | None:
    item = _case_row(db, case_id)
    if not item:
        return None
    if user is not None:
        from .permissions import case_allowed
        if not case_allowed(db, user, item):
            raise PermissionError('Esta rescisão está fora do seu recorte de acesso.')
    item["days_remaining"] = (date.fromisoformat(item["deadline_date"]) - today).days
    item["requirements"] = [dict(row) for row in db.execute(
        "SELECT * FROM local_rescission_requirements WHERE case_id=? ORDER BY id", (case_id,)
    )]
    person_id, source_person_key = _document_owner(item)
    item["available_documents"] = [dict(row) for row in db.execute(
        "SELECT id,filename,mime_type,size_bytes,created_at FROM local_registry_documents WHERE ((person_id=? AND ? IS NOT NULL) OR (source_person_key=? AND ?<>'')) AND archived_at='' ORDER BY id DESC",
        (person_id, person_id, source_person_key, source_person_key),
    )]
    deliveries = [dict(row) for row in db.execute(
        "SELECT * FROM local_rescission_delivery_events WHERE case_id=? ORDER BY id DESC", (case_id,)
    )]
    for row in deliveries:
        try:
            row["documents"] = json.loads(row.pop("documents_json") or "[]")
        except ValueError:
            row["documents"] = []
        row["is_simulated"] = True
    real_deliveries = [dict(row) for row in db.execute(
        "SELECT * FROM local_rescission_real_delivery_events WHERE case_id=? ORDER BY id DESC", (case_id,)
    )]
    for row in real_deliveries:
        row["id"] = f"REAL-{row['id']}"
        row["is_simulated"] = False
        row.pop("request_key", None)
        row.pop('evidence_json', None)
        row.pop('message', None)
        try:
            row["documents"] = json.loads(row.pop("documents_json") or "[]")
        except ValueError:
            row["documents"] = []
    deliveries.extend(real_deliveries)
    deliveries.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    item["deliveries"] = deliveries
    events = [dict(row) for row in db.execute(
        "SELECT e.*,u.name actor FROM local_rescission_events e JOIN users u ON u.id=e.actor_id WHERE e.case_id=? ORDER BY e.id DESC LIMIT 60",
        (case_id,),
    )]
    for event in events:
        try:
            event["details"] = json.loads(event.pop("details_json") or "{}")
        except ValueError:
            event["details"] = {}
    item["events"] = events
    item['receipt_confirmed'] = any(row.get('receipt_at') for row in real_deliveries)
    item['communication_state'] = 'RECEBIMENTO_CONFIRMADO' if item['receipt_confirmed'] else 'ENVIO_ACEITO' if any(row['status'] == 'ENVIADO' for row in real_deliveries) else 'PENDENTE'
    if user is not None:
        from .permissions import can
        if not can(user, 'documents.view'):
            item['available_documents'] = []
        if not can(user, 'sensitive.view'):
            cpf = re.sub(r'\D', '', str(item.get('cpf') or ''))
            item['cpf'] = '***.' + cpf[3:6] + '.***-**' if len(cpf) == 11 else 'Não disponível'
            item.pop('source_snapshot_json', None)
    item["deadline_state"] = "VENCIDO" if item["days_remaining"] < 0 else "URGENTE" if item["days_remaining"] <= 2 else "NO_PRAZO"
    return item


def upload_document(db, case_id: int, requirement_id: int, actor_id: int, filename: str,
                    mime_type: str, raw: bytes, storage_dir: Path) -> dict:
    case = _case_row(db, case_id)
    if not case:
        raise LookupError("Rescisão não encontrada.")
    if case["status"] in ("CONCLUIDO", "CANCELADO"):
        raise ValueError("Este caso já foi encerrado e não aceita novos documentos.")
    _source_ready(db, case)
    person_id, source_person_key = _document_owner(case)
    if not person_id and not source_person_key:
        raise ValueError("O cadastro local da pessoa não está disponível para guardar o documento.")
    requirement = db.execute(
        "SELECT id,code FROM local_rescission_requirements WHERE id=? AND case_id=?",
        (int(requirement_id), int(case_id)),
    ).fetchone()
    if not requirement:
        raise LookupError("Item do checklist não encontrado.")
    name, mime = _validate_document(filename, mime_type, raw)
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_key = secrets.token_hex(24)
    target = storage_dir / storage_key
    try:
        with target.open("xb") as output:
            output.write(raw)
        cursor = db.execute(
            """INSERT INTO local_registry_documents(person_id,source_person_key,storage_key,filename,mime_type,size_bytes,sha256,uploaded_by)
               VALUES(?,?,?,?,?,?,?,?)""",
            (person_id, source_person_key, storage_key, name, mime, len(raw), hashlib.sha256(raw).hexdigest(), actor_id),
        )
        document_id = int(cursor.lastrowid)
        db.execute(
            """UPDATE local_rescission_requirements SET status='ANEXADO',document_name=?,registry_document_id=?,
               exception_reason='',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (name, document_id, actor_id, int(requirement_id)),
        )
        _set_case_status(db, case_id, actor_id)
        _event(db, case_id, "DOCUMENTO_IMPORTADO_E_VINCULADO", actor_id,
               requirement_id=int(requirement_id), document_id=document_id, filename=name,
               sha256=hashlib.sha256(raw).hexdigest())
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {"ok": True, "case": get_case(db, case_id, date.today()),
            "message": "Arquivo salvo no prontuário da pessoa e vinculado ao item da rescisão."}


def get_registry_document(db, document_id: int, storage_dir: Path):
    row = db.execute("SELECT * FROM local_registry_documents WHERE id=?", (int(document_id),)).fetchone()
    if not row:
        raise LookupError("Documento do prontuário não encontrado.")
    root = storage_dir.resolve()
    target = (storage_dir / row["storage_key"]).resolve()
    if target.parent != root or not target.is_file():
        raise LookupError("Arquivo do documento indisponível.")
    return dict(row), target


def get_email_delivery_payload(db, case_id: int, requirement_ids: set[int], storage_dir: Path):
    """Resolve only real, case-linked prontuário files for a real email delivery."""
    case = _case_row(db, case_id)
    if not case:
        raise LookupError("Rescisão não encontrada.")
    if case["status"] == "CANCELADO":
        raise ValueError("Este caso foi cancelado e não aceita envio.")
    if not requirement_ids or len(requirement_ids) > 20:
        raise ValueError("Selecione de 1 a 20 documentos para o e-mail.")
    _source_ready(db, case)
    person_id, source_person_key = _document_owner(case)
    if not person_id and not source_person_key:
        raise ValueError("O cadastro local desta pessoa não está disponível.")
    placeholders = ",".join("?" for _ in requirement_ids)
    rows = [dict(row) for row in db.execute(
        f"""SELECT q.id,q.status,q.document_name,q.registry_document_id
            FROM local_rescission_requirements q
            WHERE q.case_id=? AND q.id IN ({placeholders})""",
        (case_id, *sorted(requirement_ids)),
    )]
    if len(rows) != len(requirement_ids):
        raise ValueError("Um dos itens selecionados não pertence a esta rescisão.")
    attachments = []
    document_names = []
    for requirement in rows:
        if requirement["status"] not in ("ANEXADO", "ENTREGUE"):
            raise ValueError("Só é possível enviar documentos anexados ao checklist.")
        if not requirement["registry_document_id"]:
            raise ValueError(f"{requirement['document_name'] or 'Um item selecionado'} não tem arquivo real no prontuário. Reimporte o documento antes de enviar.")
        document, target = get_registry_document(db, int(requirement["registry_document_id"]), storage_dir)
        if document.get('archived_at'):
            raise ValueError('Um dos documentos foi arquivado. Restaure-o antes de enviar.')
        if (person_id is not None and document["person_id"] != person_id) or (source_person_key and document["source_person_key"] != source_person_key):
            raise ValueError("Um dos arquivos selecionados não pertence ao prontuário desta pessoa.")
        content = target.read_bytes()
        attachments.append({
            "filename": str(document["filename"]),
            "mime_type": str(document["mime_type"]),
            "content": content,
        })
        document_names.append(str(document["filename"]))
    return case, document_names, attachments


def mutate(db, payload: dict, actor_id: int) -> dict:
    action = str(payload.get("action") or "").strip()
    try:
        case_id = int(payload.get("caseId") or 0)
    except (TypeError, ValueError):
        case_id = 0
    if action == "create_demo":
        return create_demo(db, actor_id, date.fromisoformat(str(payload.get("today") or date.today().isoformat())))
    if action == 'confirm_source':
        item = _case_row(db, case_id)
        note = ' '.join(str(payload.get('reason') or '').split())[:500]
        if not item or item['source_system'] != 'SIB' or not item['source_review']:
            raise ValueError('Não há alteração de origem pendente neste caso.')
        _source_not_pending(db, item)
        snapshot = json.loads(item['source_snapshot_json'])
        from .sib_operations import identity
        if identity(db, str(snapshot.get('employmentKey') or '')[4:], snapshot.get('admission_date') or '') != item['source_id']:
            raise ValueError('A conexão SIB mudou. A identidade anterior não pode ser liberada nesta origem.')
        if snapshot.get('sourceState') != 'VALID' or not snapshot.get('termination_date'):
            raise ValueError('A origem ainda não confirma o desligamento. Aguarde a correção do SIB.')
        if len(note) < 8:
            raise ValueError('Registre uma justificativa de conferência com pelo menos 8 caracteres.')
        db.execute("UPDATE local_rescission_cases SET source_review=0,source_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        _event(db, case_id, 'SIB_ALTERACAO_CONFERIDA', actor_id, note=note)
        return {'ok': True, 'case': get_case(db, case_id, date.today()), 'message': 'Conferência da origem registrada.'}
    if action in ("attach_demo", "link_document", "dispense"):
        req_id = int(payload.get("requirementId") or 0)
        requirement = db.execute("SELECT * FROM local_rescission_requirements WHERE id=? AND case_id=?", (req_id, case_id)).fetchone()
        if not requirement:
            raise LookupError("Documento do checklist não encontrado.")
        active_case = _case_row(db, case_id)
        if not active_case:
            raise LookupError("Rescisão não encontrada.")
        _source_ready(db, active_case)
        if active_case["status"] in ("CONCLUIDO", "CANCELADO"):
            raise ValueError("Este caso já foi encerrado e não aceita alteração de documentos.")
        if action == "attach_demo":
            if not active_case["is_test"]:
                raise ValueError("Anexos simulados são permitidos somente em casos de teste. Anexe um documento real neste caso.")
            name = str(payload.get("documentName") or "Documento de demonstração.pdf").strip()[:180]
            if not name:
                raise ValueError("Informe um nome para o documento de demonstração.")
            db.execute("UPDATE local_rescission_requirements SET status='ANEXADO',document_name=?,exception_reason='',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (name, actor_id, req_id))
            _event(db, case_id, "DOCUMENTO_ANEXADO_SIMULADO", actor_id, requirement_id=req_id, document_name=name)
        elif action == "link_document":
            document_id = int(payload.get("documentId") or 0)
            _source_ready(db, active_case)
            person_id, source_person_key = _document_owner(active_case)
            document = db.execute(
                "SELECT id,filename FROM local_registry_documents WHERE id=? AND ((person_id=? AND ? IS NOT NULL) OR (source_person_key=? AND ?<>''))",
                (document_id, person_id, person_id, source_person_key, source_person_key),
            ).fetchone()
            if not document:
                raise LookupError("Este documento não pertence ao prontuário da pessoa desta rescisão.")
            db.execute("""UPDATE local_rescission_requirements SET status='ANEXADO',document_name=?,registry_document_id=?,
                         exception_reason='',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                       (document["filename"], document_id, actor_id, req_id))
            _set_case_status(db, case_id, actor_id)
            _event(db, case_id, "DOCUMENTO_VINCULADO_DO_PRONTUARIO", actor_id,
                   requirement_id=req_id, document_id=document_id, filename=document["filename"])
        else:
            reason = " ".join(str(payload.get("reason") or "").split())[:500]
            if len(reason) < 8:
                raise ValueError("Escreva uma justificativa de pelo menos 8 caracteres para dispensar o documento.")
            db.execute("UPDATE local_rescission_requirements SET status='DISPENSADO',document_name='',registry_document_id=NULL,exception_reason=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (reason, actor_id, req_id))
            _event(db, case_id, "DOCUMENTO_DISPENSADO", actor_id, requirement_id=req_id, reason=reason)
        _set_case_status(db, case_id, actor_id)
    elif action == "deliver_simulated":
        case = _case_row(db, case_id)
        if not case:
            raise LookupError("Rescisão não encontrada.")
        if not case["is_test"]:
            raise ValueError("Entregas simuladas são permitidas somente em casos de teste. Use o envio real neste caso.")
        if case["status"] == "CANCELADO":
            raise ValueError("Reabra ou atualize o caso antes de registrar uma entrega simulada.")
        channel = str(payload.get("channel") or "").upper()
        if channel not in CHANNELS:
            raise ValueError("Selecione e-mail, WhatsApp ou ambos.")
        message = str(payload.get("message") or "").strip()[:5000]
        if not message:
            raise ValueError("Escreva a mensagem de comunicação antes de registrar a simulação.")
        selected = payload.get("documentIds") or []
        try:
            selected_ids = {int(value) for value in selected}
        except (TypeError, ValueError):
            raise ValueError("Selecione documentos válidos do checklist.")
        attached = {int(row["id"]): row["document_name"] for row in db.execute(
            "SELECT id,document_name FROM local_rescission_requirements WHERE case_id=? AND status IN ('ANEXADO','ENTREGUE')",
            (case_id,),
        )}
        if selected_ids - attached.keys():
            raise ValueError("Só é possível incluir documentos anexados a este caso.")
        targets = {
            "EMAIL": str(case.get("email") or "Não informado (simulação)"),
            "WHATSAPP": str(case.get("whatsapp") or "Não informado (simulação)"),
        }
        channels = ("EMAIL", "WHATSAPP") if channel == "AMBOS" else (channel,)
        documents = [attached[value] for value in sorted(selected_ids)]
        now = datetime.now().isoformat(timespec="seconds")
        created = []
        for delivery_channel in channels:
            protocol = f"SIM-RSC-{case_id}-{secrets.token_hex(3).upper()}-{delivery_channel}"
            cursor = db.execute(
                """INSERT INTO local_rescission_delivery_events(case_id,channel,status,target,message,documents_json,protocol,is_simulated,created_by,created_at)
                   VALUES(?,?,'SIMULADO',?,?,?,?,1,?,?)""",
                (case_id, delivery_channel, targets[delivery_channel], message,
                 json.dumps(documents, ensure_ascii=False), protocol, actor_id, now),
            )
            created.append({"id": cursor.lastrowid, "channel": delivery_channel, "protocol": protocol})
        if selected_ids:
            db.executemany("UPDATE local_rescission_requirements SET status='ENTREGUE',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           [(actor_id, value) for value in selected_ids])
        db.execute("UPDATE local_rescission_cases SET status='CONCLUIDO',updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        _event(db, case_id, "ENTREGA_SIMULADA", actor_id, channel=channel, deliveries=created, documents=documents,
               message=message, notice="Nenhuma mensagem ou arquivo foi enviado para fora da V3.")
    elif action == "cancel_case":
        if not _case_row(db, case_id):
            raise LookupError("Rescisão não encontrada.")
        db.execute("UPDATE local_rescission_cases SET status='CANCELADO',updated_at=CURRENT_TIMESTAMP WHERE id=?", (case_id,))
        _event(db, case_id, "CASO_CANCELADO", actor_id)
    else:
        raise ValueError("Ação de rescisão desconhecida.")
    return {"ok": True, "case": get_case(db, case_id, date.today()), "message": "Ação registrada no histórico da rescisão."}


def create_demo(db, actor_id: int, today: date) -> dict:
    examples = [
        ("Pessoa Exemplo 01", "00000000000", "TESTE-RSC-01", 1, "PEDIDO_EMPREGADO", "A pedido do empregado", "CLT", "demo@example.com", "PENDENTE"),
        ("Pessoa Exemplo 02", "00000000000", "TESTE-RSC-02", 0, "SEM_JUSTA_CAUSA", "Dispensa sem justa causa", "CLT", "demo@example.com", "PENDENTE"),
        ("Pessoa Exemplo 03", "00000000000", "TESTE-RSC-03", -2, "TERMINO_CONTRATO", "Término de contrato", "Temporário", "demo@example.com", "CONCLUIDO"),
        ("Pessoa Exemplo 04", "00000000000", "TESTE-RSC-04", 3, "ACORDO", "Rescisão por acordo", "CLT", "demo@example.com", "PENDENTE"),
        ("Pessoa Exemplo 05", "00000000000", "TESTE-RSC-05", -2, "PEDIDO_EMPREGADO", "A pedido do empregado", "CLT", "demo@example.com", "PENDENTE"),
    ]
    created = 0
    for name, cpf, registration, remaining_to_deadline, code, reason, contract, email, target_status in examples:
        existing = db.execute(
            "SELECT id FROM registry_employments WHERE registration=? AND is_test=1", (registration,)
        ).fetchone()
        if existing:
            continue
        dismissal = today + timedelta(days=remaining_to_deadline - 10)
        admission = dismissal - timedelta(days=180)
        person_id = int(db.execute(
            "INSERT INTO registry_people(name,cpf,email,whatsapp,is_test,created_by) VALUES(?,?,?,?,1,?)",
            (name, cpf, email, "", actor_id),
        ).lastrowid)
        person_key = f"m:{person_id}"
        employment_id = int(db.execute(
            """INSERT INTO registry_employments(person_key,registration,company,unit,contract_type,admission_date,
               termination_date,dismissal_reason_code,dismissal_reason,is_test,created_by)
               VALUES(?,?,?,'Unidade de demonstração',?,?,?,?,?,1,?)""",
            (person_key, registration, "Empresa de teste local", contract, admission.isoformat(), dismissal.isoformat(), code, reason, actor_id),
        ).lastrowid)
        case_id = ensure_case(db, employment_id, actor_id, is_test=True)
        created += 1
        if target_status == "CONCLUIDO":
            rows = db.execute("SELECT id,code FROM local_rescission_requirements WHERE case_id=?", (case_id,)).fetchall()
            attached_ids = []
            for req in rows:
                req_id, req_code = int(req["id"]), str(req["code"])
                if req_code in {"EXAME_DEMISSIONAL", "SEGURO_DESEMPREGO"}:
                    continue
                db.execute("UPDATE local_rescission_requirements SET status='ANEXADO',document_name=?,updated_by=? WHERE id=?",
                           (f"{req_code.lower()}-demonstracao.pdf", actor_id, req_id))
                attached_ids.append(req_id)
            mutate(db, {"action": "deliver_simulated", "caseId": case_id, "channel": "EMAIL",
                        "documentIds": attached_ids, "message": "Esta é uma entrega de demonstração do fluxo de rescisão."}, actor_id)
    total = int(db.execute("SELECT count(*) FROM local_rescission_cases WHERE is_test=1").fetchone()[0])
    message = (
        f"{created} novo(s) exemplo(s) criado(s); agora há {total} caso(s) de teste. "
        "Documentos e entrega são simulados; nada foi enviado."
        if created else f"Os exemplos já estão cadastrados ({total} caso(s) de teste)."
    )
    return {"ok": True, "created": created, "message": message}
