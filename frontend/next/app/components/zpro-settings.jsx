"use client";

import { useEffect, useRef, useState } from "react";
import "./zpro-settings.css";

const initialForm = { active: false, base_url: "", token: "", zpro_queue_id: "", zpro_user_id: "", zpro_whatsapp_id: "", zpro_block_chatbot: true, timeout_seconds: 20 };

function fromSettings(settings) {
  return { ...initialForm, ...Object.fromEntries(Object.keys(initialForm).filter(key => key !== "token").map(key => [key, settings?.[key] ?? initialForm[key]])), token: "" };
}

export default function ZproSettings({ canConfigure = false, onSaved }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [form, setForm] = useState(initialForm);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const submitting = useRef(false);
  useEffect(() => {
    if (!canConfigure || !open || data && reload === 0) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    fetch("/api/zpro/settings", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
      .then(async response => { const result = await response.json().catch(() => null); if (!response.ok) throw new Error(result?.error || "Não foi possível consultar o canal WhatsApp."); return result; })
      .then(result => { if (!controller.signal.aborted) { setData(result); setForm(fromSettings(result.settings || result)); } })
      .catch(cause => { if (!controller.signal.aborted) setError(cause.name === "TimeoutError" ? "O serviço demorou para responder. Tente novamente." : cause.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [canConfigure, open, reload]);
  if (!canConfigure) return null;
  const settings = data?.settings || data || {};
  const tokenConfigured = Boolean(settings.tokenConfigured || settings.token_configured || settings.has_token || settings.hasToken);
  function field(key, value) { setForm(current => ({ ...current, [key]: value })); }
  async function save(event) {
    event.preventDefault(); if (submitting.current || !data) return;
    submitting.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/zpro/settings", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-CSRF-Token": data.csrf }, body: JSON.stringify({ ...form, csrf: data.csrf, timeout_seconds: Number(form.timeout_seconds), zpro_queue_id: Number(form.zpro_queue_id) || 0, zpro_user_id: Number(form.zpro_user_id) || 0, zpro_whatsapp_id: Number(form.zpro_whatsapp_id) || 0 }) });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(result?.error || "Não foi possível salvar o canal WhatsApp.");
      setData(result); setForm(fromSettings(result.settings || result));
      setNotice(result.message || "Configuração salva. Nenhuma mensagem foi enviada."); onSaved?.(result);
    } catch (cause) { setError(cause.message); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <details className="zpro-settings" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span className="zpro-settings-icon" aria-hidden="true">☏</span><span><strong>Canal WhatsApp · ZPRO</strong><small>Configuração de envio e acompanhamento</small></span><span className={`zpro-settings-status${settings.active ? " is-active" : ""}`}>{settings.active ? "Ativo" : "Configurar"}</span><span className="zpro-settings-chevron" aria-hidden="true">⌄</span></summary>
    <div className="zpro-settings-body">
      <p>Conecte o canal usado pelo DP para enviar documentos e acompanhar o atendimento.</p>
      {loading && <p role="status">Carregando configuração…</p>}
      {error && <div className="zpro-settings-alert" role="alert">{error}{!data && <button type="button" disabled={loading} onClick={() => setReload(value => value + 1)}>Tentar novamente</button>}</div>}
      {notice && <p className="zpro-settings-notice" role="status">{notice}</p>}
      {data && <form onSubmit={save}>
        <fieldset disabled={busy || loading} className="zpro-settings-form">
          <label className="zpro-settings-active"><input type="checkbox" checked={Boolean(form.active)} onChange={event => field("active", event.target.checked)}/><span>Habilitar este canal para envios</span></label>
          <label>Endereço da API ZPRO<input type="url" required={Boolean(form.active)} value={form.base_url} onChange={event => field("base_url", event.target.value)} placeholder="https://seu-atendimento.exemplo.com" maxLength={500}/></label>
          <label>Token de acesso<input type="password" autoComplete="new-password" value={form.token} onChange={event => field("token", event.target.value)} placeholder={tokenConfigured ? "Token configurado · deixe vazio para preservar" : "Informe o token do canal"} maxLength={3000}/><small>O token salvo não é exibido novamente.</small></label>
          <details className="zpro-settings-advanced"><summary>Fila, responsável e opções de atendimento</summary><div>
            <label>ID da fila<input type="number" min="0" max="2147483647" step="1" value={form.zpro_queue_id} onChange={event => field("zpro_queue_id", event.target.value)}/></label>
            <label>ID do responsável<input type="number" min="0" max="2147483647" step="1" value={form.zpro_user_id} onChange={event => field("zpro_user_id", event.target.value)}/></label>
            <label>ID da conexão WhatsApp<input type="number" min="0" max="2147483647" step="1" value={form.zpro_whatsapp_id} onChange={event => field("zpro_whatsapp_id", event.target.value)}/></label>
            <label>Tempo máximo da consulta (segundos)<input type="number" min="3" max="120" step="1" value={form.timeout_seconds} onChange={event => field("timeout_seconds", event.target.value)} required/></label>
            <label className="zpro-settings-checkbox"><input type="checkbox" checked={Boolean(form.zpro_block_chatbot)} onChange={event => field("zpro_block_chatbot", event.target.checked)}/><span>Manter o atendimento fora do chatbot</span></label>
          </div></details>
          <div className="zpro-settings-save"><small>Salvar configura o canal. Os envios são feitos na ação de comunicação de cada caso.</small><button type="submit" className="btn btn-primary" disabled={busy || loading}>{busy ? "Salvando…" : "Salvar canal"}</button></div>
        </fieldset>
      </form>}
    </div>
  </details>;
}
