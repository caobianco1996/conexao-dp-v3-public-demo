"use client";

import { useState } from "react";

const day = value => value ? value.split("-").reverse().join("/") : "Não informado";
const levelNames = { NORMAL: "Normal", ATTENTION: "Atenção", HIGH: "Importante", CRITICAL: "Crítico" };
const statusNames = { PENDING: "Pendente", POSTPONED: "Prorrogada", DONE: "Concluída", CANCELLED: "Cancelada" };
const deliveryNames = { SENDING: "Envio em andamento", SENT: "Aceito pelo SMTP", FAILED: "Não enviado", UNCERTAIN: "Resultado a conferir" };
const historyNames = {
  AUTO_ALERT_CREATED: "Providência automática criada", AUTO_ALERT_UPDATED: "Marco automático atualizado", ALERT_CREATED: "Providência registrada", COMPLETE_ALERT: "Providência concluída",
  POSTPONE_ALERT: "Prazo prorrogado", REOPEN_ALERT: "Providência reaberta", CANCEL_ALERT: "Providência cancelada", SAVE_ALERT: "Providência atualizada",
  DOCUMENT_LINKED: "Documento vinculado", EMAIL_STARTED: "Envio iniciado", EMAIL_SENT: "E-mail aceito pelo SMTP",
  EMAIL_FAILED: "Falha no envio", EMAIL_UNCERTAIN: "Resultado do envio a conferir", RECEIPT_CONFIRMED: "Recebimento confirmado", DELIVERY_RESOLVED: "Resultado do envio conferido",
};

function LevelSelect({ name = "level", value = "ATTENTION" }) {
  return <select name={name} defaultValue={value}>{Object.entries(levelNames).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select>;
}

function ResponsibleSelect({ users, value = "" }) {
  return <select name="responsibleId" defaultValue={value ?? ""}><option value="">Sem responsável</option>{users.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}</select>;
}

export function AbsenceSettings({ policy, csrf, onChanged, onError }) {
  const [busy, setBusy] = useState(false);
  if (!policy) return null;
  async function save(event) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const response = await fetch("/api/ui/absences/workflow", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "settings", csrf, enabled: form.get("enabled") === "on", alertDays: form.get("alertDays"),
          proximityDays: form.get("proximityDays"), prolongedDays: form.get("prolongedDays"),
          ...Object.fromEntries(["accidentLevel", "prolongedLevel", "recurrenceLevel", "retroactiveLevel"].map(key => [key, form.get(key)])) }),
        signal: AbortSignal.timeout(20000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível salvar as regras.");
      onChanged(result.message);
    } catch (error) { onError(error.message); }
    finally { setBusy(false); }
  }
  return <details className="absence-followup-settings"><summary>Regras de acompanhamento <span>Marcos internos e níveis de atenção</span></summary>
    <form onSubmit={save} className="absence-followup-form"><p className="absence-form-help">Estes marcos organizam a rotina do DP. Podem ser ajustados e não representam uma definição automática de prazo legal. Novas providências são geradas para afastamentos confirmados em andamento; o histórico já registrado é preservado.</p>
      <label className="absence-check"><input type="checkbox" name="enabled" defaultChecked={policy.enabled}/>Gerar providências automaticamente</label>
      <label>Marcos em dias<input name="alertDays" defaultValue={policy.alertDays.join(", ")} placeholder="Ex.: 15, 30, 45, 60, 90" maxLength={180}/><small>Deixe vazio para desativar os marcos por duração.</small></label>
      <label>Antecedência para acompanhar<input name="proximityDays" type="number" min="0" max="90" defaultValue={policy.proximityDays}/><small>Dias antes do marco.</small></label>
      <label>Afastamento prolongado após<input name="prolongedDays" type="number" min="0" max="3650" defaultValue={policy.prolongedDays}/><small>0 desativa este critério.</small></label>
      <label>Acidente informado pelo SIB<LevelSelect name="accidentLevel" value={policy.accidentLevel}/></label>
      <label>Afastamento prolongado<LevelSelect name="prolongedLevel" value={policy.prolongedLevel}/></label>
      <label>Recorrência informada pelo SIB<LevelSelect name="recurrenceLevel" value={policy.recurrenceLevel}/></label>
      <label>Alteração retroativa informada pelo SIB<LevelSelect name="retroactiveLevel" value={policy.retroactiveLevel}/></label>
      <div className="absence-form-actions"><button className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : "Salvar regras"}</button></div>
    </form>
  </details>;
}

export function AbsenceFollowup({ detail, data, onChanged, onError }) {
  const [busy, setBusy] = useState(false);
  const [documentIds, setDocumentIds] = useState([]);
  const [message, setMessage] = useState("");
  const [subject, setSubject] = useState("Acompanhamento do afastamento");
  const [uploadFile, setUploadFile] = useState(null);
  const [requestKey, setRequestKey] = useState("");
  const canManage = data.canManage && detail.actionsAvailable;
  const canUpload = data.canUpload && detail.actionsAvailable;
  const canSend = data.canSend && detail.actionsAvailable;
  const pendingEmail = detail.deliveries?.some(value => ["SENDING", "UNCERTAIN"].includes(value.status));
  const docs = detail.availableDocuments || [];
  const bound = new Set(detail.documentIds || []);
  const csrf = data.csrf;

  async function request(action, fields = {}) {
    if (busy) return false;
    setBusy(true);
    try {
      const response = await fetch("/api/ui/absences/workflow", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, absenceId: detail.id, contextToken: detail.contextToken, csrf, ...fields }), signal: AbortSignal.timeout(20000) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível registrar a providência.");
      onChanged(result.message); return true;
    } catch (error) { onError(error.message); return false; }
    finally { setBusy(false); }
  }
  async function createAlert(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const saved = await request("create_alert", Object.fromEntries(new FormData(form)));
    if (saved) form.reset();
  }
  async function changeAlert(event, alertId) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const action = values.operation; delete values.operation;
    await request(action, { alertId, ...values });
  }
  async function upload(event) {
    event.preventDefault();
    if (!uploadFile || busy) return;
    if (uploadFile.size > 10 * 1024 * 1024) { onError("O documento deve ter no máximo 10 MB."); return; }
    const form = event.currentTarget, fields = new FormData(form);
    setBusy(true);
    try {
      const response = await fetch(`/api/ui/absences/${detail.id}/documents`, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": uploadFile.type || "application/octet-stream", "X-File-Name": encodeURIComponent(uploadFile.name), "X-CSRF-Token": csrf,
          "X-Context-Token": detail.contextToken, "X-Document-Description": encodeURIComponent(fields.get("description") || ""), "X-Document-Date": fields.get("documentDate") || "" },
        body: uploadFile, signal: AbortSignal.timeout(30000) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível anexar o documento.");
      setUploadFile(null); form.reset(); onChanged(result.message || "Documento guardado no prontuário e vinculado ao afastamento.");
    } catch (error) { onError(error.message); }
    finally { setBusy(false); }
  }
  async function send(event) {
    event.preventDefault();
    if (busy || pendingEmail) return;
    if (!window.confirm(`Enviar este acompanhamento por e-mail para ${detail.email}?${documentIds.length ? `\n\n${documentIds.length} documento(s) selecionado(s).` : "\n\nSem anexos."}`)) return;
    const attempt = requestKey || crypto.randomUUID();
    setRequestKey(attempt); setBusy(true);
    try {
      const response = await fetch(`/api/ui/absences/${detail.id}/email`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf, requestKey: attempt, contextToken: detail.contextToken, subject, message, documentIds }), signal: AbortSignal.timeout(60000) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "O envio não foi confirmado. Confira o histórico antes de tentar novamente.");
      setRequestKey(""); setMessage(""); setDocumentIds([]); onChanged(result.message || "O e-mail foi aceito pelo servidor SMTP. O recebimento pode ser registrado no histórico.");
    } catch (error) { onError(error.message); }
    finally { setBusy(false); }
  }
  function toggleDoc(id) { setDocumentIds(values => values.includes(id) ? values.filter(value => value !== id) : [...values, id]); setRequestKey(""); }

  return <div className="absence-followup">
    {!detail.actionsAvailable && <p className="absence-followup-warning">A identidade ou a resposta da origem precisa de conferência. As ações ficam disponíveis após a confirmação do vínculo e do período.</p>}
    {detail.metadata?.length > 0 && <details className="absence-followup-section"><summary>Informações complementares do SIB</summary><dl className="absence-metadata">{detail.metadata.map(field => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl></details>}

    <section className="absence-followup-section"><div className="absence-section-heading"><div><h3>Providências</h3><p>Prazos e responsáveis deste afastamento.</p></div><span>{detail.alerts?.filter(alert => ["PENDING", "POSTPONED"].includes(alert.status) && alert.currentIdentity).length || 0} em aberto</span></div>
      {!detail.alerts?.length && <p className="absence-form-help">Nenhuma providência registrada. Os marcos automáticos seguem as regras de acompanhamento.</p>}
      <div className="absence-alert-list">{detail.alerts?.map(alert => <article key={`${alert.id}-${alert.status}-${alert.due_date}-${alert.note}-${alert.updated_at}`} className={`absence-task is-${alert.level.toLowerCase()}${alert.overdue ? " is-overdue" : ""}`}>
        <div className="absence-task-heading"><strong>{alert.title}</strong><span>{alert.statusLabel}{alert.overdue && " · atrasada"}</span></div>
        <p>{alert.levelLabel} · prazo {day(alert.due_date)} · {alert.responsible_name || "Sem responsável"}{alert.automatic ? " · gerada pelo acompanhamento" : ""}</p>
        {alert.note && <p className="absence-task-note">{alert.note}</p>}
        {!alert.currentIdentity && <p className="absence-followup-warning">Histórico de uma identidade anterior. Confira os dados de origem.</p>}
        {canManage && alert.currentIdentity && <details><summary>Atualizar providência</summary><form className="absence-followup-form" onSubmit={event => changeAlert(event, alert.id)}>
          <label>Ação<select name="operation" defaultValue={["DONE", "CANCELLED"].includes(alert.status) ? "reopen_alert" : "save_alert"}>
            <option value="save_alert">Salvar responsável e observação</option>{["PENDING", "POSTPONED"].includes(alert.status) ? <><option value="complete_alert">Concluir</option><option value="postpone_alert">Prorrogar prazo</option><option value="cancel_alert">Cancelar</option></> : <option value="reopen_alert">Reabrir</option>}
          </select></label><label>Prazo<input name="dueDate" type="date" defaultValue={alert.due_date}/></label>
          <label>Responsável<ResponsibleSelect users={data.responsibles || []} value={alert.responsible_id}/></label>
          <label className="absence-wide-field">Observação / justificativa<textarea name="note" maxLength={1000} defaultValue={alert.note} rows={2}/></label>
          <div className="absence-form-actions"><button className="btn btn-outline-primary" disabled={busy}>Registrar atualização</button></div>
        </form></details>}
      </article>)}</div>
      {canManage && <details className="absence-add-alert"><summary>+ Adicionar providência</summary><form className="absence-followup-form" onSubmit={createAlert}>
        <label className="absence-wide-field">O que precisa ser acompanhado?<input name="title" required minLength={3} maxLength={180} placeholder="Descreva a providência"/></label>
        <label>Prazo<input name="dueDate" type="date" required/></label><label>Nível de atenção<LevelSelect/></label><label>Responsável<ResponsibleSelect users={data.responsibles || []}/></label>
        <label className="absence-wide-field">Observação<textarea name="note" rows={2} maxLength={1000}/></label><div className="absence-form-actions"><button className="btn btn-primary" disabled={busy}>Adicionar providência</button></div>
      </form></details>}
    </section>

    {data.canDocuments && <details className="absence-followup-section"><summary>Documentos <span>{detail.documentIds?.length || 0} vinculados</span></summary>
      <p className="absence-form-help">Os documentos ficam no prontuário do vínculo. Aqui você pode anexar ou vincular arquivos para acompanhar este afastamento.</p>
      <ul className="absence-document-list">{docs.map(document => <li key={document.id}><div><a href={document.downloadUrl}>{document.filename}</a><small>{document.documentTypeLabel}{document.document_date ? ` · ${day(document.document_date)}` : ""}</small></div>{bound.has(document.id) ? <span>Vinculado</span> : canUpload && <button type="button" disabled={busy} onClick={() => request("link_document", { documentId: document.id })}>Vincular</button>}</li>)}</ul>
      {!docs.length && <p className="absence-form-help">Nenhum documento no prontuário deste vínculo.</p>}
      {canUpload && <form onSubmit={upload} className="absence-followup-form absence-document-upload"><label className="absence-wide-field">Adicionar documento<input type="file" name="file" required accept=".pdf,.png,.jpg,.jpeg,.docx" onChange={event => setUploadFile(event.target.files?.[0] || null)}/><small>PDF, imagem ou DOCX · até 10 MB.</small></label>
        <label>Data do documento<input type="date" name="documentDate"/></label><label className="absence-wide-field">Descrição<input name="description" maxLength={500} placeholder="Ex.: atestado apresentado no acompanhamento"/></label>
        <div className="absence-form-actions"><button className="btn btn-outline-primary" disabled={busy || !uploadFile}>{busy ? "Aguarde…" : "Anexar ao prontuário e ao afastamento"}</button></div>
      </form>}
    </details>}

    <details className="absence-followup-section"><summary>Comunicações <span>{detail.deliveries?.length || 0} registro(s)</span></summary>
      {canSend && <form onSubmit={send} className="absence-followup-form"><p className="absence-form-help">Destino: <strong>{detail.email || "E-mail não informado no cadastro"}</strong>. O envio usa a conexão SMTP configurada. O aceite do servidor não comprova o recebimento.</p>
        <label className="absence-wide-field">Assunto<input required minLength={3} maxLength={180} value={subject} onChange={event => { setSubject(event.target.value); setRequestKey(""); }}/></label>
        <label className="absence-wide-field">Mensagem<textarea required minLength={10} maxLength={10000} rows={4} value={message} onChange={event => { setMessage(event.target.value); setRequestKey(""); }} placeholder="Escreva o acompanhamento que será enviado à pessoa."/></label>
        {docs.length > 0 && <fieldset className="absence-attachment-choices"><legend>Anexos opcionais do prontuário</legend>{docs.map(document => <label className="absence-check" key={document.id}><input type="checkbox" checked={documentIds.includes(document.id)} onChange={() => toggleDoc(document.id)}/>{document.filename}</label>)}</fieldset>}
        {pendingEmail && <p className="absence-followup-warning">Há envio em andamento ou com resultado incerto. Confira o histórico antes de uma nova tentativa.</p>}
        <div className="absence-form-actions"><button className="btn btn-primary" disabled={busy || !detail.email || pendingEmail}>{busy ? "Aguarde…" : "Enviar e-mail"}</button>{requestKey && !pendingEmail && <button type="button" className="btn btn-outline-primary" disabled={busy} onClick={() => { if (window.confirm("Confira o histórico: se o envio anterior foi aceito, esta ação poderá enviar outra mensagem. Iniciar uma nova tentativa?")) setRequestKey(""); }}>Iniciar nova tentativa</button>}</div>
      </form>}
      {!detail.deliveries?.length && <p className="absence-form-help">Nenhuma comunicação registrada neste afastamento.</p>}
      <div className="absence-delivery-list">{detail.deliveries?.map(delivery => <article key={delivery.id}><div><strong>{delivery.subject}</strong><span>{deliveryNames[delivery.status] || delivery.status}{delivery.receipt_at ? " · recebimento confirmado" : ""}</span></div><p>{delivery.target} · {delivery.created_at} UTC</p><small>Protocolo {delivery.protocol}</small>{delivery.error_message && <p>{delivery.error_message}</p>}
        {delivery.receipt_reference && <p>Confirmação: {delivery.receipt_reference}</p>}
        {canSend && delivery.status === "SENT" && !delivery.receipt_at && <form className="absence-receipt-form" onSubmit={event => { event.preventDefault(); request("confirm_receipt", { deliveryId: delivery.id, note: new FormData(event.currentTarget).get("note") }); }}><label>Referência do recebimento<input name="note" minLength={5} maxLength={500} required placeholder="Ex.: resposta recebida em data e horário"/></label><button className="btn btn-outline-primary" disabled={busy}>Confirmar recebimento</button></form>}
        {canSend && ["SENDING", "UNCERTAIN"].includes(delivery.status) && <details><summary>Registrar resultado conferido</summary><form className="absence-followup-form" onSubmit={event => { event.preventDefault(); request("resolve_delivery", { deliveryId: delivery.id, ...Object.fromEntries(new FormData(event.currentTarget)) }); }}><label>Resultado<select name="outcome"><option value="SENT">Envio confirmado na conferência</option><option value="FAILED">Não enviado; liberar nova tentativa</option></select></label><label className="absence-wide-field">Evidência da conferência<textarea name="note" required minLength={8} maxLength={1000} rows={2}/></label><div className="absence-form-actions"><button className="btn btn-outline-primary" disabled={busy}>Registrar conferência</button></div></form></details>}
      </article>)}</div>
    </details>

    <details className="absence-followup-section"><summary>Histórico do acompanhamento <span>Últimos 30 eventos</span></summary><div className="absence-workflow-history">{detail.followupHistory?.map(event => { const values = JSON.parse(event.details_json || "{}"); return <article key={event.id}><strong>{historyNames[event.action] || "Acompanhamento atualizado"}</strong><small>{event.created_at} UTC · {event.actor_name || "Sistema"}</small>{values.title && <p>{values.title}</p>}{values.dueDate && <p>Prazo: {day(values.dueDate)}</p>}{values.note && <p>{values.note}</p>}{values.reference && <p>{values.reference}</p>}</article>; })}{!detail.followupHistory?.length && <p className="absence-form-help">Nenhum evento de acompanhamento registrado.</p>}</div></details>
  </div>;
}
