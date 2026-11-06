"""SQLite-backed application service for the Next.js lot-planning screen."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import companies
from .lot_planning import bank_state, valid_day
from .lot_planning_v3 import calculate_plan, resolve_cutoff
from .registry import normalize


try:
    LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # Windows Python often ships without the IANA tzdata package.
    LOCAL_TZ = timezone(timedelta(hours=-3), "America/Sao_Paulo")
WRITE_ROLES = {"ADMIN", "GESTOR", "OPERADOR"}
CONFIG_ROLES = {"ADMIN", "GESTOR"}
EXPIRATION_MODE = "FOUR_LOCAL_DAYS"


class LotPlanError(ValueError):
    def __init__(self, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


def local_stamp() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def utc_stamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")


def _json(value, fallback=None):
    try:
        return json.loads(value) if value is not None else (fallback if fallback is not None else {})
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def _companies(db):
    items = companies.catalog(db)
    return {item["key"]: item for item in items}, items


def _company_for(db, company_name: str):
    by_key, items = _companies(db)
    target = normalize(company_name)
    exact = next((item for item in items if normalize(item["name"]) == target), None)
    if exact:
        return exact
    # The source occasionally includes a client code before the legal name.
    try:
        _, stripped, _ = companies._identity(company_name)
    except (AttributeError, TypeError):
        stripped = company_name
    return next((item for item in items if normalize(item["name"]) == normalize(stripped)), None)


def _employee_dict(row, db):
    client = _company_for(db, row["company"])
    return {"key": "v:" + str(row["external_key"]), "name": row["name"],
            "registration": row["registration"], "company": row["company"],
            "companyKey": client["key"] if client else "", "companyName": client["name"] if client else row["company"],
            "unit": "", "unitAvailable": False, "department": row["department"],
            "jobTitle": row["job_title"], "contractType": row["contract_type"],
            "admission_date": row["admission_date"], "termination_date": row["termination_date"],
            "status": "Ativo no snapshot" if row["in_active_export"] else "Sem saída registrada",
            "source": "Snapshot V2 importado (somente leitura)", "sourceImportedAt": row["imported_at"]}


def _local_employee_dict(row, db):
    client = _company_for(db, row["company"])
    termination = str(row["termination_date"] or "")
    active = not termination or termination >= datetime.now(LOCAL_TZ).date().isoformat()
    return {"key": "local:" + str(row["id"]), "name": row["person_name"],
            "registration": row["registration"], "company": row["company"],
            "companyKey": client["key"] if client else "",
            "companyName": client["name"] if client else row["company"],
            "unit": row["unit"], "unitAvailable": False, "department": row["unit"],
            "jobTitle": "", "contractType": row["contract_type"],
            "admission_date": row["admission_date"], "termination_date": row["termination_date"],
            "status": "Ativo no cadastro local" if active else "Desligado",
            "source": "Cadastro de Vínculos V3 local", "sourceImportedAt": row["created_at"]}


def _find_employee(db, key: str):
    if key.startswith("local:") and key[6:].isdigit():
        row = db.execute("""SELECT e.*,p.name person_name FROM registry_employments e
            JOIN registry_people p ON p.id=CAST(substr(e.person_key,3) AS INTEGER)
            WHERE e.id=? AND e.person_key LIKE 'm:%'""", (int(key[6:]),)).fetchone()
        if not row:
            raise LotPlanError("O vínculo local selecionado não está mais disponível. Atualize a busca.", 404)
        return _local_employee_dict(row, db)
    if not key.startswith("v:"):
        raise LotPlanError("Selecione um vínculo encontrado no snapshot local.")
    row = db.execute("SELECT * FROM v2_employments WHERE external_key=?", (key[2:],)).fetchone()
    if not row:
        raise LotPlanError("O vínculo selecionado não está disponível no snapshot local. Atualize a busca.", 404)
    return _employee_dict(row, db)


def _profile(db, company_key: str):
    try:
        value, source = companies.profile(db, company_key)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}, "Ficha incompleta"
    return (value if isinstance(value, dict) else {}), source


def _rule(db, company_key: str, benefit: str, unit_key: str = "", effective_on: str | None = None):
    row = db.execute("""SELECT * FROM local_company_benefit_rules
        WHERE company_key=? AND unit_key=? AND benefit_type=?""", (company_key, unit_key, benefit)).fetchone()
    if not row and unit_key:
        row = db.execute("SELECT * FROM local_company_benefit_rules WHERE company_key=? AND unit_key='' AND benefit_type=?", (company_key, benefit)).fetchone()
    if not row:
        return None
    value = dict(row)
    if effective_on:
        versions = {}
        history = db.execute("SELECT old_json,new_json FROM local_company_benefit_rule_history WHERE rule_id=? ORDER BY id", (row["id"],)).fetchall()
        for item in history:
            old, new = _json(item["old_json"], {}), _json(item["new_json"], {})
            old_from, new_from = str(old.get("valid_from") or ""), str(new.get("valid_from") or "")
            if valid_day(old_from):
                versions.setdefault(old_from, old)
            if valid_day(new_from):
                versions[new_from] = new
        current_from = str(value.get("valid_from") or "")
        if valid_day(current_from):
            versions[current_from] = value
        applicable = [start for start in versions if start <= effective_on]
        if not applicable:
            return None
        value = dict(versions[max(applicable)])
        value.setdefault("id", row["id"])
        value.setdefault("updated_by", row["updated_by"])
    if not valid_day(str(value.get("valid_from") or "")):
        return None
    value["unit_cents"] = int(value["unit_cents"])
    value["uses_per_day"] = int(value["uses_per_day"])
    value["unit_mode"] = str(value.get("unit_mode") or ("PER_USE" if benefit == "VT" else "UNSPECIFIED"))
    value["february_30_policy"] = str(value.get("february_30_policy") or "UNRESOLVED")
    value["admission_on_cutoff_policy"] = str(value.get("admission_on_cutoff_policy") or "UNRESOLVED")
    value["source"] = "Configuração local por empresa"
    return value


def _canonical_state(value):
    state = str(value or "").strip().upper()
    return {"SIM": "YES", "TRUE": "YES", "1": "YES", "NAO": "NO", "NÃO": "NO", "FALSE": "NO", "0": "NO"}.get(state, state if state in ("YES", "NO", "OTHER") else None)


def _bank_rows(db, company_key: str, unit_key: str, start: str, end: str):
    rows = {}
    aliases = {company_key}
    if company_key.startswith("v2:"):
        aliases.add(company_key[3:])
    if company_key.startswith("local-client:"):
        aliases.add(company_key)
    for row in db.execute("SELECT day,scope_type,scope_key,state,updated_at FROM local_bank_relations WHERE day BETWEEN ? AND ?", (start, end)):
        scope, key = row["scope_type"], str(row["scope_key"] or "")
        if not company_key and scope != "GENERAL":
            continue
        if scope == "UNIT" and unit_key and key != unit_key:
            continue
        if scope == "UNIT" and not unit_key:
            continue
        if scope == "CLIENT" and key not in aliases:
            continue
        if scope not in ("GENERAL", "CLIENT", "UNIT"):
            continue
        rows[(row["day"], scope, key)] = {"day": row["day"], "scope_type": scope, "scope_key": key,
                                          "state": _canonical_state(row["state"]), "source": "Local", "updatedAt": row["updated_at"]}
    imported = db.execute("""SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days'
        AND json_extract(payload,'$.day') BETWEEN ? AND ?""", (start, end)).fetchall()
    for itemrow in imported:
        item = _json(itemrow["payload"])
        day = str(item.get("day") or "")
        scope = str(item.get("scope_type") or "GENERAL").upper()
        raw_client = str(item.get("client_id") or "")
        raw_unit = str(item.get("unit_id") or "")
        if scope == "CLIENT":
            if not company_key:
                continue
            possible = {raw_client, "v2:" + raw_client}
            if not aliases.intersection(possible):
                continue
            key = company_key if company_key in aliases else raw_client
        elif scope == "UNIT":
            if not company_key:
                continue
            if not unit_key or raw_unit != unit_key:
                continue
            key = unit_key
        elif scope == "GENERAL":
            key = ""
        else:
            continue
        rows.setdefault((day, scope, key), {"day": day, "scope_type": scope, "scope_key": key,
                                            "state": _canonical_state(item.get("state")),
                                            "source": "Snapshot V2 importado", "updatedAt": item.get("updated_at") or ""})
    return list(rows.values())


def _holidays(db, company_key: str, start: str, end: str):
    profile, profile_source = _profile(db, company_key)
    city, state = str(profile.get("city") or "").strip(), str(profile.get("state") or "").strip()
    candidates = []
    for row in db.execute("SELECT day,name,state,city,source_id FROM local_calendar_holidays WHERE day BETWEEN ? AND ?", (start, end)):
        candidates.append({"day": row["day"], "name": row["name"], "state": row["state"], "city": row["city"], "source": "Calendário local"})
    for row in db.execute("""SELECT payload FROM v2_source_rows WHERE table_name='calendar_holidays'
        AND json_extract(payload,'$.day') BETWEEN ? AND ?""", (start, end)):
        item = _json(row["payload"])
        candidates.append({"day": str(item.get("day") or ""), "name": str(item.get("name") or "Feriado"),
                           "state": str(item.get("state") or ""), "city": str(item.get("city") or ""),
                           "source": str(item.get("source") or "Snapshot V2 importado")})
    result, seen = [], set()
    for item in candidates:
        day, name = item["day"], item["name"]
        if not day or not name:
            continue
        item_city, item_state = str(item["city"] or ""), str(item["state"] or "")
        if item_city and (not city or normalize(item_city) != normalize(city)):
            continue
        if item_state and (not state or normalize(item_state) != normalize(state)):
            continue
        key = (day, normalize(name))
        if key in seen:
            continue
        seen.add(key)
        result.append({**item, "scope": "Municipal" if item_city else "Estadual" if item_state else "Nacional"})
    result.sort(key=lambda item: (item["day"], normalize(item["name"])))
    return result, {"city": city, "state": state, "source": profile_source,
                    "available": bool(city or state), "complete": bool(city and state)}


def _active_employee(row):
    if "in_active_export" not in row.keys():
        termination = str(row["termination_date"] or "")
        return not termination or termination >= datetime.now(LOCAL_TZ).date().isoformat()
    if not row["in_active_export"]:
        return False
    termination = str(row["termination_date"] or "")
    return not termination or termination >= datetime.now(LOCAL_TZ).date().isoformat()


def _employee_list(db, query: str, selected_key: str = "", company_key: str = ""):
    q = query.strip()[:100]
    today = datetime.now(LOCAL_TZ).date().isoformat()
    target_company = _companies(db)[0].get(company_key) if company_key else None
    target_identity = None
    target_name = ""
    company_like = ""
    if target_company:
        target_identity, target_name, _ = companies._identity(target_company["name"], target_company.get("code", ""))
        escaped_name = target_name.replace("%", "\\%").replace("_", "\\_")
        company_like = f"%{escaped_name}%"
    company_clause = " AND company LIKE ? ESCAPE '\\'" if company_like else ""
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        rows = db.execute("""SELECT * FROM v2_employments WHERE in_active_export=1
            AND (termination_date IS NULL OR termination_date='' OR termination_date>=?)
            AND (name LIKE ? ESCAPE '\\' OR registration LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\')"""
            + company_clause + " ORDER BY name COLLATE NOCASE,company COLLATE NOCASE LIMIT 40",
            (today, like, like, like, *((company_like,) if company_like else ()))).fetchall()
    else:
        rows = []
    items = [_employee_dict(row, db) for row in rows if _active_employee(row)]
    if q:
        local_like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        local_rows = db.execute("""SELECT e.*,
            COALESCE((SELECT r.value FROM registry_reviews r WHERE r.kind='PERSON_NAME'
                AND r.subject_key=e.person_key ORDER BY r.id DESC LIMIT 1),p.name) person_name
            FROM registry_employments e
            JOIN registry_people p ON p.id=CAST(substr(e.person_key,3) AS INTEGER)
            WHERE e.person_key LIKE 'm:%'
            AND (e.termination_date IS NULL OR e.termination_date='' OR e.termination_date>=?)
            AND (p.name LIKE ? ESCAPE '\\' OR e.registration LIKE ? ESCAPE '\\'
                OR e.company LIKE ? ESCAPE '\\' OR e.unit LIKE ? ESCAPE '\\' OR CAST(e.id AS TEXT) LIKE ? ESCAPE '\\')
            ORDER BY p.name COLLATE NOCASE,e.company COLLATE NOCASE LIMIT 40""",
            (today, local_like, local_like, local_like, local_like, local_like)).fetchall()
        items.extend(_local_employee_dict(row, db) for row in local_rows)
    else:
        local_rows = []
    if target_company and target_identity:
        items = [item for item in items
                 if item["companyKey"] == company_key
                 or companies._identity(item["company"])[0] == target_identity
                 or normalize(companies._identity(item["company"])[1]) == normalize(target_name)]
    items.sort(key=lambda item: (normalize(item["name"]), normalize(item["company"]), item["key"]))
    items = items[:40]
    selected = next((item for item in items if item["key"] == selected_key), None)
    if selected_key and not selected:
        try:
            selected = _find_employee(db, selected_key)
            if selected_key.startswith("local:"):
                status_row = db.execute("SELECT termination_date FROM registry_employments WHERE id=?", (int(selected_key[6:]),)).fetchone()
                active = bool(status_row and (not status_row["termination_date"] or status_row["termination_date"] >= today))
            else:
                status_row = db.execute("SELECT * FROM v2_employments WHERE external_key=?", (selected_key[2:],)).fetchone()
                active = bool(status_row and _active_employee(status_row))
            if not active:
                selected["inactive"] = True
        except LotPlanError:
            pass
    return items, selected


def _iso_to_local(value: str):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(LOCAL_TZ).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return value


def _plan_summary(row):
    inputs = _json(row["inputs_json"])
    result = _json(row["result_json"])
    snapshot = _json(row["source_snapshot_json"])
    employee = snapshot.get("employee") or {}
    company = snapshot.get("company") or {}
    benefit = row["benefit_type"] or inputs.get("benefit_type") or ""
    return {"id": row["id"], "title": row["title"], "employeeKey": row["employee_key"],
            "employeeName": employee.get("name") or inputs.get("employee_name") or "Sem identificação",
            "registration": employee.get("registration") or "", "companyName": company.get("name") or inputs.get("company_name") or "Empresa",
            "benefitType": benefit, "competence": row["competence"] or result.get("cutoff_date", ""),
            "admissionDate": inputs.get("admission_date", ""), "cutoffDate": result.get("cutoff_date", ""),
            "paymentDate": result.get("effective_payroll_date", ""), "lotCount": len(result.get("lots") or []),
            "totalCents": result.get("total_cents", 0), "lots": result.get("lots") or [],
            "savedBy": snapshot.get("savedBy") or "", "savedAt": _iso_to_local(snapshot.get("savedAt") or row["created_at"]),
            "expiresAt": _iso_to_local(row["expires_at"]) if row["expires_at"] else "",
            "pinned": bool(row["pinned"]), "pinnedAt": _iso_to_local(row["pinned_at"]) if row["pinned_at"] else "",
            "rowVersion": row["row_version"]}


def note_expired(db, user):
    now = utc_stamp()
    rows = db.execute("""SELECT id,expires_at FROM local_benefit_plans WHERE deleted_at IS NULL AND pinned=0 AND expires_at IS NOT NULL AND expires_at<=?
        AND expiration_noted_at IS NULL""", (now,)).fetchall()
    for row in rows:
        db.execute("UPDATE local_benefit_plans SET expiration_noted_at=? WHERE id=? AND expiration_noted_at IS NULL",
                   (now, row["id"]))
        db.execute("INSERT INTO local_benefit_plan_audit(plan_id,actor_id,action,metadata_json) VALUES(?,?, 'EXPIRED',?)",
                   (row["id"], user["id"], json.dumps({"expiresAt": row["expires_at"], "detectedAt": now}, ensure_ascii=False)))


def workspace(db, user, params: dict):
    q = str(params.get("q") or "")[:100]
    selected_key = str(params.get("employee") or "")[:180]
    employees, selected = _employee_list(db, q, selected_key, str(params.get("company") or "")[:180])
    company_items, company_list = _companies(db)
    selected_company = (selected or {}).get("companyKey") or str(params.get("company") or "")
    profile, profile_source = _profile(db, selected_company) if selected_company else ({}, "Sem ficha")
    benefit = str(params.get("benefit") or "VT").upper()
    if benefit not in ("VT", "VR"):
        benefit = "VT"
    effective_on = (str(params.get("admission") or "").strip() or (selected or {}).get("admission_date") or None)
    rule = _rule(db, selected_company, benefit, effective_on=effective_on) if selected_company else None
    source_date = db.execute("SELECT MAX(imported_at) FROM v2_employments").fetchone()[0]
    plans = db.execute("""SELECT * FROM local_benefit_plans WHERE deleted_at IS NULL
        ORDER BY pinned DESC,updated_at DESC""").fetchall()
    now = utc_stamp()
    now_dt = datetime.fromisoformat(now)
    soon_dt = now_dt + timedelta(hours=24)
    plan_stats = {"total": len(plans), "current": 0, "pinned": 0, "expiringSoon": 0, "expired": 0}
    active_plans = []
    plan_query = normalize(str(params.get("plan_q") or "").strip())
    plan_benefit = str(params.get("plan_benefit") or "").upper()
    if plan_benefit not in ("", "VT", "VR"):
        plan_benefit = ""
    plan_status = str(params.get("plan_status") or "current").lower()
    if plan_status not in ("current", "pinned", "expired", "all"):
        plan_status = "current"
    plan_author = str(params.get("plan_author") or "")
    for row in plans:
        expired = bool(not row["pinned"] and row["expires_at"] and row["expires_at"] <= now)
        plan_stats["pinned"] += int(bool(row["pinned"]))
        plan_stats["expired"] += int(expired)
        plan_stats["current"] += int(bool(row["pinned"] or not expired))
        if not row["pinned"] and row["expires_at"] and not expired:
            try:
                if now_dt < datetime.fromisoformat(row["expires_at"]) <= soon_dt:
                    plan_stats["expiringSoon"] += 1
            except ValueError:
                pass
        if plan_status == "current" and expired:
            continue
        if plan_status == "pinned" and not row["pinned"]:
            continue
        if plan_status == "expired" and not expired:
            continue
        if plan_benefit and row["benefit_type"] != plan_benefit:
            continue
        if plan_author and str(row["saved_by"] or "") != plan_author:
            continue
        summary = _plan_summary(row)
        if plan_query and plan_query not in normalize(" ".join((summary["title"], summary["employeeName"], summary["registration"], summary["companyName"], summary["competence"]))):
            continue
        active_plans.append(summary)
    authors = db.execute("""SELECT DISTINCT u.id,u.name FROM local_benefit_plans p JOIN users u ON u.id=p.saved_by
        WHERE p.deleted_at IS NULL ORDER BY u.name COLLATE NOCASE""").fetchall()
    detail = None
    plan_id = str(params.get("id") or "")
    if plan_id.isdigit():
        row = db.execute("SELECT * FROM local_benefit_plans WHERE id=?", (int(plan_id),)).fetchone()
        if row:
            detail = {**_plan_summary(row), "inputs": _json(row["inputs_json"]),
                      "result": _json(row["result_json"]), "snapshot": _json(row["source_snapshot_json"]),
                      "deleted": bool(row["deleted_at"]), "expired": bool(not row["pinned"] and row["expires_at"] and row["expires_at"] <= now)}
            revisions = db.execute("""SELECT row_version,title,inputs_json,result_json,source_snapshot_json,saved_by,saved_at
                FROM local_benefit_plan_revisions WHERE plan_id=? ORDER BY row_version""", (int(plan_id),)).fetchall()
            detail["revisions"] = [{"rowVersion": item["row_version"], "title": item["title"],
                                     "inputs": _json(item["inputs_json"]), "result": _json(item["result_json"]),
                                     "snapshot": _json(item["source_snapshot_json"]),
                                     "savedBy": db.execute("SELECT name FROM users WHERE id=?", (item["saved_by"],)).fetchone()[0]
                                     if item["saved_by"] else "", "savedAt": _iso_to_local(item["saved_at"])}
                                    for item in revisions]
            original_inputs = detail["inputs"]
            live = (_rule(db, str(row["company_key"] or ""), str(row["benefit_type"] or ""),
                          str(row["unit_key"] or ""), effective_on=original_inputs.get("admission_date"))
                    if row["company_key"] else None)
            detail["configurationChanged"] = bool(live and _rule_fingerprint(live) != _rule_fingerprint(detail["snapshot"].get("rule") or {}))
    archive_rows = db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='benefit_plans' ORDER BY source_id LIMIT 100").fetchall()
    legacy = []
    for row in archive_rows:
        item = _json(row["payload"])
        legacy.append({"id": str(row["source_id"]), "title": str(item.get("title") or item.get("employee_name") or "Plano V2"),
                       "createdAt": str(item.get("created_at") or ""), "source": "Snapshot V2, somente leitura"})
    return {"csrf": user["csrf_token"], "role": user["role"], "canWrite": user["role"] in WRITE_ROLES,
            "canConfigure": user["role"] in CONFIG_ROLES, "employees": employees, "selectedEmployee": selected,
            "companies": [{"key": item["key"], "code": item["code"], "name": item["name"], "source": item["source"],
                           "headcount": item["headcount"], "active": item["active"]} for item in company_list],
            "selectedCompany": ({"key": selected_company, "name": company_items.get(selected_company, {}).get("name", ""),
                                 "profile": profile, "profileSource": profile_source, "locationAvailable": bool(profile.get("city") or profile.get("state"))} if selected_company else None),
             "benefit": benefit, "rule": rule, "ruleEffectiveOn": effective_on or "",
            "plans": active_plans, "planStats": plan_stats, "detail": detail,
            "planFilters": {"q": str(params.get("plan_q") or ""), "benefit": plan_benefit,
                            "status": plan_status, "author": plan_author},
            "planAuthors": [{"id": str(row["id"]), "name": row["name"]} for row in authors],
            "legacyPlans": legacy, "snapshotAt": source_date, "search": q,
            "viewOnlyRoleAvailable": False,
            "limitations": (["A instalação local não possui cliente de API de RH configurado; a busca usa o snapshot V2 importado e os vínculos cadastrados manualmente no V3 local.",
                             "O snapshot V2 não fornece unidade física nem escala. Vínculos locais podem mostrar a unidade informada, mas ainda não há identificador de unidade integrado às regras."]
                            + (["Município/UF não informado para a empresa; feriados locais podem não estar disponíveis para este planejamento."]
                               if selected_company and not (profile.get("city") or profile.get("state")) else []))}


def _rule_fingerprint(value):
    keys = ("payment_day", "payment_adjustment", "holiday_benefit_rule", "unit_cents", "uses_per_day",
            "unit_mode", "february_30_policy", "admission_on_cutoff_policy", "valid_from")
    return tuple(value.get(key) for key in keys)


def _calculation_token(inputs, result, snapshot):
    stable_snapshot = {key: value for key, value in (snapshot or {}).items()
                       if key not in ("capturedAt", "savedAt", "savedBy", "expiresAt")}
    material = json.dumps({"inputs": inputs, "result": result, "snapshot": stable_snapshot},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _input_payload(db, user, payload: dict):
    payment_source = str(payload.get("payment_source") or "COMPANY").upper()
    if payment_source not in ("COMPANY", "GENERAL", "MANUAL"):
        raise LotPlanError("Escolha empresa, pagamento manual da empresa ou Empresa geral.")
    general_plan = payment_source == "GENERAL"
    requested_company_key = str(payload.get("company_key") or "")[:180]
    employee_key = str(payload.get("employee_key") or "")
    employee = _find_employee(db, employee_key) if employee_key and not general_plan else None
    if general_plan and (payload.get("employee_key") or requested_company_key):
        raise LotPlanError("Empresa geral não usa vínculo nem regra de empresa específica; limpe a seleção e informe os dados manualmente.", 409)
    company_items, _ = _companies(db)
    if employee:
        if employee.get("inactive") or employee["status"] not in ("Ativo no snapshot", "Ativo no cadastro local"):
            raise LotPlanError("O vínculo aparece inativo ou sem confirmação na fotografia. Revise o cadastro antes de calcular.", 409)
        company_key = employee.get("companyKey")
        if not company_key:
            raise LotPlanError("Não foi possível associar o vínculo a uma empresa estável no catálogo local. Revise o cadastro da empresa.", 409)
        if requested_company_key and requested_company_key != company_key:
            raise LotPlanError("A empresa selecionada não corresponde à empresa do vínculo. Selecione novamente a empresa ou o vínculo.", 409)
        company_name = employee["companyName"]
        original_admission = employee.get("admission_date") or ""
    else:
        company_key = requested_company_key
        selected_company = company_items.get(company_key) if company_key else None
        if not general_plan and (not selected_company or not selected_company.get("active")):
            raise LotPlanError("Selecione uma empresa ativa ou escolha Empresa geral.")
        company_name = selected_company["name"] if selected_company else "Empresa geral"
        original_admission = ""

    admission = str(payload.get("admission_date") or original_admission)
    if not valid_day(admission):
        raise LotPlanError("Informe uma data de admissão válida no formato de calendário.")
    override_reason = str(payload.get("admission_override_reason") or "").strip()[:500]
    if employee and admission != original_admission and len(override_reason) < 5:
        raise LotPlanError("A data de admissão foi alterada; informe justificativa de pelo menos 5 caracteres.")
    if employee and admission != original_admission and user["role"] not in CONFIG_ROLES:
        raise LotPlanError("Somente Administrador ou Gestor pode corrigir manualmente a admissão do snapshot.", 403)

    benefit = str(payload.get("benefit_type") or "").upper()
    if benefit not in ("VT", "VR"):
        raise LotPlanError("Selecione VT ou VR.")
    company_rule = _rule(db, company_key, benefit, effective_on=admission) if company_key else None
    if payment_source == "COMPANY" and not company_rule:
        raise LotPlanError(f"Não há regra vigente de {benefit} para {company_name}. Escolha o dia manualmente e informe os parâmetros necessários.", 409,
                           {"needsConfiguration": True, "companyKey": company_key, "benefitType": benefit})
    if payment_source == "MANUAL" and (not company_key or company_rule):
        raise LotPlanError("O pagamento manual só pode ser usado com empresa específica sem regra vigente; uma regra cadastrada fica travada.", 409)

    lot_count = payload.get("lot_count")
    if str(lot_count) not in ("1", "2", "3"):
        raise LotPlanError("Escolha 1, 2 ou 3 lotes; a seleção não é reduzida automaticamente.")
    schedule = str(payload.get("schedule") or "")
    if schedule not in ("WEEKDAYS_5X2", "ROTATING_6X1", "ROTATING_6X2"):
        raise LotPlanError("Informe uma escala suportada. Não há escala padrão cadastrada no snapshot.")
    schedule_start = "" if schedule == "WEEKDAYS_5X2" else str(payload.get("schedule_start") or "")
    if schedule in ("ROTATING_6X1", "ROTATING_6X2") and not valid_day(schedule_start):
        raise LotPlanError("Informe a data inicial do ciclo da escala.")
    if user["role"] not in WRITE_ROLES:
        raise LotPlanError("Seu perfil somente leitura não pode informar uma escala manual para calcular.", 403)

    if payment_source == "COMPANY":
        payment_day = int(company_rule["payment_day"])
        calc_rule = dict(company_rule)
    else:
        try:
            payment_day = int(payload.get("payment_day_override"))
        except (TypeError, ValueError):
            raise LotPlanError("Selecione Dia 05 ou Dia 30 para este planejamento.")
        if payment_day not in (5, 30):
            raise LotPlanError("Escolha somente Dia 05 ou Dia 30.")
        calc_rule = {
            "payment_day": payment_day,
            "payment_adjustment": str(payload.get("manual_payment_adjustment") or "PREVIOUS_WORKDAY"),
            "holiday_benefit_rule": str(payload.get("manual_holiday_benefit_rule") or "EXCLUDE"),
            "unit_mode": str(payload.get("benefit_unit_mode") or "UNSPECIFIED"),
            "february_30_policy": str(payload.get("february_30_policy") or "UNRESOLVED"),
            "admission_on_cutoff_policy": str(payload.get("admission_on_cutoff_policy") or "UNRESOLVED"),
            "source": "Parâmetros informados neste planejamento",
        }

    if benefit == "VT":
        try:
            unit_cents = int(payload.get("vt_unit_cents"))
            uses_per_day = int(payload.get("vt_uses_per_day"))
        except (TypeError, ValueError):
            raise LotPlanError("Informe a tarifa do VT em reais e quantas utilizações são feitas por dia.")
        if not 1 <= unit_cents <= 100_000_000:
            raise LotPlanError("A tarifa do VT deve ser maior que R$ 0,00 e não ultrapassar R$ 1.000.000,00.")
        if not 1 <= uses_per_day <= 20:
            raise LotPlanError("Informe de 1 a 20 utilizações de VT por dia.")
        unit_mode = "PER_USE"
        value_source = "MANUAL_PER_EMPLOYEE" if employee else "MANUAL_PER_PLAN"
    elif payment_source == "COMPANY":
        unit_cents = int(company_rule["unit_cents"])
        uses_per_day = int(company_rule["uses_per_day"])
        unit_mode = company_rule.get("unit_mode") or "UNSPECIFIED"
        value_source = "COMPANY_RULE"
    else:
        try:
            unit_cents = int(payload.get("benefit_unit_cents"))
            uses_per_day = int(payload.get("benefit_uses_per_day"))
        except (TypeError, ValueError):
            raise LotPlanError("Informe o valor do VR e a quantidade diária para este planejamento.")
        unit_mode = str(payload.get("benefit_unit_mode") or "")
        if unit_mode not in ("PER_DAY", "PER_MEAL"):
            raise LotPlanError("Informe se o valor do VR é diário ou por refeição.")
        if unit_mode == "PER_DAY":
            uses_per_day = 1
        value_source = "MANUAL_PER_PLAN"
    calc_rule.setdefault("unit_cents", unit_cents)
    calc_rule.setdefault("uses_per_day", uses_per_day)
    calc_rule.setdefault("unit_mode", unit_mode)
    if payment_source != "COMPANY":
        if calc_rule["payment_adjustment"] not in ("PREVIOUS_WORKDAY", "NEXT_WORKDAY", "NO_ADJUSTMENT"):
            calc_rule["payment_adjustment"] = "PREVIOUS_WORKDAY"
        if calc_rule["holiday_benefit_rule"] not in ("COUNT", "EXCLUDE"):
            calc_rule["holiday_benefit_rule"] = "EXCLUDE"
    zero_value_reason = str(payload.get("zero_value_reason") or "").strip()[:500]
    if unit_cents == 0 and not zero_value_reason:
        raise LotPlanError("Para usar valor zero, informe uma justificativa.")

    inputs = {"employee_key": employee["key"] if employee else None,
              "employee_name": employee["name"] if employee else str(payload.get("employee_name") or "").strip()[:120],
              "registration": employee["registration"] if employee else "",
              "company_key": company_key, "company_name": company_name,
              "unit_key": "", "unit_name": "", "admission_date": admission,
              "source_admission_date": original_admission,
              "admission_override_reason": override_reason,
              "admission_source": ("MANUAL" if not employee else "SNAPSHOT" if admission == original_admission else "MANUAL_OVERRIDE"),
              "admission_override_by": user["id"] if employee and admission != original_admission else None,
              "benefit_type": benefit, "unit_mode": unit_mode, "unit_cents": unit_cents,
              "uses_per_day": uses_per_day, "value_source": value_source,
              "value_entered_by": user["id"] if value_source == "MANUAL_PER_PLAN" else None,
              "payment_source": payment_source,
              "payment_day_override": None if payment_source == "COMPANY" else payment_day,
              "payment_day_used": payment_day, "lot_count": int(lot_count),
              "schedule": schedule, "schedule_start": schedule_start,
              "schedule_source": "MANUAL_TEMPORARY",
              "schedule_entered_by": user["id"], "respect_bank": payload.get("respect_bank") is True,
              "zero_value_reason": zero_value_reason, "client_id": company_key}
    cutoff, _ = resolve_cutoff(admission, payment_day,
                               str(calc_rule.get("february_30_policy") or "UNRESOLVED"),
                               str(calc_rule.get("admission_on_cutoff_policy") or "UNRESOLVED"))
    raw_exceptions = payload.get("bank_exceptions") or ([payload["bank_exception"]] if payload.get("bank_exception") else [])
    if not isinstance(raw_exceptions, list) or len(raw_exceptions) > 10:
        raise LotPlanError("Informe no máximo dez exceções bancárias identificadas.")
    if raw_exceptions and user["role"] not in CONFIG_ROLES:
        raise LotPlanError("Somente Administrador ou Gestor pode autorizar exceções à Relação Bancária.", 403)
    if raw_exceptions and not inputs["respect_bank"]:
        raise LotPlanError("Ative a Relação Bancária antes de autorizar uma exceção.")
    exceptions = []
    for raw in raw_exceptions:
        if not isinstance(raw, dict):
            raise LotPlanError("Informe data e justificativa para a exceção bancária.")
        day, reason = str(raw.get("day") or ""), str(raw.get("reason") or "").strip()[:500]
        if not valid_day(day) or len(reason) < 5 or not admission <= day <= cutoff:
            raise LotPlanError("A exceção bancária deve ter data na cobertura e justificativa de pelo menos cinco caracteres.")
        exceptions.append({"day": day, "reason": reason, "authorized_by": user["id"]})
    if len({item["day"] for item in exceptions}) != len(exceptions):
        raise LotPlanError("Não repita a mesma data na lista de exceções bancárias.")
    inputs["bank_exceptions"] = exceptions
    start = date.fromisoformat(admission).replace(day=1).isoformat()
    cutoff_date = date.fromisoformat(cutoff)
    end = (cutoff_date + timedelta(days=15)).isoformat()
    inputs["bank_query_period"] = {"from": start, "through": end}
    bank = _bank_rows(db, company_key, "", start, end)
    holiday_rows, location = _holidays(db, company_key, start, end)
    if not company_key:
        location = {"city": "", "state": "", "source": "Sem empresa específica (somente feriados nacionais)",
                    "available": False, "complete": True}
    result = calculate_plan(inputs=inputs, employee=employee or {}, company_rule=calc_rule,
                            bank_rows=bank, holidays=holiday_rows)
    result["can_save"] = bool(location["complete"])
    result["status"] = "VALID" if location["complete"] else "PENDING"
    result["pending_items"] = ([] if location["complete"] else
                               ["Município e UF da empresa/unidade não estão confirmados; não é possível garantir que feriados locais foram consultados."])
    result["holiday_calendar_status"] = "CONFIRMED" if location["complete"] else "PARTIAL"
    if not location["complete"]:
        result["memory"].append("Pendência: município e UF não estão confirmados no cadastro. A proposta é parcial e não pode ser salva como definitiva até confirmar a localidade e os feriados municipais.")
    company_info = {"key": company_key, "name": company_name, "city": location["city"],
                    "state": location["state"], "profileSource": location["source"], "unit": ""}
    snapshot_employee = employee or {}
    snapshot = {"employee": snapshot_employee, "company": company_info, "rule": calc_rule,
                "ruleSource": calc_rule.get("source"), "snapshotAt": snapshot_employee.get("sourceImportedAt"),
                "inputProvenance": {"admissionSource": inputs["admission_source"],
                                    "admissionOverrideReason": override_reason,
                                    "scheduleSource": inputs["schedule_source"],
                                    "scheduleEnteredBy": user["id"],
                                    "paymentSource": payment_source, "paymentDayUsed": payment_day,
                                    "benefitValueSource": inputs["value_source"],
                                    "benefitValueEnteredBy": inputs["value_entered_by"],
                                    "bankExceptions": exceptions},
                "bankQueryPeriod": {"from": start, "through": end}, "savedBy": "",
                "location": location, "bankRelationEnabled": inputs["respect_bank"],
                "capturedAt": local_stamp()}
    return inputs, result, snapshot, snapshot_employee, calc_rule
def _ensure_writer(user):
    if user["role"] not in WRITE_ROLES:
        raise LotPlanError("Seu perfil pode consultar, mas não salvar ou alterar planejamentos.", 403)


def mutate(db, user, payload: dict, expiration_mode: str | None = None):
    action = str(payload.get("action") or "")
    if action == "save_rule":
        if user["role"] not in CONFIG_ROLES:
            raise LotPlanError("Somente Administrador ou Gestor pode manter as regras de benefício por empresa.", 403)
        company_key = str(payload.get("company_key") or "")[:180]
        benefit = str(payload.get("benefit_type") or "").upper()
        companies_by_key, _ = _companies(db)
        if company_key not in companies_by_key:
            raise LotPlanError("Selecione uma empresa existente no catálogo local.")
        if benefit not in ("VT", "VR"):
            raise LotPlanError("Selecione VT ou VR.")
        old = _rule(db, company_key, benefit)
        try:
            payment_day = int(payload.get("payment_day"))
            if benefit == "VT":
                # Preserve legacy tariff columns for history, but do not collect or use
                # them as employer-wide VT values; those are entered per employee plan.
                unit_cents = int((old or {}).get("unit_cents", 0))
                uses_per_day = int((old or {}).get("uses_per_day", 1))
            else:
                unit_cents = int(payload.get("unit_cents"))
                uses_per_day = int(payload.get("uses_per_day"))
        except (TypeError, ValueError):
            raise LotPlanError("Informe o dia de pagamento e os valores da regra exigidos para este benefício.")
        payment_adjustment = str(payload.get("payment_adjustment") or "")
        holiday_rule = str(payload.get("holiday_benefit_rule") or (old or {}).get("holiday_benefit_rule") or "EXCLUDE")
        unit_mode = str(payload.get("unit_mode") or "")
        february_30_policy = str(payload.get("february_30_policy") or "UNRESOLVED")
        admission_cutoff_policy = str(payload.get("admission_on_cutoff_policy") or "UNRESOLVED")
        valid_from = str(payload.get("valid_from") or "")
        change_reason = str(payload.get("change_reason") or "").strip()[:500]
        if not valid_day(valid_from):
            raise LotPlanError("Informe uma data válida para o início da vigência desta regra.")
        if len(change_reason) < 5:
            raise LotPlanError("Informe por que a regra manual da empresa está sendo cadastrada ou alterada (mínimo de 5 caracteres).")
        if payment_day not in (5, 30) or payment_adjustment not in ("PREVIOUS_WORKDAY", "NEXT_WORKDAY", "NO_ADJUSTMENT"):
            raise LotPlanError("Escolha pagamento no dia 05 ou 30 e uma política explícita de ajuste de data.")
        if benefit == "VT":
            unit_mode = "PER_USE"
        if benefit == "VR" and unit_mode not in ("PER_DAY", "PER_MEAL"):
            raise LotPlanError("Para VR, escolha se o valor cadastrado é por dia ou por refeição.")
        if benefit == "VR" and unit_mode == "PER_DAY":
            uses_per_day = 1
        if february_30_policy not in ("UNRESOLVED", "LAST_DAY", "NEXT_MONTH"):
            raise LotPlanError("Escolha uma política válida para fevereiro quando o pagamento é dia 30.")
        if admission_cutoff_policy not in ("UNRESOLVED", "INCLUDE_CURRENT", "NEXT_CYCLE"):
            raise LotPlanError("Escolha uma política válida para admissão no dia de corte.")
        if holiday_rule not in ("COUNT", "EXCLUDE") or not 0 <= unit_cents <= 100_000_000 or not 1 <= uses_per_day <= 20:
            raise LotPlanError("Confira a regra de feriado, valor e quantidade diária.")
        zero_reason = str(payload.get("zero_value_reason") or "").strip()[:500]
        if benefit == "VR" and unit_cents == 0 and len(zero_reason) < 5:
            raise LotPlanError("Para cadastrar valor zero, informe a justificativa da empresa.")
        new_rule = {"company_key": company_key, "unit_key": "", "benefit_type": benefit,
                    "payment_day": payment_day, "payment_adjustment": payment_adjustment,
                    "holiday_benefit_rule": holiday_rule, "unit_cents": unit_cents,
                    "uses_per_day": uses_per_day, "unit_mode": unit_mode,
                    "february_30_policy": february_30_policy,
                    "admission_on_cutoff_policy": admission_cutoff_policy,
                    "zero_value_reason": zero_reason,
                    "valid_from": valid_from, "change_reason": change_reason,
                    "source": "MANUAL", "updated_by": user["id"]}
        db.execute("""INSERT INTO local_company_benefit_rules
            (company_key,unit_key,benefit_type,payment_day,payment_adjustment,holiday_benefit_rule,unit_cents,uses_per_day,unit_mode,february_30_policy,admission_on_cutoff_policy,zero_value_reason,valid_from,source,updated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'MANUAL',?) ON CONFLICT(company_key,unit_key,benefit_type) DO UPDATE SET
            payment_day=excluded.payment_day,payment_adjustment=excluded.payment_adjustment,
            holiday_benefit_rule=excluded.holiday_benefit_rule,unit_cents=excluded.unit_cents,
            uses_per_day=excluded.uses_per_day,unit_mode=excluded.unit_mode,
            february_30_policy=excluded.february_30_policy,
            admission_on_cutoff_policy=excluded.admission_on_cutoff_policy,
            zero_value_reason=excluded.zero_value_reason,valid_from=excluded.valid_from,
            source='MANUAL',updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                   (company_key, "", benefit, payment_day, payment_adjustment, holiday_rule,
                     unit_cents, uses_per_day, unit_mode, february_30_policy, admission_cutoff_policy,
                     zero_reason, valid_from, user["id"]))
        row = db.execute("SELECT * FROM local_company_benefit_rules WHERE company_key=? AND unit_key='' AND benefit_type=?", (company_key, benefit)).fetchone()
        new_rule["id"] = row["id"]
        new_rule["source"] = "Configuração local por empresa"
        db.execute("INSERT INTO local_company_benefit_rule_history(rule_id,old_json,new_json,actor_id) VALUES(?,?,?,?)",
                   (row["id"], json.dumps(old or {}, ensure_ascii=False, sort_keys=True), json.dumps(new_rule, ensure_ascii=False, sort_keys=True), user["id"]))
        return {"message": f"Regra de {benefit} salva para a empresa.", "rule": dict(row)}
    if action in ("calculate", "save", "update"):
        if action in ("save", "update"):
            _ensure_writer(user)
        inputs, result, snapshot, employee, company_rule = _input_payload(db, user, payload)
        calculation_token = _calculation_token(inputs, result, snapshot)
        if action == "calculate":
            return {"message": "Prévia calculada; ainda não foi salva.", "inputs": inputs,
                    "result": result, "snapshot": snapshot, "calculationToken": calculation_token}
        _ensure_writer(user)
        if result.get("can_save") is False:
            raise LotPlanError("O calendário de feriados está parcial. Confirme município e UF da empresa antes de salvar este planejamento.", 409,
                               {"needsLocation": True})
        submitted_token = str(payload.get("calculation_token") or "")
        if not submitted_token or not hmac.compare_digest(submitted_token, calculation_token):
            raise LotPlanError("Uma regra ou fonte mudou desde a prévia. Revise o resultado e calcule novamente antes de salvar.", 409,
                               {"needsRecalculation": True})
        expiration_mode = expiration_mode or EXPIRATION_MODE
        if expiration_mode != "FOUR_LOCAL_DAYS":
            raise LotPlanError("A expiração está configurada para quatro dias corridos no horário de São Paulo.", 409,
                               {"needsExpirationPolicy": True})
        now_dt = datetime.now(LOCAL_TZ)
        expiry_local = now_dt + timedelta(days=4)
        saved_at, expires_at = utc_stamp(now_dt), utc_stamp(expiry_local)
        snapshot["savedBy"] = user["name"]
        snapshot["savedAt"] = saved_at
        snapshot["expiresAt"] = expires_at
        competence = result["cutoff_date"]
        request_key = str(payload.get("idempotency_key") or "")[:100]
        if not request_key:
            raise LotPlanError("Chave de salvamento ausente; atualize a prévia e tente novamente.")
        request_material = json.dumps({"inputs": inputs, "result": result}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(request_material.encode("utf-8")).hexdigest()
        existing_request = db.execute("SELECT payload_hash,plan_id FROM local_benefit_plan_idempotency WHERE request_key=?", (request_key,)).fetchone()
        if existing_request:
            if existing_request["payload_hash"] != payload_hash:
                raise LotPlanError("Esta chave de salvamento já foi usada com outros dados. Recalcule para gerar outra chave.", 409)
            plan = db.execute("SELECT * FROM local_benefit_plans WHERE id=?", (existing_request["plan_id"],)).fetchone()
            if plan:
                return {"message": "O salvamento já havia sido concluído.", "plan": _plan_summary(plan), "idempotent": True}
        plan_id = str(payload.get("plan_id") or "")
        if action == "update":
            if not plan_id.isdigit():
                raise LotPlanError("Planejamento para edição inválido.")
            current = db.execute("SELECT * FROM local_benefit_plans WHERE id=? AND deleted_at IS NULL", (int(plan_id),)).fetchone()
            if not current:
                raise LotPlanError("Planejamento não encontrado ou já excluído.", 404)
            if int(payload.get("expected_version") or 0) != int(current["row_version"]):
                raise LotPlanError("O planejamento mudou desde que foi aberto. Recarregue os dados antes de salvar.", 409)
            previous_snapshot = _json(current["source_snapshot_json"])
            db.execute("""INSERT OR IGNORE INTO local_benefit_plan_revisions
                (plan_id,row_version,title,inputs_json,result_json,source_snapshot_json,saved_by,saved_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                       (int(plan_id), int(current["row_version"]), current["title"], current["inputs_json"],
                        current["result_json"], current["source_snapshot_json"], current["saved_by"],
                        previous_snapshot.get("savedAt") or current["updated_at"]))
            if current["pinned"]:
                expires_at = None
                snapshot["expiresAt"] = None
            title = str(payload.get("title") or current["title"]).strip()[:120]
            db.execute("""UPDATE local_benefit_plans SET title=?,inputs_json=?,result_json=?,employee_key=?,company_key=?,unit_key='',
                benefit_type=?,competence=?,expires_at=?,saved_by=?,source_snapshot_json=?,row_version=row_version+1,
                idempotency_key=?,expiration_noted_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                       (title or "Planejamento de benefício", json.dumps(inputs, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                         inputs["employee_key"], (inputs["company_key"] or None), inputs["benefit_type"], competence, expires_at,
                        user["id"], json.dumps(snapshot, ensure_ascii=False), request_key, int(plan_id)))
            saved_id = int(plan_id)
            event = "UPDATE"
        else:
            candidates = db.execute("""SELECT id,title,inputs_json FROM local_benefit_plans WHERE employee_key IS ? AND COALESCE(company_key,'')=? AND unit_key=''
                AND benefit_type=? AND competence=? AND deleted_at IS NULL AND (pinned=1 OR expires_at>?)""",
                                    (inputs["employee_key"], inputs["company_key"], inputs["benefit_type"], competence, saved_at)).fetchall()
            identity = normalize(inputs.get("employee_name") or "")
            logical = next((candidate for candidate in candidates
                            if normalize(_json(candidate["inputs_json"]).get("employee_name") or "") == identity), None)
            if logical:
                raise LotPlanError("Já existe um planejamento ativo para essa identificação, empresa, benefício e corte. Abra o registro existente para editar ou exclua-o antes de criar outro.", 409,
                                   {"existingPlanId": logical["id"], "existingTitle": logical["title"]})
            title = str(payload.get("title") or
                        f"{inputs['employee_name'] or inputs['company_name']} · {inputs['benefit_type']} · {competence}").strip()[:120]
            cursor = db.execute("""INSERT INTO local_benefit_plans
                (title,inputs_json,result_json,employee_key,company_key,unit_key,benefit_type,competence,expires_at,saved_by,source_snapshot_json,idempotency_key,created_by)
                VALUES(?,?,?,?,?,'',?,?,?,?,?,?,?)""",
                                (title or "Planejamento de benefício", json.dumps(inputs, ensure_ascii=False),
                                  json.dumps(result, ensure_ascii=False), inputs["employee_key"], (inputs["company_key"] or None),
                                 inputs["benefit_type"], competence, expires_at, user["id"],
                                 json.dumps(snapshot, ensure_ascii=False), request_key, user["id"]))
            saved_id, event = cursor.lastrowid, "CREATE"
        db.execute("INSERT INTO local_benefit_plan_idempotency(request_key,payload_hash,plan_id,actor_id) VALUES(?,?,?,?)",
                   (request_key, payload_hash, saved_id, user["id"]))
        db.execute("INSERT INTO local_benefit_plan_audit(plan_id,actor_id,action,metadata_json) VALUES(?,?,?,?)",
                   (saved_id, user["id"], event, json.dumps({"benefit": inputs["benefit_type"], "cutoff": competence,
                                                               "employeeKey": inputs["employee_key"], "companyKey": inputs["company_key"],
                                                              "expiresAt": expires_at}, ensure_ascii=False)))
        row = db.execute("SELECT * FROM local_benefit_plans WHERE id=?", (saved_id,)).fetchone()
        return {"message": "Planejamento salvo com snapshot, autoria e prazo de expiração.", "plan": _plan_summary(row)}
    if action in ("pin", "delete"):
        _ensure_writer(user)
        try:
            plan_id = int(payload.get("plan_id"))
        except (TypeError, ValueError):
            raise LotPlanError("Planejamento inválido.")
        row = db.execute("SELECT * FROM local_benefit_plans WHERE id=? AND deleted_at IS NULL", (plan_id,)).fetchone()
        if not row:
            raise LotPlanError("Planejamento não encontrado ou já excluído.", 404)
        if action == "pin":
            pinned = bool(payload.get("pinned"))
            if pinned:
                db.execute("UPDATE local_benefit_plans SET pinned=1,pinned_at=?,pinned_by=?,row_version=row_version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (utc_stamp(), user["id"], plan_id))
            else:
                expiration_mode = expiration_mode or EXPIRATION_MODE
                if expiration_mode != "FOUR_LOCAL_DAYS":
                    raise LotPlanError("A expiração está configurada para quatro dias corridos no horário de São Paulo.", 409,
                                       {"needsExpirationPolicy": True})
                now_dt = datetime.now(LOCAL_TZ)
                expiry_local = now_dt + timedelta(days=4)
                db.execute("UPDATE local_benefit_plans SET pinned=0,pinned_at=NULL,pinned_by=NULL,expires_at=?,row_version=row_version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (utc_stamp(expiry_local), plan_id))
            db.execute("INSERT INTO local_benefit_plan_audit(plan_id,actor_id,action,metadata_json) VALUES(?,?,?,?)",
                       (plan_id, user["id"], "PIN" if pinned else "UNPIN", json.dumps({"pinned": pinned}, ensure_ascii=False)))
            return {"message": "Planejamento fixado; não expira enquanto fixado." if pinned else "Planejamento desafixado; prazo reiniciado.",
                    "pinned": pinned}
        db.execute("UPDATE local_benefit_plans SET deleted_at=?,deleted_by=?,row_version=row_version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (utc_stamp(), user["id"], plan_id))
        db.execute("INSERT INTO local_benefit_plan_audit(plan_id,actor_id,action,metadata_json) VALUES(?,?, 'DELETE',?)",
                   (plan_id, user["id"], json.dumps({"title": row["title"], "employeeKey": row["employee_key"],
                                                      "companyKey": row["company_key"], "benefit": row["benefit_type"],
                                                      "competence": row["competence"]}, ensure_ascii=False)))
        return {"message": "Planejamento excluído; a competência está liberada. A trilha e o snapshot foram mantidos para auditoria."}
    raise LotPlanError("Ação de planejamento desconhecida.")
