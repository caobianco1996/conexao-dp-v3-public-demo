"""Operational Home queries over local cases and the read-only V2 snapshot."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from urllib.parse import quote


ACTIVE_LOCAL = {"ABERTO", "EM_ANDAMENTO"}
ACTIVE_V2 = {"OPEN", "IN_PROGRESS"}
PRIORITY_ORDER = {"URGENT": 0, "URGENTE": 0, "HIGH": 1, "ALTA": 1, "NORMAL": 2, "LOW": 3, "BAIXA": 3}


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_time(value):
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def cases_for_home(db, user_id: int, scope: str = "mine", *, include_closed=False) -> list[dict]:
    """Return the shared, active population used by Home cards and case lists.

    V2 source rows stay read-only. Explicit local owner links may associate a
    source case with a local account without changing its imported payload.
    """
    scope = "team" if scope == "team" else "mine"
    local = db.execute("""SELECT c.id,c.kind,c.title,c.reference,c.status,c.due_date,c.owner_id,
        c.created_at,c.updated_at,u.name owner_name,
        (SELECT COUNT(*) FROM case_checklist x WHERE x.case_id=c.id) checklist_total,
        (SELECT COUNT(*) FROM case_checklist x WHERE x.case_id=c.id AND x.done=0) checklist_pending
        FROM process_cases c LEFT JOIN users u ON u.id=c.owner_id
        WHERE (c.status IN ('ABERTO','EM_ANDAMENTO') OR (?=1 AND c.status IN ('AGUARDANDO','CONCLUIDO')))
        AND (?='team' OR c.owner_id=?)""", (int(include_closed), scope, user_id)).fetchall()
    records = [{
        "source": "V3", "source_id": str(r["id"]), "local_id": r["id"],
        "title": r["title"], "status": r["status"], "status_label": {"EM_ANDAMENTO": "Em andamento", "AGUARDANDO": "Aguardando", "CONCLUIDO": "Concluído"}.get(r["status"], "Aberto"),
        "due_date": r["due_date"], "priority": "NORMAL", "owner_name": r["owner_name"] or "Sem responsável",
        "company": "Caso local", "unit": "", "updated_at": r["updated_at"],
        "checklist_total": r["checklist_total"], "checklist_pending": r["checklist_pending"],
        "href": f"/central-dp/casos/{r['id']}?return_home_scope={scope}" + ("#checklist" if r["checklist_pending"] else ""),
    } for r in local]
    if scope == "team" or db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_case_assignments'").fetchone():
        clients = {}
        for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='clients'"):
            item = json.loads(row["payload"])
            if item.get("id") is not None:
                clients[str(item["id"])] = item.get("name") or "Empresa importada"
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        overlay_fields = "o.status local_status,o.owner_id local_owner,o.due_date local_due,o.priority local_priority,o.deleted_at local_deleted" if "local_v2_case_overrides" in tables else "NULL local_status,NULL local_owner,NULL local_due,NULL local_priority,NULL local_deleted"
        overlay_join = "LEFT JOIN local_v2_case_overrides o ON o.source_case_id=v.source_id" if "local_v2_case_overrides" in tables else ""
        source_rows = db.execute(f"""SELECT v.source_id,v.payload,a.owner_id,u.name owner_name,{overlay_fields} FROM v2_source_rows v
            LEFT JOIN local_case_assignments a ON a.source_case_id=v.source_id
            LEFT JOIN users u ON u.id=a.owner_id
            {overlay_join}
            WHERE v.table_name='cases'
            AND COALESCE(json_extract(payload,'$.deleted_at'),'')=''""").fetchall()
        source_rows = [r for r in source_rows if not r["local_deleted"]
                       and (scope == "team" or (r["local_owner"] if r["local_owner"] is not None else r["owner_id"]) == user_id)
                       and (r["local_status"] or json.loads(r["payload"]).get("status")) in
                       (ACTIVE_V2 | ({"WAITING", "DONE"} if include_closed else set()))]
        active_ids = {str(json.loads(r["payload"]).get("id") or r["source_id"]) for r in source_rows}
        checklist_counts = {key: [0, 0] for key in active_ids}
        if active_ids:
            for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='case_checklist'"):
                item = json.loads(row["payload"])
                key = str(item.get("case_id") or "")
                if key in checklist_counts:
                    checklist_counts[key][0] += 1
                    checklist_counts[key][1] += int(not bool(item.get("done")))
        local_checklists = {}
        if "local_v2_checklist_items" in tables:
            local_checklists = {r["source_case_id"]: (r["total"], r["pending"]) for r in db.execute(
                "SELECT source_case_id,COUNT(*) total,SUM(CASE WHEN done=0 THEN 1 ELSE 0 END) pending FROM local_v2_checklist_items GROUP BY source_case_id")}
        owners = {r["id"]: r["name"] for r in db.execute("SELECT id,name FROM users")}
        for row in source_rows:
            item = json.loads(row["payload"])
            source_id = str(item.get("id") or row["source_id"])
            total, pending = local_checklists.get(row["source_id"], checklist_counts.get(source_id, [0, 0]))
            effective_status = row["local_status"] or item.get("status")
            owner_id = row["local_owner"] if row["local_owner"] is not None else row["owner_id"]
            records.append({
                "source": "V2", "source_id": row["source_id"], "local_id": None,
                "title": item.get("title") or "Caso importado", "status": effective_status,
                "status_label": {"IN_PROGRESS": "Em andamento", "WAITING": "Aguardando", "DONE": "Concluído"}.get(effective_status, "Aberto"),
                "due_date": str(row["local_due"] or item.get("due_at") or "")[:10] or None,
                "priority": row["local_priority"] or item.get("priority") or "NORMAL",
                "owner_name": owners.get(owner_id) or "Responsável V2 sem vínculo local",
                "company": clients.get(str(item.get("client_id")), "Geral / interno"),
                "unit": str(item.get("unit_id") or ""), "updated_at": item.get("updated_at") or item.get("created_at"),
                "checklist_total": total, "checklist_pending": pending,
                "href": f"/central-dp/casos?home_source={quote(row['source_id'], safe='')}&home_scope={scope}" + ("#checklist" if pending else ""),
            })
    return records


def filter_home_cases(records: list[dict], filter_key: str, as_of: date) -> list[dict]:
    end = as_of + timedelta(days=7)
    if filter_key == "overdue":
        rows = [r for r in records if _date(r["due_date"]) and _date(r["due_date"]) < as_of]
    elif filter_key == "upcoming":
        rows = [r for r in records if _date(r["due_date"]) and as_of <= _date(r["due_date"]) <= end]
    elif filter_key == "checklist":
        rows = [r for r in records if r["checklist_pending"]]
    else:
        rows = list(records)
    def order(row):
        due = _date(row["due_date"])
        late = due is not None and due < as_of
        updated = _date_time(row["updated_at"])
        recency = updated.toordinal() * 86400 + updated.hour * 3600 + updated.minute * 60 + updated.second
        return (0 if late else 1, due or date.max, PRIORITY_ORDER.get(str(row["priority"]).upper(), 2), -recency)
    return sorted(rows, key=order)


def home_counts(records: list[dict], as_of: date) -> dict[str, int]:
    selected = filter_home_cases(records, "all", as_of)
    overdue = filter_home_cases(records, "overdue", as_of)
    upcoming = filter_home_cases(records, "upcoming", as_of)
    return {
        "open": len(selected), "overdue": len(overdue), "upcoming": len(upcoming),
        "checklist": sum(r["checklist_pending"] for r in selected),
    }
