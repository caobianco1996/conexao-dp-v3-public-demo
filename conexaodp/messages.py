"""Local quick-message and internal-alert operations backed by SQLite."""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone


WRITE_ROLES = {"ADMIN", "GESTOR", "OPERADOR"}
ARCHIVE_ROLES = {"ADMIN", "GESTOR"}


class MessageError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _norm(value: object) -> str:
    folded = str(value or "").casefold()
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", folded)
                           if not unicodedata.combining(c)).split())


def _clean_tags(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value.split(",")
    else:
        parsed = value
    if not isinstance(parsed, list):
        parsed = []
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        tag = " ".join(str(item or "").split())[:80]
        key = _norm(tag)
        if tag and key not in seen:
            seen.add(key)
            result.append(tag)
        if len(result) == 30:
            break
    return result


def _event(db, user_id: int, entity_type: str, entity_id: str, action: str, detail: dict | None = None) -> None:
    db.execute("INSERT INTO local_messaging_events(actor_id,entity_type,entity_id,action,detail_json) VALUES(?,?,?,?,?)",
               (user_id, entity_type, entity_id, action, json.dumps(detail or {}, ensure_ascii=False)))


def _users(db, exclude: int | None = None) -> list[dict]:
    rows = db.execute("SELECT id,name,email,role FROM users WHERE active=1 ORDER BY name COLLATE NOCASE,id").fetchall()
    return [dict(row) for row in rows if row["id"] != exclude]


def _message_item(row, clients: dict[str, str]) -> dict:
    item = dict(row)
    try:
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
    except (ValueError, TypeError):
        item["tags"] = []
        item.pop("tags_json", None)
    item["client_name"] = clients.get(str(item.get("client_id") or ""), "")
    item["updated_at"] = item.get("source_updated_at") or item.get("updated_at")
    item["created_at"] = item.get("source_created_at") or item.get("created_at")
    item["imported_from_v2"] = bool(item.get("source_id"))
    return item


def _client_choices(db) -> tuple[list[dict], dict[str, str]]:
    choices: dict[str, str] = {}
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='clients'"):
        try:
            item = json.loads(row["payload"])
        except ValueError:
            continue
        client_id = str(item.get("id") or row["source_id"])
        name = str(item.get("name") or item.get("legal_name") or "").strip()
        code = str(item.get("code") or "").strip()
        if name and item.get("active", True):
            choices[client_id] = f"{code} - {name}" if code else name
    for row in db.execute("SELECT id,code,name FROM local_clients WHERE active=1"):
        choices[f"local:{row['id']}"] = f"{row['code']} - {row['name']}" if row["code"] else row["name"]
    options = [{"id": key, "name": value} for key, value in sorted(choices.items(), key=lambda pair: _norm(pair[1]))]
    return options, choices


def _model_data(db, user, params: dict[str, list[str]]) -> dict:
    q = _norm((params.get("q") or [""])[0][:120])
    category = (params.get("category") or [""])[0][:80].strip()
    try:
        page = max(1, min(10000, int((params.get("page") or ["1"])[0])))
    except ValueError:
        page = 1
    limit = 50
    where = ["archived_at IS NULL"]
    values: list[object] = []
    if q:
        where.append("normalized_text LIKE ?")
        values.append(f"%{q}%")
    if category:
        where.append("category = ?")
        values.append(category)
    clause = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) FROM local_quick_messages WHERE {clause}", values).fetchone()[0]
    rows = db.execute(f"SELECT * FROM local_quick_messages WHERE {clause} ORDER BY coalesce(source_updated_at,updated_at) DESC,id LIMIT ? OFFSET ?",
                      (*values, limit, (page - 1) * limit)).fetchall()
    categories = [row[0] for row in db.execute("SELECT DISTINCT category FROM local_quick_messages WHERE archived_at IS NULL AND trim(category)<>'' ORDER BY category COLLATE NOCASE")]
    clients, client_map = _client_choices(db)
    return {"items": [_message_item(row, client_map) for row in rows], "categories": categories,
            "clients": clients, "total": total, "page": page, "pageSize": limit,
            "csrf": user["csrf_token"], "canEdit": user["role"] in WRITE_ROLES,
            "canArchiveModels": user["role"] in ARCHIVE_ROLES,
            "canCreateAlert": user["role"] in WRITE_ROLES}


def _alert_item(db, row, scope: str) -> dict:
    item = dict(row)
    item["imported_from_v2"] = bool(item.get("imported_from_v2"))
    if scope == "sent":
        recipients = db.execute("""SELECT coalesce(u.name,r.recipient_name,'Destinatário sem correspondência') AS name,
            r.read_at FROM local_dp_alert_recipients r LEFT JOIN users u ON u.id=r.user_id
            WHERE r.alert_id=? ORDER BY name COLLATE NOCASE""", (item["id"],)).fetchall()
        item["recipients"] = [dict(recipient) for recipient in recipients]
        item["recipient_count"] = len(recipients)
        item["read_count"] = sum(1 for recipient in recipients if recipient["read_at"])
        item["recipients_mapped"] = sum(1 for recipient in db.execute(
            "SELECT 1 FROM local_dp_alert_recipients WHERE alert_id=? AND user_id IS NOT NULL", (item["id"],)))
    else:
        item["is_read"] = bool(item.get("read_at"))
    return item


def _alerts_data(db, user, params: dict[str, list[str]]) -> dict:
    scope = "inbox" if (params.get("scope") or ["sent"])[0] == "inbox" else "sent"
    if scope == "sent":
        rows = db.execute("""SELECT a.*,u.name AS sender_name FROM local_dp_alerts a
            LEFT JOIN users u ON u.id=a.created_by
            WHERE a.created_by=? AND a.sender_archived_at IS NULL
            ORDER BY a.created_at DESC LIMIT 500""", (user["id"],)).fetchall()
        # Unmapped V2 identities stay visible to admins as read-only history. We do not
        # guess which local account owns a legacy alert when its user table was not imported.
        if user["role"] == "ADMIN":
            rows += db.execute("""SELECT a.*,NULL AS sender_name FROM local_dp_alerts a
                WHERE a.imported_from_v2=1 AND a.created_by IS NULL AND a.sender_archived_at IS NULL
                ORDER BY a.created_at DESC LIMIT 500""").fetchall()
        items = [_alert_item(db, row, scope) for row in rows]
    else:
        rows = db.execute("""SELECT a.*,u.name AS sender_name,r.read_at FROM local_dp_alert_recipients r
            JOIN local_dp_alerts a ON a.id=r.alert_id LEFT JOIN users u ON u.id=a.created_by
            WHERE r.user_id=? AND r.archived_at IS NULL
            ORDER BY a.created_at DESC LIMIT 500""", (user["id"],)).fetchall()
        items = [_alert_item(db, row, scope) for row in rows]
    return {"items": items, "scope": scope, "csrf": user["csrf_token"],
            "users": _users(db, user["id"]) if user["role"] in WRITE_ROLES else [],
            "activeUserCount": len(_users(db)) if user["role"] in WRITE_ROLES else 0,
            "canCreateAlert": user["role"] in WRITE_ROLES,
            "canArchiveAlerts": user["role"] in WRITE_ROLES}


def get_messages_data(db, user, params: dict[str, list[str]]) -> dict:
    mode = "alerts" if (params.get("mode") or ["models"])[0] == "alerts" else "models"
    if mode == "alerts":
        return {"mode": mode, **_alerts_data(db, user, params)}
    return {"mode": mode, **_model_data(db, user, params)}


def _model_values(data: dict) -> tuple[str, str, str, str | None, list[str]]:
    title = " ".join(str(data.get("title") or "").split())
    body = str(data.get("body") or "").strip()
    category = " ".join(str(data.get("category") or "").split())
    client_id = str(data.get("clientId") or "").strip() or None
    tags = _clean_tags(data.get("tags", []))
    if not title or len(title) > 200:
        raise MessageError("Informe um título de até 200 caracteres.")
    if not body or len(body) > 50000:
        raise MessageError("Informe o texto da mensagem (até 50.000 caracteres).")
    if len(category) > 80:
        raise MessageError("A categoria deve ter até 80 caracteres.")
    return title, body, category, client_id, tags


def mutate_message(db, user, data: dict) -> dict:
    action = str(data.get("action") or "")
    role = user["role"]
    now = _now()
    if action in ("create_model", "update_model"):
        if role not in WRITE_ROLES:
            raise MessageError("Você não tem permissão para alterar modelos.", 403)
        title, body, category, client_id, tags = _model_values(data)
        if client_id and not (db.execute("SELECT 1 FROM v2_source_rows WHERE table_name='clients' AND (source_id=? OR json_extract(payload,'$.id')=?)", (client_id, client_id)).fetchone()
                              or (client_id.startswith("local:") and db.execute("SELECT 1 FROM local_clients WHERE id=? AND active=1", (client_id.removeprefix("local:"),)).fetchone())):
            raise MessageError("Selecione uma empresa válida.")
        encoded_tags = json.dumps(tags, ensure_ascii=False)
        normalized = _norm(" ".join((title, body, category, *tags)))
        if action == "create_model":
            item_id = "local:" + str(uuid.uuid4())
            db.execute("""INSERT INTO local_quick_messages
                (id,title,body,category,client_id,tags_json,normalized_text,created_by,updated_by)
                VALUES(?,?,?,?,?,?,?,?,?)""", (item_id, title, body, category, client_id, encoded_tags,
                    normalized, user["id"], user["id"]))
            _event(db, user["id"], "QuickMessage", item_id, "CREATED", {"title": title})
            return {"id": item_id, "message": "Modelo salvo com sucesso."}
        item_id = str(data.get("id") or "")[:180]
        previous = db.execute("SELECT id,title FROM local_quick_messages WHERE id=? AND archived_at IS NULL", (item_id,)).fetchone()
        if not previous:
            raise MessageError("Mensagem não encontrada ou arquivada.", 404)
        db.execute("""UPDATE local_quick_messages SET title=?,body=?,category=?,client_id=?,tags_json=?,normalized_text=?,
            updated_by=?,updated_at=? WHERE id=? AND archived_at IS NULL""",
            (title, body, category, client_id, encoded_tags, normalized, user["id"], now, item_id))
        _event(db, user["id"], "QuickMessage", item_id, "UPDATED", {"titleBefore": previous["title"], "titleAfter": title})
        return {"id": item_id, "message": "Alterações salvas com sucesso."}
    if action == "archive_model":
        if role not in ARCHIVE_ROLES:
            raise MessageError("Você não tem permissão para arquivar modelos.", 403)
        item_id = str(data.get("id") or "")[:180]
        result = db.execute("UPDATE local_quick_messages SET archived_at=?,updated_by=?,updated_at=? WHERE id=? AND archived_at IS NULL",
                            (now, user["id"], now, item_id))
        if not result.rowcount:
            raise MessageError("Mensagem não encontrada ou já arquivada.", 404)
        _event(db, user["id"], "QuickMessage", item_id, "ARCHIVED")
        return {"id": item_id, "message": "Modelo arquivado. Os dados e a trilha foram preservados."}
    if action == "send_alert":
        if role not in WRITE_ROLES:
            raise MessageError("Você não tem permissão para enviar alertas.", 403)
        title = " ".join(str(data.get("title") or "").split())
        body = str(data.get("body") or "").strip()
        audience = str(data.get("audienceType") or "").upper()
        if not title or len(title) > 200 or not body or len(body) > 10000:
            raise MessageError("Informe título e mensagem (até 10.000 caracteres).")
        if audience not in ("ALL", "INDIVIDUAL", "GROUP"):
            raise MessageError("Escolha quem receberá o alerta.")
        request_key = str(data.get("requestKey") or "")[:80]
        if request_key:
            existing = db.execute("SELECT id FROM local_dp_alerts WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                count = db.execute("SELECT COUNT(*) FROM local_dp_alert_recipients WHERE alert_id=?", (existing["id"],)).fetchone()[0]
                return {"id": existing["id"], "recipientCount": count, "message": f"Alerta enviado para {count} pessoas."}
        if audience == "ALL":
            recipients = db.execute("SELECT id,name,email FROM users WHERE active=1 ORDER BY id").fetchall()
        else:
            selected = data.get("recipientIds") if isinstance(data.get("recipientIds"), list) else []
            selected = list(dict.fromkeys(str(value) for value in selected if str(value).isdigit()))
            if audience == "INDIVIDUAL" and len(selected) != 1:
                raise MessageError("Selecione uma pessoa para o alerta individual.")
            if audience == "GROUP" and len(selected) < 2:
                raise MessageError("Selecione pelo menos duas pessoas para um alerta em grupo.")
            if len(selected) > 500:
                raise MessageError("O alerta pode ser direcionado a até 500 pessoas.")
            if str(user["id"]) in selected:
                raise MessageError("O remetente não pode ser destinatário individual ou de grupo.")
            if selected:
                marks = ",".join("?" for _ in selected)
                recipients = db.execute(f"SELECT id,name,email FROM users WHERE active=1 AND id IN ({marks}) ORDER BY name COLLATE NOCASE",
                                        tuple(int(value) for value in selected)).fetchall()
            else:
                recipients = []
            if len(recipients) != len(selected):
                raise MessageError("Uma ou mais pessoas selecionadas não estão ativas nesta instalação.")
        if not recipients:
            raise MessageError("Não há pessoas ativas para receber o alerta.")
        alert_id = "local:" + str(uuid.uuid4())
        db.execute("INSERT INTO local_dp_alerts(id,title,body,audience_type,created_by,created_at,request_key) VALUES(?,?,?,?,?,?,?)",
                   (alert_id, title, body, audience, user["id"], now, request_key or None))
        db.executemany("INSERT INTO local_dp_alert_recipients(alert_id,source_user_id,user_id,recipient_name,recipient_email) VALUES(?,?,?,?,?)",
                       [(alert_id, str(row["id"]), row["id"], row["name"], row["email"]) for row in recipients])
        _event(db, user["id"], "DPAlert", alert_id, "SENT", {"title": title, "audience": audience, "recipientCount": len(recipients)})
        return {"id": alert_id, "recipientCount": len(recipients), "message": f"Alerta enviado para {len(recipients)} pessoas."}
    if action == "mark_read":
        item_id = str(data.get("id") or "")[:180]
        receipt = db.execute("SELECT read_at FROM local_dp_alert_recipients WHERE alert_id=? AND user_id=?", (item_id, user["id"])).fetchone()
        if not receipt:
            raise MessageError("Alerta não encontrado para esta conta.", 404)
        if not receipt["read_at"]:
            db.execute("UPDATE local_dp_alert_recipients SET read_at=? WHERE alert_id=? AND user_id=? AND read_at IS NULL",
                       (now, item_id, user["id"]))
            _event(db, user["id"], "DPAlertReceipt", item_id, "READ_CONFIRMED")
        return {"id": item_id, "readAt": receipt["read_at"] or now, "message": "Confirmação de leitura registrada."}
    if action == "archive_alert":
        item_id = str(data.get("id") or "")[:180]
        alert = db.execute("SELECT id,created_by FROM local_dp_alerts WHERE id=? AND imported_from_v2=0", (item_id,)).fetchone()
        if not alert:
            raise MessageError("Alerta não encontrado ou importado em modo histórico.", 404)
        if data.get("scope") == "inbox":
            result = db.execute("UPDATE local_dp_alert_recipients SET archived_at=? WHERE alert_id=? AND user_id=? AND archived_at IS NULL",
                                (now, item_id, user["id"]))
            if not result.rowcount:
                raise MessageError("Alerta não encontrado na sua caixa de entrada.", 404)
            _event(db, user["id"], "DPAlertReceipt", item_id, "ARCHIVED_INBOX_COPY")
            return {"id": item_id, "message": "Alerta arquivado da sua caixa. As cópias dos demais destinatários foram mantidas."}
        if alert["created_by"] != user["id"] and role not in ARCHIVE_ROLES:
            raise MessageError("Você não tem permissão para arquivar o alerta de outra pessoa.", 403)
        db.execute("UPDATE local_dp_alerts SET sender_archived_at=? WHERE id=? AND sender_archived_at IS NULL", (now, item_id))
        _event(db, user["id"], "DPAlert", item_id, "ARCHIVED_BY_SENDER")
        return {"id": item_id, "message": "Alerta arquivado da sua lista de enviados. As cópias dos destinatários foram mantidas."}
    raise MessageError("Operação de mensagens não reconhecida.", 404)
