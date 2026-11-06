"""Comunicação rescisória com evidência de envio e recebimento separados."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import mimetypes
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import clicksign, permissions, registry_documents, rescissions

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_zpro_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), active INTEGER NOT NULL DEFAULT 0,
 base_url TEXT NOT NULL DEFAULT '', api_token_encrypted TEXT NOT NULL DEFAULT '',
 zpro_queue_id INTEGER NOT NULL DEFAULT 0,zpro_user_id INTEGER NOT NULL DEFAULT 0,
 zpro_whatsapp_id INTEGER NOT NULL DEFAULT 0,zpro_block_chatbot INTEGER NOT NULL DEFAULT 1,
 timeout_seconds INTEGER NOT NULL DEFAULT 30,updated_by INTEGER REFERENCES users(id),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate(db):
    db.executescript(SCHEMA)
    columns = {row[1] for row in db.execute('PRAGMA table_info(local_rescission_real_delivery_events)')}
    for name, declaration in {
        'channel': "TEXT NOT NULL DEFAULT 'EMAIL'", 'evidence_json': "TEXT NOT NULL DEFAULT '{}'",
        'receipt_reference': "TEXT NOT NULL DEFAULT ''", 'receipt_at': "TEXT NOT NULL DEFAULT ''",
        'receipt_by': 'INTEGER REFERENCES users(id)', 'provider_ticket': "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            db.execute(f'ALTER TABLE local_rescission_real_delivery_events ADD COLUMN {name} {declaration}')


def zpro_settings(db):
    row = db.execute('SELECT * FROM local_zpro_settings WHERE id=1').fetchone()
    result = dict(row) if row else {'active': 0, 'base_url': '', 'zpro_queue_id': 0, 'zpro_user_id': 0,
                                  'zpro_whatsapp_id': 0, 'zpro_block_chatbot': 1, 'timeout_seconds': 30}
    result['tokenConfigured'] = bool(result.pop('api_token_encrypted', ''))
    return result


def save_zpro(db, user, payload):
    if not permissions.can(user, 'integrations.manage'):
        raise PermissionError('Seu perfil não pode configurar o ZPRO.')
    url = str(payload.get('base_url') or '').strip().rstrip('/')
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Informe a URL base sem credenciais, parâmetros ou fragmentos.')
    local_http = False
    if parsed.hostname:
        try:
            address = ipaddress.ip_address(parsed.hostname)
            local_http = address.is_private or address.is_loopback
        except ValueError:
            local_http = parsed.hostname in ('localhost',)
    if not parsed.hostname or not (parsed.scheme == 'https' or parsed.scheme == 'http' and local_http):
        raise ValueError('Informe a URL HTTPS da API ZPRO; HTTP é permitido somente para endereço IP da rede privada.')
    ids = [int(payload.get(key) or 0) for key in ('zpro_queue_id', 'zpro_user_id', 'zpro_whatsapp_id')]
    active = bool(payload.get('active'))
    if active and min(ids) < 1:
        raise ValueError('Informe os IDs da fila, atendente e canal WhatsApp antes de ativar.')
    timeout = int(payload.get('timeout_seconds') or 30)
    if not 5 <= timeout <= 120:
        raise ValueError('O timeout deve ficar entre 5 e 120 segundos.')
    current = db.execute('SELECT api_token_encrypted FROM local_zpro_settings WHERE id=1').fetchone()
    token = clicksign.protect_secret(str(payload['token']).strip()) if str(payload.get('token') or '').strip() else (current[0] if current else '')
    if active and not token:
        raise ValueError('Cadastre o token do ZPRO antes de ativar.')
    db.execute('''INSERT INTO local_zpro_settings(id,active,base_url,api_token_encrypted,zpro_queue_id,zpro_user_id,
        zpro_whatsapp_id,zpro_block_chatbot,timeout_seconds,updated_by) VALUES(1,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET active=excluded.active,base_url=excluded.base_url,api_token_encrypted=excluded.api_token_encrypted,
        zpro_queue_id=excluded.zpro_queue_id,zpro_user_id=excluded.zpro_user_id,zpro_whatsapp_id=excluded.zpro_whatsapp_id,
        zpro_block_chatbot=excluded.zpro_block_chatbot,timeout_seconds=excluded.timeout_seconds,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
        (int(active), url, token, *ids, int(bool(payload.get('zpro_block_chatbot', True))), timeout, user['id']))
    return zpro_settings(db)


class MessageDispatchError(RuntimeError):
    def __init__(self, message, *, evidence=None):
        super().__init__(message)
        self.evidence = evidence or {}


class ZproClient:
    def __init__(self):
        self.protector = SimpleNamespace(unprotect=clicksign.unprotect_secret)


    def _send_zpro(
        self, row: sqlite3.Row, employee: dict[str, str], body: str, case_id: int,
        attachments: list[dict[str, Any]], previous_thread: dict[str, Any] | None,
        *, context_prefix: str = "RES",
    ) -> dict[str, Any]:
        if str(row["provider"]) != "ZPRO":
            raise ValueError("O envio de WhatsApp requer uma conexão ZPro configurada.")
        queue_id, user_id, whatsapp_id = int(row["zpro_queue_id"] or 0), int(row["zpro_user_id"] or 0), int(row["zpro_whatsapp_id"] or 0)
        if queue_id < 1 or user_id < 1 or whatsapp_id < 1:
            raise ValueError("Configure os IDs de fila, atendente responsável e canal WhatsApp na conexão ZPro antes do disparo.")
        digits = "".join(character for character in str(employee.get("phone") or "") if character.isdigit())
        if len(digits) in {10, 11}:
            digits = "55" + digits
        if len(digits) < 12:
            raise ValueError("O funcionário não possui um WhatsApp válido com DDD.")
        token = self.protector.unprotect(str(row["api_token_encrypted"] or ""))
        base_url = str(row["base_url"]).rstrip("/")
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"}
        prefix = "".join(character for character in context_prefix.upper() if character.isalnum())[:12] or "RH"
        context_id = str(case_id).replace("-", "N")
        partial: dict[str, Any] = {"provider": "ZPRO", "closed_ticket_ids": [], "attachments": []}
        current_operation = {"path": ""}
        def request_json(target: str, payload: dict[str, Any]) -> dict[str, Any]:
            current_operation["path"] = urlparse(target).path or "/"
            request = Request(target, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
            with urlopen(request, timeout=int(row["timeout_seconds"])) as response:
                raw = response.read().decode("utf-8", "replace")
                try:
                    result = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    result = {}
                if not isinstance(result, dict):
                    result = {"data": result}
                result["__http_status"] = int(response.status)
                return result
        def request_get(target: str) -> dict[str, Any]:
            current_operation["path"] = urlparse(target).path or "/"
            request = Request(target, method="GET", headers=headers)
            with urlopen(request, timeout=int(row["timeout_seconds"])) as response:
                raw = response.read().decode("utf-8", "replace")
                try:
                    result = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    result = {}
                if not isinstance(result, dict):
                    result = {"data": result}
                result["__http_status"] = int(response.status)
                return result
        def entity_id(payload: object) -> str:
            if isinstance(payload, dict):
                for key in ("id", "ticketId", "messageId", "key"):
                    if payload.get(key) not in (None, ""): return str(payload[key])
                for key in ("data", "ticket", "contact"):
                    found=entity_id(payload.get(key))
                    if found: return found
            return ""
        def active_ticket_ids(payload: object) -> list[str]:
            """Retorna todos os tickets ativos sem confundir o ID do contato."""
            found_ids: list[str] = []
            if isinstance(payload, dict):
                tickets = payload.get("tickets")
                if isinstance(tickets, list):
                    for ticket in tickets:
                        if isinstance(ticket, dict) and str(ticket.get("status") or "").lower() not in {"closed", "resolved"}:
                            found = entity_id(ticket)
                            if found and found not in found_ids:
                                found_ids.append(found)
                ticket = payload.get("ticket")
                if isinstance(ticket, dict) and str(ticket.get("status") or "").lower() not in {"closed", "resolved"}:
                    found = entity_id(ticket)
                    if found and found not in found_ids:
                        found_ids.append(found)
                for key in ("data", "contact"):
                    for found in active_ticket_ids(payload.get(key)):
                        if found not in found_ids:
                            found_ids.append(found)
            if isinstance(payload, list):
                for item in payload:
                    for found in active_ticket_ids(item):
                        if found not in found_ids:
                            found_ids.append(found)
            return found_ids
        def ticket_assignment(payload: object, wanted_ticket_id: str) -> tuple[str, bool]:
            if isinstance(payload, dict):
                candidate_id = entity_id(payload)
                looks_like_ticket = any(key in payload for key in ("ticketId", "status", "queueId", "userId"))
                if looks_like_ticket and candidate_id == wanted_ticket_id:
                    if "userId" in payload:
                        return str(payload.get("userId") or ""), True
                    user = payload.get("user")
                    if isinstance(user, dict) and user.get("id") not in (None, ""):
                        return str(user["id"]), True
                for value in payload.values():
                    assigned, seen = ticket_assignment(value, wanted_ticket_id)
                    if seen:
                        return assigned, seen
            elif isinstance(payload, list):
                for value in payload:
                    assigned, seen = ticket_assignment(value, wanted_ticket_id)
                    if seen:
                        return assigned, seen
            return "", False
        def ticket_status(payload: object, wanted_ticket_id: str) -> tuple[str, bool]:
            if isinstance(payload, dict):
                candidate_id = entity_id(payload)
                if candidate_id == wanted_ticket_id and "status" in payload:
                    return str(payload.get("status") or "").lower(), True
                for value in payload.values():
                    status, seen = ticket_status(value, wanted_ticket_id)
                    if seen:
                        return status, seen
            elif isinstance(payload, list):
                for value in payload:
                    status, seen = ticket_status(value, wanted_ticket_id)
                    if seen:
                        return status, seen
            return "", False
        def channel_state(payload: object, wanted_channel_id: int) -> tuple[str, bool]:
            if isinstance(payload, dict):
                candidate_id = payload.get("id") if payload.get("id") not in (None, "") else payload.get("whatsappId")
                if str(candidate_id or "") == str(wanted_channel_id):
                    raw_state = payload.get("status") or payload.get("state") or payload.get("connectionStatus")
                    if raw_state not in (None, ""):
                        return str(raw_state).upper(), True
                    if "isOnline" in payload:
                        return "CONNECTED" if bool(payload.get("isOnline")) else "DISCONNECTED", True
                    if "connected" in payload:
                        return "CONNECTED" if bool(payload.get("connected")) else "DISCONNECTED", True
                for value in payload.values():
                    state, seen = channel_state(value, wanted_channel_id)
                    if seen:
                        return state, seen
            elif isinstance(payload, list):
                for value in payload:
                    state, seen = channel_state(value, wanted_channel_id)
                    if seen:
                        return state, seen
            return "", False
        try:
            channels_payload=request_get(base_url+"/listChannels")
            configured_channel_state, channel_seen=channel_state(channels_payload,whatsapp_id)
            partial["configured_whatsapp_id"] = whatsapp_id
            partial["channel_status"] = configured_channel_state or "NAO_LOCALIZADO"
            connected_states={"CONNECTED","ONLINE","OPEN","READY","ACTIVE","ATIVA","CONECTADO"}
            if not channel_seen or configured_channel_state not in connected_states:
                raise MessageDispatchError(
                    f"Canal Z-PRO desconectado (canal {whatsapp_id}, status {configured_channel_state or 'não localizado'}). Reconecte o canal antes de tentar novamente.",
                    evidence=partial,
                )
            contact_payload={"name":str(employee.get("name") or ""),"number":digits,"email":str(employee.get("email") or ""),"cpf":str(employee.get("cpf") or ""),"firstName":str(employee.get("name") or "").split(" ",1)[0],"lastName":" ".join(str(employee.get("name") or "").split(" ")[1:]),"externalKey":f"RH-{prefix}-CONTACT-{context_id}","chatbotBlocked":bool(row["zpro_block_chatbot"])}
            def lookup_contact() -> dict[str, Any]:
                try:
                    return request_json(base_url+"/showcontact",{"number":digits})
                except HTTPError as exc:
                    detail=exc.read().decode("utf-8", "replace")[:1000]
                    # Para esta API, contato inexistente é uma resposta de consulta,
                    # não uma falha de integração. Outros 404 continuam bloqueados.
                    if exc.code == 404 and "ERR_CONTACT_NOT_FOUND" in detail.upper():
                        partial["contact_lookup_status"] = "NAO_CADASTRADO"
                        return {"data":{},"__http_status":404}
                    raise

            lookup=lookup_contact()
            contact_id=entity_id(lookup.get("data") if isinstance(lookup,dict) else lookup)
            if contact_id:
                partial["contact_lookup_status"] = "LOCALIZADO"
                updated=request_json(base_url+"/updateContact",contact_payload|{"contactId":int(contact_id)})
                contact_id=entity_id(updated.get("data") if isinstance(updated,dict) else updated) or contact_id
            else:
                try:
                    created=request_json(base_url+"/createContact",contact_payload)
                    contact_id=entity_id(created.get("data") if isinstance(created,dict) else created)
                except HTTPError as exc:
                    detail=exc.read().decode("utf-8", "replace")[:1000]
                    try:
                        conflict=json.loads(detail)
                    except json.JSONDecodeError:
                        conflict={}
                    if exc.code == 409 and conflict.get("existingContactId"):
                        contact_id=str(conflict["existingContactId"])
                    else:
                        raise
                if not contact_id:
                    for attempt in range(4):
                        refreshed=lookup_contact()
                        contact_id=entity_id(refreshed.get("data") if isinstance(refreshed,dict) else refreshed)
                        if contact_id:
                            lookup=refreshed
                            break
                        if attempt < 3:
                            time.sleep(0.25)
                else:
                    refreshed=lookup_contact()
                    if entity_id(refreshed.get("data") if isinstance(refreshed,dict) else refreshed):
                        lookup=refreshed
                partial["contact_lookup_status"] = "CRIADO" if contact_id else "NAO_CONFIRMADO"
            if not contact_id:
                raise MessageDispatchError("ZPro não devolveu o ID do contato; o envio foi interrompido para evitar atendimento sem vínculo.", evidence=partial)
            partial["contact_id"] = contact_id
            partial["connection_id"] = int(row["id"])
            partial["queue_id"] = queue_id
            partial["configured_user_id"] = user_id
            ticket_ids = active_ticket_ids(lookup)
            previous_ticket = str((previous_thread or {}).get("ticket_id") or "").strip()
            previous_status = str((previous_thread or {}).get("status") or "").upper()
            if ticket_ids:
                raise MessageDispatchError("Há um atendimento aberto para este contato no ZPRO. Confira o atendimento antes de criar uma nova comunicação; nenhum ticket existente foi encerrado.", evidence=partial)
            resume_ticket = ""
            unique_key = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            if resume_ticket:
                ticket_id=resume_ticket
                created_ticket={"__http_status":200}
                partial["resumed_after_create"] = True
            else:
                created_ticket=request_json(base_url+"/createTicket",{
                    "contactId": int(contact_id), "number": digits, "body": body, "status": "open",
                    "userId": user_id, "queueId": queue_id, "whatsappId": whatsapp_id,
                    "externalKey": f"RH-{prefix}-TICKET-{context_id}-{unique_key}", "chatFlowId": None,
                    "typebotStatus": False, "chatgptStatus": False, "dialogflowStatus": False,
                    "difyStatus": False, "n8nStatus": False,
                })
                ticket_id=entity_id(created_ticket.get("data") if isinstance(created_ticket,dict) else created_ticket)
                if not ticket_id:
                    recovered_lookup=request_json(base_url+"/showcontact",{"number":digits})
                    recovered_ids=[item for item in active_ticket_ids(recovered_lookup) if item not in partial["closed_ticket_ids"]]
                    ticket_id=recovered_ids[0] if recovered_ids else ""
                    if ticket_id:
                        partial["ticket_id_recovered_from_contact"] = True
                if not ticket_id:
                    raise MessageDispatchError("O ZPro não informou nem permitiu localizar o novo ticket. Nenhum anexo foi enviado.", evidence=partial)
            partial["new_ticket_id"] = ticket_id
            partial["message_status"] = "ACEITO_PELO_PROVEDOR"
            partial["message_http_status"] = int(created_ticket.get("__http_status") or 0)
            request_json(base_url+"/updatequeue",{"ticketId":int(ticket_id),"queueId":queue_id})
            assignment_response=request_json(base_url+"/updateticketinfo",{"ticketId":int(ticket_id),"userId":user_id,"status":"open","queueId":queue_id,"chatFlowId":None,"typebotStatus":False,"chatgptStatus":False,"dialogflowStatus":False,"difyStatus":False,"n8nStatus":False})
            request_json(base_url+"/updateTicketChannel",{"ticketId":int(ticket_id),"whatsappId":whatsapp_id,"channel":"whatsapp"})
            assigned_user, assignment_seen = ticket_assignment(assignment_response, ticket_id)
            if not assignment_seen or assigned_user != str(user_id):
                verified_contact=request_json(base_url+"/showcontact",{"number":digits})
                assigned_user, assignment_seen = ticket_assignment(verified_contact, ticket_id)
            partial["configured_user_id"] = user_id
            partial["assigned_user_id"] = assigned_user
            partial["assignment_status"] = "CONFIRMADO" if assignment_seen and assigned_user == str(user_id) else "NAO_CONFIRMADO"
            if not assignment_seen or assigned_user != str(user_id):
                raise MessageDispatchError(
                    "O ZPro criou o ticket e vinculou a fila, mas não confirmou o atendente configurado. Verifique se o usuário pertence à fila e ao canal antes de reenviar.",
                    evidence=partial,
                )
            protocol_items: list[str] = [f"TICKET-{ticket_id}"]
            for index, item in enumerate(attachments, start=1):
                source = Path(str(item["path"]))
                if not source.is_file():
                    raise FileNotFoundError(f"Arquivo selecionado não encontrado: {item.get('name') or source.name}")
                mime = str(item.get("mime") or mimetypes.guess_type(source.name)[0] or "application/octet-stream")
                file_name = str(item.get("name") or source.name)
                sent=request_json(base_url + "/base64", {
                    "number": digits,
                    "body": f"📎 Documento rescisório {index}/{len(attachments)}: {file_name}",
                    "base64Data": base64.b64encode(source.read_bytes()).decode("ascii"), "mimeType": mime,
                    "fileName": file_name, "externalKey": f"RH-{prefix}-{context_id}-{unique_key}-{index}",
                    "isClosed": False,
                })
                message_id = entity_id(sent.get("data") if isinstance(sent,dict) else sent) or f"TICKET-{ticket_id}-{index}"
                protocol_items.append(message_id)
                partial["attachments"].append({"name": file_name, "message_id": message_id, "http_status": int(sent.get("__http_status") or 0), "status": "ACEITO_PELO_PROVEDOR"})
            partial["status"] = "AGUARDANDO_CONFIRMACAO"
            partial["attachment_count"] = len(attachments)
            partial["protocol"] = "|".join(protocol_items)
            return {"connection_id":int(row["id"]),"contact_id":contact_id,"ticket_id":ticket_id,"queue_id":queue_id,"user_id":user_id,"whatsapp_id":whatsapp_id,"status":"AGUARDANDO_CONFIRMACAO","protocol":partial["protocol"],"evidence":partial}
        except MessageDispatchError:
            raise
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            partial["failed_operation"] = current_operation["path"]
            partial["http_status"] = exc.code
            raise MessageDispatchError(f"WhatsApp recusou o envio (HTTP {exc.code}): {detail or 'revise rota, credencial e instância.'}", evidence=partial) from exc
        except (URLError, TimeoutError) as exc:
            partial["failed_operation"] = current_operation["path"]
            raise MessageDispatchError("Não foi possível alcançar o provedor WhatsApp para o disparo.", evidence=partial) from exc
        except Exception as exc:
            partial["failed_operation"] = current_operation["path"]
            raise MessageDispatchError(str(exc), evidence=partial) from exc

def _require_case(db, case_id, user):
    case = rescissions._case_row(db, int(case_id))
    if not case:
        raise LookupError('Rescisão não encontrada.')
    if not permissions.case_allowed(db, user, case):
        raise PermissionError('Esta rescisão está fora do seu recorte de acesso.')
    return case


def send_whatsapp(database, case_id, payload, user, storage_dir):
    from .database import connect
    if not permissions.can(user, 'communications.send') or not permissions.can(user, 'rescissions.manage'):
        raise PermissionError('Seu perfil não pode enviar comunicações de rescisão.')
    request_id = str(payload.get('requestId') or '')
    if not re.fullmatch(r'[A-Za-z0-9-]{24,80}', request_id):
        raise ValueError('Atualize a página para iniciar uma tentativa identificada de envio.')
    message = str(payload.get('message') or '').strip()
    if not 20 <= len(message) <= 5000:
        raise ValueError('A mensagem deve ter entre 20 e 5.000 caracteres.')
    ids = payload.get('documentIds')
    if not isinstance(ids, list) or not ids or len(ids) > 20:
        raise ValueError('Selecione de 1 a 20 documentos do checklist.')
    ids = {int(value) for value in ids}
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        case = _require_case(db, case_id, user)
        config = db.execute('SELECT * FROM local_zpro_settings WHERE id=1 AND active=1').fetchone()
        if not config:
            raise ValueError('Configure e ative o ZPRO antes do envio.')
        previous = db.execute('SELECT status,protocol FROM local_rescission_real_delivery_events WHERE request_key=? AND case_id=?', (request_id, case_id)).fetchone()
        if previous:
            if previous['status'] == 'ENVIADO':
                return {'sent': True, 'message': 'Esta tentativa já foi aceita pelo ZPRO.', 'protocol': previous['protocol']}
            raise ValueError('Esta tentativa já foi registrada. Confira o histórico antes de reenviar.')
        if db.execute("SELECT 1 FROM local_rescission_real_delivery_events WHERE case_id=? AND status='ENVIANDO'", (case_id,)).fetchone():
            raise ValueError('Há uma comunicação em andamento ou de resultado incerto. Confira o histórico antes de reenviar.')
        case, names, _ = rescissions.get_email_delivery_payload(db, case_id, ids, storage_dir)
        attachments = []
        for req in db.execute('SELECT registry_document_id FROM local_rescission_requirements WHERE case_id=? AND id IN (' + ','.join('?' for _ in ids) + ')', (case_id, *sorted(ids))):
            document, path = registry_documents.get_document(db, req[0], storage_dir, user=user)
            if document.get('archived_at'):
                raise ValueError('Um dos documentos está arquivado. Restaure-o antes de enviar.')
            attachments.append({'path': path, 'name': document['filename'], 'mime': document['mime_type']})
        if case.get('is_test'):
            raise ValueError('WhatsApp real exige um cadastro confirmado da origem. Use e-mail para os exemplos de teste.')
        target = str(case.get('whatsapp') or '')
        protocol = f'RSC-ZPRO-{case_id}-{secrets.token_hex(6).upper()}'
        attempt_id = int(db.execute('''INSERT INTO local_rescission_real_delivery_events
            (case_id,status,target,message,documents_json,protocol,request_key,created_by,channel)
            VALUES(?,'ENVIANDO',?,?,?,?,?,?,'WHATSAPP')''',
            (case_id, target, message, json.dumps(names, ensure_ascii=False), protocol, request_id, user['id'])).lastrowid)
        previous_delivery = db.execute("SELECT provider_ticket FROM local_rescission_real_delivery_events WHERE case_id=? AND channel='WHATSAPP' AND provider_ticket<>'' ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        snapshot = case.get('source_snapshot_json') or ''
        config = dict(config) | {'provider': 'ZPRO'}
    try:
        with connect(database) as db:
            fresh = _require_case(db, case_id, user)
            rescissions._source_ready(db, fresh)
            if (fresh.get('source_snapshot_json') or '') != snapshot:
                raise ValueError('A ficha da origem mudou. Confira a rescisão antes de enviar.')
        result = ZproClient()._send_zpro(config, {'name': case['name'], 'email': case.get('email') or '',
                'cpf': case.get('cpf') or '', 'phone': target}, message, case_id, attachments,
                {'ticket_id': previous_delivery[0], 'status': 'AGUARDANDO_CONFIRMACAO'} if previous_delivery else None)
    except Exception as error:
        evidence = getattr(error, 'evidence', {})
        evidence['requirementIds'] = sorted(ids)
        # After creating a ticket or attempting a mutating request, an interrupted
        # response cannot justify a blind retry. Preserve the uncertain attempt.
        uncertain = bool(evidence.get('new_ticket_id') or evidence.get('failed_operation') in ('/createTicket', '/base64'))
        with connect(database) as db:
            db.execute('UPDATE local_rescission_real_delivery_events SET status=?,error_message=?,evidence_json=?,provider_ticket=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                       ('ENVIANDO' if uncertain else 'FALHOU', str(error)[:500], json.dumps(evidence), str(evidence.get('new_ticket_id') or ''), attempt_id))
            rescissions._event(db, case_id, 'WHATSAPP_RESULTADO_INCERTO' if uncertain else 'WHATSAPP_FALHOU', user['id'], deliveryId=attempt_id)
        raise
    with connect(database) as db:
        result['evidence']['requirementIds'] = sorted(ids)
        db.execute('UPDATE local_rescission_real_delivery_events SET status=\'ENVIADO\',protocol=?,evidence_json=?,provider_ticket=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                   (result['protocol'], json.dumps(result['evidence']), result['ticket_id'], attempt_id))
        db.executemany("UPDATE local_rescission_requirements SET status='ENTREGUE',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND id=?", [(user['id'], case_id, value) for value in ids])
        _complete_document_delivery(db, case_id, user['id'])
        rescissions._event(db, case_id, 'WHATSAPP_ACEITO', user['id'], deliveryId=attempt_id, ticket=result['ticket_id'], protocol=result['protocol'])
    return {'sent': True, 'message': 'O ZPRO aceitou a comunicação e os documentos. O recebimento deve ser confirmado separadamente.', 'protocol': result['protocol']}


def _complete_document_delivery(db, case_id, actor_id):
    pending = db.execute("SELECT COUNT(*) FROM local_rescission_requirements WHERE case_id=? AND mandatory=1 AND status NOT IN ('ENTREGUE','DISPENSADO')", (case_id,)).fetchone()[0]
    if not pending:
        db.execute("UPDATE local_rescission_cases SET status='CONCLUIDO',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status<>'CANCELADO'", (case_id,))
    else:
        rescissions._set_case_status(db, case_id, actor_id)


def delivery_action(db, payload, user):
    if not permissions.can(user, 'rescissions.manage') or not permissions.can(user, 'communications.send'):
        raise PermissionError('Seu perfil não pode registrar confirmações de entrega.')
    case_id, delivery_id = int(payload.get('caseId') or 0), int(str(payload.get('deliveryId') or '').removeprefix('REAL-'))
    case = _require_case(db, case_id, user)
    delivery = db.execute('SELECT * FROM local_rescission_real_delivery_events WHERE id=? AND case_id=?', (delivery_id, case_id)).fetchone()
    if not delivery:
        raise LookupError('Comunicação não encontrada nesta rescisão.')
    reference = ' '.join(str(payload.get('reference') or '').split())[:500]
    if len(reference) < 8:
        raise ValueError('Registre uma referência ou evidência com pelo menos 8 caracteres.')
    action = payload.get('action')
    if action == 'confirm_receipt':
        if delivery['status'] != 'ENVIADO':
            raise ValueError('Confirme primeiro que esta tentativa foi enviada.')
        db.execute('UPDATE local_rescission_real_delivery_events SET receipt_reference=?,receipt_by=?,receipt_at=CURRENT_TIMESTAMP WHERE id=?', (reference, user['id'], delivery_id))
        rescissions._event(db, case_id, 'RECEBIMENTO_CONFIRMADO', user['id'], deliveryId=delivery_id, reference=reference)
    elif action == 'resolve_uncertain':
        outcome = str(payload.get('outcome') or '')
        elapsed = db.execute("SELECT (julianday('now')-julianday(?))*86400", (delivery['created_at'],)).fetchone()[0]
        if delivery['status'] != 'ENVIANDO' or elapsed is None or elapsed < 180 or outcome not in ('ENVIADO', 'FALHOU'):
            raise ValueError('A tentativa precisa estar incerta há pelo menos 3 minutos. Confira o resultado no provedor.')
        db.execute("UPDATE local_rescission_real_delivery_events SET status=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (outcome, reference, delivery_id))
        if outcome == 'ENVIADO':
            evidence = json.loads(delivery['evidence_json'] or '{}')
            for requirement_id in evidence.get('requirementIds', []):
                db.execute("UPDATE local_rescission_requirements SET status='ENTREGUE',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND id=? AND registry_document_id IS NOT NULL", (user['id'], case_id, int(requirement_id)))
            _complete_document_delivery(db, case_id, user['id'])
        rescissions._event(db, case_id, 'RESULTADO_CONFERIDO', user['id'], deliveryId=delivery_id, outcome=outcome, reference=reference)
    else:
        raise ValueError('Ação de comunicação inválida.')
    return {'message': 'Conferência registrada no histórico.'}
