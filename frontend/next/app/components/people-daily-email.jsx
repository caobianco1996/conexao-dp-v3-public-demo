"use client";

import { useCallback, useEffect, useState } from "react";

const DEFAULT_COLUMNS = ["registration", "name", "admission", "termination", "contract", "company_code", "company_name"];
const normalize = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function greetingForTime(sendTime) {
  const hour = Number(String(sendTime || "08:00").split(":", 1)[0]);
  if (hour >= 5 && hour < 12) return "Bom dia";
  if (hour >= 12 && hour < 18) return "Boa tarde";
  return "Boa noite";
}

function updateGreeting(message, sendTime) {
  const lines = String(message || "").split("\n");
  const index = lines.findIndex(line => line.trim());
  if (index >= 0 && /^\s*(?:bom dia|boa tarde|boa noite)\b/i.test(lines[index])) {
    lines[index] = lines[index].match(/^\s*/)[0] + greetingForTime(sendTime) + ",";
  }
  return lines.join("\n");
}

function localClockTime() {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/Sao_Paulo", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date());
}

function attachmentSentence(selection) {
  const lists = [];
  if (selection.includeActive) lists.push("ativos atuais");
  if (selection.includeAdmissions) lists.push("admissões");
  if (selection.includeDismissals) lists.push("desligamentos");
  if (!lists.length) return "Nenhuma lista foi selecionada para envio.";
  if (lists.length === 1) return `Segue em anexo a lista de ${lists[0]} da empresa selecionada.`;
  return `Seguem em anexo as listas de ${lists.slice(0, -1).join(", ")} e ${lists[lists.length - 1]} da empresa selecionada.`;
}

function defaultEmailMessage(selection, sendTime = "08:00") {
  return `${greetingForTime(sendTime)},\n\nEspero que esteja bem.\n\n${attachmentSentence(selection)}\n\nPara mais informações, sigo à disposição.\n\nAtenciosamente,`;
}

function updateAttachmentSentence(message, selection) {
  const sentence = attachmentSentence(selection);
  const attachmentLine = /^.*\b(?:anexo|anexa|anexos|anexas)\b.*$|^Nenhuma lista foi selecionada para envio\.$/im;
  if (attachmentLine.test(message)) return message.replace(attachmentLine, sentence);
  const closingLine = /^(?:para mais informações|atenciosamente\b|cordialmente\b|obrigad[oa]s?\b).*$/im;
  if (closingLine.test(message)) return message.replace(closingLine, `${sentence}\n\n$&`);
  return `${message.trimEnd()}${message.trim() ? "\n\n" : ""}${sentence}`;
}

const EMPTY = {
  companyKey: "",
  recipientEmails: "",
  sendTime: "08:00",
  frequency: "daily",
  weekday: "0",
  monthday: "1",
  includeActive: true,
  includeAdmissions: true,
  includeDismissals: true,
  reportColumns: DEFAULT_COLUMNS,
  messageBody: defaultEmailMessage({ includeActive: true, includeAdmissions: true, includeDismissals: true }, "08:00"),
  active: true,
};
const runLabels = { ENVIANDO: "Em envio", ENVIADO: "Enviado", ERRO: "Falhou", INCERTO: "Confirme a entrega" };
const weekdays = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"];
const dateTime = value => value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";

async function payloadFrom(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Não foi possível acessar os agendamentos.");
  return payload;
}

function scheduleCadence(schedule) {
  if (schedule.testEveryTwoMinutes) return "A cada 2 minutos · teste";
  if (schedule.testEveryMinute) return "A cada 1 minuto · teste";
  if (schedule.frequency === "weekly") {
    return "Semanalmente · " + (weekdays[Number(schedule.weekday)] || "Segunda-feira") + " às " + schedule.send_time;
  }
  if (schedule.frequency === "monthly") {
    return "Mensalmente · dia " + schedule.monthday + " (ou último dia do mês) às " + schedule.send_time;
  }
  return "Diariamente às " + schedule.send_time;
}

function scheduleRecipients(schedule) {
  if (Array.isArray(schedule.recipients) && schedule.recipients.length) return schedule.recipients;
  return String(schedule.recipient_email || "").split(/[,;\s]+/).filter(Boolean);
}

function movementTypes(schedule) {
  const types = [];
  if (schedule.includeActive) types.push("Ativos atuais");
  if (schedule.includeAdmissions) types.push("Admissões");
  if (schedule.includeDismissals) types.push("Desligamentos");
  return types.join(" e ");
}

function scheduleHistory(schedule) {
  let summary = schedule.active ? "Próximo envio: " + dateTime(schedule.nextRunAt) : "Envio pausado";
  if (schedule.last_report_date) {
    const [date, time] = schedule.last_report_date.split("@");
    summary += " · Último: " + date.split("-").reverse().join("/") + (time ? " às " + time.slice(0, 5) + " (teste)" : "");
  } else {
    summary += " · Ainda não enviado";
  }
  if (schedule.last_status) summary += " · " + (runLabels[schedule.last_status] || schedule.last_status);
  if (schedule.last_summary) summary += " · " + schedule.last_summary;
  return summary;
}

function EmailColumns({ options, selected, defaults, disabled, onChange }) {
  const [group, setGroup] = useState("Funcionário");
  const [search, setSearch] = useState("");
  const groups = [...new Set(options.map(option => option.group))];
  const activeGroup = groups.includes(group) ? group : groups[0];
  const query = normalize(search.trim());
  const visible = options.filter(option => query
    ? normalize(option.label + " " + option.group).includes(query)
    : option.group === activeGroup);
  const chosen = options.filter(option => selected.includes(option.key));
  const toggle = (key, checked) => {
    const next = new Set(selected);
    if (checked) next.add(key); else next.delete(key);
    onChange(options.filter(option => next.has(option.key)).map(option => option.key));
  };

  return <div className="pr-email-columns">
    <div className="pr-email-columns-heading">
      <div><h4>Colunas dos anexos</h4><p>Marque os dados que devem aparecer nos arquivos deste agendamento.</p></div>
      <span className="pr-email-column-count" aria-live="polite">{chosen.length} selecionada{chosen.length === 1 ? "" : "s"}</span>
    </div>
    <div className="pr-email-column-tools">
      <label className="pr-email-column-search"><span className="visually-hidden">Buscar coluna do envio automático</span>
        <input type="search" placeholder="Buscar coluna…" value={search} onChange={event => setSearch(event.target.value)} disabled={disabled} />
      </label>
      <div className="pr-email-column-presets" role="group" aria-label="Seleções de colunas">
        <button type="button" onClick={() => onChange([...defaults])} disabled={disabled}>Essenciais</button>
        <button type="button" onClick={() => onChange(options.map(option => option.key))} disabled={disabled}>Todas</button>
        <button type="button" onClick={() => onChange([])} disabled={disabled}>Limpar</button>
      </div>
    </div>
    <div className="pr-email-column-groups" role="group" aria-label="Categorias de colunas">
      {groups.map(name => <button type="button" key={name} aria-pressed={!query && activeGroup === name}
        onClick={() => { setGroup(name); setSearch(""); }} disabled={disabled}>
        {name}<small>{options.filter(option => option.group === name && selected.includes(option.key)).length}/{options.filter(option => option.group === name).length}</small>
      </button>)}
    </div>
    <div className="pr-email-column-options" role="group" aria-label={query ? "Resultados da busca de colunas" : "Colunas de " + activeGroup}>
      {visible.map(option => <label key={option.key} className={selected.includes(option.key) ? "is-selected" : ""}>
        <input type="checkbox" checked={selected.includes(option.key)} onChange={event => toggle(option.key, event.target.checked)} disabled={disabled} />
        <span>{option.label}{query && <small>{option.group}</small>}</span>
      </label>)}
      {!visible.length && <p className="pr-email-column-empty">Nenhuma coluna encontrada.</p>}
    </div>
    {!selected.length && <p className="pr-email-field-error" role="alert">Selecione ao menos uma coluna para os anexos.</p>}
    {chosen.length > 0 && <details className="pr-email-header-preview"><summary>Ver cabeçalho dos arquivos · {chosen.length} colunas</summary>
      <div className="pr-email-header-scroll"><div>{chosen.map(option => <span key={option.key}>{option.label}</span>)}</div></div>
    </details>}
    <small className="pr-email-column-note">As colunas ficam salvas neste agendamento, independentemente dos filtros do relatório, e valem para cada lista selecionada.</small>
  </div>;
}

export default function PeopleDailyEmail() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await payloadFrom(await fetch("/api/email/people-schedules", {
        credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10000),
      }));
      setData(result);
      setForm(current => ({ ...current, companyKey: current.companyKey || result.companies?.[0]?.value || "",
        reportColumns: Array.isArray(current.reportColumns) ? current.reportColumns : result.defaultReportColumns || DEFAULT_COLUMNS }));
    } catch (cause) {
      setError(cause.name === "TimeoutError" || cause.name === "AbortError"
        ? "A consulta dos agendamentos demorou. Confira se a V3 está conectada e tente atualizar."
        : cause.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save(event) {
    event.preventDefault();
    if (!data?.csrf || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await payloadFrom(await fetch("/api/email/people-schedules", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, frequency: form.frequency.startsWith("test_") ? "daily" : form.frequency,
          reportColumns: selectedColumns,
          testEveryMinute: form.frequency === "test_1m", testEveryTwoMinutes: form.frequency === "test_2m",
          action: "save", id: editing || undefined, csrf: data.csrf }),
      }));
      setEditing(null);
      setForm({ ...EMPTY, companyKey: data.companies?.[0]?.value || "" });
      setNotice(result.message || "Agendamento salvo.");
      await load();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setSaving(false);
    }
  }

  async function toggle(schedule) {
    if (!data?.csrf || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await payloadFrom(await fetch("/api/email/people-schedules", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: schedule.active ? "pause" : "activate", id: schedule.id, csrf: data.csrf }),
      }));
      setNotice(result.message || "Agendamento atualizado.");
      await load();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setSaving(false);
    }
  }

  function edit(schedule) {
    const selection = {
      includeActive: schedule.includeActive,
      includeAdmissions: schedule.includeAdmissions,
      includeDismissals: schedule.includeDismissals,
    };
    setEditing(schedule.id);
    setForm({
      companyKey: schedule.company_key,
      recipientEmails: scheduleRecipients(schedule).join(", "),
      sendTime: schedule.send_time,
      frequency: schedule.testEveryTwoMinutes ? "test_2m" : schedule.testEveryMinute ? "test_1m" : (schedule.frequency || "daily"),
      weekday: String(schedule.weekday ?? 0),
      monthday: String(schedule.monthday ?? 1),
      reportColumns: [...(schedule.reportColumns || data.defaultReportColumns || DEFAULT_COLUMNS)],
      ...selection,
      messageBody: updateGreeting(schedule.message_body || defaultEmailMessage(selection, schedule.send_time),
        schedule.testEveryMinute || schedule.testEveryTwoMinutes ? localClockTime() : schedule.send_time),
      active: schedule.active,
    });
    document.getElementById("pa-daily-email-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function cancelEdit() {
    setEditing(null);
    setForm({ ...EMPTY, companyKey: data?.companies?.[0]?.value || "" });
  }

  function updateListSelection(key, checked) {
    setForm(current => {
      const next = { ...current, [key]: checked };
      return { ...next, messageBody: updateAttachmentSentence(current.messageBody || defaultEmailMessage(current), next) };
    });
  }

  const hasMovements = form.includeAdmissions || form.includeDismissals;
  const columnOptions = data?.columnOptions || [];
  const defaultColumns = data?.defaultReportColumns || DEFAULT_COLUMNS;
  const selectedColumns = Array.isArray(form.reportColumns) ? form.reportColumns : defaultColumns;
  const selectedLists = [form.includeActive, form.includeAdmissions, form.includeDismissals].filter(Boolean).length;
  const recipientCount = new Set(form.recipientEmails.split(/[,;\s]+/).filter(Boolean).map(value => value.toLowerCase())).size;
  const companyLabel = data?.companies?.find(company => company.value === form.companyKey)?.label;
  const movementHint = !hasMovements ? "" : form.frequency === "test_1m"
    ? "Modo de teste: envia por e-mail as movimentações do dia anterior a cada 1 minuto; pause o agendamento após conferir."
    : form.frequency === "test_2m"
      ? "Modo de teste: envia por e-mail as movimentações do dia anterior a cada 2 minutos; pause o agendamento após conferir."
    : form.frequency === "weekly"
    ? "Admissões e desligamentos: os 7 dias completos anteriores ao envio."
    : form.frequency === "monthly"
      ? "Admissões e desligamentos: mês calendário anterior ao envio."
      : "Admissões e desligamentos: dia anterior ao envio.";
  const periodHint = [movementHint, form.includeActive && "Ativos: situação vigente na data do envio, sem corte pelo período."]
    .filter(Boolean).join(" ");

  return <section className="pr-daily-email" aria-labelledby="pr-daily-email-title">
    <header className="pr-daily-email-heading">
      <div>
        <span className="pa-eyebrow">ENVIO AUTOMÁTICO · E-MAIL</span>
        <h2 id="pr-daily-email-title">Listas de pessoas por empresa</h2>
        <p>Escolha a empresa, as listas, as colunas dos anexos e quando enviar.</p>
      </div>
      <span className={"pr-daily-email-smtp " + (data?.smtpReady ? "is-ready" : "")}>
        <i />{loading ? "Verificando SMTP…" : data?.smtpReady ? "SMTP pronto" : "Configure o SMTP"}
      </span>
    </header>

    {!loading && !data && <div className="pr-daily-email-alert is-error" role="alert">
      {error}{error.includes("administrador") && " Entre com uma conta administradora para configurar envios."}
    </div>}
    {error && data && <div className="pr-daily-email-alert is-error" role="alert">{error}</div>}
    {notice && <div className="pr-daily-email-alert is-success" role="status">{notice}</div>}
    {data && !data.smtpReady && <div className="pr-daily-email-alert">
      Para ativar um envio, configure, salve e ative a conta em <a href="/central-dp/email">E-mail de teste</a>. Os agendamentos pausados ficam salvos.
    </div>}

    {data && <>
      <form id="pa-daily-email-form" className="pr-daily-email-form" onSubmit={save}>
        <section className="pr-email-form-block" aria-labelledby="pr-email-destination-title">
          <header className="pr-email-block-heading"><span aria-hidden="true">1</span><div><h3 id="pr-email-destination-title">Empresa e destinatários</h3><p>Defina de qual empresa serão os dados e quem receberá os arquivos.</p></div></header>
          <div className="pr-email-destination-fields">
            <label className="is-company">Empresa
              <select required value={form.companyKey}
                onChange={event => setForm(current => ({ ...current, companyKey: event.target.value }))}
                disabled={loading || saving}>
                <option value="">Selecione uma empresa</option>
                {data.companies.map(company => <option key={company.value} value={company.value}>{company.label}</option>)}
              </select>
              {companyLabel && <small>{companyLabel}</small>}
            </label>
            <label className="is-recipients">Destinatários
              <textarea maxLength={13000} required value={form.recipientEmails}
                onChange={event => setForm(current => ({ ...current, recipientEmails: event.target.value }))}
                placeholder="demo@example.com, gestor@example.com" disabled={loading || saving} rows={2} />
              <small>{recipientCount > 0 && `${recipientCount} endereço(s) · `}Separe por vírgula, ponto e vírgula ou quebra de linha. Até 50.</small>
            </label>
          </div>
        </section>

        <section className="pr-email-form-block" aria-labelledby="pr-email-files-title">
          <header className="pr-email-block-heading"><span aria-hidden="true">2</span><div><h3 id="pr-email-files-title">Arquivos e colunas</h3><p>Um Excel separado para cada lista marcada, com as colunas escolhidas aqui.</p></div></header>
          <div className="pr-daily-email-types" role="group" aria-label="Listas para envio automático">
            <label className={form.includeActive ? "is-selected" : ""}><input type="checkbox" checked={form.includeActive}
              onChange={event => updateListSelection("includeActive", event.target.checked)} disabled={loading || saving} />
              <span><strong>Ativos atuais</strong><small>Pessoas em atividade</small></span></label>
            <label className={form.includeAdmissions ? "is-selected" : ""}><input type="checkbox" checked={form.includeAdmissions}
              onChange={event => updateListSelection("includeAdmissions", event.target.checked)} disabled={loading || saving} />
              <span><strong>Admissões</strong><small>Entradas no período</small></span></label>
            <label className={form.includeDismissals ? "is-selected" : ""}><input type="checkbox" checked={form.includeDismissals}
              onChange={event => updateListSelection("includeDismissals", event.target.checked)} disabled={loading || saving} />
              <span><strong>Desligamentos</strong><small>Saídas no período</small></span></label>
          </div>
          {!selectedLists && <p className="pr-email-field-error" role="alert">Marque ao menos uma lista para envio.</p>}
          <EmailColumns options={columnOptions} selected={selectedColumns} defaults={defaultColumns}
            disabled={loading || saving} onChange={reportColumns => setForm(current => ({ ...current, reportColumns }))} />
        </section>

        <section className="pr-email-form-block" aria-labelledby="pr-email-cadence-title">
          <header className="pr-email-block-heading"><span aria-hidden="true">3</span><div><h3 id="pr-email-cadence-title">Frequência e horário</h3><p>Horários de Brasília.</p></div></header>
          <div className={"pr-daily-email-cadence-fields " + (form.frequency.startsWith("test_") ? "is-test-mode" : "") + (form.frequency === "daily" ? " is-daily" : "")}>
            <label>Enviar
              <select value={form.frequency}
                onChange={event => setForm(current => {
                  const next = { ...current, frequency: event.target.value };
                  const greetingTime = next.frequency.startsWith("test_") ? localClockTime() : next.sendTime;
                  return { ...next, messageBody: updateGreeting(current.messageBody, greetingTime) };
                })}
                disabled={loading || saving}>
                <option value="daily">Todos os dias</option>
                <option value="weekly">Toda semana</option>
                <option value="monthly">Todo mês</option>
                <option value="test_1m">A cada 1 minuto (teste)</option>
                <option value="test_2m">A cada 2 minutos (teste)</option>
              </select>
            </label>
            {form.frequency === "weekly" && <label>Dia da semana
              <select value={form.weekday}
                onChange={event => setForm(current => ({ ...current, weekday: event.target.value }))}
                disabled={loading || saving}>
                {weekdays.map((day, index) => <option key={day} value={index}>{day}</option>)}
              </select>
            </label>}
            {form.frequency === "monthly" && <label>Dia do mês
              <select value={form.monthday}
                onChange={event => setForm(current => ({ ...current, monthday: event.target.value }))}
                disabled={loading || saving}>
                {Array.from({ length: 31 }, (_, index) => index + 1).map(day =>
                  <option key={day} value={day}>{day}{day === 31 ? " (ou último dia)" : ""}</option>)}
              </select>
            </label>}
            {!form.frequency.startsWith("test_") && <label>Horário
              <input type="time" step="60" required value={form.sendTime}
                onChange={event => setForm(current => {
                  const next = { ...current, sendTime: event.target.value };
                  return { ...next, messageBody: updateGreeting(current.messageBody, next.sendTime) };
                })}
                disabled={loading || saving} />
            </label>}
          </div>
          <small className="pr-daily-email-period-hint">{periodHint}</small>
        </section>

        <section className="pr-email-form-block" aria-labelledby="pr-email-message-title">
          <header className="pr-email-block-heading"><span aria-hidden="true">4</span><div><h3 id="pr-email-message-title">Mensagem do e-mail</h3><p>A saudação acompanha o horário do envio e a frase dos anexos acompanha as listas marcadas.</p></div></header>
          <label className="is-message"><span className="visually-hidden">Conteúdo da mensagem</span>
            <textarea maxLength={4000} required rows={8} value={form.messageBody}
              onChange={event => setForm(current => ({ ...current, messageBody: event.target.value }))} disabled={loading || saving} />
            <small>Bom dia: 5h–11h59 · Boa tarde: 12h–17h59 · Boa noite: 18h–4h59.</small>
          </label>
        </section>

        <div className="pr-daily-email-form-actions">
          <div className="pr-email-save-summary"><strong>{selectedLists} arquivo(s) Excel · {selectedColumns.length} coluna(s)</strong><small>{editing && !form.active ? "Este agendamento está pausado." : "O próximo envio seguirá a frequência escolhida."}</small></div>
          <div className="pr-email-save-buttons">
          <button type="submit" className="btn btn-primary"
            disabled={loading || saving || !data.companies?.length || !selectedLists || !selectedColumns.length || !columnOptions.length || (form.active && !data.smtpReady)}>
            {saving ? "Salvando…" : editing ? "Salvar alterações" : "Salvar agendamento"}
          </button>
          {editing && <button type="button" className="btn btn-outline-primary" onClick={cancelEdit} disabled={saving}>Cancelar edição</button>}
          </div>
        </div>
      </form>

      <div className="pr-daily-email-list">
        <div className="pr-daily-email-list-heading">
          <h3>Agendamentos</h3>
          <button type="button" className="btn btn-outline-primary btn-sm" onClick={load} disabled={loading || saving}>Atualizar</button>
        </div>
        {loading ? <p className="pr-daily-email-empty">Carregando agendamentos…</p> : data.schedules.length
          ? data.schedules.map(schedule => <article className="pr-daily-email-item" key={schedule.id}>
            <div className="pr-daily-email-item-main">
              <div className="pr-daily-email-title-line">
                <strong>{schedule.companyLabel}</strong>
                <span className={"pr-daily-email-pill " + (schedule.active ? "is-active" : "")}>{schedule.active ? "Ativo" : "Pausado"}</span>
              </div>
              <p>{movementTypes(schedule)} · {scheduleCadence(schedule)}</p>
              <p>Destinatários: {scheduleRecipients(schedule).join(", ")}</p>
              <details className="pr-email-saved-columns"><summary>Colunas dos anexos · {(schedule.reportColumns || defaultColumns).length}</summary><p>{columnOptions.filter(option => (schedule.reportColumns || defaultColumns).includes(option.key)).map(option => option.label).join(" · ")}</p></details>
              <small>{scheduleHistory(schedule)}</small>
            </div>
            <div className="pr-daily-email-item-actions">
              <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => edit(schedule)} disabled={saving}>Editar</button>
              <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => toggle(schedule)} disabled={saving}>{schedule.active ? "Pausar" : "Ativar"}</button>
            </div>
          </article>)
          : <p className="pr-daily-email-empty">Nenhum agendamento configurado.</p>}
      </div>
      <p className="pr-daily-email-footnote">O arquivo Excel é gerado com os dados já disponíveis na base local da V3. Nenhuma consulta é feita ao Bitti/SIB no momento do envio.</p>
    </>}
  </section>;
}

