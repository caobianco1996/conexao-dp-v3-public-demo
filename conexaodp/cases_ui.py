"""Operational cases UI over the immutable V2 snapshot and local SQLite overlays."""
from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from urllib.parse import quote

from .server import esc


def _create_prefill(db, user, params):
    """Prepare an editable form from an existing activity/procedure, without writes.

    Source labels describe responsibility; they never select an account, date,
    recurrence or company on behalf of the person creating the case.
    """
    from . import sector

    def parameter(key):
        value = params.get(key, [''])
        return str(value[0] if isinstance(value, list) and value else value or '')[:180]

    activity_id, procedure_id = parameter('sector_item'), parameter('from_process')
    if not (activity_id or procedure_id):
        return {}
    result = {'title': '', 'description': '', 'reference': '', 'checklist': '',
              'notice': '', 'error': ''}
    if activity_id and procedure_id:
        result['error'] = 'Escolha uma única origem para preparar o caso.'
        return result
    if activity_id:
        try:
            item = sector.detail(db, activity_id, user)
            if item['kind'] != 'ACTIVITY':
                raise PermissionError()
        except (PermissionError, ValueError):
            result['error'] = 'A atividade não está disponível para preparar este caso.'
            return result
        payload = item['payload']
        lines = [f"Atividade do setor: {item['title']}",
                 f"Origem: {item['sourceSheet']} · {item['sourceRange']}",
                 f"Consultar: /rotinas/setor?kind=ACTIVITY&item={item['id']}"]
        if item.get('responsibleLabels'):
            lines.append('Responsáveis na origem (referência): ' + ', '.join(item['responsibleLabels']))
        if item.get('periodicity'):
            lines.append('Periodicidade de referência: ' + item['periodicity'])
        for note in payload.get('notes', []) if isinstance(payload.get('notes'), list) else []:
            if isinstance(note, str):
                lines.append(note)
        result.update(title=item['title'][:160], description='\n'.join(lines)[:4000],
                      reference=f"Atividade do setor {item['id']}",
                      notice='Revise a atividade e escolha responsável e prazo antes de criar o caso.')
        return result
    from .ui_api import processes_data
    item = next((row for row in processes_data(db, {}, user)['items'] if row['id'] == procedure_id), None)
    if (not item or str(item.get('status') or '').upper() in ('ARCHIVED', 'INACTIVE', 'DELETED')):
        result['error'] = 'O processo não está disponível para preparar este caso.'
        return result
    content = item.get('content') if isinstance(item.get('content'), dict) else {}
    nested = content.get('content') if isinstance(content.get('content'), dict) else {}
    checklist = content.get('checklist') or nested.get('checklist') or item.get('checklist') or []
    checklist_lines = []
    for value in checklist if isinstance(checklist, list) else []:
        required = isinstance(value, dict) and value.get('required') is True
        text = (value.get('title') or value.get('detail') or value.get('name') or '') if isinstance(value, dict) else value
        if isinstance(text, str):
            # Imported line breaks cannot silently create extra checklist tasks.
            text = ' '.join(text.split()).strip().lstrip('*').strip()[:180]
            if text:
                checklist_lines.append(('* ' if required else '') + text)
        if len(checklist_lines) >= 30:
            break
    description = [f"Processo: {item['title']}",
                   f"Consultar: /central-dp/processos?id={quote(procedure_id, safe='')}"]
    objective = content.get('whenToUse') or content.get('objective') or item.get('description')
    if isinstance(objective, str):
        description.append(objective)
    result.update(title=str(item['title'] or '')[:160], description='\n'.join(description)[:4000],
                  reference=('Processo ' + procedure_id.rsplit(':', 1)[-1])[:100],
                  checklist='\n'.join(checklist_lines),
                  notice='Revise as orientações e o checklist. O caso será criado somente ao salvar este formulário.')
    return result


def render(db, user, params):
    scope = "team" if params.get("scope", ["mine"])[0] == "team" else "mine"
    team_access=user["role"] in ("ADMIN","GESTOR")
    if scope=="team" and not team_access: scope="mine"
    requested_view=params.get("view",["active"])[0]
    view = requested_view if requested_view in ("active","done","deleted") and (requested_view!="deleted" or user["role"]=="ADMIN") else "active"
    search = params.get("q", [""])[0][:100].strip()
    selected_status=params.get("status",[""])[0]
    selected_owner=params.get("responsible",[""])[0]
    selected_client=params.get("client",[""])[0]
    selected_priority=params.get("priority",[""])[0]
    selected_unit=params.get("unit",[""])[0]
    selected_process=params.get("process",[""])[0]
    period=params.get("period",["all"])[0]
    competence=params.get("competence",[""])[0]
    due_before=params.get("due_before",[""])[0]
    created_after=params.get("created_after",[""])[0]
    opened = params.get("open", [""])[0]
    users = db.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
    names = {str(x["id"]): x["name"] for x in users}
    clients = {}
    for r in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='clients'"):
        x = json.loads(r["payload"])
        clients[str(x.get("id") or r["source_id"])] = x.get("name", "")
    for r in db.execute("SELECT id,name FROM local_clients ORDER BY name COLLATE NOCASE"):
        clients["local:"+str(r["id"])] = r["name"]
    units={}
    for r in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='units'"):
        x=json.loads(r["payload"]); units[str(x.get("id") or r["source_id"])]=x.get("name") or x.get("title") or str(x.get("id") or r["source_id"])
    procedures={}
    for r in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='procedures'"):
        x=json.loads(r["payload"]); procedures[str(x.get("id") or r["source_id"])]=x.get("title") or str(x.get("id") or r["source_id"])
    checklist = {}
    for r in db.execute("SELECT id,source_case_id,title,done,required FROM local_v2_checklist_items ORDER BY position,id"):
        checklist.setdefault(r["source_case_id"], []).append(r)
    events = {}
    for r in db.execute("SELECT source_case_id,actor_id,action,detail,created_at FROM local_v2_case_events ORDER BY id DESC"):
        events.setdefault(r["source_case_id"], []).append(r)
    for r in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='case_events'"):
        event=json.loads(r["payload"])
        case_key=str(event.get("case_id") or "")
        if case_key:
            events.setdefault(case_key, []).append({"created_at":event.get("created_at") or "", "action":"V2 · "+str(event.get("action") or "Atualização"), "detail":event.get("detail") or "Registro histórico importado"})
    saved=params.get("saved",[""])[0]=="1"
    alert_cards=[]
    alerts = {str(r["source_case_id"]):r for r in db.execute("SELECT source_case_id,acknowledged_at,closed_at FROM local_v2_case_alerts WHERE user_id=?",(user["id"],))}
    today=date.today()
    alert_sources=db.execute("""SELECT v.source_id,v.payload,o.status local_status,o.owner_id,o.due_date local_due,
        o.due_popup_enabled,o.deleted_at FROM v2_source_rows v LEFT JOIN local_v2_case_overrides o ON o.source_case_id=v.source_id
        WHERE v.table_name='cases' AND COALESCE(o.due_popup_enabled,0)=1""").fetchall() if view=="active" else []
    for r in alert_sources:
        x=json.loads(r["payload"]); sid=str(r["source_id"])
        assignment=db.execute("SELECT owner_id FROM local_case_assignments WHERE source_case_id=?",(sid,)).fetchone()
        owner=r["owner_id"] if r["owner_id"] is not None else assignment[0] if assignment else None
        status=r["local_status"] or x.get("status") or "OPEN"
        due=(r["local_due"] or x.get("due_at") or "")[:10]
        if owner!=user["id"] or status in ("DONE","CONCLUIDO","CANCELLED","CANCELADO") or r["deleted_at"] or x.get("deleted_at") or not due:
            continue
        try: due_day=date.fromisoformat(due)
        except ValueError: continue
        remaining=(due_day-today).days
        if remaining>7: continue
        alert=alerts.get(sid); acknowledged=bool(alert and alert["acknowledged_at"]); closed=bool(alert and alert["closed_at"])
        label="Vencido" if remaining<0 else "Vence hoje" if remaining==0 else f"Vence em {remaining} dia(s)"
        key=quote(sid,safe=""); title=esc(x.get("title") or "Caso importado")
        state=f'<strong>{label}</strong> · Prazo {esc(due)} · {title}'
        if closed:
            actions=f'<span>Você fechou o aviso; o caso continua em aberto.</span><form method="post" action="/central-dp/casos/v2/alerta"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><button name="alert_action" value="reopen" class="btn btn-outline-primary btn-sm">Reabrir aviso</button></form>'
        elif acknowledged:
            actions=f'<span>Ciência registrada. O aviso permanece até o caso ser resolvido.</span><form method="post" action="/central-dp/casos/v2/alerta" onsubmit="return confirm(\'Fechar este aviso? O caso continuará aberto e o aviso poderá ser reaberto.\')"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><button name="alert_action" value="close" class="btn btn-outline-secondary btn-sm">Fechar aviso</button></form>'
        else:
            actions=f'<form method="post" action="/central-dp/casos/v2/alerta"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><button name="alert_action" value="acknowledge" class="btn btn-primary btn-sm">Estou ciente</button></form>'
        help_form=f'<form method="post" action="/central-dp/casos/v2/alerta" class="case-alert-help"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><select name="help_to"><option value="">Registrar dependência</option>{"".join(f'<option value="{u["id"]}">{esc(u["name"])}</option>' for u in users if u["id"]!=user["id"])}</select><textarea name="help_message" maxlength="1000" required placeholder="Descreva de quem ou do que depende"></textarea><button name="alert_action" value="help" class="btn btn-outline-primary btn-sm">Pedir ajuda</button></form>'
        alert_cards.append(f'<article class="case-alert" role="alert"><p>{state}</p><div class="case-alert-actions"><a class="btn btn-outline-primary btn-sm" href="?scope={scope}&amp;view={view}&amp;open={key}#caso-{key}">Abrir caso</a>{actions}</div>{help_form}</article>')
    cards = []
    counts = {"mine": 0, "team": 0}
    for r in db.execute("""SELECT v.source_id,v.payload,o.status local_status,o.owner_id,o.priority local_priority,
        o.due_date local_due,o.due_popup_enabled,o.renew_monthly,o.next_owner_id,o.next_due_date,o.deleted_at
        FROM v2_source_rows v LEFT JOIN local_v2_case_overrides o ON o.source_case_id=v.source_id
        WHERE v.table_name='cases' ORDER BY v.imported_at DESC,v.source_id"""):
        x = json.loads(r["payload"])
        sid = str(r["source_id"])
        assignment = db.execute("SELECT owner_id FROM local_case_assignments WHERE source_case_id=?", (sid,)).fetchone()
        owner = str(r["owner_id"] if r["owner_id"] is not None else assignment[0] if assignment else "")
        status = r["local_status"] or x.get("status") or "OPEN"
        deleted=bool(r["deleted_at"] or x.get("deleted_at"))
        if view=="deleted":
            if not deleted: continue
        elif deleted: continue
        done = status in ("DONE", "CONCLUIDO")
        if view!="deleted" and ((view == "done") != done):
            continue
        counts["team"] += 1
        counts["mine"] += owner == str(user["id"])
        if scope == "mine" and owner != str(user["id"]):
            continue
        title = x.get("title") or "Caso importado"
        client = clients.get(str(x.get("client_id") or ""), "")
        description = x.get("description") or ""
        if search and search.casefold() not in f"{title} {description} {client}".casefold():
            continue
        due = (r["local_due"] or x.get("due_at") or "")[:10]
        priority = r["local_priority"] or x.get("priority") or "NORMAL"
        unit_id=str(x.get("unit_id") or "")
        process_id=str(x.get("procedure_id") or "")
        if selected_status and status!=selected_status: continue
        if selected_owner and owner!=selected_owner: continue
        if selected_client and str(x.get("client_id") or "")!=selected_client: continue
        if selected_priority and priority!=selected_priority: continue
        if selected_unit and unit_id!=selected_unit: continue
        if selected_process and process_id!=selected_process: continue
        if period=="until7" and (not due or int(due[8:10])>7): continue
        if period=="month" and competence and (not due or due[:7]!=competence): continue
        if due_before and (not due or due>due_before): continue
        if created_after and str(x.get("created_at") or "")[:10]<created_after: continue
        expanded = sid == opened and not deleted
        key = quote(sid, safe="")
        status_labels = {"OPEN":"Aberto", "IN_PROGRESS":"Em andamento", "WAITING":"Aguardando", "DONE":"Concluído", "CANCELLED":"Cancelado", "REOPENED":"Reaberto"}
        card_toggle_url = f'/central-dp/casos?scope={scope}&amp;view={view}&amp;q={quote(search)}' + ('' if expanded else f'&amp;open={key}#caso-{key}')
        toggle = '' if deleted else f'<a class="btn btn-outline-primary btn-sm" href="{card_toggle_url}">{"Recolher" if expanded else "Abrir"}</a>'
        detail = ""
        if expanded:
            items = checklist.get(sid, [])
            checklist_html = "".join(f'<label class="case-check"><input type="checkbox" name="done_ids" value="{esc(c["id"])}"{" checked" if c["done"] else ""}><span>{esc(c["title"])} <small>{"Obrigatório" if c["required"] else "Opcional"}</small></span></label>' for c in items) or '<p>Nenhum item no checklist.</p>'
            hist = "".join(f'<li><time>{esc(e["created_at"])}</time> · {esc(e["action"])} · {esc(e["detail"])}</li>' for e in events.get(sid, [])) or '<li>Sem alterações locais registradas.</li>'
            status_opts = "".join(f'<option value="{v}"{" selected" if status==v else ""}>{label}</option>' for v,label in (("OPEN","Aberto"),("IN_PROGRESS","Em andamento"),("WAITING","Aguardando"),("DONE","Concluído"),("CANCELLED","Cancelado"),("REOPENED","Reaberto")))
            priority_opts = "".join(f'<option value="{v}"{" selected" if priority==v else ""}>{label}</option>' for v,label in (("LOW","Baixa"),("NORMAL","Normal"),("HIGH","Alta"),("URGENT","Urgente")))
            owner_opts = "".join(f'<option value="{u["id"]}"{" selected" if owner==str(u["id"]) else ""}>{esc(u["name"])}</option>' for u in users)
            next_opts = "".join(f'<option value="{u["id"]}"{" selected" if r["next_owner_id"]==u["id"] else ""}>{esc(u["name"])}</option>' for u in users)
            detail = f'''<div class="case-inline"><p>{esc(description)}</p><form method="post" action="/central-dp/casos/v2/salvar"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><input type="hidden" name="scope" value="{scope}"><input type="hidden" name="view" value="{view}"><div class="case-fields"><label>Situação<select name="status">{status_opts}</select></label><label>Responsável<select name="owner_id"><option value="">Sem responsável</option>{owner_opts}</select></label><label>Prioridade<select name="priority">{priority_opts}</select></label><label>Prazo<input type="date" name="due_date" value="{esc(due)}"></label><label class="case-option"><input type="checkbox" name="popup" value="1"{" checked" if r["due_popup_enabled"] or x.get("due_popup_enabled") else ""}> Avisar em pop-up até 7 dias antes do prazo</label><label class="case-option"><input type="checkbox" name="renew_monthly" value="1"{" checked" if r["renew_monthly"] or x.get("renew_monthly") else ""}> Renovar mensalmente ao concluir</label><label>Próxima pessoa<select name="next_owner_id"><option value="">Concluir sem encaminhar</option>{next_opts}</select></label><label>Prazo da próxima etapa<input type="date" name="next_due_date" value="{esc(r["next_due_date"] or "")}"></label></div><section class="case-checklist"><h3>Checklist <small>{sum(bool(c["done"]) for c in items)}/{len(items)} concluídos</small></h3>{checklist_html}<label>Adicionar item<input name="new_item" maxlength="180" placeholder="Adicionar item ao checklist"></label><label class="case-option"><input type="checkbox" name="new_required" value="1"> Obrigatório</label><details><summary>Histórico</summary><ul class="history">{hist}</ul></details><label>Comentário (opcional)<textarea name="comment" rows="2" maxlength="2000" placeholder="Adicionar ao histórico"></textarea></label><div class="case-footer"><a href="/administracao/arquivos?ownerType=CASE&amp;ownerId={key}">Arquivos do caso</a><div class="case-submit-actions"><button class="btn btn-primary btn-sm">Salvar alterações</button><button class="btn btn-success btn-sm" name="submit_action" value="forward">Concluir e encaminhar</button></div></div></section></form></div>'''
        if user["role"]=="ADMIN":
            archive_label="Restaurar caso" if deleted else "Excluir caso"
            archive_action="restore" if deleted else "delete"
            archive_form=f'<form method="post" action="/central-dp/casos/v2/arquivar" onsubmit="return confirm(\'{"Restaurar" if deleted else "Arquivar"} este caso? O registro e o histórico serão preservados.\')"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="source_case_id" value="{esc(sid)}"><input type="hidden" name="scope" value="{scope}"><button class="btn btn-outline-danger btn-sm" name="archive_action" value="{archive_action}">{archive_label}</button></form>'
        else: archive_form=""
        cards.append(f'<article class="case-card{" is-expanded" if expanded else ""}" id="caso-{key}" data-toggle-url="{card_toggle_url}" tabindex="0" aria-expanded="{"true" if expanded else "false"}" aria-label="{"Recolher" if expanded else "Abrir"} caso: {esc(title)}"><div class="case-summary"><span class="eyebrow">{"Arquivado · " if deleted else ""}{esc(status_labels.get(status,status))}</span><h2><a class="case-title-link" href="?scope={scope}&amp;view={view}&amp;q={quote(search)}&amp;open={key}#caso-{key}">{esc(title)}</a></h2><p>{esc(description)}</p><small>{esc(names.get(owner,"Sem responsável"))} · Prazo: {esc(due or "—")} · {esc(priority)}</small></div><div class="case-card-actions">{toggle}{archive_form}</div>{detail}</article>')
    team_switch=f'<a class="{"selected" if scope=="team" else ""}" href="?scope=team&amp;view={view}">Todos da equipe <b>{counts["team"]}</b></a>' if team_access else '<span class="case-switch-disabled" title="Seu perfil consulta somente casos atribuídos ou criados por você.">Todos da equipe · restrito</span>'
    archive_switch=f'<a class="{"selected" if view=="deleted" else ""}" href="?scope={scope}&amp;view=deleted">Arquivados</a>' if user["role"]=="ADMIN" else ""
    switches = f'''<nav class="case-switches"><span class="case-switch-group"><a class="{"selected" if scope=="mine" else ""}" href="?scope=mine&amp;view={view}">Atribuídos a mim <b>{counts["mine"]}</b></a>{team_switch}</span><span class="case-switch-group"><a class="{"selected" if view=="active" else ""}" href="?scope={scope}&amp;view=active">Em acompanhamento</a><a class="{"selected" if view=="done" else ""}" href="?scope={scope}&amp;view=done">Histórico de concluídos</a></span>{archive_switch}</nav>'''
    status_values=(("","Todas"),("OPEN","Aberto"),("IN_PROGRESS","Em andamento"),("WAITING","Aguardando"),("DONE","Concluído"),("CANCELLED","Cancelado"),("REOPENED","Reaberto"))
    client_options=sorted((key,value) for key,value in clients.items() if value)
    filter_form = f'''<form class="case-filter" method="get"><input type="hidden" name="scope" value="{scope}"><input type="hidden" name="view" value="{view}"><label>Pesquisar título, conteúdo ou palavra-chave<input name="q" value="{esc(search)}" placeholder="Pesquisar título, conteúdo ou palavra-chave"></label><label>Período<select name="period">{"".join(f'<option value="{k}"{" selected" if period==k else ""}>{v}</option>' for k,v in (("until7","Até dia 07"),("all","Todo o período"),("month","Período específico")))}</select></label><label>Competência<input type="month" name="competence" value="{esc(competence)}"></label><label>Situação<select name="status">{"".join(f'<option value="{k}"{" selected" if selected_status==k else ""}>{v}</option>' for k,v in status_values)}</select></label><label>Responsável<select name="responsible"><option value="">Todos</option>{"".join(f'<option value="{u["id"]}"{" selected" if selected_owner==str(u["id"]) else ""}>{esc(u["name"])}</option>' for u in users)}</select></label><label>Cliente<select name="client"><option value="">Todos</option>{"".join(f'<option value="{esc(k)}"{" selected" if selected_client==k else ""}>{esc(v)}</option>' for k,v in client_options)}</select></label><label>Unidade<select name="unit"><option value="">Todas</option>{"".join(f'<option value="{esc(k)}"{" selected" if selected_unit==k else ""}>{esc(v)}</option>' for k,v in sorted(units.items()))}</select></label><label>Prioridade<select name="priority"><option value="">Todas</option>{"".join(f'<option value="{k}"{" selected" if selected_priority==k else ""}>{v}</option>' for k,v in (("LOW","Baixa"),("NORMAL","Normal"),("HIGH","Alta"),("URGENT","Urgente")))}</select></label><label>Processo<select name="process"><option value="">Todos</option>{"".join(f'<option value="{esc(k)}"{" selected" if selected_process==k else ""}>{esc(v)}</option>' for k,v in sorted(procedures.items()))}</select></label><label>Prazo até<input type="date" name="due_before" value="{esc(due_before)}"></label><label>Criado desde<input type="date" name="created_after" value="{esc(created_after)}"></label><button class="btn btn-outline-primary btn-sm">Filtrar</button></form>'''
    local = db.execute("SELECT id,title,reference,description,client_id,unit_id,status,due_date,owner_id,priority,created_by FROM process_cases ORDER BY id DESC LIMIT 100").fetchall()
    local = [r for r in local if scope=="team" or r["owner_id"]==user["id"] or r["created_by"]==user["id"]]
    local_cards="".join(f'<article class="case-card"><div><span class="eyebrow">Local V3 · {esc(r["status"])}</span><h2>{esc(r["title"])}</h2><p>{esc(r["description"] or r["reference"])}</p><small>{esc(clients.get(str(r["client_id"] or ""), "Geral / interno"))} · Prazo: {esc(r["due_date"] or "—")} · {esc(r["priority"] or "NORMAL")}</small></div><a href="/central-dp/casos/{r["id"]}">Abrir</a></article>' for r in local)
    kinds = (("GERAL","Geral"),("ADMISSAO","Admissões"),("RESCISAO","Rescisões"),("PRORROGACAO","Prorrogações"),("AFASTAMENTO","Afastamentos"))
    request_id = secrets.token_urlsafe(24)
    kind_options = "".join(f'<option value="{key}">{label}</option>' for key, label in kinds)
    client_select_options = "".join(f'<option value="{esc(key)}">{esc(label)}</option>' for key, label in client_options)
    unit_select_options = "".join(f'<option value="{esc(key)}">{esc(label)}</option>' for key, label in sorted(units.items()))
    owner_select_options = "".join(f'<option value="{u["id"]}">{esc(u["name"])}</option>' for u in users)
    kind_options = "".join(f'<option value="{key}">{("Geral / interno" if key == "GERAL" else label)}</option>' for key, label in kinds)
    show_create = params.get("create", [""])[0] == "1"
    prefill = _create_prefill(db, user, params) if show_create else {}
    owner_select_options = "".join(f'<option value="{u["id"]}"{" selected" if not prefill and u["id"] == user["id"] else ""}>{esc(u["name"])}</option>' for u in users)
    next_owner_options = "".join(f'<option value="{u["id"]}">{esc(u["name"])}</option>' for u in users)
    create_href = f'?scope={scope}&amp;view={view}&amp;q={quote(search)}&amp;create=1#novo'
    close_href = f'?scope={scope}&amp;view={view}&amp;q={quote(search)}'
    create = f'''<section class="card case-create-inline" id="novo"><header class="case-create-inline-head"><div><span class="eyebrow">NOVA DEMANDA</span><h2>Criar caso</h2></div><a class="btn btn-outline-secondary btn-sm" href="{close_href}">Cancelar</a></header><form method="post" action="/central-dp/casos" class="case-create-form"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="request_id" value="{request_id}"><input type="hidden" name="priority" value="NORMAL"><div class="case-create-fields"><label>Título<input name="title" maxlength="160" value="{esc(prefill.get("title", ""))}" placeholder="Ex.: Conferir fechamento da folha" required autofocus><input type="hidden" name="reference" value="{esc(prefill.get("reference", ""))}"></label><label>Tipo<select name="kind">{kind_options}</select></label><label>Responsável<select name="owner_id"><option value="">Sem responsável</option>{owner_select_options}</select></label><label>Prazo (opcional)<input name="due_date" type="date"></label><label class="case-create-check"><input type="checkbox" name="popup" value="1"> Avisar em pop-up até 7 dias antes do prazo</label><label class="case-create-check"><input type="checkbox" name="renew_monthly" value="1"> Renovar mensalmente ao concluir (exige prazo)</label><label>Próxima pessoa (opcional)<select name="next_owner_id"><option value="">Definir depois</option>{next_owner_options}</select></label><label>Prazo da próxima etapa (opcional)<input name="next_due_date" type="date"></label><label class="case-create-wide">Descrição (opcional)<textarea name="description" rows="2" maxlength="4000">{esc(prefill.get("description", ""))}</textarea></label><label class="case-create-wide">Checklist (opcional, uma tarefa por linha; * no início marca como obrigatória)<textarea name="checklist" rows="3" maxlength="6000" placeholder="Digite uma tarefa por linha; use * para marcar como obrigatória">{esc(prefill.get("checklist", ""))}</textarea></label></div><footer class="case-create-inline-footer"><span>A demanda também será incluída no Calendário DP.</span><button class="btn btn-primary" type="submit">Criar caso</button></footer></form></section>'''
    if prefill.get("notice"):
        create = create.replace('<form method="post"', f'<p class="notice">{esc(prefill["notice"])}</p><form method="post"', 1)
    if prefill.get("error"):
        create = create.replace('<form method="post"', f'<p class="alert alert-warning" role="alert">{esc(prefill["error"])}</p><form method="post"', 1)
    toast='<p class="case-toast" role="status">Salvo com sucesso.</p>' if saved else ''
    create = create.replace('action="/central-dp/casos"', 'action="/central-dp/casos/criar"')
    create_button = f'<a class="btn btn-primary btn-sm" href="{create_href}">Criar caso</a>' if not show_create else ''
    return f'''<div class="workspace cases-workspace"><p class="eyebrow">CENTRAL DP</p><h1>Casos e checklists</h1><p>Demandas, responsáveis, prazos e etapas auditadas.</p>{toast}{''.join(alert_cards)}<div class="case-switch-row">{switches}{create_button}</div>{filter_form}{create if show_create else ""}{''.join(cards) or '<section class="card">Nenhum caso corresponde aos filtros. Confira “Todos da equipe” ou ajuste a busca.</section>'}{local_cards}</div>'''
