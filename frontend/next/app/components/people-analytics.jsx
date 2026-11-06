"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ReportCatalog, ReportFilters, ReportResult } from "./people-reports";

const metricLabels = { active: "Ativos", admitted: "Admissões", terminated: "Desligamentos", balance: "Saldo", turnover: "Rotatividade", people: "Pessoas identificadas", links: "Vínculos no período" };
const colors = { active: "#2563eb", admitted: "#0d947d", terminated: "#db5967", balance: "#8462c6" };
const palette = ["#2563eb", "#0d947d", "#b568cb", "#d88a1b", "#db5967", "#4f9fad"];
const dimensions = { contracts: "Tipo de contrato", departments: "Departamento / unidade", jobs: "Cargo / função", states: "UF da empresa", branches: "Empresa e filial da folha", reasons: "Motivo / código de saída" };
const arrays = ["companies", "excludeCompanies", "compareCompanies", "sources", "reportColumns", ...Object.keys(dimensions)];
const focusLabels = { all: "Vínculos no período", active: "Ativos na data de corte", admitted: "Admitidos no período", terminated: "Desligados no período", unknown: "Situação a conferir" };
const statuses = { active: "Ativo", terminated: "Desligado", future: "Admissão futura", unknown: "A conferir" };
const number = value => value == null ? "—" : Number(value).toLocaleString("pt-BR", { maximumFractionDigits: 1 });
const day = value => value ? value.split("-").reverse().join("/") : "—";
const norm = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function initialFilters(query, section) {
  const filters = { period: "all", status: "all", compare: "none", granularity: "auto", rankBy: "active", groupBy: "contracts", focus: "all", page: 1, q: "", start: "", end: "", includeTests: false };
  if (["admissoes", "desligamentos", "perfil-ativos", "contratos", "qualidade"].includes(section)) filters.focus = ({ admissoes: "admitted", desligamentos: "terminated", qualidade: "all" })[section] || "active";
  if (section === "admitidos-demitidos") Object.assign(filters, { period: "12months", granularity: "month", rankBy: "admitted", focus: "admitted" });
  if (section === "retencao") Object.assign(filters, { rankBy: "terminated", focus: "terminated" });
  if (section === "qualidade") filters.qualityIssue = "all";
  if (section === "empresas") filters.groupBy = "company";
  if (section === "relatorios") Object.assign(filters, { reportCatalog: "1", report: "", reportPopulation: "active", reportPage: 1 });
  if (section === "perfil-ativos") filters.groupBy = "departments";
  if (["perfil-ativos", "contratos"].includes(section)) filters.status = "active";
  const reportControls = new Set(["period", "start", "end", "companies", "excludeCompanies", "report", "reportPage", "reportPopulation", "reportColumns", "reportColumnsSet"]);
  for (const [key, value] of Object.entries(query)) {
    if (section === "relatorios" && !reportControls.has(key)) continue;
    filters[key] = arrays.includes(key) ? (Array.isArray(value) ? value : [value]) : (Array.isArray(value) ? value[0] : value);
  }
  for (const key of arrays) if (key !== "sources" || key in filters) filters[key] ||= [];
  return filters;
}

function serialize(filters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (["effectiveGranularity"].includes(key)) continue;
    if (Array.isArray(value)) {
      if (key === "sources" && !value.length) params.append(key, "");
      else value.forEach(item => params.append(key, item));
    } else if (value !== "" && value != null && value !== false) params.set(key, value === true ? "1" : String(value));
  }
  return params.toString();
}

async function readAnalytics(query, signal, reconnect) {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(`/api/ui/people-analytics?${query}`, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]) });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload?.metrics) {
        const error = new Error(payload?.error || "Não foi possível carregar os indicadores locais.");
        error.retryable = [408, 429, 500, 502, 503, 504].includes(response.status);
        error.status = response.status;
        throw error;
      }
      return payload;
    } catch (error) {
      if (signal.aborted) throw error;
      if (attempt >= 2 || !(error.retryable || error instanceof TypeError || error.name === "TimeoutError")) {
        if (error.name === "TimeoutError") throw new Error("O serviço local demorou para responder. Tente atualizar os indicadores novamente.");
        throw error;
      }
      reconnect();
      await new Promise((resolve, reject) => {
        const stop = () => { clearTimeout(timer); reject(signal.reason); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", stop); resolve(); }, [400, 1000][attempt]);
        signal.addEventListener("abort", stop, { once: true });
        if (signal.aborted) { signal.removeEventListener("abort", stop); stop(); }
      });
    }
  }
}

function MultiSelect({ label, options = [], values = [], onChange, limit, help, emptyLabel = "Todas" }) {
  const [search, setSearch] = useState("");
  const id = useId();
  const visible = options.filter(item => norm(item.label).includes(norm(search)));
  return <details className="pa-multi"><summary><span>{label}</span><b>{values.length ? `${values.length} selecionada(s)` : emptyLabel}</b><span aria-hidden="true">⌄</span></summary>
    <div className="pa-multi-menu"><label htmlFor={id} className="visually-hidden">Buscar em {label}</label><input id={id} type="search" placeholder="Buscar nome ou código…" value={search} onChange={event => setSearch(event.target.value)} />
      {help && <p>{help}</p>}
      <div className="pa-multi-tools"><button type="button" onClick={() => onChange([])}>Limpar seleção</button>{!limit && <button type="button" onClick={() => onChange([...new Set([...values, ...visible.map(item => item.value)])])}>Marcar resultados</button>}</div>
      <div className="pa-options">{visible.map(option => <label key={option.value}><input type="checkbox" checked={values.includes(option.value)} disabled={Boolean(limit && values.length >= limit && !values.includes(option.value))} onChange={event => onChange(event.target.checked ? [...values, option.value] : values.filter(item => item !== option.value))} /><span>{option.label}</span>{option.count != null && <small>{number(option.count)}</small>}</label>)}{!visible.length && <p>Nenhuma opção encontrada.</p>}</div>
      {limit && <p>Até {limit} empresas. Sem seleção, o gráfico usa a visão consolidada.</p>}
    </div>
  </details>;
}

function Delta({ value, previous, percentage = false }) {
  if (previous == null || value == null) return <small>Sem base para calcular variação</small>;
  const delta = value - previous;
  return <small className="pa-delta"><b>{delta > 0 ? "+" : ""}{number(delta)}{percentage ? " p.p." : ""}</b>{!percentage && previous !== 0 ? ` (${delta > 0 ? "+" : ""}${number(delta / previous * 100)}%)` : ""} · antes {number(previous)}{percentage ? "%" : ""}</small>;
}

function TrendChart({ points, series, caption, onBucket, chartType = "line", overlaySeries = [], readoutSeries = series }) {
  const [selected, setSelected] = useState(null);
  const uid = useId().replace(/:/g, "");
  const width = Math.max(880, points.length * 24 + 72), height = 265, left = 54, right = 18, top = 22, bottom = 42;
  const plotWidth = width - left - right;
  const slotWidth = plotWidth / Math.max(1, points.length);
  const seriesCount = Math.max(1, series.length);
  const barGap = Math.min(3, slotWidth * .06);
  const barInset = Math.min(8, slotWidth * .12);
  const barWidth = Math.max(1, (slotWidth - 2 * barInset - (seriesCount - 1) * barGap) / seriesCount);
  const barGroupWidth = seriesCount * barWidth + (seriesCount - 1) * barGap;
  const values = points.flatMap(point => [...series, ...overlaySeries].map(item => Number(point[item.key]) || 0));
  const max = Math.max(1, ...values), min = Math.min(0, ...values);
  const x = index => chartType === "bar" ? left + (index + .5) * slotWidth : left + index * plotWidth / Math.max(1, points.length - 1);
  const y = value => top + (max - value) / (max - min || 1) * (height - top - bottom);
  const point = points[Math.min(selected ?? points.length - 1, points.length - 1)];
  if (!points.length || !series.length) return <div className="pa-empty">Nenhum dado para este gráfico.</div>;
  return <div className="pa-trend"><div className="pa-chart-scroll"><svg style={{ width: `${width}px`, maxWidth: points.length <= 36 ? "100%" : "none" }} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={`${uid}-title`}><title id={`${uid}-title`}>{caption}</title>
    {[0, 1, 2, 3, 4].map(tick => { const value = min + (max - min) * tick / 4; return <g key={tick}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke="#e7eef3" /><text x={left - 9} y={y(value) + 4} textAnchor="end" className="pa-svg-label">{number(value)}</text></g>; })}
    {min < 0 && <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} stroke="#8497a7" strokeWidth="1.5" strokeDasharray="4 4" />}
    {chartType === "line" && series.map(item => <polyline key={item.key} fill="none" stroke={item.color} strokeWidth="2.8" strokeLinejoin="round" points={points.map((entry, index) => `${x(index)},${y(entry[item.key] || 0)}`).join(" ")} />)}

    {points.map((entry, index) => {
      const selectedPoint = index === (selected ?? points.length - 1);
      return <g key={index} tabIndex={0} role="button" aria-label={`${entry.label}: ${readoutSeries.map(item => `${item.label} ${number(entry[item.key] || 0)}`).join(", ")}. Abrir período.`} onFocus={() => setSelected(index)} onPointerEnter={() => setSelected(index)} onClick={() => onBucket?.(entry)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onBucket?.(entry); } }}>
        <rect x={chartType === "bar" ? left + index * slotWidth : x(index) - Math.max(4, slotWidth / 2)} y={top} width={Math.max(8, slotWidth)} height={height - top - bottom} fill="transparent" />
        {chartType === "bar" ? series.map((item, seriesIndex) => {
          const value = Number(entry[item.key]) || 0;
          const valueY = y(value), zeroY = y(0);
          return <rect key={item.key} x={x(index) - barGroupWidth / 2 + seriesIndex * (barWidth + barGap)} y={Math.min(valueY, zeroY)} width={barWidth} height={Math.max(1, Math.abs(zeroY - valueY))} rx={Math.min(2, barWidth / 2)} fill={item.color} opacity={selectedPoint ? 1 : .82} />;
        }) : (index === selected || points.length === 1) && series.map(item => <circle key={item.key} cx={x(index)} cy={y(entry[item.key] || 0)} r="4" fill={item.color} stroke="white" strokeWidth="2" />)}
      {(index % Math.max(1, Math.ceil(points.length / 8)) === 0 || index === points.length - 1) && <text x={x(index)} y={height - 14} textAnchor="middle" className="pa-svg-label">{entry.label}</text>}
    </g>;
    })}
    {chartType === "bar" && overlaySeries.map(item => <g key={item.key} aria-hidden="true" pointerEvents="none"><polyline fill="none" stroke={item.color} strokeWidth="3" strokeLinejoin="round" points={points.map((entry, index) => `${x(index)},${y(entry[item.key] || 0)}`).join(" ")} />{points.map((entry, index) => <circle key={index} cx={x(index)} cy={y(entry[item.key] || 0)} r="3.5" fill={item.color} stroke="white" strokeWidth="1.5" />)}</g>)}
    </svg></div>{chartType === "bar" && selected != null && <div className="pa-chart-tooltip" role="status" aria-live="polite"><strong>{point?.label}</strong>{readoutSeries.map(item => <span key={item.key}><i style={{ background: item.color }} />{item.label}: <b>{number(point?.[item.key] || 0)}</b></span>)}</div>}<div className={`pa-chart-readout ${chartType === "bar" && selected != null ? "visually-hidden" : ""}`} aria-live="polite"><strong>{point?.label}</strong>{readoutSeries.map(item => <span key={item.key}><i style={{ background: item.color }} />{item.label}: <b>{number(point?.[item.key] || 0)}</b></span>)}</div></div>;
}

function BarList({ rows, metric, label = "name", onChoose, color = "#2563eb" }) {
  const max = Math.max(1, ...rows.map(row => Math.abs(row[metric] || 0)));
  return <div className="pa-bars">{rows.map((row, index) => <button type="button" key={row.key || row.label || index} disabled={!onChoose} onClick={() => onChoose?.(row)} title={row[label]}><span className="pa-bar-name">{row.code && <small>{row.code} · </small>}{row[label]}</span><span className="pa-bar-track"><i style={{ width: `${Math.abs(row[metric] || 0) / max * 100}%`, background: (row[metric] || 0) < 0 ? colors.terminated : color }} /></span><strong>{number(row[metric])}{metric === "turnover" && row[metric] != null ? "%" : ""}</strong></button>)}{!rows.length && <p className="pa-empty">Sem registros para esta seleção.</p>}</div>;
}

function AnalyticsMethod({ open = false }) {
  return <details className="pa-card pa-method" open={open}><summary>Como os indicadores são calculados</summary><dl><dt>Ativos na data de corte</dt><dd>Admissão até a data de corte e nenhuma saída até essa data. Datas inválidas ou sem admissão não confirmam atividade. Uma saída na data de corte já encerra o vínculo.</dd><dt>Admissões e desligamentos</dt><dd>Eventos com datas válidas e consistentes dentro do intervalo, incluindo início e fim. Vínculos com datas em conferência ficam fora dos indicadores de movimentação. Recontratações contam como novos vínculos. “Pessoas identificadas” usa o agrupamento disponível em Cadastros; a unidade principal da análise é o vínculo.</dd><dt>Rotatividade</dt><dd>((admissões + desligamentos) ÷ 2) ÷ ((ativos na véspera do início + ativos no fim) ÷ 2) × 100. Sem quadro médio, aparece “—”. Períodos longos podem superar 100%.</dd><dt>Comparações</dt><dd>Usam os mesmos filtros de empresa e cadastro. A situação é filtrada na data de corte principal. Períodos personalizados podem ter durações diferentes; os totais não são normalizados por dia. Se a base não cobre o período anterior, a comparação pode estar incompleta.</dd><dt>Cobertura e fontes</dt><dd>A base SIB pode estar parcialmente importada. Os indicadores refletem somente os vínculos disponíveis. O SIB é a origem padrão quando disponível. Ao combinar origens, registros da V2 e locais podem representar o mesmo vínculo; confira antes de somar. Registros de teste ficam fora por padrão. Códigos sem descrição não são convertidos em cargos, contratos ou motivos.</dd></dl></details>;
}

export default function PeopleAnalytics({ section = "visao-geral", initialQuery = {} }) {
  const [draft, setDraft] = useState(() => initialFilters(initialQuery, section));
  const [applied, setApplied] = useState(() => serialize(initialFilters(initialQuery, section)));
  const [reload, setReload] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [unauthorized, setUnauthorized] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const forceRefresh = useRef(false);
  const [lineMetrics, setLineMetrics] = useState(["admitted", "terminated", "active"]);
  const [timelineChartType, setTimelineChartType] = useState(section === "admitidos-demitidos" ? "bar" : "line");
  const [showTimelineBalance, setShowTimelineBalance] = useState(true);
  const [compareMetric, setCompareMetric] = useState("active");
  const [allCompanies, setAllCompanies] = useState(false);
  const [table, setTable] = useState("companies");
  const [savedViews, setSavedViews] = useState([]);
  const [viewName, setViewName] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const pendingReportFocus = useRef("");
  const overview = section === "visao-geral";
  const movements = section === "admitidos-demitidos";
  const companies = section === "empresas";
  const distribution = ["perfil-ativos", "tipos-contrato"].includes(section);
  const quality = section === "qualidade";
  const guide = section === "pro-como-usar";
  const kpiKeys = distribution ? ["active", "people"] : section === "retencao" ? ["terminated", "turnover"] : ["active", "admitted", "terminated", "balance", "people"];
  const showTimeline = overview || movements;
  const showBreakdown = overview || companies || distribution;
  const showRanking = overview || companies;
  function reveal(id) {
    const element = document.getElementById(id);
    if (element?.tagName === "DETAILS") element.open = true;
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  useEffect(() => { try { const value = JSON.parse(localStorage.getItem("conexaodp.analytics.views") || "[]"); if (Array.isArray(value)) setSavedViews(value.filter(item => typeof item.name === "string" && typeof item.query === "string").slice(0, 20)); } catch {} }, []);
  useEffect(() => {
    if (guide) { setLoading(false); return; }
    const controller = new AbortController();
    const requestQuery = `${applied}${forceRefresh.current ? "&refresh=1" : ""}`;
    forceRefresh.current = false;
    setLoading(true); setError(""); setReconnecting(false); setUnauthorized(false);
    const timer = setTimeout(() => {
      readAnalytics(requestQuery, controller.signal, () => setReconnecting(true))
        .then(payload => { if (!controller.signal.aborted) { setData(payload); setDraft({ ...payload.filters, page: payload.page }); } })
        .catch(cause => { if (!controller.signal.aborted) { setError(cause.message); setUnauthorized(cause.status === 401); } })
        .finally(() => { if (!controller.signal.aborted) { setLoading(false); setReconnecting(false); } });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [applied, reload, guide]);
  useEffect(() => {
    if (!data?.meta.snapshotAt || loading || guide || unauthorized) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      readAnalytics(applied, controller.signal, () => {})
        .then(payload => { if (!controller.signal.aborted) setData(payload); })
        .catch(cause => {
          if (controller.signal.aborted) return;
          if (cause.status === 401) { setError(cause.message); setUnauthorized(true); return; }
          setData(previous => previous ? { ...previous, meta: { ...previous.meta,
            snapshotRefreshing: false, snapshotError: "A última análise continua visível. A atualização será tentada novamente." } } : previous);
        });
    }, data.meta.snapshotRefreshing ? 2000 : 15000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [data, applied, loading, guide, unauthorized]);
  useEffect(() => {
    const selectedId = data?.reports?.selected?.id;
    if (section !== "relatorios" || loading || !selectedId || selectedId !== pendingReportFocus.current) return;
    pendingReportFocus.current = "";
    document.getElementById("pr-column-picker")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [section, loading, data?.reports?.selected?.id]);

  const update = (key, value) => setDraft(current => ({ ...current, [key]: value }));
  const changeDate = (key, event) => {
    const value = event.currentTarget.value;
    setDraft(current => current[key] === value ? current : { ...current, [key]: value, ...(["start", "end"].includes(key) ? { period: "custom" } : {}) });
  };
  function apply(filters = draft) {
    const query = serialize(filters);
    setApplied(query); setReload(value => value + 1);
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }
  const refine = patch => apply({ ...data.filters, ...patch, page: patch.page || 1, ...(section === "relatorios" ? { reportPage: patch.reportPage || 1 } : {}) });
  const selectReport = id => {
    pendingReportFocus.current = id || "";
    refine({ report: id, reportPage: 1, reportColumns: [], reportColumnsSet: "" });
  };
  const reset = () => apply(initialFilters({}, section));
  const refreshIndicators = () => { forceRefresh.current = true; setReload(value => value + 1); };
  const dirty = data && serialize({ ...draft, page: data.page }) !== serialize({ ...data.filters, page: data.page });
  const current = data?.filters;
  const toggleRecordSort = key => refine({ sort: key, direction: current.sort === key && current.direction === "asc" ? "desc" : "asc" });
  const metrics = data?.metrics;
  const selectedCompanyOptions = data?.options.companies || [];
  const comparisonSeries = (data?.companyComparison || []).map((company, index) => ({ key: company.key, label: `${company.code ? `${company.code} · ` : ""}${company.name}`, color: palette[index % palette.length] }));
  const comparisonPoints = (data?.timeline || []).map((point, index) => ({ ...point, ...Object.fromEntries((data?.companyComparison || []).map(company => [company.key, company.timeline[index]?.[compareMetric] || 0])) }));
  const chips = [];
  if (data) {
    for (const key of ["companies", "excludeCompanies", "sources", ...Object.keys(dimensions)]) {
      for (const value of current[key] || []) {
        const option = (key.includes("Companies") || key === "companies" ? data.options.companies : data.options[key])?.find(item => item.value === value);
        chips.push({ key, value, label: `${key === "excludeCompanies" ? "Excluir: " : ""}${option?.label || value}` });
      }
    }
    if (current.q) chips.push({ key: "q", value: current.q, label: `Busca: ${current.q}` });
    if (current.status !== "all") chips.push({ key: "status", value: current.status, label: `Situação: ${statuses[current.status]}` });
  }

  function saveView() {
    if (!viewName.trim()) return;
    // Store filter preferences only, never individual records or search text.
    const params = new URLSearchParams(applied); params.delete("q"); params.delete("page");
    const views = [{ name: viewName.trim().slice(0, 60), query: params.toString() }, ...savedViews.filter(item => item.name !== viewName.trim())].slice(0, 20);
    try { localStorage.setItem("conexaodp.analytics.views", JSON.stringify(views)); setSavedViews(views); setViewName(""); setSaveMessage("Seleção salva neste navegador. A busca por pessoa não foi salva."); } catch { setSaveMessage("Não foi possível salvar neste navegador."); }
  }

  return <div className="page-content pa-page">
    <header className="pa-heading page-intro-toolbar"><div><p>{section === "relatorios" ? "Escolha o relatório, filtre a empresa e o período e emita sua planilha Excel." : ({ "visao-geral": "Acompanhe o quadro de pessoal e as movimentações da sua equipe.", "admitidos-demitidos": "Compare as entradas e saídas de cada período e confira os vínculos.", empresas: "Compare empresas no mesmo período e identifique as maiores movimentações.", "perfil-ativos": "Conheça a distribuição das pessoas ativas na data de corte.", "tipos-contrato": "Analise os vínculos por tipo de contrato.", retencao: "Entenda o tempo de permanência dos vínculos encerrados.", qualidade: "Identifique informações ausentes e datas que precisam de conferência.", tabelas: "Consulte os registros e os números que compõem a análise.", "pro-como-usar": "Consulte as regras e a cobertura dos indicadores." })[section]}</p></div><div className="pa-heading-actions">{!guide && <button type="button" className="btn btn-primary" disabled={loading} onClick={refreshIndicators}>{section === "relatorios" ? "↻ Atualizar relatórios" : "↻ Atualizar indicadores"}</button>}</div></header>
    {data && <div className="pa-base-note"><span className={`pa-source-dot ${data.meta.scanRunning ? "is-running" : ""}`} /><div><strong>{data.meta.scanRunning ? "Importação em andamento · indicadores parciais" : "Base disponível para análise"}</strong><span>{number(data.meta.filteredLinks)} vínculos · {number(data.meta.companyCount)} empresas</span></div><a href="/central-dp/integracoes#sib-scan">Ver sincronização →</a></div>}
    {data?.meta.snapshotAt && <p className="pa-snapshot-note" role="status">{data.meta.snapshotError || (data.meta.snapshotRefreshing ? "Sincronizando alterações recentes em segundo plano. A análise continua disponível." : "Indicadores atualizados automaticamente a partir da base local.")}</p>}
    {error && <div className="pa-alert" role="alert"><div><strong>Não foi possível atualizar os indicadores.</strong><p>{error}{data ? " A última análise continua visível." : ""}</p></div>{unauthorized ? <a href="/entrar" className="btn btn-primary">Entrar novamente</a> : <button className="btn btn-outline-primary" disabled={loading} onClick={refreshIndicators}>Tentar novamente</button>}</div>}
    {loading && <div className="pa-loading" role="status"><i />{reconnecting ? "Reconectando ao serviço local…" : data ? "Atualizando a análise…" : "Preparando indicadores dos cadastros importados…"}</div>}
    {guide && <AnalyticsMethod open />}
    {data && <>
      {overview && data.absences && <div className="absence-analytics"><div><strong>{data.absences.confirmed ? `${number(data.absences.active)} vínculos com afastamento confirmado no corte` : "Afastamentos · aguardando sincronização"}</strong><p>{number(data.absences.confirmed)} de {number(data.absences.total)} vínculos SIB com consulta confirmada. {data.absences.unknown ? `${number(data.absences.unknown)} sem confirmação; cobertura parcial.` : "Confira a data da última consulta na central."} Data de corte: {day(data.absences.cutoff)}.</p></div><a href={`/central-dp/afastamentos?cutoff=${data.absences.cutoff}`}>Abrir central de afastamentos →</a></div>}
      {section === "relatorios" && <ReportCatalog data={data} loading={loading} onSelect={selectReport} />}
      {section === "relatorios" ? <ReportFilters data={data} draft={draft} loading={loading} dirty={Boolean(dirty)} onUpdate={update} onChangeDate={changeDate} onApply={() => apply({ ...draft, period: "custom", reportPage: 1, page: 1 })} onReset={() => apply({ ...initialFilters({}, section), report: current.report, reportColumns: draft.reportColumns, reportColumnsSet: draft.reportColumnsSet })} CompanySelect={MultiSelect} /> : !guide && <>
      <section className="pa-card pa-filter-card" aria-labelledby="pa-filter-title"><div className="pa-card-heading"><div><span className="pa-eyebrow">REFINE A ANÁLISE</span><h2 id="pa-filter-title">{quality ? "Seleção da base" : "Período e seleção"}</h2></div><span className="pa-tag">{dirty ? "Alterações por aplicar" : "Filtros aplicados"}</span></div>
        <form onSubmit={event => { event.preventDefault(); apply({ ...draft, page: 1, ...(section === "relatorios" ? { reportPage: 1 } : {}) }); }}><fieldset disabled={loading}>
          {!quality && <div className="pa-filter-grid"><label>Período<select aria-label="Período" value={draft.period} onChange={event => update("period", event.target.value)}><option value="all">Todo o histórico importado</option><option value="month">Mês atual</option><option value="3months">Últimos 3 meses</option><option value="6months">Últimos 6 meses</option><option value="12months">Últimos 12 meses</option><option value="year">Ano atual</option><option value="lastyear">Ano anterior</option><option value="custom">Escolher datas</option></select></label>
            <label>Data inicial<input type="date" value={draft.start || ""} min="1900-01-01" max={draft.end || data.meta.today} name="start" onInput={event => changeDate("start", event)} onChange={event => changeDate("start", event)} onBlur={event => changeDate("start", event)} /></label><label>Data de corte<input type="date" value={draft.end || ""} min={draft.start || "1900-01-01"} max={data.meta.today} name="end" onInput={event => changeDate("end", event)} onChange={event => changeDate("end", event)} onBlur={event => changeDate("end", event)} /></label>
            <label>Agrupar evolução<select aria-label="Agrupar evolução" value={draft.granularity} onChange={event => update("granularity", event.target.value)}><option value="auto">Automático</option><option value="month">Por mês</option><option value="quarter">Por trimestre</option><option value="year">Por ano</option></select></label>
          </div>}
          <div className="pa-company-filters pa-primary-company"><MultiSelect label="Incluir somente estas empresas" options={selectedCompanyOptions} values={draft.companies} onChange={value => update("companies", value)} help="Sem seleção, todas as empresas entram na análise." />{companies && <MultiSelect label="Comparar empresas lado a lado" options={selectedCompanyOptions} values={draft.compareCompanies} onChange={value => update("compareCompanies", value)} emptyLabel="Escolher empresas" limit={6} />}</div>
          <details className="pa-advanced"><summary>{quality ? "Mais filtros" : "Mais filtros e comparações"}</summary><div className="pa-filter-grid"><MultiSelect label="Retirar empresas da análise" options={selectedCompanyOptions} values={draft.excludeCompanies} onChange={value => update("excludeCompanies", value)} emptyLabel="Nenhuma exclusão" />{!companies && <MultiSelect label="Comparar empresas lado a lado" options={selectedCompanyOptions} values={draft.compareCompanies} onChange={value => update("compareCompanies", value)} emptyLabel="Sem comparação" limit={6} />}<label>Situação na data de corte<select aria-label="Situação na data de corte" value={draft.status} onChange={event => update("status", event.target.value)}><option value="all">Todas as situações</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><MultiSelect label="Origem dos cadastros" options={data.options.sources} values={draft.sources} onChange={value => update("sources", value)} />{Object.entries(dimensions).map(([key, label]) => <MultiSelect key={key} label={label} options={data.options[key]} values={draft[key]} onChange={value => update(key, value)} />)}<label className="pa-search">Buscar nome, matrícula ou empresa<input type="search" value={draft.q || ""} maxLength={100} onChange={event => update("q", event.target.value)} placeholder="Digite o que deseja analisar" /></label><label className="pa-check"><input type="checkbox" checked={draft.includeTests === true || draft.includeTests === "1"} onChange={event => update("includeTests", event.target.checked)} />Incluir cadastros marcados como teste</label></div>          {!quality && <div className="pa-compare-filter pa-period-comparison"><label>Comparar o período com<select aria-label="Comparar o período com" value={draft.compare} onChange={event => update("compare", event.target.value)}><option value="none">Sem comparação de períodos</option><option value="previous">Período anterior de mesma duração</option><option value="year">Mesmas datas do ano anterior</option><option value="custom">Outro período</option></select></label>{draft.compare === "custom" && <><label>Comparação: início<input type="date" min="1900-01-01" max={data.meta.today} value={draft.compareStart || ""} name="compareStart" onInput={event => changeDate("compareStart", event)} onChange={event => changeDate("compareStart", event)} onBlur={event => changeDate("compareStart", event)} required /></label><label>Comparação: fim<input type="date" min={draft.compareStart || "1900-01-01"} max={data.meta.today} value={draft.compareEnd || ""} name="compareEnd" onInput={event => changeDate("compareEnd", event)} onChange={event => changeDate("compareEnd", event)} onBlur={event => changeDate("compareEnd", event)} required /></label></>}</div>}<p className="pa-help">Campos ausentes aparecem como “Não informado”. Códigos de saída do SIB são exibidos como recebidos, sem atribuir um motivo presumido.</p></details>
          <div className="pa-compare-filter"><button type="submit" className="btn btn-primary">Aplicar filtros</button><button type="button" className="btn btn-outline-primary" onClick={reset}>Limpar filtros</button></div>
        </fieldset></form>
        <div className="pa-applied">{chips.map(chip => <button type="button" key={`${chip.key}:${chip.value}`} disabled={loading} onClick={() => refine({ [chip.key]: Array.isArray(current[chip.key]) ? current[chip.key].filter(item => item !== chip.value) : chip.key === "status" ? "all" : "" })} title={`Remover filtro ${chip.label}`}>{chip.label}<span aria-hidden="true">×</span></button>)}</div>
        <details className="pa-saved"><summary>Salvar e reutilizar seleções neste navegador</summary><div><label>Nome da seleção<input value={viewName} maxLength={60} onChange={event => setViewName(event.target.value)} placeholder="Ex.: Empresas de Suzano" /></label><button type="button" className="btn btn-outline-primary" disabled={!viewName.trim() || loading || dirty} onClick={saveView}>Salvar seleção aplicada</button><label>Seleções salvas<select defaultValue="" onChange={event => { if (event.target.value) { const params = new URLSearchParams(event.target.value); const query = {}; for (const key of new Set(params.keys())) query[key] = arrays.includes(key) ? params.getAll(key) : params.get(key); apply(initialFilters(query, section)); } event.target.value = ""; }}><option value="">Escolha uma seleção</option>{savedViews.map(item => <option key={item.name} value={item.query}>{item.name}</option>)}</select></label></div><p role="status">{saveMessage || "Salva apenas preferências de filtro. Nenhuma ficha de pessoa é armazenada no navegador."}</p></details>
      </section>
      </>}
      {!quality && !guide && section !== "relatorios" && <>
      <div className="pa-selection-heading"><div><h2>{day(current.start)} — {day(current.end)}</h2><p>Admissões e desligamentos ocorridos no intervalo · ativos ao fim de {day(current.end)}.</p></div>{data.comparison && <span className="pa-tag">Comparado com {day(data.comparison.start)} — {day(data.comparison.end)}</span>}</div>
      <section className={`pa-kpis ${kpiKeys.length < 3 ? "is-compact" : ""}`} aria-label="Indicadores da seleção">{kpiKeys.map(key => <button type="button" key={key} className={`pa-kpi pa-kpi-${key}`} disabled={loading} onClick={() => { if (["active", "admitted", "terminated", "people"].includes(key)) { refine({ focus: key === "people" ? "all" : key }); reveal("pa-records"); } else { setTable(key === "balance" ? "timeline" : "companies"); reveal("pa-summary-table"); } }}><span>{metricLabels[key]}{key === "active" ? " na data de corte" : key === "balance" ? " de movimentações" : " no período"}</span><strong>{number(metrics[key])}{key === "turnover" && metrics[key] != null ? "%" : ""}</strong>{data.comparison ? <Delta value={metrics[key]} previous={data.comparison.metrics[key]} percentage={key === "turnover"} /> : <small>{key === "active" ? `${number(metrics.activePeople)} pessoas identificadas` : key === "admitted" ? "Cada admissão conta um vínculo" : key === "terminated" ? "Pela data efetiva de saída" : key === "balance" ? "Admissões menos desligamentos" : key === "turnover" ? "Movimentações em relação ao quadro médio" : "Agrupamento disponível em Vínculos"}</small>}</button>)}</section>
      </>}
      {section === "relatorios" && <ReportResult data={data} loading={loading} pendingFilters={Boolean(dirty)} onRefine={refine} onSelect={selectReport} />}
      {section !== "relatorios" && <>

      {showTimeline && <section className="pa-card"><div className="pa-card-heading"><div><span className="pa-eyebrow">EVOLUÇÃO NO TEMPO</span><h2>Entradas, saídas e quadro ativo</h2><p>Passe o mouse sobre um período para ver os números; clique para analisar o intervalo.</p></div><div className="pa-timeline-tools"><label className="pa-chart-type">Formato<select aria-label="Formato do gráfico" value={timelineChartType} onChange={event => setTimelineChartType(event.target.value)}><option value="line">Linhas</option><option value="bar">Barras</option></select></label><div className="pa-legend">{["admitted", "terminated", "active"].map(key => <button type="button" key={key} aria-pressed={lineMetrics.includes(key)} className={lineMetrics.includes(key) ? "is-active" : ""} onClick={() => setLineMetrics(value => value.includes(key) ? value.length > 1 ? value.filter(item => item !== key) : value : [...value, key])}><i style={{ background: colors[key] }} />{metricLabels[key]}</button>)}{timelineChartType === "bar" && <button type="button" aria-pressed={showTimelineBalance} className={showTimelineBalance ? "is-active" : ""} onClick={() => setShowTimelineBalance(value => !value)}><i style={{ background: colors.balance }} />Saldo</button>}</div></div></div><TrendChart points={data.timeline} series={lineMetrics.map(key => ({ key, label: metricLabels[key], color: colors[key] }))} readoutSeries={timelineChartType === "bar" ? [{ key: "admitted", label: "Admitidos", color: colors.admitted }, { key: "terminated", label: "Demitidos", color: colors.terminated }, { key: "active", label: "Ativos no fim", color: colors.active }, { key: "balance", label: "Saldo", color: colors.balance }] : lineMetrics.map(key => ({ key, label: metricLabels[key], color: colors[key] }))} overlaySeries={timelineChartType === "bar" && showTimelineBalance ? [{ key: "balance", label: "Saldo", color: colors.balance }] : []} caption="Evolução de admissões, desligamentos, ativos e saldo" chartType={timelineChartType} onBucket={point => refine({ period: "custom", start: point.start, end: point.end })} /></section>}
      {(companies || comparisonSeries.length > 0) && !guide && !quality && <section className="pa-card"><div className="pa-card-heading"><div><span className="pa-eyebrow">COMPARAÇÃO ENTRE EMPRESAS</span><h2>Empresas lado a lado</h2><p>Mesmas datas e mesmos filtros para todas as empresas escolhidas.</p></div><label>Indicador do gráfico<select aria-label="Indicador do gráfico" value={compareMetric} onChange={event => setCompareMetric(event.target.value)}>{["active", "admitted", "terminated", "balance"].map(key => <option key={key} value={key}>{metricLabels[key]}</option>)}</select></label></div>
        {data.meta.comparisonOmitted > 0 && <p className="pa-help">{data.meta.comparisonOmitted} empresa(s) do comparativo ficaram fora pela seleção ou exclusão de empresas. Ajuste esses filtros para compará-las.</p>}
        {comparisonSeries.length ? <><div className="pa-company-kpis">{data.companyComparison.map((company, index) => <article key={company.key} style={{ borderTopColor: palette[index] }}><h3>{company.code && <small>{company.code} · </small>}{company.name}</h3><dl>{["active", "admitted", "terminated", "balance"].map(key => <div key={key}><dt>{metricLabels[key]}</dt><dd>{number(company[key])}</dd></div>)}</dl>{!company.links && !company.active && <p>Sem vínculos neste período e seleção. Confira as exclusões aplicadas.</p>}</article>)}</div><TrendChart points={comparisonPoints} series={comparisonSeries} caption={`Comparação de ${metricLabels[compareMetric]} entre empresas`} onBucket={point => refine({ period: "custom", start: point.start, end: point.end })} /></> : <div className="pa-compare-empty"><span aria-hidden="true">⇄</span><div><h3>Quais empresas você quer comparar?</h3><p>Use “Comparar empresas lado a lado” nos filtros. Você pode comparar de duas a seis empresas e alternar entre ativos, admissões, desligamentos e saldo.</p><a href="#pa-filter-title">Escolher empresas nos filtros ↑</a></div></div>}
      </section>}
      {showBreakdown && <div className={`pa-two-columns ${showRanking ? "" : "pa-single-column"}`}>{showRanking && <section className="pa-card"><div className="pa-card-heading"><div><span className="pa-eyebrow">PARTICIPAÇÃO DAS EMPRESAS</span><h2>Ranking da seleção</h2></div><label>Ordenar por<select aria-label="Ordenar por" value={current.rankBy} disabled={loading} onChange={event => refine({ rankBy: event.target.value })}>{Object.entries(metricLabels).filter(([key]) => key !== "people").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div><BarList rows={data.companies.filter(row => row[current.rankBy] != null && row[current.rankBy] !== 0).slice(0, 10)} metric={current.rankBy} onChoose={row => refine({ companies: [row.key] })} /><p className="pa-help">Até 10 empresas com valor no indicador. Clique para filtrar; a tabela reúne a seleção completa.</p></section>}<section className="pa-card"><div className="pa-card-heading"><div><span className="pa-eyebrow">DISTRIBUIÇÃO DOS VÍNCULOS</span><h2>Recortes da seleção</h2></div><label>Agrupar por<select aria-label="Agrupar por" value={current.groupBy} disabled={loading} onChange={event => refine({ groupBy: event.target.value })}><option value="company">Empresa</option><option value="source">Origem</option>{Object.entries(dimensions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div><BarList rows={data.breakdown.filter(row => row[current.rankBy] != null && row[current.rankBy] !== 0).slice(0, 10)} metric={current.rankBy} label="label" color="#0d947d" onChoose={row => refine(current.groupBy === "company" ? { companies: [row.key] } : current.groupBy === "source" ? { sources: [row.key] } : { [current.groupBy]: [row.key] })} /><p className="pa-help">Indicador: {metricLabels[current.rankBy]}. Campos ausentes são agrupados como “Não informado”.</p></section></div>}
      {(section === "retencao") && <section className="pa-card"><div className="pa-card-heading"><div><span className="pa-eyebrow">PERMANÊNCIA ATÉ O DESLIGAMENTO</span><h2>Tempo de vínculo</h2><p>Somente desligamentos no período com admissão e saída válidas.</p></div><strong className="pa-tenure">{number(metrics.averageTenure)} <small>dias em média</small></strong></div><div className="pa-retention">{data.retention.map(item => <div key={item.label}><span>{item.label}</span><strong>{number(item.count)}</strong><i style={{ width: `${item.count / Math.max(1, ...data.retention.map(row => row.count)) * 100}%` }} /></div>)}</div></section>}
      {!quality && !guide && <details className="pa-card pa-disclosure" id="pa-summary-table" open={section === "tabelas"}><summary><span>Conferir números em tabela</span><small>Empresas, períodos e recortes</small></summary><div className="pa-disclosure-body"><div className="pa-card-heading"><div><span className="pa-eyebrow">CONFERÊNCIA DOS NÚMEROS</span><h2>Tabelas da análise</h2></div><div className="pa-segmented">{[["companies", "Empresas"], ["timeline", "Períodos"], ["breakdown", "Recortes"]].map(([key, label]) => <button type="button" key={key} aria-pressed={table === key} onClick={() => setTable(key)}>{label}</button>)}</div></div><div className="pa-table-wrap"><table><thead><tr><th>{table === "companies" ? "Empresa" : table === "timeline" ? "Período" : "Recorte"}</th><th>Ativos no corte</th><th>Admissões</th><th>Desligamentos</th><th>Saldo</th>{table !== "timeline" && <th>Rotatividade</th>}</tr></thead><tbody>{(table === "companies" ? data.companies.slice(0, allCompanies ? undefined : 20) : table === "timeline" ? data.timeline : data.breakdown).map((row, index) => <tr key={row.key || row.label || index}><th scope="row">{table === "companies" ? <button type="button" disabled={loading} onClick={() => refine({ companies: [row.key] })}>{row.code && <span className="pa-code">{row.code}</span>}{row.name}</button> : row.label}</th>{["active", "admitted", "terminated", "balance"].map(key => <td key={key}>{number(row[key])}</td>)}{table !== "timeline" && <td>{row.turnover == null ? "—" : `${number(row.turnover)}%`}</td>}</tr>)}</tbody></table></div>{table === "companies" && data.companies.length > 20 && <button type="button" className="pa-text-button" onClick={() => setAllCompanies(value => !value)}>{allCompanies ? "Mostrar as primeiras 20" : `Mostrar todas as ${number(data.companies.length)} empresas`}</button>}{!(table === "companies" ? data.companies : table === "timeline" ? data.timeline : data.breakdown).length && <p className="pa-empty">Nenhum registro nesta seleção.</p>}</div></details>}
      {quality && <section className="pa-card pa-quality"><div className="pa-card-heading"><div><span className="pa-eyebrow">QUALIDADE E COBERTURA</span><h2>O que os dados permitem concluir</h2></div><a href="/cadastros/conflitos">Conferir conflitos →</a></div><div className="pa-quality-grid">{[["missingAdmission", "Sem admissão válida"], ["invalidDates", "Datas em conferência"], ["missingContract", "Contrato não informado"], ["missingDepartment", "Departamento não informado"], ["missingJob", "Cargo não informado"]].map(([key, label]) => <button type="button" key={key} disabled={loading} aria-pressed={current.qualityIssue === key} onClick={() => refine({ qualityIssue: key })}><strong>{number(data.quality[key])}</strong><span>{label}</span></button>)}</div><button className="pa-text-button" type="button" onClick={() => refine({ qualityIssue: "all" })}>Ver todas as pendências</button><p>Estes totais consideram todos os vínculos que atendem aos filtros, mesmo fora do período. Ausência de informação não é zero nem uma classificação presumida.</p></section>}
      {!guide && <details className="pa-card pa-disclosure" id="pa-records" open={section === "tabelas" || movements || quality}>
        <summary><span>Vínculos da seleção</span><small>{number(data.recordTotal)} registros</small></summary>
        <div className="pa-disclosure-body">
          <div className="pa-card-heading">
            <div><span className="pa-eyebrow">REGISTROS QUE COMPÕEM A ANÁLISE</span><h2>{quality ? "Cadastros com informações a conferir" : focusLabels[current.focus]}</h2><p>{number(data.recordTotal)} vínculo(s). {quality ? "Os registros correspondem à pendência escolhida, inclusive fora do período." : "Abra o cadastro para conferir a ficha."}</p></div>
            {!movements && !quality && <label>Exibir<select aria-label="Exibir registros" value={current.focus} disabled={loading} onChange={event => refine({ focus: event.target.value })}>{Object.entries(focusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
          </div>
          {movements && <div className="pa-segmented pa-population-tabs" aria-label="Movimentações"><button type="button" aria-pressed={current.focus === "admitted"} onClick={() => refine({ focus: "admitted" })}>Admitidos</button><button type="button" aria-pressed={current.focus === "terminated"} onClick={() => refine({ focus: "terminated" })}>Desligados</button><button type="button" aria-pressed={current.focus === "active"} onClick={() => refine({ focus: "active" })}>Ativos no corte</button><button type="button" aria-pressed={current.focus === "all"} onClick={() => refine({ focus: "all" })}>Todos os vínculos</button></div>}
          <div className="pa-record-controls">
            <label>Ordenação<select aria-label="Ordenação dos registros" value={current.sort} disabled={loading} onChange={event => refine({ sort: event.target.value })}>{[["registration", "Matrícula"], ["name", "Nome"], ["company", "Empresa"], ["admission_date", "Admissão"], ["termination_date", "Desligamento"], ["status", "Situação no corte"], ["source", "Origem"]].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            <label>Sentido<select aria-label="Sentido da ordenação" value={current.direction} disabled={loading} onChange={event => refine({ direction: event.target.value })}><option value="asc">Crescente</option><option value="desc">Decrescente</option></select></label>
          </div>
          <div className="pa-table-wrap"><table>
            <thead><tr>
              {[["registration", "Matrícula"], ["name", "Nome"], ["company", "Empresa"], ["admission_date", "Admissão"], ["termination_date", "Desligamento"], ["status", "Situação no corte"], ["source", "Origem"]].map(([key, label]) => <th scope="col" key={key} aria-sort={current.sort === key ? (current.direction === "asc" ? "ascending" : "descending") : undefined}><button type="button" className={`pa-sort-button${current.sort === key ? " is-sorted" : ""}`} disabled={loading} onClick={() => toggleRecordSort(key)} aria-label={`Ordenar por ${label}${current.sort === key ? (current.direction === "asc" ? ", crescente" : ", decrescente") : ""}`}><span>{label}</span><span className="pa-sort-indicator" aria-hidden="true">{current.sort === key ? (current.direction === "asc" ? "↑" : "↓") : "↕"}</span></button></th>)}
            </tr></thead>
            <tbody>{data.records.map(row => <tr key={row.key}><td><span className="pa-code">{row.registration || "—"}</span></td><th scope="row"><a href={`/cadastros/pessoas?id=${encodeURIComponent(row.key)}&status=all`}>{row.name}</a></th><td>{row.companyCode ? `${row.companyCode} · ` : ""}{row.company}</td><td>{day(row.admission)}</td><td>{day(row.termination)}</td><td><span className={`pa-status is-${row.status}`}>{statuses[row.status]}</span></td><td>{row.source}</td></tr>)}</tbody>
          </table></div>
          {!data.records.length && <p className="pa-empty">Nenhum vínculo corresponde à seleção.</p>}
          <div className="pa-pagination"><button type="button" className="btn btn-outline-primary" disabled={loading || data.page <= 1} onClick={() => refine({ page: data.page - 1 })}>Anterior</button><span>Página {data.page} de {Math.max(1, Math.ceil(data.recordTotal / data.pageSize))}</span><button type="button" className="btn btn-outline-primary" disabled={loading || data.page * data.pageSize >= data.recordTotal} onClick={() => refine({ page: data.page + 1 })}>Próxima</button></div>
        </div>
      </details>}

      {!guide && <AnalyticsMethod />}
      </>}
      <footer className="pa-footer">Calculado em {new Date(data.meta.generatedAt).toLocaleString("pt-BR")} · Dados locais da V3 · Consulta somente leitura</footer>
    </>}
  </div>;
}
