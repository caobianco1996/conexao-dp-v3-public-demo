"use client";

import { Fragment, useCallback, useEffect, useState } from "react";

const EMPTY_MODEL = { id: "", title: "", category: "", body: "", tagsText: "", clientId: "" };
const newRequestKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const dateTime = value => value ? new Date(value).toLocaleString("pt-BR") : "—";

function RichText({ value = "" }) {
  const parts = String(value).split(/(https?:\/\/[^\s)]+|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return <div className="message-rich-text">{parts.map((part, index) => {
    if (/^https?:\/\//i.test(part)) return <a key={index} href={part} target="_blank" rel="noopener noreferrer">{part}</a>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*")) return <strong key={index}>{part.slice(1, -1)}</strong>;
    return <Fragment key={index}>{part.split("\n").map((line, lineIndex) => <Fragment key={lineIndex}>{lineIndex > 0 && <br/>}{line}</Fragment>)}</Fragment>;
  })}</div>;
}

function Dialog({ title, children, onClose, wide = false }) {
  useEffect(() => {
    const onKeyDown = event => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
  return <div className="messages-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={`messages-dialog card${wide ? " is-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
      <header className="messages-dialog-head"><div><span className="eyebrow">Central DP</span><h2>{title}</h2></div><button type="button" className="btn btn-outline-secondary btn-sm" onClick={onClose}>Fechar</button></header>
      {children}
    </section>
  </div>;
}

export default function MessagesPage() {
  const [mode, setMode] = useState("models");
  const [scope, setScope] = useState("sent");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [alertDirectory, setAlertDirectory] = useState(null);
  const [busy, setBusy] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dialog, setDialog] = useState(null);
  const [opened, setOpened] = useState("");
  const [modelDraft, setModelDraft] = useState(EMPTY_MODEL);
  const [alertDraft, setAlertDraft] = useState({ title: "", body: "", audienceType: "ALL", recipientIds: [], requestKey: newRequestKey() });

  const load = useCallback(async (signal) => {
    const params = new URLSearchParams({ mode });
    if (mode === "models") {
      params.set("q", query);
      params.set("category", category);
      params.set("page", String(page));
    } else params.set("scope", scope);
    const response = await fetch(`/api/ui/messages?${params}`, { cache: "no-store", signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível carregar as mensagens.");
    setData(payload);
  }, [mode, query, category, page, scope]);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError("");
    const timer = setTimeout(() => load(controller.signal).catch(reason => {
      if (reason.name !== "AbortError") setError(reason.message || "Não foi possível carregar as mensagens.");
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); }), mode === "models" ? 160 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [load, mode]);

  const perform = useCallback(async (payload) => {
    setSaving(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ui/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, csrf: data?.csrf || "" }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível concluir a ação.");
      setNotice(result.message || "Alteração salva.");
      return result;
    } catch (reason) {
      setError(reason.message || "Não foi possível concluir a ação.");
      return null;
    } finally { setSaving(false); }
  }, [data?.csrf]);

  const openNewModel = () => { setModelDraft({ ...EMPTY_MODEL }); setDialog({ kind: "model", editing: false }); setError(""); };
  const openEditModel = item => {
    setModelDraft({ id: item.id, title: item.title || "", category: item.category || "", body: item.body || "", tagsText: (item.tags || []).join(", "), clientId: item.client_id || "" });
    setDialog({ kind: "model", editing: true }); setError("");
  };
  const openAlert = (prefill = {}) => {
    setAlertDraft({ title: prefill.title || "", body: prefill.body || "", audienceType: "ALL", recipientIds: [], requestKey: newRequestKey() });
    setDialog({ kind: "alert" }); setError("");
    if (!data?.users) fetch("/api/ui/messages?mode=alerts&scope=sent", { cache: "no-store" }).then(async response => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Não foi possível carregar as pessoas ativas.");
      setAlertDirectory(payload);
    }).catch(reason => setError(reason.message || "Não foi possível carregar as pessoas ativas."));
  };
  const closeDialog = useCallback(() => { if (!saving) { setDialog(null); setError(""); } }, [saving]);
  const saveModel = async event => {
    event.preventDefault();
    const result = await perform({ action: dialog.editing ? "update_model" : "create_model", id: modelDraft.id,
      title: modelDraft.title, category: modelDraft.category, body: modelDraft.body,
      clientId: modelDraft.clientId, tags: modelDraft.tagsText.split(",").map(tag => tag.trim()).filter(Boolean) });
    if (result) { setDialog(null); setPage(1); await load(); }
  };
  const archiveModel = async item => {
    if (!window.confirm(`Arquivar “${item.title}”? O modelo sairá da lista, mas o conteúdo e o histórico serão preservados.`)) return;
    const result = await perform({ action: "archive_model", id: item.id });
    if (result) await load();
  };
  const sendAlert = async event => {
    event.preventDefault();
    const audience = alertDraft.audienceType;
    const audienceData = data?.users ? data : alertDirectory;
    const count = audience === "ALL" ? audienceData?.activeUserCount || 0 : alertDraft.recipientIds.length;
    const audienceDescription = audience === "ALL" ? `todas as ${count} contas ativas desta instalação, incluindo você se sua conta estiver ativa` : `${count} pessoa(s) selecionada(s)`;
    if ((audience === "ALL" || audience === "GROUP") && !window.confirm(`Confirmar envio do alerta “${alertDraft.title}” para ${audienceDescription}?`)) return;
    const result = await perform({ action: "send_alert", ...alertDraft });
    if (result) { setDialog(null); setMode("alerts"); setScope("sent"); setNotice(result.message); setAlertDraft(current => ({ ...current, requestKey: newRequestKey() })); }
  };
  const toggleRecipient = id => setAlertDraft(current => ({ ...current, recipientIds: current.recipientIds.includes(String(id)) ? current.recipientIds.filter(value => value !== String(id)) : [...current.recipientIds, String(id)] }));
  const copyMessage = async item => {
    try {
      await navigator.clipboard.writeText(item.body || "");
      setNotice("Mensagem copiada."); setError("");
    } catch {
      setError("Não foi possível copiar. Abra o texto da mensagem, selecione o conteúdo e copie manualmente.");
      setOpened(item.id);
    }
  };
  const markRead = async item => { const result = await perform({ action: "mark_read", id: item.id }); if (result) await load(); };
  const archiveAlert = async item => {
    const message = scope === "inbox"
      ? "Arquivar este alerta só o remove da sua caixa. Os outros destinatários manterão suas cópias."
      : "Arquivar este alerta só o remove dos seus enviados. As cópias dos destinatários e os recibos serão mantidos.";
    if (!window.confirm(message)) return;
    const result = await perform({ action: "archive_alert", id: item.id, scope });
    if (result) await load();
  };

  const items = data?.items || [];
  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / (data?.pageSize || 50)));
  const audienceData = data?.users ? data : alertDirectory;
  const recipientCount = alertDraft.audienceType === "ALL" ? (audienceData?.activeUserCount || 0) : alertDraft.recipientIds.length;
  const models = mode === "models";

  return <>
    
    <section className="container-fluid page-content messages-page">
      <div className="messages-page-head"><p className="text-secondary">Consulte modelos prontos ou envie alertas aos integrantes do ambiente.</p>
        {models ? <button type="button" className="btn btn-primary btn-sm" onClick={openNewModel} disabled={!data?.canEdit}>Nova mensagem</button>
          : <button type="button" className="btn btn-primary btn-sm" onClick={() => openAlert()} disabled={!data?.canCreateAlert}>Criar alerta</button>}
      </div>
      {error && !dialog && <div className="alert alert-warning" role="alert">{error} <button type="button" className="btn btn-link btn-sm" onClick={() => { setError(""); load().catch(reason => setError(reason.message)); }}>Tentar novamente</button></div>}
      {notice && <div className="alert alert-success messages-notice" role="status" aria-live="polite"><span>{notice}</span><button type="button" aria-label="Fechar aviso" onClick={() => setNotice("")}>×</button></div>}
      <div className="btn-group messages-mode" role="group" aria-label="Mensagens e alertas">
        <button type="button" className={`btn ${models ? "btn-primary" : "btn-outline-primary"}`} aria-pressed={models} onClick={() => { setMode("models"); setOpened(""); setError(""); }}>Modelos rápidos</button>
        <button type="button" className={`btn ${!models ? "btn-primary" : "btn-outline-primary"}`} aria-pressed={!models} onClick={() => { setMode("alerts"); setScope("sent"); setOpened(""); setError(""); }}>Alertas</button>
      </div>

      {!models && <div className="btn-group messages-scope" role="group" aria-label="Histórico de alertas">
        <button className={`btn ${scope === "sent" ? "btn-primary" : "btn-outline-primary"}`} aria-pressed={scope === "sent"} onClick={() => { setScope("sent"); setOpened(""); }}>Enviados</button>
        <button className={`btn ${scope === "inbox" ? "btn-primary" : "btn-outline-primary"}`} aria-pressed={scope === "inbox"} onClick={() => { setScope("inbox"); setOpened(""); }}>Recebidos</button>
      </div>}

      {models && <div className="card messages-toolbar">
        <label className="messages-search">Pesquisar título, conteúdo ou palavra-chave<input className="form-control" type="search" value={query} placeholder="Pesquisar título, conteúdo ou palavra-chave" onChange={event => { setQuery(event.target.value); setPage(1); }} /></label>
        <label className="messages-category">Categoria<select className="form-select form-select-sm" value={category} onChange={event => { setCategory(event.target.value); setPage(1); }}><option value="">Todos</option>{(data?.categories || []).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      </div>}

      {busy && !data && <p className="text-secondary" role="status">Carregando {models ? "modelos" : "alertas"}…</p>}
      {error && !data && <div className="alert alert-warning" role="alert">{error}</div>}
      {models && !busy && !error && items.length === 0 && <div className="card messages-empty"><strong>Nenhum modelo encontrado.</strong><span>{query || category ? "Ajuste a pesquisa ou o filtro de categoria." : "Crie uma mensagem reutilizável para começar."}</span></div>}
      {!models && !busy && !error && items.length === 0 && <div className="card messages-empty"><strong>{scope === "sent" ? "Você ainda não enviou alertas." : "Você ainda não recebeu alertas."}</strong><span>{scope === "sent" ? "Quando enviar um alerta interno, ele aparecerá aqui com os recibos de leitura." : "Alertas direcionados a você aparecerão nesta caixa."}</span></div>}

      {items.length > 0 && <div className="messages-list">
        {items.map(item => {
          const isOpen = opened === item.id;
          if (models) return <article className="card message-card" key={item.id} onClick={() => setDialog({ kind: "view", item })} tabIndex={0} aria-label={`Abrir a mensagem ${item.title}`} onKeyDown={event => { if ((event.key === "Enter" || event.key === " ") && event.target === event.currentTarget) { event.preventDefault(); setDialog({ kind: "view", item }); } }}>
            <div className="message-card-copy"><span className="eyebrow">{item.category || "Central DP"}</span><h2>{item.title}</h2>
              <div className="message-preview is-expanded"><RichText value={item.body}/></div>
              {(item.tags || []).length > 0 && <div className="message-tags">{item.tags.map(tag => <span className="badge text-bg-light" key={tag}>{tag}</span>)}</div>}
              <small>Atualizada em {dateTime(item.updated_at)}{item.client_name ? ` · ${item.client_name}` : ""}</small>
            </div>
            <div className="message-actions" onClick={event => event.stopPropagation()}>
              <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setDialog({ kind: "view", item })}>Abrir</button>
              <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => copyMessage(item)}>Copiar</button>
              <button type="button" className="btn btn-outline-primary btn-sm" disabled={!data?.canCreateAlert} onClick={() => openAlert(item)}>Enviar como alerta</button>
              {data?.canEdit && <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => openEditModel(item)}>Editar</button>}
              {data?.canArchiveModels && <button type="button" className="btn btn-outline-danger btn-sm" onClick={() => archiveModel(item)}>Arquivar</button>}
            </div>
            {error && opened === item.id && <div className="message-inline-error" role="alert">{error}<textarea readOnly aria-label="Texto para cópia manual" value={item.body} onFocus={event => event.target.select()} /></div>}
          </article>;

          return <article className={`card message-card alert-card${isOpen ? " is-open" : ""}${scope === "inbox" && !item.is_read ? " is-unread" : ""}`} key={item.id}>
            <div className="message-card-copy"><div className="alert-card-labels"><span className="eyebrow">{scope === "sent" ? "Enviado" : `De ${item.sender_name || "Central DP"}`}</span>{scope === "inbox" && <span className={`message-read-state ${item.is_read ? "" : "is-unread"}`}>{item.is_read ? "Lido" : "Não lido"}</span>}{item.imported_from_v2 && <span className="message-read-state">Histórico V2</span>}</div>
              <h2>{item.title}</h2><div className={isOpen ? "message-preview is-expanded" : "message-preview"}>{isOpen ? <RichText value={item.body}/> : String(item.body || "").slice(0, 320)}{!isOpen && item.body?.length > 320 ? "…" : ""}</div>
              <small>{dateTime(item.created_at)}{scope === "inbox" && item.is_read ? ` · Lido em ${dateTime(item.read_at)}` : ""}</small>
              {scope === "sent" && <div className="alert-receipt-summary"><strong>{item.read_count || 0} de {item.recipient_count || 0} leram</strong>{!item.imported_from_v2 && (item.recipients || []).length > 0 && <details><summary>Ver destinatários e horários</summary><ul>{item.recipients.map((recipient, index) => <li key={`${recipient.name}-${index}`}><span>{recipient.name}</span><small>{recipient.read_at ? `Lido em ${dateTime(recipient.read_at)}` : "Aguardando leitura"}</small></li>)}</ul></details>}{item.imported_from_v2 && <small className="message-import-note">Origem ou contas destinatárias da V2 não puderam ser vinculadas a usuários desta instalação. Os recibos antigos estão preservados e aparecem somente como histórico.</small>}</div>}
            </div>
            <div className="message-actions">
              <button type="button" className="btn btn-outline-primary btn-sm" aria-expanded={isOpen} onClick={() => setOpened(isOpen ? "" : item.id)}>{isOpen ? "Recolher" : "Abrir"}</button>
              {scope === "inbox" && !item.is_read && !item.imported_from_v2 && <button type="button" className="btn btn-primary btn-sm" disabled={saving} onClick={() => markRead(item)}>Marcar como lido</button>}
              {!item.imported_from_v2 && <button type="button" className="btn btn-outline-secondary btn-sm" disabled={saving} onClick={() => archiveAlert(item)}>Arquivar</button>}
            </div>
          </article>;
        })}
      </div>}

      {models && pageCount > 1 && <nav className="messages-pagination" aria-label="Paginação de modelos"><button className="btn btn-outline-primary btn-sm" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Anterior</button><span>Página {page} de {pageCount} · {data?.total || 0} modelos</span><button className="btn btn-outline-primary btn-sm" disabled={page >= pageCount} onClick={() => setPage(value => value + 1)}>Próxima</button></nav>}

      <footer className="messages-footer">Desenvolvido por <strong>Caio Caobianco</strong></footer>
    </section>

    {dialog?.kind === "view" && <Dialog title={dialog.item.title} onClose={closeDialog} wide>
      <div className="message-view-content"><div className="message-meta"><span>{dialog.item.category || "Central DP"}</span>{dialog.item.client_name && <span>{dialog.item.client_name}</span>}</div><RichText value={dialog.item.body}/>{(dialog.item.tags || []).length > 0 && <div className="message-tags">{dialog.item.tags.map(tag => <span className="badge text-bg-light" key={tag}>{tag}</span>)}</div>}<small className="text-secondary">Atualizada em {dateTime(dialog.item.updated_at)}</small><div className="messages-dialog-actions"><button type="button" className="btn btn-outline-primary" onClick={() => copyMessage(dialog.item)}>Copiar</button><button type="button" className="btn btn-outline-primary" disabled={!data?.canCreateAlert} onClick={() => openAlert(dialog.item)}>Enviar como alerta</button>{data?.canEdit && <button type="button" className="btn btn-outline-primary" onClick={() => openEditModel(dialog.item)}>Editar</button>}</div></div>
    </Dialog>}

    {dialog?.kind === "model" && <Dialog title={dialog.editing ? "Editar mensagem" : "Criar mensagem"} onClose={closeDialog} wide>
      <form className="messages-form" onSubmit={saveModel}>
        <label>Título<input autoFocus className="form-control" maxLength={200} required value={modelDraft.title} onChange={event => setModelDraft({ ...modelDraft, title: event.target.value })}/></label>
        <div className="messages-form-row"><label>Categoria<input className="form-control" maxLength={80} value={modelDraft.category} onChange={event => setModelDraft({ ...modelDraft, category: event.target.value })}/></label>
          <label>Cliente / empresa (opcional)<select className="form-select" value={modelDraft.clientId} onChange={event => setModelDraft({ ...modelDraft, clientId: event.target.value })}><option value="">Sem vínculo</option>{(data?.clients || []).map(client => <option key={client.id} value={client.id}>{client.name}</option>)}</select></label></div>
        <label>Texto da mensagem<textarea className="form-control" rows={12} maxLength={50000} required value={modelDraft.body} onChange={event => setModelDraft({ ...modelDraft, body: event.target.value })}/></label>
        <label>Tags (separadas por vírgula)<input className="form-control" maxLength={1000} value={modelDraft.tagsText} onChange={event => setModelDraft({ ...modelDraft, tagsText: event.target.value })}/></label>
        {error && <div className="alert alert-warning" role="alert">{error}</div>}
        <div className="messages-dialog-actions"><button type="button" className="btn btn-outline-secondary" onClick={closeDialog} disabled={saving}>Cancelar</button><button className="btn btn-primary" disabled={saving}>{saving ? "Salvando…" : "Salvar mensagem"}</button></div>
      </form>
    </Dialog>}

    {dialog?.kind === "alert" && <Dialog title="Criar alerta" onClose={closeDialog} wide>
      <form className="messages-form" onSubmit={sendAlert}>
        <label>Título do alerta<input autoFocus className="form-control" maxLength={200} required value={alertDraft.title} onChange={event => setAlertDraft({ ...alertDraft, title: event.target.value })}/></label>
        <label>Mensagem do alerta<textarea className="form-control" rows={8} maxLength={10000} required value={alertDraft.body} onChange={event => setAlertDraft({ ...alertDraft, body: event.target.value })}/></label>
        <label>Enviar para<select className="form-select" value={alertDraft.audienceType} onChange={event => setAlertDraft({ ...alertDraft, audienceType: event.target.value, recipientIds: [] })}><option value="ALL">Todas as pessoas ativas</option><option value="INDIVIDUAL">Uma pessoa específica</option><option value="GROUP">Algumas pessoas</option></select></label>
        {alertDraft.audienceType !== "ALL" && <fieldset className="recipient-picker"><legend>{alertDraft.audienceType === "INDIVIDUAL" ? "Selecione uma pessoa" : "Selecione de duas a 500 pessoas"}</legend>{(audienceData?.users || []).map(person => <label key={person.id}><input type="checkbox" checked={alertDraft.recipientIds.includes(String(person.id))} onChange={() => toggleRecipient(person.id)}/><span>{person.name}</span></label>)}{!audienceData && <p className="text-secondary">Carregando contas ativas…</p>}{audienceData?.users?.length === 0 && <p className="text-secondary">Não há outras contas ativas disponíveis.</p>}</fieldset>}
        <div className="alert-send-review" role="status"><strong>Revisão do envio</strong><span>O alerta será enviado para {recipientCount} {recipientCount === 1 ? "pessoa" : "pessoas"}.</span><small>{alertDraft.audienceType === "ALL" ? "Inclui todas as contas ativas desta instalação, inclusive a conta remetente se estiver ativa." : (audienceData?.users || []).filter(person => alertDraft.recipientIds.includes(String(person.id))).map(person => person.name).join(" · ") || "Selecione os destinatários para revisar o envio."}</small></div>
        {error && <div className="alert alert-warning" role="alert">{error}</div>}
        <div className="messages-dialog-actions"><button type="button" className="btn btn-outline-secondary" onClick={closeDialog} disabled={saving}>Cancelar</button><button className="btn btn-primary" disabled={saving || !alertDraft.title.trim() || !alertDraft.body.trim() || recipientCount === 0 || (alertDraft.audienceType === "INDIVIDUAL" && recipientCount !== 1) || (alertDraft.audienceType === "GROUP" && recipientCount < 2)}>{saving ? "Enviando…" : "Enviar alerta"}</button></div>
      </form>
    </Dialog>}
  </>;
}
