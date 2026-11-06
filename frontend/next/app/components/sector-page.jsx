"use client";

import { useEffect, useRef, useState } from "react";

const TABS = [
  { key: "ACTIVITY", label: "Atividades e responsáveis", icon: "people" },
  { key: "REFERENCE", label: "Referências", icon: "book" },
  { key: "HISTORY", label: "Histórico", icon: "history" },
  { key: "CREDENTIAL", label: "Acessos", icon: "lock" },
];
const number = value => Number(value || 0).toLocaleString("pt-BR");
const array = value => Array.isArray(value) ? value : [];
const text = value => value == null || value === "" ? "—" : typeof value === "boolean" ? value ? "Sim" : "Não" : typeof value === "object" ? String(value.label || value.name || value.title || value.value || "—") : String(value);
const date = value => {
  if (!value) return "";
  const source = /^\d{4}-\d{2}-\d{2} \d{2}:/.test(String(value)) ? String(value).replace(" ", "T") + "Z" : value;
  const parsed = new Date(source);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
};

function Icon({ name, size = 18 }) {
  const paths = {
    people: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    book: "M4 3h6a3 3 0 0 1 3 3v15a4 4 0 0 0-4-2H3V3h1Zm9 3a3 3 0 0 1 3-3h5v16h-4a4 4 0 0 0-4 2",
    history: "M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2",
    lock: "M5 10h14v11H5ZM8 10V7a4 4 0 1 1 8 0v3M12 14v3",
    arrow: "M5 12h14m-5-5 5 5-5 5",
    search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
    source: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.source}/></svg>;
}

function safeHref(value) {
  const href = String(value || "").trim();
  if (!/^(https?:\/\/|\/(?!\/))/i.test(href)) return "";
  try {
    const parsed = new URL(href, "https://local.invalid");
    if (parsed.username || parsed.password || [...parsed.searchParams.keys()].some(key => /password|senha|token|secret|api.?key|authorization/i.test(key))) return "";
    return href;
  } catch { return ""; }
}

async function readResponse(response, fallback) {
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || typeof payload !== "object") {
    const error = new Error(response.status === 401 ? "Sua sessão expirou. Entre novamente." : payload?.error || fallback);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function DestinationLinks({ links, className = "" }) {
  const visible = array(links).map(link => ({ label: link.label, href: safeHref(link.href) })).filter(link => link.href && link.label);
  if (!visible.length) return null;
  return <div className={`sector-destinations ${className}`}>{visible.map((link, index) => <a key={`${link.href}-${index}`} href={link.href}>{link.label}<Icon name="arrow" size={14}/></a>)}</div>;
}

function Pagination({ page, pages, total, onChange, label = "Paginação dos registros", busy = false }) {
  if (Number(pages || 0) <= 1) return null;
  return <nav className="sector-pagination" aria-label={label}><span>{number(total)} registros</span><div><button type="button" className="btn btn-outline-primary btn-sm" disabled={busy || page <= 1} onClick={() => onChange(page - 1)}>Anterior</button><span>Página {number(page)} de {number(pages)}</span><button type="button" className="btn btn-outline-primary btn-sm" disabled={busy || page >= pages} onClick={() => onChange(page + 1)}>Próxima</button></div></nav>;
}

function DataGrid({ columns, rows, title }) {
  const headings = array(columns);
  const values = array(rows);
  if (!headings.length || !values.length) return null;
  return <div className="sector-table-scroll" tabIndex={0} aria-label={title || "Dados da referência"}><table className="sector-table"><thead><tr>{headings.map((column, index) => <th key={index} scope="col">{text(column)}</th>)}</tr></thead><tbody>{values.map((row, index) => <tr key={index}>{headings.map((column, columnIndex) => <td key={columnIndex}>{text(Array.isArray(row) ? row[columnIndex] : row?.[column])}</td>)}</tr>)}</tbody></table></div>;
}

function ItemContent({ item, response, page, onPage, busy }) {
  const payload = item.payload || {};
  const notes = typeof payload.notes === "string" ? [payload.notes] : array(payload.notes);
  const sections = array(payload.sections);
  const links = [...array(payload.destinationLinks), ...array(payload.links), ...array(item.destinationLinks)];
  const seen = new Set();
  const destinations = links.filter(link => { const key = `${link.label}|${link.href}`; if (seen.has(key)) return false; seen.add(key); return true; });
  return <>
    {payload.description && <p className="sector-detail-description">{text(payload.description)}</p>}
    {notes.length > 0 && <div className="sector-notes">{notes.map((note, index) => <p key={index}>{text(note)}</p>)}</div>}
    <DestinationLinks links={destinations}/>
    <DataGrid columns={payload.columns} rows={payload.rows} title={item.title}/>
    {sections.map((section, index) => <section className="sector-content-section" key={index}>{section.title && <h4>{section.title}</h4>}{section.description && <p>{section.description}</p>}{array(section.notes).map((note, noteIndex) => <p key={noteIndex}>{text(note)}</p>)}<DataGrid columns={section.columns} rows={section.rows} title={section.title}/><DestinationLinks links={section.links}/></section>)}
    <Pagination page={Number(response.rowPage || page)} pages={Number(response.rowPages || 1)} total={Number(response.rowTotal || 0)} onChange={onPage} label={`Paginação de ${item.title}`} busy={busy}/>
    <div className="sector-source-locator"><Icon name="source" size={14}/><span>Fonte: {item.sourceSheet || "Planilha do setor"}{item.sourceRange ? ` · ${item.sourceRange}` : ""}</span>{item.manuallyEdited && <span>Responsabilidade ajustada na V3</span>}</div>
  </>;
}

function ActivityEditor({ item, csrf, onSaved, onClose }) {
  const values = item.payload || {};
  const [responsible, setResponsible] = useState(array(item.responsibleLabels || values.responsibleLabels).join("\n"));
  const [periodicity, setPeriodicity] = useState(item.periodicity || values.periodicity || "");
  const [complexity, setComplexity] = useState(item.complexity || values.complexity || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save(event) {
    event.preventDefault();
    if (!csrf || saving) return;
    setSaving(true); setError("");
    try {
      const result = await readResponse(await fetch("/api/ui/sector", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, action: "update_activity", id: item.id, responsibleLabels: responsible.split(/[\n,;]/).map(value => value.trim()).filter(Boolean), periodicity, complexity }) }), "Não foi possível salvar a responsabilidade.");
      onSaved(result);
    } catch (reason) { setError(reason.message); }
    finally { setSaving(false); }
  }
  return <form className="sector-editor" onSubmit={save}>
    <div className="sector-editor-heading"><h4>Ajustar atividade</h4><p>Defina responsáveis e frequência. Acompanhe a execução em Casos e Calendário.</p></div>
    <div className="sector-editor-fields"><label className="sector-responsible-field">Responsáveis<textarea value={responsible} maxLength={4000} onChange={event => setResponsible(event.target.value)} rows={3} placeholder="Um nome por linha" disabled={saving}/></label><label>Frequência<input value={periodicity} maxLength={100} onChange={event => setPeriodicity(event.target.value)} placeholder="Não informada" list={`sector-frequency-${item.id}`} disabled={saving}/><datalist id={`sector-frequency-${item.id}`}>{["Diária", "Semanal", "Quinzenal", "Mensal", "Anual", "Sob demanda"].map(value => <option key={value} value={value}/>)}</datalist></label><label>Complexidade<input value={complexity} maxLength={100} onChange={event => setComplexity(event.target.value)} placeholder="Não informada" list={`sector-complexity-${item.id}`} disabled={saving}/><datalist id={`sector-complexity-${item.id}`}>{["Baixa", "Média", "Alta", "Relativa"].map(value => <option key={value} value={value}/>)}</datalist></label></div>
    {error && <p className="sector-inline-error" role="alert">{error}</p>}
    <div className="sector-editor-actions"><button type="submit" className="btn btn-primary btn-sm" disabled={saving || !csrf}>{saving ? "Salvando…" : "Salvar ajuste"}</button><button type="button" className="btn btn-outline-primary btn-sm" onClick={onClose} disabled={saving}>Cancelar</button></div>
  </form>;
}

function SourceSummary({ source, canReveal }) {
  if (!source) return null;
  const metadata = source.metadata || {};
  const candidates = metadata.sheetMapping || metadata.sheetDestinations || metadata.mapping;
  const mapping = Array.isArray(candidates) ? candidates : candidates && typeof candidates === "object" ? Object.entries(candidates).map(([sheet, destination]) => ({ sheet, destination: typeof destination === "string" ? destination : destination?.destination || destination?.label })) : [];
  return <details className="sector-origin"><summary><span><Icon name="source" size={15}/>{source.filename || source.label || "Origem dos dados"}</span><span>{source.importedAt ? `Importado em ${date(source.importedAt)}` : "Ver origem e organização"}<b aria-hidden="true">⌄</b></span></summary><div><p>Conteúdo organizado a partir da planilha do setor. Cada registro conserva a aba e o intervalo de origem.</p>{Number(metadata.sheetCount) > 0 && <p>{number(metadata.sheetCount)} abas analisadas · {number(metadata.calculatorDuplicatesSkipped)} ferramentas existentes reutilizadas.</p>}{array(metadata.emptySheets).length > 0 && <p>Abas sem conteúdo não geraram novos registros: {metadata.emptySheets.join(", ")}.</p>}<DestinationLinks links={metadata.calculatorLinks}/>{array(metadata.notes).map((note,index)=><p key={index}>{note}</p>)}{canReveal && source.id && <a className="btn btn-outline-primary btn-sm" href={`/api/ui/sector/source/${source.id}/download`}>Baixar origem · acesso restrito</a>}{mapping.length > 0 && <div className="sector-mapping"><table className="sector-table"><thead><tr><th scope="col">Conteúdo da origem</th><th scope="col">Onde consultar</th><th scope="col">Tratamento</th></tr></thead><tbody>{mapping.filter(entry => entry.sheet && entry.destination).map((entry, index) => <tr key={index}><td>{entry.sheet}</td><td>{entry.destination}</td><td>{entry.status}</td></tr>)}</tbody></table></div>}</div></details>;
}

const CREDENTIAL_LABELS = { contact: "Contato", email: "E-mail", login: "Login", password: "Senha", identifier: "Identificador", url: "Site", notes: "Observações", CONTATO: "Contato", "E-MAIL": "E-mail", LOGIN: "Login", SENHA: "Senha", ID: "Identificador", SITE: "Site", item: "Sistema", ITEM: "Sistema" };

export default function SectorPage() {
  const [tab, setTab] = useState("ACTIVITY"), [query, setQuery] = useState(""), [search, setSearch] = useState(""), [category, setCategory] = useState(""), [page, setPage] = useState(1);
  const [data, setData] = useState(null), [loading, setLoading] = useState(true), [error, setError] = useState(""), [reload, setReload] = useState(0), [notice, setNotice] = useState("");
  const [opened, setOpened] = useState(null), [detail, setDetail] = useState(null), [detailPage, setDetailPage] = useState(1), [detailLoading, setDetailLoading] = useState(false), [detailError, setDetailError] = useState(""), [editing, setEditing] = useState(false);
  const [revealed, setRevealed] = useState(null), [revealing, setRevealing] = useState(null), [revealError, setRevealError] = useState("");
  const [linkedItem, setLinkedItem] = useState(null);
  const listVersion = useRef(0), detailVersion = useRef(0), revealVersion = useRef(0), revealController = useRef(null), activeTab = useRef(tab);

  function hideCredential() { revealVersion.current += 1; revealController.current?.abort(); revealController.current = null; setRevealed(null); setRevealing(null); setRevealError(""); }
  function changeTab(next) { hideCredential(); closeDetail(); activeTab.current = next; setTab(next); setPage(1); setCategory(""); setNotice(""); }
  function closeDetail() { detailVersion.current += 1; setOpened(null); setLinkedItem(null); setDetail(null); setDetailError(""); setEditing(false); }
  useEffect(() => {
    const params = new URLSearchParams(window.location.search), kind = params.get("kind"), id = params.get("item");
    if (TABS.some(item=>item.key === kind && kind !== "CREDENTIAL")) { activeTab.current = kind; setTab(kind); }
    if (/^[1-9]\d*$/.test(id || "")) { setLinkedItem(Number(id)); setOpened(Number(id)); }
  }, []);
  useEffect(() => { if (query.trim() === search) return; const timer = setTimeout(() => { setSearch(query.trim()); setPage(1); closeDetail(); hideCredential(); }, 240); return () => clearTimeout(timer); }, [query,search]);
  useEffect(() => {
    const hide = () => { if (document.hidden) hideCredential(); };
    const escape = event => { if (event.key === "Escape") hideCredential(); };
    document.addEventListener("visibilitychange", hide); window.addEventListener("keydown", escape);
    return () => { document.removeEventListener("visibilitychange", hide); window.removeEventListener("keydown", escape); revealVersion.current += 1; revealController.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController(), version = ++listVersion.current;
    setLoading(true); setError("");
    const params = new URLSearchParams({ kind: tab === "CREDENTIAL" ? "ACTIVITY" : tab, q: search, category: tab === "CREDENTIAL" ? "" : category, page: String(tab === "CREDENTIAL" ? 1 : page), credentialPage: String(tab === "CREDENTIAL" ? page : 1) });
    fetch(`/api/ui/sector?${params}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(response => readResponse(response, "Não foi possível carregar a organização do setor."))
      .then(payload => { if (version === listVersion.current) setData(payload); })
      .catch(reason => { if (!controller.signal.aborted && version === listVersion.current) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted && version === listVersion.current) setLoading(false); });
    return () => controller.abort();
  }, [tab, search, category, page, reload]);
  useEffect(() => { if (data && !data.canReveal && tab === "CREDENTIAL") changeTab("ACTIVITY"); }, [data?.canReveal, tab]);
  useEffect(() => {
    if (opened == null) return;
    const controller = new AbortController(), version = ++detailVersion.current;
    setDetailLoading(true); setDetailError("");
    fetch(`/api/ui/sector/${encodeURIComponent(opened)}?rowPage=${detailPage}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(response => readResponse(response, "Não foi possível abrir este registro."))
      .then(payload => {
        if (version !== detailVersion.current) return;
        const item = payload.item || payload;
        setDetail({ ...payload, item, rowPage: payload.rowPage ?? item.rowPage,
          rowPageSize: payload.rowPageSize ?? item.rowPageSize,
          rowPages: payload.rowPages ?? item.rowPages, rowTotal: payload.rowTotal ?? item.rowTotal });
      })
      .catch(reason => { if (!controller.signal.aborted && version === detailVersion.current) setDetailError(reason.message); })
      .finally(() => { if (!controller.signal.aborted && version === detailVersion.current) setDetailLoading(false); });
    return () => controller.abort();
  }, [opened, detailPage, reload]);

  function openItem(id) { if (opened === id) { closeDetail(); return; } setLinkedItem(null); setDetail(null); setDetailPage(1); setEditing(false); setOpened(id); setNotice(""); }
  async function revealCredential(id) {
    if (!data?.canReveal || !data.csrf || revealing != null) return;
    if (revealed?.id === id) { hideCredential(); return; }
    hideCredential();
    const controller = new AbortController(), version = ++revealVersion.current;
    revealController.current = controller; setRevealing(id);
    try {
      const result = await readResponse(await fetch(`/api/ui/sector/credentials/${encodeURIComponent(id)}/reveal`, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.csrf }), signal: controller.signal }), "Não foi possível abrir este acesso.");
      if (!controller.signal.aborted && version === revealVersion.current && activeTab.current === "CREDENTIAL") setRevealed({ id, fields: result.fields || result.credential || {} });
    } catch (reason) { if (!controller.signal.aborted && version === revealVersion.current) setRevealError(reason.message); }
    finally { if (version === revealVersion.current) setRevealing(null); }
  }
  function changePage(next) { closeDetail(); hideCredential(); setPage(next); }
  const credentialTab = tab === "CREDENTIAL";
  const items = credentialTab ? array(data?.credentials) : array(data?.items);
  const total = Number(credentialTab ? data?.credentialTotal ?? items.length : data?.total || 0);
  const pages = Number(credentialTab ? data?.credentialPages || 1 : data?.pages || 1);
  const currentTab = TABS.find(item => item.key === tab);
  const itemDetail = detail?.item;

  return <section className="container-fluid page-content sector-page">
    <header className="sector-heading"><div><span className="sector-eyebrow">ROTINAS DO SETOR</span><h2>Organização do setor</h2><p>Responsabilidades, referências de trabalho e material da equipe em um só lugar.</p></div><div className="sector-heading-links"><a href="/central-dp/processos">Processos<Icon name="arrow" size={14}/></a><a href="/central-dp/manuais">Manuais<Icon name="arrow" size={14}/></a></div></header>
    <div className="sector-tabs" role="tablist" aria-label="Conteúdos da organização do setor">{TABS.filter(item => item.key !== "CREDENTIAL" || data?.canReveal).map(item => <button key={item.key} type="button" role="tab" id={`sector-tab-${item.key}`} aria-controls="sector-tab-content" aria-selected={tab === item.key} className={tab === item.key ? "is-active" : ""} onClick={() => changeTab(item.key)}><Icon name={item.icon}/><span>{item.label}</span>{item.key !== "CREDENTIAL" && data?.counts?.[item.key] != null && <small>{number(data.counts[item.key])}</small>}</button>)}</div>
    <div id="sector-tab-content" role="tabpanel" aria-labelledby={`sector-tab-${tab}`}>
      {tab === "ACTIVITY" && <div className="sector-context"><p>Consulte a distribuição das rotinas e abra os processos relacionados.</p><div><a href="/central-dp/casos">Acompanhar em Casos<Icon name="arrow" size={14}/></a><a href="/rotinas/calendario">Abrir Calendário<Icon name="arrow" size={14}/></a></div></div>}
      {tab === "HISTORY" && <div className="sector-history-note" role="note"><Icon name="history"/><p><strong>Referência histórica da planilha.</strong> Estes registros preservam a informação da origem. Consulte Pessoas e Afastamentos para conferir a situação vigente.</p></div>}
      {credentialTab && <div className="sector-access-note"><Icon name="lock"/><p>Acessos restritos à administração. Abra somente o registro necessário; os dados são ocultados ao fechar ou sair desta aba.</p></div>}
      {linkedItem && !items.some(item=>item.id === linkedItem) && <article className="sector-record is-open sector-linked-record"><div className="sector-detail"><div className="sector-linked-heading"><h3>{itemDetail?.title || "Histórico do prontuário"}</h3><button className="btn btn-outline-primary btn-sm" type="button" onClick={closeDetail}>Fechar</button></div>{detailLoading && <p role="status">Carregando histórico…</p>}{detailError && <p role="alert">{detailError}</p>}{itemDetail && !detailLoading && <ItemContent item={itemDetail} response={detail} page={detailPage} onPage={setDetailPage} busy={detailLoading}/>}</div></article>}
      <div className="sector-toolbar"><label className="sector-search"><span>Buscar em {currentTab?.label.toLocaleLowerCase("pt-BR")}</span><div><Icon name="search" size={16}/><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={credentialTab ? "Nome do sistema ou origem" : "Atividade, assunto ou origem"}/></div></label>{!credentialTab && <label className="sector-category"><span>Categoria</span><select value={category} onChange={event => { setCategory(event.target.value); setPage(1); closeDetail(); }}><option value="">Todas as categorias</option>{array(data?.categories).map(value => <option key={typeof value === "string" ? value : value.value} value={typeof value === "string" ? value : value.value}>{typeof value === "string" ? value : value.label}</option>)}</select></label>}{(query || category) && <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => { setQuery(""); setCategory(""); changePage(1); }}>Limpar filtros</button>}</div>
      {notice && <div className="sector-notice" role="status">{notice}</div>}
      {error && <div className="sector-error" role="alert"><p>{error}</p>{error.includes("sessão") ? <a href="/entrar" className="btn btn-primary btn-sm">Entrar novamente</a> : <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setReload(value => value + 1)}>Tentar novamente</button>}</div>}
      <div className="sector-list-heading"><span>{loading ? "Carregando…" : `${number(total)} ${credentialTab ? "acessos" : "registros"}`}</span><small>25 por página</small></div>
      {loading ? <div className="sector-loading" role="status"><i/>Carregando {currentTab?.label.toLocaleLowerCase("pt-BR")}…</div> : !error && <>
        {!items.length ? <div className="sector-empty"><Icon name={currentTab?.icon} size={24}/><strong>{query || category ? "Nenhum resultado para esses filtros" : "Nenhum registro disponível"}</strong><p>{query || category ? "Ajuste a busca ou consulte todas as categorias." : data?.source ? "Os conteúdos aparecem conforme a organização da origem e as permissões da sua conta." : "A planilha do setor ainda não foi incorporada a esta base."}</p></div> : <div className="sector-list">{items.map(item => credentialTab ? <article key={item.id} className={`sector-record sector-credential ${revealed?.id === item.id ? "is-open" : ""}`}><div className="sector-credential-heading"><span className="sector-record-icon"><Icon name="lock"/></span><div><h3>{item.label}</h3><small>{item.sourceSheet || "Acessos do setor"}</small></div><button type="button" className="btn btn-outline-primary btn-sm" disabled={revealing != null} onClick={() => revealCredential(item.id)} aria-expanded={revealed?.id === item.id} aria-controls={`sector-access-${item.id}`}>{revealing === item.id ? "Abrindo…" : revealed?.id === item.id ? "Ocultar acesso" : "Revelar acesso"}</button></div>{revealed?.id === item.id && <div className="sector-credential-body" id={`sector-access-${item.id}`}><dl>{Object.entries(revealed.fields).filter(([, value]) => value !== "" && value != null).map(([key, value]) => <div key={key}><dt>{CREDENTIAL_LABELS[key] || key}</dt><dd>{text(value)}</dd></div>)}</dl><button type="button" className="btn btn-outline-primary btn-sm" onClick={hideCredential}>Fechar e ocultar dados</button></div>}</article> : <article className={`sector-record ${opened === item.id ? "is-open" : ""}`} key={item.id}>
          <button type="button" className="sector-record-summary" onClick={() => openItem(item.id)} aria-expanded={opened === item.id} aria-controls={`sector-detail-${item.id}`}><span className="sector-record-icon"><Icon name={currentTab?.icon}/></span><span className="sector-record-copy"><span className="sector-record-category">{item.category || "Geral"}</span><strong>{item.title}</strong><span className="sector-record-meta">{tab === "ACTIVITY" ? <><span>{array(item.responsibleLabels).length ? item.responsibleLabels.join(" · ") : "Responsável não informado"}</span>{item.periodicity && <span>{item.periodicity}</span>}{item.complexity && <span>Complexidade {String(item.complexity).toLocaleLowerCase("pt-BR")}</span>}</> : <span>{item.sourceSheet || "Referência do setor"}</span>}</span></span><span className="sector-record-toggle">{opened === item.id ? "Recolher" : "Ver detalhes"}<b aria-hidden="true">⌄</b></span></button>
          {opened === item.id && <div className="sector-detail" id={`sector-detail-${item.id}`}>
            {detailLoading && <p className="sector-detail-loading" role="status">Carregando conteúdo…</p>}
            {detailError && <div className="sector-error" role="alert"><p>{detailError}</p><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setReload(value => value + 1)}>Tentar novamente</button></div>}
            {itemDetail && !detailLoading && <>{tab === "ACTIVITY" && <dl className="sector-activity-facts"><div><dt>Responsáveis</dt><dd>{array(itemDetail.responsibleLabels).join(" · ") || "Não informados"}</dd></div><div><dt>Frequência</dt><dd>{itemDetail.periodicity || "Não informada"}</dd></div><div><dt>Complexidade</dt><dd>{itemDetail.complexity || "Não informada"}</dd></div></dl>}<ItemContent item={itemDetail} response={detail} page={detailPage} onPage={setDetailPage} busy={detailLoading}/>{tab === "ACTIVITY" && data?.canManage && (editing ? <ActivityEditor key={itemDetail.id} item={itemDetail} csrf={data.csrf} onClose={() => setEditing(false)} onSaved={result => { setEditing(false); setNotice(result.message || "Responsabilidade atualizada."); setReload(value => value + 1); }}/> : <div className="sector-detail-actions"><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setEditing(true)}>Ajustar responsáveis e frequência</button></div>)}</>}
          </div>}
        </article>)}</div>}
        {revealError && <div className="sector-error" role="alert"><p>{revealError}</p></div>}
        <Pagination page={page} pages={pages} total={total} onChange={changePage}/>
      </>}
    </div>
    <SourceSummary source={data?.source} canReveal={data?.canReveal}/>
  </section>;
}
