"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const weekdays = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const relationStates = [
  ["YES", "Haverá relação bancária", "is-yes"],
  ["NO", "Não haverá relação bancária", "is-no"],
  ["OTHER", "Fim de semana, feriado ou outra ocorrência", "is-other"],
  ["", "Sem marcação", "is-empty"],
];
const pad = value => String(value).padStart(2, "0");
const monthKey = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}`;
const isoDate = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
const localDate = value => new Date(`${value}T12:00:00`);
const shortDate = value => value ? localDate(value).toLocaleDateString("pt-BR") : "";
const holidayDate = value => value ? localDate(value).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "";
const monthLabel = value => localDate(`${value}-01`).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
const post = (csrf, payload) => fetch("/api/ui/calendar", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, ...payload }) });

async function readJson(response) {
  const data = await response.json().catch(() => ({ error: "Resposta inválida do servidor." }));
  if (!response.ok) throw new Error(data.error || "Não foi possível concluir a operação.");
  return data;
}

function Modal({ title, subtitle, onClose, children, wide = false }) {
  useEffect(() => {
    const key = event => event.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return <div className="dp-calendar-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className={`dp-calendar-dialog${wide ? " is-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="dp-calendar-dialog-title">
      <header className="dp-calendar-dialog-head"><div><span className="dp-calendar-eyebrow">CALENDÁRIO DP</span><h2 id="dp-calendar-dialog-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="btn btn-outline-secondary btn-sm" type="button" onClick={onClose} aria-label="Fechar">×</button></header>
      {children}
    </section>
  </div>;
}

export default function CalendarPage() {
  const now = new Date();
  const [month, setMonth] = useState(monthKey(now));
  const [mode, setMode] = useState("month");
  const [layer, setLayer] = useState("events");
  const [clientId, setClientId] = useState("");
  const [mine, setMine] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [monthlyNote, setMonthlyNote] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [reload, setReload] = useState(0);

  const loadUrl = useMemo(() => {
    const params = new URLSearchParams({ month, mode, client: clientId, mine: mine ? "1" : "0", q: query.trim(), category });
    if (dialog?.type === "bank") params.set("day", dialog.date);
    return `/api/ui/calendar?${params.toString()}`;
  }, [month, mode, clientId, mine, query, category, dialog, reload]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetch(loadUrl, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(readJson)
      .then(payload => { setData(payload); setMonthlyNote(payload.monthlyNote?.body || ""); })
      .catch(reason => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [loadUrl]);

  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(""), 4200);
    return () => window.clearTimeout(id);
  }, [notice]);

  const shiftMonth = delta => {
    const current = localDate(`${month}-01`);
    current.setMonth(current.getMonth() + delta);
    setMonth(monthKey(current));
  };
  const save = useCallback(async payload => {
    if (!data?.csrf || busy) return;
    setBusy(true); setError("");
    try {
      const result = await readJson(await post(data.csrf, payload));
      setNotice(result.message || "Alteração salva.");
      setReload(value => value + 1);
      return result;
    } catch (reason) {
      setError(reason.message);
      throw reason;
    } finally { setBusy(false); }
  }, [data, busy]);

  const days = useMemo(() => {
    const first = localDate(`${month}-01`);
    if (mode === "week") {
      first.setDate(first.getDate() - first.getDay());
      return Array.from({ length: 7 }, (_, index) => { const value = new Date(first); value.setDate(first.getDate() + index); return isoDate(value); });
    }
    if (mode === "month") {
      first.setDate(first.getDate() - first.getDay());
      return Array.from({ length: 42 }, (_, index) => { const value = new Date(first); value.setDate(first.getDate() + index); return isoDate(value); });
    }
    if (!data?.rangeStart || !data?.rangeEnd) return [];
    const start = localDate(data.rangeStart), end = localDate(data.rangeEnd), result = [];
    for (let day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) result.push(isoDate(day));
    return result;
  }, [data, mode, month]);
  const eventByDay = useMemo(() => {
    const result = {};
    for (const event of data?.events || []) (result[event.date] ||= []).push(event);
    return result;
  }, [data]);
  const holidayByDay = useMemo(() => {
    const result = {};
    for (const holiday of data?.holidays || []) (result[holiday.date] ||= []).push(holiday);
    return result;
  }, [data]);
  const monthHolidays = useMemo(() => (data?.holidays || []).filter(holiday => holiday.date?.startsWith(`${month}-`)), [data, month]);
  const relationByDay = data?.relations || {};
  const today = isoDate(new Date());
  const clients = data?.clients || [];
  const selectedClient = clients.find(client => client.id === clientId);
  const titleMonth = monthLabel(month);
  const canEdit = Boolean(data?.canEdit);

  const quickMark = async (day, state) => {
    const previous = relationByDay[day] || {};
    if (!state && !previous.state) return;
    setData(current => current ? { ...current, relations: { ...current.relations, [day]: { ...previous, state } } } : current);
    try { await save({ action: "save_relation", day, clientId, state, quick: true }); }
    catch {
      setData(current => current ? { ...current, relations: { ...current.relations, [day]: previous } } : current);
      /* The message is shown in the page alert. */
    }
  };

  const submitRelation = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await save({ action: "save_relation", day: dialog.date, clientId, state: form.get("state"), note: form.get("note") });
      setDialog(null);
    } catch { /* Keep the dialog open so the entry can be corrected. */ }
  };
  const submitEvent = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await save({ action: "create_event", title: form.get("title"), date: form.get("date"), time: form.get("time"), category: form.get("category"), priority: form.get("priority"), description: form.get("description"), recurrence: form.get("recurrence"), occurrences: Number(form.get("occurrences") || 1) });
      setDialog(null);
    } catch { /* Keep the dialog open so the entry can be corrected. */ }
  };
  const submitEventEdit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget), item = dialog.event;
    try {
      await save({ action: "edit_event", eventId: item.id, title: form.get("title"), date: form.get("date"), time: form.get("time"), category: form.get("category"), priority: form.get("priority"), description: form.get("description") });
      setDialog(null);
    } catch { /* Keep the dialog open so the entry can be corrected. */ }
  };
  const toggleChecklist = async (event, item) => {
    try { await save({ action: "checklist_toggle", eventId: event.id, itemId: item.id }); setDialog(null); }
    catch { /* The page alert contains the error. */ }
  };
  const submitChecklist = async (event, eventItem) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try { await save({ action: "checklist_add", eventId: eventItem.id, title: form.get("title") }); setDialog(null); }
    catch { /* The page alert contains the error. */ }
  };
  const submitMonthNote = async event => {
    event.preventDefault(); setNoteBusy(true);
    try { await save({ action: "save_month_note", month, body: monthlyNote }); }
    catch { /* The page alert contains the error. */ }
    finally { setNoteBusy(false); }
  };
  const changeEventStatus = async event => {
    try { await save({ action: "event_status", eventId: event.id }); setDialog(null); }
    catch { /* The page alert contains the error. */ }
  };
  const archiveEvent = async event => {
    if (!window.confirm(`Arquivar “${event.title}”?`)) return;
    try { await save({ action: "archive_event", eventId: event.id }); setDialog(null); }
    catch { /* The page alert contains the error. */ }
  };

  const formatDay = day => localDate(day).toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long" });
  const rangeText = mode === "week" && days.length ? `${shortDate(days[0])} a ${shortDate(days.at(-1))}` : titleMonth;
  const visibleAgenda = useMemo(() => {
    const monthStart = `${month}-01`;
    const monthEndDate = localDate(monthStart); monthEndDate.setMonth(monthEndDate.getMonth() + 1); monthEndDate.setDate(0);
    const start = mode === "upcoming" ? today : mode === "week" ? days[0] || monthStart : monthStart;
    const end = mode === "upcoming" ? isoDate(new Date(Date.now() + 14 * 86400000)) : mode === "week" ? days.at(-1) || isoDate(monthEndDate) : isoDate(monthEndDate);
    return (data?.events || []).filter(event => event.date >= start && event.date <= end).sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
  }, [data, mode, days, month, today]);

  return <>
    <div className="dp-calendar-page-actions"><button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => window.print()}>Imprimir</button>{canEdit && <button className="btn btn-primary btn-sm" type="button" onClick={() => setDialog({ type: "event" })}>Novo evento</button>}</div>
    <section className="container-fluid page-content dp-calendar-page">
      <div className="dp-calendar-print-head"><div><span>Calendário DP</span><h1>{titleMonth}</h1></div><img src="/static/logo.svg" alt="Conexão Empregos"/></div>
      <div className="dp-calendar-intro"><p className="text-secondary">Atividades, feriados, prazos e relação bancária em uma visão mensal.</p><div className="dp-calendar-layer btn-group" role="group" aria-label="Camada do calendário"><button type="button" className={`btn ${layer === "events" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setLayer("events")}>Atividades do DP</button><button type="button" className={`btn ${layer === "bank" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setLayer("bank")}>Relação Bancária</button></div></div>
      {error && <div className="alert alert-danger dp-calendar-alert" role="alert">{error}</div>}
      {notice && <div className="alert alert-success dp-calendar-alert" role="status">{notice}</div>}
      <section className="card dp-calendar-toolbar" aria-label="Filtros e navegação do calendário">
        <div className="dp-calendar-toolbar-top"><div className="dp-calendar-views btn-group" role="group" aria-label="Visualização"><button className={`btn ${mode === "month" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setMode("month")}>Mês</button><button className={`btn ${mode === "week" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setMode("week")}>Semana</button><button className={`btn ${mode === "agenda" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setMode("agenda")}>Agenda</button><button className={`btn ${mode === "upcoming" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setMode("upcoming")}>Próximos</button></div>
          <div className="dp-calendar-month-nav"><button className="btn btn-outline-secondary btn-sm" type="button" aria-label="Mês anterior" onClick={() => shiftMonth(-1)}>‹</button><strong>{rangeText}</strong><button className="btn btn-outline-secondary btn-sm" type="button" aria-label="Próximo mês" onClick={() => shiftMonth(1)}>›</button><button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => { setMonth(monthKey(new Date())); setMode("month"); }}>Hoje</button></div>
        </div>
        <div className="dp-calendar-filters"><label className="dp-calendar-search">Pesquisar eventos<input className="form-control form-control-sm" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Título, conteúdo ou palavra-chave" /></label>
          <label>Cliente<select className="form-select form-select-sm" value={clientId} onChange={event => setClientId(event.target.value)}><option value="">Todos os clientes</option>{clients.map(client => <option key={client.id} value={client.id}>{client.name}</option>)}</select></label>
          <label>Categoria<select className="form-select form-select-sm" value={category} onChange={event => setCategory(event.target.value)}><option value="">Todas</option>{(data?.categories || []).map(item => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="dp-calendar-check"><input className="form-check-input" type="checkbox" checked={mine} onChange={event => setMine(event.target.checked)} /> Meu calendário</label>
          {selectedClient && <div className="dp-calendar-location"><span>Localidade do cliente</span><strong>{[data?.city, data?.state].filter(Boolean).join(" · ") || "Localidade não cadastrada"}</strong></div>}
        </div>
      </section>
      {loading && <p className="dp-calendar-loading" role="status">Atualizando calendário…</p>}
      {mode === "agenda" || mode === "upcoming" ? <section className="dp-calendar-agenda" aria-label={mode === "agenda" ? "Agenda do mês" : "Próximos eventos"}>
        {visibleAgenda.length ? visibleAgenda.map(event => <article className="card dp-calendar-agenda-item" key={event.id}><button type="button" className="dp-calendar-agenda-date" onClick={() => setDialog({ type: "day", date: event.date })}><strong>{localDate(event.date).getDate()}</strong><span>{localDate(event.date).toLocaleDateString("pt-BR", { month: "short" })}</span></button><button type="button" className="dp-calendar-agenda-copy" onClick={() => setDialog({ type: "event", event })}><small>{event.time || "Dia inteiro"} · {event.category}</small><strong>{event.title}</strong><span>{event.clientName || event.description || "Evento do calendário"}</span></button><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setDialog({ type: "event", event })}>Abrir</button></article>) : <div className="card dp-calendar-empty">{loading ? "Carregando eventos…" : "Nenhum evento nesta seleção."}</div>}
      </section> : <section className={`dp-calendar-grid is-${mode} is-${layer}`} aria-label={`${layer === "bank" ? "Relação bancária" : "Atividades do DP"} de ${rangeText}`}>
        <div className="dp-calendar-weekdays">{weekdays.map(day => <div key={day}>{day}</div>)}</div>
        <div className="dp-calendar-days">{days.map(day => {
          const currentMonth = day.slice(0, 7) === month;
          const nextMonth = day.slice(0, 7) > month;
          const weekend = [0, 6].includes(localDate(day).getDay());
          const relation = relationByDay[day] || {};
          const events = eventByDay[day] || [];
          const holidays = holidayByDay[day] || [];
          return <article key={day} className={`dp-calendar-day${currentMonth ? "" : " is-outside"}${nextMonth ? " is-next-month" : ""}${weekend ? " is-weekend" : ""}${day === today ? " is-today" : ""}${holidays.length ? " has-holiday" : ""}${layer === "bank" && relation.state ? ` has-bank-${relation.state.toLowerCase()}` : ""}`}>
            <header className="dp-calendar-day-head"><button type="button" className="dp-calendar-date" onClick={() => setDialog({ type: "day", date: day })} aria-label={`Abrir ${formatDay(day)}`}>{localDate(day).getDate()}</button>{layer === "bank" && <div className="dp-calendar-statuses" aria-label={`Relação bancária de ${formatDay(day)}`}>{relationStates.map(([state, label, className]) => <button key={state} type="button" className={`dp-calendar-status ${className}${relation.state === state ? " is-selected" : ""}`} disabled={!canEdit || busy} onClick={() => quickMark(day, relation.state === state ? "" : state)} title={`${label}${relation.state === state ? " (selecionado)" : ""}`} aria-label={`${formatDay(day)}: ${label}${relation.state === state ? ", selecionado" : ""}`} aria-pressed={relation.state === state}><span className="visually-hidden">{label}</span></button>)}<button className="dp-calendar-note-button" type="button" disabled={!canEdit} onClick={() => setDialog({ type: "bank", date: day })} aria-label={`Editar nota de ${formatDay(day)}`} title="Editar relação e nota">✎</button></div>}</header>
            {holidays.map((holiday, index) => <button type="button" className="dp-calendar-holiday" key={`${holiday.name}-${index}`} onClick={() => setDialog({ type: "holiday", holiday })} title={`${holiday.kind}${holiday.city ? ` · ${holiday.city}` : ""}`}><strong>{holiday.name}</strong><small>{holiday.kind === "NACIONAL" ? "Feriado nacional" : holiday.kind === "ESTADUAL" ? `Feriado estadual${holiday.state ? ` · ${holiday.state}` : ""}` : `Feriado municipal${holiday.city ? ` · ${holiday.city}` : ""}`}</small></button>)}
            {layer === "events" && <div className="dp-calendar-day-events">{events.slice(0, mode === "week" ? 10 : 3).map(event => <button type="button" key={event.id} className={`dp-calendar-event${event.status === "CONCLUIDO" ? " is-done" : ""}`} onClick={() => setDialog({ type: "event", event })}><span>{event.time && <b>{event.time} · </b>}{event.title}</span>{event.clientName && <small>{event.clientName}</small>}</button>)}{events.length > 3 && mode !== "week" && <span className="dp-calendar-more">+ {events.length - 3} evento(s)</span>}</div>}
            {layer === "bank" && relation.note && <button className="dp-calendar-relation-note" type="button" onClick={() => setDialog({ type: "bank", date: day })} title="Ver observação da data">{relation.note}</button>}
          </article>;
        })}</div>
      </section>}
      {layer === "bank" && <div className="dp-calendar-legend" aria-label="Legenda da relação bancária"><span><i className="is-yes"/>Haverá relação bancária</span><span><i className="is-no"/>Não haverá relação bancária</span><span><i className="is-other"/>Fim de semana, feriado ou outra ocorrência</span><span><i className="is-empty"/>Sem marcação</span></div>}
      <section className="card dp-calendar-observations"><div className="dp-calendar-observation-head"><div><h2>Observações de {titleMonth}</h2><p>Feriados aplicáveis à localidade selecionada, alterações e recados deste mês.</p></div></div>
        <div className="dp-calendar-holiday-list">{monthHolidays.length ? monthHolidays.map((holiday,index) => <div className="dp-calendar-holiday-row" key={`${holiday.date}-${holiday.name}-${index}`}><strong className="text-nowrap">{holidayDate(holiday.date)}</strong><span><b>{holiday.name}</b><small>{holiday.kind === "MUNICIPAL" ? `Feriado municipal${holiday.city ? ` · ${holiday.city}` : ""}` : holiday.kind === "ESTADUAL" ? `Feriado estadual${holiday.state ? ` · ${holiday.state}` : ""}` : "Feriado nacional"}</small></span></div>) : <p className="dp-calendar-muted">{clientId && !data?.city ? "Cadastre a localidade da empresa para consultar feriados estaduais e municipais." : "Nenhum feriado cadastrado para esta seleção."}</p>}</div>
        <form className="dp-calendar-note-form" onSubmit={submitMonthNote}><label htmlFor="dp-calendar-month-note">Observação mensal</label><textarea id="dp-calendar-month-note" className="form-control" rows="3" maxLength="3000" value={monthlyNote} onChange={event => setMonthlyNote(event.target.value)} placeholder="Recados e orientações ligados a este mês" disabled={!canEdit}/><div><small>{data?.monthlyNote?.updatedAt ? `Atualizada em ${new Date(`${data.monthlyNote.updatedAt.replace(" ", "T")}Z`).toLocaleString("pt-BR")}` : "Até 3.000 caracteres"}</small>{canEdit && <button className="btn btn-primary btn-sm" disabled={noteBusy || busy} type="submit">{noteBusy ? "Salvando…" : "Salvar observação"}</button>}</div></form>
      </section>
      <div className="dp-calendar-print-signature"><strong>Caio Caobianco</strong></div>
      <footer className="dp-calendar-footer">Desenvolvido por <strong>Caio Caobianco</strong></footer>
    </section>

    {dialog?.type === "bank" && <Modal title="Relação bancária e observação" subtitle={formatDay(dialog.date)} onClose={() => setDialog(null)}><form className="dp-calendar-form" onSubmit={submitRelation}><label>Situação<select className="form-select" name="state" defaultValue={relationByDay[dialog.date]?.state || ""}><option value="">Sem marcação</option><option value="YES">Haverá relação bancária</option><option value="NO">Não haverá relação bancária</option><option value="OTHER">Fim de semana, feriado ou outra ocorrência</option></select></label><label>Observação<textarea className="form-control" name="note" rows="4" maxLength="500" defaultValue={relationByDay[dialog.date]?.note || ""} placeholder="Anotação específica para esta data"/></label>{relationByDay[dialog.date]?.source && <small className="dp-calendar-modal-source">Origem da situação atual: {relationByDay[dialog.date].source}</small>}{(data?.history || []).length > 0 && <details className="dp-calendar-history"><summary>Histórico desta data ({data.history.length})</summary>{data.history.map((item,index) => <p key={`${item.changed_at}-${index}`}><strong>{item.actor}</strong> · {item.changed_at}<br/>{item.old_state || "Sem marcação"} → {item.new_state || "Sem marcação"}{item.new_note !== item.old_note ? ` · ${item.new_note || "Nota removida"}` : ""}</p>)}</details>}<div className="dp-calendar-dialog-actions"><button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => setDialog(null)}>Cancelar</button><button className="btn btn-primary btn-sm" type="submit" disabled={busy || !canEdit}>{busy ? "Salvando…" : "Salvar"}</button></div></form></Modal>}

    {dialog?.type === "event" && !dialog.event && <Modal title="Novo evento" subtitle="O evento será incluído no Calendário DP." onClose={() => setDialog(null)} wide><form className="dp-calendar-form" onSubmit={submitEvent}><div className="dp-calendar-form-grid"><label>Título<input className="form-control" name="title" maxLength="160" required autoFocus placeholder="Ex.: Fechamento da folha"/></label><label>Data<input className="form-control" type="date" name="date" required defaultValue={today}/></label><label>Horário<input className="form-control" type="time" name="time" defaultValue="09:00"/></label><label>Categoria<input className="form-control" name="category" maxLength="80" defaultValue="Geral / interno"/></label><label>Prioridade<select className="form-select" name="priority" defaultValue="NORMAL"><option value="LOW">Baixa</option><option value="NORMAL">Normal</option><option value="HIGH">Alta</option><option value="URGENT">Urgente</option></select></label><label>Repetição<select className="form-select" name="recurrence" defaultValue="NONE"><option value="NONE">Não repetir</option><option value="DAILY">Diariamente</option><option value="WEEKLY">Semanalmente</option><option value="MONTHLY">Mensalmente</option></select></label><label>Ocorrências<input className="form-control" name="occurrences" type="number" min="1" max="24" defaultValue="1"/></label><label className="is-full">Descrição<textarea className="form-control" name="description" rows="4" maxLength="4000"/></label></div><div className="dp-calendar-dialog-actions"><button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => setDialog(null)}>Cancelar</button><button className="btn btn-primary btn-sm" type="submit" disabled={busy}>{busy ? "Salvando…" : "Criar evento"}</button></div></form></Modal>}

    {dialog?.type === "edit-event" && dialog.event && <Modal title="Editar evento" subtitle="A alteração será registrada no histórico." onClose={() => setDialog(null)} wide><form className="dp-calendar-form" onSubmit={submitEventEdit}><div className="dp-calendar-form-grid"><label>Título<input className="form-control" name="title" maxLength="160" required defaultValue={dialog.event.title}/></label><label>Data<input className="form-control" type="date" name="date" required defaultValue={dialog.event.date}/></label><label>Horário<input className="form-control" type="time" name="time" defaultValue={dialog.event.time || "09:00"}/></label><label>Categoria<input className="form-control" name="category" maxLength="80" defaultValue={dialog.event.category}/></label><label>Prioridade<select className="form-select" name="priority" defaultValue={dialog.event.priority || "NORMAL"}><option value="LOW">Baixa</option><option value="NORMAL">Normal</option><option value="HIGH">Alta</option><option value="URGENT">Urgente</option></select></label><label className="is-full">Descrição<textarea className="form-control" name="description" rows="4" maxLength="4000" defaultValue={dialog.event.description}/></label></div><div className="dp-calendar-dialog-actions"><button className="btn btn-outline-secondary btn-sm" type="button" onClick={() => setDialog(null)}>Cancelar</button><button className="btn btn-primary btn-sm" type="submit" disabled={busy}>{busy ? "Salvando…" : "Salvar alterações"}</button></div></form></Modal>}

    {dialog?.type === "event" && dialog.event && <Modal title={dialog.event.title} subtitle={`${formatDay(dialog.event.date)} · ${dialog.event.time || "Dia inteiro"}`} onClose={() => setDialog(null)} wide><div className="dp-calendar-event-detail"><div className="dp-calendar-event-meta"><span>{dialog.event.category}</span><span>{dialog.event.priority === "URGENT" ? "Urgente" : dialog.event.priority === "HIGH" ? "Alta" : dialog.event.priority === "LOW" ? "Baixa" : "Normal"}</span><span>{dialog.event.status === "CONCLUIDO" ? "Concluído" : "Em aberto"}</span>{dialog.event.clientName && <span>{dialog.event.clientName}</span>}</div>{dialog.event.description && <p className="dp-calendar-event-description">{dialog.event.description}</p>}{(dialog.event.checklist?.length > 0 || dialog.event.canManage) && <section><h3>Checklist</h3>{dialog.event.checklist?.length > 0 ? <ul className="dp-calendar-checklist">{dialog.event.checklist.map(item => <li key={item.id}>{dialog.event.canManage ? <label><input className="form-check-input" type="checkbox" checked={Boolean(item.done)} onChange={() => toggleChecklist(dialog.event, item)} disabled={busy}/><span className={item.done ? "is-done" : ""}>{item.title}</span></label> : <><span className={item.done ? "is-done" : ""}>{item.done ? "✓" : "○"}</span>{item.title}</>}</li>)}</ul> : <p className="dp-calendar-muted">Nenhuma tarefa cadastrada.</p>}{dialog.event.canManage && <form className="dp-calendar-checklist-add" onSubmit={event => submitChecklist(event, dialog.event)}><input className="form-control form-control-sm" name="title" maxLength="180" required placeholder="Adicionar tarefa ao checklist"/><button className="btn btn-outline-primary btn-sm" type="submit" disabled={busy}>Adicionar</button></form>}</section>}{dialog.event.history?.length > 0 && <details className="dp-calendar-history"><summary>Histórico ({dialog.event.history.length})</summary>{dialog.event.history.map((item,index) => <p key={`${item.created_at}-${index}`}><strong>{item.actor}</strong> · {item.created_at}<br/>{item.action}: {item.detail}</p>)}</details>}{dialog.event.caseId && <a className="btn btn-outline-primary btn-sm" href={`/central-dp/casos?case=${encodeURIComponent(dialog.event.caseId)}`}>Ver caso vinculado</a>}{dialog.event.canManage && canEdit && <div className="dp-calendar-dialog-actions"><button className="btn btn-outline-danger btn-sm" type="button" onClick={() => archiveEvent(dialog.event)} disabled={busy}>Arquivar</button><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => setDialog({ type: "edit-event", event: dialog.event })} disabled={busy}>Editar</button><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => changeEventStatus(dialog.event)} disabled={busy}>{dialog.event.status === "CONCLUIDO" ? "Reabrir evento" : "Concluir evento"}</button></div>}{!dialog.event.canManage && !dialog.event.caseId && <small className="dp-calendar-modal-source">Registro vinculado à base importada; edição protegida.</small>}</div></Modal>}

    {dialog?.type === "holiday" && <Modal title={dialog.holiday.name} subtitle={formatDay(dialog.holiday.date)} onClose={() => setDialog(null)}><div className="dp-calendar-event-detail"><span className="dp-calendar-holiday-kind">{dialog.holiday.kind === "NACIONAL" ? "Feriado nacional" : dialog.holiday.kind === "ESTADUAL" ? `Feriado estadual · ${dialog.holiday.state || ""}` : `Feriado municipal · ${dialog.holiday.city || ""}`}</span><p>Fonte: {dialog.holiday.source || "Calendário DP"}</p></div></Modal>}

    {dialog?.type === "day" && <Modal title={`Eventos de ${formatDay(dialog.date)}`} onClose={() => setDialog(null)}><div className="dp-calendar-day-dialog">{(eventByDay[dialog.date] || []).map(event => <button type="button" className="dp-calendar-event" key={event.id} onClick={() => setDialog({ type: "event", event })}><strong>{event.time ? `${event.time} · ` : ""}{event.title}</strong><small>{event.category}{event.clientName ? ` · ${event.clientName}` : ""}</small></button>)}{(holidayByDay[dialog.date] || []).map(holiday => <div className="dp-calendar-event is-holiday" key={holiday.name}><strong>{holiday.name}</strong><small>{holiday.kind}</small></div>)}{layer === "bank" && <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setDialog({ type: "bank", date: dialog.date })}>Editar relação bancária</button>}{!(eventByDay[dialog.date]?.length || holidayByDay[dialog.date]?.length) && <p className="dp-calendar-muted">Nenhum evento ou feriado nesta data.</p>}</div></Modal>}
  </>;
}
