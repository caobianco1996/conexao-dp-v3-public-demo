"""Internal chat service. Every read and mutation is scoped to session membership."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import uuid
from pathlib import Path

MAX_BODY = 5000
MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
ALLOWED_MIME = {
    "application/pdf", "text/plain", "text/csv",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MIME_EXT = {".pdf", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".docx", ".xlsx", ".pptx"}
EXT_MIME = {".pdf":{"application/pdf"},".txt":{"text/plain"},".csv":{"text/csv","application/csv","application/vnd.ms-excel"},".png":{"image/png"},".jpg":{"image/jpeg"},".jpeg":{"image/jpeg"},".gif":{"image/gif"},".webp":{"image/webp"},".zip":{"application/zip"},".docx":{"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},".xlsx":{"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},".pptx":{"application/vnd.openxmlformats-officedocument.presentationml.presentation"}}


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status
        super().__init__(message)


PROFILE_AVATAR_IDS = {f"{group}-{number}" for group in ("man", "woman") for number in range(1, 9)}
PROFILE_MOOD_IDS = {"irritated", "confused", "happy", "melancholy", "anxious", "serene", "enthusiastic", "exhausted"}


def get_profile_avatar(db, user_id):
    row = db.execute("SELECT avatar_id FROM local_user_preferences WHERE user_id=?", (user_id,)).fetchone()
    avatar_id = row["avatar_id"] if row else "man-1"
    return avatar_id if avatar_id in PROFILE_AVATAR_IDS else "man-1"


def set_profile_avatar(db, user_id, avatar_id):
    avatar_id = str(avatar_id or "")
    if avatar_id not in PROFILE_AVATAR_IDS:
        raise ChatError("Miniatura inválida.")
    db.execute("""
      INSERT INTO local_user_preferences(user_id,avatar_id) VALUES(?,?)
      ON CONFLICT(user_id) DO UPDATE SET avatar_id=excluded.avatar_id,updated_at=CURRENT_TIMESTAMP
    """, (user_id, avatar_id))
    return avatar_id


def get_profile_mood(db, user_id):
    row = db.execute("SELECT mood_id FROM local_user_preferences WHERE user_id=?", (user_id,)).fetchone()
    mood_id = row["mood_id"] if row else "serene"
    return mood_id if mood_id in PROFILE_MOOD_IDS else "serene"


def set_profile_mood(db, user_id, mood_id):
    mood_id = str(mood_id or "")
    if mood_id not in PROFILE_MOOD_IDS:
        raise ChatError("Humor do perfil inválido.")
    db.execute("""
      INSERT INTO local_user_preferences(user_id,avatar_id,mood_id) VALUES(?,'man-1',?)
      ON CONFLICT(user_id) DO UPDATE SET mood_id=excluded.mood_id,updated_at=CURRENT_TIMESTAMP
    """, (user_id, mood_id))
    return mood_id


def ensure_schema(db):
    preferences = {row[1] for row in db.execute("PRAGMA table_info(local_user_preferences)")}
    if "mood_id" not in preferences:
        db.execute("ALTER TABLE local_user_preferences ADD COLUMN mood_id TEXT NOT NULL DEFAULT 'serene'")
    members = {row[1] for row in db.execute("PRAGMA table_info(local_conversation_members)")}
    if "last_read_message_id" not in members:
        db.execute("ALTER TABLE local_conversation_members ADD COLUMN last_read_message_id INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE local_conversation_members SET last_read_message_id=COALESCE((SELECT MAX(x.id) FROM local_messages x WHERE x.conversation_id=local_conversation_members.conversation_id AND local_conversation_members.last_read_at IS NOT NULL AND x.created_at<=local_conversation_members.last_read_at),0)")
    messages = {row[1] for row in db.execute("PRAGMA table_info(local_messages)")}
    if "client_message_id" not in messages:
        db.execute("ALTER TABLE local_messages ADD COLUMN client_message_id TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS local_messages_idempotency ON local_messages(conversation_id,sender_id,client_message_id) WHERE client_message_id IS NOT NULL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_chat_requests(
      request_id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL REFERENCES local_conversations(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS local_chat_attachments(
      id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES local_messages(id) ON DELETE CASCADE,
      storage_key TEXT NOT NULL UNIQUE, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
      size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_by INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS local_chat_attachments_message ON local_chat_attachments(message_id);
    CREATE TABLE IF NOT EXISTS local_chat_audit(
      id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
      conversation_id TEXT, message_id INTEGER, details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)


def _audit(db, actor, action, conversation_id=None, message_id=None, details=None):
    db.execute("INSERT INTO local_chat_audit(actor_id,action,conversation_id,message_id,details_json) VALUES(?,?,?,?,?)",
               (actor, action, conversation_id, message_id, json.dumps(details or {}, ensure_ascii=False)))


def _require_member(db, conversation_id, user_id):
    row = db.execute("SELECT c.id,c.kind,c.title FROM local_conversations c JOIN local_conversation_members m ON m.conversation_id=c.id WHERE c.id=? AND m.user_id=?", (conversation_id, user_id)).fetchone()
    if not row:
        raise ChatError("Conversa indisponível.", 404)
    return row


def _person(db, user_id):
    return db.execute("SELECT id,name FROM users WHERE id=? AND active=1", (user_id,)).fetchone()


def unread_count(db, user_id):
    row = db.execute("SELECT COUNT(*) n FROM local_conversation_members m JOIN local_messages x ON x.conversation_id=m.conversation_id WHERE m.user_id=? AND x.sender_id<>? AND x.id>m.last_read_message_id", (user_id, user_id)).fetchone()
    return int(row["n"] or 0)


def list_conversations(db, user_id, query=""):
    query = " ".join(str(query or "").split())[:120]
    rows = db.execute("""
      SELECT c.id,c.kind,c.title,c.updated_at,m.last_read_message_id,
        (SELECT id FROM local_messages x WHERE x.conversation_id=c.id ORDER BY x.id DESC LIMIT 1) last_id,
        (SELECT body FROM local_messages x WHERE x.conversation_id=c.id ORDER BY x.id DESC LIMIT 1) last_body,
        (SELECT COUNT(*) FROM local_chat_attachments a WHERE a.message_id=(SELECT x.id FROM local_messages x WHERE x.conversation_id=c.id ORDER BY x.id DESC LIMIT 1)) last_attachment_count,
        (SELECT u.name FROM local_messages x JOIN users u ON u.id=x.sender_id WHERE x.conversation_id=c.id ORDER BY x.id DESC LIMIT 1) last_author,
        (SELECT COUNT(*) FROM local_messages x WHERE x.conversation_id=c.id AND x.sender_id<>? AND x.id>m.last_read_message_id) unread,
        (SELECT group_concat(u.name, ', ') FROM local_conversation_members cm JOIN users u ON u.id=cm.user_id WHERE cm.conversation_id=c.id AND cm.user_id<>? AND u.active=1) others,
        (SELECT COALESCE(p.avatar_id,'man-1') FROM local_conversation_members cm JOIN users u ON u.id=cm.user_id LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE cm.conversation_id=c.id AND cm.user_id<>? AND u.active=1 ORDER BY u.name COLLATE NOCASE LIMIT 1) others_avatar,
        (SELECT COALESCE(p.mood_id,'serene') FROM local_conversation_members cm JOIN users u ON u.id=cm.user_id LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE cm.conversation_id=c.id AND cm.user_id<>? AND u.active=1 ORDER BY u.name COLLATE NOCASE LIMIT 1) others_mood
      FROM local_conversation_members m JOIN local_conversations c ON c.id=m.conversation_id
      WHERE m.user_id=? AND EXISTS(SELECT 1 FROM users me WHERE me.id=m.user_id AND me.active=1)
      ORDER BY COALESCE((SELECT MAX(id) FROM local_messages x WHERE x.conversation_id=c.id),0) DESC,c.updated_at DESC
    """, (user_id,user_id,user_id,user_id,user_id)).fetchall()
    result=[]
    for row in rows:
        title = row["title"] or (row["others"] if row["kind"] == "DIRECT" else row["others"] or "Grupo") or "Conversa"
        if query:
            matched = query.casefold() in title.casefold() or query.casefold() in (row["last_body"] or "").casefold()
            matched = matched or bool(db.execute("SELECT 1 FROM local_conversation_members m JOIN users u ON u.id=m.user_id WHERE m.conversation_id=? AND m.user_id<>? AND u.active=1 AND u.name LIKE ?",(row["id"],user_id,f"%{query}%")).fetchone())
            matched = matched or bool(db.execute("SELECT 1 FROM local_messages x WHERE x.conversation_id=? AND x.body LIKE ? LIMIT 1",(row["id"],f"%{query}%")).fetchone())
            if not matched: continue
        preview=row["last_body"] or ""
        if preview=="\u200b": preview="Arquivo anexado" if row["last_attachment_count"] else ""
        result.append({"id":row["id"],"kind":row["kind"],"title":title,"avatarId":row["others_avatar"] or "man-1","moodId":row["others_mood"] or "serene","updatedAt":row["updated_at"],"lastId":row["last_id"],"lastBody":preview,"lastAuthor":row["last_author"],"unread":int(row["unread"] or 0)})
    return result


def people(db, user_id):
    return [dict(row) for row in db.execute("SELECT u.id,u.name,COALESCE(p.avatar_id,'man-1') avatarId,COALESCE(p.mood_id,'serene') moodId FROM users u LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE u.active=1 AND u.id<>? ORDER BY u.name COLLATE NOCASE",(user_id,)).fetchall()]


def create_direct(db, user_id, other_id):
    if not isinstance(other_id,int) or other_id == user_id or not _person(db,other_id):
        raise ChatError("Selecione uma pessoa ativa.")
    left,right=sorted((user_id,other_id)); key=f"{left}:{right}"
    db.execute("BEGIN IMMEDIATE")
    row=db.execute("SELECT id FROM local_conversations WHERE kind='DIRECT' AND direct_key=?",(key,)).fetchone()
    if not row:
        conv_id=str(uuid.uuid4())
        db.execute("INSERT INTO local_conversations(id,kind,title,direct_key,created_by) VALUES(?,'DIRECT','',?,?)",(conv_id,key,user_id))
        db.executemany("INSERT INTO local_conversation_members(conversation_id,user_id,last_read_message_id) VALUES(?,?,0)",[(conv_id,left),(conv_id,right)])
        _audit(db,user_id,"DIRECT_CREATED",conv_id)
    else: conv_id=row["id"]
    return conv_id


def create_group(db,user_id,member_ids,title,request_id):
    if not isinstance(member_ids,list) or not request_id or len(request_id)>100:
        raise ChatError("Dados do grupo inválidos.")
    ids=[]
    for value in member_ids:
        if isinstance(value,int) and value not in ids: ids.append(value)
    if user_id not in ids: ids.append(user_id)
    if len(ids)<3 or len(ids)>51 or any(not _person(db,i) for i in ids):
        raise ChatError("Selecione pelo menos duas pessoas ativas para o grupo.")
    title=" ".join(str(title or "").split())
    if len(title)>80: raise ChatError("O nome do grupo deve ter até 80 caracteres.")
    db.execute("BEGIN IMMEDIATE")
    prior=db.execute("SELECT conversation_id FROM local_chat_requests WHERE request_id=? AND actor_id=?",(request_id,user_id)).fetchone()
    if prior:return prior["conversation_id"]
    conv_id=str(uuid.uuid4())
    db.execute("INSERT INTO local_conversations(id,kind,title,created_by) VALUES(?,'GROUP',?,?)",(conv_id,title,user_id))
    db.executemany("INSERT INTO local_conversation_members(conversation_id,user_id,last_read_message_id) VALUES(?,?,0)",[(conv_id,i) for i in ids])
    db.execute("INSERT INTO local_chat_requests(request_id,actor_id,conversation_id) VALUES(?,?,?)",(request_id,user_id,conv_id))
    _audit(db,user_id,"GROUP_CREATED",conv_id,details={"memberCount":len(ids),"title":title})
    return conv_id


def get_messages(db,user_id,conversation_id,before=None,since=None,limit=50):
    conversation=_require_member(db,conversation_id,user_id)
    membership=db.execute("SELECT last_read_message_id FROM local_conversation_members WHERE conversation_id=? AND user_id=?",(conversation_id,user_id)).fetchone()
    read_cursor=int(membership["last_read_message_id"] or 0)
    limit=max(1,min(int(limit or 50),100))
    members=[dict(row) for row in db.execute("SELECT u.id,u.name,COALESCE(p.avatar_id,'man-1') avatarId,COALESCE(p.mood_id,'serene') moodId FROM local_conversation_members m JOIN users u ON u.id=m.user_id LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE m.conversation_id=? AND u.active=1 ORDER BY u.name COLLATE NOCASE",(conversation_id,)).fetchall()]
    if since is not None:
        rows=db.execute("SELECT x.id,x.sender_id,u.name sender_name,COALESCE(p.avatar_id,'man-1') sender_avatar_id,COALESCE(p.mood_id,'serene') sender_mood_id,x.body,x.created_at FROM local_messages x JOIN users u ON u.id=x.sender_id LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE x.conversation_id=? AND x.id>? ORDER BY x.id LIMIT ?",(conversation_id,int(since),limit)).fetchall()
        has_older=False
    else:
        cursor=int(before) if before is not None else 9223372036854775807
        rows=db.execute("SELECT x.id,x.sender_id,u.name sender_name,COALESCE(p.avatar_id,'man-1') sender_avatar_id,COALESCE(p.mood_id,'serene') sender_mood_id,x.body,x.created_at FROM local_messages x JOIN users u ON u.id=x.sender_id LEFT JOIN local_user_preferences p ON p.user_id=u.id WHERE x.conversation_id=? AND x.id<? ORDER BY x.id DESC LIMIT ?",(conversation_id,cursor,limit)).fetchall()[::-1]
        has_older=bool(rows) and db.execute("SELECT 1 FROM local_messages WHERE conversation_id=? AND id<? LIMIT 1",(conversation_id,rows[0]["id"])).fetchone() is not None
    messages=[]
    for row in rows:
        attachments=[dict(a) for a in db.execute("SELECT id,filename,mime_type,size_bytes FROM local_chat_attachments WHERE message_id=? ORDER BY id",(row["id"],)).fetchall()]
        for a in attachments:a["url"]=f"/api/chat/files/{a['id']}"
        messages.append({"id":row["id"],"senderId":row["sender_id"],"senderName":row["sender_name"],"avatarId":row["sender_avatar_id"],"moodId":row["sender_mood_id"],"body":"" if row["body"]=="\u200b" else row["body"],"createdAt":row["created_at"],"attachments":attachments})
    unread_before=bool(messages and db.execute("SELECT 1 FROM local_messages WHERE conversation_id=? AND id>? AND id<? AND sender_id<>? LIMIT 1",(conversation_id,read_cursor,messages[0]["id"],user_id)).fetchone())
    return {"conversation":{"id":conversation["id"],"kind":conversation["kind"],"title":conversation["title"]},"members":members,"messages":messages,"hasOlder":has_older,"unreadTotal":unread_count(db,user_id),"lastReadMessageId":read_cursor,"unreadBeforePage":unread_before}


def _decode_files(files):
    if not isinstance(files,list) or len(files)>MAX_FILES: raise ChatError("No máximo 10 arquivos por mensagem.")
    parsed=[]; total=0
    for item in files:
        if not isinstance(item,dict): raise ChatError("Arquivo inválido.")
        name=Path(str(item.get("name") or "").replace("\\","/").replace("\x00","")).name.strip()[:180]
        ext=Path(name).suffix.lower(); mime=str(item.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream").lower()
        data64=str(item.get("data") or "")
        if not name or ext not in MIME_EXT or mime not in EXT_MIME.get(ext,set()) or mime not in ALLOWED_MIME or len(data64)>((MAX_FILE_BYTES+2)//3*4+8): raise ChatError("Tipo ou nome de arquivo não permitido.")
        try: raw=base64.b64decode(data64,validate=True)
        except (ValueError,base64.binascii.Error): raise ChatError("Arquivo inválido.")
        if not raw or len(raw)>MAX_FILE_BYTES: raise ChatError("Cada arquivo deve ter até 10 MB.")
        signatures={".png":raw.startswith(b"\x89PNG\r\n\x1a\n"),".jpg":raw.startswith(b"\xff\xd8\xff"),".jpeg":raw.startswith(b"\xff\xd8\xff"),".pdf":raw.startswith(b"%PDF-"),".gif":raw.startswith((b"GIF87a",b"GIF89a")),".webp":raw.startswith(b"RIFF") and raw[8:12]==b"WEBP"}
        if ext in signatures and not signatures[ext] or ext in (".zip",".docx",".xlsx",".pptx") and not raw.startswith(b"PK\x03\x04"):
            raise ChatError("O conteúdo não corresponde ao tipo do arquivo.")
        total+=len(raw); parsed.append((name,mime,raw))
    if total>MAX_TOTAL_BYTES: raise ChatError("Os anexos da mensagem ultrapassam 20 MB.")
    return parsed


def send_message(db,user_id,conversation_id,payload,storage_dir):
    _require_member(db,conversation_id,user_id)
    body=str(payload.get("body") or "").strip()
    key=str(payload.get("clientMessageId") or "")
    if len(body)>MAX_BODY or not key or len(key)>100: raise ChatError("Mensagem ou identificador inválido.")
    files=_decode_files(payload.get("files",[]))
    if not body and not files: raise ChatError("Escreva uma mensagem ou anexe um arquivo.")
    prior=db.execute("SELECT id FROM local_messages WHERE conversation_id=? AND sender_id=? AND client_message_id=?",(conversation_id,user_id,key)).fetchone()
    if prior:return int(prior["id"])
    db.execute("BEGIN IMMEDIATE")
    prior=db.execute("SELECT id FROM local_messages WHERE conversation_id=? AND sender_id=? AND client_message_id=?",(conversation_id,user_id,key)).fetchone()
    if prior:return int(prior["id"])
    message_id=db.execute("INSERT INTO local_messages(conversation_id,sender_id,body,client_message_id) VALUES(?,?,?,?)",(conversation_id,user_id,body or "\u200b",key)).lastrowid
    storage_dir.mkdir(parents=True,exist_ok=True)
    created=[]
    try:
        for name,mime,raw in files:
            aid=uuid.uuid4().hex; storage_key=secrets.token_hex(24); target=storage_dir/storage_key
            created.append(target)
            with target.open("xb") as handle: handle.write(raw)
            db.execute("INSERT INTO local_chat_attachments(id,message_id,storage_key,filename,mime_type,size_bytes,sha256,uploaded_by) VALUES(?,?,?,?,?,?,?,?)",(aid,message_id,storage_key,name,mime,len(raw),hashlib.sha256(raw).hexdigest(),user_id))
        db.execute("UPDATE local_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",(conversation_id,))
        _audit(db,user_id,"MESSAGE_SENT",conversation_id,message_id,{"attachmentCount":len(files)})
        return int(message_id)
    except Exception:
        for path in created:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        raise


def mark_read(db,user_id,conversation_id,message_id):
    _require_member(db,conversation_id,user_id)
    if not isinstance(message_id,int) or message_id<0:raise ChatError("Recibo inválido.")
    existing=db.execute("SELECT id FROM local_messages WHERE id=? AND conversation_id=?",(message_id,conversation_id)).fetchone() if message_id else None
    if message_id and not existing:raise ChatError("Mensagem indisponível.",404)
    previous=db.execute("SELECT last_read_message_id FROM local_conversation_members WHERE conversation_id=? AND user_id=?",(conversation_id,user_id)).fetchone()[0]
    if message_id>previous:
        db.execute("UPDATE local_conversation_members SET last_read_message_id=? WHERE conversation_id=? AND user_id=?",(message_id,conversation_id,user_id))
        _audit(db,user_id,"READ_CURSOR_ADVANCED",conversation_id,details={"messageId":message_id})
    return unread_count(db,user_id)


def get_attachment(db,user_id,attachment_id,storage_dir):
    row=db.execute("SELECT a.* FROM local_chat_attachments a JOIN local_messages x ON x.id=a.message_id JOIN local_conversation_members m ON m.conversation_id=x.conversation_id AND m.user_id=? JOIN users u ON u.id=? AND u.active=1 WHERE a.id=?",(user_id,user_id,attachment_id)).fetchone()
    if not row:raise ChatError("Arquivo indisponível.",404)
    target=(storage_dir/row["storage_key"]).resolve()
    if target.parent!=storage_dir.resolve() or not target.is_file():raise ChatError("Arquivo indisponível.",404)
    return row,target


def audit_attachment_access(db,user_id,attachment_id):
    row=db.execute("SELECT x.conversation_id,x.id message_id FROM local_chat_attachments a JOIN local_messages x ON x.id=a.message_id WHERE a.id=?",(attachment_id,)).fetchone()
    if not row: return
    _audit(db,user_id,"ATTACHMENT_DOWNLOADED",row["conversation_id"],row["message_id"])
