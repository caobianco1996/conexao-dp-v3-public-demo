"""Prepare private workbook histories without changing payroll or absence records.

The input contains cached, typed cell values. No formula is executed and no
identity is inferred from a registration number alone.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import math
import re
import sqlite3
import unicodedata


_SHEETS = (
    {"name": "B. DADOS", "header": 2, "code": "A", "person": "B", "cpf": "X", "admission": "L",
     "title": "Ficha cadastral histórica"},
    {"name": "AFASTADOS", "header": 3, "code": "A", "person": "B", "cpf": "E", "admission": "H",
     "title": "Acompanhamento histórico de afastamento"},
    {"name": "CONTROLE DE ESTÁGIO", "header": 2, "code": "A", "person": "B", "cpf": "C", "admission": "F",
     "title": "Controle histórico de estágio", "modality": "15"},
    {"name": "CONVENIO", "header": 2, "code": "B", "person": "C", "admission": "D",
     "title": "Histórico de convênio e dependentes"},
    {"name": "PRÉ-NATAL", "header": 2, "code": "A", "person": "B", "admission": "F",
     "title": "Acompanhamento histórico pré-natal"},
    {"name": "PERÍCIA", "header": 3, "code": "A", "person": "B",
     "title": "Histórico de perícias"},
)
_CELL = re.compile(r"^\$?([A-Za-z]+)\$?([1-9][0-9]*)$")
_EMPTY = {"", "0", "-", "#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!"}
_NOTE = ("Informações históricas da planilha. As situações e datas de acompanhamento "
         "da origem não alteram a ficha atual do SIB nem criam afastamentos, benefícios ou alertas ativos.")


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _text(value):
    return " ".join(str(value or "").split())


def _present(value):
    return value is not None and str(value).strip().upper() not in _EMPTY


def _integer(value):
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    try:
        number = Decimal(text)
        if not number.is_finite() or number != number.to_integral_value() or number <= 0:
            return ""
        return str(int(number))
    except (InvalidOperation, ValueError, OverflowError):
        return ""


def _registration(value):
    """Keep explicit employer/branch selectors when the sheet supplies them."""
    text = str(value or "").strip()
    parts = re.fullmatch(r"([0-9]+):([0-9]+):([0-9]+)", text)
    if parts:
        employer, branch, number = (str(int(part)) for part in parts.groups())
        return {"number": number if int(number) > 0 else "", "employer": employer, "branch": branch}
    compound = re.fullmatch(r"([0-9]+)-([0-9]+)", text)
    if compound:
        branch, number = (str(int(part)) for part in compound.groups())
        return {"number": number if int(number) > 0 else "", "employer": "", "branch": branch}
    return {"number": _integer(value), "employer": "", "branch": ""}


def _cpf(value):
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\+?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?", text):
        text = _integer(text)
    else:
        text = re.sub(r"[.\-/\s]", "", text)
    if not text.isdigit() or len(text) > 11:
        return ""
    # Excel numeric cells lose the leading zeroes of a CPF identifier.
    text = text.zfill(11)
    return text if len(set(text)) > 1 else ""


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not _present(text) or text == "00/00/0000":
        return ""
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d%m%Y", "%Y%m%d"):
        try:
            result = datetime.strptime(text, pattern).date()
            if 1900 <= result.year <= 2100:
                return result.isoformat()
        except ValueError:
            pass
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            if 20000 <= value <= 70000:
                return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
            integer = _integer(value)
            if len(integer) in (7, 8):
                return _date(integer.zfill(8))
        except (ValueError, OverflowError):
            pass
    return ""


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal):
        return str(value)
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _column_number(label):
    number = 0
    for char in label:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _grid(cells):
    rows = defaultdict(dict)
    for coordinate, value in (cells or {}).items():
        match = _CELL.fullmatch(str(coordinate))
        if match:
            column, row = match.groups()
            rows[int(row)][column.upper()] = value
    return rows


def _source_records(spec, cells):
    rows = _grid(cells)
    headers = rows.get(spec["header"], {})
    columns = sorted((column for column, label in headers.items() if _present(label)), key=_column_number)
    if not columns:
        return []
    result = []
    for number, row in sorted(rows.items()):
        if number <= spec["header"]:
            continue
        hint = _registration(row.get(spec["code"]))
        name = _text(row.get(spec["person"]))
        if not _present(name):
            name = ""
        if _normalize(name) in {"nome", "nome completo", "codigo", "informacoes"}:
            continue
        # Do not turn template formulas/default status text into people. Partial
        # identity rows remain reviewable instead of being silently discarded.
        if not hint["number"] and not name:
            continue
        result.append({
            "sheet": spec["name"], "row": number, "hint": hint, "name": name,
            "cpf": _cpf(row.get(spec.get("cpf", ""))),
            "admission": _date(row.get(spec.get("admission", ""))),
            "modality": spec.get("modality", ""),
            "section": {"title": spec["title"], "columns": [_text(headers[column]) for column in columns],
                        "rows": [[_json_value(row.get(column)) for column in columns]],
                        "sourceSheet": spec["name"], "sourceRange": f"{columns[0]}{number}:{columns[-1]}{number}"},
        })
    return result


def _employment_index(db):
    index = defaultdict(list)
    if db is None:
        return index
    try:
        records = db.execute("SELECT source_id,data_json FROM local_sib_records WHERE dataset='employments'")
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        return index
    for source_id, payload in records:
        if not re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+", str(source_id)):
            continue
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        employer, branch, number = (str(int(value)) for value in str(source_id).split(":"))
        if _integer(data.get("registration")) != number:
            continue
        tax_id, admission = _cpf(data.get("cpf")), _date(data.get("admissionDate"))
        if not tax_id or not admission:
            continue
        index[(number, tax_id, admission)].append({
            "personKey": "sib:" + str(source_id), "admission": admission,
            "employer": employer, "branch": branch,
            "modality": _integer(data.get("contractTypeCode")),
        })
    return index


def _match(record, index):
    if not record["hint"]["number"] or not record["cpf"] or not record["admission"]:
        return None
    options = index.get((record["hint"]["number"], record["cpf"], record["admission"]), [])
    options = [item for item in options
               if (not record["modality"] or item["modality"] == record["modality"])
               and all(not record["hint"][key] or item[key] == record["hint"][key] for key in ("employer", "branch"))]
    return options[0] if len(options) == 1 else None


def build_histories(sheets, db):
    """Return private, source-traceable histories; perform no database writes.

    Confirmed records share an item only for the same composite SIB key and
    admission. An unconfirmed source row stays in its own review item. Perícia
    may inherit the identity of one unique matching AFASTADOS source row.
    """
    index = _employment_index(db)
    grouped, histories, absence_sources = {}, [], defaultdict(list)
    sheet_stats, totals = {}, Counter()
    available = {_normalize(key): (key, value) for key, value in (sheets or {}).items()}
    for spec in _SHEETS:
        original_name, cells = available.get(_normalize(spec["name"]), (spec["name"], {}))
        records = _source_records({**spec, "name": original_name}, cells)
        stats = Counter(rows=len(records), linkedRows=0, reviewRows=0, duplicateRows=0)
        for record in records:
            linked = _match(record, index)
            method = "registration_cpf_admission" if linked else ""
            if spec["name"] == "PERÍCIA" and not linked:
                key = (record["hint"]["number"], _normalize(record["name"]))
                dependencies = absence_sources.get(key, [])
                if key[0] and key[1] and len(dependencies) == 1 and dependencies[0] is not None:
                    linked, method = dependencies[0], "confirmed_afastados_source_dependency"
            if spec["name"] == "AFASTADOS":
                absence_sources[(record["hint"]["number"], _normalize(record["name"]))].append(linked)
            if linked:
                group_key = (linked["personKey"], linked["admission"])
                source_key = f"history:{linked['personKey']}:{linked['admission']}"
                stats["linkedRows"] += 1
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", _normalize(original_name)).strip("-")
                group_key = ("review", original_name, record["row"])
                source_key = f"history:review:{slug}:{record['row']}"
                stats["reviewRows"] += 1
            item = grouped.get(group_key)
            if item is None:
                title = (f"Histórico da matrícula {linked['branch'].zfill(2)}-{record['hint']['number']} · empresa {linked['employer']}" if linked else
                         f"{spec['title']} · registro em revisão ({record['row']})")
                notes = [_NOTE]
                if not linked:
                    notes.append("Vínculo em revisão: faltam dados ou uma correspondência única de matrícula, CPF e admissão. Nenhuma associação foi presumida.")
                item = {"sourceKey": source_key, "title": title, "category": "Histórico de pessoas",
                        "sourceSheet": original_name, "sourceRange": record["section"]["sourceRange"],
                        "sensitive": True, "personKey": linked["personKey"] if linked else "",
                        "payload": {"historical": True, "affectsCurrentStatus": False,
                                    "identity": {"registration": record["hint"]["number"],
                                                 "admissionDate": linked["admission"] if linked else record["admission"],
                                                 "matchStatus": "CONFIRMED" if linked else "REVIEW"},
                                    "sections": [], "notes": notes}}
                grouped[group_key] = item
                histories.append(item)
            section = {**record["section"], "identityMatch": method or "REVIEW"}
            equivalent = next((saved for saved in item["payload"]["sections"]
                               if saved["sourceSheet"] == section["sourceSheet"]
                               and saved["columns"] == section["columns"] and saved["rows"] == section["rows"]), None)
            if equivalent:
                equivalent.setdefault("additionalSourceRanges", []).append(section["sourceRange"])
                stats["duplicateRows"] += 1
            else:
                item["payload"]["sections"].append(section)
        sheet_stats[original_name] = dict(stats)
        totals.update(stats)
    return {"items": histories,
            "summary": {"historicalOnly": True, "items": len(histories),
                        "linkedItems": sum(bool(item["personKey"]) for item in histories),
                        "reviewItems": sum(not item["personKey"] for item in histories),
                        **dict(totals), "bySheet": sheet_stats}}
