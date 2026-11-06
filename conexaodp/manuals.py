"""Read-only catalogue for the verified RHiD and Secullum manual sources."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "referencia_v2_site" / "server-data"
PAGE_SIZE = 30
PROCESS_DUPLICATES = {
    "conexao-dp-primary:manual:SECULLUM:DP-025",
    "conexao-dp-primary:manual:SECULLUM:DP-026",
    "conexao-dp-primary:manual:SECULLUM:DP-028",
}


def _normalized(value: object) -> str:
    text = str(value or "").casefold()
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return " ".join(text.split())


@lru_cache(maxsize=1)
def _catalogue() -> tuple[dict, ...]:
    """Read the complete source JSON, never the truncated D1 inspection mirror."""
    rhid = json.loads((DATA_DIR / "rhid-dataset.json").read_text(encoding="utf-8"))["procedures"]
    pdf_guides = json.loads((DATA_DIR / "secullum-zip-manuals.json").read_text(encoding="utf-8"))
    help_guides = json.loads((DATA_DIR / "secullum-help-guides.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for system, records in (("RHiD", rhid), ("Secullum", pdf_guides), ("Secullum", help_guides)):
        for source in records:
            record = dict(source)
            record["id"] = f"conexao-dp-primary:manual:{record['code']}"
            record["system"] = system
            record["category"] = str(record.get("category") or "Sem categoria")
            record["sourceLocator"] = str(record.get("sourceLocator") or record.get("source") or "")
            record["validFrom"] = record.get("valid_from") or record.get("validFrom")
            record["updatedAt"] = record.get("updated_at") or record.get("reviewedAt") or ""
            record["version"] = record.get("version") or 1
            record["_search"] = _normalized(" ".join((
                str(record.get("title") or ""), str(record.get("category") or ""), system,
                str(record.get("description") or ""), str(record.get("content") or ""),
                str(record.get("whenToUse") or ""), " ".join(map(str, record.get("keywords") or [])),
                " ".join(map(str, record.get("synonyms") or [])),
            )))
            rows.append(record)
    return tuple(rows)


def list_manuals(params, user: dict) -> dict:
    q = _normalized((params.get("q") or [""])[0])
    system = (params.get("system") or [""])[0]
    category = (params.get("category") or [""])[0]
    status = (params.get("status") or [""])[0]
    try:
        page = max(1, min(int((params.get("page") or ["1"])[0]), 10000))
    except (TypeError, ValueError):
        page = 1
    page_size = PAGE_SIZE
    privileged = user.get("role") in ("ADMIN", "GESTOR")
    rows = [row for row in _catalogue() if row["id"] not in PROCESS_DUPLICATES]
    if not privileged:
        rows = [row for row in rows if str(row.get("status") or "PUBLISHED").upper() == "PUBLISHED"]
    systems = sorted({row["system"] for row in rows}, key=lambda v: (v != "RHiD", _normalized(v)))
    categories = sorted({row["category"] for row in rows}, key=_normalized)
    if system:
        rows = [row for row in rows if row["system"] == system]
    if category:
        rows = [row for row in rows if row["category"] == category]
    if status and privileged:
        rows = [row for row in rows if str(row.get("status") or "PUBLISHED").upper() == status.upper()]
    if q:
        rows = [row for row in rows if q in row["_search"]]
    rows.sort(key=lambda row: (_normalized(row["title"]), row["id"]))
    total = len(rows)
    start = (page - 1) * page_size
    items = [{key: row.get(key) for key in (
        "id", "title", "category", "system", "status", "version", "sourceLocator",
        "pageStart", "pageEnd", "updatedAt", "sourceFilename", "pageCount", "kind",
    )} for row in rows[start:start + page_size]]
    return {
        "items": items, "total": total, "page": page, "pageSize": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "systems": systems, "categories": categories, "canSeeDrafts": privileged,
        "excludedProcessRecords": len(PROCESS_DUPLICATES),
    }


def get_manual(manual_id: str, user: dict) -> dict | None:
    privileged = user.get("role") in ("ADMIN", "GESTOR")
    for row in _catalogue():
        if row["id"] != manual_id or row["id"] in PROCESS_DUPLICATES:
            continue
        if not privileged and str(row.get("status") or "PUBLISHED").upper() != "PUBLISHED":
            return None
        result = {key: value for key, value in row.items() if key != "_search"}
        return result
    return None
