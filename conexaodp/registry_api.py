"""Read-only view models for the Next.js registry screens."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections import Counter
from datetime import date

from . import sib_scan as sib_scan_service
from .permissions import can, require_subject, can_access_subject, allowed_company, scope
from .companies import PROFILE_FIELDS, catalog as company_catalog, profile as company_profile
from .registry import (
    categorized_fields,
    normalize,
    person_activity,
    project as project_registry,
    project_conflicts,
    sib_activity,
)


PAGE_SIZE = 50
PEOPLE_STATUSES = ("all", "Ativo", "Desligado", "Admissão futura", "Situação a conferir")
EMPLOYMENT_STATUSES = ("all", "Ativo", "Desligado", "Admissão futura", "Sem saída registrada", "Situação a conferir")
COMPANY_GROUPS = {
    "all": "Todas",
    "with-active": "Com funcionários ativos",
    "without-active": "Sem funcionários ativos",
    "unknown": "Situação a conferir",
}
CONFLICT_STATUSES = ("all", "PENDING", "REVIEWED", "RESOLVED", "REOPENED")


def _param(parameters, name, default=""):
    value = parameters.get(name) or [default]
    return str(value[0] if isinstance(value, (list, tuple)) else value)


def _page(parameters, total):
    raw = _param(parameters, "page", "1")
    requested = int(raw) if raw.isdigit() and len(raw) <= 8 else 1
    return max(1, min(requested, max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)))


def _categories(value, *, company=False):
    return [
        {
            "title": title,
            "fields": [{"label": label, "value": item, "path": original} for label, item, original in fields],
        }
        for title, fields in categorized_fields(value, company=company).items()
    ]


def _person_codes(person):
    codes = list(dict.fromkeys(str(item.get('registration_code') or item['registration'])
                               for item in person['employments'] if item.get('registration')))
    return ", ".join(codes) if codes else ("Local " + person["key"].removeprefix("m:") if person["key"].startswith("m:") else "Sem código")


def _history(history, key):
    return [
        {"kind": row["kind"], "value": row["value"], "note": row["note"], "actor": row["actor"], "createdAt": row["created_at"]}
        for row in reversed(history.get(key, []))
    ]


def _source_detail(db, key, dataset, can_manage):
    if not can_manage or not key.startswith("sib:"):
        return []
    row = db.execute(
        "SELECT data_json,raw_json FROM local_sib_records WHERE dataset=? AND source_id=?",
        (dataset, key[4:]),
    ).fetchone()
    if not row:
        return []
    raw, data = json.loads(row['raw_json']), json.loads(row['data_json'])
    if dataset == 'employments':
        from .sib_record_mapping import employment_fields
        data.update(employment_fields(data, raw, dict(db.execute('SELECT code,label FROM local_sib_contract_types'))))
    return _categories({**raw, **data})



def _visible_registry(db, user):
    """Apply population scope before searches, counters and pagination."""
    people, employments, history = project_registry(db)
    decisions = {}
    visible = []
    for link in employments:
        company_key = link.get('company_key') or ''
        code = company_key[4:] if company_key.startswith('sib:') else ''
        if not code:
            match = re.match(r'^\s*(\d+)\s*[-·]', str(link.get('company') or ''))
            code = match.group(1) if match else ''
        cache_key = (company_key, code, link.get('company') or '')
        if cache_key not in decisions:
            decisions[cache_key] = allowed_company(db, user, company_key=company_key, code=code, name=link.get('company') or '')
        if decisions[cache_key]:
            visible.append(link)
    allowed_keys = {link['key'] for link in visible}
    scoped = {}
    for key, person in people.items():
        links = [link for link in person['employments'] if link['key'] in allowed_keys]
        if links or (not person['employments'] and can_access_subject(db, user, 'pessoas', key)):
            scoped[key] = {**person, 'employments': links, '_complete_scope': len(links) == len(person['employments'])}
    return scoped, visible, history

def _registry_payload(db, view, parameters, user):
    people, employments, history = _visible_registry(db, user)
    is_people = view == "pessoas"
    search = _param(parameters, "q").strip()[:100]
    status = _param(parameters, "status", "all")
    selected = _param(parameters, "id")[:150]
    person_filter = "" if is_people else _param(parameters, "person")[:150]
    can_manage = can(user, "registry.manage")
    if is_people:
        # Pessoas is one card per matrícula. The stable person key remains
        # attached to each row so the Vínculos tab can show the full history.
        rows = []
        for person in people.values():
            if not person['key'].startswith('sib:') and not person.get('_complete_scope', True):
                continue
            if person["employments"]:
                rows.extend({
                    "key": link["key"], "name": person["name"], "person_key": person["key"],
                    "person": person, "employment": link,
                } for link in person["employments"])
            else:
                rows.append({"key": person["key"], "name": person["name"], "person_key": person["key"],
                             "person": person, "employment": None})
    else:
        # Vínculos groups a person's registrations so rehires and prior
        # contracts are visible together in one place.
        rows = list(people.values())
    if person_filter:
        rows = [item for item in rows if item.get("person_key", item["key"]) == person_filter]
    needle = normalize(search)

    def activity(row):
        if is_people:
            link = row.get("employment")
            if link:
                return "Situação a conferir" if link["status"] == "Sem saída registrada" else link["status"]
            return person_activity(row["person"])
        return person_activity(row)

    def searchable(row):
        person = row["person"] if is_people else row
        values = [person["name"], person.get("cpf", "")]
        if is_people:
            link = row.get("employment")
            if link:
                values.extend(str(link.get(key) or "") for key in ("registration", "registration_code", "company", "contract_type"))
        else:
            values.extend(f'{item["registration"]} {item.get("registration_code", "")} {item["company"]} {item["contract_type"]}'
                          for item in person["employments"])
        return normalize(" ".join(values))

    cpf_digits = re.sub(r"\D", "", search)
    prefixed = re.fullmatch(r'(\d{1,10})\s*-\s*(\d{1,10})', search)
    def matches_prefix(row):
        if not prefixed:
            return False
        links = ([row['employment']] if is_people and row.get('employment') else
                 row['person']['employments'] if is_people else row['employments'])
        branch, registration = (str(int(part)) for part in prefixed.groups())
        return any((str(link.get('branch_code') or '') == branch and
                    str(link.get('registration') or '') == registration)
                   or (not link.get('branch_code') and normalize(link.get('registration') or '') == needle)
                   for link in links)
    cpf_needle = cpf_digits if len(cpf_digits) == 11 and re.fullmatch(r"[\d.\-\s]+", search) else ""
    searched = [item for item in rows if (matches_prefix(item) if prefixed else needle in searchable(item))
                or (cpf_needle and cpf_needle == re.sub(r"\D", "", str(
                    (item["person"] if is_people else item).get("cpf") or "")))]
    if status not in PEOPLE_STATUSES:
        status = "Situação a conferir" if status == "Sem saída registrada" else "all"
    valid_statuses = PEOPLE_STATUSES
    counts = Counter(activity(item) for item in searched)
    filtered = [item for item in searched if status == "all" or activity(item) == status]
    if is_people:
        filtered.sort(key=lambda item: (
            normalize(item["name"]),
            normalize(str((item.get("employment") or {}).get("registration") or "")),
            normalize(str((item.get("employment") or {}).get("company") or "")),
            item["key"],
        ))
    else:
        filtered.sort(key=lambda item: normalize(item["name"]))
    page = _page(parameters, len(filtered))
    visible = filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    records = []
    for item in visible:
        item_status = activity(item)
        if is_people:
            person = item["person"]
            link = item.get("employment")
            records.append({
                "key": item["key"], "personKey": person["key"], "name": item["name"],
                "code": str(link.get('registration_code') or link.get("registration") or "Sem matrícula") if link else _person_codes(person),
                "company": link.get("company") or "" if link else "",
                "status": item_status, "employmentCount": 1 if link else 0,
                "activeCount": 1 if link and link.get("active") is True else 0,
                "source": link.get("source") if link else "Cadastro local",
                "contractType": link.get('contract_type') or '' if link else '',
                "isTest": bool(person.get("is_test") or (link and link.get("is_test"))),
            })
        else:
            links = item["employments"]
            active_count = sum(link.get("active") is True for link in links)
            records.append({
                "key": item["key"], "personKey": item["key"], "name": item["name"],
                "code": _person_codes(item), "employmentCount": len(links), "activeCount": active_count,
                "source": ", ".join(sorted({link["source"] for link in links})) or "Cadastro local",
                "status": item_status,
            })

    detail = None
    chosen = next((item for item in filtered if item["key"] == selected), None)
    # Old links to the former person-level Pessoas card should still open one
    # of that person's individual matrícula cards.
    if chosen is None and is_people and selected:
        chosen = next((item for item in filtered if item.get("person_key") == selected), None)
    if chosen is None and not is_people and selected:
        chosen = next((item for item in filtered if any(link["key"] == selected for link in item["employments"])), None)
    if chosen and is_people:
        person = chosen["person"]
        link = chosen.get("employment")
        local_id = int(person["key"][2:]) if person["key"].startswith("m:") and person["key"][2:].isdigit() else 0
        documents = []
        if can_manage and local_id:
            documents = [dict(row) for row in db.execute(
                "SELECT id,filename,mime_type,size_bytes,created_at FROM local_registry_documents WHERE person_id=? ORDER BY id DESC",
                (local_id,),
            )]
        if link:
            detail = {
                "kind": "person_employment", "key": link["key"], "personKey": person["key"],
                "name": person["name"], "code": str(link.get('registration_code') or link.get("registration") or "Sem matrícula"),
                "cpf": person.get("cpf") or "", "email": person.get("email") or "", "whatsapp": person.get("whatsapp") or "",
                "status": activity(chosen), "verified": bool(person.get("verified")),
                "isTest": bool(person.get("is_test") or link.get("is_test")),
                "registration": link.get("registration") or "", "company": link.get("company") or "",
                "registrationCode": link.get('registration_code') or link.get('registration') or '',
                "branchCode": link.get('branch_code') or '',
                "contractType": link.get("contract_type") or "", "admissionDate": link.get("admission_date") or "",
                "firstDueDate": link.get("first_due_date") or "", "secondDueDate": link.get("second_due_date") or "",
                "terminationDate": link.get("termination_date") or "", "source": link.get("source") or "",
                "dismissalReason": link.get("dismissal_reason") or "", "dismissalReasonCode": link.get("dismissal_reason_code") or "",
                "department": link.get("department") or "", "jobTitle": link.get("job_title") or "",
                "departmentCode": link.get('department_code') or '', "jobCode": link.get('job_code') or '',
                "contractTypeCode": link.get('contract_type_code') or '',
                "history": sorted(_history(history, person["key"]) + _history(history, link["key"]),
                                   key=lambda entry: entry["createdAt"], reverse=True),
                "categories": _source_detail(db, link["key"], "employments", can(user, "sensitive.view")),
                "documents": documents,
            }
            if can_manage and link.get("source") == "V2":
                detail["peopleOptions"] = [
                    {"key": other["key"], "name": other["name"],
                     "company": other["employments"][0]["company"] if other["employments"] else "Cadastro local"}
                    for other in sorted(people.values(), key=lambda current: normalize(current["name"]))
                    if other["key"] != person["key"]
                ]
        else:
            detail = {
                "kind": "person", "key": person["key"], "name": person["name"], "code": _person_codes(person),
                "cpf": person.get("cpf") or "", "email": person.get("email") or "", "whatsapp": person.get("whatsapp") or "",
                "status": person_activity(person), "verified": bool(person.get("verified")), "isTest": bool(person.get("is_test")),
                "employmentCount": 0, "history": _history(history, person["key"]), "documents": documents,
                "employments": [],
            }
    elif chosen:
        links = sorted(chosen["employments"], key=lambda item: item.get("admission_date") or "", reverse=True)
        local_id = int(chosen["key"][2:]) if chosen["key"].startswith("m:") and chosen["key"][2:].isdigit() else 0
        documents = []
        if can_manage and local_id:
            documents = [dict(row) for row in db.execute(
                "SELECT id,filename,mime_type,size_bytes,created_at FROM local_registry_documents WHERE person_id=? ORDER BY id DESC",
                (local_id,),
            )]
        combined_history = _history(history, chosen["key"])
        for link in links:
            combined_history.extend(_history(history, link["key"]))
        detail = {
            "kind": "person", "key": chosen["key"], "name": chosen["name"], "code": _person_codes(chosen),
            "cpf": chosen.get("cpf") or "", "email": chosen.get("email") or "", "whatsapp": chosen.get("whatsapp") or "",
            "status": person_activity(chosen), "verified": bool(chosen.get("verified")), "isTest": bool(chosen.get("is_test")),
            "employmentCount": len(links), "documents": documents,
            "history": sorted(combined_history, key=lambda entry: entry["createdAt"], reverse=True),
            "employments": [{
                "key": item["key"], "registration": item.get("registration") or "", "company": item.get("company") or "",
                "registrationCode": item.get('registration_code') or item.get('registration') or '',
                "contractType": item.get("contract_type") or "", "admissionDate": item.get("admission_date") or "",
                "firstDueDate": item.get("first_due_date") or "", "secondDueDate": item.get("second_due_date") or "",
                "terminationDate": item.get("termination_date") or "", "status": item["status"], "source": item["source"],
                "categories": _source_detail(db, item["key"], "employments", can(user, "sensitive.view")),
            } for item in links],
        }

    selected_key = chosen["key"] if chosen else selected
    return {
        "view": view, "title": "Pessoas" if is_people else "Vínculos", "isPeople": is_people,
        "canManage": can_manage, "csrf": user["csrf_token"], "query": search, "status": status,
        "selected": selected_key, "person": person_filter, "page": page, "pageSize": PAGE_SIZE,
        "total": len(filtered), "records": records,
        "statusCounts": {key: (len(searched) if key == "all" else counts[key]) for key in valid_statuses},
        "detail": detail,
    }


def _companies_payload(db, parameters, user):
    can_manage = can(user, "registry.manage")
    search = _param(parameters, "q").strip()[:100]
    selected = _param(parameters, "id")[:150]
    group = _param(parameters, "group", "all")
    if group not in COMPANY_GROUPS:
        group = "all"
    needle = normalize(search)
    catalog = [item for item in company_catalog(db) if allowed_company(db, user, company_key=item["key"], code=item.get("code") or "")]
    searched = [item for item in catalog if needle in normalize(item["name"] + " " + item["code"])]
    counts = Counter(item["employeeGroup"] for item in searched)
    filtered = [item for item in searched if group == "all" or item["employeeGroup"] == group]
    page = _page(parameters, len(filtered))
    records = [{
        "key": item["key"], "name": item["name"], "code": item["code"] or "Sem código",
        "group": item["employeeGroup"], "headcount": item["headcount"], "total": item["total"],
        "unknownHeadcount": item.get("unknownHeadcount", 0), "headcountLabel": item["headcountLabel"], "source": item["source"],
    } for item in filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]]
    chosen = next((item for item in catalog if item["key"] == selected), None) if selected else None
    detail = None
    if chosen:
        data, source = company_profile(db, selected)
        fields = {key: value for key, value in data.items() if key != "originalSib"}
        if can(user, "sensitive.view") and isinstance(data.get("originalSib"), dict):
            fields = {**data["originalSib"], **fields}
        detail = {
            "key": chosen["key"], "name": chosen["name"], "code": chosen["code"] or "",
            "group": chosen["employeeGroup"], "headcount": chosen["headcount"], "total": chosen["total"],
            "unknownHeadcount": chosen.get("unknownHeadcount", 0), "source": chosen["source"], "profileSource": source,
            "categories": _categories(fields, company=True),
            "profile": {key: data.get(key, "") or "" for key, _, _ in PROFILE_FIELDS},
        }
    return {
        "view": "empresas", "title": "Empresas", "canManage": can_manage, "csrf": user["csrf_token"],
        "query": search, "group": group, "groupLabels": COMPANY_GROUPS, "selected": selected,
        "page": page, "pageSize": PAGE_SIZE, "total": len(filtered), "records": records,
        "groupCounts": {key: len(searched) if key == "all" else counts[key] for key in COMPANY_GROUPS}, "detail": detail,
    }


def _conflicts_payload(db, parameters, user):
    conflicts, history = project_conflicts(db)
    if scope(user) != 'ALL':
        # Cross-person conflict groups can include restricted employees. Keep
        # them private until the entire group can be evaluated in one scope.
        conflicts = []
    search = _param(parameters, "q").strip()[:100]
    status = _param(parameters, "status", "all")
    if status not in CONFLICT_STATUSES:
        status = "all"
    selected = _param(parameters, "id")[:150]
    needle = normalize(search)
    searched = [item for item in conflicts if needle in normalize(item["kind"] + " " + item["detail"])]
    counts = Counter(item["status"] for item in searched)
    filtered = [item for item in searched if status == "all" or item["status"] == status]
    page = _page(parameters, len(filtered))
    records = [{"key": item["key"], "kind": item["kind"], "detail": item["detail"], "status": item["status"]}
               for item in filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]]
    chosen = next((item for item in filtered if item["key"] == selected), None)
    detail = ({"key": chosen["key"], "kind": chosen["kind"], "description": chosen["detail"],
               "status": chosen["status"], "history": _history(history, chosen["key"])} if chosen else None)
    valid_statuses = ("all", "PENDING", "REVIEWED", "RESOLVED", "REOPENED")
    return {
        "view": "conflitos", "title": "Conflitos", "canManage": can(user, "registry.manage"), "csrf": user["csrf_token"],
        "query": search, "status": status, "selected": selected, "page": page, "pageSize": PAGE_SIZE,
        "total": len(filtered), "records": records,
        "statusCounts": {key: len(searched) if key == "all" else counts[key] for key in valid_statuses}, "detail": detail,
    }


def registry_revision(db):
    """Cheap change hint for registry snapshots; never project or expose personal data."""
    parts = [date.today().isoformat()]
    for table, timestamp in (
        ("local_sib_records", "imported_at"), ("v2_employments", "imported_at"),
        ("registry_people", "created_at"), ("registry_employments", "created_at"),
        ("registry_reviews", "created_at"), ("local_clients", "created_at"),
        ("local_company_profiles", "updated_at"), ("local_registry_documents", "created_at"),
        ("local_sib_contract_types", "confirmed_at"),
    ):
        row = db.execute(f"SELECT COUNT(*),COALESCE(MAX(rowid),0),COALESCE(MAX({timestamp}),'') FROM {table}").fetchone()
        parts.append(tuple(row))
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def get_registry_status(db, user):
    """Poll a small local status without rebuilding all people and employments."""
    result = {"revision": registry_revision(db), "canManage": can(user, "registry.manage")}
    if can(user, 'integrations.manage') and scope(user) == 'ALL':
        scan = sib_scan_service.summary(db)
        result["scan"] = {key: scan[key] for key in (
            "started", "running", "inFlight", "done", "autoResume", "retrying", "retryAt",
            "checked", "records", "companies", "updatedAt", "pending",
        )}
    return result


def get_registry_data(db, view, parameters, user):
    revision = registry_revision(db)
    if view in ("pessoas", "vinculos"):
        result = _registry_payload(db, view, parameters, user)
    elif view == "empresas":
        result = _companies_payload(db, parameters, user)
    elif view == "conflitos":
        result = _conflicts_payload(db, parameters, user)
    else:
        raise ValueError("Tela de cadastro não reconhecida.")
    if can(user, "integrations.manage") and view in ("pessoas", "vinculos", "empresas"):
        result["scan"] = sib_scan_service.summary(db)
    result["revision"] = revision
    return result


def _valid_cpf(value):
    cpf = re.sub(r"\D", "", str(value or ""))
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return ""
    for size in (9, 10):
        digit = (sum(int(cpf[index]) * (size + 1 - index) for index in range(size)) * 10 % 11) % 10
        if digit != int(cpf[size]):
            return ""
    return cpf


def _sib_person_employment_detail(db, key, can_manage):
    """Build one SIB employment card directly, without projecting every registry row."""
    source_id = key.removeprefix("sib:")
    row = db.execute(
        "SELECT data_json,raw_json FROM local_sib_records WHERE dataset='employments' AND source_id=?",
        (source_id,),
    ).fetchone()
    if not row:
        return None

    data = json.loads(row["data_json"])
    from .sib_record_mapping import employment_fields
    fields = employment_fields(data, json.loads(row['raw_json']), dict(db.execute('SELECT code,label FROM local_sib_contract_types')))
    data.update(fields)
    cpf = re.sub(r"\D", "", str(data.get("cpf") or ""))
    valid_cpf = _valid_cpf(cpf)
    person_key = "sib:p:" + (hashlib.sha256(cpf.encode()).hexdigest() if valid_cpf else source_id)
    company_id = str(data.get("companyId") or "")
    company_row = db.execute(
        "SELECT data_json FROM local_sib_records WHERE dataset='companies' AND source_id=?",
        (company_id,),
    ).fetchone() if company_id else None
    company = json.loads(company_row["data_json"]) if company_row else {}
    _, status = sib_activity(data)
    if status == "Sem saída registrada":
        status = "Situação a conferir"

    history = defaultdict(list)
    name_review = None
    review_rows = db.execute(
        "SELECT r.*,u.name actor FROM registry_reviews r JOIN users u ON u.id=r.actor_id "
        "WHERE r.subject_key IN (?,?) ORDER BY r.id",
        (person_key, key),
    )
    for item in review_rows:
        history[item["subject_key"]].append(item)
        if item["subject_key"] == person_key and item["kind"] == "PERSON_NAME":
            name_review = item["value"]
    combined_history = sorted(
        _history(history, person_key) + _history(history, key),
        key=lambda item: item["createdAt"], reverse=True,
    )
    return {
        "kind": "person_employment", "key": key, "personKey": person_key,
        "name": str(name_review or data.get("name") or f"Cadastro SIB {source_id}"),
        "code": str(data.get('registrationCode') or data.get("registration") or ""), "cpf": cpf,
        "email": str(data.get("email") or ""), "whatsapp": str(data.get("phone") or ""),
        "status": status, "verified": bool(valid_cpf or name_review), "isTest": False,
        "registration": str(data.get("registration") or ""),
        "registrationCode": str(data.get('registrationCode') or data.get('registration') or ''),
        "branchCode": str(data.get('branchCode') or ''),
        "company": str(company.get("name") or f"Empresa SIB {company_id}"),
        "contractType": str(data.get("contractType") or ""),
        "admissionDate": str(data.get("admissionDate") or ""),
        "firstDueDate": str(data.get("firstDueDate") or ""),
        "secondDueDate": str(data.get("secondDueDate") or ""),
        "terminationDate": str(data.get("dismissalDate") or data.get("terminationDate") or ""),
        "dismissalReason": str(data.get("dismissalReason") or ""),
        "dismissalReasonCode": str(data.get("dismissalReasonCode") or ""),
        "source": "SIB", "department": str(data.get("department") or ""),
        "departmentCode": fields['departmentCode'], "jobCode": fields['jobCode'],
        "contractTypeCode": fields['contractTypeCode'],
        "jobTitle": str(data.get("jobTitle") or ""), "history": combined_history,
        "categories": _source_detail(db, key, "employments", can_manage), "documents": [],
    }


def get_registry_detail(db, view, parameters, user):
    """Read only the selected record, with its own documents and source identity."""
    selected = _param(parameters, "id")[:150]
    require_subject(db, user, view, selected)
    can_manage = can(user, "registry.manage")
    detail = None
    if view == "pessoas" and selected.startswith("sib:") and not selected.startswith("sib:p:"):
        detail = _sib_person_employment_detail(db, selected, can(user, "sensitive.view"))
        if not detail:
            raise ValueError("Este cadastro não está mais disponível na importação local.")
    else:
        result = get_registry_data(db, view, parameters, user)
        detail = result.get("detail")
    if detail and can(user, 'absences.view') and view in ('pessoas', 'vinculos'):
        from .sib_absences import for_employments
        keys = [detail['key']] if detail.get('kind') == 'person_employment' else [item['key'] for item in detail.get('employments', [])]
        detail['absences'] = for_employments(db, keys)
    if detail and can(user, 'documents.view') and view in ('pessoas', 'vinculos', 'empresas'):
        from .registry_documents import resolve_owner, list_documents, DOCUMENT_TYPES, document_history
        try:
            owner = resolve_owner(db, view, detail['key'], user=user)
            detail['documents'] = list_documents(db, owner, user=user, include_archived=True)
            detail['documentHistory'] = document_history(db, owner, user=user)
            detail['documentOwner'] = {'view': view, 'key': detail['key'], 'label': owner['label']}
        except ValueError as error:
            detail['documents'] = []
            detail['documentOwnerMessage'] = str(error)
        except PermissionError:
            detail['documents'] = []
            detail['documentOwnerMessage'] = 'O prontuário geral desta pessoa reúne vínculos fora do seu recorte. Os demais dados exibidos pertencem somente aos vínculos autorizados.'
        document_types = DOCUMENT_TYPES
    else:
        if detail:
            detail['documents'] = []
        document_types = {}
    if detail and view in ('pessoas', 'vinculos', 'empresas'):
        from .sector_assets import history_links
        keys = ([detail['key']] + [item['key'] for item in detail.get('employments', [])]
                if view in ('pessoas', 'vinculos') else [])
        detail['sectorHistory'] = history_links(
            db, keys, user, company_key=detail['key'] if view == 'empresas' else '')
    if detail and not can(user, 'sensitive.view') and detail.get('cpf'):
        cpf = re.sub(r'\D', '', str(detail['cpf']))
        detail['cpf'] = '***.***.***-' + cpf[-2:] if len(cpf) == 11 else 'Acesso restrito'
    return {
        "view": view, "selected": selected, "detail": detail,
        "canManage": can_manage, "csrf": user["csrf_token"],
        "canViewDocuments": can(user, 'documents.view'), "canUploadDocuments": can(user, 'documents.upload'),
        "documentTypes": document_types,
    }
