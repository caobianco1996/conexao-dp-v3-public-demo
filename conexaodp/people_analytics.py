"""Read-only People Analytics over the same local records used by Cadastros."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import re

from .companies import catalog as company_catalog
from .registry import normalize, project
from .sib_import import source_records
from .sib_scan import summary as scan_summary
from .permissions import can as has_permission

MISSING = "Não informado"
DIMENSIONS = ("contracts", "departments", "jobs", "states", "branches", "reasons")
STATUSES = ("all", "active", "terminated", "future", "unknown")


def _one(params, key, default=""):
    value = params.get(key, [default])
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return str(value if value not in (None, "") else default)[:200]


def _many(params, key):
    values = params.get(key, [])
    if not isinstance(values, list):
        values = [values]
    selected = list(dict.fromkeys(str(value)[:200] for value in values if value))
    if len(selected) > 500:
        raise ValueError("Selecione até 500 opções por filtro. Para incluir todas as empresas, limpe a seleção de inclusão.")
    return selected


def _date(value):
    try:
        text = str(value or "")
        return date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    except ValueError:
        return None


def _shift_year(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _period(params, earliest, today):
    preset = _one(params, "period", "all")
    end = today
    if preset == "month":
        start = today.replace(day=1)
    elif preset in ("3months", "6months", "12months"):
        months = int(preset.removesuffix("months"))
        month = today.year * 12 + today.month - months
        start = date(month // 12, month % 12 + 1, 1)
    elif preset == "year":
        start = date(today.year, 1, 1)
    elif preset == "lastyear":
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif preset == "custom":
        start, end = _date(_one(params, "start")), _date(_one(params, "end"))
        if not start or not end:
            raise ValueError("Informe as duas datas do período.")
    else:
        preset, start = "all", earliest or today.replace(day=1)
    if start > end:
        raise ValueError("A data inicial deve ser anterior ou igual à data final.")
    if start.year < 1900 or end > today:
        raise ValueError("Use datas entre 1900 e hoje. A análise não projeta movimentos futuros.")
    return preset, start, end


def _active(row, day):
    if row.get('contract_cancelled') or (row.get('sib_presence') == 'EXCLUDED' and day >= date.today()):
        return False
    return bool(row["reliable"] and row["admission"] and row["admission"] <= day
                and (not row["termination"] or row["termination"] > day))


def _status(row, day):
    if (row.get('sib_presence') == 'EXCLUDED' and day >= date.today()
            and not (row.get('termination') and row['termination'] <= day)):
        return 'unknown'
    if not row["reliable"] or not row["admission"]:
        return "unknown"
    if row["admission"] > day:
        return "future"
    return "active" if _active(row, day) else "terminated"


def _metrics(rows, start, end):
    opening_day = start - timedelta(days=1)
    admitted = [row for row in rows if row["reliable"] and row["admission"] and start <= row["admission"] <= end]
    terminated = [row for row in rows if row["reliable"] and row["termination"] and start <= row["termination"] <= end]
    active = [row for row in rows if _active(row, end)]
    opening = sum(_active(row, opening_day) for row in rows)
    overlapping = [row for row in rows if row["reliable"] and row["admission"] and row["admission"] <= end
                   and (not row["termination"] or row["termination"] >= start)]
    average = (opening + len(active)) / 2
    tenures = [(row["termination"] - row["admission"]).days for row in terminated if row["admission"]]
    return {"active": len(active), "opening": opening, "admitted": len(admitted), "terminated": len(terminated),
            "balance": len(admitted) - len(terminated), "links": len(overlapping),
            "people": len({row["person_key"] for row in overlapping}),
            "activePeople": len({row["person_key"] for row in active}),
            "turnover": round((len(admitted) + len(terminated)) / 2 / average * 100, 2) if average else None,
            "averageTenure": round(sum(tenures) / len(tenures), 1) if tenures else None,
            "unknown": sum(_status(row, end) == "unknown" for row in rows)}


def _buckets(start, end, granularity):
    points = []
    cursor = date(start.year, 1 if granularity == "year" else ((start.month - 1) // 3 * 3 + 1) if granularity == "quarter" else start.month, 1)
    while cursor <= end:
        if granularity == "year":
            following, label = date(cursor.year + 1, 1, 1), str(cursor.year)
        else:
            step = 3 if granularity == "quarter" else 1
            month = cursor.year * 12 + cursor.month - 1 + step
            following = date(month // 12, month % 12 + 1, 1)
            label = f"{(cursor.month - 1) // 3 + 1}º tri/{cursor.year}" if step == 3 else f"{cursor.month:02}/{cursor.year}"
        points.append({"label": label, "start": max(start, cursor), "end": min(end, following - timedelta(days=1))})
        cursor = following
    return points


def _timeline(rows, buckets):
    # Sorted dates and binary searches keep a full historical monthly view cheap.
    admissions = sorted(row["admission"] for row in rows if row["reliable"] and row["admission"])
    terminations = sorted(row["termination"] for row in rows if row["reliable"] and row["termination"])
    reliable_starts = sorted(row["admission"] for row in rows if row["reliable"] and row["admission"])
    reliable_ends = sorted(row["termination"] for row in rows if row["reliable"] and row["admission"] and row["termination"])
    result = []
    for point in buckets:
        start, after = point["start"], point["end"] + timedelta(days=1)
        admitted = bisect_left(admissions, after) - bisect_left(admissions, start)
        terminated = bisect_left(terminations, after) - bisect_left(terminations, start)
        result.append({"label": point["label"], "start": start.isoformat(), "end": point["end"].isoformat(),
                       "admitted": admitted, "terminated": terminated, "balance": admitted - terminated,
                       "active": bisect_left(reliable_starts, after) - bisect_left(reliable_ends, after)})
    return result


def _facet(rows, key):
    return [{"value": value, "label": value, "count": count}
            for value, count in sorted(Counter(row[key] for row in rows).items(), key=lambda item: normalize(item[0]))]


def _load(db):
    # Share a single parsed snapshot across the registry and company projection.
    # Re-reading the full SIB catalog for each projection multiplied page loads.
    sib_records = {key: list(source_records(db, key)) for key in ("companies", "employments")}
    _, links, _ = project(db, sib_records=sib_records)
    catalog = company_catalog(db, sib_records=sib_records)
    companies = {item["key"]: {"value": item["key"], "name": item["name"], "code": item["code"],
                               "label": f'{item["code"]} · {item["name"]}' if item["code"] else item["name"],
                               "count": 0, "active": 0} for item in catalog}
    name_keys = defaultdict(list)
    for item in catalog:
        name_keys[normalize(item["name"])].append(item["key"])
    company_data = dict(sib_records["companies"])
    extra = dict(sib_records["employments"])
    rows = []
    for item in links:
        if item.get('contract_cancelled'):
            continue
        row = dict(item)
        company_key = row.get("company_key")
        if not company_key:
            matches = name_keys[normalize(row["company"])]
            company_key = matches[0] if len(matches) == 1 else "name:" + hashlib.sha256(normalize(row["company"]).encode()).hexdigest()
        if company_key not in companies:
            companies[company_key] = {"value": company_key, "name": row["company"] or MISSING,
                                      "code": "", "label": row["company"] or MISSING, "count": 0, "active": 0}
        raw = extra.get(row["key"][4:], {}) if row["source"] == "SIB" else {}
        company = company_data.get(company_key[4:], {}) if company_key.startswith("sib:") else {}
        row.update(companyKey=company_key, companyCode=companies[company_key]["code"],
                   contracts=row.get("contract_type") or MISSING, departments=row.get("department") or MISSING,
                   jobs=row.get("job_title") or MISSING, states=company.get("state") or MISSING,
                   branches=(f'Empresa {raw.get("employerCode")} / filial {raw.get("branchCode")}'
                             if raw.get("employerCode") is not None and raw.get("branchCode") is not None else MISSING),
                   reasons=row.get("dismissal_reason") or (f'Código SIB {raw["terminationType"]}' if raw.get("terminationType") not in (None, "") else MISSING))
        row["admission"], row["termination"] = _date(row.get("admission_date")), _date(row.get("termination_date"))
        row["reliable"] = (row.get("status") != "Situação a conferir"
                           and (not row.get("admission_date") or row["admission"] is not None)
                           and (not row.get("termination_date") or row["termination"] is not None)
                           and (not row["admission"] or row["admission"].year >= 1900)
                           and (not row["termination"] or row["termination"].year >= 1900)
                           and not (row["admission"] and row["termination"] and row["termination"] < row["admission"]))
        rows.append(row)
    return rows, companies


def get_data(db, params, *, user=None, report_export_all=False, use_cache=False):
    if _one(params, "reportCatalog") == "1" or _one(params, "report"):
        # Reports have their own visible controls. Old analytics URLs cannot leave
        # hidden status, comparison or dimension filters affecting an export.
        report_controls = {"period", "start", "end", "companies", "excludeCompanies", "reportCatalog", "report",
                           "reportPopulation", "reportPage", "reportColumns", "reportColumnsSet", "refresh"}
        params = {key: value for key, value in params.items() if key in report_controls}
    today = datetime.now(timezone(timedelta(hours=-3))).date()
    snapshot, cache_key = None, None
    if use_cache and not report_export_all:
        from . import people_analytics_cache as prepared
        all_rows, companies, snapshot = prepared.load(db, _load, force=_one(params, 'refresh') == '1')
        # Reports include live document/absence data and session-specific actions.
        # Only ordinary dashboards reuse complete responses.
        if not (_one(params, 'reportCatalog') or _one(params, 'report')):
            cache_key = prepared.response_key(snapshot, params, user)
            cached = prepared.get_response(cache_key)
            if cached is not None:
                scan = scan_summary(db)
                return {**cached, 'meta': {**cached['meta'], **snapshot[1],
                    'scanStarted': scan['started'], 'scanChecked': scan.get('checked', 0),
                    'scanDone': scan.get('done', False),
                    'scanRunning': bool(scan.get('running') or scan.get('inFlight') or scan.get('autoResume'))}}
    else:
        all_rows, companies = _load(db)
    if user is not None:
        from .permissions import allowed_company, scope
        if scope(user) != 'ALL':
            all_rows = [row for row in all_rows if allowed_company(db, user, company_key=row['companyKey'],
                        code=row.get('companyCode', ''), name=row.get('company', ''))]
            visible_company_keys = {row['companyKey'] for row in all_rows}
            companies = {key: item for key, item in companies.items() if key in visible_company_keys}
    include_tests = _one(params, "includeTests") == "1"
    base = [row for row in all_rows if include_tests or not row.get("is_test")]
    available_sources = sorted({row["source"] for row in base})
    sources = _many(params, "sources") if "sources" in params else (["SIB"] if "SIB" in available_sources else [])
    population = [row for row in base if not sources or row["source"] in sources]
    earliest = min((row["admission"] for row in population if row["admission"] and 1900 <= row["admission"].year and row["admission"] <= today), default=today)
    preset, start, end = _period(params, earliest, today)
    include, exclude = _many(params, "companies"), _many(params, "excludeCompanies")
    dimensions = {key: _many(params, key) for key in DIMENSIONS}
    status = _one(params, "status", "all")
    if status not in STATUSES:
        status = "all"
    query = _one(params, "q")[:100].strip()
    needle = normalize(query)
    rows = [row for row in population
            if (not include or row["companyKey"] in include) and row["companyKey"] not in exclude
            and all(not values or row[key] in values for key, values in dimensions.items())
            and (status == "all" or _status(row, end) == status)
            and (not needle or needle in normalize(" ".join(str(row.get(key) or "") for key in ("name", "registration", "company", "companyCode", "department", "job_title"))))]
    granularity = _one(params, "granularity", "auto")
    effective_granularity = granularity if granularity in ("month", "quarter", "year") else "year" if (end - start).days > 1096 else "month"
    buckets = _buckets(start, end, effective_granularity)
    metrics = _metrics(rows, start, end)
    compare_mode = _one(params, "compare", "none")
    comparison = None
    if compare_mode in ("previous", "year", "custom"):
        if compare_mode == "previous":
            previous_end = start - timedelta(days=1)
            previous_start = previous_end - (end - start)
            # Full-history windows may start before the supported source dates.
            if previous_start.year < 1900:
                raise ValueError("O período anterior ultrapassa 1900. Escolha datas personalizadas para comparar.")
        elif compare_mode == "year":
            previous_start, previous_end = _shift_year(start), _shift_year(end)
            if previous_start.year < 1900:
                raise ValueError("A comparação anual ultrapassa 1900. Escolha outro período.")
        else:
            previous_start, previous_end = _date(_one(params, "compareStart")), _date(_one(params, "compareEnd"))
            if not previous_start or not previous_end or previous_start > previous_end or previous_end > today or previous_start.year < 1900:
                raise ValueError("Informe um período de comparação válido, até a data de hoje.")
        comparison = {"start": previous_start.isoformat(), "end": previous_end.isoformat(),
                      "metrics": _metrics(rows, previous_start, previous_end)}
    else:
        compare_mode = "none"
    company_groups = defaultdict(list)
    for row in rows:
        company_groups[row["companyKey"]].append(row)
    company_stats = [{"key": key, "name": companies[key]["name"], "code": companies[key]["code"],
                      **_metrics(group, start, end)} for key, group in company_groups.items()]
    ranking_metric = _one(params, "rankBy", "admitted")
    if ranking_metric not in ("active", "admitted", "terminated", "balance", "turnover", "links"):
        ranking_metric = "admitted"
    company_stats.sort(key=lambda row: (-(row[ranking_metric] or 0), normalize(row["name"])))
    compare_companies = _many(params, "compareCompanies")
    if len(compare_companies) > 6:
        raise ValueError("Selecione até seis empresas para comparar no gráfico.")
    compare_stats = []
    comparison_omitted = 0
    for key in compare_companies:
        if key in exclude or (include and key not in include):
            comparison_omitted += 1
            continue
        if key in companies:
            group = company_groups.get(key, [])
            compare_stats.append({"key": key, "name": companies[key]["name"], "code": companies[key]["code"],
                                  **_metrics(group, start, end), "timeline": _timeline(group, buckets)})
    breakdown_key = _one(params, "groupBy", "contracts")
    if breakdown_key not in (*DIMENSIONS, "source", "company"):
        breakdown_key = "contracts"
    groups = defaultdict(list)
    for row in rows:
        groups[row["companyKey"] if breakdown_key == "company" else row[breakdown_key]].append(row)
    breakdown = [{"key": key, "label": companies[key]["label"] if breakdown_key == "company" else key,
                  **_metrics(group, start, end)} for key, group in groups.items()]
    breakdown.sort(key=lambda row: (-(row[ranking_metric] or 0), normalize(row["label"])))
    tenure_counts = Counter()
    for row in rows:
        if row["reliable"] and row["admission"] and row["termination"] and start <= row["termination"] <= end:
            days = (row["termination"] - row["admission"]).days
            label = "Até 30 dias" if days <= 30 else "31 a 90 dias" if days <= 90 else "91 a 180 dias" if days <= 180 else "181 a 365 dias" if days <= 365 else "Mais de 1 ano"
            tenure_counts[label] += 1
    focus = _one(params, "focus", "all")
    if focus not in ("all", "active", "admitted", "terminated", "unknown"):
        focus = "all"
    quality_issue = _one(params, "qualityIssue")
    quality_checks = {
        "missingAdmission": lambda row: not row["admission"],
        "invalidDates": lambda row: not row["reliable"],
        "missingContract": lambda row: row["contracts"] == MISSING,
        "missingDepartment": lambda row: row["departments"] == MISSING,
        "missingJob": lambda row: row["jobs"] == MISSING,
    }
    if quality_issue not in ("all", *quality_checks):
        quality_issue = ""
    def selected(row):
        # Quality totals cover the filtered base, including records with no dates.
        # Its drill-down must use the same population instead of hiding those rows.
        if quality_issue == "all": return any(check(row) for check in quality_checks.values())
        if quality_issue: return quality_checks[quality_issue](row)
        if focus == "active": return _active(row, end)
        if focus == "admitted": return row["reliable"] and row["admission"] and start <= row["admission"] <= end
        if focus == "terminated": return row["reliable"] and row["termination"] and start <= row["termination"] <= end
        if focus == "unknown": return _status(row, end) == "unknown"
        return row["reliable"] and row["admission"] and row["admission"] <= end and (not row["termination"] or row["termination"] >= start)
    records = [row for row in rows if selected(row)]
    sort_by = _one(params, "sort", "name")
    if sort_by not in ("name", "company", "admission_date", "termination_date", "registration", "status", "source"):
        sort_by = "name"
    descending = _one(params, "direction", "asc") == "desc"
    def record_order(row):
        if sort_by == "status":
            status_labels = {"unknown": "A conferir", "future": "Admissão futura",
                             "active": "Ativo", "terminated": "Desligado"}
            value = status_labels[_status(row, end)]
        else:
            value = str(row.get(sort_by) or "")
        if sort_by == "registration" and value.isdigit():
            return (0, int(value), row["key"])
        return (1, normalize(value), row["key"])
    records.sort(key=record_order, reverse=descending)
    try:
        page = max(1, min(int(_one(params, "page", "1")), max(1, (len(records) + 49) // 50)))
    except ValueError:
        page = 1
    page_rows = [{"key": row["key"], "name": row["name"], "registration": row["registration"],
                  "company": row["company"], "companyCode": row["companyCode"], "source": row["source"],
                  "admission": row.get("admission_date") or "", "termination": row.get("termination_date") or "",
                  "status": _status(row, end), "contract": row["contracts"]} for row in records[(page - 1) * 50:page * 50]]
    for row in population:
        companies[row["companyKey"]]["count"] += 1
        companies[row["companyKey"]]["active"] += _active(row, end)
    scan = scan_summary(db)
    imported_at = db.execute("SELECT MAX(imported_at) FROM local_sib_records").fetchone()[0]
    quality = {"missingAdmission": sum(not row["admission"] for row in rows),
               "invalidDates": sum(not row["reliable"] for row in rows),
               "missingContract": sum(row["contracts"] == MISSING for row in rows),
               "missingDepartment": sum(row["departments"] == MISSING for row in rows),
               "missingJob": sum(row["jobs"] == MISSING for row in rows)}
    reports = None
    if _one(params, "reportCatalog") == "1" or _one(params, "report"):
        from .people_reports import build_reports
        reports = build_reports(db, rows, companies, start, end, params,
                                admin=bool(user is not None and has_permission(user, 'sensitive.view')),
                                export_all=report_export_all)
        reports["csrf"] = str(user["csrf_token"]) if user is not None and "csrf_token" in user.keys() else ""
    from .sib_absences import analytics as absence_analytics
    absence_summary = absence_analytics(db, {row['key'] for row in rows}, end.isoformat()) if user is not None and user['role'] == 'ADMIN' else None
    result = {"metrics": metrics, "comparison": comparison, "timeline": _timeline(rows, buckets),
            "absences": absence_summary,
            "reports": reports,
            "companies": company_stats, "companyComparison": compare_stats, "breakdown": breakdown,
            "retention": [{"label": label, "count": tenure_counts[label]} for label in ("Até 30 dias", "31 a 90 dias", "91 a 180 dias", "181 a 365 dias", "Mais de 1 ano")],
            "quality": quality, "records": page_rows, "page": page, "pageSize": 50, "recordTotal": len(records),
            "options": {"companies": sorted(companies.values(), key=lambda row: normalize(row["name"])),
                        "sources": [{"value": value, "label": value, "count": sum(row["source"] == value for row in base)} for value in available_sources],
                        **{key: _facet(population, key) for key in DIMENSIONS}},
            "filters": {"period": preset, "start": start.isoformat(), "end": end.isoformat(), "sources": sources,
                        "companies": include, "excludeCompanies": exclude, **dimensions, "status": status, "q": query,
                        "granularity": granularity, "effectiveGranularity": effective_granularity,
                        "compare": compare_mode, "compareStart": comparison["start"] if comparison else "",
                        "compareEnd": comparison["end"] if comparison else "", "compareCompanies": compare_companies,
                        "groupBy": breakdown_key, "rankBy": ranking_metric, "focus": focus, "qualityIssue": quality_issue, "sort": sort_by,
                        "direction": "desc" if descending else "asc", "includeTests": include_tests,
                        **({"reportCatalog": "1", "report": _one(params, "report"),
                            "reportPopulation": reports["selected"]["population"] if reports["selected"] else "active",
                            "reportPage": reports["selected"]["page"] if reports["selected"] else 1,
                            "reportColumns": reports["selected"]["columnKeys"] if reports["selected"] else [],
                            "reportColumnsSet": "1" if reports["selected"] and reports["selected"]["columnKeys"] else ""} if reports else {})},
            "meta": {"today": today.isoformat(), "earliest": earliest.isoformat(), "sourceLinks": len(base),
                     "filteredLinks": len(rows), "companyCount": len(company_groups), "importedAt": imported_at or "",
                     "generatedAt": datetime.now(timezone.utc).isoformat(), "scanStarted": scan["started"],
                     "comparisonOmitted": comparison_omitted,
                     "scanRunning": bool(scan.get("running") or scan.get("inFlight") or scan.get("autoResume")),
                     "scanChecked": scan.get("checked", 0), "scanDone": scan.get("done", False),
                     **(snapshot[1] if snapshot else {})}}
    if cache_key is not None:
        prepared.save_response(cache_key, result)
    return result
