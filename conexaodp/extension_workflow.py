"""Reviewed SIB TPR workflow. Provider writes happen only after an explicit action."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta

from . import clicksign
from .database import connect


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS local_extension_workflows (
        extension_id INTEGER PRIMARY KEY REFERENCES local_temporary_extensions(id),
        config_key TEXT NOT NULL, prepared_json TEXT NOT NULL, source_hash TEXT NOT NULL,
        prepared_hash TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '',
        representative_signer_id TEXT NOT NULL DEFAULT '', employee_signer_id TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT 'PREPARED', error TEXT NOT NULL DEFAULT '',
        provider_json TEXT NOT NULL DEFAULT '{}', archived_document_id INTEGER,
        evidence_document_id INTEGER, archived_at TEXT NOT NULL DEFAULT '',
        last_checked_at REAL NOT NULL DEFAULT 0, next_check_at REAL NOT NULL DEFAULT 0,
        lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
        prepared_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE IF NOT EXISTS local_extension_events (
        id INTEGER PRIMARY KEY, extension_id INTEGER NOT NULL REFERENCES local_temporary_extensions(id),
        action TEXT NOT NULL, actor_id INTEGER REFERENCES users(id), details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE IF NOT EXISTS local_extension_webhook_events (
        id INTEGER PRIMARY KEY, config_key TEXT NOT NULL, event_key TEXT NOT NULL,
        envelope_id TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(config_key,event_key))""")
    db.execute("CREATE INDEX IF NOT EXISTS extension_workflow_due ON local_extension_workflows(next_check_at,lease_until)")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _envelope_name(context, first):
    return f"TPR {context['reference']} - {first} - {context['contract']['person_name']}"[:180]


def _event(db, extension_id, action, actor_id=None, **details):
    db.execute("INSERT INTO local_extension_events(extension_id,action,actor_id,details_json) VALUES(?,?,?,?)",
               (extension_id, action, actor_id, _json(details)))


def _text(value, limit=180):
    return " ".join(str(value or "").split())[:limit]


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _email(value):
    value = str(value or "").strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Informe um e-mail válido para cada signatário; ambos recebem o PDF final.")
    return value


def _cnpj(value):
    value = _digits(value)
    if len(value) != 14 or len(set(value)) == 1:
        return False
    for length, weights in ((12, (5,4,3,2,9,8,7,6,5,4,3,2)), (13, (6,5,4,3,2,9,8,7,6,5,4,3,2))):
        remainder = sum(int(digit) * weight for digit, weight in zip(value[:length], weights)) % 11
        if int(value[length]) != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


def _keys(payload):
    if payload.get("sourceSystem") != "SIB":
        raise ValueError("Este fluxo atende somente as prorrogações SIB.")
    key, first = str(payload.get("sourceId") or "")[:180], str(payload.get("firstDueDate") or "")[:10]
    parts = key.split(":")
    if len(parts) != 5 or not re.fullmatch(r"[a-f0-9]{16}", parts[0]) or not all(re.fullmatch(r"-?\d{1,10}", item) for item in parts[1:4]):
        raise ValueError("A identidade da admissão SIB não foi confirmada.")
    try:
        date.fromisoformat(parts[4]); date.fromisoformat(first)
    except ValueError:
        raise ValueError("As datas do vínculo SIB são inválidas.") from None
    return key, first, "sib:" + ":".join(parts[1:4])


def _contract(db, key, first, *, current=True):
    row = db.execute("SELECT * FROM local_temporary_contracts WHERE source_system='SIB' AND source_id=?", (key,)).fetchone()
    if not row:
        raise LookupError("Contrato SIB não encontrado.")
    contract = dict(row)
    if current:
        snapshot = db.execute("SELECT source_id FROM local_sib_operation_snapshots WHERE json_extract(snapshot_json,'$.identity')=?", (key,)).fetchone()
        if not snapshot or db.execute("SELECT 1 FROM local_sib_operation_jobs WHERE source_id=?", (snapshot[0],)).fetchone():
            raise ValueError("A ficha SIB tem uma atualização pendente. Aguarde antes de preparar ou enviar.")
        from .sib_operations import identity
        if identity(db, snapshot[0], contract["admission_date"]) != key:
            raise ValueError("A conexão SIB mudou. Confira a identidade do vínculo.")
        today = date.today()
        days = (date.fromisoformat(first) - today).days
        if (not contract["active"] or contract["first_due_date"] != first or "TEMPOR" not in contract["contract_type"].upper()
                or contract["source_state"] == "REVIEW" or contract["second_due_date"] or not 0 <= days <= 30):
            raise ValueError("A preparação e o envio exigem temporário ativo, sem segundo vencimento, na janela de 30 dias.")
    return contract


def _source_hash(contract):
    fields = ("source_id", "person_name", "cpf", "registration", "company_key", "company_name", "contract_type",
              "admission_date", "first_due_date", "second_due_date", "active", "source_state", "email", "whatsapp",
              "tomadora_name", "tomadora_cnpj", "tomadora_address", "worker_function", "work_location", "complementary_demand")
    return _hash({name: contract.get(name) for name in fields})


def _configuration(db, company_key, *, fixed_key=None, environment=None):
    if fixed_key:
        row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (fixed_key,)).fetchone()
    else:
        row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (company_key,)).fetchone()
        if not row:
            row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key='__global__'").fetchone()
    if not row or not row["active"]:
        raise ValueError("Ative a conexão Clicksign aplicável a esta empresa em Assinaturas.")
    config = dict(row)
    if environment and config["environment"] != environment:
        raise ValueError("O ambiente Clicksign mudou. Restaure o ambiente do envelope antes de consultá-lo.")
    token = clicksign.unprotect_secret(config["access_token_protected"]) if config["access_token_protected"] else os.environ.get("CLICKSIGN_API_TOKEN", "").strip()
    if not token:
        raise ValueError("Cadastre o Access Token Clicksign na conexão aplicável.")
    return config, token


def _authorize(db, user, employment_key):
    if user is not None:
        from .permissions import can, require_subject
        if not can(user, "extensions.manage"):
            raise PermissionError("Seu perfil não permite operar prorrogações.")
        require_subject(db, user, "pessoas", employment_key)


def prepare(db, payload, actor_id, *, user=None):
    key, first, employment_key = _keys(payload)
    _authorize(db, user, employment_key)
    contract = _contract(db, key, first)
    previous = db.execute("SELECT e.*,w.stage,w.lease_until FROM local_temporary_extensions e LEFT JOIN local_extension_workflows w ON w.extension_id=e.id WHERE e.source_system='SIB' AND e.source_id=? AND e.first_due_date=?", (key, first)).fetchone()
    if previous and (previous["lease_until"] or 0) > time.time():
        raise ValueError("Há uma ação em andamento neste pacote. Aguarde a atualização.")
    if previous and (previous["status"] == "SIGNED" or (previous["envelope_id"] and previous["status"] != "CANCELLED")):
        raise ValueError("Já existe envelope. Consulte ou cancele o pacote antes de preparar outro.")
    if previous and previous["stage"] in ("CREATING_ENVELOPE", "OUTCOME_UNKNOWN"):
        raise ValueError("A criação anterior tem resultado incerto. Confira o envelope na Clicksign antes de liberar uma nova tentativa.")
    config, _ = _configuration(db, contract["company_key"])
    sender = payload.get("representative") if isinstance(payload.get("representative"), dict) else {}
    signer_id = str(payload.get("signerId") or "")
    if signer_id:
        if not signer_id.isdigit():
            raise ValueError("Selecione um representante válido.")
        saved = db.execute("SELECT * FROM local_clicksign_signers WHERE id=? AND active=1 AND company_key IN (?, '__global__')", (int(signer_id), contract["company_key"])).fetchone()
        if not saved:
            raise ValueError("O representante não está autorizado nesta empresa.")
        sender = {"name": saved["name"], "cpf": saved["cpf"], "role": saved["role"], "email": saved["email"],
                  "phone": saved["whatsapp"], "channel": saved["channel"], "birthday": saved["birth_date"]}
    representative = {name: _text(sender.get(name), 300 if name == "email" else 180) for name in ("name", "cpf", "role", "email", "phone", "channel", "birthday")}
    representative["cpf"] = _digits(representative["cpf"])
    representative["phone"] = _digits(representative["phone"])
    representative["channel"] = representative["channel"].upper()
    representative["email"] = _email(representative["email"])
    if len(representative["name"].split()) < 2 or not clicksign.valid_cpf(representative["cpf"]) or not representative["role"]:
        raise ValueError("Informe nome completo, CPF válido e cargo do representante da empregadora.")
    if representative["birthday"]:
        try:
            birth = date.fromisoformat(representative["birthday"])
            if birth >= date.today(): raise ValueError()
        except ValueError:
            raise ValueError("O nascimento do representante é inválido.") from None
    if payload.get("authorizedRepresentative") is not True:
        raise ValueError("Confirme que o representante está autorizado a assinar pela empregadora.")
    worker_channel = str(payload.get("workerChannel") or "").upper()
    if worker_channel not in ("EMAIL", "WHATSAPP") or representative["channel"] not in ("EMAIL", "WHATSAPP"):
        raise ValueError("Escolha e-mail ou WhatsApp para cada convite.")
    if config["allowed_channels"] != "BOTH" and any(value != config["allowed_channels"] for value in (worker_channel, representative["channel"])):
        raise ValueError("Os canais escolhidos não são permitidos pela conexão Clicksign.")
    if not clicksign.valid_cpf(_digits(contract["cpf"])):
        raise ValueError("Corrija o CPF do trabalhador na origem antes de preparar o termo.")
    worker = {"name": contract["person_name"], "cpf": _digits(contract["cpf"]),
              "email": _email(payload.get("workerEmail") or contract["email"]), "phone": _digits(payload.get("workerPhone") or contract["whatsapp"])}
    if worker["cpf"] == representative["cpf"]:
        raise ValueError("O trabalhador e o representante da empregadora precisam ser signatários distintos.")
    if any(channel == "WHATSAPP" and len(party["phone"]) not in (10, 11) for channel, party in ((worker_channel, worker), (representative["channel"], representative))):
        raise ValueError("O convite por WhatsApp exige um telefone com DDD válido.")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    supplements = ("employer_name", "employer_cnpj", "employer_address", "worker_function", "work_location", "complementary_demand", "tomadora_name", "tomadora_cnpj")
    approved = {**contract}
    for name in supplements:
        # Source values remain intact; this approved snapshot records explicit local completion.
        approved[name] = _text(fields.get(name) or contract.get(name), 400 if name in ("employer_address", "work_location", "complementary_demand") else 180)
    if any(not approved[name] for name in supplements):
        raise ValueError("Complete os dados da empregadora, função, tomadora, local e demanda antes de gerar o TPR.")
    if not all(_cnpj(approved[name]) for name in ("employer_cnpj", "tomadora_cnpj")):
        raise ValueError("Informe CNPJs válidos para empregadora e tomadora.")
    from .registry_documents import resolve_owner
    owner = resolve_owner(db, "pessoas", employment_key, user=None)
    if owner["sourcePersonKey"] != "sib:" + key:
        raise ValueError("A admissão mudou. Reabra a ficha correta antes de preparar o termo.")
    context = {"contract": approved, "representative": representative, "worker": worker, "workerChannel": worker_channel,
               "signerId": int(signer_id or 0), "owner": owner, "environment": config["environment"],
               "proposedDueDate": (date.fromisoformat(first) + timedelta(days=90)).isoformat(),
               "preparedAt": datetime.now().astimezone().isoformat(timespec="seconds"), "reference": uuid.uuid4().hex[:12].upper(),
               "model": "TPR-39-SIC-v1", "authorizedRepresentative": True}
    from .tpr_document import build_tpr
    pdf = build_tpr(context)
    prepared_hash = hashlib.sha256(pdf).hexdigest()
    db.execute("""INSERT INTO local_temporary_extensions(source_system,source_id,first_due_date,status,signer_name,worker_channel,
          delivery_mode,delivery_environment,sender_channel,sender_name,sender_cpf,sender_email,sender_phone,recipient_email,preview_pdf,prepared_at,provider_status)
        VALUES('SIB',?,?,'PREPARED',?,?,'CLICKSIGN_REAL',?,?,?,?,?,?,?,?,?,'TPR_PREVIA_VALIDADA')
        ON CONFLICT(source_system,source_id,first_due_date) DO UPDATE SET status='PREPARED',envelope_id='',signer_name=excluded.signer_name,
          worker_channel=excluded.worker_channel,delivery_mode='CLICKSIGN_REAL',delivery_environment=excluded.delivery_environment,
          sender_channel=excluded.sender_channel,sender_name=excluded.sender_name,sender_cpf=excluded.sender_cpf,sender_email=excluded.sender_email,
          sender_phone=excluded.sender_phone,recipient_email=excluded.recipient_email,preview_pdf=excluded.preview_pdf,prepared_at=excluded.prepared_at,
          sender_signed_at='',worker_signed_at='',recipient_invited_at='',provider_status='TPR_PREVIA_VALIDADA',updated_at=CURRENT_TIMESTAMP""",
        (key, first, representative["name"], worker_channel, config["environment"], representative["channel"], representative["name"],
         representative["cpf"], representative["email"], representative["phone"], worker["email"], pdf, context["preparedAt"]))
    extension_id = db.execute("SELECT id FROM local_temporary_extensions WHERE source_system='SIB' AND source_id=? AND first_due_date=?", (key, first)).fetchone()[0]
    db.execute("""INSERT INTO local_extension_workflows(extension_id,config_key,prepared_json,source_hash,prepared_hash,prepared_by)
        VALUES(?,?,?,?,?,?) ON CONFLICT(extension_id) DO UPDATE SET config_key=excluded.config_key,prepared_json=excluded.prepared_json,
        source_hash=excluded.source_hash,prepared_hash=excluded.prepared_hash,document_id='',representative_signer_id='',employee_signer_id='',
        stage='PREPARED',error='',provider_json='{}',archived_document_id=NULL,evidence_document_id=NULL,archived_at='',last_checked_at=0,
        next_check_at=0,lease_token='',lease_until=0,prepared_by=excluded.prepared_by,updated_at=CURRENT_TIMESTAMP""",
        (extension_id, config["company_key"], _json(context), _source_hash(contract), prepared_hash, actor_id))
    _event(db, extension_id, "TPR_PREPARADO", actor_id, model=context["model"], reference=context["reference"], pdf_sha256=prepared_hash,
           source_hash=_source_hash(contract), local_fields=[name for name in supplements if approved[name] != contract.get(name)])
    return "TPR preparado. Abra o PDF e confira as partes, o prazo e os canais antes de autorizar o envio."


def _workflow(db, key, first):
    row = db.execute("""SELECT w.*,e.source_id,e.first_due_date,e.status,e.envelope_id,e.preview_pdf,e.delivery_environment,
         e.sender_signed_at,e.worker_signed_at,e.last_notice_at FROM local_extension_workflows w JOIN local_temporary_extensions e ON e.id=w.extension_id
         WHERE e.source_system='SIB' AND e.source_id=? AND e.first_due_date=?""", (key, first)).fetchone()
    if not row:
        raise ValueError("Prepare uma prévia real deste termo antes de continuar.")
    return dict(row)


def _claim(db, workflow, action, actor_id):
    lease, now = uuid.uuid4().hex, time.time()
    changed = db.execute("UPDATE local_extension_workflows SET lease_token=?,lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE extension_id=? AND lease_until<=?",
                         (lease, now + 900, workflow["extension_id"], now))
    if changed.rowcount != 1:
        raise ValueError("Uma operação já está em andamento neste pacote.")
    _event(db, workflow["extension_id"], "CLICKSIGN_" + action.upper() + "_SOLICITADO", actor_id)
    return lease


def _save_progress(database, extension_id, lease, progress):
    with connect(database) as db:
        workflow = db.execute("SELECT provider_json FROM local_extension_workflows WHERE extension_id=? AND lease_token=?", (extension_id, lease)).fetchone()
        if not workflow:
            raise ValueError("A reserva deste envio expirou; confira o envelope antes de continuar.")
        merged = {**json.loads(workflow[0] or "{}"), **progress}
        db.execute("""UPDATE local_extension_workflows SET document_id=?,representative_signer_id=?,employee_signer_id=?,stage=?,provider_json=?,
            lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE extension_id=? AND lease_token=?""",
            (merged.get("document_id", ""), merged.get("representative_signer_id", ""), merged.get("employee_signer_id", ""),
             merged.get("stage", "ENVIANDO"), _json(merged), time.time() + 900, extension_id, lease))
        if merged.get("envelope_id"):
            db.execute("UPDATE local_temporary_extensions SET envelope_id=?,provider_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (merged["envelope_id"], "CLICKSIGN_" + merged.get("stage", "ENVIANDO"), extension_id))


def action(database, payload, actor_id, storage_dir, *, user=None):
    key, first, employment_key = _keys(payload)
    selected = str(payload.get("action") or "")
    if selected == "prepare":
        with connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            return prepare(db, payload, actor_id, user=user)
    if selected not in ("send", "refresh", "remind", "cancel", "register_non_signature", "recover", "recover_creation"):
        raise ValueError("Ação de prorrogação inválida.")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        _authorize(db, user, employment_key)
        if user is not None and selected in ('send', 'remind'):
            from .permissions import can
            if not can(user, 'communications.send'):
                raise PermissionError('Seu perfil não permite enviar convites ou lembretes aos signatários.')
        workflow = _workflow(db, key, first)
        context = json.loads(workflow["prepared_json"])
        config, token = _configuration(db, context["contract"]["company_key"], fixed_key=workflow["config_key"], environment=workflow["delivery_environment"])
        if selected == "send":
            contract = _contract(db, key, first)
            if _source_hash(contract) != workflow["source_hash"]:
                raise ValueError("A ficha SIB mudou após a prévia. Gere e confira outra antes de enviar.")
            if workflow["status"] != "PREPARED" or workflow["stage"] != "PREPARED" or workflow["envelope_id"]:
                raise ValueError("Este pacote já foi enviado ou tem uma tentativa pendente. Consulte o histórico.")
            if payload.get("approvedPreview") is not True or payload.get("previewHash") != workflow["prepared_hash"]:
                raise ValueError("Confirme a revisão do PDF atual antes de enviar.")
            if config["allowed_channels"] != "BOTH" and any(channel != config["allowed_channels"] for channel in (context["workerChannel"], context["representative"]["channel"])):
                raise ValueError("Os canais da conexão mudaram. Gere outra prévia.")
        elif selected == "recover_creation":
            reason = _text(payload.get("reason"), 500)
            if workflow["envelope_id"] or workflow["stage"] not in ("CREATING_ENVELOPE", "OUTCOME_UNKNOWN") or payload.get("confirmedNoEnvelope") is not True or len(reason) < 12:
                raise ValueError("Confira a Clicksign, confirme que não existe envelope e descreva a verificação antes de liberar outra tentativa.")
            if workflow["lease_until"] > time.time():
                raise ValueError("A tentativa ainda pode estar em andamento. Aguarde antes de liberar.")
            db.execute("UPDATE local_extension_workflows SET stage='PREPARED',error='',updated_at=CURRENT_TIMESTAMP WHERE extension_id=?", (workflow["extension_id"],))
            db.execute("UPDATE local_temporary_extensions SET status='PREPARED',provider_status='TPR_LIBERADO_APOS_CONFERENCIA' WHERE id=?", (workflow["extension_id"],))
            _event(db, workflow["extension_id"], "CRIACAO_CONFERIDA_SEM_ENVELOPE", actor_id, reason=reason)
            return "Conferência registrada. Reabra o PDF antes de iniciar uma nova tentativa."
        elif selected != "recover" and not workflow["envelope_id"]:
            raise ValueError("Este pacote ainda não possui um envelope confirmado. Confira a criação anterior.")
        if selected == "remind" and workflow["last_notice_at"]:
            try:
                if (datetime.now().astimezone() - datetime.fromisoformat(workflow["last_notice_at"])).total_seconds() < 60:
                    raise ValueError("Aguarde um minuto entre lembretes para o mesmo pacote.")
            except TypeError:
                pass
        lease = _claim(db, workflow, selected, actor_id)
        if selected == "send":
            db.execute("UPDATE local_extension_workflows SET stage='CREATING_ENVELOPE',error='' WHERE extension_id=?", (workflow["extension_id"],))
            db.execute("UPDATE local_temporary_extensions SET attempts=attempts+1,provider_status='CLICKSIGN_ENVIANDO',updated_at=CURRENT_TIMESTAMP WHERE id=?", (workflow["extension_id"],))
    extension_id, timeout = workflow["extension_id"], int(config["timeout_seconds"])
    try:
        if selected == "send":
            result = clicksign.create_tpr_signature(config["environment"], token,
                name=_envelope_name(context, first), pdf=bytes(workflow["preview_pdf"]), employee=context["worker"],
                representative=context["representative"], employee_channel=context["workerChannel"], timeout=timeout,
                progress_callback=lambda values: _save_progress(database, extension_id, lease, values))
            with connect(database) as db:
                db.execute("UPDATE local_temporary_extensions SET status='AWAITING_SIGNATURE',last_sent_at=?,provider_status='CLICKSIGN_AGUARDANDO_ASSINATURA',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (datetime.now().astimezone().isoformat(timespec="seconds"), extension_id))
                _event(db, extension_id, "TPR_ENVIADO", actor_id, envelope_id=result["envelope_id"], environment=config["environment"])
            return "Envelope criado. O representante assina primeiro; a Clicksign libera o convite ao trabalhador em seguida."
        if selected == "recover":
            identifier = str(payload.get("envelopeId") or "").strip()
            if workflow["envelope_id"] or not re.fullmatch(r"[a-zA-Z0-9-]{8,100}", identifier):
                raise ValueError("Informe o envelope da tentativa anterior para conferir sua identidade.")
            details = clicksign.envelope_details(config["environment"], token, identifier, timeout=timeout)
            signers = clicksign.envelope_signers(config["environment"], token, identifier, timeout=timeout)
            expected = {context["worker"]["cpf"], context["representative"]["cpf"]}
            actual = {_digits((row.get("attributes") or {}).get("documentation")) for row in signers}
            name = str((details.get("data") or {}).get("attributes", {}).get("name") or "")
            if expected - actual or name != _envelope_name(context, first):
                raise ValueError("Nome do pacote ou CPFs dos signatários não correspondem à prévia. O envelope não foi associado.")
            documents = clicksign._api_request(config["environment"], token, f"/envelopes/{identifier}/documents", method="GET", body=None, timeout=timeout).get("data") or []
            if len(documents) != 1:
                raise ValueError("O envelope precisa conter exatamente o documento TPR desta tentativa.")
            progress = {"envelope_id": identifier, "document_id": str(documents[0].get("id") or ""), "stage": "RECOVERED"}
            for row in signers:
                cpf = _digits((row.get("attributes") or {}).get("documentation"))
                if cpf == context["worker"]["cpf"]: progress["employee_signer_id"] = str(row["id"])
                if cpf == context["representative"]["cpf"]: progress["representative_signer_id"] = str(row["id"])
            _save_progress(database, extension_id, lease, progress)
            workflow.update(envelope_id=identifier, document_id=progress["document_id"])
            with connect(database) as db: _event(db, extension_id, "ENVELOPE_RECUPERADO", actor_id, envelope_id=identifier)
        if selected in ("refresh", "recover"):
            return _refresh(database, workflow, config, token, storage_dir, actor_id)
        if selected == "remind":
            signers = clicksign.envelope_signers(config["environment"], token, workflow["envelope_id"], timeout=timeout)
            signed = _signed_signers(signers)
            target = workflow["employee_signer_id"] if workflow["representative_signer_id"] in signed else workflow["representative_signer_id"]
            clicksign.remind_tpr(config["environment"], token, workflow["envelope_id"], target, timeout=timeout)
            with connect(database) as db:
                db.execute("UPDATE local_temporary_extensions SET last_notice_at=?,last_notice_target=? WHERE id=?", (datetime.now().astimezone().isoformat(timespec="seconds"), target, extension_id))
                _event(db, extension_id, "LEMBRETE_ACEITO", actor_id, signer_id=target)
            return "A Clicksign aceitou o lembrete para o signatário cuja etapa está liberada."
        if selected in ("cancel", "register_non_signature"):
            reason = _text(payload.get("reason"), 500)
            if len(reason) < 12:
                raise ValueError("Registre o motivo do cancelamento com pelo menos 12 caracteres.")
            confirmed = clicksign.cancel_tpr(config["environment"], token, workflow["envelope_id"], workflow["document_id"], timeout=timeout)
            with connect(database) as db:
                db.execute("UPDATE local_temporary_extensions SET status='CANCELLED',provider_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", ("CLICKSIGN_" + confirmed.upper(), extension_id))
                db.execute("UPDATE local_extension_workflows SET stage=?,error='' WHERE extension_id=?", ("NON_SIGNATURE" if selected == "register_non_signature" else "CANCELLED", extension_id))
                _event(db, extension_id, "NAO_ASSINATURA_CONFIRMADA" if selected == "register_non_signature" else "PACOTE_CANCELADO", actor_id, reason=reason, envelope_id=workflow["envelope_id"])
                if selected == "register_non_signature":
                    from .tpr_document import build_non_signature
                    from .registry_documents import add_document
                    document = add_document(db, context["owner"], actor_id, f"TPR - Ausência de assinatura - {context['contract']['registration']}.pdf", "application/pdf", build_non_signature(context, workflow["envelope_id"], reason), Path(storage_dir),
                        {"documentType": "PRORROGACAO", "description": reason, "documentDate": date.today().isoformat(), "origin": "CLICKSIGN"})
                    db.execute("UPDATE local_extension_workflows SET evidence_document_id=? WHERE extension_id=?", (document["id"], extension_id))
            return "Cancelamento confirmado. O histórico foi preservado" + (" e a ocorrência foi arquivada no prontuário." if selected == "register_non_signature" else ".")
    except Exception as error:
        with connect(database) as db:
            row = db.execute("SELECT stage FROM local_extension_workflows WHERE extension_id=?", (extension_id,)).fetchone()
            stage = str(row[0]) if row else "ERROR"
            if selected == "send":
                stage = "OUTCOME_UNKNOWN" if stage == "CREATING_ENVELOPE" else "PROVIDER_REVIEW"
                db.execute("UPDATE local_temporary_extensions SET status='ERROR',provider_status='CLICKSIGN_CONFERIR_TENTATIVA',updated_at=CURRENT_TIMESTAMP WHERE id=?", (extension_id,))
            db.execute("UPDATE local_extension_workflows SET error=?,stage=?,next_check_at=? WHERE extension_id=?", (_text(error, 700), stage, time.time()+60, extension_id))
            _event(db, extension_id, "CLICKSIGN_ACAO_PENDENTE", actor_id, operation=selected, error=_text(error, 500))
        raise
    finally:
        with connect(database) as db:
            db.execute("UPDATE local_extension_workflows SET lease_token='',lease_until=0,updated_at=CURRENT_TIMESTAMP WHERE extension_id=? AND lease_token=?", (extension_id, lease))


def _signed_signers(signers):
    return {str(row.get("id")) for row in signers if (row.get("attributes") or {}).get("signed_at") or str((row.get("attributes") or {}).get("status") or "").lower() in ("signed", "closed")}


def _refresh(database, workflow, config, token, storage_dir, actor_id=None):
    extension_id, envelope_id, timeout = workflow["extension_id"], workflow["envelope_id"], int(config["timeout_seconds"])
    context = json.loads(workflow["prepared_json"])
    details = clicksign.envelope_details(config["environment"], token, envelope_id, timeout=timeout)
    provider = str((details.get("data") or {}).get("attributes", {}).get("status") or "").lower()
    if provider not in ("draft", "running", "closed", "canceled"):
        raise ValueError("A Clicksign retornou um estado de envelope ainda não reconhecido. Confira o pacote.")
    signers = clicksign.envelope_signers(config["environment"], token, envelope_id, timeout=timeout)
    signed = _signed_signers(signers)
    refused = any((row.get("attributes") or {}).get("refused_at") or str((row.get("attributes") or {}).get("status") or "").lower() in ("refused", "declined") for row in signers)
    stage = {"draft": "PROVIDER_REVIEW", "running": "AWAITING_SIGNATURE", "closed": "SIGNED", "canceled": "CANCELLED"}[provider]
    if refused and provider != "closed": stage = "REFUSED"
    elif provider == "running" and date.today() > date.fromisoformat(workflow["first_due_date"]): stage = "OVERDUE_SIGNATURE"
    status = {"draft": "ERROR", "running": "AWAITING_SIGNATURE", "closed": "SIGNED", "canceled": "CANCELLED"}[provider]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect(database) as db:
        current = db.execute("SELECT stage FROM local_extension_workflows WHERE extension_id=?", (extension_id,)).fetchone()
        db.execute("""UPDATE local_temporary_extensions SET status=?,provider_status=?,sender_signed_at=CASE WHEN ? THEN CASE WHEN sender_signed_at='' THEN ? ELSE sender_signed_at END ELSE sender_signed_at END,
            worker_signed_at=CASE WHEN ? THEN CASE WHEN worker_signed_at='' THEN ? ELSE worker_signed_at END ELSE worker_signed_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, "CLICKSIGN_" + provider.upper(), workflow["representative_signer_id"] in signed or provider == "closed", now,
             workflow["employee_signer_id"] in signed or provider == "closed", now, extension_id))
        db.execute("UPDATE local_extension_workflows SET stage=?,error='',last_checked_at=?,next_check_at=?,updated_at=CURRENT_TIMESTAMP WHERE extension_id=?",
                   (stage,time.time(),time.time()+max(60,int(config["refresh_minutes"])*60),extension_id))
        if not current or current[0] != stage:
            _event(db, extension_id, "CLICKSIGN_STATUS_CONFIRMADO", actor_id, status=provider, stage=stage)
    if provider == "closed" and not workflow["archived_document_id"]:
        if not workflow["document_id"]:
            raise ValueError("Envelope concluído sem documento identificado. Confira a tentativa antes de arquivar.")
        raw = clicksign.download_signed_tpr(config["environment"], token, envelope_id, workflow["document_id"], timeout=timeout)
        from .registry_documents import add_document
        with connect(database) as db:
            document = add_document(db, context["owner"], actor_id or workflow["prepared_by"], f"TPR assinado - {context['contract']['registration']}.pdf", "application/pdf", raw, Path(storage_dir),
                {"documentType":"PRORROGACAO", "description":f"TPR assinado · envelope {envelope_id} · proposta {context['proposedDueDate']}", "documentDate":date.today().isoformat(),"origin":"CLICKSIGN"})
            db.execute("UPDATE local_extension_workflows SET archived_document_id=?,archived_at=?,error='' WHERE extension_id=?", (document["id"], now, extension_id))
            _event(db, extension_id, "TPR_ASSINADO_ARQUIVADO", actor_id, envelope_id=envelope_id, document_id=document["id"], sha256=hashlib.sha256(raw).hexdigest())
        return "Assinaturas confirmadas e PDF final arquivado no prontuário da admissão. O SIB não foi alterado."
    if provider == "closed": return "Assinaturas confirmadas; o PDF final já está arquivado no prontuário."
    if stage == "OVERDUE_SIGNATURE": return "A assinatura continua pendente após o vencimento. Confira o pacote e registre a providência."
    if stage == "REFUSED": return "A Clicksign registrou recusa de signatário. Confira o histórico e a providência."
    return "Situação confirmada na Clicksign: " + {"draft":"rascunho incompleto", "running":"aguardando assinatura", "canceled":"cancelado"}[provider] + "."


def run_due(database, storage_dir, *, limit=3):
    """Poll saved real SIB envelopes; never creates, reminds or cancels packages."""
    processed = 0
    with connect(database) as db:
        rows = [dict(row) for row in db.execute("""SELECT e.source_id,e.first_due_date,w.extension_id FROM local_extension_workflows w
            JOIN local_temporary_extensions e ON e.id=w.extension_id WHERE e.source_system='SIB' AND e.envelope_id<>''
            AND e.status<>'CANCELLED' AND (e.status<>'SIGNED' OR w.archived_document_id IS NULL)
            AND w.next_check_at<=? AND w.lease_until<=? ORDER BY w.next_check_at LIMIT ?""", (time.time(),time.time(),max(1,min(10,int(limit)))))]
    for item in rows:
        try:
            action(database, {"sourceSystem":"SIB","sourceId":item["source_id"],"firstDueDate":item["first_due_date"],"action":"refresh"}, None, storage_dir)
            processed += 1
        except (ValueError, LookupError, RuntimeError, OSError, sqlite3.Error):
            with connect(database) as db:
                db.execute("UPDATE local_extension_workflows SET next_check_at=? WHERE extension_id=?", (time.time()+120,item["extension_id"]))
    return {"processed":processed}


def process_webhook(db, company_key, raw, content_hmac):
    """Authenticate and deduplicate hints, then verify status through the provider API."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 1_000_000:
        raise ValueError("Evento Clicksign inválido.")
    config = db.execute("SELECT hmac_secret_protected,active FROM local_clicksign_company_settings WHERE company_key=?", (company_key,)).fetchone()
    if not config or not config["active"] or not config["hmac_secret_protected"]:
        raise PermissionError("Webhook Clicksign não configurado.")
    secret = clicksign.unprotect_secret(config["hmac_secret_protected"])
    supplied = str(content_hmac or "").strip().lower().removeprefix("sha256=")
    expected = hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied,expected):
        raise PermissionError("Assinatura do webhook Clicksign inválida.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError,ValueError,RecursionError): raise ValueError("O evento Clicksign não contém JSON válido.") from None
    if not isinstance(payload,dict):
        raise ValueError("O evento Clicksign precisa ser um objeto.")
    def find(value, names):
        pending, visited = [(value,0)], 0
        while pending and visited<2000:
            item, depth = pending.pop()
            visited += 1
            if depth>20: continue
            if isinstance(item,dict):
                for name in names:
                    if name in item and isinstance(item[name],(str,int)): return str(item[name])[:200]
                pending.extend((nested,depth+1) for nested in list(item.values())[:100])
            elif isinstance(item,list):
                pending.extend((nested,depth+1) for nested in item[:100])
        return ''
    envelope = find(payload,('envelope_id','envelope_key'))
    if not envelope:
        data=payload.get('data') if isinstance(payload.get('data'),dict) else {}
        relations=data.get('relationships') if isinstance(data.get('relationships'),dict) else {}
        relationship=relations.get('envelope') if isinstance(relations.get('envelope'),dict) else {}
        linked=relationship.get('data') if isinstance(relationship.get('data'),dict) else {}
        envelope=str(linked.get('id') or '')[:200]
    # Hash the complete signed event. Reused document/envelope IDs never suppress distinct events.
    event_key=hashlib.sha256(raw).hexdigest()
    inserted=db.execute("INSERT OR IGNORE INTO local_extension_webhook_events(config_key,event_key,envelope_id) VALUES(?,?,?)", (company_key,event_key,envelope)).rowcount
    if inserted:
        db.execute("UPDATE local_extension_workflows SET next_check_at=0 WHERE config_key=? AND extension_id IN (SELECT id FROM local_temporary_extensions WHERE envelope_id=?)", (company_key,envelope))
    return {"accepted":True,"duplicate":not bool(inserted),"message":"Evento recebido; a situação será conferida na Clicksign."}


def enrich_queue(db, queue, *, can_manage=False):
    for item in queue.get("items",[]):
        if item.get("sourceSystem") != "SIB": continue
        workflow = db.execute("""SELECT w.* FROM local_extension_workflows w JOIN local_temporary_extensions e ON e.id=w.extension_id
           WHERE e.source_system='SIB' AND e.source_id=? AND e.first_due_date=?""", (item["sourceId"],item["firstDueDate"])).fetchone()
        contract = db.execute("SELECT * FROM local_temporary_contracts WHERE source_system='SIB' AND source_id=?", (item["sourceId"],)).fetchone()
        fields=("employer_name","employer_cnpj","employer_address","worker_function","work_location","complementary_demand","tomadora_name","tomadora_cnpj")
        item["workflow"] = None
        if can_manage:
            item["formDefaults"]={"fields":{field:contract[field] or "" for field in fields},"workerEmail":contract["email"] or "", "workerPhone":contract["whatsapp"] or ""} if contract else {}
            item["signers"]=[{"id":row["id"],"name":row["name"],"role":row["role"]} for row in db.execute("SELECT id,name,role FROM local_clicksign_signers WHERE active=1 AND company_key IN (?, '__global__') ORDER BY signer_order,id",(item["companyKey"],))]
        if workflow:
            item["workflow"]={"stage":workflow["stage"],"error":workflow["error"],"lastCheckedAt":workflow["last_checked_at"],
                              "archivedDocumentId":workflow["archived_document_id"],"evidenceDocumentId":workflow["evidence_document_id"],
                              "archivedAt":workflow["archived_at"],"inProgress":workflow["lease_until"]>time.time()}
            if can_manage:
                context=json.loads(workflow["prepared_json"])
                item["workflow"].update(previewHash=workflow["prepared_hash"],context={"fields":{field:context["contract"].get(field,"") for field in fields},
                    "representative":context["representative"],"workerEmail":context["worker"]["email"],"workerPhone":context["worker"]["phone"],
                    "workerChannel":context["workerChannel"],"signerId":context.get("signerId",0)})
                item["workflow"]["events"]=[dict(row) for row in db.execute("SELECT action,created_at,details_json FROM local_extension_events WHERE extension_id=? ORDER BY id DESC LIMIT 20",(workflow["extension_id"],))]
            item["deliveryEnvironment"]=json.loads(workflow["prepared_json"])["environment"]
            item["proposedDueDate"] = item.get("proposedDueDate") or json.loads(workflow["prepared_json"])["proposedDueDate"]
        else:
            row=db.execute("SELECT environment,active FROM local_clicksign_company_settings WHERE company_key=?",(item["companyKey"],)).fetchone()
            if not row:row=db.execute("SELECT environment,active FROM local_clicksign_company_settings WHERE company_key='__global__'").fetchone()
            item["deliveryEnvironment"]=row["environment"] if row and row["active"] else ""
        for previous in item.get("history", []):
            saved = db.execute("""SELECT w.archived_document_id,w.evidence_document_id,w.stage FROM local_extension_workflows w
                JOIN local_temporary_extensions e ON e.id=w.extension_id WHERE e.source_system='SIB' AND e.source_id=? AND e.first_due_date=?""",
                (item["sourceId"], previous["firstDueDate"])).fetchone()
            if saved:
                previous.update(archivedDocumentId=saved["archived_document_id"],evidenceDocumentId=saved["evidence_document_id"],workflowStage=saved["stage"])
    return queue
