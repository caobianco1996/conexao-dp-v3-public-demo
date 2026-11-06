"use client";

import { useEffect, useRef, useState } from "react";
import "./permissions-page.css";

const profileLabels = { ADMIN: "Administrador", GESTOR: "Gestor", OPERADOR: "Operador", CONSULTA: "Consulta" };
const profileDescriptions = { ADMIN: "Administra contas, configurações e operações.", GESTOR: "Coordena a operação e acompanha os dados da equipe.", OPERADOR: "Executa as rotinas e registra as providências do DP.", CONSULTA: "Consulta cadastros e indicadores, sem alterar registros." };
const populationLabels = { ALL: "Todos os funcionários", EXTERNAL: "Somente funcionários externos", INTERNAL: "Somente funcionários internos" };
const groups = [
  ["Cadastros e documentos", ["registry.", "documents."]],
  ["Operações de DP", ["absences.", "extensions.", "rescissions.", "communications."]],
  ["Indicadores e dados", ["analytics.", "sensitive."]],
  ["Administração", ["access.", "integrations."]],
];
const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const equalPermissions = (left, right) => [...left].sort().join("|") === [...right].sort().join("|");

export default function PermissionsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState({ profile: "CONSULTA", population: "EXTERNAL", permissions: [] });
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const editor = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    fetch("/api/access", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
      .then(async response => { const payload = await response.json().catch(() => null); if (!response.ok) throw new Error(payload?.error || "Seu perfil não permite administrar as contas."); return payload; })
      .then(payload => { if (!controller.signal.aborted) setData(payload); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause.name === "TimeoutError" ? "O serviço demorou para responder. Tente novamente." : cause.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);
  const selected = data?.users?.find(user => user.id === selectedId);
  const ownAccount = Boolean(selected && selected.id === data?.userId);
  const dirty = Boolean(selected && (form.profile !== selected.accessProfile || form.population !== selected.accessScope || !equalPermissions(form.permissions, selected.permissions || [])));
  function choose(user) {
    if (busy) return;
    if (dirty && !window.confirm("As alterações desta conta ainda não foram salvas. Deseja descartá-las?")) return;
    setSelectedId(user.id); setForm({ profile: user.accessProfile, population: user.accessScope, permissions: [...(user.permissions || [])] });
    setError(""); setNotice(""); requestAnimationFrame(() => editor.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
  function changeProfile(profile) {
    const permissions = [...(data?.defaults?.[profile] || [])];
    setForm(current => ({ ...current, profile, population: profile === "ADMIN" ? "ALL" : current.population, permissions }));
  }
  function changePermission(key, checked) {
    setForm(current => ({ ...current, permissions: checked ? [...new Set([...current.permissions, key])] : current.permissions.filter(value => value !== key) }));
  }
  async function save(event) {
    event.preventDefault(); if (submitting.current || !selected || !dirty) return;
    submitting.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/access", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-CSRF-Token": data.csrf }, body: JSON.stringify({ csrf: data.csrf, userId: selected.id, ...form }) });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error || "Não foi possível salvar as permissões.");
      setData(payload); const saved = payload.users?.find(user => user.id === selected.id);
      if (saved) setForm({ profile: saved.accessProfile, population: saved.accessScope, permissions: [...saved.permissions] });
      setNotice(payload.message || "Permissões salvas. Os próximos acessos usarão esta configuração.");
    } catch (cause) { setError(cause.message); }
    finally { submitting.current = false; setBusy(false); }
  }
  const users = (data?.users || []).filter(user => normalize(`${user.name} ${user.email}`).includes(normalize(search)));
  return <section className="page-content permissions-page">
    <header className="permissions-intro"><div><p>Defina o que cada pessoa da equipe pode consultar e operar.</p><small>Funcionários internos: empresa {data?.internalCompanyCodes?.join(", ") || "1"}. O recorte usa a empresa tomadora do cadastro.</small></div><span className="permissions-admin-badge">Acesso administrativo</span></header>
    {error && <div className="permissions-message is-error" role="alert">{error}{!data && <button type="button" onClick={() => setReload(value => value + 1)} disabled={loading}>Tentar novamente</button>}</div>}
    {notice && <div className="permissions-message is-success" role="status">{notice}</div>}
    {loading && <p className="permissions-loading" role="status">Carregando contas e permissões…</p>}
    {data && <>
      <section className="permissions-accounts"><div className="permissions-section-heading"><div><span className="permissions-kicker">EQUIPE</span><h2>Contas cadastradas <span>{data.users?.length || 0}</span></h2></div><label>Buscar conta<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nome ou e-mail" maxLength={160}/></label></div>
        <div className="permissions-accounts-list">{users.map(user => <button type="button" key={user.id} disabled={busy} className={`permissions-account${selectedId === user.id ? " is-selected" : ""}`} onClick={() => choose(user)} aria-pressed={selectedId === user.id}>
          <span className="permissions-avatar" aria-hidden="true">{user.name?.trim().slice(0, 1)?.toUpperCase() || "U"}</span><span className="permissions-account-name"><strong>{user.name}{user.id === data.userId ? " · você" : ""}</strong><small>{user.email}</small></span><span className="permissions-account-profile">{profileLabels[user.accessProfile] || user.accessProfile}<small>{populationLabels[user.accessScope]}</small></span><span className={`permissions-account-state ${user.active ? "is-active" : ""}`}>{user.active ? "Ativa" : "Inativa"}</span><span className="permissions-account-arrow" aria-hidden="true">→</span>
        </button>)}{!users.length && <p className="permissions-empty">Nenhuma conta corresponde à busca.</p>}</div>
      </section>
      {selected && <form className="permissions-editor" ref={editor} onSubmit={save}>
        <div className="permissions-editor-heading"><div><span className="permissions-kicker">CONFIGURAR ACESSO</span><h2>{selected.name}</h2><p>{selected.email}</p></div><span className={`permissions-changes${dirty ? " is-dirty" : ""}`}>{dirty ? "Alterações não salvas" : "Configuração atual"}</span></div>
        {!selected.active && <p className="permissions-inactive-note">Esta conta está inativa. A configuração pode ser consultada; a alteração exige uma conta ativa.</p>}
        <fieldset disabled={busy || !selected.active} className="permissions-main-fields"><label>Perfil<select value={form.profile} disabled={ownAccount} onChange={event => changeProfile(event.target.value)}>{(data.profiles || []).map(profile => <option key={profile} value={profile}>{profileLabels[profile] || profile}</option>)}</select><small>{profileDescriptions[form.profile]}</small></label>
          <label>Quais funcionários pode acessar<select value={form.population} disabled={ownAccount || form.profile === "ADMIN"} onChange={event => setForm(current => ({ ...current, population: event.target.value }))}>{Object.entries(populationLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><small>{form.population === "ALL" ? "Cadastros internos e externos autorizados pelo perfil." : form.population === "INTERNAL" ? "Somente a empresa interna configurada acima." : "Empresas externas; funcionários internos ficam fora deste recorte."}</small></label>
        </fieldset>
        <div className="permissions-operation-heading"><div><h3>Permissões por operação</h3><p>Ao trocar o perfil, a seleção volta ao padrão desse perfil. Ajuste as exceções abaixo.</p></div><button type="button" disabled={busy || !selected.active} onClick={() => changeProfile(form.profile)}>Restaurar padrão do perfil</button></div>
        <div className="permissions-groups">{groups.map(([title, prefixes]) => { const options = Object.entries(data.permissionOptions || {}).filter(([key]) => prefixes.some(prefix => key.startsWith(prefix))); const checked = options.filter(([key]) => form.permissions.includes(key)).length; return <fieldset key={title} disabled={busy || !selected.active}><legend>{title}<span>{checked} de {options.length}</span></legend>{options.map(([key, label]) => <label key={key} className="permissions-checkbox"><input type="checkbox" checked={form.permissions.includes(key)} disabled={(ownAccount && key === "access.manage") || (form.profile !== "ADMIN" && ["access.manage", "integrations.manage"].includes(key))} onChange={event => changePermission(key, event.target.checked)}/><span>{label}</span></label>)}</fieldset>; })}</div>
        {ownAccount && <p className="permissions-own-note">Sua conta mantém o perfil administrador, acesso a todos os funcionários e gestão de permissões.</p>}
        <footer className="permissions-save"><span>{form.permissions.length} permissões selecionadas</span><button className="btn btn-primary" disabled={busy || !dirty || !selected.active}>{busy ? "Salvando…" : "Salvar permissões"}</button></footer>
      </form>}
      {!selected && <div className="permissions-choose"><span aria-hidden="true">↗</span><p>Selecione uma conta para revisar o perfil, o recorte e as operações permitidas.</p></div>}
    </>}
  </section>;
}
