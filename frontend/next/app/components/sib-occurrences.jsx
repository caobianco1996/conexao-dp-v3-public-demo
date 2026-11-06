"use client";

import { useEffect, useState } from "react";
import { SyncIcon } from "./integrations-panels";

const LABELS = { MISSING: "Em confirmação", EXCLUDED: "Excluídos no SIB", VACANT: "Números vagos", DIVERGENT: "Identidades divergentes", AWAITING_FICHE: "Aguardando ficha", HISTORY: "Histórico" };
const EVENTS = { MISSING: "Deixou de aparecer", EXCLUDED: "Exclusão confirmada", REAPPEARED: "Cadastro voltou ao SIB", VACANCY_FILLED: "Número vago preenchido", CANCELLED_BEFORE_START: "Cancelamento antes do início confirmado", DIVERGENT: "Identidade divergente" };
const date = value => value ? new Date(value * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
function registration(key) {
  const [employer, branch, number] = key.split(":");
  return `${branch.padStart(2, "0")}-${number.padStart(6, "0")} · empresa ${employer}`;
}

export default function SibOccurrences({ summary, csrf, compact = false }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("MISSING");
  const [page, setPage] = useState(1);
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!open && summary) return;
    let cancelled = false, timer;
    const controller = new AbortController();
    async function read() {
      setLoading(true);
      try {
        const response = await fetch(`/api/integrations/sib/occurrences?${new URLSearchParams({ status, page: String(page), q: query })}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const result = await response.json().catch(() => null);
        if (!response.ok || !Array.isArray(result?.items)) throw new Error(result?.error || "Não foi possível carregar as ocorrências. A tela tentará novamente em instantes.");
        if (!cancelled) { setData(result); setError(""); }
      } catch (reason) {
        if (!cancelled && reason.name !== "AbortError") setError(reason.message);
      } finally { if (!cancelled) setLoading(false); }
      if (!cancelled && open) timer = setTimeout(read, 20000);
    }
    setData(null);
    read();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [open, status, page, query, revision, Boolean(summary)]);

  async function cancelContract() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/integrations/sib/occurrences", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data?.csrf || csrf, action: "confirm_cancellation", sourceId: confirm }) });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result) throw new Error(result?.error || "Não foi possível confirmar o cancelamento. Tente novamente.");
      setConfirm(""); setAccepted(false); setRevision(value => value + 1); setMessage(result.message);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }

  const current = open && data?.summary ? data.summary : summary || data?.summary || {};
  return <section id="sib-occurrences" className={`integrations-occurrences${compact ? " is-compact" : ""}`}>
    <header>{compact && <span className="sync-panel-icon"><SyncIcon name="alert"/></span>}<div>{!compact && <span className="integrations-eyebrow">CONTROLE DAS MATRÍCULAS</span>}<h3>Ocorrências no SIB</h3><p>{compact && !current.lastCompletedAt ? current.ready ? "Aguardando a primeira conferência de identificadores." : "Vagas, exclusões e divergências · após a recuperação inicial." : "Histórico preservado para números vagos, exclusões e divergências."}</p></div><button type="button" className="btn btn-outline-primary" aria-expanded={open} aria-controls="sib-occurrences-body" onClick={() => setOpen(value => !value)}>{open ? "Fechar detalhes" : "Ver ocorrências"}</button></header>
    {(!compact || open || current.lastCompletedAt > 0) && <div className="integrations-occurrence-counts">{[["VACANT", current.vacant || 0], ["MISSING", current.missing || 0], ["EXCLUDED", current.excluded || 0], ["DIVERGENT", current.divergent || 0]].map(([key, count]) => <button type="button" key={key} onClick={() => { setStatus(key); setPage(1); setConfirm(""); setOpen(true); }}><strong>{current.lastCompletedAt || count ? Number(count).toLocaleString("pt-BR") : "—"}</strong><span>{LABELS[key]}</span></button>)}</div>}
    {(!compact || open) && (!current.ready ? <small>O inventário periódico começa após as três passagens e a conferência inicial do catálogo. As novas matrículas já têm prioridade.</small>
      : <small>Inventário de identificadores a cada 5 minutos após concluir a leitura. Última conclusão: {date(current.lastCompletedAt)}{current.running ? ` · ${current.checked.toLocaleString("pt-BR")} de ${Number(current.total || 0).toLocaleString("pt-BR")} identificadores` : ""}.</small>)}
    {current.error && <p className="integrations-alert is-warning" role="status">{current.error}</p>}
    {current.pending > 0 && <p><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => { setStatus("AWAITING_FICHE"); setPage(1); setOpen(true); }}>{current.pending.toLocaleString("pt-BR")} identidade(s) aguardando ficha completa</button></p>}
    {open && <div id="sib-occurrences-body" className="integrations-occurrences-body">
      <form onSubmit={event => { event.preventDefault(); setQuery(input.trim()); setPage(1); }} className="integrations-occurrence-filters">
        <label>Situação<select value={status} onChange={event => { setStatus(event.target.value); setPage(1); setConfirm(""); }}>{Object.entries(LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Nome ou matrícula<input value={input} onChange={event => setInput(event.target.value)} maxLength={120} placeholder="Ex.: 404 ou nome da pessoa"/></label><button className="btn btn-primary" type="submit">Pesquisar</button>
      </form>
      <p className="integrations-occurrence-help">{status === "VACANT" ? "Números sem cadastro conhecido dentro das sequências conferidas. Números acima do último cadastro confirmado não são considerados vagas. A lista de identificadores detecta se forem preenchidos depois."
        : status === "MISSING" ? "Ausência detectada no catálogo completo. Confirmações individuais por empresa, filial, matrícula e vínculo após 5, 15 e 60 minutos. Falhas de conexão não contam como ausência."
          : status === "EXCLUDED" ? "Exclusões confirmadas sem apagar a ficha. Informe cancelamento antes do início somente quando vocês tiverem confirmado esse motivo; o contrato sairá dos indicadores e filas operacionais."
            : status === "DIVERGENT" ? "A identificação passou a retornar outro vínculo ou outra pessoa. A ficha anterior foi preservada para revisão."
              : status === "AWAITING_FICHE" ? "Identificadores encontrados no catálogo que aguardam leitura ou confirmação da ficha completa. Falhas ficam registradas com a próxima tentativa."
                : "Registro das exclusões, reaparecimentos, vagas preenchidas e confirmações de cancelamento."}</p>
      {error && <p className="integrations-alert is-error" role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {loading && !data ? <p role="status">Carregando ocorrências…</p> : data?.items.length ? <>
        <div className="integrations-occurrence-list">{data.items.map((item, index) => <article key={`${item.source_id}-${index}`}>
          <div><strong>{item.name || (status === "VACANT" ? item.quantity > 1 ? `${item.quantity} números sem cadastro confirmado` : "Número sem cadastro confirmado" : "Cadastro SIB")}</strong><small>{registration(item.source_id)}{item.quantity > 1 ? ` até ${String(item.registration_end).padStart(6, "0")}` : ""}{item.modality ? ` · ${item.modality}` : ""}</small>
            <span>{item.cancellation ? "Contrato cancelado antes do início" : EVENTS[item.status] || LABELS[item.status] || item.status}</span>
            <small>{status === "VACANT" ? "Conferido" : status === "HISTORY" ? "Registrado" : "Última confirmação no SIB"}: {date(status === "HISTORY" ? item.changed_at : item.last_seen)}{item.next_check > 0 ? ` · próxima tentativa: ${date(item.next_check)}` : ""}{status === "MISSING" ? ` · ${item.checks}/3 confirmações` : ""}</small>
            {item.error && <small className="integrations-occurrence-error">{item.error}</small>}
          </div>
          <div className="integrations-inline-actions">{status !== "VACANT" && item.name && <a className="btn btn-outline-primary btn-sm" href={`/cadastros/pessoas?id=${encodeURIComponent("sib:" + item.source_id)}`}>Abrir ficha</a>}
            {status === "EXCLUDED" && !item.cancellation && <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => { setConfirm(item.source_id); setAccepted(false); setMessage(""); }}>Informar cancelamento</button>}</div>
          {confirm === item.source_id && <div className="integrations-occurrence-confirm"><label><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)}/> Confirmo que este contrato foi cancelado antes do início das atividades.</label><p>A ficha e o histórico serão preservados. Esta confirmação retira o contrato dos indicadores e das filas operacionais.</p><div className="integrations-inline-actions"><button type="button" className="btn btn-primary" disabled={!accepted || busy} onClick={cancelContract}>{busy ? "Salvando…" : "Confirmar cancelamento"}</button><button type="button" className="btn btn-outline-primary" disabled={busy} onClick={() => setConfirm("")}>Voltar</button></div></div>}
        </article>)}</div>
        <footer className="integrations-history-pagination"><span>{Number(data.total).toLocaleString("pt-BR")} {status === "VACANT" ? "faixa(s)" : "ocorrência(s)"} · página {data.page} de {data.pageCount}</span><div><button className="btn btn-outline-primary btn-sm" type="button" disabled={data.page <= 1 || loading} onClick={() => setPage(data.page - 1)}>Anterior</button><button className="btn btn-outline-primary btn-sm" type="button" disabled={data.page >= data.pageCount || loading} onClick={() => setPage(data.page + 1)}>Próxima</button></div></footer>
      </> : !loading && <p className="integrations-occurrence-empty">{!current.lastCompletedAt ? "As ocorrências serão classificadas após a primeira conferência completa dos identificadores." : "Nenhuma ocorrência nesta seleção."}</p>}
    </div>}
  </section>;
}
