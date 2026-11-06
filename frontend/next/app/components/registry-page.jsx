"use client";
import { AbsenceRecords } from "./absences-page";

import { useEffect, useRef, useState } from "react";
import SibIssuesPanel from "./sib-issues-panel";
import SibOccurrences from "./sib-occurrences";

const sections = [
  ["pessoas", "Pessoas"],
  ["vinculos", "Vínculos"],
  ["empresas", "Empresas"],
  ["conflitos", "Conflitos"],
];
const sectionDescriptions = {
  pessoas: "Consulte cada pessoa e reúna seus dados de contato e histórico de trabalho.",
  vinculos: "Localize matrículas e confira situação, empresa e histórico de cada vínculo.",
  empresas: "Consulte as empresas importadas e veja o quadro de vínculos associado.",
  conflitos: "Revise inconsistências e pendências da integração antes de registrar uma decisão.",
};
const personStatuses = [
  ["all", "Todas"], ["Ativo", "Ativas"], ["Desligado", "Desligadas"],
  ["Admissão futura", "Admissões futuras"], ["Situação a conferir", "A conferir"],
];
const employmentStatuses = [
  ["all", "Todos"], ["Ativo", "Ativos"], ["Desligado", "Desligados"],
  ["Admissão futura", "Admissões futuras"], ["Sem saída registrada", "Sem saída registrada"],
  ["Situação a conferir", "A conferir"],
];
const conflictStatuses = [
  ["all", "Todas"], ["PENDING", "Pendentes"], ["REVIEWED", "Revisados"],
  ["RESOLVED", "Resolvidos"], ["REOPENED", "Reabertos"],
];
const conflictLabels = { PENDING: "Pendente", REVIEWED: "Revisado", RESOLVED: "Resolvido", REOPENED: "Reaberto" };
const profileFields = [
  ["legalName", "Razão social", 200], ["cnpj", "CNPJ", 18], ["postalCode", "CEP", 9],
  ["address", "Endereço", 300], ["addressNumber", "Número", 30], ["addressComplement", "Complemento", 200],
  ["neighborhood", "Bairro", 160], ["city", "Cidade", 160], ["state", "Estado (UF)", 2],
  ["payrollDay", "Dia de pagamento", 30], ["advanceDay", "Dia do adiantamento", 30],
  ["pointStart", "Início do fechamento do ponto", 2], ["pointEnd", "Fim do fechamento do ponto", 2],
];
const contractOptions = [["CLT", "CLT"], ["Temporário", "Temporário"], ["Prazo determinado", "Prazo determinado"], ["Estágio", "Estágio"], ["Outro", "Outro"]];
const reasonOptions = [["PEDIDO_EMPREGADO", "A pedido do empregado"], ["SEM_JUSTA_CAUSA", "Dispensa sem justa causa"], ["JUSTA_CAUSA", "Dispensa por justa causa"], ["TERMINO_CONTRATO", "Término de contrato"], ["ACORDO", "Rescisão por acordo"], ["FALECIMENTO", "Falecimento"], ["OUTRO", "Outro"]];
const numberLabel = value => Number(value || 0).toLocaleString("pt-BR");
const plainClick = event => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

function initialSearch(query) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    for (const item of Array.isArray(value) ? value : [value]) if (item != null) params.append(key, String(item));
  }
  return params;
}

function hrefFor(view, params, changes = {}) {
  const next = new URLSearchParams(params);
  for (const [key, value] of Object.entries(changes)) {
    if (value == null || value === "") next.delete(key);
    else next.set(key, String(value));
  }
  const query = next.toString();
  return `/cadastros/${view}${query ? `?${query}` : ""}`;
}

function dateLabel(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "—";
  return `${value.slice(8, 10)}/${value.slice(5, 7)}/${value.slice(0, 4)}`;
}

function statusTone(value) {
  if (["Ativo", "Com funcionários ativos", "with-active", "RESOLVED"].includes(value)) return "is-positive";
  if (["Desligado", "without-active", "REOPENED"].includes(value)) return "is-muted";
  if (["PENDING", "Situação a conferir", "Sem saída registrada", "unknown"].includes(value)) return "is-warning";
  return "is-neutral";
}

function statusLabel(value) {
  return ({ Ativo: "Ativa", Desligado: "Desligada", "Admissão futura": "Admissão futura", "Situação a conferir": "A conferir", "Sem saída registrada": "Sem saída registrada", "with-active": "Com funcionários ativos", "without-active": "Sem funcionários ativos", unknown: "Situação a conferir", ...conflictLabels })[value] || value || "—";
}

function InfoGrid({ items }) {
  return <dl className="registry-info-grid">{items.filter(([, value]) => value !== undefined).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value == null || value === "" ? "—" : value}</dd></div>)}</dl>;
}

function CategoryGroups({ groups }) {
  if (!groups?.length) return null;
  return <div className="registry-detail-groups">{groups.map((group, index) => <details className="registry-detail-group" key={group.title} open={index < 2}>
    <summary><span>{group.title}</span><small>{group.fields.length} campo(s)</small></summary>
    <dl className="registry-field-grid">{group.fields.map((field, fieldIndex) => <div key={`${field.path}-${fieldIndex}`} title={field.path}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
  </details>)}</div>;
}

function ActionForm({ data, target, onSaved, children, className = "", submitLabel = "Salvar" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  async function submit(event) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true); setError("");
    const fields = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      const response = await fetch("/api/ui/registry-actions", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf, target, fields }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(result?.error || "Não foi possível salvar a alteração.");
      if (result?.redirect) window.location.assign(result.redirect);
      else if (onSaved) await onSaved(result);
    } catch (cause) { setError(cause.message); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <form className={`registry-action-form ${className}`} onSubmit={submit}>
    {children}
    {error && <p className="registry-inline-error" role="alert">{error}</p>}
    <button className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : submitLabel}</button>
  </form>;
}

function Field({ label, name, defaultValue = "", type = "text", required = false, maxLength, placeholder, children }) {
  return <label className="registry-form-field">{label}
    {children || <input className="form-control" name={name} type={type} defaultValue={defaultValue} required={required} maxLength={maxLength} placeholder={placeholder} />}
  </label>;
}

function SelectField({ label, name, options, required = false, firstLabel, defaultValue = "" }) {
  return <label className="registry-form-field">{label}<select className="form-select" name={name} defaultValue={defaultValue} required={required}>
    {firstLabel && <option value="">{firstLabel}</option>}{options.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
  </select></label>;
}

function History({ items }) {
  if (!items?.length) return null;
  return <section className="registry-history"><h3>Histórico de decisões</h3><ul>{items.map((item, index) => <li key={`${item.createdAt}-${index}`}><strong>{item.value}</strong><span>{item.actor} · {item.createdAt}</span><p>{item.note}</p></li>)}</ul></section>;
}

function ScanNotice({ data }) {
  const scan = data?.scan;
  if (!data?.canManage || !scan) return null;
  const active = scan.running || scan.autoResume;
  const label = scan.retrying ? "SIB reconectando" : active ? "Importando cadastros do SIB" : scan.inFlight ? "Finalizando consulta SIB" : scan.started && !scan.done ? "Importação SIB pausada" : scan.done ? "Importação SIB concluída" : "Integração SIB disponível";
  return <section className={`registry-scan-notice ${active ? "is-running" : ""}`} aria-label="Situação da importação SIB">
    <div className="registry-scan-summary"><span className="registry-scan-dot" aria-hidden="true" /><div><strong>{label}</strong><p>{numberLabel(scan.records)} fichas disponíveis para consulta</p></div></div>
    <div className="registry-scan-actions"><a href="/central-dp/integracoes#sib-scan">Acompanhar integração <span aria-hidden="true">→</span></a></div>
  </section>;
}

function EmploymentTiles({ person, links }) {
  if (!links?.length) return <div className="registry-empty-note">Nenhum vínculo associado.</div>;
  return <div className="registry-employment-list">{links.map(item => <a className="registry-employment-card" key={item.key} href={hrefFor("pessoas", new URLSearchParams(), { id: item.key, status: "all" })}>
    <span className="registry-employment-code">{item.registrationCode || item.registration || "Sem matrícula"}</span>
    <div><strong>{item.company || "Empresa não informada"}</strong><span>{item.contractType || "Contrato não classificado"} · admissão {dateLabel(item.admissionDate)}</span></div>
    <span className={`registry-status ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
  </a>)}</div>;
}

function DossierDocuments({ data, detail, onUpdated }) {
  const [uploadOpen, setUploadOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(null);
  const activeRequest = useRef(false);
  if (!data.canViewDocuments) return null;
  const documents = detail.documents || [];
  const active = documents.filter(item => !item.archived_at);
  const archived = documents.filter(item => item.archived_at);
  const visible = showArchived ? documents : active;
  const owner = detail.documentOwner;
  const types = Object.entries(data.documentTypes || {});
  async function upload(event) {
    event.preventDefault();
    if (activeRequest.current) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const files = values.getAll("files").filter(file => file?.size);
    if (!files.length) { setError("Selecione ao menos um arquivo."); return; }
    if (files.length > 10 || files.some(file => file.size > 10 * 1024 * 1024)) { setError("Escolha até 10 arquivos de no máximo 10 MB cada."); return; }
    activeRequest.current = true; setBusy(true); setError(""); setNotice("");
    let saved = 0;
    try {
      for (const file of files) {
        const query = new URLSearchParams({ view: owner.view, id: owner.key });
        const response = await fetch(`/api/ui/registry-documents?${query}`, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { "Content-Type": file.type || "application/octet-stream", "X-CSRF-Token": data.csrf,
            "X-File-Name": encodeURIComponent(file.name), "X-Document-Type": values.get("documentType") || "OUTRO",
            "X-Document-Description": encodeURIComponent(values.get("description") || ""), "X-Document-Date": values.get("documentDate") || "" },
          body: file,
        });
        const result = await response.json().catch(() => null);
        if (!response.ok) throw new Error(result?.error || `Não foi possível anexar ${file.name}.`);
        saved += 1;
      }
      setNotice(`${saved} ${saved === 1 ? "arquivo salvo" : "arquivos salvos"} no prontuário. Arquivos idênticos são reutilizados.`);
      form.reset(); setUploadOpen(false);
    } catch (cause) { setError(`${saved ? `${saved} arquivo(s) já salvo(s). ` : ""}${cause.message}`); }
    finally { if (saved) await onUpdated?.(); activeRequest.current = false; setBusy(false); }
  }
  async function action(payload) {
    if (activeRequest.current) return;
    activeRequest.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ui/registry-documents/actions", { method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": data.csrf }, body: JSON.stringify({ ...payload, csrf: data.csrf }) });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(result?.error || "Não foi possível alterar o documento.");
      setEditing(null); setNotice(result.message || "Alteração registrada."); await onUpdated?.();
    } catch (cause) { setError(cause.message); }
    finally { activeRequest.current = false; setBusy(false); }
  }
  return <section className="registry-dossier" id="registry-dossier-documents">
    <div className="registry-dossier-heading"><div><span className="registry-kicker">PRONTUÁRIO DIGITAL</span><h3>Documentos <span>{active.length}</span></h3><p>Arquivos do cadastro selecionado, com histórico e acesso privado.</p></div>
      {data.canUploadDocuments && owner && <button type="button" className="btn btn-primary" aria-expanded={uploadOpen} disabled={busy} onClick={() => { setUploadOpen(value => !value); setError(""); }}>＋ Anexar documentos</button>}
    </div>
    {detail.documentOwnerMessage && <p className="registry-empty-note">{detail.documentOwnerMessage}</p>}
    {uploadOpen && owner && <form className="registry-document-upload registry-form-grid" onSubmit={upload}>
      <label className="registry-form-field registry-document-files">Arquivos<input name="files" type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg" multiple required disabled={busy}/><small>PDF, Word ou imagem · até 10 arquivos por envio · 10 MB por arquivo</small></label>
      <SelectField label="Tipo do documento" name="documentType" options={types} defaultValue="OUTRO" required />
      <Field label="Data do documento" name="documentDate" type="date" />
      <label className="registry-form-field registry-document-description">Descrição<input className="form-control" name="description" maxLength={500} placeholder="Ex.: contrato inicial, comprovante ou documento complementar" /></label>
      <div className="registry-document-form-actions"><button className="btn btn-primary" disabled={busy}>{busy ? "Salvando documentos…" : "Salvar no prontuário"}</button><button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={() => setUploadOpen(false)}>Cancelar</button></div>
    </form>}
    {error && <p className="registry-inline-error" role="alert">{error}</p>}{notice && <p className="registry-document-notice" role="status">{notice}</p>}
    {archived.length > 0 && <label className="registry-document-archived-toggle"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)}/> Mostrar arquivados ({archived.length})</label>}
    <div className="registry-document-list">{visible.map(item => <article key={item.id} className={`registry-document-card${item.archived_at ? " is-archived" : ""}`}>
      <span className="registry-document-symbol" aria-hidden="true">▤</span><div className="registry-document-copy"><a href={item.downloadUrl || `/api/ui/person-documents/${item.id}`}>{item.filename}</a><p>{item.description || item.documentTypeLabel || "Documento do prontuário"}</p><small>{item.documentTypeLabel || "Outro documento"} · {item.document_date ? `${dateLabel(item.document_date)} · ` : ""}{Math.max(1, Math.round(item.size_bytes / 1024))} KB{item.archived_at ? " · Arquivado" : ""}</small></div>
      {data.canUploadDocuments && <div className="registry-document-actions"><button type="button" disabled={busy} onClick={() => setEditing(editing === item.id ? null : item.id)}>Organizar</button><button type="button" disabled={busy} onClick={() => {
        if (item.archived_at) action({ action: "restore", documentId: item.id });
        else { const reason = window.prompt("Justificativa para arquivar este documento (o arquivo e o histórico serão preservados):"); if (reason) action({ action: "archive", documentId: item.id, reason }); }
      }}>{item.archived_at ? "Restaurar" : "Arquivar"}</button></div>}
      {editing === item.id && <form className="registry-document-edit registry-form-grid" onSubmit={event => { event.preventDefault(); action({ ...Object.fromEntries(new FormData(event.currentTarget).entries()), action: "metadata", documentId: item.id }); }}>
        <SelectField label="Tipo" name="documentType" options={types} defaultValue={item.document_type || "OUTRO"} /><Field label="Data" name="documentDate" type="date" defaultValue={item.document_date || ""}/><Field label="Descrição" name="description" defaultValue={item.description || ""} maxLength={500}/><div className="registry-document-form-actions"><button className="btn btn-primary" disabled={busy}>Salvar organização</button><button type="button" className="btn btn-outline-secondary" onClick={() => setEditing(null)}>Cancelar</button></div>
      </form>}
    </article>)}</div>
    {!visible.length && <div className="registry-dossier-empty"><span aria-hidden="true">▤</span><div><strong>Nenhum documento anexado</strong><p>{owner && data.canUploadDocuments ? "Use Anexar documentos para guardar os arquivos deste cadastro." : "Os documentos vinculados a esta matrícula aparecerão aqui."}</p></div></div>}
    {detail.documentHistory?.length > 0 && <details className="registry-document-history"><summary>Histórico dos documentos</summary><ul>{detail.documentHistory.map((item, index) => <li key={`${item.createdAt}-${index}`}><strong>{({ ANEXADO: "Documento anexado", ARQUIVADO: "Documento arquivado", RESTAURADO: "Documento restaurado", RESTAURADO_POR_REIMPORTACAO: "Documento reutilizado", METADADOS_ALTERADOS: "Organização alterada" })[item.action] || item.action}</strong><span>{item.filename} · {item.actor} · {item.createdAt}</span>{item.details?.reason && <p>{item.details.reason}</p>}</li>)}</ul></details>}
  </section>;
}

function SectorHistory({ items }) {
  if (!items?.length) return null;
  return <details className="registry-action registry-source-detail">
    <summary>Histórico da planilha do setor ({items.length})</summary>
    <p>Informações anteriores preservadas para consulta. A situação atual do cadastro continua sendo a recebida do SIB.</p>
    <ul>{items.map(item => <li key={item.id}><a className="registry-related-link" href={item.href}>{item.title} →</a></li>)}</ul>
  </details>;
}

function PersonDetail({ data, detail, onUpdated }) {
  const createEmployment = <details className="registry-action"><summary>Novo vínculo / recontratação</summary>
    <p>Uma recontratação cria outro vínculo e preserva o histórico anterior da pessoa.</p>
    <ActionForm data={data} target="/cadastros/vinculos" className="registry-form-grid">
      <input type="hidden" name="person_key" value={detail.key} />
      <Field label="Matrícula" name="registration" required maxLength={80} />
      <Field label="Cliente / empresa" name="company" required maxLength={160} />
      <Field label="Unidade" name="unit" maxLength={160} />
      <SelectField label="Tipo de contrato" name="contract_type" options={contractOptions} required />
      <Field label="Admissão" name="admission_date" type="date" required />
      <Field label="1º vencimento temporário" name="first_due_date" type="date" />
      <Field label="2º vencimento temporário" name="second_due_date" type="date" />
      <Field label="Saída / demissão" name="termination_date" type="date" />
      <SelectField label="Motivo" name="dismissal_reason_code" options={reasonOptions} firstLabel="Não desligado" />
      <Field label="Detalhe do motivo" name="dismissal_reason" maxLength={160} />
    </ActionForm>
  </details>;
  return <>
    <div className="registry-detail-heading"><div><span className="registry-kicker">Histórico da pessoa</span><h2>{detail.name}</h2><p>{detail.code}</p></div><span className={`registry-status ${statusTone(detail.status)}`}>{statusLabel(detail.status)}</span></div>
    <InfoGrid items={[["CPF", detail.cpf || "Não informado"], ["E-mail", detail.email || "Não informado"], ["WhatsApp", detail.whatsapp || "Não informado"], ["Identificação", detail.isTest ? "Cadastro de teste" : detail.verified ? "Conferida" : "Provisória"], ["Vínculos", detail.employmentCount]]} />
    <div className="registry-detail-section-head"><h3>Vínculos e histórico</h3></div>
    <EmploymentTiles person={detail} links={detail.employments} />
    <AbsenceRecords value={detail.absences} />
    {detail.employments?.flatMap(item => item.categories?.length ? [<section key={`src-${item.key}`} className="registry-source-detail"><h3>Detalhes da origem SIB · {item.registrationCode || item.registration}</h3><CategoryGroups groups={item.categories} /></section>] : [])}
    <DossierDocuments data={data} detail={detail} onUpdated={onUpdated}/>
    <SectorHistory items={detail.sectorHistory}/>
    {data.canManage && <>
      {createEmployment}
      <details className="registry-action"><summary>Corrigir nome com justificativa</summary><ActionForm data={data} target="/cadastros/revisoes" className="registry-form-grid">
        <input type="hidden" name="kind" value="PERSON_NAME" /><input type="hidden" name="subject_key" value={detail.key} />
        <Field label="Nome" name="value" defaultValue={detail.name} required maxLength={160} /><Field label="Justificativa" name="note" required maxLength={1000} />
      </ActionForm></details>
    </>}
    <History items={detail.history} />
  </>;
}

function EmploymentDetail({ data, detail, onUpdated }) {
  const sourceField = (description, code) => description || (code ? `Código ${code} · descrição não recebida` : detail.source === "SIB" ? "Não informado pelo SIB" : "");
  return <>
    <div className="registry-detail-heading"><div><span className="registry-kicker">Cadastro por matrícula · {detail.source}</span><h2>{detail.name}</h2><p>Matrícula {detail.registrationCode || detail.registration || "não informada"}</p></div><span className={`registry-status ${statusTone(detail.status)}`}>{statusLabel(detail.status)}</span></div>
    <InfoGrid items={[["CPF", detail.cpf || "Não informado"], ["E-mail", detail.email || "Não informado"], ["WhatsApp", detail.whatsapp || "Não informado"], ["Empresa", detail.company], ["Contrato", sourceField(detail.contractType, detail.contractTypeCode)], ["Admissão", dateLabel(detail.admissionDate)], ["Desligamento", dateLabel(detail.terminationDate)], ["1º vencimento", dateLabel(detail.firstDueDate)], ["2º vencimento", dateLabel(detail.secondDueDate)], ["Departamento", sourceField(detail.department, detail.departmentCode)], ["Cargo", sourceField(detail.jobTitle, detail.jobCode)], ["Fonte", detail.source]]} />
    {detail.terminationDate && <section className="registry-dismissal-reason"><h3>Motivo do desligamento</h3><p>{detail.dismissalReason || (detail.dismissalReasonCode ? `Código ${detail.dismissalReasonCode} · descrição não recebida` : detail.source === "SIB" ? "O SIB não retornou o motivo deste desligamento." : "Motivo não informado.")}</p>{detail.dismissalReason && detail.dismissalReasonCode && <small>Código da origem: {detail.dismissalReasonCode}</small>}</section>}
    <a className="registry-related-link" href={hrefFor("vinculos", new URLSearchParams(), { person: detail.personKey, id: detail.personKey })}>Abrir histórico completo na aba Vínculos →</a>
    <AbsenceRecords value={detail.absences} />
    {detail.categories?.length > 0 && <section className="registry-source-detail"><h3>Dados retornados pelo SIB</h3><CategoryGroups groups={detail.categories} /></section>}
    <DossierDocuments data={data} detail={detail} onUpdated={onUpdated}/>
    <SectorHistory items={detail.sectorHistory}/>
    {data.canManage && detail.peopleOptions?.length > 0 && <details className="registry-action"><summary>Associar a outra pessoa</summary><p>Confira a identidade antes de associar. O registro da origem continua preservado.</p>
      <ActionForm data={data} target="/cadastros/revisoes" className="registry-form-grid">
        <input type="hidden" name="kind" value="LINK_EMPLOYMENT" /><input type="hidden" name="subject_key" value={detail.key} />
        <SelectField label="Pessoa" name="value" options={detail.peopleOptions.map(item => [item.key, `${item.name} · ${item.company}`])} firstLabel="Selecione" required />
        <Field label="Justificativa" name="note" maxLength={1000} required />
      </ActionForm>
    </details>}
    <History items={detail.history} />
  </>;
}

function CompanyDetail({ data, detail, onUpdated }) {
  const groupLabel = data.groupLabels?.[detail.group] || statusLabel(detail.group);
  return <>
    <div className="registry-detail-heading"><div><span className="registry-kicker">Empresa · {detail.source}</span><h2>{detail.name}</h2><p>{detail.code || "Sem código"}</p></div><span className={`registry-status ${statusTone(detail.group)}`}>{groupLabel}</span></div>
    <InfoGrid items={[["Vínculos ativos", detail.headcount], ["Vínculos consultados", detail.total], ["Situação a conferir", detail.unknownHeadcount], ["Ficha", detail.profileSource]]} />
    <InfoGrid items={[["Razão social", detail.profile?.legalName || detail.name], ["CNPJ", detail.profile?.cnpj], ["Cidade / UF", [detail.profile?.city, detail.profile?.state].filter(Boolean).join(" / ")], ["Endereço", [detail.profile?.address, detail.profile?.addressNumber].filter(Boolean).join(", ")]]}/>
    <p className="registry-help-text">A situação usa os vínculos trazidos até agora. A ficha de origem é exibida sem alterar os dados do Bitti.</p>
    <a className="registry-related-link" href={hrefFor("vinculos", new URLSearchParams(), { q: detail.name })}>Consultar vínculos desta empresa →</a>
    <CategoryGroups groups={detail.categories} />
    <DossierDocuments data={data} detail={detail} onUpdated={onUpdated}/>
    <SectorHistory items={detail.sectorHistory}/>
    {data.canManage && <details className="registry-action"><summary>Complementar ficha local</summary><p>Os campos vazios continuam preservados quando você salvar esta ficha.</p>
      <ActionForm data={data} target="/cadastros/empresas/ficha" className="registry-form-grid">
        <input type="hidden" name="client_key" value={detail.key} />
        {profileFields.map(([key, label, maxLength]) => <Field key={key} label={label} name={key} defaultValue={detail.profile?.[key] || ""} maxLength={maxLength} />)}
      </ActionForm>
    </details>}
  </>;
}

function ConflictDetail({ data, detail }) {
  return <>
    <div className="registry-detail-heading"><div><span className="registry-kicker">Conferência de dados</span><h2>{detail.kind}</h2></div><span className={`registry-status ${statusTone(detail.status)}`}>{statusLabel(detail.status)}</span></div>
    <p className="registry-conflict-description">{detail.description}</p>
    {data.canManage && <details className="registry-action" open><summary>Registrar decisão</summary>
      <ActionForm data={data} target="/cadastros/revisoes" className="registry-form-grid">
        <input type="hidden" name="kind" value="CONFLICT_REVIEW" /><input type="hidden" name="subject_key" value={detail.key} />
        <SelectField label="Decisão" name="value" options={conflictStatuses.slice(1).map(([value, label]) => [value, label])} defaultValue={detail.status} />
        <Field label="Justificativa" name="note" required maxLength={1000} />
      </ActionForm>
    </details>}
    <History items={detail.history} />
  </>;
}

function RecordCard({ view, record, params, selected, onSelect, disabled = false }) {
  const key = record.key;
  return <a className={`registry-record ${selected ? "is-selected" : ""}`} href={hrefFor(view, params, { id: key })} aria-current={selected ? "page" : undefined} aria-disabled={disabled || undefined} tabIndex={disabled ? -1 : undefined}
    onClick={event => {
      if (disabled) { event.preventDefault(); return; }
      if (!plainClick(event)) return;
      event.preventDefault();
      onSelect(key);
    }}>
    <span className="registry-record-code" title={`${view === "empresas" ? "Código" : "Matrícula"} ${record.code}`}>{record.code}</span>
    <div className="registry-record-copy"><strong title={record.name}>{record.name}</strong>
      {view === "vinculos" && <span>{record.employmentCount} vínculo(s) · {record.activeCount} ativo(s) · {record.source}</span>}
      {view === "empresas" && <span>{record.headcountLabel} · {record.source}</span>}
      {view === "pessoas" && <span title={[record.company, record.source, record.contractType].filter(Boolean).join(" · ")}>{record.company ? `${record.company} · ` : ""}{record.source}{record.contractType ? ` · ${record.contractType}` : ""}</span>}
      {view === "conflitos" && <span>{record.detail}</span>}
    </div>
    <span className={`registry-status ${statusTone(view === "empresas" ? record.group : view === "conflitos" ? record.status : record.status)}`}>{statusLabel(view === "empresas" ? record.group : record.status)}</span>
  </a>;
}

async function loadRegistry(url, signal, onRetry = () => {}, detail = false) {
  const delays = [350, 1000];
  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await fetch(url, {
        credentials: "same-origin", cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || (detail ? !payload?.detail : !Array.isArray(payload?.records))) {
        const cause = new Error(payload?.error || (detail ? "Este cadastro não está disponível. Atualize a lista para conferir." : `Não foi possível carregar os cadastros (HTTP ${response.status}).`));
        cause.retryable = [408, 429, 500, 502, 503, 504].includes(response.status) || (!detail && response.ok);
        throw cause;
      }
      return payload;
    } catch (cause) {
      if (signal.aborted) throw cause;
      const retryable = cause.retryable || cause instanceof TypeError || cause.name === "TimeoutError";
      if (!retryable || attempt >= delays.length) {
        if (cause.name === "TimeoutError") throw new Error("O servidor demorou para responder. Tente novamente.");
        throw cause;
      }
      onRetry();
      await new Promise((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(signal.reason); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, delays[attempt]);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) { signal.removeEventListener("abort", abort); abort(); }
      });
    }
  }
}

export default function RegistryPage({ view, initialQuery = {} }) {
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [reload, setReload] = useState(0);
  const [navigation, setNavigation] = useState(() => initialSearch(initialQuery).toString());
  const [searchValue, setSearchValue] = useState(() => initialSearch(initialQuery).get("q") || "");
  const [detailRetry, setDetailRetry] = useState(0);
  const [liveStatus, setLiveStatus] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [detailState, setDetailState] = useState({ key: "", payload: null, loading: false, error: "" });
  const detailCache = useRef(new Map());
  const statusGeneration = useRef(0);
  const detailPanel = useRef(null);
  const focusDetail = useRef(false);
  const listScrollPosition = useRef(0);
  const params = new URLSearchParams(navigation);
  const selectedId = params.get("id") || "";
  const listParams = new URLSearchParams(params);
  listParams.delete("id");
  const queryString = listParams.toString();
  const requestKey = `${view}?${queryString}`;
  const data = snapshot?.payload?.view === view ? snapshot.payload : null;
  const currentSnapshot = snapshot?.key === requestKey;
  const listUnavailable = loading || !currentSnapshot;
  const refresh = () => { detailCache.current.clear(); setReload(value => value + 1); };
  const currentPath = `/cadastros/${view}`;
  function navigate(changes) {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value == null || value === "") next.delete(key); else next.set(key, String(value));
    }
    const query = next.toString();
    if (query === window.location.search.slice(1)) return;
    window.history.pushState(null, "", `${currentPath}${query ? `?${query}` : ""}`);
    setNavigation(query);
  }
  function selectRecord(key) {
    if (key === selectedId) return;
    if (key) listScrollPosition.current = window.scrollY;
    focusDetail.current = Boolean(key);
    navigate({ id: key });
    if (!key) requestAnimationFrame(() => window.scrollTo({ top: listScrollPosition.current, behavior: "auto" }));
  }
  function navigateLink(event, changes) {
    if (!plainClick(event)) return;
    event.preventDefault();
    navigate(changes);
  }
  function submitSearch(event) {
    event.preventDefault();
    if (searchValue.trim() === appliedQuery && (!params.get("page") || params.get("page") === "1") && !selectedId) refresh();
    else navigate({ q: searchValue.trim(), page: 1, id: "" });
  }
  function retryDetails() {
    detailCache.current.delete(`${view}:${selectedId}`);
    setDetailRetry(value => value + 1);
  }

  useEffect(() => {
    const syncNavigation = () => setNavigation(window.location.search.slice(1));
    window.addEventListener("popstate", syncNavigation);
    return () => window.removeEventListener("popstate", syncNavigation);
  }, []);
  const initialKey = initialSearch(initialQuery).toString();
  useEffect(() => {
    setNavigation(window.location.pathname === `/cadastros/${view}` ? window.location.search.slice(1) : initialKey);
    setCreateOpen(false);
  }, [view, initialKey]);
  const appliedQuery = params.get("q") || "";
  useEffect(() => { setSearchValue(appliedQuery); }, [appliedQuery]);

  useEffect(() => {
    const controller = new AbortController();
    statusGeneration.current += 1;
    setLoading(true); setError(""); setRetrying(false);
    const api = new URLSearchParams(queryString);
    api.set("view", view);
    // Strict Mode mounts effects twice in development. Defer the read so the
    // discarded mount does not send and cancel a second expensive projection.
    const timer = setTimeout(() => {
      loadRegistry(`/api/ui/registry?${api.toString()}`, controller.signal, () => setRetrying(true))
        .then(payload => { if (!controller.signal.aborted) { statusGeneration.current += 1; setSnapshot({ key: requestKey, payload }); setLiveStatus({ revision: payload.revision, scan: payload.scan }); } })
        .catch(cause => { if (!controller.signal.aborted) setError(cause.message || "Não foi possível conectar ao serviço de cadastros."); })
        .finally(() => { if (!controller.signal.aborted) { setLoading(false); setRetrying(false); } });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [view, queryString, requestKey, reload]);

  useEffect(() => {
    if (!data || view === "conflitos") return;
    const controller = new AbortController();
    let timer;
    async function poll() {
      const generation = statusGeneration.current;
      try {
        const response = await fetch("/api/ui/registry-status", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
        const payload = await response.json().catch(() => null);
        if (response.ok && payload?.revision && !controller.signal.aborted && generation === statusGeneration.current) setLiveStatus(payload);
      } catch { /* Keep the last status; list and detail requests report their own errors. */ }
      if (!controller.signal.aborted) timer = window.setTimeout(poll, document.hidden ? 45000 : 15000);
    }
    timer = window.setTimeout(poll, 15000);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [view, Boolean(data)]);

  useEffect(() => {
    if (!selectedId) {
      setDetailState({ key: "", payload: null, loading: false, error: "" });
      return;
    }
    const cacheKey = `${view}:${selectedId}`;
    const cached = detailCache.current.get(cacheKey);
    if (cached && cached.expires > Date.now()) {
      detailCache.current.delete(cacheKey);
      detailCache.current.set(cacheKey, cached);
      setDetailState({ key: selectedId, payload: cached.payload, loading: false, error: "" });
      return;
    }
    detailCache.current.delete(cacheKey);
    const controller = new AbortController();
    setDetailState({ key: selectedId, payload: null, loading: true, error: "" });
    const api = new URLSearchParams();
    api.set("view", view);
    api.set("id", selectedId);
    const timer = setTimeout(() => {
      loadRegistry(`/api/ui/registry-detail?${api.toString()}`, controller.signal, undefined, true).then(payload => {
        if (controller.signal.aborted) return;
        detailCache.current.set(cacheKey, { payload, expires: Date.now() + 60000 });
        while (detailCache.current.size > 40) detailCache.current.delete(detailCache.current.keys().next().value);
        setDetailState({ key: selectedId, payload, loading: false, error: "" });
      }).catch(cause => {
        if (!controller.signal.aborted) setDetailState({ key: selectedId, payload: null, loading: false, error: cause.message || "Não foi possível carregar os detalhes deste cadastro." });
      });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [view, selectedId, reload, detailRetry]);

  useEffect(() => {
    if (focusDetail.current && detailState.key === selectedId && !detailState.loading) {
      focusDetail.current = false;
      detailPanel.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    }
  }, [selectedId, detailState]);

  const statusOptions = view === "pessoas" || view === "vinculos" ? personStatuses : view === "conflitos" ? conflictStatuses : [];
  const activeFilter = currentSnapshot ? (view === "empresas" ? data?.group || "all" : data?.status || "all") : params.get(view === "empresas" ? "group" : "status") || "all";
  const filters = view === "empresas" ? Object.entries(data?.groupLabels || {}) : statusOptions;
  const counts = currentSnapshot ? (view === "empresas" ? data?.groupCounts : data?.statusCounts) : null;
  const heading = sections.find(([key]) => key === view)?.[1] || "Cadastros";
  const selectedDetail = detailState.key === selectedId ? detailState.payload : null;
  const activeDetail = detailState.key === selectedId ? detailState : { loading: Boolean(selectedId), error: "" };
  const hasNewData = Boolean(data?.revision && liveStatus?.revision && data.revision !== liveStatus.revision);
  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / (data?.pageSize || 50)));

  return <div className={`page-content registry-page${selectedId ? " is-reading" : ""}`}>
    <header className="registry-page-heading"><div><p>{sectionDescriptions[view] || "Consulte os cadastros e seus históricos relacionados."}</p></div>
      {view === "pessoas" && data?.canManage && <button className="btn btn-primary registry-add-button" type="button" onClick={() => setCreateOpen(value => !value)} aria-expanded={createOpen} aria-controls="registry-create-person"><span aria-hidden="true">＋</span> Adicionar cadastro</button>}
    </header>
    {data && view !== "conflitos" && <ScanNotice data={{ ...data, scan: liveStatus?.scan || data.scan }} />}
    {view === "conflitos" && data?.canManage && <SibIssuesPanel csrf={data.csrf} />}
    {view === "conflitos" && data?.canManage && <div className="card integrations-card"><SibOccurrences csrf={data.csrf}/></div>}
    {view === "pessoas" && data?.canManage && createOpen && <section className="registry-create-card" id="registry-create-person"><div className="registry-create-heading"><span><strong>Adicionar pessoa e vínculo</strong><small>Cadastro local da V3</small></span><button className="registry-close" type="button" onClick={() => setCreateOpen(false)} aria-label="Fechar formulário de cadastro">×</button></div>
      <p>O registro será criado localmente. Nenhum dado será gravado no SIB ou no Bitti.</p>
      <ActionForm data={data} target="/cadastros/pessoas" className="registry-form-grid">
        <h3 className="registry-form-section">Identificação e contato</h3>
        <Field label="Nome completo" name="name" required maxLength={160} placeholder="Ex.: Marina Costa" />
        <Field label="CPF" name="cpf" maxLength={14} placeholder="123.456.789-00" />
        <Field label="E-mail" name="email" type="email" maxLength={254} />
        <Field label="WhatsApp" name="whatsapp" maxLength={20} />
        <h3 className="registry-form-section">Vínculo de trabalho</h3>
        <Field label="Matrícula" name="registration" required maxLength={80} />
        <Field label="Empresa / cliente" name="company" required maxLength={160} />
        <Field label="Unidade" name="unit" maxLength={160} />
        <SelectField label="Tipo de contrato" name="contract_type" options={contractOptions} required />
        <Field label="Admissão" name="admission_date" type="date" required />
        <Field label="1º vencimento temporário" name="first_due_date" type="date" />
        <Field label="2º vencimento temporário" name="second_due_date" type="date" />
        <Field label="Saída / demissão" name="termination_date" type="date" />
        <SelectField label="Motivo" name="dismissal_reason_code" options={reasonOptions} firstLabel="Selecione se houver saída" />
        <Field label="Detalhe do motivo" name="dismissal_reason" maxLength={160} />
      </ActionForm>
    </section>}
    {view === "empresas" && data?.canManage && <details className="registry-create-card"><summary><span><strong>Adicionar empresa local</strong><small>Complemento do cadastro V3</small></span><span className="registry-create-plus">+</span></summary>
      <ActionForm data={data} target="/cadastros/empresas" className="registry-form-grid"><Field label="Nome" name="name" required maxLength={200} /><Field label="Código" name="code" maxLength={40} /></ActionForm>
    </details>}
    <section className="registry-toolbar" aria-label="Busca e filtros dos cadastros">
      <form className="registry-search-form" action={currentPath} method="get" onSubmit={submitSearch}>
        {view === "empresas" ? <input type="hidden" name="group" value={activeFilter} /> : <input type="hidden" name="status" value={activeFilter} />}
        {params.get("person") && <input type="hidden" name="person" value={params.get("person")} />}
        <label className="registry-search-field"><span>Buscar {view === "pessoas" ? "pessoa" : view === "vinculos" ? "vínculo" : view === "empresas" ? "empresa" : "pendência"}</span><input className="form-control" name="q" value={searchValue} onChange={event => setSearchValue(event.target.value)} maxLength={100} placeholder={view === "empresas" ? "Nome ou código da empresa" : view === "conflitos" ? "Tipo ou descrição" : "Nome, matrícula, empresa ou CPF"} /></label>
        <button className="btn btn-primary">Pesquisar</button>
      </form>
      <div className="registry-filter-row"><div className="registry-status-filters" aria-label="Filtrar por situação">
        {filters.map(([value, label]) => <a key={value} className={activeFilter === value ? "is-active" : ""} href={hrefFor(view, params, { [view === "empresas" ? "group" : "status"]: value, page: 1, id: "" })} onClick={event => navigateLink(event, { [view === "empresas" ? "group" : "status"]: value, page: 1, id: "" })} aria-current={activeFilter === value ? "page" : undefined}>
          {label}<strong>{counts ? numberLabel(counts[value]) : "…"}</strong>
        </a>)}
      </div>{(appliedQuery || activeFilter !== "all" || params.get("person")) && <a className="registry-clear-filters" href={currentPath} onClick={event => navigateLink(event, { q: "", status: "", group: "", person: "", page: "", id: "" })}>Limpar filtros</a>}
      </div>
      {params.get("person") && view === "vinculos" && <p className="registry-filter-context">Exibindo o histórico da pessoa selecionada.</p>}
    </section>
    {hasNewData && <div className="registry-update-notice" role="status"><span>Há atualizações na base. Atualize a lista quando terminar sua consulta.</span><button type="button" onClick={refresh} disabled={loading}>{loading ? "Atualizando…" : "Atualizar lista"}</button></div>}
    {error && <div className="registry-alert" role="alert"><strong>{data ? "Não foi possível atualizar a lista." : "Não foi possível carregar esta tela."}</strong><span>{error}{data ? " Os registros da última leitura continuam abaixo." : " Seus cadastros salvos foram preservados."}</span><button className="btn btn-outline-primary" onClick={refresh} disabled={loading}>Tentar novamente</button></div>}
    <div className={`registry-workspace${selectedId ? " has-selection" : ""}`}>
      {selectedId && <nav className="registry-record-navigation" aria-label="Navegação do cadastro"><button type="button" onClick={() => selectRecord("")}>← Voltar à lista de {heading.toLowerCase()}</button><span>{selectedDetail?.detail?.name || "Ficha do cadastro"}</span></nav>}
      <section hidden={Boolean(selectedId)} className={`registry-list-panel ${listUnavailable ? "is-loading" : ""}`} aria-label={`Lista de ${heading.toLowerCase()}`} aria-busy={loading}>
        <div className="registry-panel-heading"><div><span className="registry-kicker">{view === "pessoas" ? "UM CADASTRO POR MATRÍCULA" : "LISTA DE REGISTROS"}</span><h2>{view === "empresas" ? data?.groupLabels?.[data?.group] || heading : heading}</h2></div><div className="registry-list-actions"><span className="registry-total">{data ? `${numberLabel(data.total)} ${view === "pessoas" ? (data.total === 1 ? "cadastro" : "cadastros") : (data.total === 1 ? "registro" : "registros")}` : "—"}</span><button type="button" onClick={refresh} disabled={loading} aria-label="Atualizar lista" title="Atualizar lista">↻</button></div></div>
        {loading && <div className="registry-loading" role="status"><span className="registry-spinner" />{retrying ? "Reconectando para carregar os cadastros…" : data ? "Atualizando registros…" : "Carregando registros…"}</div>}
        {data?.records?.length ? <div className="registry-record-list">{data.records.map(record => <RecordCard key={record.key} view={view} record={record} params={params} selected={selectedId === record.key} onSelect={selectRecord} disabled={listUnavailable} />)}</div> : !loading && (data ? <div className="registry-empty"><strong>{data.query ? "Nenhum resultado para esta busca" : "Nenhum registro neste grupo"}</strong><p>Ajuste a busca ou escolha outra situação para conferir os cadastros.</p></div> : <div className="registry-empty"><strong>Lista ainda não carregada</strong><p>Tente carregar novamente para consultar os cadastros salvos.</p></div>)}
        {data && data.total > data.pageSize && <nav className="registry-pagination" aria-label="Páginas de registros">
          <button type="button" className="btn btn-outline-primary" disabled={listUnavailable || data.page <= 1} onClick={() => navigate({ page: data.page - 1 })}>Anterior</button>
          <form key={data.page} onSubmit={event => { event.preventDefault(); if (listUnavailable) return; const requested = Number(new FormData(event.currentTarget).get("page")); if (Number.isInteger(requested)) navigate({ page: Math.max(1, Math.min(pageCount, requested)) }); }}><label>Página <input aria-label="Ir para a página" name="page" type="number" min="1" max={pageCount} defaultValue={data.page} disabled={listUnavailable} /></label><span> de {numberLabel(pageCount)}</span><button type="submit" disabled={listUnavailable} aria-label="Ir para página informada">Ir</button></form>
          <button type="button" className="btn btn-outline-primary" disabled={listUnavailable || data.page >= pageCount} onClick={() => navigate({ page: data.page + 1 })}>Próxima</button>
        </nav>}
      </section>
      {selectedId && <section ref={detailPanel} className={`registry-detail-panel ${selectedId ? "has-detail" : "is-empty"}`} aria-label="Detalhes e histórico do cadastro" aria-busy={activeDetail.loading}>
        <div className="registry-panel-heading"><div><span className="registry-kicker">FICHA E HISTÓRICO</span><h2>{activeDetail.loading ? "Abrindo ficha" : selectedDetail?.detail ? "Detalhes do cadastro" : "Selecione um registro"}</h2></div>{selectedId && <button className="registry-close" type="button" onClick={() => selectRecord("")} aria-label="Fechar ficha">×</button>}</div>
        <div className="registry-detail-body" key={selectedId}>
          {activeDetail.loading && <div className="registry-loading" role="status"><span className="registry-spinner" />Carregando detalhes…</div>}
          {activeDetail.error && <div className="registry-empty registry-detail-empty" role="alert"><strong>Não foi possível abrir esta ficha</strong><p>{activeDetail.error}</p><button type="button" className="btn btn-outline-primary" onClick={retryDetails}>Tentar novamente</button></div>}
          {selectedDetail?.detail && (view === "pessoas" ? (selectedDetail.detail.kind === "person" ? <PersonDetail data={selectedDetail} detail={selectedDetail.detail} onUpdated={retryDetails} /> : <EmploymentDetail data={selectedDetail} detail={selectedDetail.detail} onUpdated={retryDetails} />) : view === "vinculos" ? <PersonDetail data={selectedDetail} detail={selectedDetail.detail} onUpdated={retryDetails} /> : view === "empresas" ? <CompanyDetail data={selectedDetail} detail={selectedDetail.detail} onUpdated={retryDetails} /> : <ConflictDetail data={selectedDetail} detail={selectedDetail.detail} />)}
          {!activeDetail.loading && !activeDetail.error && !selectedDetail?.detail && <div className="registry-empty registry-detail-empty"><span className="registry-detail-empty-icon" aria-hidden="true">↖</span><strong>{data ? `Escolha ${view === "pessoas" ? "uma pessoa" : view === "vinculos" ? "um vínculo" : view === "empresas" ? "uma empresa" : "uma pendência"} na lista` : "Aguardando os cadastros"}</strong><p>O resumo, os dados completos e o histórico aparecerão neste painel.</p></div>}
        </div>
      </section>}
    </div>
    <footer className="registry-footer">Dados exibidos a partir dos cadastros locais e das consultas importadas. As fichas do SIB são somente leitura.</footer>
  </div>;
}
