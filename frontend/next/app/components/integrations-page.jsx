"use client";

import { useEffect, useRef, useState } from "react";
import SibOccurrences from "./sib-occurrences";
import { SyncFold, SyncIcon } from "./integrations-panels";

const STATUSES = ["pending", "pre-exported", "exported", "processing", "accepted", "rejected"];
const SCAN_POLL_ERROR = "Não foi possível atualizar o progresso. A tela tentará novamente em instantes.";
const EVENT_LABELS = { BUSCA_MATRICULAS: "Busca de matrículas", DESCOBERTA_INCREMENTAL: "Novas matrículas e recuperação", HEALTH: "Verificação da conexão", VALIDACAO_FICHA: "Validação de ficha" };
const CHANGE_FIELD_LABELS = {
  "data.name": "Nome", "data.registration": "Matrícula", "data.companyId": "Empresa",
  "data.employerCode": "Código da empresa SIB", "data.branchCode": "Filial", "data.registrationCode": "Matrícula · vínculo", "data.admissionDate": "Admissão",
  "data.dismissalDate": "Desligamento", "data.terminationDate": "Desligamento", "data.firstDueDate": "1º vencimento",
  "data.secondDueDate": "2º vencimento", "data.contractType": "Modalidade do vínculo",
  "data.contractTypeCode": "Tipo de vínculo", "data.internCode": "Código de estágio",
  "data.department": "Departamento", "data.departmentCode": "Código do departamento",
  "data.position": "Cargo", "data.jobTitle": "Cargo", "data.jobCode": "Código do cargo",
  "data.active": "Ativo", "data.status": "Situação", "data.sourceStatus": "Situação informada pelo SIB",
  "data.statusBasis": "Critério da situação", "data.activityDatesValid": "Datas de atividade válidas",
  "data.terminationType": "Tipo de rescisão", "data.dismissalReason": "Motivo do desligamento",
  "data.dismissalReasonCode": "Código do motivo do desligamento", "data.cnpj": "CNPJ",
  "raw.VIN_FUN": "Tipo de vínculo · VIN_FUN", "raw.COD_CLI": "Empresa · COD_CLI",
  "raw.COD_FIL": "Filial · COD_FIL", "raw.ID_FOLHA": "Matrícula · ID_FOLHA",
};

function changeFieldLabel(field) {
  return CHANGE_FIELD_LABELS[field] || (field.startsWith("raw.") ? `Campo SIB · ${field.slice(4)}` : field.replace(/^data\./, ""));
}

function changeValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

async function readJson(response) {
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("Resposta inesperada do servidor V3.");
  if (!response.ok) throw new Error(payload.error || payload.message || "Não foi possível concluir a operação.");
  return payload;
}

async function readSettings(signal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(abort, 15000);
  try {
    return await readJson(await fetch("/api/integrations/settings", {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
    }));
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : /^\d{4}-\d{2}-\d{2} \d{2}:/.test(value) ? value.replace(" ", "T") + "Z" : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatChangeDateTime(value) {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : /^\d{4}-\d{2}-\d{2} \d{2}:/.test(value) ? value.replace(" ", "T") + "Z" : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function IntegrationsPage() {
  const [data, setData] = useState(null);
  const [sib, setSib] = useState({ baseUrl: "", active: false, timeoutSeconds: 20, caBundlePath: "" });
  const [selecty, setSelecty] = useState({ active: false, timeoutSeconds: 20 });
  const [secrets, setSecrets] = useState({ sibApiKey: "", sibCfClientId: "", sibCfClientSecret: "", selectyApiId: "", selectySecret: "" });
  const [remove, setRemove] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState("");
  const [eventPage, setEventPage] = useState(1);
  const [historyEvents, setHistoryEvents] = useState(null);
  const [changeHistoryOpen, setChangeHistoryOpen] = useState(false);
  const [changeHistory, setChangeHistory] = useState(null);
  const [changeHistoryError, setChangeHistoryError] = useState("");
  const [changePage, setChangePage] = useState(1);
  const [changeSearchInput, setChangeSearchInput] = useState("");
  const [changeSearch, setChangeSearch] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [notice, setNotice] = useState("");
  const [sibId, setSibId] = useState("");
  const [sibBatch, setSibBatch] = useState("");
  const [resolve, setResolve] = useState({ cpf: "", admissionDate: "", takerCnpj: "" });
  const [sibResult, setSibResult] = useState(null);
  const [selectyQuery, setSelectyQuery] = useState({ status: "exported", cpf: "", vacancyId: "", page: 1 });
  const [selectyResult, setSelectyResult] = useState(null);
  const [selectyDetail, setSelectyDetail] = useState(null);
  const [scanRange, setScanRange] = useState({ startId: "0", endId: "999999" });
  const [editingScanRange, setEditingScanRange] = useState(false);
  const scanRevision = useRef(0);
  const operationInFlight = useRef(false);

  async function load(signal) {
    scanRevision.current += 1;
    setLoading(true); setError("");
    try {
      const result = await readSettings(signal);
      if (signal?.aborted) return;
      setData(result); setSib(result.sib); setSelecty(result.selecty);
      if (result.sibScan?.started) setScanRange({ startId: String(result.sibScan.startId), endId: String(result.sibScan.endId) });
      setSecrets({ sibApiKey: "", sibCfClientId: "", sibCfClientSecret: "", selectyApiId: "", selectySecret: "" }); setRemove({});
      setPollError("");
    } catch (reason) { if (!signal?.aborted) setError(reason.name === "AbortError" ? "O servidor demorou para responder. Tente carregar novamente." : reason.message); }
    finally { scanRevision.current += 1; if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, []);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(""), 8000); return () => window.clearTimeout(timer); }, [notice]);
  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function poll() {
      const revision = scanRevision.current;
      let nextPoll = 10000;
      try {
        const result = await readSettings(controller.signal);
        if (result.sibScan?.autoResume || result.sibScan?.inFlight || result.sibAutoRefresh?.running || result.sibDiscovery?.running || result.sibDiscovery?.inFlight) nextPoll = 3000;
        else if (result.sibDiscovery?.enabled) nextPoll = 5000;
        if (!cancelled && revision === scanRevision.current) {
          setData(current => ({ ...current, csrf: result.csrf, sib: result.sib, selecty: result.selecty, sibImport: result.sibImport, sibScan: result.sibScan, sibIssueJob: result.sibIssueJob, sibRefresh: result.sibRefresh, sibAutoRefresh: result.sibAutoRefresh, sibDiscovery: result.sibDiscovery, sibAbsences: result.sibAbsences, events: result.events ?? current?.events }));
          setPollError("");
        }
      } catch (reason) {
        if (cancelled) return;
        setPollError(SCAN_POLL_ERROR);
      }
      if (!cancelled) timer = window.setTimeout(poll, document.hidden ? 15000 : nextPoll);
    }
    timer = window.setTimeout(poll, 3000);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [Boolean(data)]);
  useEffect(() => {
    if (!changeHistoryOpen) return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function pollChanges() {
      try {
        const params = new URLSearchParams({ page: String(changePage), q: changeSearch });
        const result = await readJson(await fetch(`/api/integrations/sib/changes?${params}`, {
          credentials: "same-origin", cache: "no-store", signal: controller.signal,
        }));
        if (!cancelled) { setChangeHistory(result); setChangeHistoryError(""); }
      } catch (reason) {
        if (!cancelled && reason.name !== "AbortError") setChangeHistoryError(reason.message || "Não foi possível carregar o histórico de alterações.");
      }
      if (!cancelled) timer = window.setTimeout(pollChanges, data?.sibAutoRefresh?.running ? 5000 : 20000);
    }
    pollChanges();
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [changeHistoryOpen, changePage, changeSearch, Boolean(data?.sibAutoRefresh?.running)]);
  function updateSib(key, value) { setSib(current => ({ ...current, [key]: value })); }
  function updateSelecty(key, value) { setSelecty(current => ({ ...current, [key]: value })); }
  function updateSecret(key, value) { setSecrets(current => ({ ...current, [key]: value })); setRemove(current => ({ ...current, [key]: false })); }

  async function post(path, body, key) {
    if (!data?.csrf || busy || saving || operationInFlight.current) return null;
    const isSibRequest = path.startsWith("/api/integrations/sib/");
    const isSibPause = body?.action === "pause" && [
      "/api/integrations/sib/scan", "/api/integrations/sib/refresh", "/api/integrations/sib/auto-refresh", "/api/integrations/sib/discovery",
    ].includes(path);
    if (isSibRequest && !isSibPause && (scanActive || issueJobActive || refreshActive || discoveryActive || autoRefreshActive)) return null;
    operationInFlight.current = true;
    scanRevision.current += 1;
    setBusy(key); setError(""); setNotice("");
    try {
      const result = await readJson(await fetch(path, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, csrf: data.csrf }),
      }));
      if (result.message) setNotice(result.message);
      return result;
    } catch (reason) { setError(reason.message); return null; }
    finally { scanRevision.current += 1; operationInFlight.current = false; setBusy(""); }
  }

  async function save(event) {
    event.preventDefault(); if (saving || busy || operationInFlight.current || data?.sibAutoRefresh?.running || data?.sibScan?.running || data?.sibScan?.inFlight || data?.sibScan?.autoResume || data?.sibIssueJob?.running || data?.sibIssueJob?.inFlight || data?.sibRefresh?.running || data?.sibRefresh?.inFlight || data?.sibDiscovery?.running || data?.sibDiscovery?.inFlight) return;
    operationInFlight.current = true;
    scanRevision.current += 1;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await readJson(await fetch("/api/integrations/settings", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sibBaseUrl: sib.baseUrl, sibActive: Boolean(sib.active), sibTimeoutSeconds: sib.timeoutSeconds,
          sibCaBundlePath: sib.caBundlePath, selectyActive: Boolean(selecty.active), selectyTimeoutSeconds: selecty.timeoutSeconds,
          ...secrets,
          removeSibApiKey: Boolean(remove.sibApiKey), removeSibCfClientId: Boolean(remove.sibCfClientId),
          removeSibCfClientSecret: Boolean(remove.sibCfClientSecret), removeSelectyApiId: Boolean(remove.selectyApiId),
          removeSelectySecret: Boolean(remove.selectySecret), csrf: data.csrf }),
      }));
      await load(); setNotice(result.message || "Configurações salvas.");
    } catch (reason) { setError(reason.message); }
    finally { scanRevision.current += 1; operationInFlight.current = false; setSaving(false); }
  }

  async function testSib() {
    const result = await post("/api/integrations/sib/test", {}, "sib-test");
    if (result) { await load(); }
  }
  async function importSib(restart = false) {
    if (sibOperationsBlocked) return;
    const result = await post("/api/integrations/sib/import", { restart }, "sib-import");
    if (result?.import) setData(current => ({ ...current, sibImport: result.import }));
  }
  async function scanSib(action) {
    if (sibOperationsBlocked && action !== "pause") return;
    const startId = Number(scanRange.startId), endId = Number(scanRange.endId);
    if ((action === "start" || action === "next" || action === "reset") &&
        (!/^\d{1,6}$/.test(scanRange.startId) || !/^\d{1,6}$/.test(scanRange.endId) || startId > endId)) {
      setError("Informe uma faixa de IDs entre 0 e 999999, com o início menor ou igual ao fim.");
      return;
    }
    const result = await post("/api/integrations/sib/scan", { action, startId, endId }, `sib-scan-${action}`);
    if (result?.scan) {
      setData(current => ({ ...current, sibScan: result.scan }));
      if (action === "reset") setEditingScanRange(false);
    }
  }
  async function refreshSib(action) {
    const result = await post("/api/integrations/sib/refresh", { action }, `sib-refresh-${action}`);
    if (result?.refresh) setData(current => ({ ...current, sibRefresh: result.refresh }));
  }

  async function controlAutoRefresh(action) {
    const result = await post("/api/integrations/sib/auto-refresh", { action }, `sib-auto-refresh-${action}`);
    if (result?.autoRefresh) setData(current => ({ ...current, sibAutoRefresh: result.autoRefresh }));
  }

  async function controlDiscovery(action) {
    const result = await post("/api/integrations/sib/discovery", { action }, `sib-discovery-${action}`);
    if (result?.discovery) setData(current => ({ ...current, sibDiscovery: result.discovery }));
  }
  async function querySib() {
    const result = await post("/api/integrations/sib/employee", { idFolha: sibId }, "sib-query");
    if (result) setSibResult(result.item);
  }
  async function querySibBatch() {
    const result = await post("/api/integrations/sib/batch", { idsFolha: sibBatch }, "sib-batch");
    if (result) setSibResult(result.items);
  }
  async function resolveSib() {
    const result = await post("/api/integrations/sib/resolve", resolve, "sib-resolve");
    if (result) setSibResult(result.item);
  }
  async function queryAbsences() {
    const result = await post("/api/integrations/sib/absences", { idFolha: sibId }, "sib-absence");
    if (result) setSibResult(result.items);
  }
  async function testSelecty() {
    const result = await post("/api/integrations/selecty/test", {}, "selecty-test");
    if (result) { setSelectyResult(result); await load(); }
  }
  async function querySelecty(event, requestedPage) {
    event?.preventDefault();
    const query = { ...selectyQuery, ...(requestedPage ? { page: requestedPage } : {}) };
    if (requestedPage) setSelectyQuery(current => ({ ...current, page: requestedPage }));
    const result = await post("/api/integrations/selecty/payroll", query, "selecty-list");
    if (result) { setSelectyResult(result); setSelectyDetail(null); }
  }
  async function querySelectyDetail(workId) {
    const result = await post("/api/integrations/selecty/detail", { workId }, `selecty-detail-${workId}`);
    if (result) setSelectyDetail(result);
  }

  const canSib = Boolean(data?.sib?.active && data?.sib?.apiKeyConfigured);
  const canSelecty = Boolean(data?.selecty?.active && data?.selecty?.apiIdConfigured && data?.selecty?.secretConfigured);
  const latestEvents = data?.events || [];
  const eventItems = historyEvents ?? latestEvents;
  const newEvents = historyEvents ? latestEvents.filter(item => item.id > (historyEvents[0]?.id || 0)).length : 0;
  const eventPageSize = 5;
  const eventPageCount = Math.max(1, Math.ceil(eventItems.length / eventPageSize));
  const visibleEvents = eventItems.slice((eventPage - 1) * eventPageSize, eventPage * eventPageSize);
  useEffect(() => { setEventPage(page => Math.min(page, eventPageCount)); }, [eventPageCount]);
  const sibImport = data?.sibImport;
  const sibScan = data?.sibScan;
  const scanActive = Boolean(sibScan?.running || sibScan?.inFlight || sibScan?.autoResume);
  const sibRefresh = data?.sibRefresh;
  const refreshActive = Boolean(sibRefresh?.running || sibRefresh?.inFlight);
  const sibDiscovery = data?.sibDiscovery;
  const issueJob = data?.sibIssueJob;
  const issueJobActive = Boolean(issueJob?.running || issueJob?.inFlight);
  const discoveryActive = Boolean(sibDiscovery?.running || sibDiscovery?.inFlight);
  const autoRefreshActive = Boolean(data?.sibAutoRefresh?.running);
  const sibOperationsBlocked = scanActive || issueJobActive || refreshActive || discoveryActive || autoRefreshActive || Boolean(data?.sibAbsences?.running);
  const scanTotal = sibScan?.started ? Number(sibScan.endId) - Number(sibScan.startId) + 1 : 0;
  const scanPercent = scanTotal > 0 ? Math.min(100, Math.max(0, Number(sibScan.checked || 0) / scanTotal * 100)) : 0;
  const scanStatus = sibScan?.retrying ? "Reconectando automaticamente" : sibScan?.autoResume && !sibScan?.running && !sibScan?.inFlight ? "Retomando do ponto salvo" : sibScan?.running ? "Consulta em andamento" : sibScan?.inFlight ? "Finalizando consulta ativa" : sibScan?.autoStopped ? "Encerrada · limite de IDs vazios" : sibScan?.done ? "Faixa consultada" : sibScan?.error ? "Consulta interrompida" : sibScan?.started ? "Pausada · progresso salvo" : "Pronto para iniciar";
  const discoveryStatus = !sibDiscovery?.ready ? "Após a busca geral" : !sibDiscovery.enabled && sibDiscovery.inFlight ? "Finalizando consulta" : sibDiscovery.running ? "Consultando SIB" : sibDiscovery.enabled && sibDiscovery.waitingForOther ? "Aguardando Bridge" : sibDiscovery.enabled ? "Monitor ativo" : "Pausado";
  const discoveryQueueKind = sibDiscovery?.queueKind;
  const recovery = sibDiscovery?.recovery;
  const internRecoveryPriority = recovery?.priorityLane === "interns";
  const recoveryRanges = Array.isArray(recovery?.ranges) ? recovery.ranges : [];
  const recoveryPasses = Number(recovery?.passes || 3);
  const discoveryWorkLabel = internRecoveryPriority ? "Recuperação de estagiários" : discoveryQueueKind === "fresh" ? "Novas matrículas" : discoveryQueueKind === "recovery" ? "Recuperação decrescente" : discoveryQueueKind === "presence" ? "Inventário de identificadores" : "Catálogo SIB";
  const discoveryActivity = sibDiscovery?.activity === "identity_inventory" ? "Conferindo identificadores e ocorrências no SIB"
    : sibDiscovery?.activity === "catalog_employments" ? "Lendo o catálogo completo de vínculos no SIB"
    : sibDiscovery?.activity === "catalog_companies" ? "Lendo o catálogo de empresas no SIB"
      : sibDiscovery?.activity === "confirm_identity" ? `${discoveryWorkLabel} · confirmando filial e vínculo`
        : sibDiscovery?.activity === "scan_batch" ? discoveryQueueKind === "recovery" ? "Recuperação · consultando do maior número para o menor" : "Novas matrículas · consultando os próximos números"
          : sibDiscovery?.running ? `${discoveryWorkLabel} · processando consultas`
            : sibDiscovery?.waitingForOther ? "Aguardando outra rotina liberar a Bridge"
              : sibDiscovery?.enabled ? "Monitor ativo; aguardando o próximo turno"
                : "Monitoramento pausado";
  const discoveryWindowTotal = Number(sibDiscovery?.windowTotal || 0);
  const discoveryWindowChecked = Number(sibDiscovery?.windowChecked || 0);
  const discoveryCurrentIds = Array.isArray(sibDiscovery?.currentIds) ? sibDiscovery.currentIds : [];

  if (!data) return <section className="container-fluid page-content integrations-page"><div className="card integrations-card integrations-loading" role="status"><strong>{loading ? "Carregando integrações…" : "Não foi possível carregar as integrações"}</strong>{error && <p>{error}</p>}{!loading && <button type="button" className="btn btn-primary" onClick={() => load()}>Tentar novamente</button>}</div></section>;


  const recoveryPercent = recovery?.total ? Math.min(100, Number(recovery.checked || 0) / Number(recovery.total) * 100) : 0;
  const recoveryPercentLabel = `${recoveryPercent.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
  const fichas = data.sibAutoRefresh;
  const discoveryProblem = Boolean(sibDiscovery?.error || (!recovery?.closed && (sibDiscovery?.catalogError || sibDiscovery?.catalogSupported === false)));
  const fichaStatus = scanActive || (internRecoveryPriority && fichas?.enabled) ? "Na fila" : fichas?.stopping ? "Finalizando" : fichas?.running ? "Atualizando" : fichas?.enabled === false ? "Pausada" : fichas?.error ? "Reconectando" : "Ativa";
  const scanPanel = <SyncFold id="sib-scan" icon="archive" title={sibScan?.done ? "Busca inicial concluída" : "Busca inicial por matrículas"} description={sibScan?.started ? `Faixa ${Number(sibScan.startId).toLocaleString("pt-BR")}–${Number(sibScan.endId).toLocaleString("pt-BR")} · progresso salvo` : "Importar uma faixa de matrículas do SIB"} badge={sibScan?.done ? "Concluída" : scanStatus} tone={sibScan?.error && !sibScan.retrying ? "danger" : scanActive ? "active" : "neutral"} initiallyOpen={scanActive || !sibScan?.started} forceOpen={scanActive || editingScanRange}>
          <p className="integrations-copy">Consulta uma faixa de matrículas e salva o avanço. A busca para após {Number(sibScan?.emptyStopThreshold || 100).toLocaleString("pt-BR")} números seguidos sem cadastro.</p>
      {(!sibScan?.started || editingScanRange) && <div className="integrations-fields integrations-scan-range">
        <label>ID inicial<input type="number" min="0" max="999999" step="1" inputMode="numeric" value={scanRange.startId} disabled={scanActive || Boolean(busy)} onChange={event => setScanRange(current => ({ ...current, startId: event.target.value }))} /></label>
        <label>ID final<input type="number" min="0" max="999999" step="1" inputMode="numeric" value={scanRange.endId} disabled={scanActive || Boolean(busy)} onChange={event => setScanRange(current => ({ ...current, endId: event.target.value }))} /></label>
      </div>}
      {sibScan?.started && !editingScanRange && <div className="integrations-scan-range-summary"><span>Faixa atual</span><strong>{Number(sibScan.startId).toLocaleString("pt-BR")}–{Number(sibScan.endId).toLocaleString("pt-BR")}</strong>{!sibScan.done && <small>Próximo lote a partir de {Number(sibScan.nextId || 0).toLocaleString("pt-BR")}</small>}</div>}
      {editingScanRange ? <div className="integrations-inline-actions">
        <button className="btn btn-primary" type="button" onClick={() => scanSib("reset")} disabled={!canSib || busy || saving || scanActive || sibOperationsBlocked}>{busy === "sib-scan-reset" ? "Preparando faixa…" : "Aplicar nova faixa"}</button>
        <button className="btn btn-outline-primary" type="button" disabled={Boolean(busy)} onClick={() => { setEditingScanRange(false); setScanRange({ startId: String(sibScan.startId), endId: String(sibScan.endId) }); }}>Cancelar</button>
        <small>Os IDs já cobertos pela busca são pulados. O início sugerido é o próximo ID ainda não percorrido.</small>
      </div> : <div className="integrations-inline-actions">
        {!scanActive && !sibScan?.done && <button className="btn btn-primary" type="button" onClick={() => scanSib(sibScan?.started ? "resume" : "start")} disabled={!canSib || busy || saving || sibOperationsBlocked || sibScan?.done || sibScan?.inFlight}>{busy === "sib-scan-start" || busy === "sib-scan-resume" ? "Iniciando…" : sibScan?.started ? "Continuar automaticamente" : "Iniciar contínuo"}</button>}
        {!scanActive && !sibScan?.done && <button className="btn btn-outline-primary" type="button" onClick={() => scanSib("next")} disabled={!canSib || busy || saving || sibOperationsBlocked}>{busy === "sib-scan-next" ? "Consultando lote…" : "Consultar somente 1 lote"}</button>}
        {scanActive && <button className="btn btn-outline-primary" type="button" onClick={() => scanSib("pause")} disabled={Boolean(busy) || (!sibScan.running && !sibScan.autoResume)}>{busy === "sib-scan-pause" ? "Parando…" : !sibScan.running && !sibScan.autoResume ? "Finalizando consulta…" : "Parar após consulta ativa"}</button>}
        {sibScan?.started && !scanActive && <button className="btn btn-outline-primary" type="button" onClick={() => { setScanRange({ startId: String(sibScan.nextId), endId: String(Math.max(Number(sibScan.endId), Number(sibScan.nextId))) }); setEditingScanRange(true); }} disabled={Boolean(busy) || saving || issueJobActive || refreshActive || discoveryActive}>{sibScan.done ? "Iniciar nova faixa" : "Alterar faixa"}</button>}
      </div>}
      {sibScan?.error && <div className={`integrations-alert integrations-scan-error ${sibScan.retrying ? "" : "is-error"}`} role={sibScan.retrying ? "status" : "alert"}>
        <strong>{sibScan.retrying ? "Aguardando reconexão:" : "A consulta parou:"}</strong> {sibScan.error}
        <p>{sibScan.retrying ? `Nova tentativa automática a partir de ${new Date(sibScan.retryAt * 1000).toLocaleTimeString("pt-BR")}. O ponto salvo e as fichas pendentes serão retomados; você pode parar a consulta durante a espera.` : "O progresso e as fichas pendentes foram preservados. Corrija a causa indicada e use Continuar automaticamente para retomar."}</p>
      </div>}
      {sibScan?.started && <>
        <div className="integrations-scan-progress"><div className="integrations-progress-label"><span>{Number(sibScan.checked || 0).toLocaleString("pt-BR")} de {scanTotal.toLocaleString("pt-BR")} IDs percorridos</span><strong>{scanPercent.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%</strong></div><progress value={scanPercent} max="100" aria-label="Progresso da faixa de IDs" /></div>
        {sibScan.pending > 0 && <p className="integrations-scan-note">{Number(sibScan.pending).toLocaleString("pt-BR")} fichas aguardando confirmação nesta faixa.</p>}
        {sibScan.emptyStreak > 0 && !sibScan.done && <small className="integrations-scan-note">{Number(sibScan.emptyStreak).toLocaleString("pt-BR")} de {Number(sibScan.emptyStopThreshold || 100).toLocaleString("pt-BR")} IDs vazios seguidos.</small>}
        {sibScan.inFlight && !sibScan.running && <p className="integrations-copy" role="status">A consulta ativa será salva antes de parar. As fichas restantes ficam na fila para continuar depois.</p>}
      </>}
      <div className="integrations-scan-footer"><small>{sibScan?.updatedAt ? `Progresso salvo em ${formatDateTime(sibScan.updatedAt)}` : "O progresso será salvo automaticamente."}</small><a href="/cadastros/conflitos#sib-issues">Conferir pendências e conflitos <span aria-hidden="true">→</span></a></div>


  </SyncFold>;

  return <section className="container-fluid page-content integrations-page integrations-refresh">
    <header className="sync-intro"><div><span className="sync-eyebrow">SIB / BITTI</span><h2>Sincronização dos cadastros</h2><p>O que está rodando e o que precisa da sua atenção.</p></div><span className={`sync-connection ${canSib ? "is-enabled" : ""}`}><span aria-hidden="true"/>{canSib ? "SIB habilitado" : "SIB não habilitado"}</span></header>
    {error && <div className="integrations-alert is-error" role="alert">{error}</div>}
    {notice && <div className="integrations-alert is-success" role="status">{notice}</div>}
    {pollError && <div className="integrations-alert is-warning" role="status">{pollError}</div>}

    <div className="sync-overview" aria-label="Resumo dos cadastros sincronizados">
      <div><span className="sync-stat-icon"><SyncIcon name="person"/></span><dl><dt>Cadastros na V3</dt><dd>{Number(sibScan?.records || 0).toLocaleString("pt-BR")}</dd></dl></div>
      <div><span className="sync-stat-icon"><SyncIcon name="company"/></span><dl><dt>Empresas</dt><dd>{Number(sibScan?.companies || 0).toLocaleString("pt-BR")}</dd></dl></div>
      <div className="sync-overview-time"><span className="sync-stat-icon"><SyncIcon name="clock"/></span><dl><dt>Última busca de novas matrículas</dt><dd>{sibDiscovery?.freshLastCheckedAt ? formatDateTime(sibDiscovery.freshLastCheckedAt) : "Aguardando"}</dd></dl></div>
    </div>

    {(scanActive || !sibScan?.started) && scanPanel}

    {sibDiscovery && <SyncFold id="sib-monitor" className="sync-primary" icon="sync" title="Busca de cadastros" description={internRecoveryPriority ? "Prioridade temporária para recuperar estagiários" : sibDiscovery.presence?.ready ? "Novas matrículas e acompanhamento de ocorrências" : "Novas matrículas com prioridade sobre a recuperação"} badge={discoveryProblem ? "Atenção" : discoveryStatus} tone={discoveryProblem ? "danger" : !sibDiscovery.enabled ? "paused" : "active"} initiallyOpen={Boolean(sibDiscovery.ready)} forceOpen={discoveryProblem}>
      {sibDiscovery.ready ? <>
        <div className="sync-priority-line"><span><i aria-hidden="true"/>{internRecoveryPriority ? "Prioridade temporária · estagiários" : "Novas matrículas"}</span><small>{internRecoveryPriority ? "Demais consultas retomam automaticamente ao concluir" : "Conferência prioritária · cerca de 1 minuto"}</small></div>
        <dl className="sync-frontiers"><div><dt>Próximo número · funcionários</dt><dd>{Number(sibDiscovery.cursor || 0).toLocaleString("pt-BR")}</dd></div><div><dt>Próximo número · estágios</dt><dd>{Number(sibDiscovery.internCursor || 0).toLocaleString("pt-BR")}</dd></div></dl>

        <section className={`sync-live ${sibDiscovery.running ? "is-running" : ""}`} aria-label="Atividade atual da consulta SIB">
          <div className="sync-live-heading"><span className="sync-live-dot" aria-hidden="true"/><div><small>{sibDiscovery.running ? "EM ANDAMENTO" : "ACOMPANHAMENTO"}</small><strong>{discoveryActivity}</strong></div>{sibDiscovery.running && sibDiscovery.currentRegistration && <span className="sync-current-id">Matrícula <b>{sibDiscovery.currentRegistration}</b></span>}</div>
          {discoveryWindowTotal > 0 && ["fresh", "recovery"].includes(discoveryQueueKind) && <div className="sync-live-progress"><progress value={discoveryWindowChecked} max={discoveryWindowTotal} aria-label="Progresso do trecho em consulta"/><span>{discoveryWindowChecked} de {discoveryWindowTotal} números</span></div>}
          {sibDiscovery.activity?.startsWith("catalog_") && <div className="sync-live-progress"><progress value={Number(sibDiscovery.catalogChecked || 0)} max={Math.max(1, Number(sibDiscovery.catalogTotal || 0))} aria-label="Progresso do catálogo inicial"/><span>{Number(sibDiscovery.catalogChecked || 0).toLocaleString("pt-BR")} de {sibDiscovery.catalogTotal == null ? "—" : Number(sibDiscovery.catalogTotal).toLocaleString("pt-BR")}</span></div>}
          {discoveryCurrentIds.length > 0 && <details className="sync-current-numbers"><summary>Ver números deste trecho</summary><div>{discoveryCurrentIds.map((id, index) => <code key={`${id}-${index}`}>{Number(id).toLocaleString("pt-BR")}</code>)}</div></details>}
        </section>

        <SyncFold className="sync-nested" title="Recuperação inicial" icon="records" description={recovery?.closed ? "Encerrada por solicitação após conferência da base" : internRecoveryPriority ? "Consultando somente estagiários · vínculo 15" : recovery?.complete ? sibDiscovery.presence?.ready ? "Passagens e catálogo concluídos" : "Passagens concluídas · catálogo em conferência" : `${recoveryPasses} passagens para conferir a numeração anterior`} badge={recovery?.closed ? "Encerrada" : internRecoveryPriority ? "Estagiários priorizados" : recovery?.complete ? "Passagens concluídas" : recoveryPercentLabel} forceOpen={internRecoveryPriority}>
          <p className="sync-help">{recovery?.closed ? `Encerrada em ${formatDateTime(recovery.closedAt)}. A varredura histórica e o catálogo inicial não serão retomados automaticamente. A busca de novas matrículas e o acompanhamento dos cadastros continuam.` : internRecoveryPriority ? "As consultas estão dedicadas aos estagiários até concluir suas três passagens. Funcionários, novas matrículas, catálogo e atualização de fichas retomam automaticamente do ponto salvo." : recovery?.complete ? "A varredura histórica terminou. O inventário começa após concluir também o catálogo inicial." : "Percorre as faixas do maior número até o 1. As novas matrículas têm prioridade e o avanço fica salvo."}</p>
          {recovery?.closed ? <p className="sync-catalog-note">Histórico preservado: {Number(recovery.checked || 0).toLocaleString("pt-BR")} consultas e {Number(recovery.imported || 0).toLocaleString("pt-BR")} cadastros recuperados.</p> : <div className="sync-recovery-ranges">{recoveryRanges.map(range => <div key={range.key}><div><strong>{range.label}</strong><span>{range.complete ? "Concluída" : internRecoveryPriority && range.key !== "interns" ? "Aguardando estagiários" : `Passagem ${Math.min(recoveryPasses, Number(range.pass || 1))} de ${recoveryPasses}`}</span></div><progress value={Number(range.checked || 0)} max={Math.max(1, Number(range.total || 0))} aria-label={`Progresso da recuperação de ${range.label.toLowerCase()}`}/><small>{range.complete ? "Faixa conferida" : `Próximo: ${Number(range.next || 0).toLocaleString("pt-BR")} ↓ 1`} · {Number(range.checked || 0).toLocaleString("pt-BR")} de {Number(range.total || 0).toLocaleString("pt-BR")} consultas</small></div>)}</div>}
          {!recovery?.closed && sibDiscovery.catalogSupported === true && <p className="sync-catalog-note">{sibDiscovery.catalogLastCompletedAt > 0 ? `Catálogo inicial concluído em ${formatDateTime(sibDiscovery.catalogLastCompletedAt)}.` : `Catálogo inicial: ${Number(sibDiscovery.catalogChecked || 0).toLocaleString("pt-BR")} de ${sibDiscovery.catalogTotal == null ? "—" : Number(sibDiscovery.catalogTotal).toLocaleString("pt-BR")} registros na etapa atual.`}</p>}
          <details className="sync-explanation"><summary>Como os vínculos são conferidos</summary><p>Empresa, filial, matrícula e modalidade são conferidas para separar pessoas com o mesmo número. {recovery?.closed ? "O encerramento foi solicitado pelo responsável; casos inconclusivos continuam disponíveis em Conflitos." : "As três passagens encerram a recuperação histórica; casos inconclusivos ficam disponíveis em Conflitos."}</p><a href="/cadastros/conflitos#sib-issues">Conferir identidades pendentes →</a></details>
        </SyncFold>

        <div className="sync-primary-footer"><small>{sibDiscovery.enabled ? "Monitoramento contínuo · avanço salvo automaticamente" : "Progresso salvo para retomar"}</small><div className="integrations-inline-actions">{!sibDiscovery.enabled ? <button className="btn btn-primary" type="button" onClick={() => controlDiscovery("resume")} disabled={!canSib || busy || saving || sibDiscovery.inFlight || scanActive || refreshActive || issueJobActive}>{busy === "sib-discovery-resume" ? "Retomando…" : "Retomar monitoramento"}</button> : <button className="btn btn-outline-primary" type="button" onClick={() => controlDiscovery("pause")} disabled={Boolean(busy)}>{busy === "sib-discovery-pause" ? "Pausando…" : "Pausar monitoramento"}</button>}</div></div>
      </> : <p className="sync-help">O monitoramento começa quando a busca inicial terminar. O progresso será retomado automaticamente.</p>}
      {!recovery?.closed && sibDiscovery.catalogSupported === false && <div className="integrations-alert is-warning" role="status">A Bridge ainda precisa da extensão de catálogo para conferir todos os vínculos.</div>}
      {!recovery?.closed && sibDiscovery.catalogError && <div className="integrations-alert is-warning" role="status">Catálogo aguardando nova tentativa: {sibDiscovery.catalogError}</div>}
      {sibDiscovery.error && <div className="integrations-alert is-error" role="alert">{sibDiscovery.error}</div>}
    </SyncFold>}

    {fichas && <SyncFold title="Atualização de fichas" icon="records" description={`${Number(fichas.checked || 0).toLocaleString("pt-BR")} de ${Number(fichas.total || 0).toLocaleString("pt-BR")} conferidas · ${Number(fichas.changed || 0).toLocaleString("pt-BR")} alteradas`} badge={fichaStatus} tone={fichas.enabled === false ? "paused" : fichas.error ? "danger" : "active"} initiallyOpen={Boolean(fichas.running)} forceOpen={Boolean(fichas.error)} onOpenChange={open => { if (!open) setChangeHistoryOpen(false); }}>
      <div className="sync-ficha-progress"><progress value={Number(fichas.checked || 0)} max={Math.max(1, Number(fichas.total || 0))} aria-label="Progresso da atualização de fichas"/><span>{Number(fichas.checked || 0).toLocaleString("pt-BR")} de {Number(fichas.total || 0).toLocaleString("pt-BR")} fichas</span></div>
      <div className="integrations-monitor-metrics"><span><b>{Number(fichas.changed || 0).toLocaleString("pt-BR")}</b> alteradas</span><span><b>{Number(fichas.unresolved || 0).toLocaleString("pt-BR")}</b> sem confirmação</span></div>
      <p className="sync-help">Esta rodada final percorre todos os cadastros para completar os cargos. Nas próximas atualizações, serão consultados somente vínculos ativos; vínculos com desligamento efetivado saem da fila. Ao pausar, a consulta ativa termina e o ponto fica salvo.</p>
      {fichas.priorityMode && <p className="integrations-scan-note" role="status">Prioridade ativa: novas matrículas, recuperação e afastamentos estão pausados. Serão reativados ao fim desta rodada.</p>}
      <div className="integrations-inline-actions">
            {!fichas.priorityMode && <button className="btn btn-outline-primary btn-sm" type="button" onClick={() => controlAutoRefresh("prioritize")} disabled={!canSib || Boolean(busy) || saving}>{busy === "sib-auto-refresh-prioritize" ? "Prioritizando…" : "Priorizar até concluir"}</button>}
            {data.sibAutoRefresh.enabled === false
              ? <button className="btn btn-primary btn-sm" type="button" onClick={() => controlAutoRefresh("resume")} disabled={!canSib || Boolean(busy) || saving || scanActive || issueJobActive || refreshActive || discoveryActive || data.sibAutoRefresh.stopping}>{busy === "sib-auto-refresh-resume" ? "Retomando…" : "Retomar atualização"}</button>
              : <button className="btn btn-outline-primary btn-sm" type="button" onClick={() => controlAutoRefresh("pause")} disabled={Boolean(busy) || saving || data.sibAutoRefresh.stopping}>{data.sibAutoRefresh.stopping ? "Parando após lote…" : "Parar atualização"}</button>}
            <button className="btn btn-outline-primary btn-sm" type="button" aria-expanded={changeHistoryOpen} onClick={() => setChangeHistoryOpen(value => !value)}>{changeHistoryOpen ? "Ocultar alterações" : `Ver alterações detalhadas${changeHistory?.total ? ` (${Number(changeHistory.total).toLocaleString("pt-BR")})` : ""}`}</button>
          </div>
      {changeHistoryOpen && <section className="integrations-change-history" aria-label="Histórico detalhado de alterações SIB">
            <div className="integrations-change-heading"><div><strong>O que mudou nas fichas</strong><small>Valores recebidos do SIB comparados com o cadastro anterior.</small></div>{changeHistory && <span>{Number(changeHistory.total).toLocaleString("pt-BR")} alteração(ões)</span>}</div>
            <form className="integrations-change-search" onSubmit={event => { event.preventDefault(); setChangePage(1); setChangeSearch(changeSearchInput.trim()); }}>
              <input aria-label="Buscar alterações por nome, matrícula ou empresa" value={changeSearchInput} onChange={event => setChangeSearchInput(event.target.value)} placeholder="Buscar nome, matrícula ou empresa" maxLength={120}/>
              <button className="btn btn-primary btn-sm" type="submit">Buscar</button>
            </form>
            {changeHistoryError && <div className="integrations-alert is-warning" role="status">{changeHistoryError}</div>}
            {!changeHistory ? <p className="integrations-empty">Carregando histórico…</p> : changeHistory.items.length === 0 ? <p className="integrations-empty">Nenhuma diferença detalhada registrada ainda. As alterações contabilizadas antes da ativação deste histórico não têm os valores anteriores guardados.</p> : <>
              <div className="integrations-change-list">{changeHistory.items.map(item => <article className="integrations-change-item" key={item.id}>
                <header><div><strong>{item.dataset === "company" ? `Empresa · ${item.record_name || item.company_name || item.source_id}` : `${item.record_name || "Ficha SIB"} · matrícula ${item.registration}`}</strong><small>{item.dataset === "company" ? `Código ${item.source_id}` : `${item.company_name || "Empresa não informada"} · filial ${item.branch_code || "—"}`}</small></div><time>{formatChangeDateTime(item.changed_at)}</time></header>
                <div className="integrations-change-fields">{(item.changes || []).map((change, index) => <div className="integrations-change-field" key={`${change.field}-${index}`}><strong>{changeFieldLabel(change.field)}</strong><span className="integrations-change-before"><small>Antes</small>{changeValue(change.before)}</span><b aria-hidden="true">→</b><span className="integrations-change-after"><small>Depois</small>{changeValue(change.after)}</span></div>)}</div>
              </article>)}</div>
              {changeHistory.pageCount > 1 && <div className="integrations-history-pagination"><span>Página {changeHistory.page} de {changeHistory.pageCount}</span><div><button className="btn btn-outline-primary btn-sm" type="button" disabled={changePage <= 1} onClick={() => setChangePage(page => page - 1)}>Anterior</button><button className="btn btn-outline-primary btn-sm" type="button" disabled={changePage >= changeHistory.pageCount} onClick={() => setChangePage(page => page + 1)}>Próxima</button></div></div>}
            </>}
            <small className="integrations-history-privacy">O histórico começa com as próximas alterações; mudanças anteriores não podem ser reconstruídas. CPF, contatos e dados bancários são excluídos por privacidade.</small>
          </section>}
      {fichas.lastCompletedAt > 0 && <small className="sync-help">Última rodada concluída em {formatDateTime(fichas.lastCompletedAt)}.</small>}
      {fichas.error && <div className="integrations-alert is-warning" role="status">{fichas.error}</div>}
    </SyncFold>}

    <div className="sync-occurrences-card"><SibOccurrences compact summary={sibDiscovery?.presence} csrf={data.csrf}/></div>

    <div className="sync-section-divider"><span>Outras rotinas e ferramentas</span><small>Abra somente o que precisar consultar</small></div>
    {data.sibAbsences && <SyncFold title="Afastamentos" icon="health" description={data.sibAbsences.confirmed > 0 ? `${Number(data.sibAbsences.confirmed).toLocaleString("pt-BR")} de ${Number(data.sibAbsences.total).toLocaleString("pt-BR")} matrículas conferidas` : "Consulta de períodos de afastamento no SIB"} badge={!data.sibAbsences.enabled ? "Pausado" : data.sibAbsences.running ? "Consultando" : data.sibAbsences.waiting ? "Na fila" : "Ativo"} tone={data.sibAbsences.enabled ? "active" : "neutral"}>
      <p className="sync-help">{data.sibAbsences.confirmed > 0 ? "Acompanhe os períodos e as ocorrências de afastamento na central." : "A primeira conferência aguarda a disponibilidade das consultas de cadastros."}</p><div className="integrations-inline-actions"><a className="btn btn-outline-primary" href="/central-dp/afastamentos">Abrir central de afastamentos →</a></div>
    </SyncFold>}
    {sibScan?.started && !scanActive && scanPanel}
    <details className="card integrations-card integrations-history" onToggle={event => { setHistoryEvents(event.currentTarget.open ? latestEvents : null); setEventPage(1); }}>
      <summary className="integrations-card-heading integrations-history-summary">
        <span className="sync-panel-icon"><SyncIcon name="clock"/></span><div><p>AUDITORIA DE CONSULTAS</p><h3>Atividade recente</h3></div>
        <span>{eventItems.length ? `${eventItems.length} registros recentes · ${eventPageSize} por página` : "Sem atividade registrada"}</span>
        <i className="integrations-history-toggle" aria-hidden="true" />
      </summary>
      {eventItems.length ? <>
        {newEvents > 0 && <div className="integrations-history-new" role="status"><span>{newEvents} nova(s) atividade(s)</span><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => { setHistoryEvents(latestEvents); setEventPage(1); }}>Atualizar lista</button></div>}
        <div className="integrations-history-list">{visibleEvents.map(item => <div key={item.id}><strong>{item.integration} · {EVENT_LABELS[item.action] || item.action}</strong><span className={item.outcome === "OK" ? "is-ok" : "is-error"}>{item.outcome === "OK" ? "Concluída" : "Falhou"}</span><small>{formatDateTime(item.created_at)} · {item.actor}{item.result_count > 0 ? ` · ${Number(item.result_count).toLocaleString("pt-BR")} registro(s)` : ""}</small><p>{item.summary}</p></div>)}</div>
        <p className="integrations-history-privacy">Não guarda CPF, conta bancária nem payload bruto.</p>
        {eventPageCount > 1 && <nav className="integrations-pagination integrations-history-pagination" aria-label="Paginação da atividade de consultas">
          <span>Página {eventPage} de {eventPageCount} · exibindo {Math.min((eventPage - 1) * eventPageSize + 1, eventItems.length)}–{Math.min(eventPage * eventPageSize, eventItems.length)} de {eventItems.length}</span>
          <div><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setEventPage(page => Math.max(1, page - 1))} disabled={eventPage <= 1}>Anterior</button><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setEventPage(page => Math.min(eventPageCount, page + 1))} disabled={eventPage >= eventPageCount}>Próxima</button></div>
        </nav>}
      </> : <p className="integrations-empty">Ainda não há consultas registradas.</p>}
    </details>
    <SyncFold title="Consultas e manutenção" icon="settings" description="Reconsulta manual, busca individual e catálogo completo" forceOpen={Boolean(refreshActive || sibRefresh?.error)}>
      {sibRefresh && <details className="card integrations-card integrations-fold" open={refreshActive || Boolean(sibRefresh.error)}><summary><span><b>Atualização completa sob demanda</b><small>Reconsultar todos os cadastros já importados</small></span><i aria-hidden="true" /></summary><div className="integrations-fold-body">
      <div className="integrations-card-heading"><div><p>CADASTROS SIB · ATUALIZAÇÃO</p><h3 id="sib-refresh-heading">Atualizar cadastros já trazidos</h3></div><span role="status">{refreshActive ? "Atualizando" : sibRefresh.done ? "Concluída" : sibRefresh.started ? "Pausada" : "Pronta"}</span></div>
      <p className="integrations-copy">Reconsulta somente vínculos ativos. Vínculos com desligamento efetivado ficam fora das atualizações recorrentes. O progresso fica salvo.</p>
      {sibRefresh.started && <>
        <div className="integrations-scan-progress"><progress value={Number(sibRefresh.checked || 0)} max={Math.max(1, Number(sibRefresh.total || 0))} aria-label="Progresso da atualização dos cadastros" /><span>{Number(sibRefresh.checked || 0).toLocaleString("pt-BR")} de {Number(sibRefresh.total || 0).toLocaleString("pt-BR")} cadastro(s) reconsultados</span></div>
        <dl className="integrations-scan-stats"><div><dt>Alterados</dt><dd>{Number(sibRefresh.changed || 0).toLocaleString("pt-BR")}</dd></div><div><dt>Sem confirmação</dt><dd>{Number(sibRefresh.unresolved || 0).toLocaleString("pt-BR")}</dd></div></dl>
      </>}
      <div className="integrations-inline-actions">
        {!sibRefresh.running && <button className="btn btn-primary" type="button" onClick={() => refreshSib(sibRefresh.started && !sibRefresh.done ? "resume" : "start")} disabled={!canSib || busy || saving || scanActive || sibOperationsBlocked || sibRefresh.inFlight}>{busy === "sib-refresh-start" || busy === "sib-refresh-resume" ? "Iniciando…" : sibRefresh.started && !sibRefresh.done ? "Continuar atualização" : sibRefresh.done ? "Atualizar novamente" : "Atualizar agora"}</button>}
        {sibRefresh.running && <button className="btn btn-outline-primary" type="button" onClick={() => refreshSib("pause")} disabled={Boolean(busy)}>Parar após consulta ativa</button>}
      </div>
      {sibRefresh.error && <div className="integrations-alert is-error" role="alert">A atualização parou: {sibRefresh.error}</div>}
      {sibRefresh.unresolved > 0 && <p className="integrations-scan-note">Cadastros sem confirmação permaneceram como estavam; uma matrícula ambígua não substitui outra ficha.</p>}
    </div></details>}
      <div className="integrations-tools">
    <details className="card integrations-card integrations-catalog-details">
      <summary>Importação manual pelo catálogo</summary>
    <section className="card integrations-card integrations-operation-card">
      <div className="integrations-card-heading"><div><p>CADASTROS SIB</p><h3>Trazer empresas e trabalhadores aos poucos</h3></div><span>{sibImport?.done ? "Leitura concluída" : sibImport?.started ? "Progresso salvo" : "Pronto para iniciar"}</span></div>
      <p className="integrations-copy">Requer a extensão de catálogo instalada na Bridge. Cada clique salva até 50 registros na V3: primeiro as empresas e depois os cadastros de trabalhadores, incluindo desligados. Você pode continuar de onde parou.</p>
      <div className="integration-result-fields">
        {[["companies", "Empresas"], ["employments", "Cadastros de trabalhadores"]].map(([key, label]) => {
          const progress = sibImport?.datasets?.[key];
          return <div key={key}><strong>{label}</strong><p>{progress?.count || 0} de {progress?.total ?? "total ainda não consultado"}{progress?.done ? " · concluído" : ""}</p></div>;
        })}
      </div>
      <div className="integrations-test-row">
        <button className="btn btn-primary" type="button" onClick={() => importSib()} disabled={!canSib || busy || saving || scanActive || sibOperationsBlocked || sibImport?.done}>{busy === "sib-import" ? "Consultando e trazendo lote…" : sibImport?.started ? "Consultar e trazer próximo lote" : "Consultar e trazer primeiro lote"}</button>
        {sibImport?.started && <button className="btn btn-outline-primary" type="button" onClick={() => importSib(true)} disabled={!canSib || busy || saving || scanActive || sibOperationsBlocked}>Iniciar nova leitura</button>}
        <small>Os lotes já trazidos ficam nos <a href="/cadastros/empresas">Cadastros de empresas</a>, <a href="/cadastros/pessoas">Pessoas</a> e <a href="/cadastros/vinculos">Vínculos</a>.</small>
      </div>
    </section>
    </details>

    <section className="card integrations-card integrations-operation-card">
      <div className="integrations-card-heading"><div><p>CONSULTAS SIB/BITTI</p><h3>Consultar vínculo sem sincronizar</h3></div><span>CPF e dados bancários serão exibidos somente neste acesso administrativo.</span></div>
      <div className="integrations-query-grid">
        <label>ID Folha<input value={sibId} onChange={event => setSibId(event.target.value)} maxLength={80} placeholder="ID Folha" /></label>
        <button className="btn btn-primary" type="button" onClick={querySib} disabled={!canSib || !sibId.trim() || busy || sibOperationsBlocked}>{busy === "sib-query" ? "Consultando…" : "Consultar vínculo"}</button>
        <button className="btn btn-outline-primary" type="button" onClick={queryAbsences} disabled={!canSib || !sibId.trim() || busy || sibOperationsBlocked}>{busy === "sib-absence" ? "Consultando…" : "Consultar afastamentos"}</button>
        <label className="integrations-batch-input">IDs Folha para lote<textarea rows="2" value={sibBatch} onChange={event => setSibBatch(event.target.value)} placeholder="Um por linha, até 50" /></label>
        <button className="btn btn-outline-primary" type="button" onClick={querySibBatch} disabled={!canSib || !sibBatch.trim() || busy || sibOperationsBlocked}>{busy === "sib-batch" ? "Consultando…" : "Consultar lote"}</button>
      </div>
      <details className="integrations-resolve"><summary>Conciliar por CPF + admissão + CNPJ do tomador</summary><div className="integrations-query-grid">
        <label>CPF<input inputMode="numeric" value={resolve.cpf} onChange={event => setResolve(current => ({ ...current, cpf: event.target.value }))} maxLength={14} /></label>
        <label>Admissão<input type="date" value={resolve.admissionDate} onChange={event => setResolve(current => ({ ...current, admissionDate: event.target.value }))} /></label>
        <label>CNPJ do tomador<input inputMode="numeric" value={resolve.takerCnpj} onChange={event => setResolve(current => ({ ...current, takerCnpj: event.target.value }))} maxLength={18} /></label>
        <button className="btn btn-outline-primary" type="button" onClick={resolveSib} disabled={!canSib || busy || sibOperationsBlocked}>{busy === "sib-resolve" ? "Conciliando…" : "Pesquisar correspondência"}</button>
      </div><small>A consulta envia esses identificadores à Bridge. Uma resposta não atualiza cadastro nem confirma a associação automaticamente.</small></details>
      {sibResult && <div className="integration-result"><ResultView value={sibResult} /></div>}
    </section>

    {selecty.active && <>
    <section className="card integrations-card integrations-operation-card">
      <div className="integrations-card-heading"><div><p>CONSULTAS SELECTY</p><h3>Listar contratações</h3></div><span>work_id técnico não será tratado como ID Folha.</span></div>
      <form className="integrations-selecty-query" onSubmit={querySelecty}>
        <label>Status da API<select value={selectyQuery.status} onChange={event => setSelectyQuery(current => ({ ...current, status: event.target.value, page: 1 }))}>{STATUSES.map(status => <option key={status} value={status}>{status}</option>)}</select></label>
        <label>CPF (opcional)<input inputMode="numeric" value={selectyQuery.cpf} onChange={event => setSelectyQuery(current => ({ ...current, cpf: event.target.value }))} maxLength={14} /></label>
        <label>ID da vaga (opcional)<input value={selectyQuery.vacancyId} onChange={event => setSelectyQuery(current => ({ ...current, vacancyId: event.target.value }))} maxLength={80} /></label>
        <label>Página<input type="number" min="1" max="10000" value={selectyQuery.page} onChange={event => setSelectyQuery(current => ({ ...current, page: Number(event.target.value) }))} /></label>
        <button className="btn btn-primary" type="submit" disabled={!canSelecty || busy}>{busy === "selecty-list" ? "Consultando…" : "Consultar contratações"}</button>
      </form>
      {selectyResult?.items && <div className="integrations-table-wrap"><p>{selectyResult.total ?? selectyResult.items.length} resultado(s) · página {selectyResult.page || 1} de {selectyResult.lastPage || 1}</p><table className="table integrations-table"><thead><tr><th>work_id</th><th>Nome</th><th>CPF</th><th>Status</th><th>Empresa / vaga</th><th>Admissão</th><th></th></tr></thead><tbody>{selectyResult.items.map((item, index) => <tr key={`${item.workId}-${index}`}><td>{item.workId || "—"}</td><td>{item.name || "Sem nome"}</td><td>{item.cpfMasked || "—"}</td><td>{item.status || "—"}</td><td>{[item.company, item.vacancy].filter(Boolean).join(" · ") || "—"}</td><td>{item.admissionDate || "—"}</td><td><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => querySelectyDetail(item.workId)} disabled={!item.workId || busy}>Detalhar</button></td></tr>)}</tbody></table>
        <div className="integrations-pagination"><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => querySelecty(null, Math.max(1, (selectyResult.page || 1) - 1))} disabled={!canSelecty || (selectyResult.page || 1) <= 1 || busy}>Anterior</button><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => querySelecty(null, (selectyResult.page || 1) + 1)} disabled={!canSelecty || (selectyResult.page || 1) >= (selectyResult.lastPage || 1) || busy}>Próxima</button></div>
      </div>}
      {selectyDetail && <div className="integration-result"><strong>Detalhe da contratação</strong><ResultView value={selectyDetail} /></div>}
    </section>
    </>}
</div>
    </SyncFold>
    <SyncFold id="integration-settings" title="Conexões e credenciais" icon="settings" description="Configuração do SIB e conexões opcionais">
      <form className="integrations-config-grid" onSubmit={save}>
      <article className="card integrations-card">
        <div className="integrations-card-heading"><div><p>SIB / BITTI</p><h3>Consulta via Bridge</h3></div><span className={`integrations-status ${canSib ? "is-active" : ""}`}>{canSib ? "Habilitada" : "Desativada"}</span></div>
        <p className="integrations-copy">Configure o endereço e as credenciais de leitura da Bridge.</p>
        <div className="integrations-fields">
          <label className="integrations-span-2">URL base da Bridge<input value={sib.baseUrl || ""} onChange={event => updateSib("baseUrl", event.target.value)} placeholder="https://servidor:porta" autoComplete="url" /></label>
          <label>Timeout (s)<input type="number" min="3" max="60" value={sib.timeoutSeconds || 20} onChange={event => updateSib("timeoutSeconds", Number(event.target.value))} /></label>
          <label className="integrations-span-2">Chave X-API-Key<input type="password" autoComplete="new-password" value={secrets.sibApiKey} onChange={event => updateSecret("sibApiKey", event.target.value)} placeholder={data?.sib?.apiKeyConfigured ? "Configurada; deixe em branco para preservar" : "Cole a chave no backend"} /></label>
          <label>CA PEM (opcional)<input value={sib.caBundlePath || ""} onChange={event => updateSib("caBundlePath", event.target.value)} placeholder="Caminho acessível pelo serviço V3" /></label>
          <label>Cloudflare Client ID<input type="password" autoComplete="new-password" value={secrets.sibCfClientId} onChange={event => updateSecret("sibCfClientId", event.target.value)} placeholder={data?.sib?.cfClientIdConfigured ? "Configurado; em branco preserva" : "Opcional"} /></label>
          <label>Cloudflare Client Secret<input type="password" autoComplete="new-password" value={secrets.sibCfClientSecret} onChange={event => updateSecret("sibCfClientSecret", event.target.value)} placeholder={data?.sib?.cfClientSecretConfigured ? "Configurado; em branco preserva" : "Opcional"} /></label>
        </div>
        <div className="integrations-options"><label><input type="checkbox" checked={Boolean(sib.active)} onChange={event => updateSib("active", event.target.checked)} /> Habilitar consultas manuais</label>
          {data?.sib?.apiKeyConfigured && <label className="is-danger"><input type="checkbox" checked={Boolean(remove.sibApiKey)} onChange={event => setRemove(current => ({ ...current, sibApiKey: event.target.checked }))} /> Remover chave SIB</label>}
        </div>
        <div className="integrations-inline-actions"><button className="btn btn-primary" type="button" onClick={testSib} disabled={!canSib || busy || saving || sibOperationsBlocked}>{busy === "sib-test" ? "Testando…" : "Testar saúde da Bridge"}</button><small>{data?.sib?.lastTestAt ? `Última verificação: ${data.sib.lastTestAt} · ${data.sib.lastTestStatus}` : "Teste explícito; não lê cadastro de pessoas."}</small></div>
      </article>

      <details className="sync-optional-connection"><summary>Selecty · conexão opcional</summary>
      <article className="card integrations-card">
        <div className="integrations-card-heading"><div><p>SELECTY</p><h3>Contratações e detalhes</h3></div><span className={`integrations-status ${selecty.active && data?.selecty?.apiIdConfigured && data?.selecty?.secretConfigured ? "is-active" : ""}`}>{selecty.active ? "Consultas manuais" : "Desativada"}</span></div>
        <p className="integrations-copy">API oficial v2. A tela permite autenticar e consultar; não envia status, admissão, desligamento nem atualização para a Selecty.</p>
        <div className="integrations-fields">
          <label className="integrations-span-2">URL oficial<input value="https://api.selecty.app/v2" readOnly /></label>
          <label>Timeout (s)<input type="number" min="3" max="60" value={selecty.timeoutSeconds || 20} onChange={event => updateSelecty("timeoutSeconds", Number(event.target.value))} /></label>
          <label>API ID<input type="password" autoComplete="new-password" value={secrets.selectyApiId} onChange={event => updateSecret("selectyApiId", event.target.value)} placeholder={data?.selecty?.apiIdConfigured ? "Configurado; em branco preserva" : "API ID da conta"} /></label>
          <label>Secret<input type="password" autoComplete="new-password" value={secrets.selectySecret} onChange={event => updateSecret("selectySecret", event.target.value)} placeholder={data?.selecty?.secretConfigured ? "Configurado; em branco preserva" : "Secret da conta"} /></label>
        </div>
        <div className="integrations-options"><label><input type="checkbox" checked={Boolean(selecty.active)} onChange={event => updateSelecty("active", event.target.checked)} /> Habilitar consultas manuais</label>
          {data?.selecty?.apiIdConfigured && <label className="is-danger"><input type="checkbox" checked={Boolean(remove.selectyApiId)} onChange={event => setRemove(current => ({ ...current, selectyApiId: event.target.checked }))} /> Remover API ID</label>}
          {data?.selecty?.secretConfigured && <label className="is-danger"><input type="checkbox" checked={Boolean(remove.selectySecret)} onChange={event => setRemove(current => ({ ...current, selectySecret: event.target.checked }))} /> Remover Secret</label>}
        </div>
        <div className="integrations-inline-actions"><button className="btn btn-primary" type="button" onClick={testSelecty} disabled={!canSelecty || busy || saving}>{busy === "selecty-test" ? "Testando…" : "Testar autenticação e leitura"}</button><small>{data?.selecty?.lastTestAt ? `Último teste: ${data.selecty.lastTestAt} · ${data.selecty.lastTestStatus}` : "Executa auth e uma consulta GET sem alterar registros."}</small></div>
      </article>
      </details>

       <div className="integrations-save-row"><button className="btn btn-primary" type="submit" disabled={loading || saving || busy || sibOperationsBlocked || autoRefreshActive || !data?.csrf}>{saving ? "Salvando…" : "Salvar configurações"}</button><span>{sibOperationsBlocked || autoRefreshActive ? "Aguarde a consulta ativa terminar antes de salvar as configurações." : "Credenciais protegidas no servidor. Campos em branco preservam os valores salvos."}</span></div>
    </form>
    </SyncFold>
  </section>;
}

function ResultView({ value }) {
  if (!value) return null;
  if (Array.isArray(value)) return <div className="integration-result-list">{value.map((item, index) => <ResultView key={index} value={item} />)}</div>;
  if (typeof value !== "object") return <pre>{String(value)}</pre>;
  const labels = { idFolha: "ID Folha", name: "Nome", cpf: "CPF", company: "Empresa", takerCnpj: "CNPJ tomador", admissionDate: "Admissão", bank: "Banco", bankBranch: "Agência", bankAccount: "Conta", accountDigit: "Dígito", dismissalDate: "Desligamento", dismissalReasonCode: "Código do motivo", dismissalReason: "Motivo", dismissalReasonOrigin: "Origem do motivo", terminationType: "Tipo de término", id: "ID afastamento", startDate: "Início", endDate: "Fim", status: "Situação", reason: "Motivo do afastamento", workId: "work_id", personId: "ID pessoa Selecty", cpfMasked: "CPF", vacancyId: "Vaga", vacancy: "Cargo/vaga", registrationCode: "Código de cadastro Selecty", email: "E-mail", phone: "Telefone", position: "Posição", contractType: "Tipo de contrato", rawDocumentCount: "Documentos disponíveis" };
  const fields = Object.entries(value).filter(([key, item]) => labels[key] && item !== "" && item !== null && item !== undefined);
  return <dl className="integration-result-fields">{fields.length ? fields.map(([key, item]) => <div key={key}><dt>{labels[key]}</dt><dd>{String(item)}</dd></div>) : <p>Nenhum campo compatível foi retornado.</p>}</dl>;
}
