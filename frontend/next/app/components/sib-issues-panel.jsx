"use client";

import { useEffect, useRef, useState } from "react";

async function readJson(response) {
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("Resposta inesperada do servidor V3.");
  if (!response.ok) throw new Error(payload.error || payload.message || "Não foi possível concluir a operação.");
  return payload;
}

async function readJsonFetch(path, signal) {
  return readJson(await fetch(path, { credentials: "same-origin", cache: "no-store", signal }));
}

export default function SibIssuesPanel({ csrf }) {
  const [issuesQuery, setIssuesQuery] = useState({ page: 1, status: "open", q: "" });
  const [issuesSearch, setIssuesSearch] = useState("");
  const [issues, setIssues] = useState(null);
  const [issuesLoading, setIssuesLoading] = useState(false);
  const [issuesError, setIssuesError] = useState("");
  const [issuesNotice, setIssuesNotice] = useState("");
  const [issuesRefresh, setIssuesRefresh] = useState(0);
  const [issueBusy, setIssueBusy] = useState("");
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [issueDetail, setIssueDetail] = useState(null);
  const [issueDetailLoading, setIssueDetailLoading] = useState(false);
  const [issueDetailError, setIssueDetailError] = useState("");
  const [issueDetailRefresh, setIssueDetailRefresh] = useState(0);
  const [settings, setSettings] = useState(null);
  const [settingsRefresh, setSettingsRefresh] = useState(0);
  const [issueIdentity, setIssueIdentity] = useState({ cpf: "", admissionDate: "", takerCnpj: "" });
  const issuesRevision = useRef(0);
  const previousIssueJobActive = useRef(false);
  const issueDetailHeading = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    readJsonFetch("/api/integrations/settings", controller.signal)
      .then(setSettings)
      .catch(reason => { if (reason.name !== "AbortError") setIssuesError(reason.message); });
    return () => controller.abort();
  }, [settingsRefresh]);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    const revision = ++issuesRevision.current;
    setIssuesLoading(true); setIssuesError("");
    const query = new URLSearchParams({ page: String(issuesQuery.page), status: issuesQuery.status, q: issuesQuery.q });
    readJsonFetch(`/api/integrations/sib/issues?${query}`, controller.signal)
      .then(result => { if (!cancelled && revision === issuesRevision.current) setIssues(result); })
      .catch(reason => { if (!cancelled && reason.name !== "AbortError") setIssuesError(reason.message); })
      .finally(() => { if (!cancelled) setIssuesLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [issuesQuery.page, issuesQuery.status, issuesQuery.q, issuesRefresh]);

  useEffect(() => {
    if (!selectedIssueId) { setIssueDetail(null); return; }
    const controller = new AbortController();
    let cancelled = false;
    setIssueDetailLoading(true); setIssueDetailError("");
    readJsonFetch(`/api/integrations/sib/issues/${encodeURIComponent(selectedIssueId)}`, controller.signal)
      .then(result => { if (!cancelled) setIssueDetail(result); })
      .catch(reason => { if (!cancelled && reason.name !== "AbortError") setIssueDetailError(reason.message); })
      .finally(() => { if (!cancelled) setIssueDetailLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [selectedIssueId, issueDetailRefresh]);

  const issueJob = issues?.job;
  const issueJobActive = Boolean(issueJob?.running || issueJob?.inFlight);
  const scanActive = Boolean(settings?.sibScan?.running || settings?.sibScan?.inFlight || settings?.sibScan?.autoResume);
  const canSib = Boolean(settings?.sib?.active && settings?.sib?.apiKeyConfigured);
  const issueActionsBlocked = !canSib || Boolean(issueBusy) || scanActive || issueJobActive;

  useEffect(() => {
    if (!issueJobActive) return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function pollIssues() {
      const revision = issuesRevision.current;
      try {
        const query = new URLSearchParams({ page: String(issuesQuery.page), status: issuesQuery.status, q: issuesQuery.q });
        const result = await readJsonFetch(`/api/integrations/sib/issues?${query}`, controller.signal);
        if (!cancelled && revision === issuesRevision.current) {
          setIssues(result); setIssuesError("");
          if (!result.job?.running && !result.job?.inFlight) return;
        }
      } catch (reason) {
        if (cancelled || reason.name === "AbortError") return;
        setIssuesError("Não foi possível atualizar o tratamento. A tela tentará novamente em instantes.");
      }
      if (!cancelled) timer = window.setTimeout(pollIssues, 3000);
    }
    timer = window.setTimeout(pollIssues, 3000);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [issueJobActive, issuesQuery.page, issuesQuery.status, issuesQuery.q]);

  useEffect(() => {
    if (!scanActive && !issueJobActive) return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function pollSettings() {
      try {
        const result = await readJsonFetch("/api/integrations/settings", controller.signal);
        if (!cancelled) {
          setSettings(result);
          if (!result.sibScan?.autoResume && !result.sibScan?.running && !result.sibScan?.inFlight && !result.sibIssueJob?.running && !result.sibIssueJob?.inFlight) return;
        }
      } catch (reason) {
        if (cancelled || reason.name === "AbortError") return;
      }
      if (!cancelled) timer = window.setTimeout(pollSettings, 3000);
    }
    timer = window.setTimeout(pollSettings, 3000);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [scanActive, issueJobActive]);

  useEffect(() => {
    if (previousIssueJobActive.current && !issueJobActive) {
      setIssueDetailRefresh(current => current + 1);
      setIssuesRefresh(current => current + 1);
      setSettingsRefresh(current => current + 1);
    }
    previousIssueJobActive.current = issueJobActive;
  }, [issueJobActive]);

  useEffect(() => {
    if (!selectedIssueId) return;
    issueDetailHeading.current?.focus({ preventScroll: true });
    issueDetailHeading.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedIssueId]);

  useEffect(() => {
    if (window.location.hash !== "#sib-issues") return;
    window.requestAnimationFrame(() => document.getElementById("sib-issues")?.scrollIntoView({ block: "start" }));
  }, []);

  function searchIssues(event) {
    event.preventDefault();
    const value = issuesSearch.trim();
    if (value && !/^\d{1,6}$/.test(value)) { setIssuesError("Busque uma matrícula entre 0 e 999999."); return; }
    setIssuesQuery(current => ({ ...current, q: value, page: 1 }));
    setIssuesRefresh(current => current + 1);
  }

  function openIssue(payrollId) {
    setSelectedIssueId(String(payrollId)); setIssueDetail(null); setIssueDetailError("");
    setIssueIdentity({ cpf: "", admissionDate: "", takerCnpj: "" });
    setIssueDetailRefresh(current => current + 1);
  }

  async function treatIssue(action, payrollId = "", identity = null) {
    if (!csrf || issueBusy || scanActive || (issueJobActive && action !== "pause")) return;
    setIssueBusy(action); setIssuesError(""); setIssuesNotice("");
    issuesRevision.current += 1;
    try {
      const result = await readJson(await fetch("/api/integrations/sib/issues", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...(payrollId !== "" ? { payrollId } : {}), ...identity, csrf }),
      }));
      issuesRevision.current += 1;
      setIssues(current => ({ ...current, job: result.job }));
      setIssuesNotice(result.message || "Solicitação de tratamento registrada.");
      if (action === "identify") setIssueIdentity({ cpf: "", admissionDate: "", takerCnpj: "" });
      if (!result.job?.running && !result.job?.inFlight) {
        setIssueDetailRefresh(current => current + 1);
        setSettingsRefresh(current => current + 1);
      }
    } catch (reason) { setIssuesError(reason.message); }
    finally { issuesRevision.current += 1; setIssueBusy(""); setIssuesRefresh(current => current + 1); }
  }

  function identifyIssue(event) {
    event.preventDefault();
    const identity = { ...issueIdentity, cpf: issueIdentity.cpf.replace(/\D/g, ""), takerCnpj: issueIdentity.takerCnpj.replace(/\D/g, "") };
    if (identity.cpf.length !== 11 || identity.takerCnpj.length !== 14 || !/^\d{4}-\d{2}-\d{2}$/.test(identity.admissionDate)) {
      setIssuesError("Informe CPF, data de admissão e CNPJ do tomador válidos para identificar a matrícula."); return;
    }
    treatIssue("identify", selectedIssueId, identity);
  }

  const sibScan = settings?.sibScan;
  const inconclusiveCount = Number(sibScan?.inconclusive ?? sibScan?.multipleMatches ?? 0);
  const scanMetrics = [
    ["records", "Cadastros trazidos"], ["companies", "Empresas associadas"],
    ["missing", "IDs sem cadastro confirmado"], ["pending", "Fichas na fila"],
    ["inconclusive", "Retornos inconclusivos"], ["reviewRequired", "Outras pendências"],
  ];

  return <section id="sib-issues" className="card integrations-card integrations-operation-card integrations-issues" aria-labelledby="sib-issues-heading">
    <div className="integrations-card-heading"><div><p>CADASTROS SIB · INCONSISTÊNCIAS</p><h3 id="sib-issues-heading">Conferir pendências e ocorrências</h3></div><span>{Number(issues?.total || 0).toLocaleString("pt-BR")} ocorrência(s) nesta seleção</span></div>
    <p className="integrations-copy">Pendências de consultas SIB, falhas de identificação e registros que precisam de revisão ficam reunidos aqui. Uma resposta inconclusiva não confirma ausência nem duplicidade de cadastro.</p>
    {sibScan?.error && <div className="integrations-alert is-error" role="alert">{sibScan.error}</div>}
    {sibScan?.started && <section className="integrations-issue-job" aria-label="Resumo da consulta SIB"><strong>Resumo da faixa consultada</strong><span>{Number(sibScan.checked || 0).toLocaleString("pt-BR")} matrícula(s) verificadas</span><dl className="integrations-scan-stats">{scanMetrics.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{Number((key === "inconclusive" ? inconclusiveCount : sibScan[key]) || 0).toLocaleString("pt-BR")}</dd></div>)}</dl>{Array.isArray(sibScan.inconclusiveIds) && sibScan.inconclusiveIds.length > 0 && <p>Retornos inconclusivos: {sibScan.inconclusiveIds.join(", ")}</p>}{Array.isArray(sibScan.reviewIds) && sibScan.reviewIds.length > 0 && <p>Outras pendências: {sibScan.reviewIds.join(", ")}</p>}{scanActive && <p role="status">A consulta por faixa está ativa. Aguarde terminar antes de reconsultar ocorrências.</p>}<a className="btn btn-outline-primary btn-sm" href="/central-dp/integracoes#sib-scan">Abrir consulta SIB</a></section>}
    {settings && !canSib && <p className="integrations-copy">As consultas e reconsultas exigem a Bridge ativa. <a href="/central-dp/integracoes">Abrir configurações de Integrações</a>.</p>}
    {issuesError && <div className="integrations-alert is-error" role="alert">{issuesError}</div>}
    {issuesNotice && <div className="integrations-alert is-success" role="status">{issuesNotice}</div>}
    <form className="integrations-issues-filter" onSubmit={searchIssues}>
      <label>Matrícula<input inputMode="numeric" maxLength={6} value={issuesSearch} onChange={event => setIssuesSearch(event.target.value)} placeholder="Número exato ou todas" /></label>
      <label>Situação<select value={issuesQuery.status} onChange={event => setIssuesQuery(current => ({ ...current, status: event.target.value, page: 1 }))}><option value="open">Abertas</option><option value="resolved">Resolvidas</option><option value="all">Todas</option></select></label>
      <button className="btn btn-outline-primary" type="submit" disabled={issuesLoading}>{issuesLoading ? "Carregando…" : "Buscar matrícula"}</button>
      <button className="btn btn-outline-primary" type="button" onClick={() => { setIssuesSearch(""); setIssuesQuery({ page: 1, status: "open", q: "" }); setIssuesRefresh(current => current + 1); }} disabled={issuesLoading}>Limpar busca</button>
    </form>
    <div className="integrations-inline-actions">
      <button className="btn btn-primary" type="button" onClick={() => treatIssue("retry-open")} disabled={issueActionsBlocked || issuesLoading || !issues}>{issueBusy === "retry-open" ? "Preparando reconsultas…" : "Reconsultar próximas 50 abertas"}</button>
      {issueJobActive && <button className="btn btn-outline-primary" type="button" onClick={() => treatIssue("pause")} disabled={Boolean(issueBusy) || scanActive}>{issueBusy === "pause" ? "Parando…" : "Parar após consulta ativa"}</button>}
      {issuesQuery.q && <small>A reconsulta das próximas 50 usa a fila de ocorrências abertas, independentemente da busca exibida.</small>}
    </div>
    {issueJob && (issueJobActive || Number(issueJob.total || 0) > 0 || issueJob.error) && <div className="integrations-issue-job" role="status"><strong>{issueJob.running ? "Tratamento em andamento" : issueJob.inFlight ? "Concluindo consulta ativa" : "Último tratamento"}</strong><span>{Number(issueJob.processed || 0)} de {Number(issueJob.total || 0)} processadas · {Number(issueJob.resolved || 0)} resolvidas · {Number(issueJob.identified || 0)} identificadas · {Number(issueJob.unresolved || 0)} não resolvidas</span>{issueJob.error && <p className="integrations-issue-error">{issueJob.error}</p>}</div>}
    <div className="integrations-table-wrap" aria-busy={issuesLoading}>
      <table className="table integrations-table integrations-issues-table"><thead><tr><th>Matrícula</th><th>Situação</th><th>Resposta registrada</th><th>Motivo</th><th>Última tentativa</th><th></th></tr></thead><tbody>
        {(issues?.items || []).map(item => <tr key={item.payrollId} className={String(item.payrollId) === selectedIssueId ? "is-selected" : ""}><td>{item.payrollId}</td><td>{item.statusLabel || item.status || "—"}</td><td>{item.reasonCode === "vinculo_ambiguo" ? "Ficha não retornada; causa inconclusiva" : item.reasonLabel || "Consulta inconclusiva"}{item.reasonCode && <code>{item.reasonCode}</code>}</td><td><span className={`integrations-evidence-badge ${item.confirmed ? "is-confirmed" : ""}`}>{item.confirmed ? "Confirmado" : "Não confirmado"}</span></td><td>{item.lastAttemptAt || "—"}<small>{Number(item.attempts || 0)} tentativa(s)</small></td><td><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => openIssue(item.payrollId)} aria-label={`Abrir tratamento da matrícula ${item.payrollId}`}>Abrir</button></td></tr>)}
        {!issues?.items?.length && <tr><td colSpan={6}>{issuesLoading ? "Carregando ocorrências…" : issuesError ? "A lista não pôde ser atualizada." : "Nenhuma ocorrência nesta seleção."}</td></tr>}
      </tbody></table>
    </div>
    <nav className="integrations-pagination integrations-issues-pagination" aria-label="Paginação das ocorrências"><span>Página {issues?.page || issuesQuery.page} de {issues?.pages || 1} · até 50 por página</span><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => setIssuesQuery(current => ({ ...current, page: Math.max(1, (issues?.page || current.page) - 1) }))} disabled={issuesLoading || (issues?.page || issuesQuery.page) <= 1}>Anterior</button><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => setIssuesQuery(current => ({ ...current, page: (issues?.page || current.page) + 1 }))} disabled={issuesLoading || (issues?.page || issuesQuery.page) >= (issues?.pages || 1)}>Próxima</button></nav>
    {selectedIssueId && <section className="integrations-issue-detail" aria-labelledby="sib-issue-detail-heading" aria-busy={issueDetailLoading}>
      <div className="integrations-card-heading"><div><p>DETALHE DA OCORRÊNCIA</p><h3 id="sib-issue-detail-heading" tabIndex={-1} ref={issueDetailHeading}>Matrícula {selectedIssueId}</h3></div><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => { setSelectedIssueId(""); setIssueDetail(null); }}>Fechar detalhe</button></div>
      {issueDetailLoading && <p role="status">Atualizando resposta e histórico…</p>}
      {issueDetailError && <div className="integrations-alert is-error" role="alert">{issueDetailError}</div>}
      {issueDetail?.item && <>
        <dl className="integration-result-fields"><div><dt>Situação</dt><dd>{issueDetail.item.statusLabel || issueDetail.item.status || "—"}</dd></div><div><dt>Motivo</dt><dd>{issueDetail.item.reasonCode === "vinculo_ambiguo" ? "Ficha não retornada; causa inconclusiva" : issueDetail.item.reasonLabel || "Consulta inconclusiva"} · {issueDetail.item.confirmed ? "confirmado" : "não confirmado"}</dd></div><div><dt>Origem da resposta</dt><dd>{String(issueDetail.item.evidence?.origin || issueDetail.item.source || "Não informada")}</dd></div><div><dt>Código recebido</dt><dd>{issueDetail.item.evidence?.code || issueDetail.item.reasonCode || "Não informado"}</dd></div><div><dt>HTTP</dt><dd>{issueDetail.item.evidence?.httpStatus || "Não informado"}</dd></div><div><dt>Tentativas</dt><dd>{Number(issueDetail.item.attempts || 0)}</dd></div>{issueDetail.item.evidence?.requestId && <div><dt>ID da consulta</dt><dd><code>{issueDetail.item.evidence.requestId}</code></dd></div>}</dl>
        <div className="integrations-issue-evidence"><strong>Mensagem registrada</strong><p>{issueDetail.item.evidence?.message || "A origem não forneceu uma mensagem detalhada."}</p>{!issueDetail.item.evidence?.duplicateConfirmed && <small>Esta resposta não confirma duplicidade de cadastros. O histórico de recontratações da pessoa permanece em <a href="/cadastros/vinculos">Vínculos</a>, com matrículas próprias.</small>}</div>
        {issueDetail.item.identity && <div className="integrations-issue-identity"><h4>Identidade retornada</h4><dl className="integration-result-fields">{[["name", "Nome"], ["cpfMasked", "CPF"], ["company", "Empresa"], ["admissionDate", "Admissão"]].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{issueDetail.item.identity[key] || "—"}</dd></div>)}</dl><small>{String(issueDetail.item.status || "").toUpperCase() === "RESOLVED" ? "Cadastro importado na V3." : "A validação de identidade, por si só, não conclui a importação."}</small></div>}
        <div className="integrations-inline-actions"><button className="btn btn-primary" type="button" onClick={() => treatIssue("retry", selectedIssueId)} disabled={issueActionsBlocked || issueDetailLoading}>{issueBusy === "retry" ? "Solicitando reconsulta…" : "Reconsultar matrícula"}</button><small>A resposta e o histórico serão atualizados ao terminar.</small></div>
        <details className="integrations-resolve"><summary>Validar identidade por CPF, admissão e CNPJ do tomador</summary><form className="integrations-issue-identify" onSubmit={identifyIssue}><label>CPF<input inputMode="numeric" autoComplete="off" value={issueIdentity.cpf} onChange={event => setIssueIdentity(current => ({ ...current, cpf: event.target.value }))} maxLength={14} required /></label><label>Admissão<input type="date" value={issueIdentity.admissionDate} onChange={event => setIssueIdentity(current => ({ ...current, admissionDate: event.target.value }))} required /></label><label>CNPJ do tomador<input inputMode="numeric" autoComplete="off" value={issueIdentity.takerCnpj} onChange={event => setIssueIdentity(current => ({ ...current, takerCnpj: event.target.value }))} maxLength={18} required /></label><button className="btn btn-outline-primary" type="submit" disabled={issueActionsBlocked || issueDetailLoading}>{issueBusy === "identify" ? "Validando…" : "Validar identidade"}</button></form><small>Somente valida identidade no SIB; não força importação.</small></details>
        <div className="integrations-issue-history"><h4>Histórico de respostas</h4>{issueDetail.history?.length ? <ol>{issueDetail.history.map((event, index) => <li key={`${event.createdAt}-${index}`}><strong>{event.createdAt || "Data não informada"} · {event.action || "Consulta"}</strong><span>{event.outcome || "Resultado não informado"}{event.httpStatus ? ` · HTTP ${event.httpStatus}` : ""}{event.code ? ` · ${event.code}` : ""}</span>{event.requestId && <small>ID da consulta: <code>{event.requestId}</code></small>}{event.message && <p>{event.message}</p>}</li>)}</ol> : <p>Nenhuma tentativa registrada no histórico.</p>}</div>
      </>}
    </section>}
  </section>;
}
