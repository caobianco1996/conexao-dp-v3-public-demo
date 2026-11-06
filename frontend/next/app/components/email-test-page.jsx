"use client";

import { useEffect, useState } from "react";

const DEFAULTS = {
  connectionName: "SMTP Demonstração",
  smtpHost: "",
  smtpPort: 587,
  security: "STARTTLS",
  username: "",
  senderEmail: "demo@example.com",
  active: false,
  timeoutSeconds: 30,
};

async function readJson(response) {
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("O servidor respondeu em um formato inesperado.");
  if (!response.ok) throw new Error(payload.error || payload.message || "Não foi possível concluir a operação.");
  return payload;
}

export default function EmailTestPage() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(DEFAULTS);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await readJson(await fetch("/api/email/settings", { credentials: "same-origin", cache: "no-store" }));
      setData(result);
      setForm({
        connectionName: result.connectionName || DEFAULTS.connectionName,
        smtpHost: result.smtpHost || DEFAULTS.smtpHost,
        smtpPort: result.smtpPort || DEFAULTS.smtpPort,
        security: result.security || DEFAULTS.security,
        username: result.username || DEFAULTS.username,
        senderEmail: result.senderEmail || DEFAULTS.senderEmail,
        active: Boolean(result.active),
        timeoutSeconds: result.timeoutSeconds || DEFAULTS.timeoutSeconds,
      });
      setDirty(false);
    } catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  function change(key, value) {
    setForm(current => ({ ...current, [key]: value }));
    setDirty(true);
    setNotice("");
  }

  async function save(event) {
    event.preventDefault();
    if (!data?.csrf || saving || sending) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await readJson(await fetch("/api/email/settings", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, password, csrf: data.csrf }),
      }));
      setPassword("");
      await load();
      setNotice(result.message || "Configuração SMTP salva.");
    } catch (reason) { setError(reason.message); }
    finally { setSaving(false); }
  }

  async function sendTest() {
    if (!data?.csrf || !data?.saved || !data?.credentialConfigured || dirty || saving || sending) return;
    setSending(true); setError(""); setNotice("");
    try {
      const result = await readJson(await fetch("/api/email/send-test", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf }),
      }));
      await load();
      setNotice(result.message || "E-mail de teste enviado.");
    } catch (reason) { setError(reason.message); }
    finally { setSending(false); }
  }

  return <>
    
    <section className="container-fluid page-content email-test-page">
      <header className="email-test-intro">
        <div><p>ENVIO MANUAL · SMTP LOCAWEB</p><h2>Teste real para sua caixa de e-mail</h2><span>Verifique o envio SMTP com o pacote de documentos fictícios. Este teste não usa WhatsApp nem envia para funcionários.</span></div>
        <span className={`email-test-status ${data?.saved && data?.active ? "is-active" : ""}`}><i />{data?.saved && data?.active ? "Configuração salva" : "Configuração pendente"}</span>
      </header>

      {error && <div className="email-test-alert is-error" role="alert">{error}</div>}
      {notice && <div className="email-test-alert is-success" role="status">{notice}</div>}

      <form className="card email-test-card" onSubmit={save}>
        <div className="email-test-section-heading"><div><p>CONEXÃO SMTP</p><h3>{form.connectionName || "SMTP Locaweb"}</h3></div><span>{data?.credentialConfigured ? "Senha protegida no Windows" : "Senha SMTP pendente"}</span></div>
        <div className="email-test-form-grid">
          <label>Nome da conexão<input maxLength={100} value={form.connectionName} onChange={event => change("connectionName", event.target.value)} required disabled={loading} /></label>
          <label>Servidor SMTP<input maxLength={253} value={form.smtpHost} onChange={event => change("smtpHost", event.target.value)} required disabled={loading} /></label>
          <label>Porta SMTP<input type="number" min="1" max="65535" value={form.smtpPort} onChange={event => change("smtpPort", event.target.value)} required disabled={loading} /></label>
          <label>Segurança<select value={form.security} onChange={event => change("security", event.target.value)} disabled={loading}><option value="SSL_TLS">SSL/TLS</option><option value="STARTTLS">STARTTLS</option></select></label>
          <label>Usuário SMTP<input maxLength={254} value={form.username} onChange={event => change("username", event.target.value)} required disabled={loading} /></label>
          <label>Remetente<input type="email" value={form.senderEmail} readOnly aria-readonly="true" disabled={loading} /><small>Fixo em {DEFAULTS.senderEmail}; o teste será enviado somente para este endereço.</small></label>
          <label>Timeout (segundos)<input type="number" min="3" max="120" value={form.timeoutSeconds} onChange={event => change("timeoutSeconds", event.target.value)} required disabled={loading} /></label>
          <label className="email-test-secret-field">Senha SMTP<input type="password" autoComplete="new-password" maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} placeholder={data?.credentialConfigured ? "Salva e protegida no Windows; digite outra para trocar" : "Informe a senha da conta ou senha de aplicativo"} disabled={loading} /><small>{data?.credentialConfigured ? "Deixe em branco para manter a senha protegida. Uma nova senha substitui a atual." : "Ao salvar, fica protegida no Gerenciador de Credenciais do Windows; não é exibida nem gravada no SQLite."}</small></label>
        </div>
        <div className="email-test-options"><label><input type="checkbox" checked={form.active} onChange={event => change("active", event.target.checked)} disabled={loading} /> Conexão ativa para teste manual</label></div>
        <aside className="email-test-note"><strong>O que será enviado</strong><span>Um e-mail de teste real para <b>{DEFAULTS.senderEmail}</b>, com o PDF fictício anexado. O botão não envia mensagem a outras pessoas e não altera o checklist da rescisão.</span></aside>
        {data?.lastTestSentAt && <p className="email-test-last-send">Último envio de teste: {data.lastTestSentAt}</p>}
        <div className="email-test-actions">
          <button className="btn btn-outline-primary" type="submit" disabled={loading || saving || sending}>{saving ? "Salvando…" : "Salvar configuração"}</button>
          <button className="btn btn-primary" type="button" onClick={sendTest} disabled={loading || saving || sending || !data?.saved || !data?.credentialConfigured || dirty || !form.active}>{sending ? "Enviando e-mail…" : `Enviar teste para ${DEFAULTS.senderEmail}`}</button>
          {dirty && <span>Salve as alterações antes do envio.</span>}
          {!data?.saved && <span>Salve a configuração SMTP para liberar o teste.</span>}
          {data?.saved && !data?.credentialConfigured && <span>Informe a senha SMTP acima e salve para habilitar o teste e os envios de rescisão.</span>}
        </div>
      </form>
    </section>
  </>;
}
