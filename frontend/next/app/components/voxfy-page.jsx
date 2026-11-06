"use client";

import { useEffect, useState } from "react";

const DEFAULT_URL = "https://vox.selecty.app/v2/api/external/80d4bcf0-9a92-436f-a677-07ec8ff07ff9";

async function readJson(response) {
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("O servidor respondeu em um formato inesperado.");
  if (!response.ok) throw new Error(payload.error || payload.message || "Não foi possível concluir a operação VoxFy.");
  return payload;
}

export default function VoxFyPage() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ connectionName: "VoxFy - WhatsApp principal", apiBaseUrl: DEFAULT_URL, active: false, timeoutSeconds: 30 });
  const [secret, setSecret] = useState("");
  const [removeSecret, setRemoveSecret] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testResult, setTestResult] = useState(null);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await readJson(await fetch("/api/voxfy/settings", { credentials: "same-origin", cache: "no-store" }));
      setData(result);
      setForm({ connectionName: result.connectionName || "VoxFy - WhatsApp principal", apiBaseUrl: result.apiBaseUrl || DEFAULT_URL, active: Boolean(result.active), timeoutSeconds: result.timeoutSeconds || 30 });
      setSecret("");
      setRemoveSecret(false);
    } catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  function change(key, value) { setForm(current => ({ ...current, [key]: value })); }

  async function save(event) {
    event.preventDefault();
    if (!data?.csrf || saving) return;
    setSaving(true); setError(""); setNotice(""); setTestResult(null);
    try {
      const result = await readJson(await fetch("/api/voxfy/settings", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, accessToken: secret, removeAccessToken: removeSecret, csrf: data.csrf }),
      }));
      await load();
      setNotice(result.message || "Configuração salva.");
    } catch (reason) { setError(reason.message); }
    finally { setSaving(false); }
  }

  async function testConnection() {
    if (!data?.csrf || !data?.tokenConfigured || saving || checking) return;
    setChecking(true); setError(""); setNotice(""); setTestResult(null);
    try {
      const result = await readJson(await fetch("/api/voxfy/test", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.csrf }),
      }));
      setTestResult(result);
      await load();
    } catch (reason) { setError(reason.message); }
    finally { setChecking(false); }
  }

  const canTest = Boolean(data?.tokenConfigured) && !secret && !removeSecret &&
    form.apiBaseUrl === data?.apiBaseUrl && Number(form.timeoutSeconds) === Number(data?.timeoutSeconds);

  return <section className="container-fluid page-content voxfy-page">
    <header className="voxfy-intro">
      <div><p>INTEGRAÇÕES · WHATSAPP</p><h2>Conexão VoxFy</h2><span>Configure o acesso ao Z-PRO usado pelo VoxFy. A chave fica protegida no backend e nunca é exibida novamente.</span></div>
      <span className={`voxfy-status ${data?.active && data?.tokenConfigured ? "is-active" : ""}`}><i />{data?.active && data?.tokenConfigured ? "Ativa" : "Configuração pendente"}</span>
    </header>

    {error && <div className="voxfy-alert is-error" role="alert">{error}</div>}
    {notice && <div className="voxfy-alert is-success" role="status">{notice}</div>}
    {testResult && <div className={`voxfy-alert ${testResult.connected ? "is-success" : "is-error"}`} role="status">{testResult.message}</div>}

    <form className="card voxfy-card" onSubmit={save}>
      <div className="voxfy-section-heading"><div><p>CONFIGURAÇÃO DA CONTA</p><h3>{form.connectionName || "VoxFy"}</h3></div><span>Provedor Z-PRO · WhatsApp · Produção</span></div>
      <div className="voxfy-form-grid">
        <label>Nome da conexão<input maxLength={100} value={form.connectionName} onChange={event => change("connectionName", event.target.value)} required disabled={loading} /></label>
        <label className="voxfy-wide-field">URL base da API<input type="url" maxLength={500} value={form.apiBaseUrl} onChange={event => change("apiBaseUrl", event.target.value)} required disabled={loading} /><small>URL da API externa VoxFy, incluindo o identificador da conta.</small></label>
        <label className="voxfy-secret-field">Token / API key<input type="password" autoComplete="new-password" maxLength={8192} value={secret} onChange={event => { setSecret(event.target.value); setRemoveSecret(false); }} placeholder={data?.tokenConfigured ? "Já cadastrado; deixe em branco para preservar" : "Cole a chave Bearer da API"} disabled={loading} /><small>Enviado somente ao backend e protegido pelo Windows. A API Z-PRO documenta autenticação Bearer.</small></label>
        <label>Timeout (segundos)<input type="number" min="3" max="120" value={form.timeoutSeconds} onChange={event => change("timeoutSeconds", event.target.value)} required disabled={loading} /></label>
      </div>
      <div className="voxfy-options">
        <label className="voxfy-check"><input type="checkbox" checked={form.active} onChange={event => change("active", event.target.checked)} disabled={loading} />Ativa</label>
        {data?.tokenConfigured && <label className="voxfy-check is-danger"><input type="checkbox" checked={removeSecret} onChange={event => setRemoveSecret(event.target.checked)} />Remover token desta configuração</label>}
      </div>
      <aside className="voxfy-note"><strong>Teste somente de conectividade</strong><span>O botão consulta GET <code>/params/</code>, uma rota de leitura documentada pelo Z-PRO. Nenhum WhatsApp ou e-mail será enviado.</span></aside>
      <div className="voxfy-actions"><button className="btn btn-primary" type="submit" disabled={loading || saving}>{saving ? "Salvando…" : "Salvar conexão"}</button><button className="btn btn-outline-primary" type="button" onClick={testConnection} disabled={loading || saving || checking || !canTest}>{checking ? "Testando…" : "Testar conexão"}</button><span>{data?.lastTestAt ? `Último teste bem-sucedido: ${data.lastTestAt}` : data?.tokenConfigured ? "Salve alterações antes de testar." : "Informe o token e salve para liberar o teste."}</span></div>
    </form>
  </section>;
}
