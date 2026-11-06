"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const categories = ["Salários e adiantamentos", "Jornada e adicionais", "Benefícios e descontos", "Férias e 13º", "Tributos e rescisão"];
const categoryById = {
  advance: categories[0], proportional: categories[0],
  overtime: categories[1], direct: categories[1], night: categories[1], reduced: categories[1], interval: categories[1], nighthe: categories[1], dsr: categories[1], "dsr-discount": categories[1], averages: categories[1], unhealthy: categories[1], danger: categories[1], absence: categories[1], delay: categories[1],
  transport: categories[2], net: categories[2], thirteenth: categories[3], vacation: categories[3], inss: categories[4], irrf: categories[4], termination: categories[4],
};
const descriptions = {
  advance: "Simule a antecipação com competência, admissão, corte e base visíveis.", proportional: "Estime salário proporcional com período e divisor informados.",
  overtime: "Calcule horas extras a partir do salário, divisor, horas e adicional.", direct: "Calcule horas extras informando diretamente o valor da hora.", night: "Estime o adicional noturno sobre as horas informadas.", reduced: "Converta horas de relógio em horas noturnas equivalentes.", interval: "Apure o período noturno entre horários, intervalo e prorrogação.", nighthe: "Discrimine hora normal, extra e adicional noturno.", dsr: "Estime DSR sobre valores variáveis no período informado.", "dsr-discount": "Estime reflexos de faltas em repousos semanais.", averages: "Calcule médias a partir dos valores mensais digitados.", unhealthy: "Simule adicional com base e percentual informados.", danger: "Simule periculosidade sobre a base informada.",
  absence: "Estime desconto de faltas com salário e divisor em dias.", delay: "Estime desconto de horas e minutos de atraso.", transport: "Calcule o desconto de vale-transporte conforme limite informado.", net: "Subtraia descontos informados dos proventos informados.", thirteenth: "Estime parcelas do 13º com avos e médias informados.", vacation: "Estime férias, terço, abono e descontos informados.", inss: "Consulte cálculo pela versão de tabela local publicada para a competência.", irrf: "Consulte cálculo pela versão de tabela local publicada para a competência.", termination: "Organize verbas e descontos informados sem presumir rubricas rescisórias.",
};
const fieldHelp = {
  divisor: "Informe o divisor previsto na jornada ou na regra aplicável; não é presumido para toda pessoa.",
  divisorDays: "Informe o divisor em dias usado neste cenário.",
  percentage: "Use o percentual informado pela política aplicável à simulação.",
  hours: "Informe a quantidade apurada no mesmo período do cálculo.",
  holidays: "Datas ISO, uma por linha; feriados locais cadastrados podem ser selecionados separadamente.",
  variableTotal: "Somente valores variáveis elegíveis; evite somar DSR já incluído.",
};
const dateToday = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};
const blankAdvance = () => ({
  competence: dateToday(), companyKey: "", manualSimulationConfirmed: "", entryMode: "", monthlySalary: "", hourlyRate: "", monthlyHours: "", method: "", percentage: "", admissionDate: "", paymentDate: "", cutoffDate: "", dayDivisor: "", fullPeriodApproved: "", paymentDateOverrideConfirmed: "", paymentDateOverrideReason: "", hoursPeriodStart: "", hoursPeriodEnd: "", eligibleHours: "", hoursAreProjected: "0", previousAdvance: "", systemName: "", systemValue: "", systemHourlyRate: "", systemMonthlyHours: "", systemPeriod: "", systemRubrics: "", systemNotes: "",
  insalubrityAmount: "", insalubrityTreatment: "UNSPECIFIED", hazardPayAmount: "", hazardPayTreatment: "UNSPECIFIED", nightPayAmount: "", nightPayTreatment: "UNSPECIFIED", overtimeAmount: "", overtimeTreatment: "UNSPECIFIED", variableDsrAmount: "", variableDsrTreatment: "UNSPECIFIED", commissionsAmount: "", commissionsTreatment: "UNSPECIFIED", bonusAmount: "", bonusTreatment: "UNSPECIFIED", otherComponentsAmount: "", otherComponentsTreatment: "UNSPECIFIED",
});
const cleanSnapshot = input => Object.fromEntries(Object.entries(input || {}).filter(([key]) => !key.startsWith("_")));
const parseData = async response => {
  if (response.redirected || response.status === 301 || response.status === 302 || response.status === 303 || response.status === 307 || response.status === 308 || /(^|\/)entrar(?:[?#]|$)/i.test(response.headers.get("location") || "")) throw new Error("Sua sessão expirou. Entre novamente.");
  if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new Error("A API redirecionou para a tela de entrada. Entre novamente e tente outra vez.");
  const payload = await response.json().catch(() => ({ error: "Resposta inválida do servidor." }));
  if (!response.ok) throw new Error(payload.error || "Não foi possível concluir a operação.");
  return payload;
};
const money = value => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value) || 0);
const dateLabel = value => {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return day ? `${day}/${month}/${year}` : `${month}/${year}`;
};
const methodLabels = { FULL: "Salário integral", PROJECTED: "Salário mensal proporcional projetado", ACCUMULATED: "Valor acumulado até o corte", HOURS: "Horas informadas" };
const numberKeys = new Set(["salary", "salaryHour", "base", "benefit", "percentage", "divisor", "hours", "clockHours", "breakMinutes", "extensionMinutes", "reducedMinutes", "nightPercentage", "overtimePercentage", "exposureHours", "days", "divisorDays", "entitledDays", "months", "averages", "otherBase", "firstInstallmentPercentage", "deductions", "vacationDays", "sellDays", "remuneration", "taxableRemuneration", "otherRemuneration", "contributionAlreadyWithheld", "inss", "inssDeductible", "dependents", "alimony", "otherLegalDeductions", "totalMonths"]);
const textareas = new Set(["values", "earnings", "deductions", "absenceDates", "holidays"]);
const selectOptions = {
  applyReduction: [["1", "Aplicar redução"], ["0", "Não aplicar"]], includeAbono: [["1", "Incluir abono"], ["0", "Não incluir"]], includeThirdOnAbono: [["1", "Incluir 1/3"], ["0", "Não incluir"]], applyPublishedTaxes: [["NO", "Não, manter descontos informados"], ["YES", "Sim, usar tabelas locais publicadas"]],
  nightPremiumBasis: [["NORMAL", "Hora normal"], ["OVERTIME_HOUR", "Hora extra" ]], divideBy: [["ALL_MONTHS", "Todos os meses"], ["INFORMED_MONTHS", "Meses com valor"]], installment: [["TOTAL", "Total"], ["FIRST", "1ª parcela"], ["SECOND", "2ª parcela"]], deductionMethod: [["AUTO", "Comparar métodos"], ["LEGAL", "Deduções legais"], ["SIMPLIFIED", "Desconto simplificado"]], mode: [["MONTHLY", "Mensal"], ["THIRTEENTH", "13º salário"]], conditionStatus: [["CONFIRMED", "Condição reconhecida/informada"], ["HYPOTHETICAL", "Hipótese sem condição confirmada"]],
};
const requiredByCalculator = {
  overtime: ["salary", "divisor", "hours", "percentage"], direct: ["salaryHour", "hours", "percentage"], night: ["salary", "divisor", "hours", "percentage"],
  reduced: ["clockHours", "reducedMinutes", "applyReduction"], interval: ["start", "end", "reducedMinutes", "applyReduction"],
  nighthe: ["salary", "divisor", "clockHours", "overtimePercentage", "nightPercentage", "reducedMinutes", "applyReduction", "nightPremiumBasis"],
  dsr: ["variableTotal", "startDate", "endDate"], "dsr-discount": ["salary", "divisorDays", "startDate", "endDate", "absenceDates"],
  averages: ["values", "totalMonths", "divideBy"], unhealthy: ["base", "percentage", "conditionStatus", "validity"], danger: ["base", "percentage", "conditionStatus", "validity"],
  absence: ["salary", "days", "divisorDays"], delay: ["salary", "divisor", "hours", "minutes"], proportional: ["salary", "divisorDays", "entitledDays"],
  thirteenth: ["salary", "months", "installment", "firstInstallmentPercentage"], vacation: ["salary", "vacationDays", "includeAbono", "includeThirdOnAbono"],
  transport: ["salary", "percentage", "benefit"], net: ["earnings"], termination: ["earnings"], inss: ["competence", "mode", "remuneration"], irrf: ["competence", "paymentDate", "mode", "remuneration", "inss", "deductionMethod"],
};
const optionalBlankByCalculator = {
  interval: ["breakMinutes", "extensionMinutes"], delay: ["minutes"], dsr: ["city", "holidays"], "dsr-discount": ["city", "holidays"],
  unhealthy: ["source"], danger: ["source", "exposureHours"], thirteenth: ["averages", "otherBase", "deductions"],
  vacation: ["averages", "additions", "sellDays", "deductions"], net: ["deductions"], termination: ["deductions"],
  inss: ["otherRemuneration", "contributionAlreadyWithheld"], irrf: ["inss", "dependents", "alimony", "otherLegalDeductions"],
  net: ["deductions", "competence", "paymentDate", "taxableRemuneration", "otherRemuneration", "contributionAlreadyWithheld", "inssDeductible", "dependents", "alimony", "otherLegalDeductions"],
};

function inputExample(label, type) {
  const text = String(label).toLocaleLowerCase("pt-BR");
  if (type === "date" || type === "month") return "";
  if (type === "time") return "Ex.: 08:00";
  if (type === "number") {
    if (/percentual|adicional|alíquota|taxa/.test(text)) return "Ex.: 50";
    if (/salário-hora|salario-hora/.test(text)) return "Ex.: 15,00";
    if (/horas mensais/.test(text)) return "Ex.: 220";
    if (/dia|mês|mes|avo|divisor|dependente|quantidade/.test(text)) return "Ex.: 30";
    if (/salário|salario|valor|base|remuneração|provento|desconto|parcela|adiantamento|tarifa/.test(text)) return "Ex.: 3.000,00";
    if (/hora|minuto/.test(text)) return "Ex.: 1";
    return "Ex.: 1";
  }
  if (/motivo/.test(text)) return "Ex.: ajuste previsto para esta simulação";
  if (/sistema/.test(text)) return "Ex.: Sistema de folha";
  if (/período|periodo/.test(text)) return "Ex.: 01/10 a 31/10";
  return "Ex.: informe o dado";
}

function dateExample(type) {
  if (type === "date") return "Exemplo: 15/10/2026.";
  if (type === "month") return "Exemplo: 10/2026.";
  if (type === "time") return "Exemplo: 08:00.";
  return "";
}

const textareaExamples = {
  values: "Ex.: 250,00; 300,00; 280,00",
  earnings: "Ex.: Salário 3.000,00; horas extras 150,00",
  deductions: "Ex.: INSS 250,00; vale-transporte 100,00",
  absenceDates: "Ex.: 2026-10-05; 2026-10-12",
  holidays: "Ex.: 2026-10-12",
};
const displayLabelFor = (key, label) => ({ values: "Valores mensais separados por ponto e vírgula ou nova linha", earnings: "Proventos separados por ponto e vírgula ou nova linha", deductions: "Descontos separados por ponto e vírgula ou nova linha", absenceDates: "Datas das faltas em ISO, separadas por ponto e vírgula ou nova linha" }[key] || label);

function Input({ label, value, onChange, type = "text", required = false, help, placeholder, min, max, step, className = "" }) {
  const example = placeholder?.startsWith("Ex.:") ? placeholder : type === "number" ? inputExample(label, type) : placeholder ? `Ex.: ${placeholder}` : inputExample(label, type);
  const hint = [help, dateExample(type)].filter(Boolean).join(" ");
  return <label className={`calc-field ${className}`}><span>{label}{required && <b aria-hidden="true"> *</b>}</span>
    <input className="form-control" type={type === "number" ? "text" : type} inputMode={type === "number" ? "decimal" : undefined} value={value ?? ""} onChange={onChange} required={required} placeholder={example} min={min} max={max} step={step} />
    {hint && <small>{hint}</small>}
  </label>;
}

function Select({ label, value, onChange, options, required = false, help, className = "" }) {
  return <label className={`calc-field ${className}`}><span>{label}{required && <b aria-hidden="true"> *</b>}</span>
    <select className="form-select" value={value ?? ""} onChange={onChange} required={required}><option value="">Selecione uma opção</option>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select>
    {help && <small>{help}</small>}
  </label>;
}

function Textarea({ label, value, onChange, required = false, help, placeholder = "Ex.: informe os dados necessários separados por ponto e vírgula", rows = 3, className = "" }) {
  return <label className={`calc-field ${className}`}><span>{label}{required && <b aria-hidden="true"> *</b>}</span>
    <textarea className="form-control" value={value ?? ""} onChange={onChange} required={required} rows={rows} placeholder={placeholder} />{help && <small>{help}</small>}</label>;
}

function AdvanceForm({ form, setField, companies }) {
  const company = companies.find(item => item.key === form.companyKey);
  const companyDay = company?.advanceDay || "";
  const mode = form.entryMode;
  const method = form.method;
  const [year, month] = String(form.competence || "").split("-").map(Number);
  const validMonth = year && month && month >= 1 && month <= 12;
  const possibleRegisteredDate = validMonth && companyDay && Number(companyDay) <= new Date(year, month, 0).getDate() ? `${year}-${String(month).padStart(2, "0")}-${String(companyDay).padStart(2, "0")}` : "";
  const hasOverride = companyDay && form.paymentDate && Number(form.paymentDate.slice(-2)) !== Number(companyDay);
  const newHire = Boolean(form.admissionDate && validMonth && form.admissionDate >= `${year}-${String(month).padStart(2, "0")}-01`);
  const components = [["insalubrity", "Insalubridade"], ["hazardPay", "Periculosidade"], ["nightPay", "Adicional noturno"], ["overtime", "Horas extras"], ["variableDsr", "DSR de variáveis"], ["commissions", "Comissões"], ["bonus", "Prêmios ou bonificações"], ["otherComponents", "Outras parcelas"]];
  return <div className="calc-advance-form">
    <section className="calc-form-group"><div className="calc-group-title"><span>01</span><div><h3>Competência e política</h3><p>A empresa pode informar a data; percentual, método e base continuam explícitos.</p></div></div>
      <div className="row g-3"><Input label="Competência" type="month" value={form.competence} onChange={e => { const next = e.target.value; const [nextYear, nextMonth] = next.split("-").map(Number); const days = nextYear && nextMonth ? new Date(nextYear, nextMonth, 0).getDate() : 0; setField("competence", next); setField("paymentDate", companyDay && Number(companyDay) <= days ? `${nextYear}-${String(nextMonth).padStart(2, "0")}-${String(companyDay).padStart(2, "0")}` : ""); setField("paymentDateOverrideConfirmed", ""); setField("paymentDateOverrideReason", ""); }} required className="col-md-4" />
        <Select label="Empresa ou unidade (opcional)" value={form.companyKey} onChange={e => { const selected = companies.find(item => item.key === e.target.value); setField("companyKey", e.target.value); setField("manualSimulationConfirmed", ""); setField("paymentDate", selected?.advanceDay && validMonth && Number(selected.advanceDay) <= new Date(year, month, 0).getDate() ? `${year}-${String(month).padStart(2, "0")}-${String(selected.advanceDay).padStart(2, "0")}` : ""); setField("paymentDateOverrideConfirmed", ""); setField("paymentDateOverrideReason", ""); }} options={companies.map(item => [item.key, `${item.name} · ${item.source}`])} className="col-md-8" />
      </div>
      <div className={`calc-policy-note ${company?.advanceStatus === "NONE" ? "is-warning" : ""}`} role="status">
        <strong>{!company ? "Simulação manual sem política empresarial vinculada" : company.advanceStatus === "HAS_DAY" ? `Data do adiantamento cadastrada: dia ${company.advanceDay}` : company.advanceStatus === "NONE" ? "Esta empresa não possui adiantamento salarial cadastrado" : company.advanceStatus === "UNKNOWN" ? "O cadastro tem um valor de adiantamento que precisa ser conferido" : "A empresa não tem uma regra de adiantamento identificada no cadastro"}</strong>
        <span>{company ? `Fonte do cadastro: ${company.profileSource}; vigência do campo não está registrada.` : "Nenhum cadastro de empresa será usado nesta simulação."} O cadastro disponível não informa percentual, método ou composição da base.</span>
        {company?.advanceStatus === "HAS_DAY" && <span>O dia cadastrado é preservado como data civil; não foi deslocado para dia útil.</span>}
      </div>
      <label className="calc-check-line"><input className="form-check-input" type="checkbox" checked={form.manualSimulationConfirmed === "1"} onChange={e => setField("manualSimulationConfirmed", e.target.checked ? "1" : "")} required />
        <span>Confirmo que os parâmetros abaixo são uma simulação manual e não representam regra empresarial cadastrada.</span></label>
      <div className="row g-3"><Select label="Método de cálculo" value={method} onChange={e => setField("method", e.target.value)} required options={[["FULL", "Salário integral"], ["PROJECTED", "Salário mensal proporcional projetado"], ["ACCUMULATED", "Valor acumulado até o corte"], ["HOURS", "Horas apuradas ou projetadas"]]} className="col-lg-6" help="O método não é escolhido automaticamente pelo dia do pagamento." />
        <Input label="Percentual do adiantamento" value={form.percentage} onChange={e => setField("percentage", e.target.value)} required type="number" min="0" max="100" step="0.01" placeholder="Ex.: 40" className="col-lg-6" help="40% é apenas um valor de exemplo, não uma taxa universal." />
        <Input label="Data prevista de pagamento do adiantamento" value={form.paymentDate} onChange={e => setField("paymentDate", e.target.value)} required type="date" className="col-md-6" />
        <Input label="Data de corte da apuração" value={form.cutoffDate} onChange={e => setField("cutoffDate", e.target.value)} required type="date" className="col-md-6" help="Limite inclusivo para apuração; não é a data do depósito." />
      </div>
      {companyDay && !possibleRegisteredDate && <p className="calc-inline-warning">O cadastro informa dia {companyDay}, que não existe nesta competência. Escolha a data civil prevista sem antecipação automática.</p>}
      {hasOverride && <div className="calc-override"><label className="calc-check-line"><input className="form-check-input" type="checkbox" checked={form.paymentDateOverrideConfirmed === "1"} onChange={e => setField("paymentDateOverrideConfirmed", e.target.checked ? "1" : "")} required /><span>A data prevista difere do dia {companyDay} cadastrado; confirmo o uso dessa outra data nesta simulação.</span></label><Input label="Motivo da alteração da data" value={form.paymentDateOverrideReason} onChange={e => setField("paymentDateOverrideReason", e.target.value)} required placeholder="Ex.: antecipação prevista em política específica" /></div>}
    </section>

    <section className="calc-form-group"><div className="calc-group-title"><span>02</span><div><h3>Salário e jornada</h3><p>Informe os valores da remuneração que correspondem à modalidade selecionada.</p></div></div>
      <div className="row g-3"><Select label="Modalidade de entrada" value={mode} onChange={e => setField("entryMode", e.target.value)} required options={[["MONTHLY", "Mensalista"], ["HOURLY_REFERENCE", "Horista com referência mensal"], ["HOURLY_ACTUAL", "Horista por horas apuradas ou projetadas"]]} className="col-md-12" />
        {mode === "MONTHLY" && <Input label="Salário mensal bruto de referência" value={form.monthlySalary} onChange={e => setField("monthlySalary", e.target.value)} required type="number" min="0.01" step="0.01" placeholder="0,00" className="col-md-6" help="Valor nominal bruto informado para esta simulação." />}
        {mode === "HOURLY_REFERENCE" && <><Input label="Salário-hora" value={form.hourlyRate} onChange={e => setField("hourlyRate", e.target.value)} required type="number" min="0.01" step="0.01" placeholder="0,00" className="col-md-6" /><Input label="Horas mensais de referência" value={form.monthlyHours} onChange={e => setField("monthlyHours", e.target.value)} required type="number" min="0.01" step="0.01" placeholder="Informe a jornada contratual" className="col-md-6" help="220 só quando essa for a jornada contratual informada." /></>}
        {mode === "HOURLY_ACTUAL" && <><Input label="Salário-hora" value={form.hourlyRate} onChange={e => setField("hourlyRate", e.target.value)} required type="number" min="0.01" step="0.01" placeholder="0,00" className="col-md-4" /><Input label="Horas elegíveis" value={form.eligibleHours} onChange={e => setField("eligibleHours", e.target.value)} required type="number" min="0" step="0.01" placeholder="0,00" className="col-md-4" /><Select label="Natureza das horas" value={form.hoursAreProjected} onChange={e => setField("hoursAreProjected", e.target.value)} required options={[["0", "Apuradas"], ["1", "Projetadas"]]} className="col-md-4" /><Input label="Início do período de horas" value={form.hoursPeriodStart} onChange={e => setField("hoursPeriodStart", e.target.value)} required type="date" className="col-md-6" /><Input label="Fim do período de horas" value={form.hoursPeriodEnd} onChange={e => setField("hoursPeriodEnd", e.target.value)} required type="date" className="col-md-6" /><p className="calc-help-wide">O DSR deve ser informado à parte quando não estiver incluído nessas horas.</p></>}
      </div>
      {(method === "PROJECTED" || method === "ACCUMULATED") && <div className="row g-3 mt-1"><Input label="Divisor de dias" value={form.dayDivisor} onChange={e => setField("dayDivisor", e.target.value)} required type="number" min="1" max="31" step="1" placeholder="Informe o divisor desta política" className="col-md-6" help="Não foi presumido 30; digite o divisor confirmado para o cenário." /></div>}
      {method === "FULL" && newHire && <label className="calc-check-line mt-3"><input className="form-check-input" type="checkbox" checked={form.fullPeriodApproved === "1"} onChange={e => setField("fullPeriodApproved", e.target.checked ? "1" : "")} required /><span>Confirmo que o método integral é permitido para a admissão nesta competência.</span></label>}
    </section>

    <section className="calc-form-group"><div className="calc-group-title"><span>03</span><div><h3>Componentes da base</h3><p>Valores informados para o mesmo período da base salarial selecionada.</p></div></div>
      <div className="calc-components">{components.map(([key, label]) => <div className="calc-component-row" key={key}><div><strong>{label}</strong><small>Origem: informado manualmente; não há inclusão empresarial verificada.</small></div><Input label={`Valor de ${label.toLowerCase()}`} value={form[`${key}Amount`]} onChange={e => setField(`${key}Amount`, e.target.value)} type="number" min="0" step="0.01" placeholder="Não informado" /><Select label="Tratamento na base" value={form[`${key}Treatment`]} onChange={e => setField(`${key}Treatment`, e.target.value)} required={Boolean(form[`${key}Amount`])} options={[["UNSPECIFIED", "Não definido"], ["INCLUDED", "Incluir nesta simulação"], ["EXCLUDED", "Excluir desta simulação"]]} /></div>)}</div>
      <Input label="Adiantamento já pago nesta competência" value={form.previousAdvance} onChange={e => setField("previousAdvance", e.target.value)} type="number" min="0" step="0.01" placeholder="0,00" help="Será abatido uma única vez do valor calculado." className="mt-3" />
    </section>

    <details className="calc-compare"><summary>Comparar com valor do sistema</summary><p>Registre o resultado externo para ver a diferença; uma divergência não prova erro de nenhum dos sistemas.</p><div className="row g-3"><Input label="Nome do sistema (opcional)" value={form.systemName} onChange={e => setField("systemName", e.target.value)} placeholder="Sistema de folha" className="col-md-6" /><Input label="Valor informado no sistema" value={form.systemValue} onChange={e => setField("systemValue", e.target.value)} type="number" min="0" step="0.01" placeholder="0,00" className="col-md-6" /><Input label="Salário-hora exibido (se souber)" value={form.systemHourlyRate} onChange={e => setField("systemHourlyRate", e.target.value)} type="number" min="0" step="0.01" className="col-md-6" /><Input label="Horas mensais de referência no sistema" value={form.systemMonthlyHours} onChange={e => setField("systemMonthlyHours", e.target.value)} type="number" min="0" step="0.01" className="col-md-6" /><Input label="Período usado no sistema" value={form.systemPeriod} onChange={e => setField("systemPeriod", e.target.value)} placeholder="Ex.: 06/10 a 31/10" className="col-12" /><Textarea label="Rubricas incluídas no sistema" value={form.systemRubrics} onChange={e => setField("systemRubrics", e.target.value)} rows={2} className="col-12" /><Textarea label="Observação" value={form.systemNotes} onChange={e => setField("systemNotes", e.target.value)} rows={2} className="col-12" /></div></details>
  </div>;
}

function GenericForm({ calculator, form, setField, locations }) {
  if (!calculator) return null;
  const taxFields = new Set(["competence", "paymentDate", "taxableRemuneration", "otherRemuneration", "contributionAlreadyWithheld", "inssDeductible", "dependents", "alimony", "otherLegalDeductions", "deductionMethod"]);
  const fields = calculator.id === "net" && form.applyPublishedTaxes !== "YES" ? calculator.fields.filter(field => !taxFields.has(field.key)) : calculator.fields;
  return <div className="row g-3">{fields.map(field => {
    const { key, label, default: defaultValue } = field;
    const options = selectOptions[key];
    const required = (requiredByCalculator[calculator.id] || []).includes(key) || (calculator.id === "net" && form.applyPublishedTaxes === "YES" && ["competence", "paymentDate", "taxableRemuneration", "deductionMethod"].includes(key));
    if (options) {
      const available = key === "mode" && calculator.id === "irrf" ? [["MONTHLY", "Salário mensal"]] : options;
      return <Select key={key} label={label} value={form[key]} onChange={e => setField(key, e.target.value)} options={available} required={key !== "applyPublishedTaxes" && required} className="col-md-6" help={key === "applyPublishedTaxes" ? "Informe competência do INSS e data do pagamento do IRRF separadamente. Não repita os tributos no campo de descontos." : undefined} />;
    }
    if (key === "city") return <Select key={key} label="Localidade dos feriados cadastrados" value={form[key]} onChange={e => setField(key, e.target.value)} options={locations.map(item => [`${item.state || ""}|${item.city}`, `${item.city}${item.state ? ` · ${item.state}` : ""}`])} help="Selecione localidade disponível; datas informadas manualmente continuam aceitas." className="col-md-6" />;
    if (textareas.has(key)) return <Textarea key={key} label={displayLabelFor(key, label)} value={form[key]} onChange={e => setField(key, e.target.value)} rows={key === "values" || key === "earnings" || key === "deductions" ? 4 : 3} required={required} help={fieldHelp[key]} placeholder={textareaExamples[key]} className="col-md-6" />;
    const type = key === "competence" ? "month" : key === "paymentDate" || key === "startDate" || key === "endDate" ? "date" : key === "start" || key === "end" ? "time" : numberKeys.has(key) ? "number" : "text";
    const numType = type === "number";
    const displayLabel = displayLabelFor(key, label);
    const help = key === "competence" && calculator.id === "irrf" ? "Contexto previdenciário do cálculo. A tabela do IRRF é escolhida pela data de pagamento abaixo." : key === "paymentDate" && calculator.id === "irrf" ? "Data efetiva usada para resolver a regra de retenção." : key === "deductions" && calculator.id === "net" ? "Com tabelas publicadas ligadas, não inclua INSS e IRRF aqui." : key === "inssDeductible" ? "Deixe vazio para usar a contribuição estimada acima; informe valor manual apenas com fonte confirmada." : key === "validity" ? "Ex.: 10/2026 ou 01/10/2026 a 31/10/2026." : fieldHelp[key] || (defaultValue ? "Exemplo; não foi aplicado automaticamente. Origem: entrada manual." : "Origem: entrada manual.");
    return <Input key={key} label={displayLabel} value={form[key]} onChange={e => setField(key, e.target.value)} type={type} required={required} step={numType ? "any" : undefined} min={numType ? "0" : undefined} placeholder={defaultValue ? `Ex.: ${defaultValue}` : inputExample(displayLabel, type)} help={help} className="col-md-6" />;
  })}</div>;
}

function AdvanceResult({ result }) {
  const detail = result.details || {};
  const compare = detail.comparison;
  return <div className="calc-result-body"><div className="calc-result-amount"><span>Adiantamento estimado</span><strong>{money(result.value)}</strong><small>Simulação manual · não é processamento nem pagamento de folha</small></div>
    <dl className="calc-result-grid"><div><dt>Competência</dt><dd>{dateLabel(detail.competence)}</dd></div><div><dt>Empresa e origem</dt><dd>{detail.companyName} · {detail.companySource}</dd></div><div><dt>Método</dt><dd>{methodLabels[detail.method] || detail.method}</dd></div><div><dt>Base considerada</dt><dd>{money(detail.base)}</dd></div><div><dt>Salário de referência</dt><dd>{detail.referenceSalary ? money(detail.referenceSalary) : "Horas apuradas"}</dd></div><div><dt>Percentual</dt><dd>{detail.percentage}%</dd></div><div><dt>Período considerado</dt><dd>{dateLabel(detail.periodStart)} a {dateLabel(detail.periodEnd)}{detail.daysApplied ? ` · ${detail.daysApplied} dias aplicados` : ""}</dd></div><div><dt>Pagamento previsto</dt><dd>{dateLabel(detail.paymentDate)}</dd></div><div><dt>Data de corte</dt><dd>{dateLabel(detail.cutoffDate)}</dd></div><div><dt>Adiantamento anterior abatido</dt><dd>{money(detail.previousAdvance)}</dd></div></dl>
    {detail.includedComponents?.length > 0 && <p><strong>Parcelas incluídas:</strong> {detail.includedComponents.map(item => `${item.name} ${money(item.amount)}`).join(" · ")}</p>}
    {detail.excludedComponents?.length > 0 && <p><strong>Parcelas não consideradas:</strong> {detail.excludedComponents.map(item => `${item.name} ${money(item.amount)}`).join(" · ")}</p>}
    {compare && <div className={`calc-comparison ${compare.status === "DIVERGENT" ? "is-warning" : "is-ok"}`}><strong>{compare.status === "DIVERGENT" ? "O valor calculado com as regras selecionadas difere do valor informado no sistema. Confira período, rubricas e divisor." : "Valores coincidem nesta simulação."}</strong><div className="calc-compare-values"><span>{compare.systemName}: {money(compare.systemValue)}</span><span>Simulação: {money(compare.scenarioValue)}</span><b>Diferença: {money(compare.difference)}{compare.differencePercent !== null ? ` (${compare.differencePercent}%)` : ""}</b></div>{compare.status === "DIVERGENT" && <details><summary>Dados que podem explicar a diferença</summary><ul><li>Jornada e horas mensais de referência.</li><li>Data de admissão, período e corte usados no sistema.</li><li>Percentual efetivamente aplicado.</li><li>Adicionais, DSR, variáveis e parcelas já pagas.</li></ul><p>Ainda não é possível identificar a causa da diferença com os dados informados.</p></details>}</div>}
    <ul className="calc-warnings">{(detail.warnings || []).map((warning, index) => <li key={index}>{warning}</li>)}</ul>
  </div>;
}

function SavedList({ items, onOpen, onPin, loadingId }) {
  return <section className="card calc-saved-card"><div className="calc-section-head"><div><span className="calc-eyebrow">SOMENTE QUANDO SALVAR</span><h2>Simulações salvas</h2></div><small>Expiram em 4 dias; fixadas não expiram.</small></div>{items.length === 0 ? <p className="calc-empty-saved">Você ainda não salvou nenhuma simulação.</p> : <div className="table-responsive"><table className="table align-middle mb-0"><thead><tr><th scope="col">Cálculo</th><th scope="col">Resultado</th><th scope="col">Criado em</th><th scope="col">Validade</th><th scope="col"><span className="visually-hidden">Ações</span></th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><strong>{item.title}</strong></td><td>{money(item.value)}</td><td>{item.createdAt}</td><td>{item.pinned ? "Fixada" : item.expiresAt}</td><td className="text-end"><button className="btn btn-outline-primary btn-sm" type="button" onClick={() => onOpen(item.id)} disabled={loadingId === item.id}>Abrir</button><button className="btn btn-outline-secondary btn-sm ms-2" type="button" onClick={() => onPin(item.id)} disabled={loadingId === item.id}>{item.pinned ? "Desafixar" : "Fixar"}</button></td></tr>)}</tbody></table></div>}</section>;
}

export default function CalculatorsPage() {
  const [data, setData] = useState(null);
  const [activeId, setActiveId] = useState("");
  const [form, setForm] = useState({});
  const [result, setResult] = useState(null);
  const [resultSignature, setResultSignature] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showMemory, setShowMemory] = useState(false);
  const [savingId, setSavingId] = useState(null);
  const formRef = useRef(null);
  const csrf = data?.csrf || "";
  const calculators = useMemo(() => data ? [{ id: "advance", title: "Adiantamento salarial", fields: [] }, ...data.calculators] : [], [data?.calculators]);
  const calculatorButtons = useMemo(() => [...calculators.filter(item => item.id !== "advance"), ...calculators.filter(item => item.id === "advance")], [calculators]);
  const calculatorGroups = useMemo(() => categories.map(category => ({ category, items: calculatorButtons.filter(item => categoryById[item.id] === category) })).filter(group => group.items.length), [calculatorButtons]);
  const activeCalc = calculators.find(item => item.id === activeId);
  const signature = useMemo(() => JSON.stringify([activeId, form]), [activeId, form]);
  const stale = Boolean(result && resultSignature !== signature);

  const load = useCallback(async (id, preserve = false) => {
    const suffix = id ? `?id=${encodeURIComponent(id)}` : "";
    const response = await fetch(`/api/ui/calculators${suffix}`, { credentials: "same-origin", cache: "no-store" });
    const payload = await parseData(response);
    setData(payload);
    if (payload.selected) {
      const selected = payload.selected;
      const inputs = cleanSnapshot(selected.inputs);
      setActiveId(selected.calculatorId);
      setForm(selected.calculatorId === "advance" ? { ...blankAdvance(), ...inputs } : Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, String(value ?? "")])));
      setResult(selected.result);
      setResultSignature(JSON.stringify([selected.calculatorId, selected.calculatorId === "advance" ? { ...blankAdvance(), ...inputs } : Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, String(value ?? "")]))]));
    } else if (!id && !preserve) {
      setActiveId(""); setForm({}); setResult(null); setResultSignature("");
    }
    return payload;
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const selectedId = params.get("id");
    load(selectedId).then(payload => {
      const requested = params.get("calc");
      const item = requested === "advance" ? { id: "advance", fields: [] } : payload.calculators.find(entry => entry.id === requested);
      if (!selectedId && item) { setActiveId(item.id); setForm(makeForm(item)); }
      else if (!selectedId && !requested) { const initial = payload.calculators.find(entry => entry.id === "overtime"); if (initial) { setActiveId(initial.id); setForm(makeForm(initial)); } }
    }).catch(reason => setError(reason.message));
  }, [load]);

  const updateField = (key, value) => { setForm(current => ({ ...current, [key]: value })); setError(""); };
  const makeForm = item => item.id === "advance" ? blankAdvance() : Object.fromEntries((item.fields || []).map(field => [field.key, ""]));
  const openCalc = item => {
    setActiveId(item.id); setForm(makeForm(item)); setResult(null); setResultSignature(""); setError(""); setShowMemory(false);
    const target = `/rotinas/calculadoras?calc=${encodeURIComponent(item.id)}`;
    window.history.replaceState({}, "", target);
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };
  const resetForm = () => { if (activeCalc) setForm(makeForm(activeCalc)); setResult(null); setResultSignature(""); setError(""); setShowMemory(false); };
  const send = async action => {
    if (busy || stale && action === "save") return;
    const formElement = formRef.current?.querySelector("form");
    if (formElement && !formElement.reportValidity()) return;
    setBusy(true); setError("");
    try {
      const omitted = new Set(optionalBlankByCalculator[activeId] || []);
      const inputs = activeId === "advance" ? form : Object.fromEntries(Object.entries(form).filter(([key, value]) => !(value === "" && omitted.has(key))));
      const response = await fetch("/api/ui/calculators", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, action, calculatorId: activeId, inputs }) });
      const payload = await parseData(response);
      if (action === "save") {
        await load("", true); setActiveId(activeId); setForm(form); setResult(payload.result); setResultSignature(signature);
      } else {
        setResult(payload.result); setResultSignature(signature); setShowMemory(true);
      }
    } catch (reason) { setError(reason.message || "Não foi possível concluir este cálculo."); }
    finally { setBusy(false); }
  };
  const openSaved = async id => {
    setSavingId(id); setError("");
    try { await load(id); window.history.replaceState({}, "", `/rotinas/calculadoras?id=${encodeURIComponent(id)}`); setShowMemory(true); }
    catch (reason) { setError(reason.message); }
    finally { setSavingId(null); }
  };
  const togglePin = async id => {
    setSavingId(id); setError("");
    try {
      const response = await fetch("/api/ui/calculators", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, action: "pin", id }) });
      await parseData(response); await load("", true);
    } catch (reason) { setError(reason.message); }
    finally { setSavingId(null); }
  };

  return <>
    
    <div className="page-content calc-page">
    <nav aria-label="Caminho da página"><ol className="breadcrumb calc-breadcrumb"><li className="breadcrumb-item">Rotinas DP</li><li className="breadcrumb-item active" aria-current="page">Calculadoras DP</li></ol></nav>
    <header className="calc-page-head page-intro-toolbar"><div><p>Simule valores e confira cada etapa do cálculo.</p></div></header>
    {!data && !error && <div className="alert alert-info" role="status">Carregando calculadoras…</div>}
    {error && !activeId && <div className="alert alert-danger" role="alert">{error}{(error.includes("sessão expirou") || error.includes("tela de entrada")) && <a className="btn btn-sm btn-outline-danger ms-3" href="/entrar">Entrar novamente</a>}<button className="btn btn-sm btn-outline-danger ms-3" type="button" onClick={() => load().catch(reason => setError(reason.message))}>Tentar novamente</button></div>}
    <nav className="calc-shortcuts" aria-label="Escolher calculadora">{calculatorGroups.map(group => <section className="calc-shortcut-group" key={group.category} aria-label={group.category}><h2>{group.category}</h2><div className="calc-shortcut-options">{group.items.map(item => <button key={item.id} type="button" className={`btn btn-sm ${activeId === item.id ? "btn-primary" : "btn-outline-primary"}`} aria-pressed={activeId === item.id} onClick={() => { if (activeId !== item.id) openCalc(item); }}>{item.title}</button>)}</div></section>)}</nav>

    {activeCalc && <section className="calc-workspace" ref={formRef} aria-labelledby="calc-form-title"><div className="calc-workspace-grid">
      <div className="card calc-form-card"><div className="calc-form-card-title"><div><span className="calc-eyebrow">{categoryById[activeId] || categories[0]}</span><h2 id="calc-form-title">{activeCalc.title}</h2></div></div><form onSubmit={event => { event.preventDefault(); send("calculate"); }} noValidate={false}>
        {activeId === "advance" ? <AdvanceForm form={form} setField={updateField} companies={data?.companies || []} /> : <><div className="calc-form-group"><div className="calc-group-title"><span>01</span><div><h3>Parâmetros da simulação</h3><p>Preencha os campos necessários. Exemplos aparecem como dica e não entram no cálculo.</p></div></div><GenericForm calculator={activeCalc} form={form} setField={updateField} locations={data?.locations || []} /></div><div className="calc-source-note"><strong>Origem dos parâmetros</strong><span>Entradas desta simulação: informadas manualmente. Tabelas de INSS/IRRF: versão local publicada para a competência, se disponível.</span></div></>}
        {error && activeId && <div className="alert alert-danger calc-api-error" role="alert">{error}{(error.includes("sessão expirou") || error.includes("tela de entrada")) && <a className="btn btn-sm btn-outline-danger ms-3" href="/entrar">Entrar novamente</a>}</div>}
        <div className="calc-form-actions"><button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Calculando…" : activeId === "advance" ? "Calcular adiantamento" : "Calcular"}</button><button className="btn btn-outline-secondary" type="button" onClick={resetForm} disabled={busy}>Limpar campos</button>{result && !stale && <button className="btn btn-outline-primary" type="button" onClick={() => setShowMemory(value => !value)}>{showMemory ? "Ocultar memória" : "Ver memória do cálculo"}</button>}</div>
      </form></div>

      <div className="calc-result-column">
        {stale && <div className="alert alert-warning calc-stale" role="status"><strong>Os dados foram alterados. Recalcule para atualizar o resultado.</strong> O resultado antigo não pode ser salvo.</div>}
        <section className={`card calc-result-card ${stale ? "is-stale" : ""}`} aria-live="polite" aria-label="Resultado do cálculo"><div className="calc-section-head"><div><span className="calc-eyebrow">{result ? stale ? "RESULTADO DESATUALIZADO" : "RESULTADO VÁLIDO PARA ESTES DADOS" : "RESULTADO"}</span><h2>{activeId === "advance" ? "Adiantamento estimado" : "Resultado e memória"}</h2></div>{result && !stale && <button className="btn btn-outline-primary btn-sm" type="button" onClick={() => send("save")} disabled={busy}>{busy ? "Salvando…" : "Salvar simulação"}</button>}</div>
          {!result && <p className="text-secondary">Preencha os campos e clique em Calcular.</p>}
          {result && (activeId === "advance" ? <AdvanceResult result={result} /> : <div className="calc-result-body"><div className="calc-generic-result"><span>Resultado</span><strong>{activeId === "reduced" ? `${result.value} horas` : money(result.value)}</strong><small>Estimativa baseada nos dados e nas opções informadas.</small></div><p className="calc-source-note">Revise a regra aplicável e as premissas antes de usar o valor em qualquer rotina de folha.</p></div>)}
          {result && showMemory && <details className="calc-memory" open><summary>Memória do cálculo</summary><ol>{(result.memory || []).map((line, index) => <li key={index}>{line}</li>)}</ol></details>}
        </section>
      </div>
    </div>
    <details className="calc-how"><summary>Como calcular e quais são os limites</summary><div><p>{activeId === "advance" ? "A base depende do método escolhido, da modalidade, do período e das parcelas explicitamente incluídas. Nenhum percentual, divisor ou adicional é tratado como política da empresa sem cadastro verificável." : "O motor Python do servidor calcula com os parâmetros mostrados na memória. A tela não mantém uma fórmula paralela no navegador."}</p><ul><li>Os valores são simulações e não alteram cadastro nem processam folha.</li><li>O resultado arredonda para centavos ao final, quando a fórmula não indicar outra etapa.</li><li>Regras empresariais, convenções e bases legais precisam estar vigentes e confirmadas.</li></ul></div></details>
    </section>}

    {data && <SavedList items={data.saved || []} onOpen={openSaved} onPin={togglePin} loadingId={savingId} />}
    <footer className="calc-footer">Desenvolvido por <strong>Caio Caobianco</strong></footer>
    </div>
  </>;
}
