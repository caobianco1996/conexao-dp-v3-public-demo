"use client";

import { useEffect, useState } from "react";
import { AbsenceFollowup, AbsenceSettings } from "./absence-followup";

const labels = { all: "Todos", active: "Em andamento", closed: "Encerrados", future: "Programados", review: "Conferir" };
const recordLabels = { active: "Em andamento", closed: "Encerrado", future: "Programado", review: "Conferir" };
const num = value => Number(value || 0).toLocaleString("pt-BR");
const day = value => value ? value.split("-").reverse().join("/") : "Sem fim informado";
const historyValue = (value, key) => key === "sourceMissing" ? (value ? "Sim" : "Não") : key === "employmentKey" ? (value ? "Identidade confirmada" : "A conferir") : ["startDate", "endDate"].includes(key) ? day(value) : String(value || "Não informado");
const instant = value => value ? new Date(value * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "Ainda não consultado";

export function AbsenceRecords({ value }) {
  if (!value || !value.coverage.total) return null;
  return <section className="absence-inline"><div className="registry-detail-section-head"><h3>Afastamentos do SIB</h3><a href="/central-dp/afastamentos">Abrir central →</a></div>
    <p className="registry-help-text">{value.coverage.unknown ? "Consulta ainda não confirmada para todos os vínculos desta ficha." : `Última consulta confirmada: ${instant(value.coverage.oldestCheckedAt)}.`}</p>
    {value.records.length ? value.records.map(item => <article key={item.id}><div><strong>{day(item.startDate)} → {day(item.endDate)}</strong><span className={`absence-status is-${item.status}`}>{recordLabels[item.status]}</span></div><p>{item.reasonCode ? `${item.reasonCode} · ` : ""}{item.reason || "Motivo não informado"}</p>{item.sourceMissing && <small>Não apareceu na última resposta. Histórico preservado para conferência.</small>}</article>) : <p className="registry-help-text">{value.coverage.unknown ? "Os dados ainda não permitem confirmar a ausência de afastamentos." : "Nenhum afastamento retornado na última consulta confirmada."}</p>}
  </section>;
}

export default function AbsencesPage() {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const sync = () => { setQuery(window.location.search.slice(1)); setDraft(new URLSearchParams(window.location.search).get("q") || ""); };
    sync(); window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  useEffect(() => {
    const controller = new AbortController(); let timer;
    setLoading(true);
    async function read() {
      try {
        const response = await fetch(`/api/ui/absences?${query}`, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Não foi possível carregar os afastamentos.");
        if (!controller.signal.aborted) { setData(result); setError(""); }
      } catch (reason) { if (!controller.signal.aborted) setError(reason.name === "TimeoutError" ? "O serviço demorou para responder. Tente novamente." : reason.message); }
      finally { if (!controller.signal.aborted) { setLoading(false); timer = window.setTimeout(read, document.hidden ? 45000 : 15000); } }
    }
    timer = window.setTimeout(read, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, reload]);
  function navigate(changes) {
    const params = new URLSearchParams(query);
    for (const [key, value] of Object.entries(changes)) { if (value === "" || value == null) params.delete(key); else params.set(key, String(value)); }
    const next = params.toString();
    window.history.pushState(null, "", `/central-dp/afastamentos${next ? `?${next}` : ""}`); setQuery(next);
  }
  async function sync(action) {
    if (busy || !data?.csrf) return;
    setBusy(true); setNotice("");
    try {
      const response = await fetch("/api/integrations/sib/absences/sync", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, csrf: data.csrf }), signal: AbortSignal.timeout(15000) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível atualizar o monitoramento.");
      setNotice(result.message); setReload(value => value + 1);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  const params = new URLSearchParams(query), summary = data?.summary;
  const selected = params.get("status") || "all";
  const state = !summary ? "Verificando" : !summary.enabled ? "Pausado" : summary.waiting ? "Aguardando a consulta de cadastros" : summary.running ? "Consultando afastamentos" : summary.error ? "Aguardando nova tentativa" : "Monitoramento ativo";
  const changed = message => { setNotice(message || ""); setError(""); setReload(value => value + 1); };
  return <section className="page-content absences-page">
    <div className="absence-intro"><div><p>Consulte períodos, organize providências e acompanhe o retorno ao trabalho.</p><small>Informações e ações conforme seu perfil de acesso</small></div><button type="button" className="btn btn-outline-primary" disabled={loading} onClick={() => setReload(value => value + 1)}>Atualizar tela</button></div>
    {error && <div className="absence-alert" role="alert">{error} <a href="/entrar">Conferir acesso</a></div>}
    {notice && <div className="absence-notice" role="status">{notice}</div>}
    <section className="absence-sync"><div><span className={`absence-dot ${summary?.enabled ? "is-on" : ""}`} /><div><strong>{state}</strong><p>{summary ? `${num(summary.confirmed)} de ${num(summary.total)} matrículas com retorno e identidade confirmados` : "Carregando cobertura da sincronização…"}</p><small>{summary?.lastCheckedAt ? `Último lote: ${instant(summary.lastCheckedAt)}` : "A primeira carga respeita a prioridade da importação geral."}</small></div></div>
      {data?.canManageSync && <div className="absence-sync-actions"><button className="btn btn-outline-primary" type="button" disabled={!data || busy} onClick={() => sync(summary?.enabled ? "pause" : "start")}>{summary?.enabled ? "Pausar" : "Ativar monitoramento"}</button><button className="btn btn-primary" type="button" disabled={!data || busy} onClick={() => sync("refresh")}>{busy ? "Salvando…" : "Solicitar atualização"}</button></div>}
      <details><summary>Como a atualização funciona</summary><p>Ativos e afastamentos em aberto voltam à fila após 2 minutos; vínculos encerrados, após 24 horas. A latência depende do tamanho da fila e do tempo da Bridge. Lotes de até {summary?.batchSize || 50} matrículas têm progresso salvo e retomam após interrupções.</p><p>A busca geral de cadastros tem prioridade. Novos vínculos entram automaticamente na fila. Falhas e identidades ambíguas não são interpretadas como ausência de afastamentos.</p>{summary && <p>{num(summary.pending)} pendentes · {num(summary.errors)} com erro de consulta · {num(summary.ambiguous)} para conferir</p>}{summary?.error && <p>{summary.error}</p>}</details>
    </section>
    {data?.followupSummary && <div className="absence-followup-metrics" aria-label="Providências dos afastamentos">{[["pending", "Providências em aberto", data.followupSummary.pending], ["overdue", "Prazos atrasados", data.followupSummary.overdue], ["critical", "Afastamentos críticos", data.followupSummary.critical]].map(([key, label, value]) => <button key={key} type="button" className={`is-${key}`} aria-pressed={params.get("followup") === key} onClick={() => navigate({ followup: params.get("followup") === key ? "" : key, status: "all", page: 1, id: "" })}><span>{label}</span><strong>{num(value)}</strong><small>Ver afastamentos →</small></button>)}</div>}
    <AbsenceSettings key={JSON.stringify(data?.followupSettings)} policy={data?.followupSettings} csrf={data?.csrf} onChanged={changed} onError={setError}/>
    {params.get("followup") && <div className="absence-active-filter">Filtrando afastamentos com {params.get("followup") === "overdue" ? "providências atrasadas" : params.get("followup") === "critical" ? "atenção crítica" : "providências em aberto"}<button type="button" onClick={() => navigate({ followup: "", page: 1 })}>Limpar recorte ×</button></div>}
    {data?.issueTotal > 0 && <details className="absence-issues"><summary>Consultas para conferir <span>{num(data.issueTotal)}</span></summary><div>{data.issues.map(item => <article key={item.registration}><strong>Matrícula {item.registration} · {item.status === "ERROR" ? "Falha de consulta" : "Identidade ou retorno não confirmado"}</strong><p>{item.error}</p><small>Próxima tentativa a partir de {instant(item.retryAt)}</small><a href={`/cadastros/pessoas?q=${encodeURIComponent(item.registration)}`}>Conferir cadastros →</a></article>)}{data.issueTotal > 10 && <nav aria-label="Páginas de consultas para conferir"><button type="button" disabled={loading || data.issuePage <= 1} onClick={() => navigate({ issuePage: data.issuePage - 1 })}>Anterior</button><span>{data.issuePage} de {Math.ceil(data.issueTotal / 10)}</span><button type="button" disabled={loading || data.issuePage * 10 >= data.issueTotal} onClick={() => navigate({ issuePage: data.issuePage + 1 })}>Próxima</button></nav>}</div></details>}
    <section className="absence-workspace"><form className="absence-filters" onSubmit={event => { event.preventDefault(); navigate({ q: draft.trim(), page: 1, id: "" }); }}><label>Buscar matrícula, pessoa, empresa ou motivo<div><input value={draft} onChange={event => setDraft(event.target.value)} placeholder="Busque nos afastamentos já recebidos" maxLength={160}/><button className="btn btn-primary" disabled={loading}>Pesquisar</button></div></label><label>Data de corte<input type="date" value={params.get("cutoff") || data?.cutoff || ""} onChange={event => navigate({ cutoff: event.target.value, page: 1, id: "" })}/></label></form>
      <div className="absence-tabs" aria-label="Situação do afastamento">{Object.entries(labels).map(([key, label]) => <button key={key} type="button" aria-pressed={key === selected} disabled={loading} onClick={() => navigate({ status: key, page: 1, id: "" })}>{label}<span>{data ? num(data.counts[key]) : "—"}</span></button>)}</div>
      <div className="absence-results-head"><span>{loading ? "Carregando…" : `${num(data?.total)} ${data?.total === 1 ? "afastamento" : "afastamentos"} na seleção`}</span><small>Contagens dos registros recebidos · cobertura pode ser parcial</small></div>
      <div className={`absence-results${loading ? " is-loading" : ""}`} aria-busy={loading}>
        {data?.records.map(item => <article className="absence-record" key={item.id}><button type="button" className="absence-record-summary" disabled={loading} aria-expanded={data.detail?.id === item.id} onClick={() => navigate({ id: data.detail?.id === item.id ? "" : item.id })}><span className="absence-person"><small>Matrícula {item.registration}{item.company ? ` · ${item.company}` : item.companyCode ? ` · empresa ${item.companyCode}` : ""}</small><strong>{item.name}</strong><span>{item.reasonCode ? `${item.reasonCode} · ` : ""}{item.reason || "Motivo não informado"}</span>{item.endDateFromDismissal && <small>Fim considerado pela data de desligamento do vínculo</small>}{item.pendingAlerts > 0 && <span className={`absence-row-attention is-${item.criticality?.toLowerCase()}`}>{item.criticalityLabel} · {item.pendingAlerts} providência(s){item.overdueAlerts > 0 ? ` · ${item.overdueAlerts} atrasada(s)` : item.nextAlert ? ` · próximo prazo ${day(item.nextAlert)}` : ""}</span>}</span><span className="absence-dates"><strong>{day(item.startDate)}</strong><span>até {day(item.endDate)}</span></span><span className={`absence-status is-${item.status}`}>{recordLabels[item.status]}</span><span className="absence-chevron" aria-hidden="true">{data.detail?.id === item.id ? "−" : "+"}</span></button>
          {data.detail?.id === item.id && <div className="absence-record-detail"><div><p>Última consulta: {instant(item.checkedAt)} · sequência {data.detail.sequence}</p>{item.employmentKey ? <a href={`/cadastros/pessoas?id=${encodeURIComponent(item.employmentKey)}`}>Abrir ficha do vínculo →</a> : <p>O retorno não identifica um vínculo único. Nenhuma pessoa foi associada por suposição.</p>}{item.sourceMissing && <p>Não apareceu na última resposta do SIB. O histórico permanece salvo; não foi criada uma data de encerramento.</p>}</div><details><summary>Histórico de alterações ({data.detail.history.length}{data.detail.history.length === 20 ? "+" : ""})</summary>{data.detail.history.map((event, index) => { const before = event.previous_json ? JSON.parse(event.previous_json) : null; const after = JSON.parse(event.current_json); return <div className="absence-history" key={index}><strong>{instant(event.changed_at)} · {before ? "Alteração recebida" : "Primeira importação"}</strong>{[["Início", "startDate"], ["Fim", "endDate"], ["Motivo", "reason"], ["Vínculo", "employmentKey"], ["Ausente na origem", "sourceMissing"]].filter(([, key]) => !before || before[key] !== after[key]).map(([label, key]) => <p key={key}>{label}: {before ? `${historyValue(before[key], key)} → ` : ""}{historyValue(after[key], key)}</p>)}</div>; })}</details><AbsenceFollowup key={`${data.detail.id}-${data.detail.contextToken}`} detail={data.detail} data={data} onChanged={changed} onError={setError}/></div>}
        </article>)}
        {!loading && !data?.records.length && <div className="absence-empty"><span aria-hidden="true">✓</span><h2>{summary?.pending || summary?.waiting || !summary?.confirmed ? "Aguardando dados confirmados" : "Nenhum afastamento nesta seleção"}</h2><p>{summary?.pending || summary?.waiting || !summary?.confirmed ? "A carga inicial e as consultas pendentes aparecerão aqui conforme forem concluídas." : "Ajuste a busca, a situação ou a data de corte para consultar outros períodos."}</p></div>}
      </div>
      {data && data.total > 25 && <nav className="absence-pagination" aria-label="Páginas de afastamentos"><button type="button" className="btn btn-outline-primary" disabled={loading || data.page <= 1} onClick={() => navigate({ page: data.page - 1, id: "" })}>Anterior</button><span>Página {data.page} de {Math.ceil(data.total/25)}</span><button type="button" className="btn btn-outline-primary" disabled={loading || data.page * 25 >= data.total} onClick={() => navigate({ page: data.page + 1, id: "" })}>Próxima</button></nav>}
    </section>
    <p className="absence-footnote">Consulta somente leitura no SIB. Registros sem identidade confirmada ou ausentes na última resposta ficam em “Conferir” e não entram nos indicadores de afastados.</p>
  </section>;
}
