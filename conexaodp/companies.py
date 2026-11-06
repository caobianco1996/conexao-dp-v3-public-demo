"""Cadastro de empresas sobre o snapshot da V2 e complementos locais."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from urllib.parse import urlencode

from .registry import employment_activity, normalize, render_categories, sib_activity, snapshot_activity
from .sib_import import source_records


PROFILE_FIELDS = (
    ("legalName", "Razão social", 200), ("cnpj", "CNPJ", 18), ("postalCode", "CEP", 9),
    ("address", "Endereço", 300), ("addressNumber", "Número", 30),
    ("addressComplement", "Complemento", 200), ("neighborhood", "Bairro", 160),
    ("city", "Cidade", 160), ("state", "Estado (UF)", 2),
    ("payrollDay", "Dia de pagamento", 30), ("advanceDay", "Dia do adiantamento", 30),
    ("pointStart", "Início do fechamento do ponto", 2), ("pointEnd", "Fim do fechamento do ponto", 2),
)


def _identity(name: str, code: str = ""):
    match = re.match(r"^\s*(\d{3,8})\s+-\s+(.+)$", name)
    if match and not code:
        code, name = match.group(1), match.group(2)
    code = code.strip().removesuffix(".0")
    if code.upper() in ("PA_IMPORT", "PA_DEPARTMENT"):
        code = ""
    return ("code:" + code if code else "name:" + normalize(name)), name.strip(), code


def catalog(db, *, sib_records=None):
    counts = Counter()
    active = Counter()
    unknown = Counter()
    export_names = {}
    for row in db.execute("SELECT company,in_active_export,admission_date,termination_date FROM v2_employments"):
        identity, name, code = _identity(row["company"])
        counts[identity] += 1
        flag, _ = snapshot_activity(row["in_active_export"], row["admission_date"], row["termination_date"])
        active[identity] += flag is True
        unknown[identity] += flag is None
        export_names[identity] = (name, code)
    groups = {}
    profile_ids = {str(json.loads(row[0]).get("client_id")) for row in
                   db.execute("SELECT payload FROM v2_source_rows WHERE table_name='client_company_profiles'")}
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='clients'"):
        payload = json.loads(row["payload"])
        name = str(payload.get("name") or "")
        identity, _, code = _identity(name, str(payload.get("code") or ""))
        if identity in groups and groups[identity]["key"][3:] in profile_ids and row["source_id"] not in profile_ids:
            continue
        groups[identity] = {"key": "v2:" + row["source_id"], "name": name, "code": code,
                            "active": bool(active[identity]), "headcount": active[identity],
                            "total": counts[identity], "unknownHeadcount": unknown[identity], "source": "Cadastro V2"}
    for row in db.execute("SELECT id,code,name,active FROM local_clients"):
        identity, _, code = _identity(row["name"], row["code"])
        groups[identity] = {"key": f'local:{row["id"]}', "name": row["name"], "code": code,
                            "active": bool(active[identity]), "headcount": active[identity],
                            "total": counts[identity], "unknownHeadcount": unknown[identity], "source": "Cadastro local"}
    known_names = {normalize(item["name"]) for item in groups.values() if item["code"]}
    for identity, (name, code) in export_names.items():
        if identity not in groups and not (not code and normalize(name) in known_names):
            groups[identity] = {"key": "csv:" + hashlib.sha256(identity.encode()).hexdigest(),
                                "name": name, "code": code, "active": bool(active[identity]),
                                "headcount": active[identity], "total": counts[identity], "unknownHeadcount": unknown[identity], "source": "Apenas planilha"}
    sib_totals, sib_active, sib_unknown = Counter(), Counter(), Counter()
    for _, data in (sib_records["employments"] if sib_records is not None else source_records(db, "employments")):
        company_id = str(data.get("companyId") or "")
        flag, _ = sib_activity(data)
        sib_totals[company_id] += 1
        sib_active[company_id] += flag is True
        sib_unknown[company_id] += flag is None
    for source_id, data in (sib_records["companies"] if sib_records is not None else source_records(db, "companies")):
        groups["sib:" + source_id] = {"key": "sib:" + source_id, "name": data.get("name") or f"Empresa SIB {source_id}",
            "code": str(data.get("code") or source_id), "active": None, "headcount": sib_active[source_id],
            "unknownHeadcount": sib_unknown[source_id], "headcountKnown": not sib_unknown[source_id] and bool(sib_totals[source_id]),
            "total": sib_totals[source_id], "source": "SIB"}
    for row in db.execute("SELECT company,admission_date,termination_date FROM registry_employments"):
        identity, name, code = _identity(row["company"])
        # Associa contagens somente quando há uma empresa identificável sem ambiguidade.
        matches = [item for item in groups.values() if
                   (code and item["code"] == code) or
                   normalize(_identity(item["name"], item["code"])[1]) == normalize(name)]
        if len(matches) == 1:
            company = matches[0]
        else:
            key = "local-affiliation:" + hashlib.sha256(identity.encode()).hexdigest()
            company = groups.setdefault(key, {"key": key, "name": name, "code": code, "active": None,
                "headcount": 0, "total": 0, "unknownHeadcount": 0, "source": "Vínculo local"})
        flag, _ = employment_activity(row["admission_date"], row["termination_date"])
        company["total"] += 1
        company["headcount"] += flag is True
        company["unknownHeadcount"] += flag is None
    items = list(groups.values())
    for item in items:
        item["headcountKnown"] = not item.get("unknownHeadcount") and bool(item["total"])
        item["employeeGroup"] = "with-active" if item["headcount"] else "unknown" if item.get("unknownHeadcount") or not item["total"] else "without-active"
        item["active"] = True if item["employeeGroup"] == "with-active" else False if item["employeeGroup"] == "without-active" else None
        item["headcountLabel"] = f'{item["headcount"]} ativo(s) · {item["total"]} vínculo(s) consultado(s)'
        if item.get("unknownHeadcount"):
            item["headcountLabel"] += f' · {item["unknownHeadcount"]} a conferir'
    items.sort(key=lambda item: normalize(item["name"]))
    return items


def profile(db, client_key: str):
    local = db.execute("SELECT profile_json FROM local_company_profiles WHERE client_key=?", (client_key,)).fetchone()
    if local and not client_key.startswith("sib:"):
        return json.loads(local[0]), "Complemento local"
    if client_key.startswith("sib:"):
        row = db.execute("SELECT data_json,raw_json FROM local_sib_records WHERE dataset='companies' AND source_id=?", (client_key[4:],)).fetchone()
        if row:
            data = json.loads(row["data_json"])
            base = {**data, "legalName": data.get("name") or "", "originalSib": json.loads(row["raw_json"])}
            if local:
                overlay = json.loads(local[0])
                base.update({key: value for key, value in overlay.items() if key not in ("originalSib", "sourceId", "code", "name")})
            return base, "Ficha SIB e complemento local" if local else "Ficha SIB"
    if client_key.startswith("v2:"):
        row = db.execute("SELECT payload FROM v2_source_rows WHERE table_name='client_company_profiles' AND json_extract(payload,'$.client_id')=?", (client_key[3:],)).fetchone()
        if row:
            data = json.loads(row[0])
            raw = data.get("profile_json") or "{}"
            if isinstance(raw, str):
                try:
                    return json.loads(raw), "Ficha V2"
                except json.JSONDecodeError:
                    return {"rawProfile": raw}, "Ficha V2 truncada na extração"
            return raw, "Ficha V2"
    return {}, "Sem ficha"


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten(child, f"{prefix} · {key}" if prefix else key)
    elif isinstance(value, list):
        for index, child in enumerate(value, 1):
            yield from _flatten(child, f"{prefix} {index}")
    else:
        yield prefix, "Sim" if value is True else "Não" if value is False else "—" if value is None else str(value)


def render(db, parameters, escape, csrf, can_manage):
    items = catalog(db)
    query = (parameters.get("q") or [""])[0].strip()[:100]
    selected = (parameters.get("id") or [""])[0][:150]
    group = (parameters.get("group") or ["all"])[0]
    group_labels = {"all": "Todas", "with-active": "Com funcionários ativos", "without-active": "Sem funcionários ativos", "unknown": "Situação a conferir"}
    if group not in group_labels:
        group = "all"
    searched = [item for item in items if normalize(query) in normalize(item["name"] + " " + item["code"])]
    subset = [item for item in searched if group == "all" or item["employeeGroup"] == group]
    total_pages = max(1, (len(subset) + 49) // 50)
    requested_page = (parameters.get("page") or ["1"])[0]
    page = max(1, min(int(requested_page), total_pages)) if requested_page.isdigit() and len(requested_page) <= 8 else 1
    visible = subset[(page - 1) * 50:page * 50]
    counts = Counter(item["employeeGroup"] for item in searched)
    group_links = ''.join(
        f'<a class="registry-group-link{" selected" if value == group else ""}" href="/cadastros/empresas?{escape(urlencode({"q": query, "group": value}))}">{escape(label)} <strong>{len(searched) if value == "all" else counts[value]}</strong></a>'
        for value, label in group_labels.items())
    entries = "".join(f'<a class="registry-item registry-record-card{" selected" if item["key"]==selected else ""}" href="/cadastros/empresas?{escape(urlencode({"q":query,"group":group,"page":page,"id":item["key"]}))}"><div class="registry-card-title"><span class="registry-code">{escape(item["code"] or "Sem código")}</span><strong>{escape(item["name"])}</strong></div><span class="registry-activity registry-activity-{item["employeeGroup"]}">{escape(group_labels[item["employeeGroup"]])}</span><span>{escape(item["headcountLabel"])} · {escape(item["source"])}</span></a>' for item in visible)
    pagination = '<nav class="registry-pagination" aria-label="Páginas de empresas">'
    if page > 1:
        pagination += f'<a href="/cadastros/empresas?{escape(urlencode({"q":query,"group":group,"page":page-1}))}">Anterior</a>'
    pagination += f'<span>Página {page} de {total_pages} · {len(subset)} registro(s)</span>'
    if page < total_pages:
        pagination += f'<a href="/cadastros/empresas?{escape(urlencode({"q":query,"group":group,"page":page+1}))}">Próxima</a>'
    pagination += '</nav>'
    chosen = next((item for item in items if item["key"] == selected), None)
    detail = '<p>Selecione uma empresa à esquerda.</p>'
    if chosen:
        data, source = profile(db, selected)
        situation = group_labels[chosen["employeeGroup"]]
        detail = (f'<h3>Identificação da empresa</h3><dl class="registry-dl"><dt>Código</dt><dd>{escape(chosen["code"] or "—")}</dd><dt>Empresa</dt><dd>{escape(chosen["name"])}</dd><dt>Grupo por funcionários</dt><dd>{situation}</dd><dt>Vínculos ativos</dt><dd>{chosen["headcount"]}</dd><dt>Vínculos trazidos</dt><dd>{chosen["total"]}</dd><dt>Situação a conferir</dt><dd>{chosen.get("unknownHeadcount", 0)}</dd><dt>Fonte</dt><dd>{escape(chosen["source"])}</dd><dt>Ficha</dt><dd>{escape(source)}</dd></dl>')
        detail += '<p class="registry-criterion">A separação considera os vínculos já trazidos. No SIB, ativo significa admissão já ocorrida e desligamento ausente ou futuro; datas insuficientes ficam em conferência.</p>'
        detail += f'<a class="registry-related-link" href="/cadastros/vinculos?{escape(urlencode({"q": chosen["name"]}))}">Consultar vínculos desta empresa</a>'
        if data:
            fields = {key: value for key, value in data.items() if key != "originalSib"}
            if can_manage and isinstance(data.get("originalSib"), dict):
                fields = {**data["originalSib"], **fields}
            detail += render_categories(fields, escape, company=True)
        if can_manage:
            fields = ''.join(f'<label>{escape(label)}<input name="{key}" value="{escape(data.get(key, ""))}" maxlength="{limit}"></label>' for key,label,limit in PROFILE_FIELDS)
            detail += (f'<details><summary>Complementar ficha local</summary><p>Os campos não exibidos nesta edição são preservados. A ficha original da V2 permanece intacta.</p><form class="fields" method="post" action="/cadastros/empresas/ficha"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="client_key" value="{escape(selected)}">{fields}<button>Salvar ficha</button></form></details>')
    create = (f'<details class="card"><summary>Nova empresa</summary><form class="fields" method="post" action="/cadastros/empresas"><input type="hidden" name="csrf" value="{escape(csrf)}"><label>Nome<input name="name" maxlength="200" required></label><label>Código<input name="code" maxlength="40"></label><button>Cadastrar empresa</button></form></details>') if can_manage else ""
    return (f'<p>{len(items)} empresa(s) entre os cadastros V2, locais, SIB e nomes presentes nas planilhas.</p>{create}'
            f'<form class="row" method="get"><input type="hidden" name="group" value="{escape(group)}"><label>Buscar<input name="q" value="{escape(query)}" placeholder="Nome ou código"></label><button>Filtrar</button></form>'
            f'<nav class="registry-company-groups" aria-label="Empresas por funcionários">{group_links}</nav>'
            '<p class="registry-criterion">Os grupos refletem os vínculos consultados e são atualizados a cada lote. A consulta por matrícula encontra as empresas dos funcionários retornados.</p>'
            f'<div class="registry-layout"><section class="card"><h2>{escape(group_labels[group])} · {len(subset)}</h2><div class="registry-items registry-record-cards">{entries or "<p>Nenhum resultado.</p>"}</div>{pagination}</section><section class="card"><h2>Cadastro e operação</h2>{detail}</section></div>')
