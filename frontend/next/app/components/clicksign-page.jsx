"use client";

import { useEffect, useState } from "react";
import OperationsIcon from "./operations-icon";

async function readJson(response) {
  const contentType = response.headers.get("content-type")?.toLowerCase() || "";
  if (!contentType.includes("application/json")) {
    throw new Error(response.status === 401 ? "Sua sessão expirou. Entre novamente." : "O servidor respondeu em um formato inesperado.");
  }
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("O servidor enviou uma resposta inválida.");
  if (!response.ok) throw new Error(payload.error || payload.message || "Não foi possível consultar a Clicksign.");
  return payload;
}

const defaultSettings = { connectionName: "Clicksign", environment: "sandbox", active: false, timeoutSeconds: 20, allowedChannels: "BOTH", refreshMinutes: 15, webhookUrl: "" };
const GLOBAL_SCOPE_KEY = "__global__";

export default function ClicksignPage() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(defaultSettings);
  const [secret, setSecret] = useState({ accessToken: "", hmacSecret: "", removeAccessToken: false, removeHmacSecret: false });
  const [panel, setPanel] = useState("connection");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [sendingTest, setSendingTest] = useState(false);
  const [testSender, setTestSender] = useState({ name: "", email: "", phone: "", cpf: "", channel: "WHATSAPP" });
  const [testRecipient, setTestRecipient] = useState({ name: "", email: "", phone: "", cpf: "", channel: "WHATSAPP" });
  const [testSent, setTestSent] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState(null);

  async function load(companyKey = "") {
    setLoading(true);
    setError("");
    try {
      const suffix = companyKey ? `?companyKey=${encodeURIComponent(companyKey)}` : "";
      const payload = await readJson(await fetch(`/api/clicksign/settings${suffix}`, { credentials: "same-origin", cache: "no-store" }));
      setData(payload);
      setForm({ connectionName: payload.connectionName || "Clicksign", environment: payload.environment || "sandbox", active: Boolean(payload.active), timeoutSeconds: payload.timeoutSeconds || 20, allowedChannels: payload.allowedChannels || "BOTH", refreshMinutes: payload.refreshMinutes || 15, webhookUrl: payload.webhookUrl || "" });
      setTestSender(current => ({ ...current, name: current.name || payload.currentUser?.name || "", email: current.email || payload.currentUser?.email || "" }));
      setSecret({ accessToken: "", hmacSecret: "", removeAccessToken: false, removeHmacSecret: false });
    } catch (reason) {
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(""); }, []);

  function setField(key, value) {
    setForm(current => ({ ...current, [key]: value }));
  }

  async function sendJson(url, payload) {
    const response = await fetch(url, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, csrf: data?.csrf }) });
    return readJson(response);
  }

  async function saveSettings(event) {
    event.preventDefault();
    if (!data?.companyKey || saving) return;
    setSaving(true); setError(""); setNotice(""); setResult(null);
    try {
      const payload = await sendJson("/api/clicksign/settings", { ...form, ...secret, companyKey: data.companyKey });
      await load(data.companyKey);
      setNotice(payload.message || "Configuração salva.");
    } catch (reason) { setError(reason.message); }
    finally { setSaving(false); }
  }

  async function testConnection() {
    if (!data?.canManage || !data?.companyKey || checking || !data.configured) return;
    setChecking(true); setError(""); setNotice(""); setResult(null);
    try { setResult(await sendJson("/api/clicksign/test", { companyKey: data.companyKey })); }
    catch (reason) { setError(reason.message); }
    finally { setChecking(false); }
  }

  async function sendTest(event) {
    event.preventDefault();
    const channelAllowed = channel => form.allowedChannels === "BOTH" || form.allowedChannels === channel;
    if (!data?.canManage || !data?.companyKey || sendingTest || !data.configured || !form.active ||
        !channelAllowed(testSender.channel) || !channelAllowed(testRecipient.channel)) return;
    setSendingTest(true); setError(""); setNotice(""); setTestSent(null);
    try {
      const payload = await sendJson("/api/clicksign/send-test", {
        companyKey: data.companyKey,
        senderName: testSender.name, senderEmail: testSender.email, senderPhone: testSender.phone,
        senderChannel: testSender.channel, senderCpf: testSender.cpf,
        recipientName: testRecipient.name, recipientEmail: testRecipient.email, recipientPhone: testRecipient.phone,
        recipientChannel: testRecipient.channel, recipientCpf: testRecipient.cpf,
      });
      setTestSent(payload);
    } catch (reason) { setError(reason.message); }
    finally { setSendingTest(false); }
  }

  const stateLabel = loading ? "Carregando" : error ? "Precisa de atenção" : result?.connected ? "Conectada" : result ? "Falha na conexão" : data?.configured ? "Credencial configurada" : "Aguardando configuração";
  const stateClass = result?.connected ? "is-connected" : error || (result && !result.connected) ? "is-error" : "is-pending";
  const selectedCompany = data?.companies?.find(item => item.key === data.companyKey);
  const generalScope = data?.globalScope || data?.companyKey === GLOBAL_SCOPE_KEY;
  const testConfigNeedsSave = Boolean(data && (form.environment !== data.environment || form.active !== data.active || form.allowedChannels !== data.allowedChannels || secret.accessToken || secret.removeAccessToken));
  const channelAllowed = channel => form.allowedChannels === "BOTH" || form.allowedChannels === channel;
  const selectedChannelsAllowed = channelAllowed(testSender.channel) && channelAllowed(testRecipient.channel);

  return <>
    
    <section className="container-fluid page-content clicksign-page operations-page">
      <div className="clicksign-intro operations-intro">
        <div><p className="operations-eyebrow">ASSINATURAS ELETRÔNICAS</p><h2>Conexão Clicksign</h2><span>Configure a conexão e acompanhe o teste de assinatura entre remetente e destinatário.</span></div>
        <a className="btn btn-outline-primary operations-secondary-action" href="/central-dp/prorrogacoes">Ver prorrogações<OperationsIcon name="arrow"/></a>
      </div>

      {error && <div className="clicksign-alert is-error" role="alert">{error}</div>}
      {notice && <div className="clicksign-alert is-success" role="status">{notice}</div>}
      {result && <div className={`clicksign-alert ${result.connected ? "is-success" : "is-error"}`} role="status">{result.message}{result.connected && <span> A conta possui {result.envelopeCount} envelope(s).</span>}</div>}
      {testSent && <div className="clicksign-alert is-success" role="status">{testSent.message} Ambiente: {testSent.environmentLabel}. Identificador do envelope: {testSent.envelopeId}.</div>}

      <article className="card clicksign-card clicksign-company-picker">
        <span className="clicksign-scope-icon"><OperationsIcon name="building"/></span>
        <div className="clicksign-scope-field"><label htmlFor="clicksign-company">Configuração utilizada</label>
        <select id="clicksign-company" value={data?.companyKey || ""} disabled={loading} onChange={event => { setResult(null); setNotice(""); setSecret({ accessToken: "", hmacSecret: "", removeAccessToken: false, removeHmacSecret: false }); load(event.target.value); }}>
          <option value={GLOBAL_SCOPE_KEY}>Conta Clicksign · configuração geral</option>
          {data?.companies?.map(item => <option key={item.key} value={item.key}>{item.name}{item.code ? ` · ${item.code}` : ""}</option>)}
        </select>
        {generalScope && <small>O token autentica a conta Clicksign e pode ser configurado ou testado sem cadastro de empresa.</small>}
        {selectedCompany && <small>{selectedCompany.source}{selectedCompany.code ? ` · Código ${selectedCompany.code}` : ""}</small>}
        </div><span className={`clicksign-status ${stateClass}`}><i aria-hidden="true" />{stateLabel}</span>
      </article>

      {loading && <div className="clicksign-loading" role="status">Carregando configuração Clicksign…</div>}
      {data?.companyKey && data.canManage && !loading && <>
        <nav className="operations-tabs clicksign-panels" aria-label="Seções de assinaturas">
          <button type="button" className={panel === "connection" ? "is-active" : ""} aria-pressed={panel === "connection"} onClick={() => setPanel("connection")}><OperationsIcon name="plug"/>Conexão</button>
          <button type="button" className={panel === "test" ? "is-active" : ""} aria-pressed={panel === "test"} onClick={() => setPanel("test")}><OperationsIcon name="pen"/>Teste de assinatura</button>
        </nav>
        {panel === "connection" && <form className="card clicksign-card clicksign-settings-form" onSubmit={saveSettings}>
          <div className="clicksign-section-heading"><div><p className="clicksign-eyebrow">ACESSO E PREFERÊNCIAS</p><h3>{generalScope ? "Configuração geral da conta" : "Configuração da empresa"}</h3></div><label className="clicksign-active-toggle"><input type="checkbox" checked={form.active} onChange={event => setField("active", event.target.checked)} /> Conexão ativa</label></div>
          <div className="clicksign-form-grid">
            <label>Conexão<input maxLength={100} value={form.connectionName} onChange={event => setField("connectionName", event.target.value)} required /></label>
            <label>Ambiente<select value={form.environment} onChange={event => setField("environment", event.target.value)}><option value="sandbox">Sandbox</option><option value="production">Produção</option></select></label>
            <label>Canais permitidos<select value={form.allowedChannels} onChange={event => setField("allowedChannels", event.target.value)}><option value="BOTH">Ambos</option><option value="EMAIL">E-mail</option><option value="WHATSAPP">WhatsApp</option></select></label>
            <label>Access Token<input type="password" autoComplete="new-password" value={secret.accessToken} placeholder={data.tokenConfigured ? "Configurado · deixe em branco para preservar" : data.configured ? "Usando credencial geral do servidor" : "Informe o token da Clicksign"} onChange={event => setSecret(current => ({ ...current, accessToken: event.target.value, removeAccessToken: false }))} /></label>
          </div>
          <div className="clicksign-secret-actions">
            {data.tokenStoredHere && <label><input type="checkbox" checked={secret.removeAccessToken} onChange={event => setSecret(current => ({ ...current, removeAccessToken: event.target.checked, accessToken: "" }))} /> Remover token desta configuração</label>}
          </div>
          <details className="clicksign-advanced operations-more-filters"><summary><OperationsIcon name="settings"/><span>Configurações avançadas</span><small>Atualização, timeout e webhook</small><OperationsIcon name="chevron"/></summary><div className="clicksign-form-grid">
            <label>Timeout (segundos)<input type="number" min="3" max="120" value={form.timeoutSeconds} onChange={event => setField("timeoutSeconds", event.target.value)} required /></label>
            <label>Atualizar status a cada<select value={form.refreshMinutes} onChange={event => setField("refreshMinutes", event.target.value)}><option value="5">5 minutos</option><option value="15">15 minutos</option><option value="30">30 minutos</option><option value="60">1 hora</option><option value="360">6 horas</option><option value="1440">24 horas</option></select></label>
            <label className="clicksign-wide-field">Webhook público HTTPS<input type="url" maxLength={500} value={form.webhookUrl} placeholder="https://seu-dominio.com/webhooks/clicksign" onChange={event => setField("webhookUrl", event.target.value)} /><small>O endereço é salvo nesta configuração. O recebimento de notificações ainda não está habilitado.</small></label>
            <label className="clicksign-wide-field">HMAC SHA256 Secret<input type="password" autoComplete="new-password" value={secret.hmacSecret} placeholder={data.hmacConfigured ? "Configurado · deixe em branco para preservar" : "Opcional"} onChange={event => setSecret(current => ({ ...current, hmacSecret: event.target.value, removeHmacSecret: false }))} /></label>
          </div>{data.hmacStoredHere && <div className="clicksign-secret-actions"><label><input type="checkbox" checked={secret.removeHmacSecret} onChange={event => setSecret(current => ({ ...current, removeHmacSecret: event.target.checked, hmacSecret: "" }))} /> Remover segredo desta configuração</label></div>}</details>
          <div className="clicksign-form-actions"><button className="btn btn-primary" type="submit" disabled={saving}>{saving ? "Salvando…" : "Salvar configuração"}</button><button className="btn btn-outline-primary" type="button" onClick={testConnection} disabled={loading || saving || checking || !data.configured}>{checking ? "Testando…" : "Testar conexão"}</button><span>Access Token: {data.tokenSource}. {data.canUseProtectedStorage ? "Segredos protegidos pela conta atual do Windows." : "Credenciais locais exigem armazenamento protegido do Windows."}</span></div>
        </form>}

        {panel === "test" && <form className="card clicksign-card clicksign-test-send" onSubmit={sendTest}>
          <div className="clicksign-section-heading"><div><p className="clicksign-eyebrow">CONFERÊNCIA DO FLUXO</p><h3>Teste de assinatura em duas etapas</h3></div><span>{data.environmentLabel}</span></div>
          <p>O remetente assina primeiro. Depois, o destinatário recebe seu convite. O CPF identifica cada signatário; a data de nascimento é confirmada na Clicksign.</p>
          {data.environment === "production" && <div className="clicksign-production-warning" role="note">Produção: a Clicksign enviará um convite real pelo canal escolhido para cada signatário. Quando todos assinarem, o documento finalizado será enviado por e-mail aos dois.</div>}
          <div className="clicksign-party-grid">
            <fieldset className="clicksign-party">
              <legend><span>1</span> Remetente</legend>
              <p>Assina primeiro. Escolha por onde receberá o convite; o documento finalizado chega por e-mail.</p>
              <div className="clicksign-party-fields">
                <label>Nome completo<input maxLength={180} value={testSender.name} onChange={event => setTestSender(current => ({ ...current, name: event.target.value }))} required /></label>
                <label>Canal do convite<select value={testSender.channel} onChange={event => setTestSender(current => ({ ...current, channel: event.target.value }))}><option value="WHATSAPP" disabled={!channelAllowed("WHATSAPP")}>WhatsApp</option><option value="EMAIL" disabled={!channelAllowed("EMAIL")}>E-mail</option></select></label>
                <label>E-mail para receber o documento final<input type="email" maxLength={254} value={testSender.email} onChange={event => setTestSender(current => ({ ...current, email: event.target.value }))} required /></label>
                <label>WhatsApp com DDD<input inputMode="tel" maxLength={20} value={testSender.phone} onChange={event => setTestSender(current => ({ ...current, phone: event.target.value }))} required={testSender.channel === "WHATSAPP"} /></label>
                <label>CPF para confirmação<input inputMode="numeric" maxLength={18} value={testSender.cpf} onChange={event => setTestSender(current => ({ ...current, cpf: event.target.value }))} placeholder="000.000.000-00" required /></label>
              </div>
            </fieldset>
            <fieldset className="clicksign-party">
              <legend><span>2</span> Destinatário</legend>
              <p>Recebe o convite escolhido após a assinatura do remetente. O documento finalizado chega por e-mail.</p>
              <div className="clicksign-party-fields">
                <label>Nome completo<input maxLength={180} value={testRecipient.name} onChange={event => setTestRecipient(current => ({ ...current, name: event.target.value }))} required /></label>
                <label>Canal do convite<select value={testRecipient.channel} onChange={event => setTestRecipient(current => ({ ...current, channel: event.target.value }))}><option value="WHATSAPP" disabled={!channelAllowed("WHATSAPP")}>WhatsApp</option><option value="EMAIL" disabled={!channelAllowed("EMAIL")}>E-mail</option></select></label>
                <label>E-mail para receber o documento final<input type="email" maxLength={254} value={testRecipient.email} onChange={event => setTestRecipient(current => ({ ...current, email: event.target.value }))} required /></label>
                <label>WhatsApp com DDD<input inputMode="tel" maxLength={20} value={testRecipient.phone} onChange={event => setTestRecipient(current => ({ ...current, phone: event.target.value }))} required={testRecipient.channel === "WHATSAPP"} /></label>
                <label>CPF para confirmação<input inputMode="numeric" maxLength={18} value={testRecipient.cpf} onChange={event => setTestRecipient(current => ({ ...current, cpf: event.target.value }))} placeholder="000.000.000-00" required /></label>
              </div>
            </fieldset>
          </div>
          <div className="clicksign-form-actions"><button className="btn btn-primary" type="submit" disabled={sendingTest || loading || !data.configured || !data.active || testConfigNeedsSave || !selectedChannelsAllowed}>{sendingTest ? "Enviando…" : "Enviar convite ao remetente"}</button><span>{!data.configured ? "Configure o token antes de enviar." : !data.active ? "Ative e salve a conexão para liberar o envio." : !selectedChannelsAllowed ? "Os canais escolhidos precisam estar liberados na configuração." : testConfigNeedsSave ? "Salve as alterações da conexão antes de enviar." : "O destinatário será convidado no canal escolhido após a assinatura do remetente; o documento final será enviado por e-mail aos dois."}</span></div>
        </form>}

      </>}

      {data?.companyKey && !data.canManage && !loading && <article className="card clicksign-card"><h3>Configuração restrita</h3><p>As credenciais da conexão só podem ser acessadas por um administrador.</p></article>}
    </section>
  </>;
}
