"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import OperationsIcon from "./operations-icon";
import ZproSettings from "./zpro-settings";
import RescissionDeliveryHistory from "./rescission-delivery-history";

const phaseLabels = { NO_PRAZO: "Dentro do prazo", URGENTE: "Atenção final", VENCIDO: "Vencido", CONCLUIDO: "Concluído", CONFERIR: "Conferir origem", CANCELADO: "Cancelado", EM_DOCUMENTACAO: "Documentação pendente", PRONTO_PARA_ENVIO: "Pronto para comunicar" };
const filterCards = [["NO_PRAZO", "No prazo · 10 dias", "noPrazo"], ["URGENTES", "Atenção final", "urgent"], ["VENCIDOS", "Vencidos", "overdue"], ["CONCLUIDOS", "Concluídos", "completed"]];
const phaseIcons = { NO_PRAZO: "calendar", URGENTES: "clock", VENCIDOS: "alert", CONCLUIDOS: "check" };

function formatDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "—";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function dateHint(days) {
  if (days < 0) return `${Math.abs(days)} dia(s) em atraso`;
  if (days === 0) return "vence hoje";
  return `${days} dia(s) restantes`;
}

function attachedRequirementIds(item) {
  const rows = item?.is_test ? item.requirements.filter(row => row.code === "TRCT") : item?.requirements || [];
  return rows.filter(row => ["ANEXADO", "ENTREGUE"].includes(row.status)).map(row => row.id);
}

function defaultMessage(item) {
  return `Olá, ${item?.name || ""}. Estamos organizando a documentação referente ao seu desligamento em ${formatDate(item?.dismissal_date)}. Por favor, confira os documentos encaminhados e fale com o DP se precisar de ajuda.`;
}

export default function RescissionsPage({ caseId = "" }) {
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [company, setCompany] = useState("");
  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState("");
  const [dateFrom, setDateFrom] = useState(() => { const day = new Date(); day.setDate(day.getDate() - 90); return day.toLocaleDateString("sv-SE"); });
  const [page, setPage] = useState(1);
  const listRequest = useRef(null);
  const [dateTo, setDateTo] = useState("");
  const [message, setMessage] = useState("");
  const [deliveryChannel, setDeliveryChannel] = useState("EMAIL");
  const [documentIds, setDocumentIds] = useState([]);
  const deliveryRequestId = useRef(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [openRequirementAction, setOpenRequirementAction] = useState(null);
  const [uploadFiles, setUploadFiles] = useState({});
  const [dispenseReasons, setDispenseReasons] = useState({});
  const reasonOptions = useMemo(() => {
    return (data?.reasons || []).map(item => {
      const code = String(item?.code || "").trim();
      const label = String(item?.label || "").trim();
      const value = JSON.stringify([code, label]);
      return { value, label: code ? `${code} · ${label || `Motivo ${code}`}` : label || "Motivo não informado" };
    });
  }, [data?.reasons]);

  async function loadData(quiet = false) {
    listRequest.current?.abort();
    const controller = new AbortController(); listRequest.current = controller;
    if (!quiet) setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ search, company, reason, phase, dateFrom, dateTo, page: String(page) });
      const response = await fetch(`/api/ui/rescissions?${query}`, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error || "Não foi possível carregar as rescisões.");
      if (!controller.signal.aborted) setData(payload);
    } catch (cause) { if (!controller.signal.aborted) setError(cause.message); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }

  async function loadCase(id) {
    deliveryRequestId.current = null;
    setDetail(null);
    setError("");
    setOpenRequirementAction(null);
    setUploadFiles({});
    setDispenseReasons({});
    try {
      const response = await fetch(`/api/ui/rescissions/${encodeURIComponent(id)}`, { credentials: "same-origin", cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error || "Não foi possível abrir este caso.");
      setDetail(payload.case);
      setMessage(defaultMessage(payload.case));
      setDocumentIds(attachedRequirementIds(payload.case));
      setSelectedDocumentId(String(payload.case.available_documents?.[0]?.id || ""));
    } catch (cause) { setError(cause.message); }
  }

  useEffect(() => {
    if (caseId) loadCase(caseId);
  }, [caseId]);
  useEffect(() => { setPage(1); }, [search, company, reason, phase, dateFrom, dateTo]);
  useEffect(() => { const timer = window.setTimeout(() => loadData(), 300); return () => { window.clearTimeout(timer); listRequest.current?.abort(); }; }, [caseId, search, company, reason, phase, dateFrom, dateTo, page]);
  useEffect(() => { if (caseId || busy) return; const timer = window.setInterval(() => { if (!document.hidden) loadData(true); }, 15000); return () => window.clearInterval(timer); }, [caseId, busy, search, company, reason, phase, dateFrom, dateTo, page]);

  async function mutate(payload) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/ui/rescissions", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, csrf: data?.csrf }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(result?.error || "Não foi possível salvar a ação.");
      if (result?.case) {
        setDetail(result.case);
        setMessage(defaultMessage(result.case));
        setDocumentIds(attachedRequirementIds(result.case));
        if (!result.case.available_documents?.some(row => String(row.id) === selectedDocumentId)) {
          setSelectedDocumentId(String(result.case.available_documents?.[0]?.id || ""));
        }
      }
      if (["link_document", "dispense"].includes(payload.action)) {
        setOpenRequirementAction(null);
        if (payload.action === "dispense") {
          setDispenseReasons(current => {
            const next = { ...current };
            delete next[payload.requirementId];
            return next;
          });
        }
      }
      setNotice(result?.message || "Ação registrada.");
      await loadData();
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  async function sendRealEmail() {
    if (!data?.csrf || !detail || busy) return;
    if (detail.deliveries?.some(row => !row.is_simulated && row.status === "ENVIANDO")) {
      setError("Há uma tentativa em andamento ou com resultado incerto. Confira o histórico antes de reenviar."); return;
    }
    if (deliveryChannel === "WHATSAPP" && !data.zproConfigured) { setError("Configure e ative o ZPRO antes do envio."); return; }
    if (deliveryChannel === "EMAIL" && !data.smtpPasswordConfigured) { setError("Salve a senha SMTP em Central DP · E-mail de teste antes de enviar."); return; }
    if (!documentIds.length) { setError("Selecione ao menos um documento anexado para enviar."); return; }
    const selectedNames = visibleRequirements.filter(req => documentIds.includes(req.id)).map(req => req.document_name || req.label);
    const confirmed = window.confirm(`Enviar de verdade por ${deliveryChannel === "WHATSAPP" ? "WhatsApp" : "e-mail"} para o contato cadastrado desta pessoa?\n\nAnexos:\n${selectedNames.map(name => `• ${name}`).join("\n")}`);
    if (!confirmed) return;
    if (!deliveryRequestId.current) deliveryRequestId.current = window.crypto.randomUUID();
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/ui/rescissions/${encodeURIComponent(detail.id)}/${deliveryChannel === "WHATSAPP" ? "whatsapp" : "deliver-email"}`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf, message, documentIds, requestId: deliveryRequestId.current }),
      });
      const result = await response.json().catch(() => null);
      if (result?.case) setDetail(result.case);
      if (!response.ok) throw new Error(result?.error || "Não foi possível enviar o e-mail.");
      setNotice(result.message || "O servidor SMTP aceitou o e-mail. Confira o recebimento na caixa do destinatário.");
      deliveryRequestId.current = null;
      if (result?.case) setDocumentIds(attachedRequirementIds(result.case));
      await loadCase(detail.id);
      await loadData();
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  const items = data?.items || [];
  const hasActiveFilters = [search, company, reason, phase, dateFrom, dateTo].some(Boolean);
  const visibleRequirements = detail ? (detail.is_test ? detail.requirements.filter(row => row.code === "TRCT") : detail.requirements) : [];
  const pendingEmail = detail?.deliveries?.some(row => !row.is_simulated && row.status === "ENVIANDO");
  const canEditDocuments = Boolean(data?.canManage && detail && !detail.source_review && !["CONCLUIDO", "CANCELADO"].includes(detail.status));

  function resetFilters() {
    setSearch(""); setCompany(""); setReason(""); setPhase(""); setDateFrom(""); setDateTo("");
  }

  function toggleDocument(id) {
    setDocumentIds(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  }

  function toggleRequirementAction(requirementId, action) {
    const sameActionIsOpen = openRequirementAction?.requirementId === requirementId && openRequirementAction.action === action;
    if (openRequirementAction?.action === "import") {
      setUploadFiles(current => {
        const next = { ...current };
        delete next[openRequirementAction.requirementId];
        return next;
      });
    }
    setOpenRequirementAction(sameActionIsOpen ? null : { requirementId, action });
  }

  function closeRequirementAction() {
    if (openRequirementAction?.action === "import") {
      setUploadFiles(current => {
        const next = { ...current };
        delete next[openRequirementAction.requirementId];
        return next;
      });
    }
    setOpenRequirementAction(null);
  }

  function renderRequirement(req) {
    const panelOpen = action => openRequirementAction?.requirementId === req.id && openRequirementAction.action === action;
    const uploadedFile = uploadFiles[req.id] || null;
    const dispenseReason = dispenseReasons[req.id] || "";
    const realDelivery = detail?.deliveries?.some(row => !row.is_simulated && row.status === "ENVIADO" && row.documents?.includes(req.document_name));
    const statusLabel = req.status === "PENDENTE" ? "Pendente" : req.status === "ANEXADO" ? "Anexado" : req.status === "ENTREGUE" ? (realDelivery ? "Enviado pelo canal" : "Registro simulado anterior") : "Dispensado";
    return <article className={`rescissions-requirement status-${req.status.toLowerCase()}`} key={req.id}>
      <div className="rescissions-requirement-summary">
        <div><strong>{req.label}</strong><small>{req.mandatory ? "Obrigatório" : "Uso interno / opcional"} · {req.status === "ANEXADO" || req.status === "ENTREGUE" ? req.document_name : req.status === "DISPENSADO" ? req.exception_reason : "Aguardando documento"}</small></div>
        <span>{statusLabel}</span>
      </div>
      {canEditDocuments && <>
        <div className="rescissions-requirement-actions">
          <button type="button" className="btn btn-outline-secondary btn-sm" aria-expanded={panelOpen("link")} aria-controls={`requirement-${req.id}-link`} onClick={() => toggleRequirementAction(req.id, "link")} disabled={busy}>Vincular do prontuário {panelOpen("link") ? "−" : "+"}</button>
          <button type="button" className="btn btn-outline-secondary btn-sm" aria-expanded={panelOpen("import")} aria-controls={`requirement-${req.id}-import`} onClick={() => toggleRequirementAction(req.id, "import")} disabled={busy}>Importar e vincular {panelOpen("import") ? "−" : "+"}</button>
          <button type="button" className="btn btn-outline-danger btn-sm" aria-expanded={panelOpen("dispense")} aria-controls={`requirement-${req.id}-dispense`} onClick={() => toggleRequirementAction(req.id, "dispense")} disabled={busy}>Dispensar item {panelOpen("dispense") ? "−" : "+"}</button>
        </div>
        {(panelOpen("link") || panelOpen("import") || panelOpen("dispense")) && <div className="rescissions-requirement-panels">
          {panelOpen("link") && <section className="rescissions-action-panel" id={`requirement-${req.id}-link`}>
            <div className="rescissions-action-panel-head"><strong>Vincular do prontuário</strong><button type="button" aria-label="Fechar vinculação do prontuário" onClick={closeRequirementAction}>−</button></div>
            <form className="rescissions-action-form rescissions-action-form-link" onSubmit={event => { event.preventDefault(); mutate({ action: "link_document", caseId: detail.id, requirementId: req.id, documentId: selectedDocumentId }); }}>
              <label>Documento do prontuário<select aria-label="Documento do prontuário" value={selectedDocumentId} disabled={busy || !detail.available_documents?.length} onChange={event => setSelectedDocumentId(event.target.value)}><option value="">{detail.available_documents?.length ? "Selecione o arquivo do prontuário" : "Prontuário sem documentos"}</option>{(detail.available_documents || []).map(doc => <option key={doc.id} value={String(doc.id)}>{doc.filename} · {formatDate(String(doc.created_at || "").slice(0, 10))}</option>)}</select></label>
              <button type="submit" className="btn btn-success btn-sm" disabled={busy || !selectedDocumentId}>{busy ? "Vinculando…" : "Vincular"}</button>
              {!detail.available_documents?.length && <small className="rescissions-action-help">Importe um arquivo ao prontuário para poder vinculá-lo aqui.</small>}
            </form>
          </section>}
          {panelOpen("import") && <section className="rescissions-action-panel" id={`requirement-${req.id}-import`}>
            <div className="rescissions-action-panel-head"><strong>Importar e vincular</strong><button type="button" aria-label="Fechar importação" onClick={closeRequirementAction}>−</button></div>
            <form className="rescissions-action-form rescissions-action-form-import" onSubmit={event => { event.preventDefault(); uploadDocument(uploadedFile, req); }}>
              <label>Tipo para indexação<select value={req.code} disabled><option value={req.code}>{req.label} ({req.code})</option></select></label>
              <label>Arquivo<input type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg" disabled={busy} onChange={event => { const file = event.currentTarget.files?.[0] || null; setUploadFiles(current => ({ ...current, [req.id]: file })); }}/></label>
              <button type="submit" className="btn btn-success btn-sm" disabled={busy || !uploadedFile}>{busy ? "Importando…" : "Importar"}</button>
            </form>
          </section>}
          {panelOpen("dispense") && <section className="rescissions-action-panel rescissions-action-panel-danger" id={`requirement-${req.id}-dispense`}>
            <div className="rescissions-action-panel-head"><strong>Dispensar item</strong><button type="button" aria-label="Fechar dispensa" onClick={closeRequirementAction}>−</button></div>
            <form className="rescissions-action-form rescissions-action-form-link" onSubmit={event => { event.preventDefault(); mutate({ action: "dispense", caseId: detail.id, requirementId: req.id, reason: dispenseReason }); }}>
              <label>Justificativa<input type="text" maxLength={500} value={dispenseReason} onChange={event => setDispenseReasons(current => ({ ...current, [req.id]: event.target.value }))} placeholder="Informe a justificativa"/></label>
              <button type="submit" className="btn btn-danger btn-sm" disabled={busy || dispenseReason.trim().length < 8}>{busy ? "Salvando…" : "Dispensar"}</button>
              <small className="rescissions-action-help">A justificativa precisa ter pelo menos 8 caracteres.</small>
            </form>
          </section>}
        </div>}
      </>}
    </article>;
  }

  async function uploadDocument(file, requirement) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      setError("O arquivo deve ter no máximo 10 MB.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/ui/rescissions/${detail.id}/requirements/${requirement.id}/upload`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": file.type || "application/octet-stream", "X-CSRF-Token": data?.csrf || "", "X-File-Name": encodeURIComponent(file.name) },
        body: file,
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(result?.error || "Não foi possível anexar o documento.");
      setDetail(result.case);
      setSelectedDocumentId(String(result.case.available_documents?.[0]?.id || ""));
      setNotice(result.message || "Documento anexado ao prontuário.");
      setDocumentIds(attachedRequirementIds(result.case));
      setUploadFiles(current => {
        const next = { ...current };
        delete next[requirement.id];
        return next;
      });
      setOpenRequirementAction(null);
      await loadData();
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  return <>
    
    <section className="container-fluid page-content rescissions-page operations-page">
      <div className="rescissions-heading operations-intro"><div><p className="rescissions-eyebrow operations-eyebrow">{caseId ? "CASO DE DESLIGAMENTO" : "CONTROLE DOCUMENTAL"}</p><h2>{caseId ? "Detalhes e documentos" : "Documentos, prazos e entregas"}</h2><p>{caseId ? "Revise o prazo, organize os documentos e acompanhe a comunicação deste caso." : "Acompanhe a documentação e a comunicação no prazo operacional de 10 dias corridos após o desligamento."}</p></div>{caseId ? <a className="btn btn-outline-secondary operations-secondary-action" href="/central-dp/rescisoes">← Voltar para a lista</a> : <button className="btn btn-outline-primary operations-secondary-action" type="button" onClick={() => loadData()} disabled={loading}><OperationsIcon name="refresh"/>{loading ? "Atualizando…" : "Atualizar lista"}</button>}</div>
      {caseId ? <div className="rescissions-simulation-note"><strong>Comunicação com o trabalhador.</strong> Confira mensagem, documentos e canal antes do envio. O recebimento é registrado separadamente no histórico.</div> : <div className="operations-note"><OperationsIcon name="info"/><p>Desligamentos do SIB entram na fila automaticamente. Dentro do caso, organize os documentos e escolha e-mail ou WhatsApp. {dateFrom && <>Seleção desde {formatDate(dateFrom)}.</>}</p></div>}
      <ZproSettings canConfigure={Boolean(data?.canConfigure)} onSaved={() => loadData(true)}/>

      {error && <div className="rescissions-alert" role="alert">{error}</div>}
      {notice && <div className="rescissions-notice" role="status">{notice}</div>}
      {loading && !data && <div className="rescissions-empty card" role="status">Carregando casos de desligamento…</div>}
      {data && !caseId && <>
        <div className="rescissions-stats" aria-label="Indicadores de prazo">
          <button type="button" onClick={() => setPhase("")} className={`rescissions-stat operations-stat stat-total ${!phase ? "is-active" : ""}`} aria-pressed={!phase}><span className="operations-stat-top"><OperationsIcon name="files"/><strong>{data.summary?.total || 0}</strong></span><span>Todos os casos</span><small>Registros na base</small></button>
          {filterCards.map(([key, label, field]) => <button type="button" key={key} onClick={() => setPhase(current => current === key ? "" : key)} className={`rescissions-stat operations-stat stat-${key.toLowerCase()} ${phase === key ? "is-active" : ""}`} aria-pressed={phase === key}><span className="operations-stat-top"><OperationsIcon name={phaseIcons[key]}/><strong>{data.summary?.[field] || 0}</strong></span><span>{label}</span><small>{key === "VENCIDOS" ? "Prioridade imediata" : key === "URGENTES" ? "Até 2 dias restantes" : key === "NO_PRAZO" ? "De 3 a 10 dias" : "Entrega registrada"}</small></button>)}
        </div>

        <section className="rescissions-filters card operations-filter-card" aria-labelledby="rescissions-filter-title">
          <h3 id="rescissions-filter-title" className="operations-filter-title"><OperationsIcon name="filter"/>Filtrar rescisões</h3>
          <div className="operations-filter-main">
            <label className="operations-search">Buscar pessoa<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nome, matrícula ou CPF"/></label>
            <label>Empresa<input value={company} list="rescission-companies" onChange={event => setCompany(event.target.value)} placeholder="Todas as empresas"/><datalist id="rescission-companies">{data.companies.map(value => <option key={value} value={value}/>)}</datalist></label>
            <label>Fase operacional<select value={phase} onChange={event => setPhase(event.target.value)}><option value="">Todas as fases</option><option value="NO_PRAZO">No prazo de 10 dias</option><option value="URGENTES">Atenção final · até 2 dias</option><option value="VENCIDOS">Vencidos</option><option value="PENDENTES">Pendentes</option><option value="CONCLUIDOS">Concluídos</option><option value="CONFERIR">Conferir origem SIB</option></select></label>
          </div>
          <details className="operations-more-filters"><summary><span>Mais filtros</span>{(dateFrom || dateTo || reason) && <b>{[dateFrom,dateTo,reason].filter(Boolean).length}</b>}<small>Período de desligamento e motivo</small><OperationsIcon name="chevron"/></summary><div className="operations-extra-grid">
            <label>Desligamento a partir de<input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)}/></label>
            <label>Desligamento até<input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)}/></label>
            <label>Motivo<select value={reason} onChange={event => setReason(event.target.value)}><option value="">Todos os motivos</option>{reasonOptions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          </div></details>
          <div className="operations-filter-footer"><span>{[search,company,phase,dateFrom,dateTo,reason].filter(Boolean).length ? `${[search,company,phase,dateFrom,dateTo,reason].filter(Boolean).length} filtro(s) aplicado(s).` : "Todos os casos disponíveis."} A lista atualiza automaticamente.</span><button type="button" className="operations-clear" onClick={resetFilters}>Limpar filtros</button></div>
        </section>

        <section className="rescissions-list-card">
          <div className="operations-results-heading"><div><h3>Desligamentos nesta seleção</h3><p>Pendências primeiro, em ordem de urgência.</p></div><span>{items.length} de {data.total ?? data.items.length} casos</span></div>
          {items.length ? <div className="rescissions-list">{items.map(item => <article className={`rescissions-row ${item.status === "CONCLUIDO" ? "is-completed" : item.days_remaining < 0 ? "is-overdue" : item.days_remaining <= 2 ? "is-urgent" : ""}`} key={item.id}>
            <div className="rescissions-person"><strong>{item.name}</strong>{Boolean(item.is_test) && <em>Dados de teste</em>}{item.source_system === "SIB" && <em>SIB{item.source_review ? " · conferir origem" : ""}</em>}<small>{item.company} · {item.unit || "Unidade não informada"}</small><small>Matrícula {item.registration} · CPF {item.cpf || "não informado"}</small></div>
            <div className="rescissions-date"><span className="operations-data-label">Desligamento</span><strong>{formatDate(item.dismissal_date)}</strong><small>{item.reason_label || "Motivo não informado"}</small></div>
            <div className="rescissions-deadline"><strong>{item.status === "CONCLUIDO" ? (item.real_delivery_count ? "Envio registrado" : item.delivery_count ? "Concluído · simulação" : "Concluído") : phaseLabels[item.operational_phase || item.deadline_state]}</strong><small>{item.status === "CONCLUIDO" ? `${item.real_delivery_count ? item.real_delivery_count + " envio(s) real(is)" : item.delivery_count + " registro(s) anterior(es)"}` : `${dateHint(item.days_remaining)} · prazo ${formatDate(item.deadline_date)}`}</small></div>
            <div className="rescissions-checklist"><strong>{item.pending_documents} pendente(s)</strong><small>{item.attached_documents} documento(s) anexado(s)</small></div>
            <a className="btn btn-outline-primary operations-row-action" href={`/central-dp/rescisoes/${item.id}`}>Gerenciar<OperationsIcon name="arrow"/></a>
          </article>)}</div> : <div className="rescissions-empty"><strong>{hasActiveFilters ? "Nenhuma rescisão corresponde aos filtros." : "Ainda não há desligamentos cadastrados."}</strong><p>{hasActiveFilters ? "Ajuste ou limpe os filtros para ver outros casos." : "Cadastre a pessoa e o vínculo em Pessoas; ao informar a data e o motivo da saída, a rescisão será criada aqui. Você também pode gerar exemplos fictícios para explorar o fluxo."}</p>{hasActiveFilters ? <button type="button" className="btn btn-outline-primary" onClick={resetFilters}>Limpar seleção</button> : <a className="btn btn-outline-primary" href="/cadastros/pessoas">Abrir Pessoas</a>}</div>}
        </section>
        {data.total > data.pageSize && <nav className="absence-pagination" aria-label="Páginas de rescisões"><button type="button" className="btn btn-outline-primary" disabled={loading || data.page <= 1} onClick={() => setPage(data.page - 1)}>Anterior</button><span>Página {data.page} de {Math.ceil(data.total / data.pageSize)}</span><button type="button" className="btn btn-outline-primary" disabled={loading || data.page * data.pageSize >= data.total} onClick={() => setPage(data.page + 1)}>Próxima</button></nav>}
        {data.canCreateDemo && <details className="operations-help"><summary><OperationsIcon name="settings"/>Opções de demonstração<OperationsIcon name="chevron"/></summary><div><p>Crie cadastros fictícios para conhecer as etapas de documentação e entrega.</p><button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={() => mutate({ action: "create_demo", today: data.today })}>{busy ? "Preparando…" : "Criar exemplos de teste"}</button></div></details>}
      </>}
      {caseId && data && <section id="rescission-detail" className="rescissions-detail card">
        {!detail ? error ? <div className="rescissions-empty" role="alert"><strong>Não foi possível carregar esta rescisão.</strong><p>{error}</p><a className="btn btn-outline-secondary" href="/central-dp/rescisoes">Voltar para a lista</a></div> : <p role="status">Carregando detalhes…</p> : <>
          <div className="rescissions-detail-head"><div><p className="rescissions-eyebrow">GERENCIAR RESCISÃO {Boolean(detail.is_test) && "· TESTE"}</p><h2>{detail.name}</h2><p>{detail.company} · matrícula {detail.registration} · desligamento {formatDate(detail.dismissal_date)}</p></div><button type="button" className="btn btn-outline-primary" disabled={busy} onClick={() => loadCase(caseId)}>Atualizar dados</button></div>
            <div className={`rescissions-deadline-banner ${detail.days_remaining < 0 ? "is-overdue" : detail.days_remaining <= 2 ? "is-urgent" : detail.status === "CONCLUIDO" ? "is-completed" : ""}`}><strong>{detail.status === "CONCLUIDO" ? (detail.deliveries?.some(row => !row.is_simulated && row.status === "ENVIADO") ? "Envio aceito pelo canal" : detail.deliveries?.some(row => row.is_simulated) ? "Concluído em simulação anterior" : "Concluído") : `${phaseLabels[detail.deadline_state]} · ${dateHint(detail.days_remaining)}`}</strong><span>Prazo operacional: 10 dias corridos após o desligamento · {formatDate(detail.deadline_date)}</span></div>
          {detail.source_review ? <div className="operations-note"><p><strong>Conferir alteração da origem.</strong> {detail.source_message}</p>{data.canManage && <button type="button" className="btn btn-outline-primary" disabled={busy} onClick={() => { const reason = window.prompt("Registre a conferência das datas e dos documentos deste desligamento:"); if (reason) mutate({ action: "confirm_source", caseId: detail.id, reason }); }}>Registrar conferência</button>}</div> : detail.source_system === "SIB" && <p className="operations-note">Origem SIB · histórico e documentos vinculados à admissão correta. <a href={`/cadastros/pessoas?id=${encodeURIComponent(detail.employment_key)}`}>Abrir ficha →</a></p>}
          <div className="rescissions-section-head"><div><h3>Checklist de documentos</h3><p>{detail.is_test ? "Neste caso de teste, o checklist mostra apenas o TRCT. O arquivo anexado será enviado de verdade por e-mail." : "Anexe ou reutilize os arquivos do prontuário. Os itens selecionados abaixo serão enviados de verdade por e-mail."}</p></div></div>
          <div className="rescissions-requirements">{visibleRequirements.map(renderRequirement)}</div>
          <form className="rescissions-delivery" onSubmit={event => { event.preventDefault(); sendRealEmail(); }}>
            <div><h3>Comunicar o desligamento</h3><p>O contato vem da ficha da pessoa. Confira os documentos selecionados.</p></div>
            <label>Canal de envio<select value={deliveryChannel} onChange={event => { setDeliveryChannel(event.target.value); deliveryRequestId.current = null; }} disabled={busy}><option value="EMAIL">E-mail</option><option value="WHATSAPP" disabled={Boolean(detail.is_test)}>WhatsApp · ZPRO</option></select></label>
            <div className="rescissions-delivery-grid"><label>Mensagem<textarea rows="4" maxLength="5000" value={message} onChange={event => setMessage(event.target.value)} required/></label></div>
            <fieldset><legend>Documentos que serão enviados</legend>{visibleRequirements.filter(req => ["ANEXADO", "ENTREGUE"].includes(req.status)).length ? visibleRequirements.filter(req => ["ANEXADO", "ENTREGUE"].includes(req.status)).map(req => <label className="rescissions-doc-choice" key={req.id}><input type="checkbox" checked={documentIds.includes(req.id)} onChange={() => toggleDocument(req.id)}/><span>{req.document_name || req.label}</span></label>) : <small>Nenhum documento anexado. Importe ou vincule um arquivo antes de enviar.</small>}</fieldset>
            <p className="rescissions-simulation-note">A mensagem e os arquivos serão enviados pelo canal escolhido. O aceite do canal não comprova o recebimento pelo trabalhador.</p>
            {deliveryChannel === "EMAIL" && !data.smtpPasswordConfigured && <p className="rescissions-smtp-missing">Configure o <a href="/central-dp/email">e-mail de saída</a> antes de enviar.</p>}
            {deliveryChannel === "WHATSAPP" && !data.zproConfigured && <p className="rescissions-smtp-missing">A conexão ZPRO precisa ser configurada e ativada por um administrador.</p>}
            {pendingEmail && <p className="rescissions-smtp-missing">Há uma tentativa em andamento ou de resultado incerto. Confira o histórico antes de reenviar.</p>}
            <button type="submit" className="btn btn-success" disabled={busy || pendingEmail || Boolean(detail.source_review) || !data.canManage || !data.canSend || detail.status === "CANCELADO" || !documentIds.length || (deliveryChannel === "EMAIL" ? !data.smtpPasswordConfigured : !data.zproConfigured)}>{busy ? "Enviando…" : deliveryChannel === "EMAIL" ? "Enviar por e-mail" : "Enviar pelo ZPRO"}</button>
          </form>
          <RescissionDeliveryHistory detail={detail} csrf={data.csrf} canManage={Boolean(data.canManage && data.canSend)} onUpdated={async () => { await loadCase(detail.id); await loadData(true); }}/>

          <section className="rescissions-history"><h3>Trilha de auditoria</h3>{detail.events.length ? <ol>{detail.events.map(event => <li key={event.id}><strong>{event.event_type.replaceAll("_", " ")}</strong><span>{event.actor} · {event.created_at}</span></li>)}</ol> : <p>Nenhuma ação registrada.</p>}</section>
        </>}
      </section>}
    </section>
  </>;
}
