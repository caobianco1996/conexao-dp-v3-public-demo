"""Consulta paginada das tabelas de pessoal importadas da V2."""

from __future__ import annotations

import csv
import io
from datetime import date
from urllib.parse import urlencode


DATASETS = {
    "active": ("Ativos", "in_active_export", None),
    "admissions": ("Admitidos", "in_admitted_export", "admission_date"),
    "terminations": ("Desligados", "in_terminated_export", "termination_date"),
}
YEARS = ("todos", "2025", "2026")
FIELDS = ("registration", "name", "company", "department", "job_title", "contract_type",
          "admission_date", "termination_date", "termination_reason_code", "termination_reason")
HEADERS = ("Código", "Nome", "Cliente", "Departamento", "Função", "Tipo de contrato",
           "Admissão", "Desligamento", "Código do motivo", "Descrição do motivo")


def selection(parameters: dict[str, list[str]]) -> tuple[str, str, str, int]:
    kind = (parameters.get("tipo") or ["active"])[0]
    if kind not in DATASETS:
        kind = "active"
    year = (parameters.get("ano") or ["2026"])[0]
    if year not in YEARS:
        year = "2026"
    query = (parameters.get("q") or [""])[0].strip()[:100]
    raw_page = (parameters.get("page") or ["1"])[0]
    page = max(1, min(10000, int(raw_page))) if raw_page.isdigit() else 1
    return kind, year, query, page


def conditions(kind: str, year: str, query: str) -> tuple[str, list[str]]:
    _, flag, dated_field = DATASETS[kind]
    clauses = [f"{flag}=1"]
    values: list[str] = []
    if year != "todos" and dated_field:
        clauses.append(f"{dated_field}>=? AND {dated_field}<?")
        values.extend((f"{year}-01-01", f"{int(year)+1}-01-01"))
    if query:
        clauses.append("(" + " OR ".join(f"{field} LIKE ?" for field in
                       ("registration", "name", "company", "department", "job_title", "contract_type", "termination_reason")) + ")")
        values.extend([f"%{query}%"] * 7)
    return " WHERE " + " AND ".join(clauses), values


def _date(value: str | None) -> str:
    return date.fromisoformat(value).strftime("%d/%m/%Y") if value else "—"


def render(db, parameters: dict[str, list[str]], escape) -> str:
    kind, year, query, page = selection(parameters)
    clause, values = conditions(kind, year, query)
    count = db.execute("SELECT COUNT(*) FROM v2_employments" + clause, values).fetchone()[0]
    rows = db.execute("SELECT " + ",".join(FIELDS) + " FROM v2_employments" + clause +
                      " ORDER BY name,registration LIMIT 20 OFFSET ?", (*values, (page - 1) * 20)).fetchall()
    tabs = "".join(f'<a class="data-tab{" selected" if kind == key else ""}" href="/analytics/tabelas?{urlencode({"tipo": key,"ano": year})}">{escape(label)}</a>'
                   for key, (label, _, _) in DATASETS.items())
    years = "".join(f'<option value="{item}"{" selected" if item == year else ""}>{"Todos os anos" if item == "todos" else item}</option>' for item in YEARS)
    cells = "".join("<tr>" + "".join(f"<td>{escape(_date(row[field]) if field.endswith('_date') else row[field] or '—')}</td>" for field in FIELDS) + "</tr>" for row in rows)
    base = {"tipo": kind, "ano": year, "q": query}
    previous = f'<a href="/analytics/tabelas?{urlencode({**base,"page":page-1})}">← Anterior</a>' if page > 1 else ""
    following = f'<a href="/analytics/tabelas?{urlencode({**base,"page":page+1})}">Próxima →</a>' if page * 20 < count else ""
    export = urlencode(base)
    return (f'<div class="data-tabs">{tabs}</div><form class="row data-filters" method="get">'
            f'<input type="hidden" name="tipo" value="{kind}"><label>Período<select name="ano">{years}</select></label>'
            f'<label>Buscar na tabela<input name="q" value="{escape(query)}" placeholder="Nome, código, empresa, função"></label>'
            f'<button>Aplicar</button></form><div class="data-table-head"><p>{count:,} registro(s) nesta seleção.</p>'
            f'<a class="export-link" href="/analytics/tabelas/exportar?{export}">Exportar CSV</a></div>'
            '<div class="table-wrap"><table><thead><tr>' + ''.join(f'<th>{escape(h)}</th>' for h in HEADERS) +
            f'</tr></thead><tbody>{cells or "<tr><td colspan=10>Nenhum registro nesta seleção.</td></tr>"}</tbody></table></div>'
            f'<nav class="data-pages" aria-label="Páginas">{previous}<span>Página {page} de {max(1,(count+19)//20)}</span>{following}</nav>')


def _csv_cell(value: str) -> str:
    """Keep imported text from becoming a formula when opened in a spreadsheet."""
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def export_csv(db, parameters: dict[str, list[str]]) -> bytes:
    kind, year, query, _ = selection(parameters)
    clause, values = conditions(kind, year, query)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_ALL)
    writer.writerow(HEADERS)
    for row in db.execute("SELECT " + ",".join(FIELDS) + " FROM v2_employments" + clause + " ORDER BY name,registration", values):
        writer.writerow([_csv_cell(_date(row[field]) if field.endswith("_date") else row[field] or "") for field in FIELDS])
    return ("\ufeff" + output.getvalue()).encode("utf-8")
