"""JSON read models for the Next.js Central DP pages."""
from __future__ import annotations

import json
import re
import calendar
import unicodedata
from datetime import date, timedelta

from .home import cases_for_home, filter_home_cases, home_counts
from .chat import unread_count as chat_unread_count


def _description_from_json_text(value):
    match = re.search(r'"description"\s*:\s*("(?:\\.|[^"\\])*")', str(value or ""))
    if not match:
        return ""
    try: return json.loads(match.group(1))
    except ValueError: return ""


def home_data(db, user, scope="mine"):
    scope = "team" if scope == "team" and user["role"] in ("ADMIN", "GESTOR") else "mine"
    today = date.today()
    population = cases_for_home(db, user["id"], scope, include_closed=True)
    records = [row for row in population if row["status"] in ("ABERTO", "EM_ANDAMENTO", "OPEN", "IN_PROGRESS")]
    counts = home_counts(records, today)
    counts["waiting"] = sum(row["status"] in ("AGUARDANDO", "WAITING") for row in population)
    counts["completed"] = sum(row["status"] in ("CONCLUIDO", "DONE") for row in population)
    case_rows = filter_home_cases(records, "all", today)[:10]
    process_rows = []
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='procedures'"):
        item = json.loads(row["payload"])
        if item.get("deleted_at") or str(item.get("status") or "ACTIVE").upper() in ("ARCHIVED", "INACTIVE", "DELETED"):
            continue
        process_rows.append({"id": row["source_id"], "title": item.get("title") or "Processo"})
    process_rows.sort(key=lambda item: item["title"].casefold())
    unread = chat_unread_count(db, user["id"])
    return {"scope": scope, "csrf": user["csrf_token"], "metrics": counts, "cases": case_rows, "processes": process_rows[:5], "unread": unread,
            "updatedAt": today.isoformat()}


def processes_data(db, query, user):
    params = {key: (values[0] if values else "")[:120] for key, values in query.items()}
    clients = {}
    for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='clients'"):
        item = json.loads(row[0])
        clients[str(item.get("id") or "")] = str(item.get("name") or item.get("legal_name") or item.get("legalName") or "Empresa importada")
    versions = {}
    for version_row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='procedure_versions' ORDER BY source_id"):
        version = json.loads(version_row["payload"])
        if not isinstance(version, dict) or version.get("deleted_at"):
            continue
        try:
            version_number = int(version.get("number") or 0)
        except (TypeError, ValueError):
            version_number = 0
        procedure = str(version.get("procedure_id") or "")
        if procedure and (procedure not in versions or version_number > versions[procedure][0]):
            versions[procedure] = (version_number, version)
    rows = []
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='procedures'"):
        item = json.loads(row["payload"])
        if item.get("deleted_at"):
            continue
        procedure_id = str(item.get("id") or row["source_id"])
        version = versions.get(procedure_id, (0, {}))[1]
        version_content = version.get("content") or {}
        if isinstance(version_content, str):
            try: version_content = json.loads(version_content)
            except ValueError: version_content = {"description": _description_from_json_text(version_content)}
        if not isinstance(version_content, dict):
            version_content = {}
        nested_description = version_content.get("description") if isinstance(version_content, dict) else None
        if isinstance(nested_description, str) and nested_description.lstrip().startswith("{"):
            try: embedded_version = json.loads(nested_description)
            except ValueError: embedded_version = {"description": _description_from_json_text(nested_description)}
            if isinstance(embedded_version, dict):
                version_content.update({key: value for key, value in embedded_version.items() if key not in version_content or key == "description"})
        description = item.get("description") or ""
        if isinstance(description, str) and description.lstrip().startswith("{"):
            try: embedded = json.loads(description)
            except ValueError: embedded = {}
            if isinstance(embedded, dict):
                if not version_content: version_content = embedded
                item["status"] = item.get("status") or embedded.get("status")
                item["source"] = item.get("source") or embedded.get("sourceCode") or embedded.get("source")
                item["current_version"] = item.get("current_version") or embedded.get("version")
                description = embedded.get("description") or ""
        item["description"] = description or version_content.get("description") or f"Registro de {str(item.get('title') or 'processo').casefold()} identificado na fonte, ainda sem detalhamento textual suficiente para publicação."
        item["content"] = version_content
        item["source"] = item.get("source") or version.get("source") or "—"
        item["current_version"] = item.get("current_version") or version.get("number") or 1
        item["valid_from"] = item.get("valid_from") or version.get("valid_from")
        item.update({"id": row["source_id"], "client_name": clients.get(str(item.get("client_id") or ""), ""), "local": False})
        rows.append(item)
    for row in db.execute("SELECT id,title,category,body,updated_at FROM local_content WHERE kind='PROCESSO' ORDER BY title COLLATE NOCASE"):
        rows.append({"id": f"local:{row['id']}", "title": row["title"], "category": row["category"], "system": "Interno",
                     "status": "LOCAL", "description": row["body"], "client_name": "", "local": True, "updated_at": row["updated_at"]})
    visible = []
    search = params.get("q", "").strip().casefold()
    filters = {"category": "category", "system": "system", "client": "client_name", "unit": "unit_name", "status": "status"}
    for item in rows:
        searchable = " ".join(str(item.get(key) or "") for key in ("title", "description", "category", "system", "client_name", "unit_name")).casefold()
        if search and search not in searchable:
            continue
        if any(params.get(param) and str(item.get(field) or "") != params[param] for param, field in filters.items()):
            continue
        visible.append({key: item.get(key) for key in ("id", "title", "category", "system", "status", "current_version", "description", "client_name", "unit_name", "valid_from", "source", "content", "steps", "checklist", "local")})
    visible.sort(key=lambda item: str(item.get("title") or "").casefold())
    choices = {key: sorted({str(item.get(field) or "") for item in rows if item.get(field)}, key=str.casefold)
               for key, field in (("category", "category"), ("system", "system"), ("client", "client_name"), ("unit", "unit_name"), ("status", "status"))}
    from .sector_assets import enrich_processes
    enrich_processes(db, visible, user)
    return {"csrf": user["csrf_token"], "items": visible, "choices": choices, "total": len(visible)}


def calendar_data(db, query, user):
    """Read only the visible calendar range and its directly related layers."""
    params = {key: (values[0] if values else "")[:160] for key, values in query.items()}
    requested = params.get("month", "")
    try:
        first = date.fromisoformat(requested + "-01") if re.fullmatch(r"\d{4}-\d{2}", requested) else date.today().replace(day=1)
    except ValueError:
        first = date.today().replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    month_end = first.replace(day=last_day)
    month = first.strftime("%Y-%m")
    grid_start = first - timedelta(days=(first.weekday() + 1) % 7)
    # Keep a six-week month grid so the following month's first week is visible.
    grid_end = grid_start + timedelta(days=41)
    mode = params.get("mode") if params.get("mode") in ("month", "week", "agenda", "upcoming") else "month"
    start, end = grid_start.isoformat(), grid_end.isoformat()
    if mode == "upcoming":
        start, end = date.today().isoformat(), (date.today() + timedelta(days=14)).isoformat()
    client_id = params.get("client", "")

    clients = {}
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='clients'"):
        try: item = json.loads(row["payload"])
        except (TypeError, ValueError): continue
        if str(item.get("active", 1)).casefold() in ("0", "false", "inactive"): continue
        key = str(item.get("id") or row["source_id"])
        clients[key] = {"id": key, "name": str(item.get("name") or item.get("legal_name") or item.get("legalName") or "Empresa")}
    for row in db.execute("SELECT id,code,name FROM local_clients WHERE active=1 ORDER BY name COLLATE NOCASE"):
        key = f"local-client:{row['id']}"
        clients[key] = {"id": key, "name": (f"{row['code']} · " if row["code"] else "") + row["name"]}
    clients_list = sorted(clients.values(), key=lambda item: item["name"].casefold())
    client_name = clients.get(client_id, {}).get("name", "")
    client_city = ""
    client_state = ""
    if client_id:
        profile_row = db.execute("SELECT profile_json FROM local_company_profiles WHERE client_key=?", (client_id,)).fetchone()
        if profile_row:
            try: profile = json.loads(profile_row[0])
            except (TypeError, ValueError): profile = {}
            client_city, client_state = str(profile.get("city") or ""), str(profile.get("state") or "")
        if not client_city:
            profile_row = db.execute("SELECT payload FROM v2_source_rows WHERE table_name='client_company_profiles' AND json_extract(payload,'$.client_id')=? ORDER BY source_id LIMIT 1", (client_id,)).fetchone()
            if profile_row:
                try:
                    profile = json.loads(profile_row[0]); profile = json.loads(profile.get("profile_json") or "{}") if isinstance(profile.get("profile_json"), str) else profile.get("profile_json") or {}
                    client_city, client_state = str(profile.get("city") or ""), str(profile.get("state") or "")
                except (TypeError, ValueError): pass

    event_rows = db.execute("""SELECT e.id,e.title,e.due_date,e.starts_at,e.category,e.status,e.description,e.notes,e.priority,e.created_by,
        m.source_id,p.payload AS source_payload FROM local_calendar_events e
        LEFT JOIN local_calendar_source_map m ON m.local_event_id=e.id
        LEFT JOIN v2_source_rows p ON p.table_name='calendar_events' AND p.source_id=m.source_id
        WHERE e.archived=0 AND e.due_date BETWEEN ? AND ? ORDER BY e.due_date,e.starts_at,e.id""", (start, end)).fetchall()
    search = params.get("q", "").strip().casefold()
    category_filter = params.get("category", "")
    event_clients = {}
    events = []
    for row in event_rows:
        payload = {}
        if row["source_payload"]:
            try: payload = json.loads(row["source_payload"])
            except (TypeError, ValueError): payload = {}
        related_client = str(payload.get("client_id") or "")
        related_name = clients.get(related_client, {}).get("name", "")
        if client_id and related_client != client_id: continue
        if params.get("mine") == "1" and int(row["created_by"] or -1) != int(user["id"]): continue
        haystack = " ".join(str(value or "") for value in (row["title"], row["category"], row["description"], row["notes"], related_name)).casefold()
        if search and search not in haystack: continue
        if category_filter and str(row["category"] or "") != category_filter: continue
        checklist = [dict(item) for item in db.execute("SELECT id,title,done FROM local_calendar_checklist WHERE event_id=? ORDER BY id", (row["id"],))]
        history = [dict(item) for item in db.execute("SELECT h.action,h.detail,h.created_at,u.name AS actor FROM local_calendar_history h JOIN users u ON u.id=h.actor_id WHERE h.event_id=? ORDER BY h.id DESC LIMIT 8", (row["id"],))]
        case_id = row["source_id"].removeprefix("local-case:") if str(row["source_id"] or "").startswith("local-case:") else ""
        events.append({"id": row["id"], "title": row["title"], "date": row["due_date"], "time": (row["starts_at"] or "")[11:16], "category": row["category"] or "Evento", "status": row["status"], "description": row["description"] or row["notes"] or "", "priority": row["priority"], "clientId": related_client, "clientName": related_name, "source": row["source_id"] or "LOCAL", "mine": int(row["created_by"] or -1) == int(user["id"]), "caseId": case_id, "checklist": checklist, "history": history, "canManage": not row["source_id"] and user["role"] in ("ADMIN", "GESTOR")})
        if row["category"]: event_clients[row["category"]] = True

    def norm(value):
        return " ".join("".join(char for char in unicodedata.normalize("NFKD", str(value or "").casefold()) if not unicodedata.combining(char)).split())
    holiday_by_day = {}
    source_holidays = db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calendar_holidays' AND json_extract(payload,'$.day') BETWEEN ? AND ?", (start, end)).fetchall()
    holidays = [json.loads(row[0]) for row in source_holidays]
    for row in db.execute("SELECT day,name,state,city,source_id FROM local_calendar_holidays WHERE day BETWEEN ? AND ?", (start, end)):
        holidays.append({"day": row["day"], "name": row["name"], "state": row["state"], "city": row["city"], "kind": "NACIONAL" if not row["city"] and not row["state"] else "MUNICIPAL" if row["city"] else "ESTADUAL", "source": "Registro local"})
    seen = set()
    for item in holidays:
        day, name = str(item.get("day") or ""), str(item.get("name") or "Feriado")
        if not (start <= day <= end): continue
        kind = str(item.get("kind") or ("MUNICIPAL" if item.get("city") else "ESTADUAL" if item.get("state") else "NACIONAL")).upper()
        city, state = str(item.get("city") or ""), str(item.get("state") or "")
        if kind == "MUNICIPAL" and (not client_id or not client_city or norm(city) != norm(client_city) or (state and client_state and norm(state) != norm(client_state))): continue
        if kind == "ESTADUAL" and client_state and state and norm(state) != norm(client_state): continue
        if kind == "ESTADUAL" and not client_id: continue
        key = (day, norm(name))
        if key in seen: continue
        seen.add(key)
        holiday_by_day.setdefault(day, []).append({"date": day, "name": name, "kind": kind, "city": city, "state": state, "source": str(item.get("source") or "Fonte importada")})

    imported_relations = {}
    for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days' AND json_extract(payload,'$.day') BETWEEN ? AND ?", (start, end)):
        try: item = json.loads(row[0])
        except (TypeError, ValueError): continue
        if str(item.get("scope_type") or "GENERAL") == "GENERAL": imported_relations[str(item.get("day"))] = item
    if client_id:
        for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days' AND json_extract(payload,'$.day') BETWEEN ? AND ?", (start, end)):
            try: item = json.loads(row[0])
            except (TypeError, ValueError): continue
            if str(item.get("scope_type") or "GENERAL") == "CLIENT" and str(item.get("client_id") or "") == client_id:
                imported_relations[str(item.get("day"))] = item
    local_relations = {}
    for row in db.execute("SELECT day,scope_type,scope_key,state,note,updated_by,updated_at FROM local_bank_relations WHERE day BETWEEN ? AND ? AND scope_type='GENERAL' AND scope_key=''", (start, end)):
        local_relations[row["day"]] = dict(row)
    if client_id:
        for row in db.execute("SELECT day,scope_type,scope_key,state,note,updated_by,updated_at FROM local_bank_relations WHERE day BETWEEN ? AND ? AND scope_type='CLIENT' AND scope_key=?", (start, end, client_id)):
            local_relations[row["day"]] = dict(row)
    relations = {}
    for day, item in imported_relations.items():
        if day in local_relations: continue
        state = str(item.get("state") or "").upper()
        state = {"SIM": "YES", "TRUE": "YES", "NAO": "NO", "NÃO": "NO"}.get(state, state if state in ("YES", "NO", "OTHER") else "")
        relations[day] = {"state": state, "note": str(item.get("note") or ""), "source": "Importado V2", "updatedAt": item.get("updated_at") or ""}
    for day, item in local_relations.items():
        relations[day] = {"state": item.get("state") or "", "note": item.get("note") or "", "source": "Local", "updatedAt": item.get("updated_at") or ""}

    note_row = db.execute("SELECT body,updated_by,updated_at FROM local_calendar_month_notes WHERE month=?", (month,)).fetchone()
    note_history = db.execute("SELECT old_state,new_state,old_note,new_note,changed_at,u.name AS actor FROM local_bank_relation_history h JOIN users u ON u.id=h.actor_id WHERE h.day=? AND h.scope_type=? AND h.scope_key=? ORDER BY h.id DESC LIMIT 12", (params.get("day") or date.today().isoformat(), "CLIENT" if client_id else "GENERAL", client_id or "")).fetchall()
    categories = sorted({str(row["category"]) for row in db.execute("SELECT DISTINCT category FROM local_calendar_events WHERE archived=0 AND category<>''")}, key=str.casefold)
    return {"csrf": user["csrf_token"], "role": user["role"], "canEdit": user["role"] in ("ADMIN", "GESTOR"), "month": month, "mode": mode, "clientId": client_id, "clientName": client_name, "city": client_city, "state": client_state, "clients": clients_list, "categories": categories, "events": events, "holidays": [holiday for values in holiday_by_day.values() for holiday in values], "relations": relations, "monthlyNote": dict(note_row) if note_row else None, "history": [dict(row) for row in note_history], "rangeStart": start, "rangeEnd": end, "mine": params.get("mine") == "1", "filter": {"q": search, "category": category_filter}}
