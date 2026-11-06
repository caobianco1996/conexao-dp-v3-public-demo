"""Authenticated HTTP adapters for dossiers and operational workflows."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from urllib.parse import parse_qs, unquote, urlsplit

from .database import connect
from . import permissions, registry_documents, email_delivery, smtp_credentials
from . import absence_workflow, rescission_workflow, extension_workflow


def _raw_upload(handler, user):
    if not permissions.can(user, 'documents.upload'):
        raise PermissionError('Seu perfil não pode anexar documentos.')
    if not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), user['csrf_token']):
        raise PermissionError('Sessão expirada. Atualize a página.')
    length = int(handler.headers.get('Content-Length') or 0)
    if not 0 < length <= 10 * 1024 * 1024:
        raise ValueError('O documento deve ter conteúdo e no máximo 10 MB.')
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError('O envio do arquivo foi interrompido.')
    metadata = {key: unquote(handler.headers.get(header, '')) for key, header in
                (('documentType', 'X-Document-Type'), ('description', 'X-Document-Description'), ('documentDate', 'X-Document-Date'))}
    return unquote(handler.headers.get('X-File-Name', 'documento')), handler.headers.get('Content-Type', '').split(';')[0], raw, metadata


def _smtp(db):
    row = db.execute('SELECT * FROM local_smtp_settings WHERE id=1 AND active=1').fetchone()
    if not row:
        raise ValueError('Configure e ative a conexão SMTP antes de enviar.')
    settings = email_delivery.validate_settings({'connectionName': row['connection_name'], 'smtpHost': row['smtp_host'],
            'smtpPort': row['smtp_port'], 'security': row['security'], 'username': row['username'],
            'senderEmail': row['sender_email'], 'active': row['active'], 'timeoutSeconds': row['timeout_seconds']})
    password = smtp_credentials.get_password()
    if not password:
        raise ValueError('Cadastre a senha SMTP antes de enviar.')
    return settings, password


def get(handler, path):
    if path not in ('/api/zpro/settings', '/api/access/self'):
        return False
    user = handler.principal()
    if not user:
        handler.respond_json({'error': 'Sua sessão expirou.'}, 401)
        return True
    if path == '/api/access/self':
        handler.respond_json({'permissions': user['permissions'], 'profile': user['accessProfile'], 'population': user['accessScope']})
    elif not permissions.can(user, 'integrations.manage'):
        handler.respond_json({'error': 'Seu perfil não pode configurar canais.'}, 403)
    else:
        with connect(handler.server.database) as db:
            handler.respond_json({'settings': rescission_workflow.zpro_settings(db), 'csrf': user['csrf_token']})
    return True


def post(handler, path):
    webhook = re.fullmatch(r'/webhooks/clicksign/([^/]+)', path)
    raw_document = path == '/api/ui/registry-documents'
    absence_document = re.fullmatch(r'/api/ui/absences/(\d+)/documents', path)
    absence_email = re.fullmatch(r'/api/ui/absences/(\d+)/email', path)
    whatsapp = re.fullmatch(r'/api/ui/rescissions/(\d+)/whatsapp', path)
    json_route = path in ('/api/ui/registry-documents/actions', '/api/ui/absences/workflow',
                         '/api/ui/rescissions/delivery-actions', '/api/zpro/settings')
    if not (webhook or raw_document or absence_document or absence_email or whatsapp or json_route):
        return False
    database, storage = handler.server.database, handler.server.database.parent / 'private-registry-documents'
    try:
        if webhook:
            length = int(handler.headers.get('Content-Length') or 0)
            if not 0 < length <= 256000:
                raise ValueError('Conteúdo do webhook inválido.')
            with connect(database) as db:
                result = extension_workflow.process_webhook(db, unquote(webhook[1]), handler.rfile.read(length), handler.headers.get('Content-Hmac', ''))
            handler.respond_json(result)
            return True
        if raw_document or absence_document:
            user = handler.principal()
            if not user:
                handler.respond_json({'error': 'Sua sessão expirou.'}, 401)
                return True
            filename, mime, raw, metadata = _raw_upload(handler, user)
            with connect(database) as db:
                if raw_document:
                    query = parse_qs(urlsplit(handler.path).query)
                    result = registry_documents.upload(db, (query.get('view') or ['pessoas'])[0],
                            (query.get('id') or [''])[0], user['id'], filename, mime, raw, storage, metadata, user=user)
                else:
                    row, employee, _, fingerprint, _ = absence_workflow.context(db, int(absence_document[1]), user, writable=True)
                    if not secrets.compare_digest(handler.headers.get('X-Context-Token', ''), fingerprint):
                        raise ValueError('O afastamento foi atualizado. Abra os detalhes novamente antes de anexar.')
                    owner = registry_documents.resolve_owner(db, 'pessoas', row['employment_key'], user=user)
                    metadata.update(documentType='AFASTAMENTO', origin='AFASTAMENTO')
                    result = registry_documents.add_document(db, owner, user['id'], filename, mime, raw, storage, metadata)
                    absence_workflow.link_document(db, int(absence_document[1]), result['id'], user)
            handler.respond_json({**result, 'csrf': user['csrf_token']}, 201)
            return True
        authenticated = handler._chat_payload()
        if not authenticated:
            return True
        user, payload = authenticated
        if whatsapp:
            result = rescission_workflow.send_whatsapp(database, int(whatsapp[1]), payload, user, storage)
        elif absence_email:
            if not permissions.can(user, 'communications.send'):
                raise PermissionError('Seu perfil não pode enviar comunicações.')
            delivery = None
            try:
                with connect(database) as db:
                    settings, password = _smtp(db)
                    delivery = absence_workflow.begin_delivery(db, int(absence_email[1]), payload, user)
                    if delivery.get('alreadySent'):
                        handler.respond_json({'message': 'Esta tentativa já foi aceita pelo SMTP.'})
                        return True
                # Commit the reservation before reading files or making external calls.
                with connect(database) as db:
                    attachments = []
                    for document_id in delivery['documentIds']:
                        document, target = registry_documents.get_document(db, document_id, storage, user=user)
                        if document.get('archived_at'):
                            raise ValueError('Um documento foi arquivado. Revise os anexos antes de enviar.')
                        attachments.append({'filename': document['filename'], 'mime_type': document['mime_type'], 'content': target.read_bytes()})
                with connect(database) as db:
                    _, _, _, fingerprint, _ = absence_workflow.context(db, int(absence_email[1]), user, writable=True)
                    if fingerprint != delivery['snapshotHash']:
                        raise ValueError('O afastamento foi atualizado. Confira os dados antes de enviar.')
                email_delivery.send_email(settings, password, delivery['target'], delivery['subject'], delivery['message'], attachments, allow_empty_attachments=True)
            except Exception as error:
                if delivery and delivery.get('deliveryId'):
                    with connect(database) as db:
                        absence_workflow.finish_delivery(db, delivery['deliveryId'], sent=False,
                            uncertain=bool(getattr(error, 'possibly_accepted', False)), error=str(error), actor_id=user['id'])
                raise
            with connect(database) as db:
                absence_workflow.finish_delivery(db, delivery['deliveryId'], sent=True, actor_id=user['id'])
            result = {'message': 'A comunicação foi aceita pelo SMTP. Registre a confirmação quando houver retorno do destinatário.'}
        else:
            with connect(database) as db:
                if path == '/api/ui/registry-documents/actions':
                    result = registry_documents.mutate(db, payload, user['id'], user=user)
                elif path == '/api/ui/absences/workflow':
                    result = absence_workflow.mutate(db, payload, user)
                elif path == '/api/ui/rescissions/delivery-actions':
                    result = rescission_workflow.delivery_action(db, payload, user)
                else:
                    result = {'settings': rescission_workflow.save_zpro(db, user, payload), 'message': 'Conexão ZPRO salva.'}
        handler.respond_json({**(result or {}), 'csrf': user['csrf_token']})
    except PermissionError as error:
        handler.respond_json({'error': str(error)}, 403)
    except LookupError as error:
        handler.respond_json({'error': str(error)}, 404)
    except (ValueError, TypeError, KeyError) as error:
        handler.respond_json({'error': str(error)}, 400)
    except (RuntimeError, OSError) as error:
        handler.respond_json({'error': str(error)[:500]}, 502)
    except sqlite3.DatabaseError:
        handler.respond_json({'error': 'Não foi possível registrar a operação. Confira o histórico antes de tentar novamente.'}, 503)
    return True
