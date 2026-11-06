"use client";

import { useState } from "react";

export default function RescissionDeliveryHistory({ detail, csrf, canManage, onUpdated }) {
  const [editing, setEditing] = useState(null), [reference, setReference] = useState("");
  const [outcome, setOutcome] = useState("ENVIADO"), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function save(event, row) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/ui/rescissions/delivery-actions", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, caseId: detail.id, deliveryId: row.id, action: row.status === "ENVIANDO" ? "resolve_uncertain" : "confirm_receipt", reference, outcome }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || "Não foi possível registrar a conferência.");
      setEditing(null); setReference(""); await onUpdated();
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }
  return <section className="rescissions-history"><h3>Envio e recebimento</h3>
    <p>O aceite do canal registra o envio. A confirmação de recebimento exige uma referência do retorno do destinatário.</p>
    {error && <p className="rescissions-alert" role="alert">{error}</p>}
    {detail.deliveries.length ? <div className="rescissions-deliveries">{detail.deliveries.map(row => {
      const channel = row.channel === "WHATSAPP" ? "WhatsApp · ZPRO" : "E-mail";
      const status = row.is_simulated ? "Simulado" : row.receipt_at ? "Recebimento confirmado" : row.status === "ENVIADO" ? "Envio aceito pelo canal" : row.status === "ENVIANDO" ? row.finished_at ? "Resultado incerto" : "Em envio" : "Falha no envio";
      return <article key={row.id}><strong>{channel} · {status}</strong><span>{row.created_at}</span><small>Protocolo {row.protocol} · {row.documents?.join(", ") || "Sem anexos"}</small>
        {row.error_message && <small>{row.error_message}</small>}{row.receipt_at && <small>{row.receipt_at} · {row.receipt_reference}</small>}
        {canManage && !row.is_simulated && !row.receipt_at && ["ENVIADO", "ENVIANDO"].includes(row.status) && <button className="btn btn-outline-primary btn-sm" type="button" disabled={busy} onClick={() => { setEditing(editing === row.id ? null : row.id); setReference(""); setError(""); }}>{row.status === "ENVIADO" ? "Registrar recebimento" : "Conferir resultado no canal"}</button>}
        {editing === row.id && <form onSubmit={event => save(event, row)} className="rescissions-receipt-form">{row.status === "ENVIANDO" && <label>Resultado conferido<select value={outcome} onChange={event => setOutcome(event.target.value)}><option value="ENVIADO">O canal confirmou o envio</option><option value="FALHOU">O canal confirmou que não enviou</option></select><small>Aguarde pelo menos 3 minutos após o início antes de resolver uma tentativa sem resposta.</small></label>}<label>Referência da confirmação<textarea value={reference} onChange={event => setReference(event.target.value)} minLength={8} maxLength={500} placeholder="Ex.: resposta do funcionário, data e referência no atendimento" required/></label><button className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : "Salvar conferência"}</button></form>}
      </article>;
    })}</div> : <p>Nenhuma entrega registrada.</p>}
  </section>;
}
