"use client";

import { useEffect, useMemo, useState } from "react";
import OperationsIcon from "./operations-icon";

const filters = [
  ["overdue", "Atrasados", "overdue"],
  ["week", "Até 7 dias", "week"],
  ["month", "Próximos 30 dias", "month"],
  ["pending", "Pendentes", "pending"],
  ["awaiting", "Aguardando assinatura", "awaiting"],
  ["final", "Prazo final em 30 dias", "finalMonth"],
];

const views = [
  ["queue", "Prazos"],
  ["unsigned", "Não assinados"],
  ["completed", "Concluídos"],
];
const periodIcons = { overdue: "alert", week: "clock", month: "calendar", pending: "files", awaiting: "pen", final: "flag" };

const statusLabels = {
  PENDING: "Pendente",
  PREPARED: "Prévia validada",
  AWAITING_SIGNATURE: "Aguardando assinatura",
  SIGNED: "Assinado",
  ERROR: "Com erro",
  CANCELLED: "Cancelado",
  INCOMPLETE: "Dados incompletos", SOURCE_REVIEW: "Conferir origem", SOURCE_EXTENDED: "Prorrogado no SIB", EXPIRED: "Vencido · bloqueado",
};

function formatDate(value, fallback = "Não informado") {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return fallback;
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function formatDateTime(value, fallback = "Não enviado") {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
  return match ? `${match[3]}/${match[2]}/${match[1]}, ${match[4]}:${match[5]}` : fallback;
}

function remaining(days) {
  if (days == null) return "A definir";
  if (days < 0) return `${Math.abs(days)} dia(s) em atraso`;
  if (days === 0) return "Vence hoje";
  return `${days} dia(s) restantes`;
}

function statusLabel(item) {
  if (item.isTest && item.deliveryMode !== "CLICKSIGN_REAL" && item.status !== "PENDING") return "Aguardando teste real";
  return statusLabels[item.status] || item.status;
}

const realTestDefaults = {
  senderName: "Pessoa Exemplo Remetente",
  senderCpf: "",
  senderEmail: "demo@example.com",
  senderPhone: "",
  senderChannel: "WHATSAPP",
  recipientEmail: "destinatario@example.com",
  recipientChannel: "WHATSAPP",
};

function realTestDraft(item, drafts) {
  const current = drafts[item.key] || {};
  return {
    senderName: current.senderName ?? (item.senderName || realTestDefaults.senderName),
    senderCpf: current.senderCpf ?? (item.senderCpf || realTestDefaults.senderCpf),
    senderEmail: current.senderEmail ?? (item.senderEmail || realTestDefaults.senderEmail),
    senderPhone: current.senderPhone ?? (item.senderPhone || realTestDefaults.senderPhone),
    senderChannel: current.senderChannel ?? (item.senderChannel || realTestDefaults.senderChannel),
    recipientEmail: current.recipientEmail ?? (item.recipientEmail || realTestDefaults.recipientEmail),
    recipientChannel: current.recipientChannel ?? (item.workerChannel || realTestDefaults.recipientChannel),
  };
}

function realTestDraftMatches(item, draft) {
  return item.senderName === draft.senderName && item.senderCpf === draft.senderCpf &&
    item.senderEmail === draft.senderEmail && item.senderPhone === draft.senderPhone &&
    item.senderChannel === draft.senderChannel && item.recipientEmail === draft.recipientEmail &&
    item.workerChannel === draft.recipientChannel;
}

function RealClicksignTestPanel({ item, draft, canManage, busy, onField, onAction, previewHref }) {
  const isReal = item.deliveryMode === "CLICKSIGN_REAL";
  const hasLiveEnvelope = isReal && item.envelopeId && !item.envelopeId.startsWith("SIM-");
  const canPrepare = canManage && (!hasLiveEnvelope || item.status === "CANCELLED") && item.status !== "SIGNED";
  const canSend = isReal && item.status === "PREPARED" && item.hasPreview && realTestDraftMatches(item, draft);
  const channelLabel = value => value === "EMAIL" ? "e-mail" : "WhatsApp";
  return <section className="extensions-real-test" aria-label={`Teste real Clicksign de ${item.name}`}>
    <div className="extensions-real-heading"><div><strong>Teste real pela Clicksign</strong><span>Executado nesta linha da fila, com o número do temporário e o remetente configurado aqui.</span></div>{isReal && item.deliveryEnvironment && <em>{item.deliveryEnvironment === "production" ? "Produção" : "Sandbox"}</em>}</div>
    {!isReal && <p className="extensions-real-note">O pacote anterior era apenas simulação local e não enviou mensagem. O teste abaixo cria um envelope real. O PDF é demonstrativo, sem validade contratual; o TPR-39 definitivo depende dos dados completos do SIB.</p>}
    {canPrepare && <>
      <div className="extensions-real-parties">
        <div className="extensions-real-party"><strong>Remetente · assina primeiro</strong><div className="extensions-real-grid">
          <label>Nome<input value={draft.senderName} maxLength="180" onChange={event => onField("senderName", event.target.value)} /></label>
          <label>CPF<input value={draft.senderCpf} inputMode="numeric" maxLength="14" onChange={event => onField("senderCpf", event.target.value)} /></label>
          <label>Canal do convite<select value={draft.senderChannel} onChange={event => onField("senderChannel", event.target.value)}><option value="WHATSAPP">WhatsApp</option><option value="EMAIL">E-mail</option></select></label>
          <label>E-mail do documento final<input type="email" value={draft.senderEmail} maxLength="254" onChange={event => onField("senderEmail", event.target.value)} /></label>
          <label>WhatsApp<input inputMode="tel" value={draft.senderPhone} maxLength="20" onChange={event => onField("senderPhone", event.target.value)} /></label>
        </div></div>
        <div className="extensions-real-party"><strong>Destinatário · recebe após o remetente</strong><p>{item.name} · CPF cadastrado no teste · WhatsApp {item.hasWhatsapp ? "informado" : "não informado"}</p><div className="extensions-real-grid">
          <label>Canal do convite<select value={draft.recipientChannel} onChange={event => onField("recipientChannel", event.target.value)}><option value="WHATSAPP" disabled={!item.hasWhatsapp}>WhatsApp</option><option value="EMAIL">E-mail</option></select></label>
          <label>E-mail do documento final<input type="email" value={draft.recipientEmail} maxLength="254" onChange={event => onField("recipientEmail", event.target.value)} /></label>
        </div></div>
      </div>
      <button type="button" className="btn btn-outline-primary btn-sm" disabled={busy} onClick={event => onAction(item, "prepare", event)}>{busy ? "Preparando…" : isReal && item.status === "PREPARED" ? "Atualizar prévia" : isReal && item.status === "CANCELLED" ? "Preparar novo pacote" : "Gerar prévia do teste real"}</button>
    </>}
    {isReal && item.status === "PREPARED" && <div className="extensions-real-ready" role="status"><strong>Prévia pronta para conferência.</strong><span>Este PDF demonstra a ordem das assinaturas e não contém cláusulas contratuais.</span><div className="extensions-actions"><a className="btn btn-outline-primary btn-sm" href={previewHref(item)} target="_blank" rel="noopener noreferrer">Abrir prévia PDF</a><button type="button" className="btn btn-primary btn-sm" disabled={!canSend || busy} onClick={event => onAction(item, "send", event)}>{busy ? "Enviando à Clicksign…" : "Confirmar e enviar convite real"}</button></div>{!realTestDraftMatches(item, draft) && <small>Os campos mudaram. Gere uma nova prévia antes de enviar.</small>}</div>}
    {hasLiveEnvelope && item.status === "AWAITING_SIGNATURE" && <div className="extensions-real-status" role="status"><strong>Convite real iniciado · {channelLabel(item.senderChannel)} para o remetente</strong><span>A Clicksign mantém o destinatário no segundo grupo e o notifica depois da assinatura do remetente. O documento final será enviado por e-mail aos dois signatários.</span><span>Envelope: {item.envelopeId}</span><button type="button" className="btn btn-outline-primary btn-sm" disabled={busy} onClick={event => onAction(item, "refresh", event)}>{busy ? "Consultando…" : "Atualizar status na Clicksign"}</button></div>}
    {hasLiveEnvelope && ["SIGNED", "SOURCE_EXTENDED"].includes(item.status) && <div className="extensions-real-ready" role="status"><strong>Envelope concluído na Clicksign.</strong><span>As duas assinaturas foram finalizadas. O documento final foi configurado para envio por e-mail.</span><span>Envelope: {item.envelopeId}</span><button type="button" className="btn btn-outline-primary btn-sm" disabled={busy} onClick={event => onAction(item, "refresh", event)}>Consultar novamente</button></div>}
    {hasLiveEnvelope && item.status === "CANCELLED" && <div className="extensions-real-status" role="status"><strong>Envelope cancelado na Clicksign.</strong><span>O identificador permanece nesta linha para consulta.</span><span>Envelope: {item.envelopeId}</span></div>}
    {isReal && item.providerStatus === "CLICKSIGN_ERRO" && <div className="extensions-alert" role="alert">A Clicksign recusou o envio. Confira os dados e tente novamente depois de atualizar a prévia.</div>}
  </section>;
}

function RegistryExtensionSimulation({ item, channel, canManage, busy, onChannel, onAction, previewHref }) {
  const hasContact = channel === "WHATSAPP" ? item.hasWhatsapp : item.hasEmail;
  return <section className="extensions-real-test" aria-label={`Simulação local de prorrogação de ${item.name}`}>
    <div className="extensions-real-heading"><div><strong>Prorrogação vinculada ao cadastro local</strong><span>Este vínculo foi criado em Pessoas. A prévia, o envio e as assinaturas abaixo são apenas simulados.</span></div><em>LOCAL · SEM ENVIO EXTERNO</em></div>
    {item.status === "PENDING" && <div className="extensions-real-grid"><label>Canal fictício do convite<select value={channel} onChange={event => onChannel(event.target.value)}><option value="WHATSAPP" disabled={!item.hasWhatsapp}>WhatsApp {item.hasWhatsapp ? "" : "· sem número cadastrado"}</option><option value="EMAIL" disabled={!item.hasEmail}>E-mail {item.hasEmail ? "" : "· sem e-mail cadastrado"}</option></select></label><button type="button" className="btn btn-outline-primary btn-sm" disabled={!canManage || busy || !hasContact} onClick={() => onAction(item, "prepare", channel)}>{busy ? "Preparando…" : "Gerar prévia local"}</button></div>}
    {item.status === "PREPARED" && <div className="extensions-real-ready"><strong>Prévia de demonstração pronta.</strong><span>Revise o documento fictício antes de avançar a simulação.</span><div className="extensions-actions"><a className="btn btn-outline-primary btn-sm" href={previewHref(item)} target="_blank" rel="noopener noreferrer">Abrir prévia PDF</a><button type="button" className="btn btn-primary btn-sm" disabled={!canManage || busy} onClick={() => onAction(item, "send", channel)}>{busy ? "Registrando…" : "Simular envio do pacote"}</button></div></div>}
    {item.status === "AWAITING_SIGNATURE" && <div className="extensions-real-status"><strong>Etapa do remetente {item.senderSignedAt ? "concluída" : "aguardando"}</strong><span>{item.senderSignedAt ? "A etapa do destinatário está liberada." : "O destinatário permanece bloqueado até a assinatura simulada do remetente."}</span><span>Pacote local: {item.envelopeId || "—"} · canal fictício {item.workerChannel === "EMAIL" ? "e-mail" : "WhatsApp"}</span><div className="extensions-actions">{!item.senderSignedAt && <button type="button" className="btn btn-outline-primary btn-sm" disabled={!canManage || busy} onClick={() => onAction(item, "sign_sender", channel)}>Simular assinatura do remetente</button>}{item.senderSignedAt && !item.workerSignedAt && <button type="button" className="btn btn-primary btn-sm" disabled={!canManage || busy} onClick={() => onAction(item, "sign_worker", channel)}>Simular assinatura do destinatário</button>}</div><small>Nenhuma mensagem ou assinatura real foi enviada.</small></div>}
    {["SIGNED", "SOURCE_EXTENDED"].includes(item.status) && <div className="extensions-real-ready"><strong>Prorrogação concluída na simulação local.</strong><span>As duas etapas de assinatura foram simuladas; nada foi enviado à Clicksign.</span></div>}
    {item.status === "CANCELLED" && <div className="extensions-real-status"><strong>Esta simulação foi cancelada.</strong><span>Cadastre outro vínculo temporário em Pessoas para abrir um novo fluxo.</span></div>}
  </section>;
}

const tprFields = [["employer_name", "Razão social da empregadora"], ["employer_cnpj", "CNPJ da empregadora"],
  ["employer_address", "Endereço da empregadora"], ["worker_function", "Função do trabalhador"],
  ["tomadora_name", "Razão social da tomadora"], ["tomadora_cnpj", "CNPJ da tomadora"],
  ["work_location", "Local da prestação"], ["complementary_demand", "Demanda complementar"]];
const workflowLabels = { PREPARED: "Prévia pronta", CREATING_ENVELOPE: "Criação em andamento", OUTCOME_UNKNOWN: "Conferir criação anterior",
  PROVIDER_REVIEW: "Conferir pacote na Clicksign", AWAITING_SIGNATURE: "Aguardando assinaturas", OVERDUE_SIGNATURE: "Assinatura pendente após o vencimento",
  REFUSED: "Recusa de signatário", SIGNED: "Assinaturas concluídas", CANCELLED: "Pacote cancelado", NON_SIGNATURE: "Ausência de assinatura registrada", RECOVERED: "Envelope recuperado" };

function sibDraft(item) {
  const stored = item.workflow?.context || {}, defaults = item.formDefaults || {};
  return { fields: { ...(defaults.fields || {}), ...(stored.fields || {}) },
    representative: stored.representative || { name: "", cpf: "", role: "", email: "", phone: "", channel: "EMAIL", birthday: "" },
    workerEmail: stored.workerEmail || defaults.workerEmail || "", workerPhone: stored.workerPhone || defaults.workerPhone || "",
    workerChannel: stored.workerChannel || (item.hasEmail ? "EMAIL" : "WHATSAPP"), signerId: String(stored.signerId || ""),
    authorizedRepresentative: Boolean(stored.representative) };
}

function SibExtensionPanel({ item, busy, canManage, onAction, previewHref }) {
  const [draft, setDraft] = useState(() => sibDraft(item));
  const [approved, setApproved] = useState(false);
  const [recoveryId, setRecoveryId] = useState("");
  const [checkedCreation, setCheckedCreation] = useState(false);
  const [reason, setReason] = useState("");
  const workflow = item.workflow;
  useEffect(() => { setDraft(sibDraft(item)); setApproved(false); }, [workflow?.previewHash]);
  const live = Boolean(item.envelopeId && !item.envelopeId.startsWith("SIM-"));
  const canPrepare = canManage && item.eligible && item.status !== "SIGNED" && (!live || item.status === "CANCELLED") && !["CREATING_ENVELOPE", "OUTCOME_UNKNOWN"].includes(workflow?.stage);
  const saved = workflow?.context;
  const unchanged = saved && tprFields.every(([key]) => (draft.fields[key] || "").trim() === (saved.fields[key] || "").trim()) &&
    draft.workerEmail.trim().toLowerCase() === saved.workerEmail && draft.workerPhone.replace(/\D/g, "") === saved.workerPhone &&
    draft.workerChannel === saved.workerChannel && String(saved.signerId || "") === draft.signerId &&
    (Boolean(draft.signerId) || ["name", "role", "cpf", "email", "phone", "channel", "birthday"].every(key => String(draft.representative[key] || "").trim() === String(saved.representative[key] || "").trim()));
  const disabled = busy || Boolean(workflow?.inProgress);
  const update = (key, value) => { setDraft(current => ({ ...current, [key]: value })); setApproved(false); };
  const act = (action, extra = {}) => onAction(item, action, { ...draft, ...extra });
  return <section className="extensions-real-test extensions-sib-workflow" aria-label={`Prorrogação de ${item.name}`}>
    <div className="extensions-real-heading"><div><strong>Termo de prorrogação</strong><span>{workflowLabels[workflow?.stage] || item.sourceMessage || "Confira os dados e prepare o TPR."}</span></div><a href={`/cadastros/pessoas?id=${encodeURIComponent(item.employmentKey)}`}>Abrir prontuário →</a></div>
    {item.proposedDueDate && <p>Nova data proposta: <strong>{formatDate(item.proposedDueDate)}</strong> · 90 dias após o primeiro vencimento. O vencimento informado pelo SIB permanece separado.</p>}
    {workflow?.error && <div className="extensions-alert" role="alert">{workflow.error}</div>}
    {canPrepare && <form onSubmit={event => { event.preventDefault(); act("prepare"); }}>
      <details className="extensions-tpr-fields" open={!workflow}><summary>Dados do termo e da empregadora</summary><div className="extensions-real-grid">
        {tprFields.map(([key, label]) => <label key={key}>{label}<input required maxLength={["employer_address", "work_location", "complementary_demand"].includes(key) ? 400 : 180} value={draft.fields[key] || ""} onChange={event => update("fields", { ...draft.fields, [key]: event.target.value })} /></label>)}
      </div><small>Complete os dados ausentes para este termo. Os complementos ficam registrados na prévia aprovada.</small></details>
      <div className="extensions-real-parties">
        <section className="extensions-real-party"><strong>Representante da empregadora · assina primeiro</strong>
          {item.signers?.length > 0 && <label>Representante cadastrado<select value={draft.signerId} onChange={event => update("signerId", event.target.value)}><option value="">Informar representante neste termo</option>{item.signers.map(signer => <option key={signer.id} value={signer.id}>{signer.name}{signer.role ? ` · ${signer.role}` : ""}</option>)}</select></label>}
          {!draft.signerId && <div className="extensions-real-grid">{[["name", "Nome completo", "text"], ["cpf", "CPF", "text"], ["role", "Cargo / função", "text"], ["email", "E-mail para o PDF final", "email"], ["phone", "WhatsApp com DDD", "tel"], ["birthday", "Nascimento (opcional)", "date"]].map(([key, label, type]) => <label key={key}>{label}<input type={type} required={!["phone", "birthday"].includes(key) || (key === "phone" && draft.representative.channel === "WHATSAPP")} maxLength={key === "email" ? 254 : 180} value={draft.representative[key] || ""} onChange={event => update("representative", { ...draft.representative, [key]: event.target.value })}/></label>)}
            <label>Canal do convite<select value={draft.representative.channel} onChange={event => update("representative", { ...draft.representative, channel: event.target.value })}><option value="EMAIL">E-mail</option><option value="WHATSAPP">WhatsApp</option></select></label>
          </div>}
        </section>
        <section className="extensions-real-party"><strong>Trabalhador · assina na segunda etapa</strong><p>{item.name} · matrícula {item.registration}</p><div className="extensions-real-grid">
          <label>E-mail para o PDF final<input type="email" required maxLength={254} value={draft.workerEmail} onChange={event => update("workerEmail", event.target.value)}/></label>
          <label>WhatsApp com DDD<input type="tel" required={draft.workerChannel === "WHATSAPP"} maxLength={20} value={draft.workerPhone} onChange={event => update("workerPhone", event.target.value)}/></label>
          <label>Canal do convite<select value={draft.workerChannel} onChange={event => update("workerChannel", event.target.value)}><option value="EMAIL">E-mail</option><option value="WHATSAPP">WhatsApp</option></select></label>
        </div></section>
      </div>
      <label className="extensions-tpr-check"><input type="checkbox" required checked={draft.authorizedRepresentative} onChange={event => update("authorizedRepresentative", event.target.checked)}/><span>Conferi os dados e confirmo que o representante está autorizado a assinar pela empregadora.</span></label>
      <button className="btn btn-outline-primary" disabled={disabled}>{disabled ? "Aguarde…" : workflow ? "Atualizar prévia do TPR" : "Gerar prévia do TPR"}</button>
    </form>}
    {item.hasPreview && <div className="extensions-real-ready"><div className="extensions-actions"><a className="btn btn-outline-primary" href={previewHref(item)} target="_blank" rel="noopener noreferrer">Abrir PDF preparado</a>
      {canManage && workflow?.stage === "PREPARED" && item.eligible && <span className="extensions-sib-environment">Clicksign: {item.deliveryEnvironment === "production" ? "Produção" : "Sandbox"}</span>}</div>
      {canManage && workflow?.stage === "PREPARED" && item.eligible && <><label className="extensions-tpr-check"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} disabled={!unchanged || disabled}/><span>Revisei este PDF, o prazo de 90 dias, os signatários e os canais. Autorizo o envio deste termo.</span></label>
        {!unchanged && <small>Há campos alterados. Atualize e confira a prévia antes de enviar.</small>}
        <button type="button" className="btn btn-primary" disabled={!approved || !unchanged || disabled} onClick={() => act("send", { approvedPreview: true, previewHash: workflow.previewHash })}>Enviar TPR para assinatura</button></>}
    </div>}
    {live && <div className="extensions-real-status"><strong>Envelope {item.envelopeId}</strong><span>{item.senderSignedAt ? "Representante: assinatura confirmada" : "Representante: aguardando confirmação"} · {item.workerSignedAt ? "Trabalhador: assinatura confirmada" : "Trabalhador: aguardando confirmação"}</span>
      {canManage && <div className="extensions-actions"><button type="button" className="btn btn-outline-primary" disabled={disabled} onClick={() => act("refresh")}>Consultar situação</button>{!["SIGNED", "CANCELLED"].includes(item.status) && <button type="button" className="btn btn-outline-primary" disabled={disabled} onClick={() => act("remind")}>Enviar lembrete</button>}</div>}
      {workflow?.archivedDocumentId && <a href={`/api/ui/person-documents/${workflow.archivedDocumentId}`}>Baixar TPR assinado do prontuário →</a>}
      {workflow?.evidenceDocumentId && <a href={`/api/ui/person-documents/${workflow.evidenceDocumentId}`}>Baixar registro de ausência de assinatura →</a>}
      {item.status === "SIGNED" && !workflow?.archivedDocumentId && <span>Assinaturas concluídas. O arquivamento do PDF será retomado na próxima consulta.</span>}
      {canManage && !["SIGNED", "CANCELLED"].includes(item.status) && <details className="extensions-tpr-fields"><summary>Cancelar ou registrar ausência de assinatura</summary><label>Motivo<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} placeholder="Descreva a providência e o motivo"/></label><div className="extensions-actions"><button type="button" className="btn btn-outline-secondary" disabled={disabled || reason.trim().length < 12} onClick={() => act("cancel", { reason })}>Cancelar pacote</button><button type="button" className="btn btn-outline-secondary" disabled={disabled || reason.trim().length < 12} onClick={() => act("register_non_signature", { reason })}>Registrar ausência de assinatura</button></div></details>}
    </div>}
    {canManage && workflow && !live && ["CREATING_ENVELOPE", "OUTCOME_UNKNOWN"].includes(workflow.stage) && <details className="extensions-tpr-fields"><summary>Conferir tentativa anterior na Clicksign</summary><p>Confira o pacote na Clicksign antes de iniciar outra criação. Associe o envelope encontrado ou registre a verificação de que ele não existe.</p><label>Identificador do envelope<input value={recoveryId} onChange={event => setRecoveryId(event.target.value)} maxLength={100}/></label><button type="button" className="btn btn-outline-primary" disabled={disabled || !recoveryId.trim()} onClick={() => act("recover", { envelopeId: recoveryId })}>Conferir e associar envelope</button><label className="extensions-tpr-check"><input type="checkbox" checked={checkedCreation} onChange={event => setCheckedCreation(event.target.checked)}/><span>Verifiquei a Clicksign e não existe envelope desta tentativa.</span></label><label>Referência da conferência<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500}/></label><button type="button" className="btn btn-outline-secondary" disabled={disabled || !checkedCreation || reason.trim().length < 12} onClick={() => act("recover_creation", { confirmedNoEnvelope: true, reason })}>Registrar conferência e liberar nova tentativa</button></details>}
    {workflow?.events?.length > 0 && <details className="extensions-tpr-fields"><summary>Histórico de ações ({workflow.events.length})</summary><div className="extensions-tpr-events">{workflow.events.map((event, index) => <p key={index}><strong>{formatDateTime(event.created_at.replace(" ", "T"), event.created_at)}</strong><span>{event.action.replaceAll("_", " ")}</span></p>)}</div></details>}
    {item.history.length > 0 && <details className="operations-help"><summary>Pacotes desta admissão</summary><div>{item.history.map((record,index) => <p key={index}>{formatDate(record.firstDueDate)} · {statusLabels[record.status] || record.status} {record.envelopeId && `· envelope ${record.envelopeId}`} {record.hasPreview && <a href={previewHref({...item, firstDueDate: record.firstDueDate})} target="_blank" rel="noopener noreferrer">PDF preservado</a>}</p>)}</div></details>}
  </section>;
}

function matchesPeriod(item, period) {
  if (period === "overdue") return item.daysToFirst < 0 && !["SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW"].includes(item.status);
  if (period === "week") return item.daysToFirst >= 0 && item.daysToFirst <= 7 && !["SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW"].includes(item.status);
  if (period === "month") return item.daysToFirst >= 0 && item.daysToFirst <= 30 && !["SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW"].includes(item.status);
  if (period === "pending") return ["PENDING", "PREPARED", "ERROR", "CANCELLED"].includes(item.status);
  if (period === "awaiting") return item.status === "AWAITING_SIGNATURE";
  if (period === "final") return item.daysToSecond != null && item.daysToSecond >= 0 && item.daysToSecond <= 30;
  return true;
}

export default function ExtensionsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState("queue");
  const [period, setPeriod] = useState("month");
  const [company, setCompany] = useState("");
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("");
  const [realDrafts, setRealDrafts] = useState({});
  const [localChannels, setLocalChannels] = useState({});
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState("");
  const [classification, setClassification] = useState({ code: "", label: "Temporário" });

  function setRealField(item, key, value) {
    setRealDrafts(previous => ({ ...previous, [item.key]: { ...realTestDraft(item, previous), [key]: value } }));
  }

  async function runRealAction(item, action, event) {
    event?.preventDefault();
    if (action === "send" && !window.confirm(`A Clicksign está em ${item.deliveryEnvironment === "sandbox" ? "Sandbox" : item.deliveryEnvironment === "production" ? "Produção" : "ambiente configurado"}. Enviar agora criará um envelope real e notificará o remetente no canal escolhido. O destinatário só recebe o convite depois da assinatura do remetente. O PDF é demonstrativo, sem validade contratual. Continuar?`)) return;
    setBusyKey(item.key);
    setError("");
    setNotice("");
    try {
      const draft = realTestDraft(item, realDrafts);
      const response = await fetch("/api/ui/temporary-extensions/real", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf, sourceSystem: item.sourceSystem,
          sourceId: item.sourceId, firstDueDate: item.firstDueDate, action,
          ...draft }),
      });
      const payload = await response.json().catch(() => null);
      if (Array.isArray(payload?.items)) setData(payload);
      if (!response.ok) throw new Error(payload?.error || "Não foi possível preparar o teste real da Clicksign.");
      setNotice(payload.message || "Teste real atualizado.");
    } catch (reason) { setError(reason.message); }
    finally { setBusyKey(""); }
  }

  async function runLocalSimulation(item, action, channel) {
    setBusyKey(item.key); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ui/temporary-extensions", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf, sourceSystem: item.sourceSystem, sourceId: item.sourceId,
          firstDueDate: item.firstDueDate, action, channel, signerName: "Caio Caobianco · Assistente DP" }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error || "Não foi possível atualizar a simulação local.");
      if (Array.isArray(payload?.items)) setData(payload);
      setNotice(payload.message || "Etapa da simulação atualizada.");
    } catch (cause) { setError(cause.message); }
    finally { setBusyKey(""); }
  }

  async function runSibAction(item, action, values = {}) {
    setBusyKey(item.key); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ui/temporary-extensions/real", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.csrf, sourceSystem: "SIB", sourceId: item.sourceId, firstDueDate: item.firstDueDate, action, ...values }) });
      const result = await response.json().catch(() => null);
      if (Array.isArray(result?.items)) setData(result);
      if (!response.ok) throw new Error(result?.error || "Não foi possível concluir a ação deste termo.");
      setNotice(result.message || "Prorrogação atualizada.");
    } catch (reason) { setError(reason.message); } finally { setBusyKey(""); }
  }
  async function classifyCode(event) {
    event.preventDefault(); if (!classification.code) return;
    if (!window.confirm(`Confirmar que o código interno ${classification.code} do SIB significa ${classification.label}? Essa classificação recalculará as filas dos vínculos desse código.`)) return;
    setBusyKey("classification"); setError("");
    try {
      const response = await fetch("/api/ui/temporary-extensions", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.csrf, action: "classify_sib_code", ...classification }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "Não foi possível confirmar o código."); setData(result); setNotice(result.message);
    } catch (reason) { setError(reason.message); } finally { setBusyKey(""); }
  }
  function previewHref(item) {
    const query = new URLSearchParams({ sourceSystem: item.sourceSystem,
      sourceId: item.sourceId, firstDueDate: item.firstDueDate });
    return `/api/ui/temporary-extensions/preview?${query.toString()}`;
  }

  useEffect(() => {
    if (busyKey) return;
    let active = true;
    const read = () => fetch("/api/ui/temporary-extensions", { credentials: "same-origin", cache: "no-store" })
      .then(async response => {
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(payload?.error || "Não foi possível carregar as prorrogações.");
        return payload;
      })
      .then(payload => { if (active) setData(payload); })
      .catch(reason => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    read();
    const timer = window.setInterval(() => { if (!document.hidden && !busyKey) read(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [busyKey]);

  const items = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("pt-BR");
    return (data?.items || []).filter(item =>
      (view === "queue"
        ? !["SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW"].includes(item.status) && matchesPeriod(item, period)
        : view === "completed"
          ? ["SIGNED", "SOURCE_EXTENDED"].includes(item.status)
          : !["SIGNED", "SOURCE_EXTENDED", "CANCELLED"].includes(item.status)) &&
      (!company || item.companyName === company) &&
      (!stage || item.status === stage) &&
      (!needle || [item.name, item.registration, item.companyName, item.envelopeId].some(value => String(value || "").toLocaleLowerCase("pt-BR").includes(needle)))
    );
  }, [data, view, period, company, search, stage]);

  const viewCounts = useMemo(() => {
    const all = data?.items || [];
    return {
      queue: all.filter(item => !["SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW"].includes(item.status)).length,
      unsigned: all.filter(item => !["SIGNED", "SOURCE_EXTENDED", "CANCELLED"].includes(item.status)).length,
      completed: all.filter(item => ["SIGNED", "SOURCE_EXTENDED"].includes(item.status)).length,
    };
  }, [data]);

  function selectView(nextView) {
    setView(nextView);
    setPeriod(nextView === "queue" ? "month" : "all");
    setStage("");
  }

  const emptyMessage = view === "completed"
    ? ["Ainda não há prorrogações concluídas.", "Prorrogações informadas pelo SIB e pacotes assinados aparecem aqui com situações distintas; uma data na origem não comprova assinatura."]
    : view === "unsigned"
      ? ["Nenhuma prorrogação sem assinatura.", "Pendências, prévias, pacotes aguardando assinatura e erros ficam nesta lista até serem concluídos ou cancelados."]
      : !data?.items?.length
        ? ["Nenhum contrato temporário ativo recebido.", "Temporários ativos entram pela data do primeiro vencimento. O segundo vencimento informado pela origem identifica uma prorrogação já registrada."]
        : ["Nenhuma prorrogação corresponde aos filtros selecionados.", "Ajuste os filtros de prazo, empresa ou etapa para ver outros contratos."];

  return <>
    
    <section className="container-fluid page-content extensions-page operations-page">
      <div className="extensions-intro operations-intro"><div><p className="extensions-eyebrow operations-eyebrow">CONTRATOS TEMPORÁRIOS</p><h2>Renovações por vencimento</h2><p>Acompanhe os vencimentos, confira as pendências e consulte as etapas de assinatura.</p></div><a className="btn btn-outline-primary operations-secondary-action" href="/rascunho-termo-prorrogacao.html" target="_blank" rel="noopener noreferrer"><OperationsIcon name="files"/>Modelo do termo</a></div>

      {error && <div className="extensions-alert" role="alert">{error}</div>}
      {notice && <div className="extensions-notice" role="status">{notice}</div>}
      {loading && <div className="extensions-empty" role="status">Carregando vencimentos…</div>}
      {!loading && data && <>
        <div className="operations-note"><OperationsIcon name="info"/><p>Prepare o termo nos 30 dias anteriores ao primeiro vencimento. Confira os dados e o PDF antes de enviar. Desligamentos e segundo vencimento confirmado impedem uma nova preparação.</p></div>
        {data.canSimulate && data.unclassifiedCodes?.length > 0 && <details className="operations-help"><summary>Conferir códigos de vínculo do SIB</summary><div><p>A origem retorna códigos internos sem descrição. Confirme o significado antes de classificar os contratos.</p><form className="extensions-real-grid" onSubmit={classifyCode}><label>Código SIB<select value={classification.code} onChange={event => setClassification(current => ({...current,code:event.target.value}))}><option value="">Selecione</option>{data.unclassifiedCodes.map(code => <option key={code} value={code}>{code}</option>)}</select></label><label>Tipo confirmado<select value={classification.label} onChange={event => setClassification(current => ({...current,label:event.target.value}))}>{["Temporário","CLT","Estágio","Outro"].map(label => <option key={label}>{label}</option>)}</select></label><button className="btn btn-outline-primary" disabled={!classification.code || Boolean(busyKey)}>Confirmar classificação</button></form></div></details>}
        <nav className="extensions-views operations-tabs" aria-label="Visualização de prorrogações">
          {views.map(([key, label]) => <button type="button" key={key} onClick={() => selectView(key)} className={`extensions-view is-${key} ${view === key ? "is-active" : ""}`} aria-pressed={view === key}><span>{label}</span><strong>{viewCounts[key]}</strong></button>)}
        </nav>

        {view === "queue" && <div className="extensions-stats" aria-label="Filtrar prorrogações por prazo e etapa">
          {filters.map(([key, label, field]) => <button type="button" key={key} onClick={() => setPeriod(current => current === key ? "all" : key)} className={`extensions-stat operations-stat stat-${key} ${period === key ? "is-active" : ""}`} aria-pressed={period === key}><span className="operations-stat-top"><OperationsIcon name={periodIcons[key]}/><strong>{data.summary?.[field] || 0}</strong></span><span>{label}</span></button>)}
        </div>}

        <section className="extensions-filters card operations-filter-card" aria-labelledby="extensions-filter-title">
          <h3 id="extensions-filter-title" className="operations-filter-title"><OperationsIcon name="filter"/>Filtrar contratos</h3>
          <div className="operations-filter-main">
            <label className="operations-search">Buscar contrato<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nome, matrícula ou envelope" /></label>
            <label>Empresa<select value={company} onChange={event => setCompany(event.target.value)}><option value="">Todas as empresas</option>{data.companies.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            <label>Etapa<select value={stage} onChange={event => setStage(event.target.value)}><option value="">Todas as etapas</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          <div className="operations-filter-footer"><span>{view === "queue" && period !== "all" ? `Prazo selecionado: ${filters.find(([key]) => key === period)?.[1]}.` : "Todos os prazos nesta visualização."} Os filtros atualizam a lista automaticamente.</span><button type="button" className="operations-clear" onClick={() => { setPeriod("all"); setCompany(""); setSearch(""); setStage(""); }}>Limpar filtros</button></div>
        </section>

        <div className="operations-results-heading"><h3>Contratos nesta seleção</h3><span>{items.length} {items.length === 1 ? "contrato" : "contratos"}</span></div>

        {!items.length ? <div className="extensions-empty card"><strong>{emptyMessage[0]}</strong><p>{emptyMessage[1]}</p>{view === "queue" && !data.items.length && <a className="btn btn-outline-primary btn-sm" href="/central-dp/assinaturas">Configurar Clicksign</a>}</div>
          : <div className="extensions-list">{items.map(item => {
            const draft = realTestDraft(item, realDrafts);
            return <details className={`extensions-item ${item.daysToFirst != null && item.daysToFirst < 0 ? "is-overdue" : item.daysToFirst != null && item.daysToFirst <= 7 ? "is-urgent" : ""}`} key={item.key}>
              <summary><span className="extensions-person"><strong>{item.name}</strong>{item.isTest && <em className="extensions-test-badge">Dados de teste</em>}<small>{item.registration ? `Matrícula ${item.registration}` : "Matrícula não informada"} · {item.contractType || "Tipo a conferir"}</small><small>{item.companyName || "Empresa não informada"}</small></span><span className="extensions-date"><span>1º vencimento</span><strong>{formatDate(item.firstDueDate)}</strong><small>{remaining(item.daysToFirst)}</small></span><span className="extensions-date extensions-final"><span>2º vencimento</span><strong>{formatDate(item.secondDueDate, "A definir")}</strong><small>{remaining(item.daysToSecond)}</small></span><span className={`extensions-status status-${item.status.toLowerCase()}${item.isTest && item.deliveryMode !== "CLICKSIGN_REAL" ? " is-simulated" : ""}`}>{statusLabel(item)}</span><span className="extensions-plus"><OperationsIcon name="chevron"/></span></summary>
              <div className="extensions-detail">
                <div className="extensions-detail-grid"><div><span>Admissão</span><strong>{formatDate(item.admissionDate)}</strong></div><div><span>Fonte</span><strong>{item.sourceSystem}</strong></div><div><span>Envelope Clicksign</span><strong>{item.envelopeId || "Ainda não criado"}</strong></div><div><span>Último envio</span><strong>{formatDateTime(item.lastSentAt)}</strong></div></div>
                {item.sourceSystem === "REGISTRY" && <RegistryExtensionSimulation item={item} channel={localChannels[item.key] || (item.hasWhatsapp ? "WHATSAPP" : "EMAIL")} canManage={data.canSimulate} busy={busyKey === item.key} onChannel={value => setLocalChannels(previous => ({ ...previous, [item.key]: value }))} onAction={runLocalSimulation} previewHref={previewHref}/>}
                {item.isTest && item.sourceSystem !== "REGISTRY" && <RealClicksignTestPanel item={item} draft={draft} canManage={data.canSimulate} busy={busyKey === item.key} onField={(key, value) => setRealField(item, key, value)} onAction={runRealAction} previewHref={previewHref}/>}
                {item.sourceSystem === "SIB" && <SibExtensionPanel item={item} busy={busyKey === item.key} canManage={data.canManage ?? data.canSimulate} onAction={runSibAction} previewHref={previewHref}/>}
                {!item.isTest && !["REGISTRY","SIB"].includes(item.sourceSystem) && <p className="extensions-guidance">O envio real do termo será habilitado após conectar os vínculos à origem SIB/BITTI e receber os dados cadastrais completos.</p>}
              </div>
            </details>;
          })}</div>}
        <details className="operations-help"><summary><OperationsIcon name="info"/>Sobre o termo de prorrogação<OperationsIcon name="chevron"/></summary><p>A prévia segue o modelo TPR-39. As seções 1 e 2 usam os dados da empresa e da pessoa recebidos via SIB; campos ainda ausentes aparecem como pendentes.</p></details>
      </>}
    </section>
  </>;
}
