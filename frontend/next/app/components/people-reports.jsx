"use client";

import { useEffect, useRef, useState } from "react";

const number = value => value == null ? "—" : typeof value === "number" ? value.toLocaleString("pt-BR", { maximumFractionDigits: 2 }) : String(value);
const day = value => value?.split("-").reverse().join("/") || "—";
const norm = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const chartReports = new Set(["departments", "jobs", "sex", "age", "salary", "tenure", "contracts"]);

export function ReportFilters({ data, draft, loading, dirty, onUpdate, onChangeDate, onApply, onReset, CompanySelect }) {
  const report = data.reports?.selected;
  const options = report?.columnOptions || [];
  const selected = draft.reportColumnsSet === "1" ? draft.reportColumns || [] : options.filter(item => item.default).map(item => item.key);
  const groups = [...new Set(options.map(item => item.group))];
  const setColumns = keys => { onUpdate("reportColumns", keys); onUpdate("reportColumnsSet", "1"); };
  const empty = options.length > 0 && selected.length === 0;
  return <section className="pa-card pa-filter-card pr-simple-filters" aria-labelledby="pa-filter-title">
    <div className="pa-card-heading"><div><span className="pa-eyebrow">ETAPA 2 · PERÍODO E EMPRESAS</span><h2 id="pa-filter-title">Filtros do relatório</h2></div><span className="pa-tag">{dirty ? "Alterações por aplicar" : "Seleção aplicada"}</span></div>
    <form onSubmit={event => { event.preventDefault(); if (!empty) onApply(); }}><fieldset disabled={loading}>
      <div className="pr-date-filters"><label>Data inicial<input type="date" value={draft.start || ""} min="1900-01-01" max={draft.end || data.meta.today} name="start" required onInput={event => onChangeDate("start", event)} onChange={event => onChangeDate("start", event)} onBlur={event => onChangeDate("start", event)} /></label><label>Data de corte<input type="date" value={draft.end || ""} min={draft.start || "1900-01-01"} max={data.meta.today} name="end" required onInput={event => onChangeDate("end", event)} onChange={event => onChangeDate("end", event)} onBlur={event => onChangeDate("end", event)} /></label></div>
      <div className="pr-enterprise-filters"><CompanySelect label="Incluir somente estas empresas" options={data.options.companies} values={draft.companies} onChange={value => onUpdate("companies", value)} help="Sem seleção, entram todas as empresas disponíveis." /><CompanySelect label="Retirar empresas da análise" options={data.options.companies} values={draft.excludeCompanies} onChange={value => onUpdate("excludeCompanies", value)} emptyLabel="Nenhuma exclusão" help="As exclusões prevalecem sobre as empresas incluídas." /></div>
      {options.length > 0 && <section className="pr-column-picker" id="pr-column-picker" aria-labelledby="pr-column-title"><div className="pr-column-heading"><div><span className="pa-eyebrow">ETAPA 3 · CAMPOS DO EXCEL</span><h3 id="pr-column-title">Colunas de {report.title}</h3><p>Marque os dados que devem virar colunas. A prévia usará a mesma seleção.</p></div><strong>{selected.length} de {options.length} selecionadas</strong></div><div className="pr-column-tools"><button type="button" onClick={() => setColumns(options.map(item => item.key))}>Marcar todas</button><button type="button" onClick={() => setColumns([])}>Desmarcar todas</button><button type="button" onClick={() => setColumns(options.filter(item => item.default).map(item => item.key))}>Restaurar padrão</button></div><div className="pr-column-groups">{groups.map(group => <fieldset key={group} className="pr-column-group"><legend>{group}</legend><div>{options.filter(item => item.group === group).map(item => <label key={item.key} className={`pr-column-choice ${selected.includes(item.key) ? "is-checked" : ""}`}><input type="checkbox" name="reportColumns" value={item.key} checked={selected.includes(item.key)} onChange={event => setColumns(event.target.checked ? [...selected, item.key] : selected.filter(key => key !== item.key))} /><span>{item.label}{item.administrative && <small>Administrativo</small>}</span></label>)}</div></fieldset>)}</div><p className="pa-help">{report.id === "absences" ? "Cada afastamento confirmado ocupa uma linha. Uma matrícula pode ter mais de um período." : "Campos que não constam no cadastro aparecem como “Não informado”. Cada matrícula continua em uma linha, mesmo que você oculte essa coluna."}</p>{empty && <p className="pr-export-warning" role="status">Marque pelo menos uma coluna para aplicar e emitir o relatório.</p>}</section>}
      {!report && <p className="pa-help">Etapa 1: escolha um relatório no catálogo. As colunas disponíveis aparecem aqui automaticamente.</p>}
      <div className="pr-filter-actions"><button type="submit" className="btn btn-primary" disabled={empty}>Aplicar filtros e colunas</button><button type="button" className="btn btn-outline-primary" onClick={onReset}>Limpar filtros</button><p>{dirty ? "Aplique as alterações antes de exportar." : "A prévia e o Excel usam a seleção aplicada."}</p></div>
    </fieldset></form>
  </section>;
}

export function ReportCatalog({ data, loading, onSelect }) {
  const [search, setSearch] = useState("");
  const reports = data.reports?.catalog || [];
  const groups = ["Quadro", "Movimentações", "Indicadores"];
  const visible = reports.filter(report => norm(`${report.title} ${report.description} ${report.group}`).includes(norm(search)));
  return <section className="pr-catalog" aria-labelledby="pr-catalog-title" id="pr-catalog">
    <div className="pr-catalog-heading"><div><span className="pa-eyebrow">ETAPA 1 · ESCOLHA O RELATÓRIO</span><h2 id="pr-catalog-title">Central de relatórios</h2><p>Escolha uma opção. A tela abrirá os campos disponíveis para montar a planilha Excel.</p></div><label>Encontrar relatório<input type="search" placeholder="Nome ou assunto…" value={search} onChange={event => setSearch(event.target.value)} /></label></div>
    <div className="pr-catalog-grid">{groups.map(group => <section key={group} className="pr-category"><h3>{group}<span>{visible.filter(report => report.group === group).length}</span></h3><div>{visible.filter(report => report.group === group).map(report => <button type="button" key={report.id} className={`pr-report-link is-${report.state} ${data.reports.selected?.id === report.id ? "is-selected" : ""}`} onClick={() => onSelect(report.id)} disabled={loading} aria-pressed={data.reports.selected?.id === report.id}><strong>{report.title}</strong><span>{report.description}</span><small>{report.administrative ? "Acesso administrativo · " : ""}{report.caption}</small></button>)}</div></section>)}</div>
    {!visible.length && <p className="pa-empty">Nenhum relatório corresponde à busca.</p>}
  </section>;
}

export function ReportResult({ data, loading, pendingFilters, onRefine, onSelect }) {
  const report = data.reports?.selected;
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [exportError, setExportError] = useState("");
  const [download, setDownload] = useState(null);
  const exportController = useRef(null);
  const selectionKey = JSON.stringify(data.filters);
  useEffect(() => {
    exportController.current?.abort(); setExporting(false); setExportMessage(""); setExportError(""); setDownload(null);
    return () => exportController.current?.abort();
  }, [selectionKey]);
  if (!report) return null;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(data.filters)) {
    if (key === "effectiveGranularity") continue;
    if (Array.isArray(value)) { if (key === "sources" && !value.length) params.append(key, ""); else value.forEach(item => params.append(key, item)); }
    else if (value !== "" && value != null && value !== false) params.set(key, value === true ? "1" : String(value));
  }
  async function exportExcel() {
    if (exporting || loading || pendingFilters) return;
    const controller = new AbortController(); exportController.current = controller;
    setExporting(true); setDownload(null); setExportError(""); setExportMessage(`Preparando Excel: ${report.title}…`);
    try {
      const response = await fetch("/api/ui/people-reports/export", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.reports.csrf, query: params.toString() }), signal: controller.signal });
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || "Não foi possível iniciar a exportação.");
      const deadline = Date.now() + 240000;
      while (Date.now() < deadline) {
        const statusResponse = await fetch(`/api/ui/people-reports/export/${job.id}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const status = await statusResponse.json();
        if (!statusResponse.ok || status.state === "error") throw new Error(status.error || "Não foi possível concluir a exportação.");
        if (status.state === "ready") {
          const href = `/api/ui/people-reports/export/${job.id}/download`;
          setDownload({ href, filename: status.filename }); setExportMessage(`Excel pronto com ${number(status.rows)} ${report.unit}. O download foi iniciado.`);
          const anchor = document.createElement("a"); anchor.href = href; anchor.download = status.filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
          return;
        }
        await new Promise((resolve,reject) => { const stop=()=>{clearTimeout(timer);reject(controller.signal.reason);}; const timer=setTimeout(()=>{controller.signal.removeEventListener("abort",stop);resolve();},900);controller.signal.addEventListener("abort",stop,{once:true});if(controller.signal.aborted)stop(); });
      }
      throw new Error("A geração está demorando. Emita novamente para conferir a exportação em andamento.");
    } catch (error) { if (!controller.signal.aborted) { setExportError(error.message); setExportMessage(""); } }
    finally { if (exportController.current === controller && !controller.signal.aborted) setExporting(false); }
  }
  const chart = chartReports.has(report.id) && report.chartReady && report.rows.length > 0;
  const maximum = Math.max(1, ...report.rows.map(row => Number(row[1]) || 0));
  return <section className="pa-card pr-result" id="pa-report-result" aria-labelledby="pr-result-title">
    <header className="pr-result-heading"><div><span className="pa-eyebrow">RELATÓRIO · {report.group}</span><h2 id="pr-result-title">{report.title}</h2><p>Período: {day(data.filters.start)} a {day(data.filters.end)} · corte em {day(data.filters.end)}</p><small>Gerado em {new Date(data.meta.generatedAt).toLocaleString("pt-BR")} · base local da V3</small></div><div className="pr-result-actions"><button type="button" className="btn btn-outline-primary" onClick={() => { onSelect(""); document.getElementById("pr-catalog")?.scrollIntoView({ behavior: "smooth" }); }} disabled={loading}>Voltar ao catálogo</button>{!report.pending && <button type="button" className="btn btn-primary" disabled={loading || exporting || pendingFilters} onClick={exportExcel}>{exporting ? "Preparando Excel…" : "Exportar Excel (.xlsx)"}</button>}</div></header>
    <div className="pr-applied-summary"><p><strong>Empresas:</strong> {data.filters.companies.length ? data.options.companies.filter(item => data.filters.companies.includes(item.value)).map(item => item.label).join("; ") : "Todas as disponíveis"}{data.filters.excludeCompanies.length > 0 && ` · ${data.filters.excludeCompanies.length} excluída(s)`}<br /><strong>Colunas:</strong> {report.columns.length} selecionada(s)</p><div><a href="#pa-filter-title">Alterar filtros</a><a href="#pr-column-title">Alterar colunas</a></div></div>
    {pendingFilters && <p className="pr-export-warning" role="status">Há filtros ou colunas alterados. Clique em Aplicar filtros e colunas antes de emitir o Excel.</p>}
    {(exportMessage || exportError) && <div className={`pr-export-status ${exportError ? "is-error" : ""}`} role={exportError ? "alert" : "status"}><span>{exportError || exportMessage}</span>{download && <a href={download.href} download={download.filename}>Baixar Excel novamente</a>}</div>}
    {report.hasPopulation && <div className="pr-population"><label>População do relatório<select aria-label="População do relatório" value={report.population} disabled={loading} onChange={event => onRefine({ reportPopulation: event.target.value, reportPage: 1 })}><option value="active">Ativos na data de corte</option><option value="period">Vínculos com vigência no período</option><option value="all">Cadastros até a data de corte</option></select></label><p>{report.populationLabel} · {number(report.populationSize)} vínculo(s) antes do agrupamento.</p></div>}
    {report.pending ? <div className="pr-pending" role="status"><strong>{report.caption}</strong><p>{report.pending}</p><a href={report.state === "restricted" ? "/entrar" : "/central-dp/integracoes"}>{report.state === "restricted" ? "Entrar com uma conta autorizada" : "Abrir integração SIB"} →</a></div> : <>
      <div className="pr-result-summary"><strong>{number(report.total)} <span>{report.unit}</span></strong><p>{data.meta.scanRunning ? "Base parcial: a importação SIB está em andamento." : "Resultados limitados aos cadastros disponíveis e aos filtros aplicados."}</p></div>
      {chart && <div className="pr-distribution" aria-label="Distribuição do relatório">{report.rows.slice(0, 12).map((row, index) => <div key={index}><span>{row[0]}</span><i><b style={{ width: `${(Number(row[1]) || 0) / maximum * 100}%` }} /></i><strong>{number(row[1])}</strong><small>{number(row[2])}%</small></div>)}{report.total > 12 && <p>As primeiras 12 categorias desta página. A tabela contém todas as categorias, com paginação.</p>}</div>}
      <div className="pa-table-wrap"><table className="pr-table"><caption className="visually-hidden">{report.title}</caption><thead><tr>{report.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead><tbody>{report.rows.map((row, index) => <tr key={index}>{row.map((value, cell) => cell === 0 ? <th key={cell} scope="row">{number(value)}</th> : <td key={cell}>{number(value)}</td>)}</tr>)}</tbody></table></div>
      {!report.total && <div className="pa-empty"><strong>{report.id === "history" ? "Nenhuma revisão local auditada neste período e seleção." : "Nenhum registro atende aos filtros deste relatório."}</strong><p>Confira a cobertura da base, o período e as empresas selecionadas.</p></div>}
      {report.total > report.pageSize && <nav className="pa-pagination" aria-label="Páginas do relatório"><button type="button" className="btn btn-outline-primary" disabled={loading || report.page <= 1} onClick={() => onRefine({ reportPage: report.page - 1 })}>Anterior</button><span>Página {report.page} de {Math.ceil(report.total / report.pageSize)} · {report.pageSize} linhas por página</span><button type="button" className="btn btn-outline-primary" disabled={loading || report.page * report.pageSize >= report.total} onClick={() => onRefine({ reportPage: report.page + 1 })}>Próxima</button></nav>}
    </>}
    <aside className="pr-notes"><h3>Critérios e cobertura</h3><ul>{report.notes.map(note => <li key={note}>{note}</li>)}<li>A base representa os registros já recebidos pela V3. Ausência de cadastro ou informação não comprova ausência no SIB.</li><li>Este relatório consulta apenas a base local e não grava nem modifica dados no Bitti/SIB.</li></ul></aside>
  </section>;
}
