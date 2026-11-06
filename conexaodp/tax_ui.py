"""Responsive local tax tables, simulation and audit screens."""
from __future__ import annotations

import json
from datetime import date
from html import escape

from .tax_tables import INSS_2026, IRRF_2026, parameters_for_version, resolve, simulation_snapshot_signature


VIEWS = [
    ("overview", "Visão Geral"), ("inss", "Tabelas INSS"),
    ("irrf", "Tabelas IRRF"), ("simulator", "Simulador"),
    ("candidates", "Candidatas e validação"), ("history", "Histórico de versões"),
    ("audit", "Auditoria"),
]
STATUS = {
    "COLLECTED": "Coletada", "INVALID_CONTENT": "Conteúdo inválido",
    "VALIDATED": "Validada", "PENDING_REVIEW": "Aguardando revisão",
    "APPROVED": "Aprovada", "PUBLISHED": "Publicada",
    "REJECTED": "Rejeitada", "SUPERSEDED": "Substituída", "DRAFT": "Candidata",
}


def _e(value):
    return escape(str(value if value is not None else ""), quote=True)


def _q(query, name, default=""):
    value = query.get(name, [default])
    return str(value[0] if isinstance(value, list) else value or default)


def _csrf(user):
    return f'<input type="hidden" name="csrf" value="{_e(user["csrf_token"])}">'


def _money(value):
    try:
        from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        whole, cents = f"{amount:.2f}".split(".")
        whole = f"{int(whole):,}".replace(",", ".")
        return f"R$ {whole},{cents}"
    except (TypeError, ValueError, InvalidOperation):
        return "—"


def _status(value):
    return STATUS.get(value, value or "—")


def _tabs(view):
    return '<nav class="tax-nav" aria-label="Seções de tributos">' + "".join(
        f'<a href="/rotinas/tributos?view={key}"{" aria-current=page" if key == view else ""}>{_e(label)}</a>'
        for key, label in VIEWS if key != "audit" or view == "audit"
    ) + "</nav>"


def _table_name(row):
    if not row:
        return "Sem tabela publicada para esta referência"
    return f'{row["kind"]} · empregado · v{row["version"]} · {row["valid_from"]}'


def _meta_source(row):
    if not row:
        return "Fonte não disponível"
    url = row["source_url"] or ""
    link = f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer">Abrir fonte oficial</a>' if url else "Fonte sem URL registrada"
    return (f'<div class="tax-source"><strong>{_e(row["source_title"] or row["source"])}</strong>'
            f'<span>{_e(row["source_agency"] or "Órgão não informado")} · conferida em {_e(row["source_accessed_at"] or "data não informada")}</span>'
            f'<span>Hash dos parâmetros: <code>{_e((row["content_hash"] or "")[:20])}</code> · {link}</span></div>')


def _band_rows(kind, parameters):
    bands = parameters.get("bands", []) if isinstance(parameters, dict) else []
    if kind == "INSS":
        rows = "".join(f'<tr><td data-label="De">{_money(band.get("from"))}</td><td data-label="Até">{_money(band.get("to"))}</td><td data-label="Alíquota marginal">{_e(band.get("rate"))}%</td></tr>' for band in bands)
        return f'<div class="tax-table-scroll"><table class="tax-table tax-bracket-table"><thead><tr><th scope="col">De</th><th scope="col">Até</th><th scope="col">Alíquota marginal</th></tr></thead><tbody>{rows}</tbody></table></div>'
    rows = "".join(f'<tr><td data-label="Base de cálculo">{_money(band.get("from"))}</td><td data-label="Até">{_money(band.get("to")) if band.get("to") is not None else "Sem limite"}</td><td data-label="Alíquota">{_e(band.get("rate"))}%</td><td data-label="Parcela a deduzir">{_money(band.get("deduction"))}</td></tr>' for band in bands)
    extras = (f'<p>Dependente: {_money(parameters.get("dependentDeduction"))} · Desconto simplificado mensal: {_money(parameters.get("simplifiedDiscount"))}</p>'
        '<h3>Redução de imposto</h3><div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Rendimentos tributáveis</th><th>Regra</th><th>Parâmetros</th></tr></thead><tbody>' + "".join(
            f'<tr><td>{_money(item.get("from"))} a {_money(item.get("to"))}</td><td>{"Zerar até o limite do imposto" if item.get("kind")=="ZERO_TAX" else "Fórmula linear"}</td><td>{_e(item.get("formula") or (f"min(imposto, {_money(item.get('maximum'))})" if item.get("kind")=="ZERO_TAX" else f"{item.get('constant')} − {item.get('coefficient')} × rendimento tributável"))}</td></tr>'
            for item in parameters.get("reductions", [])) + '</tbody></table></div>')
    return f'<div class="tax-table-scroll"><table class="tax-table tax-bracket-table"><thead><tr><th scope="col">Base de cálculo</th><th scope="col">Até</th><th scope="col">Alíquota</th><th scope="col">Parcela a deduzir</th></tr></thead><tbody>{rows}</tbody></table></div>{extras}'


def _version_detail(db, user, row):
    if not row:
        return '<section class="card tax-empty">A versão selecionada não está disponível.</section>'
    try:
        params = json.loads(row["parameters_json"])
        validation = json.loads(row["validation_json"] or "{}")
    except (TypeError, ValueError):
        params, validation = {}, {}
    status_label = _status(row["workflow_state"] or row["status"])
    history_rows = db.execute("SELECT h.*,u.name FROM local_tax_history h JOIN users u ON u.id=h.actor_id WHERE tax_version_id=? ORDER BY h.id DESC", (row["id"],)).fetchall()
    created = db.execute("SELECT name FROM users WHERE id=?", (row["created_by"],)).fetchone()
    approved = db.execute("SELECT name FROM users WHERE id=?", (row["approved_by"],)).fetchone() if row["approved_by"] else None
    published = db.execute("SELECT name FROM users WHERE id=?", (row["published_by"],)).fetchone() if row["published_by"] else None
    history = "".join(f'<li><strong>{_e(STATUS.get(item["action"],item["action"]))}</strong><span>{_e(item["name"])} · {_e(item["created_at"])}</span><p>{_e(item["detail"])}</p></li>' for item in history_rows)
    actions = ""
    if user["role"] == "ADMIN":
        if row["status"] == "DRAFT" and row["workflow_state"] != "VALIDATED":
            actions += f'<form method="post" action="/rotinas/tributos/validar">{_csrf(user)}<input type="hidden" name="id" value="{row["id"]}"><button class="tax-button">Validar candidata</button></form>'
            actions += f'<a class="tax-button tax-button-secondary" href="/rotinas/tributos?view=candidates&id={row["id"]}&edit=1">Editar candidata</a>'
        if row["status"] == "DRAFT" and row["workflow_state"] == "VALIDATED":
            actions += f'<form method="post" action="/rotinas/tributos/acao">{_csrf(user)}<input type="hidden" name="id" value="{row["id"]}"><input type="hidden" name="action" value="approve"><button class="tax-button">Aprovar versão</button></form>'
        if row["status"] == "APPROVED":
            actions += f'<form method="post" action="/rotinas/tributos/acao">{_csrf(user)}<input type="hidden" name="id" value="{row["id"]}"><input type="hidden" name="action" value="publish"><button class="tax-button">Publicar internamente</button></form>'
        if row["status"] in ("DRAFT", "APPROVED"):
            actions += f'<form method="post" action="/rotinas/tributos/acao" class="tax-reject">{_csrf(user)}<input type="hidden" name="id" value="{row["id"]}"><input type="hidden" name="action" value="reject"><label>Motivo da rejeição<input name="reason" required minlength="3" maxlength="500"></label><button class="tax-button tax-button-danger">Rejeitar</button></form>'
    checks = "".join(f'<li class="{"is-ok" if item.get("ok") else "is-error"}">{_e(item.get("name"))}: {_e("Concluído" if item.get("ok") else "Revisar")}</li>' for item in validation.get("checks", []))
    errors = "".join(f'<li>{_e(error)}</li>' for error in validation.get("errors", []))
    report = f'<section class="tax-report"><h3>Relatório de validação · {_e(status_label)}</h3><ul>{checks}</ul>{f"<ul class=tax-errors>{errors}</ul>" if errors else ""}</section>' if validation else '<p class="tax-muted">A validação estrutural ainda não foi executada.</p>'
    display_params = parameters_for_version(row)
    legacy_note = ('<p class="tax-confirm">Snapshot original V2 preservado. As regras técnicas de elegibilidade mensal e arredondamento foram normalizadas apenas em memória para os cálculos locais.</p>' if row["parser_version"] == "legacy-v2-preserved-v3-compatible" else "")
    provenance = (f'<p class="tax-provenance">Criada por {_e(created["name"] if created else "—")} · aprovada por {_e(approved["name"] if approved else "—")} · publicada por {_e(published["name"] if published else "—")}</p>')
    return (f'<section class="card tax-version-detail"><div class="tax-section-heading"><div><span class="tax-kicker">{_e(row["kind"])} · {_e(row["category"])}</span>'
            f'<h2>{_e(row["competence"])} · revisão {row["version"]}</h2></div><span class="tax-status">{_e(status_label)}</span></div>'
            f'<p>Vigência {_e(row["valid_from"])} a {_e(row["valid_to"] or "sem encerramento previsto")} · Tipo {_e(row["income_type"])}</p>'
            f'{_meta_source(row)}{provenance}{legacy_note}<p><strong>Motivo:</strong> {_e(row["justification"] or "—")}</p>{_band_rows(row["kind"], display_params)}{report}{_candidate_diff(db, row, display_params)}'
            f'<div class="tax-actions">{actions}</div><details class="tax-json"><summary>Parâmetros e hash versionados</summary><pre>{_e(json.dumps(params,ensure_ascii=False,indent=2))}</pre><code>SHA-256 dos parâmetros: {_e(row["content_hash"])}</code></details>'
            f'<details class="tax-history"><summary>Linha do tempo ({len(history_rows)} eventos)</summary><ol>{history or "<li>Sem eventos registrados.</li>"}</ol></details></section>')


def _candidate_diff(db, candidate, parameters):
    if candidate["status"] == "PUBLISHED":
        return ""
    current = db.execute("""SELECT * FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND status IN ('PUBLISHED','SUPERSEDED') AND published_at IS NOT NULL AND valid_from<=?
        ORDER BY valid_from DESC,version DESC LIMIT 1""", (candidate["kind"],candidate["category"],candidate["income_type"],candidate["valid_from"])).fetchone()
    if not current or current["id"] == candidate["id"]:
        return '<section class="tax-diff"><h3>Comparação com a revisão publicada</h3><p>Não há uma versão anterior para esta referência.</p></section>'
    try:
        before = parameters_for_version(current)
    except (TypeError, ValueError):
        before = {}
    changes = []
    def compare(left, right, path=""):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                compare(left.get(key, "(ausente)"), right.get(key, "(ausente)"), f"{path}.{key}".strip("."))
        elif isinstance(left, list) and isinstance(right, list):
            if left != right:
                changes.append((path or "Parâmetros", json.dumps(left,ensure_ascii=False), json.dumps(right,ensure_ascii=False)))
        elif left != right:
            changes.append((path or "Parâmetros", str(left), str(right)))
    compare(before, parameters)
    rows = "".join(f'<tr><th>{_e(path)}</th><td>{_e(old)}</td><td>{_e(new)}</td></tr>' for path,old,new in changes[:60])
    return (f'<section class="tax-diff"><h3>Comparação · atual v{current["version"]} × candidata v{candidate["version"]}</h3>'
            f'<p>Vigência da versão atual: {_e(current["valid_from"])} a {_e(current["valid_to"] or "aberta")}.</p>'
            f'<div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Parâmetro</th><th>Versão atual</th><th>Candidata</th></tr></thead><tbody>{rows or "<tr><td colspan=3>Nenhuma diferença de conteúdo.</td></tr>"}</tbody></table></div></section>')


def _edit_form(user, row, params):
    return (f'<details class="tax-edit"><summary>Editar candidata</summary><form method="post" action="/rotinas/tributos/editar" class="tax-form">{_csrf(user)}'
        f'<input type="hidden" name="id" value="{row["id"]}"><label>Vigência inicial<input type="date" name="valid_from" value="{_e(row["valid_from"])}" required></label>'
        f'<label>Vigência final (opcional)<input type="date" name="valid_to" value="{_e(row["valid_to"] or "")}"></label>'
        f'<label>Categoria<select name="category"><option value="EMPLOYEE" selected>Empregado, doméstico ou avulso</option></select></label>'
        f'<label>Natureza<select name="income_type"><option value="{_e(row["income_type"])}" selected>{_e(row["income_type"])}</option></select></label>'
        f'<label>Título da fonte<input name="source_title" value="{_e(row["source_title"])}" required></label><label>Órgão<input name="source_agency" value="{_e(row["source_agency"])}" required></label>'
        f'<label class="tax-wide">URL HTTPS<input type="url" name="source_url" value="{_e(row["source_url"])}" required></label>'
        f'<label>Data de conferência<input type="date" name="source_accessed_at" value="{_e((row["source_accessed_at"] or "")[:10])}"></label>'
        f'<label class="tax-wide">Justificativa da edição<textarea name="justification" required minlength="8">{_e(row["justification"])}</textarea></label>'
        f'<label class="tax-wide">Parâmetros JSON<textarea name="parameters_json" rows="18" required>{_e(json.dumps(params,ensure_ascii=False,indent=2))}</textarea></label>'
        '<button class="tax-button">Salvar e exigir nova validação</button></form></details>')


def _candidate_form(user, kind, preset):
    title, agency, url = ("Tabela progressiva anual/mensal · revise para a referência" if kind == "INSS" else "Tabela de incidência · revise para a referência", "Órgão oficial", "")
    if kind == "INSS":
        title, agency, url = "Tabela de contribuição mensal · empregado, doméstico e avulso", "Instituto Nacional do Seguro Social", "https://www.gov.br/inss/pt-br/direitos-e-deveres/inscricao-e-contribuicao/tabela-de-contribuicao-mensal"
        income = "EMPLOYEE_MONTHLY"
    else:
        title, agency, url = "Tabela IRRF mensal · revise a vigência e a redução", "Receita Federal do Brasil", "https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda/tabelas/2026"
        income = "SALARY_MONTHLY"
    return (f'<details class="tax-create"><summary>Cadastrar candidata {kind}</summary><p>Este formulário cria somente uma candidata. Ela não alimenta cálculos antes de validação, aprovação e publicação.</p>'
        f'<form method="post" action="/rotinas/tributos/candidata" class="tax-form">{_csrf(user)}<input type="hidden" name="kind" value="{kind}">'
        f'<input type="hidden" name="category" value="EMPLOYEE"><input type="hidden" name="income_type" value="{income}">'
        '<label>Vigência inicial<input type="date" name="valid_from" required></label><label>Vigência final (opcional)<input type="date" name="valid_to"></label>'
        f'<label class="tax-wide">Título da fonte<input name="source_title" value="{_e(title)}" required maxlength="500"></label>'
        f'<label>Órgão responsável<input name="source_agency" value="{_e(agency)}" required maxlength="250"></label>'
        f'<label>Data de conferência<input type="date" name="source_accessed_at" value="{date.today().isoformat()}" required></label>'
        f'<label class="tax-wide">URL HTTPS da fonte<input type="url" name="source_url" value="{_e(url)}" required maxlength="1000"></label>'
        '<label class="tax-wide">Justificativa, ato normativo ou motivo da revisão<textarea name="justification" required minlength="8" maxlength="2000" placeholder="Ex.: Portaria/lei, vigência confirmada e motivo desta revisão"></textarea></label>'
        f'<label class="tax-wide">Parâmetros completos em JSON<textarea name="parameters_json" rows="15" required spellcheck="false">{_e(json.dumps(preset,ensure_ascii=False,indent=2))}</textarea></label>'
        '<p class="tax-help tax-wide">Os valores preenchidos são uma base de referência local, não uma nova vigência confirmada. Confira cada parâmetro e a fonte antes de validar.</p>'
        '<button class="tax-button">Salvar candidata para validação</button></form></details>')


def _version_list(db, kind=None, status=None, valid_from="", valid_to=""):
    clauses, args = [], []
    if kind:
        clauses.append("kind=?"); args.append(kind)
    if status:
        clauses.append("workflow_state=?"); args.append(status)
    if valid_from:
        clauses.append("valid_from>=?"); args.append(valid_from)
    if valid_to:
        clauses.append("valid_from<=?"); args.append(valid_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return db.execute("SELECT * FROM local_tax_versions" + where + " ORDER BY valid_from DESC,kind,version DESC,id DESC LIMIT 300", args).fetchall()


def _versions_table(rows, include_author=False):
    author = '<th>Autor</th>' if include_author else ""
    entries = []
    for row in rows:
        author_cell = f'<td>{_e(row["creator_name"] or "—")}</td>' if include_author else ""
        entries.append(f'<tr><td>{_e(row["kind"])}</td><td>{_e(row["category"])}</td><td>{_e(row["valid_from"])} a {_e(row["valid_to"] or "aberta")}</td><td>v{row["version"]}</td><td><span class="tax-status">{_e(_status(row["workflow_state"]))}</span></td>{author_cell}<td><a href="/rotinas/tributos?view={"inss" if row["kind"]=="INSS" else "irrf"}&id={row["id"]}">Abrir</a></td></tr>')
    colspan = 6 if include_author else 5
    return f'<div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Tipo</th><th>Categoria</th><th>Vigência</th><th>Revisão</th><th>Estado</th>{author}<th></th></tr></thead><tbody>{"".join(entries) or f"<tr><td colspan={colspan}>Nenhuma versão corresponde aos filtros.</td></tr>"}</tbody></table></div>'


def _table_page(db, user, query, kind):
    ref_name = "inssReference" if kind == "INSS" else "paymentDate"
    reference = _q(query, ref_name, date.today().strftime("%Y-%m") if kind == "INSS" else date.today().isoformat())
    row = None
    try:
        row = resolve(db, kind, reference if kind == "INSS" else reference, "EMPLOYEE", "SALARY_MONTHLY")
    except ValueError:
        pass
    status_filter = _q(query, "status")
    start_filter, end_filter = _q(query, "from"), _q(query, "to")
    rows = _version_list(db, kind, status_filter, start_filter, end_filter)
    id_value = _q(query, "id")
    detail = ""
    if id_value.isdigit():
        selected = db.execute("SELECT * FROM local_tax_versions WHERE id=? AND kind=?", (int(id_value), kind)).fetchone()
        if selected:
            detail = _version_detail(db, user, selected)
    label = "Competência previdenciária" if kind == "INSS" else "Data efetiva do pagamento"
    input_type = "month" if kind == "INSS" else "date"
    top = (f'<section class="card tax-current"><div class="tax-section-heading"><div><span class="tax-kicker">TABELA PUBLICADA</span><h2>{_e(_table_name(row))}</h2></div>'
        f'<form method="get" action="/rotinas/tributos" class="tax-reference"><input type="hidden" name="view" value="{"inss" if kind=="INSS" else "irrf"}"><label>{label}<input type="{input_type}" name="{ref_name}" value="{_e(reference)}" required></label><button class="tax-button">Consultar</button></form></div>')
    if row:
        params = parameters_for_version(row)
        top += _meta_source(row) + _band_rows(kind, params)
        if kind == "INSS":
            top += f'<p>Teto previdenciário: <strong>{_money(params.get("ceiling"))}</strong> · a alíquota é progressiva por faixa · categoria: empregado, doméstico e avulso.</p>'
        else:
            top += '<p>O rendimento e a base após deduções são referências diferentes; a redução é avaliada pelo rendimento tributável aplicável.</p>'
    else:
        top += f'<div class="tax-empty" role="status">Não há uma versão publicada válida para esta referência de {kind}. Nenhum valor oficial é assumido.</div>'
    top += '</section>'
    filters = (f'<section class="card"><form method="get" action="/rotinas/tributos" class="tax-reference tax-history-filter"><input type="hidden" name="view" value="{kind.lower()}">'
        f'<label>Vigência inicial a partir de<input type="date" name="from" value="{_e(start_filter)}"></label><label>Vigência até<input type="date" name="to" value="{_e(end_filter)}"></label>'
        '<label>Estado<select name="status"><option value="">Todos</option>' + "".join(
            f'<option value="{key}"{" selected" if status_filter==key else ""}>{_e(label)}</option>'
            for key,label in (("PUBLISHED","Publicada"),("SUPERSEDED","Substituída"),("COLLECTED","Coletada"),("INVALID_CONTENT","Conteúdo inválido"),("VALIDATED","Validada"),("APPROVED","Aprovada"),("REJECTED","Rejeitada"))) +
        '</select></label><button class="tax-button">Filtrar versões</button><a class="tax-button tax-button-secondary" href="/rotinas/tributos?view=' + kind.lower() + '">Limpar filtros</a></form></section>')
    return top + detail + f'{filters}<section class="card"><div class="tax-section-heading"><div><span class="tax-kicker">HISTÓRICO LOCAL</span><h2>Versões {kind}</h2></div><span>{len(rows)} versão(ões)</span></div>{_versions_table(rows)}</section>'


def _overview(db, user, query):
    inss_ref = _q(query, "inssReference", date.today().strftime("%Y-%m"))
    payment_date = _q(query, "paymentDate", date.today().isoformat())
    inss_row = resolve(db, "INSS", inss_ref, "EMPLOYEE", "EMPLOYEE_MONTHLY") if inss_ref else None
    irrf_row = resolve(db, "IRRF", payment_date, "EMPLOYEE", "SALARY_MONTHLY") if payment_date else None
    pending = db.execute("SELECT COUNT(*) FROM local_tax_versions WHERE workflow_state IN ('COLLECTED','INVALID_CONTENT','VALIDATED','PENDING_REVIEW','APPROVED')").fetchone()[0] if user["role"] == "ADMIN" else None
    conflicts = db.execute("SELECT COUNT(*) FROM local_tax_versions WHERE workflow_state IN ('INVALID_CONTENT','PENDING_REVIEW')").fetchone()[0] if user["role"] == "ADMIN" else None
    future = db.execute("SELECT COUNT(*) FROM local_tax_versions WHERE status='PUBLISHED' AND valid_from>?", (date.today().isoformat(),)).fetchone()[0]
    verified_at = db.execute("SELECT MAX(source_accessed_at) FROM local_tax_versions WHERE status IN ('PUBLISHED','SUPERSEDED') AND source_url LIKE 'https://%' AND source_accessed_at<>''").fetchone()[0]
    cards = []
    for label, row, ref in (("INSS vigente", inss_row, inss_ref), ("IRRF vigente", irrf_row, payment_date)):
        status = ("Publicada · fonte verificada" if row and row["source_url"] else "Publicada · fonte não verificada") if row else "Sem tabela"
        link = f'/rotinas/tributos?view={"inss" if label.startswith("INSS") else "irrf"}'
        cards.append(f'<a class="tax-metric" href="{link}"><span>{label}</span><strong>{status}</strong><small>{_e(_table_name(row) if row else "Não há tabela para "+ref)}</small></a>')
    cards.extend([f'<a class="tax-metric" href="/rotinas/tributos?view=candidates"><span>Candidatas pendentes</span><strong>{pending if pending is not None else "Restrito"}</strong><small>Somente perfis autorizados veem a fila.</small></a>',
                  f'<a class="tax-metric" href="/rotinas/tributos?view=candidates"><span>Divergências para revisão</span><strong>{conflicts if conflicts is not None else "Restrito"}</strong><small>Conteúdo inválido ou pendente de análise técnica.</small></a>',
                  f'<a class="tax-metric" href="/rotinas/tributos?view=history"><span>Próximas vigências publicadas</span><strong>{future}</strong><small>Não se aplicam antes da data inicial.</small></a>'])
    table_cards = []
    for kind, row, ref in (("INSS", inss_row, inss_ref), ("IRRF", irrf_row, payment_date)):
        params = parameters_for_version(row) if row else {}
        summary = f'<p>Referência {_e(ref)} · {_e(_table_name(row))}</p>' if row else f'<p>Não há uma versão publicada válida para esta referência ({_e(ref)}).</p>'
        if row and kind == "INSS":
            summary += f'<p>Teto: <strong>{_money(params.get("ceiling"))}</strong> · {len(params.get("bands", []))} faixas.</p>'
        if row and kind == "IRRF":
            summary += f'<p>Tabela mensal · {len(params.get("bands", []))} faixas · redução versionada: {len(params.get("reductions", []))} regra(s).</p>'
        summary += _meta_source(row) if row else ""
        table_cards.append(f'<article class="card tax-table-card"><div class="tax-section-heading"><h2>{kind}</h2><a class="tax-button tax-button-secondary" href="/rotinas/tributos?view={kind.lower()}">Ver tabela</a></div>{summary}</article>')
    selectors = (f'<section class="card"><form method="get" action="/rotinas/tributos" class="tax-reference tax-reference-set"><input type="hidden" name="view" value="overview">'
        f'<label>Competência do INSS<input type="month" name="inssReference" value="{_e(inss_ref)}" required></label>'
        f'<label>Data de pagamento para IRRF<input type="date" name="paymentDate" value="{_e(payment_date)}" required></label>'
        '<button class="tax-button">Consultar referências</button><a class="tax-button tax-button-secondary" href="/rotinas/tributos?view=simulator">Abrir simulador</a></form></section>')
    freshness = f'<p class="tax-freshness">Última data de conferência registrada: {_e(verified_at or "sem conferência registrada")}. As fontes são conferidas manualmente; os cálculos usam snapshots locais.</p>'
    return f'<section class="tax-metrics">{"".join(cards)}</section>{selectors}<section class="tax-current-grid">{"".join(table_cards)}</section>{freshness}'


def _candidate_page(db, user, query):
    rows = _version_list(db)
    candidates = [row for row in rows if row["status"] in ("DRAFT", "APPROVED", "REJECTED") or row["workflow_state"] in ("COLLECTED", "INVALID_CONTENT", "VALIDATED", "PENDING_REVIEW")]
    id_value = _q(query, "id")
    detail = ""
    if id_value.isdigit():
        row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(id_value),)).fetchone()
        if row:
            detail = _version_detail(db, user, row)
            if _q(query, "edit") == "1" and row["status"] == "DRAFT" and user["role"] == "ADMIN":
                params = json.loads(row["parameters_json"])
                detail = detail.replace('</div><details class="tax-json">', _edit_form(user, row, params) + '</div><details class="tax-json">', 1)
    create = ""
    if user["role"] == "ADMIN":
        create = ('<section class="card tax-create-card"><span class="tax-kicker">NOVA PROPOSTA</span><h2>Cadastrar candidata para análise</h2>'
            '<p>Informe fonte primária, vigência confirmada, natureza tributária, parâmetros completos e justificativa. Salvar não publica.</p>'
            + _candidate_form(user, "INSS", INSS_2026) + _candidate_form(user, "IRRF", IRRF_2026) + '</section>')
    return f'{detail}{create}<section class="card"><div class="tax-section-heading"><div><span class="tax-kicker">FLUXO DE REVISÃO</span><h2>Candidatas e validação</h2></div><span>{len(candidates)} registro(s)</span></div><p>Coletada → Validada → Aprovada → Publicada. Publicadas futuras não são usadas antes da vigência.</p>{_versions_table(candidates)}</section>'


def _history_page(db):
    rows = db.execute("SELECT v.*,u.name creator_name FROM local_tax_versions v JOIN users u ON u.id=v.created_by ORDER BY v.valid_from DESC,v.kind,v.version DESC,v.id DESC LIMIT 300").fetchall()
    return f'<section class="card"><span class="tax-kicker">VERSÕES IMUTÁVEIS</span><h2>Histórico de versões</h2><p>Versões publicadas e substituídas continuam consultáveis; snapshots salvos retêm o identificador usado.</p>{_versions_table(rows, include_author=True)}</section>'


def _audit_page(db, user):
    if user["role"] != "ADMIN":
        return '<section class="card tax-empty" role="alert"><h2>Acesso restrito</h2><p>A auditoria detalhada está disponível apenas ao perfil autorizado.</p></section>'
    rows = db.execute("""SELECT h.*,v.kind,v.competence,v.version,u.name FROM local_tax_history h
        JOIN local_tax_versions v ON v.id=h.tax_version_id JOIN users u ON u.id=h.actor_id
        ORDER BY h.id DESC LIMIT 500""").fetchall()
    entries = "".join(f'<tr><td>{_e(item["created_at"])}</td><td>{_e(item["name"])}</td><td>{_e(item["action"])}</td><td>{_e(item["kind"])} {item["competence"]} v{item["version"]}</td><td>{_e(item["outcome"])}</td><td>{_e(item["detail"])}</td></tr>' for item in rows)
    return f'<section class="card"><span class="tax-kicker">ACESSO ADMINISTRATIVO</span><h2>Auditoria do módulo</h2><p>Criação, edição, validação, aprovação, rejeição, publicação e substituição.</p><div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Quando</th><th>Usuário</th><th>Ação</th><th>Versão</th><th>Resultado</th><th>Detalhe</th></tr></thead><tbody>{entries or "<tr><td colspan=6>Nenhum evento registrado.</td></tr>"}</tbody></table></div></section>'


def _field(name, label, value="", type="text", required=False, placeholder="", wide=False, help_text=""):
    return (f'<label class="{"tax-wide" if wide else ""}">{_e(label)}<input type="{type}" name="{_e(name)}" value="{_e(value)}"'
        + (" required" if required else "") + (f' placeholder="{_e(placeholder)}"' if placeholder else "") + '>'
        + (f'<small>{_e(help_text)}</small>' if help_text else "") + '</label>')


def _simulation_result(db, user, simulation):
    if not simulation:
        return ""
    results = simulation.get("results", {})
    inss, irrf = results.get("inss"), results.get("irrf")
    errors = simulation.get("errors", {})
    items = [
        ("Base INSS", inss.get("assessedBase") if inss else None),
        ("INSS progressivo teórico total", inss.get("totalContributionTheoretical") if inss else None),
        ("INSS já informado em outros vínculos", inss.get("contributionsAlreadyInformed") if inss else None),
        ("Saldo teórico após retenções informadas", inss.get("contributionDue") if inss else None),
        ("Estimativa atribuível a este vínculo", inss.get("estimatedContributionForThisLink") if inss else None),
        ("Rendimentos tributáveis IRRF", irrf.get("grossTaxableIncome") if irrf else None),
        ("Base IRRF", irrf.get("base") if irrf else None),
        ("IRRF preliminar", irrf.get("preliminaryTax") if irrf else None),
        ("Redução aplicada", irrf.get("reduction") if irrf else None),
        ("IRRF final", irrf.get("finalTax") if irrf else None),
    ]
    cards = "".join(f'<article class="tax-result-card"><span>{label}</span><strong>{"R$ " + _money(value) if value is not None else ("Não atribuída automaticamente" if "vínculo" in label.lower() else "Não calculado")}</strong></article>' for label,value in items)
    error_markup = "".join(f'<li>{_e(text)}</li>' for text in errors.values())
    version_links = []
    for key, label in (("inss", "INSS"), ("irrf", "IRRF")):
        result = results.get(key)
        if result:
            href = f'/rotinas/tributos?view={key}&id={result["versionId"]}'
            version_links.append(f'<li><strong>{label} v{result["version"]}</strong> · {_e(result.get("competence") or result.get("paymentDate"))} · <a href="{href}">Ver versão</a><br>{_e(result.get("source"))}</li>')
    memory = []
    for key, result in (("INSS", inss), ("IRRF", irrf)):
        if result:
            warnings = "".join(f'<li>{_e(line)}</li>' for line in result.get("warnings", []))
            memory.append(f'<section><h4>{key}</h4><ol>{"".join(f"<li>{_e(line)}</li>" for line in result.get("memory", []))}</ol>{f"<ul class=tax-notices>{warnings}</ul>" if warnings else ""}</section>')
    notices = "".join(f'<li>{_e(item)}</li>' for item in simulation.get("notices", []))
    save = ""
    if any(results.values()) and not errors and not simulation.get("savedId"):
        payload = json.dumps(simulation.get("inputs", {}), ensure_ascii=False, separators=(",", ":"))
        versions = json.dumps(simulation.get("versions", {}), ensure_ascii=False, separators=(",", ":"))
        signature = simulation_snapshot_signature(user["csrf_token"], simulation.get("inputs", {}), simulation.get("versions", {}))
        save = (f'<form method="post" action="/rotinas/tributos/simular" class="tax-save-form">{_csrf(user)}'
            '<input type="hidden" name="action" value="save"><label>Nome da memória<input name="title" maxlength="120" placeholder="Ex.: Simulação mensal"></label>'
            f'<input type="hidden" name="inputs_json" value="{_e(payload)}"><input type="hidden" name="versions_json" value="{_e(versions)}"><input type="hidden" name="snapshot_signature" value="{signature}">'
            '<button class="tax-button">Salvar esta memória privada</button><small>O snapshot fica visível apenas nesta conta.</small></form>')
    saved_label = '<span class="tax-status">Snapshot salvo · leitura histórica</span>' if simulation.get("savedId") else ""
    deduction_comparison = (f'<section class="tax-deduction-compare"><h3>Comparação das deduções</h3><div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Alternativa</th><th>Total deduzido</th><th>Base gerada</th><th>Seleção</th></tr></thead><tbody>'
        f'<tr><th>Deduções legais</th><td>{_money(irrf.get("legalDeductions"))}</td><td>{_money(irrf.get("legalBase"))}</td><td>{"Selecionada" if irrf.get("deductionMethod")=="LEGAL" else "Não selecionada"}</td></tr>'
        f'<tr><th>Desconto simplificado mensal</th><td>{_money(irrf.get("simplifiedDeduction"))}</td><td>{_money(irrf.get("simplifiedBase"))}</td><td>{"Selecionada" if irrf.get("deductionMethod")=="SIMPLIFIED" else "Não selecionada"}</td></tr>'
        '</tbody></table></div><p>Quando o simplificado é escolhido, o INSS é comparado, mas não é abatido novamente.</p></section>') if irrf else ""
    return (f'<section class="card tax-results"><div class="tax-section-heading"><div><span class="tax-kicker">RESULTADO DA SIMULAÇÃO</span><h2>Memória do cálculo</h2></div>{saved_label}</div>'
        f'{f"<div class=tax-empty role=alert><strong>Resultado parcial:</strong><ul>{error_markup}</ul></div>" if errors else ""}'
        f'<div class="tax-result-grid">{cards}</div>{deduction_comparison}<div class="tax-result-columns"><section><h3>Versões usadas</h3><ul>{"".join(version_links) or "<li>Nenhuma tabela foi selecionada.</li>"}</ul><ul class="tax-notices">{notices}</ul></section>'
        f'<details class="tax-memory" open><summary>Memória reproduzível</summary>{"".join(memory) or "<p>Sem cálculo disponível.</p>"}</details></div>{save}</section>')


def _simulator_page(db, user, query, simulation=None, error="", notice=""):
    default = simulation.get("inputs", {}) if simulation else {}
    saved_id = str(default.get("savedId") or _q(query, "saved_id"))
    if saved_id.isdigit() and not simulation:
        row = db.execute("SELECT * FROM local_tax_simulations WHERE id=? AND created_by=?", (int(saved_id), user["id"])).fetchone()
        if row:
            try:
                saved_inputs = json.loads(row["inputs_json"])
                saved_result = json.loads(row["result_json"])
                saved_result["inputs"] = saved_inputs
                saved_result["savedId"] = row["id"]
                simulation = saved_result
                default = saved_inputs
                notice = "Snapshot histórico aberto; as entradas e versões originais foram preservadas."
            except (ValueError, TypeError):
                error = "Não foi possível ler esta memória salva."
    saved_rows = db.execute("SELECT id,title,competence,payment_date,created_at FROM local_tax_simulations WHERE created_by=? ORDER BY id DESC LIMIT 50", (user["id"],)).fetchall()
    saved_entries = "".join(f'<tr><td><a href="/rotinas/tributos?view=simulator&saved_id={row["id"]}">{_e(row["title"])}</a></td><td>{_e(row["competence"] or "—")}</td><td>{_e(row["payment_date"] or "—")}</td><td>{_e(row["created_at"])}</td><td><form method="post" action="/rotinas/tributos/simular">{_csrf(user)}<input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{row["id"]}"><button class="tax-button tax-button-danger" aria-label="Excluir memória {_e(row["title"])}">Excluir</button></form></td></tr>' for row in saved_rows)
    saved_table = (f'<section class="card"><div class="tax-section-heading"><div><span class="tax-kicker">SOMENTE MEMÓRIAS SALVAS</span><h2>Histórico desta conta</h2></div><span>{len(saved_rows)} memória(s)</span></div>'
        f'<div class="tax-table-scroll"><table class="tax-table"><thead><tr><th>Nome</th><th>Competência</th><th>Pagamento</th><th>Salvo em</th><th>Ação</th></tr></thead><tbody>{saved_entries or "<tr><td colspan=5>Nenhuma memória salva explicitamente.</td></tr>"}</tbody></table></div></section>')
    link_rows = []
    for index in range(1, 4):
        link_rows.append(f'<fieldset class="tax-link-row"><legend>Outro vínculo {index} (opcional)</legend>'
            + _field(f"other_{index}_competence", "Competência", default.get(f"other_{index}_competence", ""), "month")
            + _field(f"other_{index}_base", "Remuneração/base", default.get(f"other_{index}_base", ""), "number", placeholder="0,00")
            + _field(f"other_{index}_inss", "INSS já retido", default.get(f"other_{index}_inss", ""), "number", placeholder="0,00")
            + _field(f"other_{index}_source", "Fonte ou certeza", default.get(f"other_{index}_source", ""), placeholder="Ex.: holerite confirmado") + '</fieldset>')
    form = (f'<section class="card"><div class="tax-section-heading"><div><span class="tax-kicker">SIMULAÇÃO HIPOTÉTICA</span><h2>INSS e IRRF por referências separadas</h2></div></div>'
        '<p>Informe valores sem buscar ou identificar colaborador. Cálculo usa somente versões locais publicadas; a fonte externa não é consultada nesta tela.</p>'
        f'{f"<div class=tax-empty role=alert>{_e(error)}</div>" if error else ""}{f"<div class=tax-confirm role=status>{_e(notice)}</div>" if notice else ""}'
        f'<form method="post" action="/rotinas/tributos/simular" class="tax-form" data-tax-simulator>{_csrf(user)}<input type="hidden" name="action" value="calculate">'
        f'<label>Categoria<select name="category"><option value="EMPLOYEE" selected>Empregado, doméstico ou avulso</option></select></label>'
        f'<label>Apuração previdenciária<select name="mode"><option value="MONTHLY"{" selected" if default.get("mode","MONTHLY")=="MONTHLY" else ""}>Remuneração mensal</option><option value="THIRTEENTH"{" selected" if default.get("mode")=="THIRTEENTH" else ""}>13º salário separado</option></select></label>'
        + _field("competence", "Competência previdenciária (INSS)", default.get("competence", ""), "month", help_text="Obrigatória quando calcular o INSS; não é usada para escolher o IRRF.")
        + _field("paymentDate", "Data efetiva do pagamento (IRRF)", default.get("paymentDate", ""), "date", help_text="Obrigatória quando calcular o IRRF; a data do pagamento determina a versão.")
        + _field("remuneration", "Rendimentos tributáveis / remuneração", default.get("remuneration", ""), "number", True, placeholder="0,00")
        + f'<label>Calcular INSS<select name="calculateInss"><option value="1"{" selected" if default.get("calculateInss","1")=="1" else ""}>Sim</option><option value="0"{" selected" if default.get("calculateInss")=="0" else ""}>Não</option></select></label>'
        + f'<label>Calcular IRRF mensal<select name="calculateIrrf"><option value="1"{" selected" if default.get("calculateIrrf","1")=="1" else ""}>Sim</option><option value="0"{" selected" if default.get("calculateIrrf")=="0" else ""}>Não</option></select><small>Para 13º, o IRRF fica indisponível até existir versão própria validada.</small></label>'
        + f'<label>Origem do INSS para dedução do IRRF<select name="inssSource"><option value="CALCULATED"{" selected" if default.get("inssSource","CALCULATED")=="CALCULATED" else ""}>Usar contribuição calculada acima</option><option value="MANUAL"{" selected" if default.get("inssSource")=="MANUAL" else ""}>Informar valor e origem</option></select></label>'
        + _field("manualInss", "INSS dedutível manual (opcional)", default.get("manualInss", ""), "number", False, "0,00")
        + _field("manualInssSource", "Origem do INSS manual", default.get("manualInssSource", ""), "text", False, "Ex.: comprovante do outro vínculo")
        + _field("dependents", "Dependentes para dedução legal", default.get("dependents", "0"), "number", False, "0")
        + _field("alimony", "Pensão alimentícia dedutível", default.get("alimony", ""), "number", False, "0,00")
        + _field("otherLegalDeductions", "Outras deduções legais permitidas", default.get("otherLegalDeductions", ""), "number", False, "0,00")
        + f'<label>Método de dedução<select name="deductionMethod"><option value="AUTO"{" selected" if default.get("deductionMethod","AUTO")=="AUTO" else ""}>Comparar e usar o mais vantajoso</option><option value="LEGAL"{" selected" if default.get("deductionMethod")=="LEGAL" else ""}>Deduções legais</option><option value="SIMPLIFIED"{" selected" if default.get("deductionMethod")=="SIMPLIFIED" else ""}>Desconto simplificado</option></select></label>'
        + '<div class="tax-wide tax-links"><h3>Vínculos concomitantes</h3><p>Informe somente dados da mesma competência. O saldo mostrado é teórico; sem ordem e comprovantes por fonte, a parcela atribuível não é afirmada como definitiva.</p>' + "".join(link_rows) + '</div>'
        + '<div class="tax-actions tax-wide"><button class="tax-button">Calcular memória</button><a class="tax-button tax-button-secondary" href="/rotinas/tributos?view=simulator">Limpar campos</a></div></form></section>')
    return f'{form}{_simulation_result(db,user,simulation)}{saved_table}'


def render_taxes(db, user, query, simulation=None, error="", notice=""):
    view = _q(query, "view", "overview")
    if view not in dict(VIEWS):
        view = "overview"
    heading = dict(VIEWS)[view]
    descriptions = {
        "overview": "Consulte tabelas publicadas, vigências e estados do fluxo tributário.",
        "inss": "Faixas progressivas de empregado, doméstico e trabalhador avulso por competência.",
        "irrf": "Retenção mensal escolhida pela data efetiva de pagamento do rendimento.",
        "simulator": "Simule com memória e snapshots locais; nada é lançado na folha.",
        "candidates": "Confira parâmetros e fonte antes de validar, aprovar e publicar.",
        "history": "Versões e revisões preservadas para consulta histórica.",
        "audit": "Trilha de transições e responsabilidades do módulo.",
    }
    messages = {
        "candidate-created": "Candidata criada. Ela ainda não está disponível para cálculos oficiais.",
        "candidate-updated": "Candidata atualizada. Valide novamente antes da aprovação.",
        "candidate-valid": "Candidata validada. A aprovação continua necessária.",
        "candidate-invalid": "A candidata contém parâmetros que precisam de correção.",
        "candidate-approved": "Versão aprovada. Ela ainda não está publicada para os cálculos.",
        "version-published": "Versão publicada internamente para a vigência confirmada.",
        "candidate-rejected": "Candidata rejeitada e registrada no histórico.",
        "snapshot-saved": "Memória salva nesta conta; as entradas e versões foram preservadas.",
        "snapshot-deleted": "Memória removida do histórico desta conta.",
    }
    notice = notice or messages.get(_q(query, "notice"), "")
    try:
        if view == "overview":
            content = _overview(db, user, query)
        elif view == "inss":
            content = _table_page(db, user, query, "INSS")
        elif view == "irrf":
            content = _table_page(db, user, query, "IRRF")
        elif view == "candidates":
            content = _candidate_page(db, user, query)
        elif view == "history":
            content = _history_page(db)
        elif view == "audit":
            content = _audit_page(db, user)
        else:
            content = _simulator_page(db, user, query, simulation, error, notice)
    except (ValueError, TypeError, KeyError):
        content = '<section class="card tax-empty" role="alert">Não foi possível carregar esta seção com a referência informada. Revise o período.</section>'
    return (f'<div class="workspace module-workspace tax-workspace"><div class="tax-page-heading"><div><p class="eyebrow">ROTINAS DP · TRIBUTOS</p>'
        f'<h1>{_e(heading)}</h1><p>{_e(descriptions[view])}</p></div></div>{_tabs(view)}'
        f'{f"<div class=tax-alert role=alert>{_e(error)}</div>" if error else ""}{f"<div class=tax-confirm role=status>{_e(notice)}</div>" if notice else ""}{content}'
        '<footer class="tax-boundary">Escopo publicado: empregado, doméstico e trabalhador avulso em INSS e salário mensal em IRRF. FGTS, outros regimes, RRA, 13º do IRRF e obrigações ficam fora desta apuração até existir regra e fonte próprias validadas.</footer></div>')
