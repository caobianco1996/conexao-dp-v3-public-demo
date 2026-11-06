"""Local SQLite UI for chat, calendar, benefit lots and payroll calculators."""
from __future__ import annotations

import json
import html
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from .calculators import CALCULATORS
from .home import cases_for_home, filter_home_cases, home_counts
from .chat import unread_count as chat_unread_count
def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _csrf(user):
    return f'<input type="hidden" name="csrf" value="{esc(user["csrf_token"])}">'


def render_processes(db, query, user):
    """Lista processos do snapshot local e conteúdo criado na V3, com filtros."""
    params = {key: (values[0] if values else "")[:120] for key, values in query.items()}
    search = params.get("q", "").strip().casefold()
    filters = {key: params.get(key, "") for key in ("category", "system", "client", "unit", "status")}
    rows = []
    clients = {}
    for source in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='clients'"):
        item = json.loads(source[0])
        clients[str(item.get("id") or "")] = str(item.get("name") or item.get("legal_name") or item.get("legalName") or "Empresa importada")
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='procedures'"):
        item = json.loads(row["payload"])
        if item.get("deleted_at"):
            continue
        client_id = str(item.get("client_id") or "")
        item["client_name"] = clients.get(client_id, "")
        item["unit_name"] = str(item.get("unit_name") or item.get("unit_id") or "")
        item["local"] = False
        item["key"] = str(row["source_id"])
        rows.append(item)
    for row in db.execute("SELECT id,title,category,body,updated_at FROM local_content WHERE kind='PROCESSO' ORDER BY title COLLATE NOCASE"):
        rows.append({"key": f"local:{row['id']}", "title": row["title"], "category": row["category"],
                     "system": "Interno", "status": "LOCAL", "current_version": 1,
                     "description": row["body"], "client_name": "", "unit_name": "", "local": True,
                     "updated_at": row["updated_at"]})
    categories = sorted({str(r.get("category") or "") for r in rows if r.get("category")}, key=str.casefold)
    systems = sorted({str(r.get("system") or "") for r in rows if r.get("system")}, key=str.casefold)
    client_names = sorted({str(r.get("client_name") or "") for r in rows if r.get("client_name")}, key=str.casefold)
    units = sorted({str(r.get("unit_name") or "") for r in rows if r.get("unit_name")}, key=str.casefold)
    status_labels = {"PUBLISHED": "Publicado", "DRAFT": "Rascunho", "ARCHIVED": "Arquivado", "LOCAL": "Local"}
    visible = []
    for item in rows:
        haystack = " ".join(str(item.get(k) or "") for k in ("title", "description", "category", "system", "client_name", "unit_name")).casefold()
        status = str(item.get("status") or "").upper()
        if search and search not in haystack: continue
        if filters["category"] and str(item.get("category") or "") != filters["category"]: continue
        if filters["system"] and str(item.get("system") or "") != filters["system"]: continue
        if filters["client"] and str(item.get("client_name") or "") != filters["client"]: continue
        if filters["unit"] and str(item.get("unit_name") or "") != filters["unit"]: continue
        if filters["status"] and status != filters["status"]: continue
        visible.append(item)
    visible.sort(key=lambda r: str(r.get("title") or "").casefold())
    list_params = [(key, value) for key, value in params.items() if key not in ("id", "local") and value]
    list_query = urlencode(list_params)
    category_options = '<option value="">Todas</option>' + "".join(f'<option value="{esc(v)}"{" selected" if v==filters["category"] else ""}>{esc(v)}</option>' for v in categories)
    system_options = '<option value="">Todos</option>' + "".join(f'<option value="{esc(v)}"{" selected" if v==filters["system"] else ""}>{esc(v)}</option>' for v in systems)
    client_options = '<option value="">Todos</option>' + "".join(f'<option value="{esc(v)}"{" selected" if v==filters["client"] else ""}>{esc(v)}</option>' for v in client_names)
    unit_options = '<option value="">Todas</option>' + "".join(f'<option value="{esc(v)}"{" selected" if v==filters["unit"] else ""}>{esc(v)}</option>' for v in units)
    statuses = sorted({str(r.get("status") or "").upper() for r in rows})
    status_options = '<option value="">Todas</option>' + "".join(f'<option value="{esc(v)}"{" selected" if v==filters["status"] else ""}>{esc(status_labels.get(v,v.title()))}</option>' for v in statuses)
    cards = []
    for item in visible:
        status = str(item.get("status") or "").upper()
        version = item.get("current_version") or 1
        description = str(item.get("description") or "").strip()
        if not description:
            description = f"Registro de {str(item.get('title') or 'processo').casefold()} identificado na fonte, ainda sem detalhamento textual suficiente para publicação."
        detail_param = ("local=" + quote(item["key"][6:])) if item["local"] else ("id=" + quote(item["key"], safe=""))
        href = "/central-dp/processos?" + ((list_query + "&") if list_query else "") + detail_param
        cards.append(f'<article class="process-card" tabindex="0" data-process-card data-detail-href="{href}" aria-label="Abrir detalhes de {esc(item.get("title") or "Processo")}"><div class="process-card-main"><p class="process-meta">{esc(item.get("category") or "Geral")} · {esc(item.get("system") or "Interno")}</p><h2>{esc(item.get("title") or "Processo")}</h2><p class="process-description">{esc(description[:500])}</p><small>Versão {esc(version)} · {esc(status_labels.get(status, "Local" if item["local"] else status.title()))}</small></div><a class="process-open" href="{href}">Abrir</a></article>')
    selected_id = params.get("id", "")
    detail = ""
    return_params = [(key, value) for key, value in params.items() if key not in ("id", "local") and value]
    return_url = "/central-dp/processos" + ("?" + urlencode(return_params) if return_params else "")

    def read_json(value):
        if isinstance(value, dict): return value
        try: return json.loads(value or "{}")
        except (TypeError, ValueError): return {}

    def readable(value):
        value = str(value or "").replace("N�O", "NÃO").replace("n�o", "não").replace("publica��o", "publicação").replace("Aba �ACOMP DE AFAST�", "Aba “ACOMP DE AFAST”")
        return esc(value)

    def render_detail(item, versions, is_local=False):
        title = str(item.get("title") or "Processo")
        category = str(item.get("category") or "—")
        system = str(item.get("system") or ("Interno" if is_local else "—"))
        source = str(item.get("source") or ("CONTEÚDO LOCAL" if is_local else "—"))
        current = versions[0] if versions else {}
        version_content = read_json(current.get("content"))
        nested = read_json(version_content.get("content"))
        description = str(item.get("description") or version_content.get("description") or "").strip()
        objective = str(version_content.get("whenToUse") or nested.get("whenToUse") or "")
        steps = version_content.get("steps") or version_content.get("quickGuide") or nested.get("steps") or nested.get("quickGuide") or []
        checklist = nested.get("checklist") or read_json(current.get("checklist"))
        if isinstance(checklist, dict): checklist = checklist.get("items", [])
        docs = nested.get("documents") or version_content.get("documents") or version_content.get("assets") or []
        notes = str(nested.get("notes") or version_content.get("notes") or "").strip()
        # Notas técnicas de migração não são observações operacionais do processo.
        if notes.casefold().startswith("conteúdo mantido em rascunho"):
            notes = ""
        document_labels = []
        for doc in docs if isinstance(docs, list) else []:
            if isinstance(doc, dict): document_labels.append(str(doc.get("name") or doc.get("title") or doc.get("filename") or "Documento"))
            elif doc: document_labels.append(str(doc))
        documents_html = "<br>".join(readable(name) for name in document_labels) or (readable(notes) if notes else '<span class="text-muted">Não informado</span>')
        def list_html(values):
            entries = []
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict):
                    text = value.get("detail") or value.get("title") or value.get("name") or ""
                else: text = value
                if str(text or "").strip(): entries.append(f"<li>{readable(text)}</li>")
            return "".join(entries)
        source_locator = str(version_content.get("sourceLocator") or current.get("source") or "")
        history = []
        for version in versions:
            vcontent = read_json(version.get("content"))
            locator = str(vcontent.get("sourceLocator") or version.get("source") or "Fonte não informada")
            created = str(version.get("created_at") or "")
            try:
                stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
                created = stamp.astimezone(timezone(timedelta(hours=-3), "BRT")).strftime("%d/%m/%Y, %H:%M:%S")
            except (ValueError, OverflowError): pass
            number = version.get("number") or vcontent.get("version") or "—"
            history.append(f'<details><summary>Versão {esc(number)} · {esc(created or "Data não informada")} · {readable(locator)}</summary></details>')
        history_html = "".join(history) or '<details><summary>Versão 1 · Conteúdo local</summary></details>'
        effective = current.get("valid_from") or item.get("valid_from") or "—"
        process_key = str(item.get("id") or "")
        case_href = "/central-dp/casos?from_process=" + quote(process_key, safe="") + "#novo" if not is_local else "/central-dp/casos#novo"
        files_href = "/administracao/arquivos?ownerType=PROCESS&amp;ownerId=" + quote(process_key, safe="")
        edit_button = '<button type="button" class="btn btn-outline-primary btn-sm" disabled title="A edição/versionamento local deste processo ainda não está disponível.">Editar e versionar</button>'
        return f'''<div class="dp-detail-backdrop" data-detail-backdrop><article class="dp-detail" role="dialog" aria-modal="true" aria-label="{esc(title)}" tabindex="-1" data-process-dialog>
          <header><div><span class="eyebrow">Processos</span><h2>{esc(title)}</h2></div><button type="button" class="btn btn-outline-secondary btn-sm" data-close-detail data-return-url="{esc(return_url)}">Fechar</button></header>
          <div class="dp-meta"><span>Categoria: {esc(category)}</span><span>Sistema: {esc(system)}</span><span>Fonte: {readable(source)}</span><span>Vigência: {esc(effective or "—")}</span></div>
          <p class="dp-detail-description"><span>{readable(description)}</span></p><h3>Objetivo</h3><p><span class="dp-pre-wrap">{readable(objective)}</span></p>
          <h3>Passo a passo</h3><ul class="mb-0">{list_html(steps)}</ul><h3>Checklist</h3><ul class="mb-0">{list_html(checklist)}</ul>
          <h3>Documentos e observações</h3>{documents_html}<div class="dp-actions"><a class="btn btn-outline-primary btn-sm" href="/central-dp/manuais">Abrir manuais</a><a class="btn btn-outline-primary btn-sm" href="{case_href}">Criar caso a partir deste processo</a></div>
          <h3>Versões anteriores e fontes</h3><div class="dp-history">{history_html}</div><a class="btn btn-outline-primary btn-sm dp-files-link" href="{files_href}">Arquivos vinculados</a>{edit_button}
          </article></div>'''
    if selected_id:
        selected = db.execute("SELECT payload FROM v2_source_rows WHERE table_name='procedures' AND source_id=?", (selected_id,)).fetchone()
        if selected:
            item = json.loads(selected[0])
            versions = [dict(row) for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='procedure_versions' AND json_extract(payload,'$.procedure_id')=? ORDER BY CAST(json_extract(payload,'$.number') AS INTEGER) DESC", (str(item.get("id") or selected_id),))]
            versions = [read_json(row["payload"]) for row in versions]
            detail = render_detail(item, versions)
    local_id = params.get("local", "")
    if local_id.isdigit():
        local = db.execute("SELECT title,category,body FROM local_content WHERE id=? AND kind='PROCESSO'", (int(local_id),)).fetchone()
        if local:
            detail = render_detail({"id": f"local:{local_id}", "title": local["title"], "category": local["category"], "description": local["body"], "source": "CONTEÚDO LOCAL"}, [], True)
    return f'''<div class="workspace processes-workspace"><section class="processes-page">
      <div class="processes-heading"><p>Procedimentos operacionais, versões e regras específicas.</p><a class="process-new" href="#novo-processo">Novo processo</a></div>
      <form method="get" class="process-filters"><label class="process-search">Pesquisar título, conteúdo ou palavra-chave<input type="search" name="q" maxlength="120" value="{esc(search)}" placeholder="Pesquisar título, conteúdo ou palavra-chave"></label>
      <label>Categoria<select name="category">{category_options}</select></label><label>Sistema<select name="system">{system_options}</select></label>
      <label>Cliente<select name="client">{client_options}</select></label><label>Unidade<select name="unit">{unit_options}</select></label><label>Situação<select name="status">{status_options}</select></label><button>Filtrar</button></form>
      <p class="process-count">{len(visible)} processo(s) nesta seleção. Registros importados da V2 são somente leitura; processos locais podem ser criados abaixo.</p>
      {detail}<div class="process-list">{"".join(cards) or '<div class="card"><p>Nenhum processo corresponde aos filtros.</p></div>'}</div>
      <section class="card process-create" id="novo-processo"><h2>Novo processo local</h2><form method="post" action="/central-dp/processos" class="fields">{_csrf(user)}<label>Título<input name="title" maxlength="160" required></label><label>Categoria<input name="category" maxlength="80"></label><label class="full-field">Conteúdo<textarea name="body" rows="6" maxlength="50000" required></textarea></label><button>Salvar processo</button></form></section>
      </section></div>'''


def _pt_date(value):
    try:return date.fromisoformat(str(value)[:10]).strftime("%d/%m/%Y")
    except (TypeError,ValueError):return str(value or "")


def _pt_datetime(value):
    if not value:return ""
    try:
        stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone(timedelta(hours=-3),"BRT")).strftime("%d/%m/%Y %H:%M")
    except (ValueError,OverflowError):return str(value)


def _action_label(value):
    labels={"CREATED":"Caso criado","RESPONSIBLE_ID":"Responsável atualizado","ASSIGNEE":"Atribuição atualizada","DUE_AT":"Prazo atualizado","DUEAT":"Prazo atualizado","CHECKLIST_ADDED":"Item de checklist adicionado","CHECKLIST":"Checklist atualizado","WORKFLOW_STEP_OPEN":"Etapa iniciada","WORKFLOW_STEP_SKIPPED":"Etapa ignorada","WORKFLOW_NEXT_OPEN":"Próxima etapa iniciada","STATUS":"Situação atualizada","DONE":"Caso concluído","CANCELLED":"Caso cancelado","WAITING":"Aguardando","HANDOFF":"Caso encaminhado","DELETED":"Caso removido"}
    return labels.get(str(value or ""),str(value or "Atualização").replace("_"," ").capitalize())


def _today_brasilia():
    return datetime.now(timezone(timedelta(hours=-3),"BRT")).date()


def render_home(db, user, query):
    today=_today_brasilia(); end=today+timedelta(days=7)
    scope=(query.get("scope") or ["mine"])[0]
    scope="team" if scope=="team" else "mine"
    records=cases_for_home(db,user["id"],scope)
    counts=home_counts(records,today)
    if scope=="team":
        snapshot_terminal=[json.loads(r[0]) for r in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='cases'")]
        counts["waiting"]=sum(1 for row in snapshot_terminal if not row.get("deleted_at") and row.get("status")=="WAITING")
        counts["completed"]=sum(1 for row in snapshot_terminal if not row.get("deleted_at") and row.get("status")=="DONE")
        counts["waiting"]+=db.execute("SELECT COUNT(*) FROM process_cases WHERE status='AGUARDANDO' ").fetchone()[0]
        counts["completed"]+=db.execute("SELECT COUNT(*) FROM process_cases WHERE status='CONCLUIDO'").fetchone()[0]
    else:
        counts["waiting"]=0
        counts["completed"]=db.execute("SELECT COUNT(*) FROM process_cases WHERE status='CONCLUIDO' AND owner_id=?",(user["id"],)).fetchone()[0]
    active_snapshot=db.execute("SELECT COUNT(*) FROM v2_source_rows WHERE table_name='cases' AND json_extract(payload,'$.status') IN ('OPEN','IN_PROGRESS') AND COALESCE(json_extract(payload,'$.deleted_at'),'')='' ").fetchone()[0]
    assigned_snapshot=db.execute("SELECT COUNT(*) FROM local_case_assignments a JOIN v2_source_rows v ON v.table_name='cases' AND v.source_id=a.source_case_id WHERE json_extract(v.payload,'$.status') IN ('OPEN','IN_PROGRESS') AND COALESCE(json_extract(v.payload,'$.deleted_at'),'')='' ").fetchone()[0]
    unlinked_snapshot=max(0,active_snapshot-assigned_snapshot)
    reference=db.execute("SELECT MAX(json_extract(payload,'$.reference_date')) FROM v2_source_rows WHERE table_name='dataset_versions' AND json_extract(payload,'$.dataset_type')='active'").fetchone()[0]
    uploaded=db.execute("SELECT MAX(json_extract(payload,'$.uploaded_at')) FROM v2_source_rows WHERE table_name='dataset_versions' AND json_extract(payload,'$.dataset_type')='active'").fetchone()[0]
    source_note=(f"Snapshot V2 · referência {_pt_date(reference)}" if reference else "Snapshot V2 sem data de referência")+(f" · importado {_pt_datetime(uploaded)} (Brasília)" if uploaded else "")
    scope_controls=f'<nav class="home-scope" aria-label="Escopo dos casos"><a href="/central-dp/inicio?scope=mine" aria-pressed="{"true" if scope=="mine" else "false"}">Atribuídos a mim</a><a href="/central-dp/inicio?scope=team" aria-pressed="{"true" if scope=="team" else "false"}">Todos da equipe</a></nav>'
    metrics=[("open","Em aberto","Casos operacionais ativos","open"),("overdue","Vencidos","Prazo anterior a hoje","overdue"),("upcoming","Próximos 7 dias","Inclui hoje e o sétimo dia","upcoming"),("checklist","Checklists pendentes","Itens incompletos em casos ativos","checklist"),("waiting","Aguardando retorno","Casos marcados como aguardando","active"),("completed","Concluídos","Casos concluídos nesta instalação","active")]
    cards="".join((f'<div class="home-metric home-{key} home-metric-static"><span>{label}</span><strong>{counts[key]}</strong></div>' if key in ("waiting","completed") else f'<a class="home-metric home-{key}" href="/central-dp/casos?home_scope={scope}&home_filter={filter_key}"><span>{label}</span><strong>{counts[key]}</strong></a>') for key,label,hint,filter_key in metrics)
    if scope=="mine" and not records:
        case_section=f'<section class="card home-empty"><h2>Atribuídos a mim</h2><p>Não há casos ativos atribuídos a esta conta.</p><p>{unlinked_snapshot} caso(s) ativos do snapshot V2 ainda estão sem atribuição local. Os vínculos são registrados na V3, sem alterar os dados importados.</p><a class="action" href="/central-dp/inicio?scope=team">Ver Todos da equipe</a></section>'
    else:
        visible=filter_home_cases(records,"all",today)[:10]
        lines="".join(f'<article class="home-case"><div><span class="home-case-source">{esc(row["source"]+(" · consulta" if row["source"]=="V2" else ""))} · {esc(row["status_label"])}</span><h3><a href="{row["href"]}">{esc(row["title"])}</a></h3><p>{esc(row["company"])}{(" · Unidade "+esc(row["unit"])) if row["unit"] else ""} · {esc(row["owner_name"])}</p><small>{("Prazo "+esc(_pt_date(row["due_date"]))) if row["due_date"] else "Sem prazo definido"}{(" · "+str(row["checklist_pending"])+" item(ns) pendente(s)") if row["checklist_pending"] else ""}</small></div><a class="home-resolve" href="{row["href"]}">{"Resolver agora" if row["source"]=="V3" else "Abrir caso"}</a></article>' for row in visible)
        case_section=f'<section class="card home-case-section"><div class="home-section-heading"><div><h2>{"Atribuídos a mim" if scope=="mine" else "Todos da equipe"}</h2><p>{len(records)} caso(s) ativos neste escopo · até 10 prioritários</p></div><a href="/central-dp/casos?home_scope={scope}&home_filter=active">Ver todos</a></div>{lines or "<p>Nenhum caso no escopo atual.</p>"}</section>'
    if scope=="team":
        case_section+=f'<p class="home-source-note">Inclui {active_snapshot} caso(s) ativos do snapshot V2 em modo somente leitura; {unlinked_snapshot} ainda sem atribuição local. Atribuições locais não alteram o snapshot.</p>'
    events=db.execute("""SELECT e.id,e.title,e.due_date,e.starts_at,e.category,e.priority,m.source_id,p.payload
        FROM local_calendar_events e LEFT JOIN local_calendar_source_map m ON m.local_event_id=e.id
        LEFT JOIN v2_source_rows p ON p.table_name='calendar_events' AND p.source_id=m.source_id
        WHERE e.archived=0 AND e.status<>'CONCLUIDO' AND e.due_date BETWEEN ? AND ?
        AND COALESCE(json_extract(p.payload,'$.case_id'),'')=''
        ORDER BY e.due_date,e.starts_at,e.id LIMIT 6""",(today.isoformat(),end.isoformat())).fetchall()
    event_html="".join(f'<li><a href="/rotinas/calendario?id={e["id"]}&month={e["due_date"][:7]}"><time>{esc(_pt_date(e["due_date"]))}{(" · "+esc(e["starts_at"][11:16])) if e["starts_at"] and len(e["starts_at"])>=16 else ""}</time><span>{esc(e["title"])}</span></a><small>{esc(e["category"] or "Evento")}</small></li>' for e in events) or '<li class="home-muted">Nenhum evento independente de caso nos próximos sete dias.</li>'
    message_rows=[]
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='quick_messages' ORDER BY source_id"):
        item=json.loads(row["payload"])
        if not item.get("archived_at"): message_rows.append((row["source_id"],item))
    message_html="".join(f'<li><a href="/central-dp/mensagens?id={quote(source_id, safe="")}">{esc(item.get("title") or "Modelo")}</a></li>' for source_id,item in message_rows[:3]) or '<li class="home-muted">Nenhum modelo importado.</li>'
    procedure_rows=[]
    for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='procedures'"):
        item=json.loads(row["payload"])
        if not item.get("archived_at") and str(item.get("status") or "ACTIVE").upper() not in ("ARCHIVED","INACTIVE","DELETED"):
            procedure_rows.append((row["source_id"],item))
    # O snapshot fornece os processos disponíveis, mas não registra frequência de uso.
    procedure_rows.sort(key=lambda entry:str(entry[1].get("title") or "").casefold())
    procedure_html="".join(f'<li><a href="/central-dp/processos?id={quote(source_id, safe="")}">{esc(item.get("title") or "Processo")}</a></li>' for source_id,item in procedure_rows[:5]) or '<li class="home-muted">Nenhum processo disponível.</li>'
    import_rows=db.execute("SELECT source_name,row_count,imported_at FROM v2_import_log ORDER BY imported_at DESC,id DESC LIMIT 5").fetchall()
    import_html="".join(f'<li><span>{esc(row["source_name"])} · {row["row_count"]} linha(s)</span><time>{esc(_pt_datetime(row["imported_at"]))}</time></li>' for row in import_rows) or '<li class="home-muted">Nenhuma importação registrada.</li>'
    unread=chat_unread_count(db,user["id"])
    recent=[]
    local_events=db.execute("""SELECT e.action,c.title,e.created_at FROM case_events e JOIN process_cases c ON c.id=e.case_id
        WHERE c.status IN ('ABERTO','EM_ANDAMENTO') AND (?='team' OR c.owner_id=?) ORDER BY e.id DESC LIMIT 10""",(scope,user["id"])).fetchall()
    recent.extend({"action":r["action"],"title":r["title"],"created_at":r["created_at"],"source":"V3"} for r in local_events)
    if scope=="team":
        visible_source_ids={r["source_id"] for r in records if r["source"]=="V2"}
        source_cases={r["source_id"]:json.loads(r["payload"]).get("title") for r in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='cases'") if r["source_id"] in visible_source_ids}
        for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='case_events'"):
            item=json.loads(row[0])
            if str(item.get("case_id")) in source_cases:
                recent.append({"action":item.get("action") or "Atualização","title":source_cases[str(item.get("case_id"))],"created_at":item.get("created_at"),"source":"V2"})
    recent.sort(key=lambda r:r.get("created_at") or "",reverse=True)
    activity="".join(f'<li><span>{esc(_action_label(r["action"]))} · {esc(r["title"] or "Caso")}</span><small>{esc(r["source"])} · {esc(_pt_datetime(r["created_at"]))}</small></li>' for r in recent[:10]) or '<li class="home-muted">Nenhuma movimentação nos casos ativos.</li>'
    note='A visão pessoal combina atribuições locais explícitas e vínculos locais dos casos V2 ativos. Os vínculos locais não alteram o snapshot importado.' if scope=="mine" else 'A visão da equipe combina casos locais ativos e snapshot V2 ativo. Casos concluídos, cancelados e excluídos não entram nos cartões.'
    focus_label="Prazo próximo" if counts["upcoming"] else "Rotina em dia"
    focus_text=f'{counts["upcoming"]} demanda(s) vencem nos próximos 7 dias. Prepare a próxima etapa.' if counts["upcoming"] else 'Não há prazos de casos nos próximos 7 dias.'
    return f'<div class="workspace home-workspace"><p class="eyebrow">CENTRAL DP</p><h1>Início</h1><p class="home-intro">O que precisa de atenção na operação agora.</p><div class="home-updated"><span>Dados e regras aplicados à data de {today.strftime("%d/%m/%Y")} · janela: hoje até {end.strftime("%d/%m/%Y")}</span><small>{source_note}</small></div>{scope_controls}<section class="home-metrics" aria-label="Resumo de atenção">{cards}</section><nav class="home-shortcuts home-actions" aria-label="Ações rápidas"><a href="/central-dp/casos#novo">Criar caso</a><a href="/central-dp/processos">Consultar processo</a><a href="/central-dp/manuais">Abrir manual</a><a href="/central-dp/casos?home_scope={scope}&home_filter=checklist">Visualizar pendências</a></nav><section class="home-focus"><div><span class="eyebrow">Foco de hoje</span><h2>Casos e checklists</h2><p><strong>{focus_label}</strong> · {esc(focus_text)}</p></div><a class="home-primary-action" href="/central-dp/casos?home_scope={scope}&home_filter=upcoming">Ver próximos prazos</a></section><p class="home-method-note">{esc(note)}</p><div class="home-dashboard-grid"><div>{case_section}<section class="card"><div class="home-section-heading"><h2>Processos disponíveis</h2><a href="/central-dp/processos">Ver processos</a></div><ul class="home-list">{procedure_html}</ul><p class="home-muted">O snapshot não registra frequência de uso; a lista apresenta processos em ordem alfabética.</p></section></div><div><section class="card"><div class="home-section-heading"><h2>Alertas para você</h2><a href="/central-dp/mensagens">Ver alertas</a></div><p class="home-muted">Nenhum registro até o momento.</p></section><section class="card"><div class="home-section-heading"><h2>Próximos eventos do calendário</h2><a href="/rotinas/calendario?mode=agenda&month={today:%Y-%m}">Ver calendário</a></div><ul class="home-list">{event_html}</ul></section><section class="card"><div class="home-section-heading"><h2>Mensagens rápidas</h2><a href="/central-dp/chat">Chat{f" · {unread} não lida(s)" if unread else ""}</a></div><ul class="home-list">{message_html}</ul><a href="/central-dp/mensagens">Abrir modelos</a></section></div></div><div class="home-dashboard-grid"><section class="card"><div class="home-section-heading"><h2>Atividades recentes</h2><a href="/central-dp/casos">Casos e checklists</a></div><ul class="recent home-activity">{activity}</ul></section><section class="card"><div class="home-section-heading"><h2>Movimentações recentes</h2><a href="/analytics/atualizar">Histórico de importações</a></div><ul class="home-list">{import_html}</ul></section></div><p class="home-method-note">Tempo médio de conclusão: indicador indisponível nos registros locais importados. Indicadores operacionais, sem ranking individual.</p></div>'


def render_home_cases(db,user,query):
    scope=(query.get("home_scope") or ["mine"])[0]
    scope="team" if scope=="team" else "mine"
    selected=(query.get("home_source") or [""])[0]
    if selected:
        # Resolve Home deep-links into the same editable V3 case card; the V2
        # snapshot remains read-only while local overlays hold subsequent work.
        from .cases_ui import render as render_cases
        return render_cases(db,user,{"scope":[scope],"view":["active"],"open":[selected]})
        row=db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='cases' AND source_id=?",(selected,)).fetchone()
        home_scope="team" if scope=="team" else "mine"
        if not row:
            return f'<div class="workspace"><section class="card"><h1>Caso não encontrado</h1><a href="/central-dp/inicio?scope={home_scope}">Voltar ao Início</a></section></div>'
        item=json.loads(row["payload"])
        if item.get("deleted_at") or item.get("status") not in ("OPEN","IN_PROGRESS"):
            return f'<div class="workspace"><section class="card"><h1>Caso indisponível na fila ativa</h1><p>Este caso não pertence à população operacional ativa.</p><a href="/central-dp/inicio?scope={home_scope}">Voltar ao Início</a></section></div>'
        checks=[]
        for checkrow in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='case_checklist'"):
            check=json.loads(checkrow[0])
            if str(check.get("case_id"))==str(item.get("id")):
                checks.append(f'<li>{"☑" if check.get("done") else "☐"} {esc(check.get("title") or "Etapa")} <small>{"Concluído" if check.get("done") else "Pendente"}</small></li>')
        title=esc(item.get("title") or "Caso V2")
        client_id=str(item.get("client_id") or "")
        client_row=db.execute("SELECT json_extract(payload,'$.name') FROM v2_source_rows WHERE table_name='clients' AND json_extract(payload,'$.id')=? LIMIT 1",(client_id,)).fetchone() if client_id else None
        company_name=(client_row[0] if client_row and client_row[0] else "Empresa importada") if client_id else "Geral / interno"
        company=esc(company_name)
        due=esc(_pt_date(str(item.get("due_at") or "")[:10]) if item.get("due_at") else "Sem prazo")
        assignment=db.execute("SELECT u.name FROM local_case_assignments a JOIN users u ON u.id=a.owner_id WHERE a.source_case_id=?",(selected,)).fetchone()
        assigned_label=f'<p>Atribuído localmente a {esc(assignment["name"])}; atribuição somente na V3.</p>' if assignment else '<p>Registro importado em modo somente leitura. Ainda sem responsável vinculado a uma conta local.</p>'
        body=f'<div class="workspace"><p class="eyebrow">CASOS E CHECKLISTS · SNAPSHOT V2</p><h1>{title}</h1>{assigned_label}<section class="card"><h2>Resumo</h2><p>Situação: {esc(item.get("status"))} · Prioridade: {esc(item.get("priority") or "Normal")} · Prazo: {due}</p><p>Empresa: {company} · Unidade: {esc(item.get("unit_id") or "—")}</p><p>{esc(item.get("description") or "")}</p></section><section class="card" id="checklist"><h2>Checklist importado ({len(checks)} itens)</h2><ul class="checklist">{"".join(checks) or "<li>Sem itens.</li>"}</ul></section><a href="/central-dp/inicio?scope={home_scope}">← Voltar ao Início</a></div>'
        return body
    filter_key=(query.get("home_filter") or ["active"])[0]
    labels={"open":"Em aberto","overdue":"Vencidos","upcoming":"Próximos 7 dias","checklist":"Checklists pendentes","active":"Casos ativos"}
    if filter_key not in labels: filter_key="active"
    records=cases_for_home(db,user["id"],scope)
    rows=filter_home_cases(records,filter_key,_today_brasilia())
    cards="".join(f'<article class="home-case"><div><span class="home-case-source">{esc(row["source"]+(" · consulta" if row["source"]=="V2" else ""))} · {esc(row["status_label"])}</span><h2><a href="{row["href"]}">{esc(row["title"])}</a></h2><p>{esc(row["company"])} · {esc(row["owner_name"])}</p><small>{("Prazo "+esc(_pt_date(row["due_date"]))) if row["due_date"] else "Sem prazo"} · {row["checklist_pending"]} item(ns) pendente(s)</small></div><a class="home-resolve" href="{row["href"]}">{"Resolver agora" if row["source"]=="V3" else "Abrir caso"}</a></article>' for row in rows) or '<section class="card home-empty"><p>Nenhum caso corresponde a este filtro.</p><a href="/central-dp/inicio?scope='+scope+'">Voltar ao Início</a></section>'
    return f'<div class="workspace home-case-list"><p class="eyebrow">CENTRAL DP</p><h1>{esc(labels[filter_key])}</h1><p>Escopo: {"Todos da equipe" if scope=="team" else "Atribuídos a mim"} · {len(rows)} caso(s) · ordenados por urgência e prazo.</p><a href="/central-dp/inicio?scope={scope}">← Voltar ao Início</a>{cards}</div>'


def render_taxes(db, user, query):
    from .tax_ui import render_taxes as render
    return render(db, user, query)


def _source_snapshot(db, table, columns):
    rows = db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name=? ORDER BY source_id LIMIT 200", (table,)).fetchall()
    heading = "".join(f"<th>{esc(k.replace('_',' ').title())}</th>" for k in columns)
    content = "".join("<tr>"+"".join(f"<td>{esc(json.loads(row['payload']).get(key) or '—')}</td>" for key in columns)+"</tr>" for row in rows)
    return f'<details class="card"><summary>Histórico importado da V2 ({len(rows)} registros, somente leitura)</summary><div class="table-wrap"><table><thead><tr>{heading}</tr></thead><tbody>{content or "<tr><td>Nenhum registro importado.</td></tr>"}</tbody></table></div></details>'


def render_chat(db, user, query):
    """Mount the shared interactive client for the Python chat page."""
    return '<div class="chat-page-mount" aria-label="Chat da equipe"></div>'

def render_calendar(db, user, query):
    month = (query.get("month") or [date.today().strftime("%Y-%m")])[0]
    try: first = date.fromisoformat(month+"-01")
    except ValueError: first = date.today().replace(day=1)
    next_month = (date(first.year+1,1,1) if first.month==12 else date(first.year,first.month+1,1))
    end = next_month-timedelta(days=1)
    mode = (query.get("mode") or ["month"])[0]
    events = db.execute("SELECT id,title,due_date,starts_at,category,status,description,notes,priority,recurrence_type FROM local_calendar_events WHERE archived=0 AND due_date BETWEEN ? AND ? ORDER BY due_date,starts_at,id",(first.isoformat(),end.isoformat())).fetchall()
    archived_rows=db.execute("SELECT id,title,due_date FROM local_calendar_events WHERE archived=1 ORDER BY due_date DESC,id DESC LIMIT 100",()).fetchall()
    archived_events="".join(f'<tr><td>{esc(row["due_date"])}</td><td>{esc(row["title"])}</td><td><form method="post" action="/rotinas/calendario/{row["id"]}/restaurar">{_csrf(user)}<button>Restaurar</button></form></td></tr>' for row in archived_rows)
    archived_section=f'<details class="card"><summary>Eventos arquivados ({len(archived_rows)})</summary><div class="table-wrap"><table><thead><tr><th>Data</th><th>Evento</th><th></th></tr></thead><tbody>{archived_events or "<tr><td colspan=3>Nenhum evento arquivado.</td></tr>"}</tbody></table></div></details>'
    if mode == "week":
        anchor=(query.get("week") or [date.today().isoformat()])[0]
        try: week_start=date.fromisoformat(anchor)-timedelta(days=date.fromisoformat(anchor).weekday())
        except ValueError: week_start=first-timedelta(days=first.weekday())
        week_end=week_start+timedelta(days=6)
        events=db.execute("SELECT id,title,due_date,starts_at,category,status,description,notes,priority,recurrence_type FROM local_calendar_events WHERE archived=0 AND due_date BETWEEN ? AND ? ORDER BY due_date,starts_at,id",(week_start.isoformat(),week_end.isoformat())).fetchall()
    elif mode == "upcoming":
        anchor=date.today(); events=db.execute("SELECT id,title,due_date,starts_at,category,status,description,notes,priority,recurrence_type FROM local_calendar_events WHERE archived=0 AND due_date BETWEEN ? AND ? ORDER BY due_date,starts_at,id",(anchor.isoformat(),(anchor+timedelta(days=14)).isoformat())).fetchall()
    event_rows = "".join(f'<tr><td>{esc(row["due_date"])}</td><td><a href="/rotinas/calendario?id={row["id"]}&month={first:%Y-%m}">{esc(row["title"])}</a></td><td>{esc(row["category"] or "—")}</td><td>{esc(row["priority"])}</td><td>{"Concluído" if row["status"]=="CONCLUIDO" else "Pendente"}</td><td><form method="post" action="/rotinas/calendario/{row["id"]}/concluir">{_csrf(user)}<button>{"Reabrir" if row["status"]=="CONCLUIDO" else "Concluir"}</button></form></td></tr>' for row in events) or '<tr><td colspan="6">Sem eventos nesta visualização.</td></tr>'
    if mode == "month":
        grid_start=first-timedelta(days=first.weekday()); grid_end=end+timedelta(days=6-end.weekday())
        by_day={}
        for event in events: by_day.setdefault(event["due_date"],[]).append(event)
        cells=[]; cursor=grid_start
        while cursor<=grid_end:
            day=cursor.isoformat()
            chips="".join(f'<a class="calendar-chip" href="/rotinas/calendario?id={e["id"]}&month={first:%Y-%m}">{esc(e["title"])}</a>' for e in by_day.get(day,[]))
            cells.append(f'<div class="calendar-day{" outside" if cursor.month!=first.month else ""}"><a href="/rotinas/calendario?month={first:%Y-%m}&bank_day={day}" class="calendar-date">{cursor.day}</a>{chips}</div>')
            cursor+=timedelta(days=1)
        event_view='<div class="month-grid"><div class="weekdays"><span>Seg</span><span>Ter</span><span>Qua</span><span>Qui</span><span>Sex</span><span>Sáb</span><span>Dom</span></div><div class="calendar-days">'+"".join(cells)+'</div></div>'
    else:
        event_view=f'<div class="table-wrap"><table><thead><tr><th>Data</th><th>Evento</th><th>Categoria</th><th>Prioridade</th><th>Status</th><th></th></tr></thead><tbody>{event_rows}</tbody></table></div>'
    prev_month = (date(first.year-1,12,1) if first.month==1 else date(first.year,first.month-1,1)).strftime("%Y-%m")
    new_event = f'<section class="card"><h2>Novo evento</h2><form method="post" action="/rotinas/calendario" class="fields">{_csrf(user)}<label>Título<input name="title" maxlength="160" required></label><label>Data<input name="due_date" type="date" value="{date.today().isoformat()}" required></label><label>Hora<input name="starts_at" type="time" value="09:00"></label><label>Categoria<input name="category" maxlength="80"></label><label>Prioridade<select name="priority"><option>NORMAL</option><option>ALTA</option><option>URGENTE</option><option>BAIXA</option></select></label><label>Repetir<select name="recurrence_type"><option value="NONE">Não repetir</option><option value="DAILY">Diariamente</option><option value="WEEKLY">Semanalmente</option><option value="MONTHLY">Mensalmente</option></select></label><label>Ocorrências (até 24)<input name="repeat_count" type="number" min="1" max="24" value="1"></label><label class="full-field">Descrição / observações<textarea name="description" rows="3" maxlength="4000"></textarea></label><button>Salvar evento</button></form></section>'
    selected = ""
    selected_id=(query.get("id") or [""])[0]
    if selected_id.isdigit():
        row=db.execute("SELECT * FROM local_calendar_events WHERE id=? AND archived=0",(int(selected_id),)).fetchone()
        if row:
            checklist=db.execute("SELECT id,title,done FROM local_calendar_checklist WHERE event_id=? ORDER BY id",(row["id"],)).fetchall()
            items="".join(f'<li><form method="post" action="/rotinas/calendario/check/{item["id"]}">{_csrf(user)}<button>{"☑" if item["done"] else "☐"}</button> {esc(item["title"])}</form></li>' for item in checklist)
            history=db.execute("SELECT action,detail,created_at FROM local_calendar_history WHERE event_id=? ORDER BY id DESC LIMIT 40",(row["id"],)).fetchall()
            source=db.execute("SELECT source_id FROM local_calendar_source_map WHERE local_event_id=?",(row["id"],)).fetchone()
            legacy_history=[]
            if source:
                for old in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calendar_history'"):
                    data=json.loads(old[0])
                    if str(data.get("event_id"))==source["source_id"]: legacy_history.append(data)
            audit="".join(f'<li><time>{esc(item["created_at"])}</time> · {esc(item["action"])} · {esc(item["detail"])}</li>' for item in history)+"".join(f'<li><time>{esc(item.get("created_at"))}</time> · V2 {esc(item.get("action"))} · {esc(item.get("detail") or item.get("title") or "Registro")}</li>' for item in legacy_history)
            selected=f'<section class="card"><h2>Editar evento</h2><form method="post" action="/rotinas/calendario/{row["id"]}/editar" class="fields">{_csrf(user)}<label>Título<input name="title" value="{esc(row["title"])}" required></label><label>Data<input name="due_date" type="date" value="{esc(row["due_date"])}" required></label><label>Hora<input name="starts_at" type="time" value="{esc((row["starts_at"] or "")[11:16])}"></label><label>Categoria<input name="category" value="{esc(row["category"])}"></label><label>Prioridade<select name="priority">'+"".join(f'<option{" selected" if p==row["priority"] else ""}>{p}</option>' for p in ("BAIXA","NORMAL","ALTA","URGENTE"))+f'</select></label><label class="full-field">Descrição<textarea name="description">{esc(row["description"] or row["notes"])}</textarea></label><button>Salvar alterações</button></form><form method="post" action="/rotinas/calendario/{row["id"]}/concluir">{_csrf(user)}<button>{"Reabrir" if row["status"]=="CONCLUIDO" else "Concluir"}</button></form><h3>Checklist</h3><ul>{items}</ul><form method="post" action="/rotinas/calendario/{row["id"]}/checklist" class="row">{_csrf(user)}<input name="title" required maxlength="180" placeholder="Novo item"><button>Adicionar</button></form><form method="post" action="/rotinas/calendario/{row["id"]}/arquivar">{_csrf(user)}<button>Arquivar evento</button></form><details><summary>Histórico ({len(history)+len(legacy_history)})</summary><ul class="history">{audit or "<li>Sem alterações registradas.</li>"}</ul></details></section>'
    bank_day=(query.get("bank_day") or [date.today().isoformat()])[0]
    if not self_valid_date(bank_day): bank_day=date.today().isoformat()
    bank_scope=(query.get("scope_type") or ["GENERAL"])[0]
    bank_key=(query.get("scope_key") or [""])[0][:100]
    if bank_scope not in ("GENERAL","CLIENT","UNIT"): bank_scope="GENERAL"
    relation = db.execute("SELECT state,note FROM local_bank_relations WHERE day=? AND scope_type=? AND scope_key=?",(bank_day,bank_scope,bank_key if bank_scope!="GENERAL" else "")).fetchone()
    bank_history=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days' ORDER BY source_id LIMIT 100").fetchall()
    holiday_history=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calendar_holidays' ORDER BY source_id LIMIT 100").fetchall()
    state=relation["state"] if relation else ""
    bank=f'<section class="card"><h2>Relação bancária — {date.fromisoformat(bank_day):%d/%m/%Y}</h2><form method="post" action="/rotinas/calendario/banco" class="row">{_csrf(user)}<label>Data<input type="date" name="day" value="{bank_day}"></label><select name="scope_type">'+"".join(f'<option value="{v}"{" selected" if v==bank_scope else ""}>{label}</option>' for v,label in (("GENERAL","Geral"),("CLIENT","Empresa"),("UNIT","Unidade")))+f'</select><input name="scope_key" value="{esc(bank_key)}" maxlength="100" placeholder="ID da empresa/unidade"><select name="state"><option value="">Sem marcação</option>'+"".join(f'<option value="{val}"{" selected" if state==val else ""}>{label}</option>' for val,label in (("YES","Sim / útil"),("NO","Não"),("OTHER","Outro")))+f'</select><input name="note" value="{esc(relation["note"] if relation else "")}" placeholder="Observação"><button>Salvar</button></form></section>'
    snapshot=_source_snapshot(db,"calendar_events",("title","due_at","status"))
    bank_old="".join(f'<tr><td>{esc(json.loads(row[0]).get("day"))}</td><td>{esc(json.loads(row[0]).get("scope_type"))}</td><td>{esc(json.loads(row[0]).get("state"))}</td><td>{esc(json.loads(row[0]).get("note"))}</td></tr>' for row in bank_history)
    holiday_old="".join(f'<tr><td>{esc(json.loads(row[0]).get("day"))}</td><td>{esc(json.loads(row[0]).get("name"))}</td><td>{esc(json.loads(row[0]).get("state") or json.loads(row[0]).get("city"))}</td></tr>' for row in holiday_history)
    extras=f'<details class="card"><summary>Relação bancária importada ({len(bank_history)} dias)</summary><div class="table-wrap"><table><thead><tr><th>Data</th><th>Escopo</th><th>Status</th><th>Observação</th></tr></thead><tbody>{bank_old}</tbody></table></div></details><details class="card"><summary>Feriados importados ({len(holiday_history)})</summary><div class="table-wrap"><table><thead><tr><th>Data</th><th>Feriado</th><th>Localidade</th></tr></thead><tbody>{holiday_old}</tbody></table></div></details>'
    return f'<div class="calendar-toolbar"><a href="/rotinas/calendario?month={prev_month}">← Mês anterior</a><strong>{first:%m/%Y}</strong><a href="/rotinas/calendario?month={next_month:%Y-%m}">Próximo mês →</a><div><a href="?mode=month&month={first:%Y-%m}">Mês</a> · <a href="?mode=week&month={first:%Y-%m}&week={date.today().isoformat()}">Semana</a> · <a href="?mode=agenda&month={first:%Y-%m}">Agenda</a> · <a href="?mode=upcoming&month={first:%Y-%m}">Próximos 14 dias</a></div></div><section class="card"><h2>Eventos — {len(events)}</h2>{event_view}</section>{selected}{new_event}{bank}{archived_section}{snapshot}{extras}'


def self_valid_date(value):
    try: return date.fromisoformat(value).isoformat()==value
    except (ValueError,TypeError): return False


def render_lots(db, user, query):
    plans=db.execute("SELECT id,title,inputs_json,result_json,pinned,created_at FROM local_benefit_plans WHERE created_by=? ORDER BY pinned DESC,updated_at DESC",(user["id"],)).fetchall()
    saved="".join(f'<details><summary>{"★ " if p["pinned"] else ""}{esc(p["title"])} · {esc(p["created_at"])}</summary><pre>{esc(json.dumps(json.loads(p["result_json"]),ensure_ascii=False,indent=2))}</pre><form method="post" action="/rotinas/planejamento-lotes/{p["id"]}/pin">{_csrf(user)}<button>Alternar fixação</button></form></details>' for p in plans) or "<p>Nenhum planejamento salvo ainda.</p>"
    rows=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='benefit_plans' ORDER BY source_id LIMIT 100").fetchall()
    archive="".join(f'<details><summary>{esc((json.loads(r[0]).get("employee_name") or json.loads(r[0]).get("title") or "Plano importado"))} · {esc(json.loads(r[0]).get("admission_date") or "")}</summary><pre>{esc(json.dumps(json.loads(r[0]),ensure_ascii=False,indent=2))}</pre></details>' for r in rows)
    proposal=""
    selected=(query.get("id") or [""])[0]
    if selected.isdigit():
        plan=db.execute("SELECT title,inputs_json,result_json FROM local_benefit_plans WHERE id=? AND created_by=?",(int(selected),user["id"])).fetchone()
        if plan:
            result=json.loads(plan["result_json"])
            lots="".join(f'<tr><td>{lot["number"]}</td><td>{esc(lot["from"])}</td><td>{esc(lot["to"])}</td><td>{esc(lot["pay_date"])}</td><td>{lot["days"]}</td><td>{lot["rides"]}</td><td>R$ {lot["amount_cents"]/100:.2f}</td></tr>' for lot in result["lots"])
            editor="".join(f'<tr><td>{lot["number"]}<input type="hidden" name="number_{lot["number"]}" value="{lot["number"]}"></td><td><input type="date" name="from_{lot["number"]}" value="{lot["from"]}"></td><td><input type="date" name="to_{lot["number"]}" value="{lot["to"]}"></td><td><input type="date" name="pay_date_{lot["number"]}" value="{lot["pay_date"]}"></td></tr>' for lot in result["lots"])
            proposal=f'<section class="card"><h2>{esc(plan["title"])}</h2><p>Corte da folha: <strong>{esc(result["cutoff_date"])}</strong> · Pagamento normal estimado: <strong>{esc(result["effective_payroll_date"])}</strong></p><div class="table-wrap"><table><thead><tr><th>Lote</th><th>De</th><th>Até</th><th>Pagamento</th><th>Dias</th><th>Utilizações</th><th>Valor</th></tr></thead><tbody>{lots}</tbody></table></div><p>Total: <strong>R$ {result["total_cents"]/100:.2f}</strong></p><h3>Ajustar e validar datas</h3><form method="post" action="/rotinas/planejamento-lotes/{selected}/validar">{_csrf(user)}<div class="table-wrap"><table><thead><tr><th>Lote</th><th>De</th><th>Até</th><th>Pagamento</th></tr></thead><tbody>{editor}</tbody></table></div><button>Validar alteração</button></form></section>'
    return f'{proposal}<section class="card"><h2>Propor lotes de benefício</h2><p>Prévia calculada localmente; confirme datas, relação bancária e escala antes de efetivar.</p><form method="post" action="/rotinas/planejamento-lotes" class="fields">{_csrf(user)}<label>Nome do planejamento<input name="title" maxlength="120" required value="Plano de benefício"></label><label>Admissão<input name="admission" type="date" required></label><label>Dia de pagamento normal<select name="payroll_day"><option value="5">Dia 5</option><option value="30">Dia 30</option></select></label><label>Quantidade de lotes<select name="lot_count"><option>1</option><option selected>2</option><option>3</option></select></label><label>Valor unitário (R$)<input name="unit_value" value="0,00" required></label><label>Utilizações por dia<input name="uses_per_day" type="number" min="1" value="2"></label><label>Escala<select name="schedule"><option value="">Dias corridos</option><option value="WEEKDAYS_5X2">5x2</option><option value="ROTATING_6X1">6x1</option><option value="ROTATING_6X2">6x2</option></select></label><label>Início da escala<input name="schedule_start" type="date"></label><label>Empresa (opcional)<input name="client_id" placeholder="ID local/importado"></label><label>Unidade (opcional)<input name="unit_id" placeholder="ID local/importado"></label><label><input type="checkbox" name="consider_bank" value="yes"> Considerar relação bancária</label><button>Calcular e salvar</button></form></section><section class="card"><h2>Planejamentos locais</h2>{saved}</section><details class="card"><summary>Planos V2 preservados ({len(rows)}, somente leitura)</summary>{archive or "<p>Nenhum snapshot.</p>"}</details>'


def render_calculators(db, user, query, result_data=None, error=""):
    calc_id=(query.get("calc") or [""])[0]
    saved_id=(query.get("id") or [""])[0]
    if result_data is None and saved_id.isdigit():
        saved_result=db.execute("SELECT calculator_id,inputs_json,result_json FROM local_calculations WHERE id=? AND created_by=?",(int(saved_id),user["id"])).fetchone()
        if saved_result:
            result_data=(saved_result["calculator_id"],json.loads(saved_result["result_json"]),json.loads(saved_result["inputs_json"]))
    if result_data is not None:
        calc_id=result_data[0]
    chosen=next((item for item in CALCULATORS if item[0]==calc_id),CALCULATORS[0])
    menu="".join(f'<option value="{key}"{" selected" if key==chosen[0] else ""}>{esc(title)}</option>' for key,title,_ in CALCULATORS)
    fields=[]
    for key,label,default in chosen[2]:
        required=" required" if not default else ""
        if key in ("applyReduction","includeAbono","includeThirdOnAbono"):
            selected_yes=str(default).casefold() in ("sim","yes","true","1")
            control=f'<select name="{key}"><option value="0"{" selected" if not selected_yes else ""}>Não</option><option value="1"{" selected" if selected_yes else ""}>Sim</option></select>'
        elif key in ("nightPremiumBasis","divideBy","installment","deductionMethod","mode"):
            options={"nightPremiumBasis":[("NORMAL","Hora normal"),("OVERTIME_HOUR","Hora extra")],"divideBy":[("ALL_MONTHS","Todos os meses"),("INFORMED_MONTHS","Meses informados")],"installment":[("TOTAL","Total"),("FIRST","1ª parcela"),("SECOND","2ª parcela")],"deductionMethod":[("AUTO","Automático"),("LEGAL","Deduções legais"),("SIMPLIFIED","Simplificado")],"mode":[("MONTHLY","Mensal"),("THIRTEENTH","13º salário")]}[key]
            control=f'<select name="{key}">'+"".join(f'<option value="{value}"{" selected" if value==default else ""}>{label}</option>' for value,label in options)+'</select>'
        elif key == "city":
            locations=db.execute("SELECT DISTINCT state,city FROM local_calendar_holidays WHERE city<>'' ORDER BY state,city").fetchall()
            options="".join(f'<option value="{esc((row["state"] or "")+"|"+row["city"])}">{esc(row["city"]+((" — "+row["state"]) if row["state"] else ""))}</option>' for row in locations)
            control=f'<select name="city"><option value="">Feriados informados manualmente</option>{options}</select>'
        elif key in ("startDate","endDate"):
            control=f'<input type="date" name="{key}" value="{esc(default)}"{required}>'
        elif key == "competence":
            control=f'<input type="month" name="{key}" value="{esc(default)}"{required}>'
        elif key in ("start","end"):
            control=f'<input type="time" name="{key}" value="{esc(default)}"{required}>'
        elif key in ("values","earnings","deductions","absenceDates","holidays"):
            control=f'<textarea name="{key}" rows="2" placeholder="Separe por ponto e vírgula"{required}>{esc(default)}</textarea>'
        else:
            control=f'<input name="{key}" value="{esc(default)}"{required}>'
        fields.append(f'<label>{esc(label)}{control}</label>')
    fields="".join(fields)
    output=""
    if result_data:
        result_obj=result_data[1]
        memory="".join(f"<li>{esc(line)}</li>" for line in result_obj.get("memory",[]))
        output=f'<section class="card calculator-result"><h2>Resultado: R$ {esc(result_obj["value"])}</h2><ul>{memory}</ul><form method="post" action="/rotinas/calculadoras/salvar">{_csrf(user)}<input type="hidden" name="calculator_id" value="{esc(calc_id)}"><input type="hidden" name="title" value="{esc(chosen[1])}"><input type="hidden" name="inputs_json" value="{esc(json.dumps(result_data[2],ensure_ascii=False))}"><input type="hidden" name="result_json" value="{esc(json.dumps(result_obj,ensure_ascii=False))}"><button>Salvar cálculo</button></form></section>'
    saved=db.execute("SELECT id,title,calculator_id,result_json,pinned,expires_at FROM local_calculations WHERE created_by=? AND (pinned=1 OR expires_at>=date('now')) ORDER BY pinned DESC,created_at DESC LIMIT 100",(user["id"],)).fetchall()
    saved_html="".join(f'<li><a href="/rotinas/calculadoras?calc={quote(row["calculator_id"])}&id={row["id"]}">{"★ " if row["pinned"] else ""}{esc(row["title"])} — R$ {esc(json.loads(row["result_json"]).get("value","—"))}</a><form method="post" action="/rotinas/calculadoras/{row["id"]}/pin">{_csrf(user)}<button>Fixar / desafixar</button></form></li>' for row in saved) or "<li>Nenhum cálculo salvo.</li>"
    old_rows=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calculator_saves' ORDER BY source_id LIMIT 100").fetchall()
    old_calc="".join(f'<details><summary>{esc((json.loads(row[0]).get("title") or json.loads(row[0]).get("calculator_name") or "Cálculo V2"))} · {esc(json.loads(row[0]).get("created_at") or "")}</summary><pre>{esc(json.dumps({"inputs":json.loads(row[0]).get("inputs"),"result":json.loads(row[0]).get("result")},ensure_ascii=False,indent=2))}</pre></details>' for row in old_rows)
    old_section=f'<details class="card"><summary>Cálculos importados da V2 ({len(old_rows)}, somente leitura)</summary>{old_calc or "<p>Nenhum registro importado.</p>"}</details>'
    return f'<section class="card"><h2>Calculadoras DP</h2><form method="get" class="row"><label>Calculadora<select name="calc">{menu}</select></label><button>Abrir</button></form><form method="post" action="/rotinas/calculadoras" class="fields">{_csrf(user)}<input type="hidden" name="action" value="calculate"><input type="hidden" name="calculator_id" value="{esc(chosen[0])}">{fields}<button>Calcular</button></form>{f"<p class=error>{esc(error)}</p>" if error else ""}</section>{output}<section class="card"><h2>Meus cálculos</h2><ul class="recent">{saved_html}</ul></section>{old_section}<p class="notice">Estimativas locais alinhadas à referência V2 2.0.158. Valide os parâmetros de folha, convenção coletiva e competência antes de processar pagamentos.</p>'
