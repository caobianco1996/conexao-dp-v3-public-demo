"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const schedules = [["WEEKDAYS_5X2", "5x2"], ["ROTATING_6X1", "6x1"], ["ROTATING_6X2", "6x2"]];
const GENERAL_COMPANY_OPTION = "__EMPRESA_GERAL__";
const parseMoney = value => {
  let raw = String(value ?? "").trim().replace(/R\$/gi, "").replace(/\s/g, "");
  if (!raw) return NaN;
  if (/^-?\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(raw)) raw = raw.replace(/\./g, "").replace(",", ".");
  else if (/^-?\d+,\d{1,2}$/.test(raw)) raw = raw.replace(",", ".");
  else if (/^-?\d{1,3}(?:\.\d{3})+$/.test(raw)) raw = raw.replace(/\./g, "");
  else if (!/^-?\d+(?:\.\d{1,2})?$/.test(raw)) return NaN;
  const number = Number(raw);
  return Number.isFinite(number) ? Math.round(number * 100) : NaN;
};
const money = cents => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format((Number(cents) || 0) / 100);
const isoDate = value => {
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isNaN(date.valueOf()) ? value || "—" : date.toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
};
const stamp = value => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" });
};
const expiryLabel = plan => {
  if (plan.pinned) return `Fixado em ${stamp(plan.pinnedAt)} · não expira`;
  if (!plan.expiresAt) return "Prazo de expiração não informado";
  const remaining = new Date(plan.expiresAt).getTime() - Date.now();
  if (remaining <= 0) return `Expirado · prazo encerrou em ${stamp(plan.expiresAt)}`;
  const days = Math.ceil(remaining / 86_400_000);
  return `Expira em ${days} ${days === 1 ? "dia" : "dias"} · ${stamp(plan.expiresAt)}`;
};
const dateKey = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const dateAtNoon = value => new Date(`${value}T12:00:00`);
const moneyField = cents => (Number(cents || 0) / 100).toFixed(2);

async function readJson(response) {
  const raw = await response.text();
  let data;
  try { data = raw ? JSON.parse(raw) : {}; }
  catch { data = { error: `Resposta inválida do servidor (HTTP ${response.status}; ${response.headers.get("content-type") || "tipo não informado"}).` }; }
  if (!response.ok) {
    const error = new Error(data.error || "Não foi possível concluir a operação.");
    Object.assign(error, data);
    throw error;
  }
  return data;
}

function CalendarPreview({ result }) {
  const [monthIndex, setMonthIndex] = useState(0);
  const [selectedDay, setSelectedDay] = useState(null);
  const dayMap = useMemo(() => Object.fromEntries((result?.calendar || []).map(day => [day.day, day])), [result]);
  const months = useMemo(() => {
    const calculated = [...new Set((result?.calendar || []).map(day => day.day.slice(0, 7)))];
    return calculated;
  }, [result]);
  useEffect(() => { setMonthIndex(0); setSelectedDay(null); }, [result]);
  if (!result || !months.length) return null;
  const monthLabel = month => dateAtNoon(`${month}-01`).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
  const currentMonth = months[Math.min(monthIndex, months.length - 1)];
  const miniCalendar = (month, purpose) => {
    const first = dateAtNoon(`${month}-01`);
    const start = new Date(first);
    start.setDate(1 - first.getDay());
    const slots = Array.from({ length: 42 }, (_, index) => {
      const dayDate = new Date(start);
      dayDate.setDate(start.getDate() + index);
      return dayDate;
    });
    return <div className={`lp-mini-month lp-mini-month-${purpose}`} key={`${month}-${purpose}`}>
      <h4>{monthLabel(month)} <small>{purpose === "lots" ? "Cobertura prevista" : "Relação Bancária"}</small></h4>
      <div className="lp-mini-grid" role="grid" aria-label={`${monthLabel(month)} · ${purpose === "lots" ? "distribuição dos lotes" : "Relação Bancária"}`}>
        {["D", "S", "T", "Q", "Q", "S", "S"].map((day, index) => <strong className="lp-mini-weekday" key={`${day}-${index}`}>{day}</strong>)}
        {slots.map((date, index) => {
          const key = dateKey(date);
          const day = dayMap[key];
          const otherMonth = date.getMonth() !== first.getMonth();
          const status = !day ? "is-outside-period" : purpose === "lots"
            ? (!day.inPeriod ? "is-outside-period" : !day.work ? "is-day-off" : day.covered ? "is-covered" : "is-uncovered")
            : day.bankState === "YES" ? "is-bank-yes" : day.bankState === "NO" ? "is-bank-no" : "is-bank-unknown";
          const stateText = day?.bankState === "YES" ? "Relação Bancária: Sim" : day?.bankState === "NO" ? "Relação Bancária: Não" : day?.bankState === "OTHER" ? "Relação Bancária: outra ocorrência" : "Relação Bancária: não definida";
          const holidayText = day?.holidayDetails?.length ? day.holidayDetails.map(item => `${item.name} · ${item.scope || "abrangência não identificada"}${item.city ? ` · ${item.city}${item.state ? `/${item.state}` : ""}` : item.state ? ` · ${item.state}` : ""}`).join("; ") : day?.holidayNames?.length ? day.holidayNames.join(", ") : "sem feriado confirmado";
          const workText = !day ? "fora do período consultado" : day.work ? "dia previsto na escala" : "folga da escala";
          const coverageText = day?.inPeriod ? (day.covered ? `incluído no planejamento · lote ${day.lotNumber || "não identificado"}` : day.eligible ? "dia elegível sem lote" : `não incluído · ${day.reason || "fora da elegibilidade"}`) : "fora do período de cobertura";
          const bankText = result.bank_relation_enabled ? stateText : `${stateText} · consulta visual, não aplicada ao cálculo`;
          const title = `${new Intl.DateTimeFormat("pt-BR", { weekday: "long", timeZone: "America/Sao_Paulo" }).format(date)} ${isoDate(key)} · Trabalho: ${workText} · Feriados: ${holidayText} · ${purpose === "lots" ? `Cobertura: ${coverageText}` : bankText}${day?.payDate ? ` · Data sugerida para processamento do lote ${day.paymentLot || ""}` : ""}`;
          const marker = purpose === "lots" ? (day?.lotNumber ? `L${day.lotNumber}` : "") : (day?.bankState === "YES" ? "S" : day?.bankState === "NO" ? "N" : day ? "?" : "");
          const selected = selectedDay?.key === key;
          return <button type="button" role="gridcell" aria-selected={selected} className={`lp-mini-day ${status}${otherMonth ? " is-other-month" : ""}${selected ? " is-selected" : ""}`} key={`${purpose}-${key}`} title={title} aria-label={title} onClick={() => setSelectedDay({ key, day, title })}>
            <b>{date.getDate()}</b>{marker && <i className="lp-day-state" aria-hidden="true">{marker}</i>}{day?.holidayDetails?.length > 0 && <i className="lp-day-holiday-mark" aria-hidden="true">F</i>}
          </button>;
        })}
      </div>
    </div>;
  };
  const holidayDays = (result?.calendar || []).filter(day => day.holidayDetails?.length || day.holidayNames?.length);
  const selected = selectedDay?.day;
  return <section className="card lp-calendar-card" aria-label="Comparação dos calendários do planejamento">
    <div className="lp-calendar-heading"><div><span className="eyebrow">Compare as datas</span><h3>Calendário dos lotes e Relação Bancária</h3><p>Cobertura prevista e disponibilidade bancária são estados separados; feriados permanecem identificados nas duas grades.</p></div><div className="lp-month-controls"><button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setMonthIndex(value => Math.max(0, value - 1))} disabled={monthIndex === 0} aria-label="Mês anterior">‹</button><strong>{monthLabel(currentMonth)}</strong><button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setMonthIndex(value => Math.min(months.length - 1, value + 1))} disabled={monthIndex >= months.length - 1} aria-label="Próximo mês">›</button>{monthIndex !== 0 && <button type="button" className="btn btn-link btn-sm" onClick={() => setMonthIndex(0)}>Ir para o período</button>}</div></div>
    <div className="lp-legend lp-legend-groups"><span><i className="is-covered"/>Incluído no planejamento</span><span><i className="is-uncovered"/>Dia elegível sem cobertura</span><span><i className="is-rest"/>Folga ou fora do período</span><span><i className="is-bank-yes"/>Relação Sim</span><span><i className="is-bank-no"/>Relação Não</span><span><i className="is-bank-unknown"/>Não definido (?)</span><span><i className="is-holiday"/>Feriado (F)</span></div>
    <div className="lp-mini-months">{miniCalendar(currentMonth, "lots")}{miniCalendar(currentMonth, "bank")}</div>
    {selectedDay && <div className="lp-day-detail" role="status" aria-live="polite"><div><strong>{selectedDay.title}</strong><button type="button" className="btn-close" aria-label="Fechar detalhe do dia" onClick={() => setSelectedDay(null)}/></div><span>Trabalho: {!selected ? "não confirmado" : selected.work ? "previsto" : "folga"} · Relação Bancária: {selected?.bankState === "YES" ? "Sim" : selected?.bankState === "NO" ? "Não" : selected?.bankState === "OTHER" ? "outra ocorrência" : "Não definido"} · Cobertura: {selected?.covered ? `incluída no lote ${selected.lotNumber}` : selected?.eligible ? "elegível, sem lote" : selected?.reason || "fora do período consultado"}</span>{selected?.holidayDetails?.map((holiday, index) => <span key={`${holiday.name}-${index}`}>Feriado: {holiday.name} · {holiday.scope}{holiday.city ? ` · ${holiday.city}${holiday.state ? `/${holiday.state}` : ""}` : holiday.state ? ` · ${holiday.state}` : ""}</span>)}</div>}
    {holidayDays.length > 0 ? <><h4 className="lp-holiday-heading">Feriados no período</h4><ul className="lp-holiday-list" aria-label="Feriados do período">{holidayDays.map((day, dayIndex) => (day.holidayDetails?.length ? day.holidayDetails : day.holidayNames.map(name => ({ name, scope: "Abrangência não identificada" }))).map((holiday, index) => <li key={`${day.day}-${holiday.name}-${index}`}><strong>{isoDate(day.day)}</strong><span>{holiday.name} · {holiday.scope}{holiday.city ? ` · ${holiday.city}${holiday.state ? `/${holiday.state}` : ""}` : holiday.state ? ` · ${holiday.state}` : ""}</span></li>))}</ul></> : <p className="lp-calendar-source">Nenhum feriado aplicável encontrado no snapshot consultado.</p>}
    <p className="lp-calendar-source">Relação Bancária {result.bank_relation_enabled ? "considerada no cálculo" : "consultada para comparação, sem filtrar o cálculo"}: {isoDate(result.bank_query_period?.from)} a {isoDate(result.bank_query_period?.through)}. As datas exibidas são sugestões de processamento, não pagamentos realizados.</p>
  </section>;
}

function PlanRevision({ revision }) {
  const inputs = revision.inputs || {};
  const result = revision.result || {};
  return <details className="lp-revision">
    <summary>Versão {revision.rowVersion} · {revision.title} · {stamp(revision.savedAt)} · {money(result.total_cents)}</summary>
    <div className="lp-revision-body">
      <p>{inputs.employee_name || "Sem identificação"} · {inputs.company_name || revision.snapshot?.company?.name || "Empresa"} · {inputs.benefit_type || "Benefício"}</p>
      <p>Admissão {isoDate(inputs.admission_date)} · corte {isoDate(result.cutoff_date)} · pagamento {isoDate(result.effective_payroll_date)} · {inputs.schedule === "WEEKDAYS_5X2" ? "escala 5x2 (segunda a sexta)" : `escala ${inputs.schedule || "—"} · ciclo iniciado em ${isoDate(inputs.schedule_start)}`}</p>
      <div className="table-responsive lp-table-wrap"><table className="table table-sm align-middle"><thead><tr><th>Lote</th><th>Pagamento</th><th>Cobertura inclusiva</th><th>Dias</th><th>Quantidade</th><th>Valor</th></tr></thead><tbody>{(result.lots || []).map(lot => <tr key={lot.number}><th scope="row">{lot.number}</th><td>{isoDate(lot.pay_date)}</td><td>{isoDate(lot.from)} a {isoDate(lot.to)}</td><td>{lot.eligible_days}</td><td>{lot.quantity}</td><td>{money(lot.amount_cents)}</td></tr>)}</tbody><tfoot><tr><th colSpan="5">Total da versão</th><th>{money(result.total_cents)}</th></tr></tfoot></table></div>
      <details><summary>Memória e calendário desta versão</summary><ul>{(result.memory || []).map((line, index) => <li key={index}>{line}</li>)}</ul><CalendarPreview result={result}/></details>
      <small>Snapshot original salvo por {revision.savedBy || "usuário local"}; a edição posterior não altera estes dados.</small>
    </div>
  </details>;
}

export default function LotPlanningPage() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [activeEmployeeIndex, setActiveEmployeeIndex] = useState(0);
  const [planQuery, setPlanQuery] = useState("");
  const [planBenefit, setPlanBenefit] = useState("");
  const [planStatus, setPlanStatus] = useState("current");
  const [planAuthor, setPlanAuthor] = useState("");
  const [planSort, setPlanSort] = useState("recent");
  const [planPage, setPlanPage] = useState(0);
  const [companyKey, setCompanyKey] = useState("");
  const [generalPlanMode, setGeneralPlanMode] = useState(false);
  const [employeeKey, setEmployeeKey] = useState("");
  const [subjectName, setSubjectName] = useState("");
  const [benefit, setBenefit] = useState("VT");
  const [planPaymentDay, setPlanPaymentDay] = useState("");
  const [manualPaymentAdjustment, setManualPaymentAdjustment] = useState("PREVIOUS_WORKDAY");
  const [manualHolidayRule, setManualHolidayRule] = useState("EXCLUDE");
  const [manualVrMode, setManualVrMode] = useState("");
  const [manualBenefitValue, setManualBenefitValue] = useState("");
  const [manualBenefitUses, setManualBenefitUses] = useState("");
  const [manualFebruaryPolicy, setManualFebruaryPolicy] = useState("NEXT_MONTH");
  const [manualAdmissionCutoffPolicy, setManualAdmissionCutoffPolicy] = useState("NEXT_CYCLE");
  const [vtTariff, setVtTariff] = useState("");
  const [vtUsesPerDay, setVtUsesPerDay] = useState("");
  const [admission, setAdmission] = useState("");
  const [admissionReason, setAdmissionReason] = useState("");
  const [lotCount, setLotCount] = useState("3");
  const lotCountTouched = useRef(false);
  const [schedule, setSchedule] = useState("");
  const [scheduleStart, setScheduleStart] = useState("");
  const [respectBank, setRespectBank] = useState(false);
  const [ruleDraft, setRuleDraft] = useState({ payment_day: "", payment_adjustment: "", holiday_benefit_rule: "", unit_mode: "", february_30_policy: "UNRESOLVED", admission_on_cutoff_policy: "UNRESOLVED", amount: "", uses_per_day: "", zero_value_reason: "", valid_from: "", change_reason: "" });
  const [preview, setPreview] = useState(null);
  const [editing, setEditing] = useState(null);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const lastEmployee = useRef("");
  const saveRequestKey = useRef("");
  const draftBaseline = useRef(null);
  const latestFormSignature = useRef("");
  const latestSchedule = useRef("");
  const calculationRequestId = useRef(0);

  const fetchWorkspace = useCallback(async (signal) => {
    const params = new URLSearchParams({ q: query.trim(), employee: employeeKey, company: companyKey, benefit, admission,
      plan_q: planQuery.trim(), plan_benefit: planBenefit, plan_status: planStatus, plan_author: planAuthor });
    const response = await fetch(`/api/ui/lot-plans?${params}`, { credentials: "same-origin", cache: "no-store", signal });
    return readJson(response);
  }, [query, companyKey, employeeKey, benefit, admission, planQuery, planBenefit, planStatus, planAuthor, reload]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true); setError("");
      fetchWorkspace(controller.signal).then(setData).catch(reason => { if (reason.name !== "AbortError") setError(reason.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, query || planQuery ? 220 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [fetchWorkspace]);

  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(""), 5000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const employee = data?.selectedEmployee?.key === employeeKey ? data.selectedEmployee : null;
  const company = data?.selectedCompany?.key === companyKey ? data.selectedCompany : null;
  const matchingEmployees = data?.employees || [];
  const requestedAdmission = admission || employee?.admission_date || "";
  const companyContextReady = Boolean(companyKey && data?.benefit === benefit && data?.selectedCompany?.key === companyKey && data?.ruleEffectiveOn === requestedAdmission);
  const rule = companyContextReady ? data.rule : null;
  const companyPaymentDayKnown = [5, 30].includes(Number(rule?.payment_day));
  const manualRuleMode = generalPlanMode || Boolean(companyContextReady && companyKey && !rule);
  const hasPlanningContext = generalPlanMode || Boolean(companyKey || employee);
  const canWrite = Boolean(data?.canWrite);
  const canConfigure = Boolean(data?.canConfigure);
  const rotatingSchedule = schedule === "ROTATING_6X1" || schedule === "ROTATING_6X2";

  useEffect(() => {
    if (!employee || employee.key === lastEmployee.current) return;
    lastEmployee.current = employee.key;
    setAdmission(employee.admission_date || "");
    setAdmissionReason(""); setSchedule(""); setScheduleStart("");
    setPreview(null); setEditing(null); setSelectedDetail(null); setTitle("");
  }, [employee]);

  useEffect(() => {
    if (!rule) {
      setRuleDraft({ payment_day: "", payment_adjustment: "", holiday_benefit_rule: "", unit_mode: benefit === "VT" ? "PER_USE" : "", february_30_policy: "UNRESOLVED", admission_on_cutoff_policy: "UNRESOLVED", amount: "", uses_per_day: benefit === "VR" ? "1" : "", zero_value_reason: "", valid_from: "", change_reason: "" });
      return;
    }
    setRuleDraft({ payment_day: String(rule.payment_day), payment_adjustment: rule.payment_adjustment,
      holiday_benefit_rule: rule.holiday_benefit_rule, amount: moneyField(rule.unit_cents),
      unit_mode: rule.unit_mode || (benefit === "VT" ? "PER_USE" : "UNSPECIFIED"),
      february_30_policy: rule.february_30_policy || "UNRESOLVED",
      admission_on_cutoff_policy: rule.admission_on_cutoff_policy || "UNRESOLVED",
      uses_per_day: String(rule.uses_per_day), zero_value_reason: rule.zero_value_reason || "", valid_from: rule.valid_from || "", change_reason: "" });
  }, [rule?.id, rule?.payment_day, rule?.payment_adjustment, rule?.holiday_benefit_rule,
      rule?.unit_cents, rule?.unit_mode, rule?.february_30_policy, rule?.admission_on_cutoff_policy,
      rule?.uses_per_day, rule?.zero_value_reason, rule?.valid_from, benefit, company?.key]);

  const postAction = useCallback(async payload => {
    if (!data?.csrf) throw new Error("A sessão local não está pronta. Recarregue a página autenticada.");
    const response = await fetch("/api/ui/lot-plans", { method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: data.csrf, ...payload }) });
    return readJson(response);
  }, [data]);

  const formInput = () => ({ employee_key: generalPlanMode ? "" : employeeKey, company_key: generalPlanMode ? "" : companyKey, benefit_type: benefit, admission_date: admission,
    admission_override_reason: admissionReason, lot_count: lotCount, schedule, schedule_start: rotatingSchedule ? scheduleStart : "",
    respect_bank: respectBank, zero_value_reason: ruleDraft.zero_value_reason,
    vt_unit_cents: benefit === "VT" ? parseMoney(vtTariff) : null,
    vt_uses_per_day: benefit === "VT" ? vtUsesPerDay : null,
    employee_name: subjectName.trim(),
    payment_source: generalPlanMode ? "GENERAL" : companyPaymentDayKnown ? "COMPANY" : "MANUAL",
    payment_day_override: generalPlanMode || !companyPaymentDayKnown ? planPaymentDay : "",
    manual_payment_adjustment: manualRuleMode ? manualPaymentAdjustment : "",
    manual_holiday_benefit_rule: manualRuleMode ? manualHolidayRule : "",
    benefit_unit_mode: manualRuleMode ? (benefit === "VT" ? "PER_USE" : manualVrMode) : rule?.unit_mode,
    benefit_unit_cents: manualRuleMode && benefit === "VR" ? parseMoney(manualBenefitValue) : null,
    benefit_uses_per_day: manualRuleMode && benefit === "VR" ? manualBenefitUses : null,
    february_30_policy: manualRuleMode ? manualFebruaryPolicy : rule?.february_30_policy,
    admission_on_cutoff_policy: manualRuleMode ? manualAdmissionCutoffPolicy : rule?.admission_on_cutoff_policy,
  });
  const currentInputSignature = JSON.stringify(formInput());
  latestFormSignature.current = currentInputSignature;
  latestSchedule.current = schedule;
  const currentDraftSignature = JSON.stringify({ input: formInput(), title: title.trim() });

  const runCalculation = async event => {
    event?.preventDefault();
    const requestId = ++calculationRequestId.current;
    const submittedSignature = currentInputSignature;
    saveRequestKey.current = "";
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    if (!editing) setSelectedDetail(null);
    try {
      const result = await postAction({ action: "calculate", ...formInput() });
      const formChanged = latestFormSignature.current !== submittedSignature;
      setPreview({ ...result, formSignature: submittedSignature });
      setNotice(formChanged
        ? "Prévia calculada com os dados enviados. Como os campos mudaram durante o cálculo, confira os valores e recalcule antes de salvar."
        : result.message);
    } catch (reason) {
      const formChanged = latestFormSignature.current !== submittedSignature;
      if (formChanged && reason.message === "Informe a data inicial do ciclo da escala." && latestSchedule.current === "WEEKDAYS_5X2") {
        setError("");
        setNotice("A tentativa anterior usava uma escala rotativa e terminou após a troca para 5x2. Revise os campos e calcule novamente.");
        return;
      }
      setError(reason.message);
      if (reason.existingPlanId) setNotice(`Já existe o planejamento ${reason.existingTitle || "ativo"}. Abra o registro existente na lista.`);
    } finally { if (calculationRequestId.current === requestId) setBusy(false); }
  };

  const saveRule = async event => {
    event.preventDefault();
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    try {
      const amountCents = benefit === "VT" ? Number(rule?.unit_cents ?? 0) : parseMoney(ruleDraft.amount);
      if (benefit === "VR" && !Number.isInteger(amountCents)) throw new Error("Informe o valor em reais usando centavos, por exemplo 5,75.");
      const result = await postAction({ action: "save_rule", company_key: company?.key, benefit_type: benefit,
        payment_day: ruleDraft.payment_day, payment_adjustment: ruleDraft.payment_adjustment,
        holiday_benefit_rule: ruleDraft.holiday_benefit_rule, unit_cents: amountCents,
        unit_mode: benefit === "VT" ? "PER_USE" : ruleDraft.unit_mode,
        february_30_policy: ruleDraft.february_30_policy,
        admission_on_cutoff_policy: ruleDraft.admission_on_cutoff_policy,
        uses_per_day: benefit === "VT" ? Number(rule?.uses_per_day || 1) : ruleDraft.unit_mode === "PER_DAY" ? 1 : ruleDraft.uses_per_day,
        zero_value_reason: ruleDraft.zero_value_reason,
        valid_from: ruleDraft.valid_from, change_reason: ruleDraft.change_reason });
      setNotice(result.message); setReload(value => value + 1);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const savePlan = async () => {
    if (!preview?.result || !preview?.calculationToken || preview.formSignature !== currentInputSignature || preview.result.can_save === false || busy) return;
    setBusy(true); setError(""); setNotice("");
    const requestKey = saveRequestKey.current || (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    saveRequestKey.current = requestKey;
    try {
      const result = await postAction({ action: editing ? "update" : "save", ...formInput(),
        title: title.trim(), plan_id: editing?.id, expected_version: editing?.rowVersion,
        calculation_token: preview.calculationToken, idempotency_key: requestKey });
      setNotice(result.message); draftBaseline.current = currentDraftSignature; setEditing(null); setSelectedDetail(null); setPreview(null); saveRequestKey.current = ""; setReload(value => value + 1);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const openPlan = async planId => {
    if (draftDirty && !window.confirm("Há alterações neste planejamento que ainda não foram salvas. Descartar e abrir o registro selecionado?")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/ui/lot-plans?id=${encodeURIComponent(planId)}`, { credentials: "same-origin", cache: "no-store" });
      const initial = await readJson(response);
      const detail = initial.detail;
      if (!detail) throw new Error("Planejamento não encontrado.");
      const params = new URLSearchParams({ employee: detail.inputs.employee_key || "", company: detail.inputs.company_key || "",
        admission: detail.inputs.admission_date || "", benefit: detail.inputs.benefit_type, id: String(planId) });
      const contextResponse = await fetch(`/api/ui/lot-plans?${params}`, { credentials: "same-origin", cache: "no-store" });
      const loaded = await readJson(contextResponse);
      const selected = loaded.detail || detail;
      lastEmployee.current = selected.inputs.employee_key || "";
      setData(loaded); setSelectedDetail(selected);
      setGeneralPlanMode(!selected.inputs.employee_key && selected.inputs.payment_source === "GENERAL");
      setCompanyKey(selected.inputs.company_key || "");
      setEmployeeKey(selected.inputs.employee_key || ""); setBenefit(selected.inputs.benefit_type);
      setSubjectName(selected.inputs.employee_name || selected.snapshot?.employee?.name || "");
      setAdmission(selected.inputs.admission_date); setAdmissionReason(selected.inputs.admission_override_reason || "");
      setPlanPaymentDay(String(selected.inputs.payment_day_used || selected.inputs.payment_day_override || ""));
      setManualPaymentAdjustment(selected.snapshot?.rule?.payment_adjustment || "PREVIOUS_WORKDAY");
      setManualHolidayRule(selected.snapshot?.rule?.holiday_benefit_rule || "EXCLUDE");
      setManualVrMode(selected.inputs.unit_mode || selected.snapshot?.rule?.unit_mode || "");
      setManualBenefitValue(selected.inputs.benefit_type === "VR" ? moneyField(selected.inputs.unit_cents || 0).replace(".", ",") : "");
      setManualBenefitUses(String(selected.inputs.uses_per_day || ""));
      setManualFebruaryPolicy(selected.snapshot?.rule?.february_30_policy || "NEXT_MONTH");
      setManualAdmissionCutoffPolicy(selected.snapshot?.rule?.admission_on_cutoff_policy || "NEXT_CYCLE");
      setVtTariff(selected.inputs.benefit_type === "VT" && (selected.inputs.unit_cents ?? selected.snapshot?.rule?.unit_cents) ? moneyField(selected.inputs.unit_cents ?? selected.snapshot?.rule?.unit_cents) : "");
      setVtUsesPerDay(selected.inputs.benefit_type === "VT" && (selected.inputs.uses_per_day ?? selected.snapshot?.rule?.uses_per_day) ? String(selected.inputs.uses_per_day ?? selected.snapshot?.rule?.uses_per_day) : "");
      lotCountTouched.current = true;
      setLotCount(String(selected.inputs.lot_count)); setSchedule(selected.inputs.schedule); setScheduleStart(selected.inputs.schedule === "WEEKDAYS_5X2" ? "" : selected.inputs.schedule_start);
      setRespectBank(Boolean(selected.inputs.respect_bank));
      setTitle(selected.title); setPreview({ result: selected.result, snapshot: selected.snapshot, formSignature: "" });
      setEditing(null); saveRequestKey.current = "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const togglePin = async plan => {
    setBusy(true); setError("");
    try { const result = await postAction({ action: "pin", plan_id: plan.id, pinned: !plan.pinned }); setNotice(result.message); setReload(value => value + 1); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const deletePlan = async plan => {
    const confirmed = window.confirm(`Excluir o planejamento de ${plan.employeeName}, empresa ${plan.companyName}, benefício ${plan.benefitType} e corte ${isoDate(plan.cutoffDate)}? A combinação será liberada; o registro ficará preservado no histórico de auditoria.`);
    if (!confirmed) return;
    setBusy(true); setError("");
    try { const result = await postAction({ action: "delete", plan_id: plan.id }); setNotice(result.message); if (selectedDetail?.id === plan.id) { setSelectedDetail(null); setPreview(null); setEditing(null); } setReload(value => value + 1); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const chooseEmployee = item => {
    if (employeeKey !== item.key && draftDirty && !window.confirm("Descartar as alterações atuais e usar os dados deste vínculo?")) return;
    draftBaseline.current = null;
    setGeneralPlanMode(false);
    setCompanyKey(item.companyKey || companyKey); setEmployeeKey(item.key); setAdmission(item.admission_date || "");
    setSubjectName(item.name || ""); setQuery(""); setActiveEmployeeIndex(0);
    setVtTariff(""); setVtUsesPerDay(""); setPlanPaymentDay(""); setManualPaymentAdjustment("PREVIOUS_WORKDAY"); setManualHolidayRule("EXCLUDE");
    setManualVrMode(""); setManualBenefitValue(""); setManualBenefitUses("");
    setManualFebruaryPolicy("NEXT_MONTH"); setManualAdmissionCutoffPolicy("NEXT_CYCLE");
    setSchedule(""); setScheduleStart("");
    setPreview(null); setEditing(null); setSelectedDetail(null); setTitle(""); saveRequestKey.current = "";
  };

  const chooseCompany = value => {
    draftBaseline.current = null;
    setGeneralPlanMode(false);
    setCompanyKey(value); setEmployeeKey(""); setQuery(""); setActiveEmployeeIndex(0); setAdmission(""); setAdmissionReason(""); setSubjectName("");
    setVtTariff(""); setVtUsesPerDay("");
    setPlanPaymentDay(""); setManualPaymentAdjustment("PREVIOUS_WORKDAY"); setManualHolidayRule("EXCLUDE");
    setManualVrMode(""); setManualBenefitValue(""); setManualBenefitUses("");
    setManualFebruaryPolicy("NEXT_MONTH"); setManualAdmissionCutoffPolicy("NEXT_CYCLE");
    setSchedule(""); setScheduleStart(""); setRespectBank(false);
    setPreview(null); setEditing(null); setSelectedDetail(null); setTitle(""); saveRequestKey.current = "";
  };

  const chooseGeneralPlan = () => {
    draftBaseline.current = null;
    setGeneralPlanMode(true);
    setCompanyKey(""); setEmployeeKey(""); setQuery(""); setActiveEmployeeIndex(0); setAdmission(""); setAdmissionReason(""); setSubjectName("");
    setVtTariff(""); setVtUsesPerDay("");
    setPlanPaymentDay(""); setManualPaymentAdjustment("PREVIOUS_WORKDAY"); setManualHolidayRule("EXCLUDE");
    setManualVrMode(""); setManualBenefitValue(""); setManualBenefitUses("");
    setManualFebruaryPolicy("NEXT_MONTH"); setManualAdmissionCutoffPolicy("NEXT_CYCLE");
    setSchedule(""); setScheduleStart(""); setRespectBank(false);
    setPreview(null); setEditing(null); setSelectedDetail(null); setTitle(""); saveRequestKey.current = "";
  };

  const startNewPlan = () => {
    if (draftDirty && !window.confirm("Descartar as alterações não salvas e iniciar um novo planejamento?")) return;
    draftBaseline.current = null;
    setGeneralPlanMode(false);
    setCompanyKey(""); setEmployeeKey(""); setQuery(""); setActiveEmployeeIndex(0); setAdmission(""); setAdmissionReason(""); setSubjectName("");
    setVtTariff(""); setVtUsesPerDay("");
    setPlanPaymentDay(""); setManualPaymentAdjustment("PREVIOUS_WORKDAY"); setManualHolidayRule("EXCLUDE");
    setManualVrMode(""); setManualBenefitValue(""); setManualBenefitUses("");
    setManualFebruaryPolicy("NEXT_MONTH"); setManualAdmissionCutoffPolicy("NEXT_CYCLE");
    setBenefit("VT"); setLotCount("3"); lotCountTouched.current = false; setSchedule(""); setScheduleStart("");
    setRespectBank(false); setPreview(null); setEditing(null);
    setSelectedDetail(null); setTitle(""); saveRequestKey.current = "";
  };

  const changeBenefit = value => { if (value !== benefit && draftDirty && !window.confirm("Descartar a prévia e as alterações atuais para trocar o benefício?")) return; setBenefit(value); setManualVrMode(""); setManualBenefitValue(""); setManualBenefitUses(""); if (!lotCountTouched.current) setLotCount(value === "VR" ? "2" : "3"); setPreview(null); setEditing(null); setSelectedDetail(null); saveRequestKey.current = ""; };
  const sourceAdmission = employee?.admission_date || "";
  const eligibility = preview?.result?.eligible_dates?.length ?? 0;
  const result = preview?.result || null;
  const pageSize = 20;
  const sortedPlans = useMemo(() => {
    const values = [...(data?.plans || [])];
    if (planSort === "recent") return values;
    return values.sort((left, right) => {
      if (left.pinned !== right.pinned) return Number(right.pinned) - Number(left.pinned);
      if (planSort === "employee") return left.employeeName.localeCompare(right.employeeName, "pt-BR");
      if (planSort === "cutoff") return String(left.cutoffDate).localeCompare(String(right.cutoffDate));
      return String(left.expiresAt || "9999").localeCompare(String(right.expiresAt || "9999"));
    });
  }, [data?.plans, planSort]);
  const totalPlanPages = Math.max(1, Math.ceil(sortedPlans.length / pageSize));
  const visiblePlans = sortedPlans.slice(planPage * pageSize, (planPage + 1) * pageSize);
  useEffect(() => { if (planPage >= totalPlanPages) setPlanPage(totalPlanPages - 1); }, [planPage, totalPlanPages]);
  useEffect(() => {
    if (selectedDetail && !editing) draftBaseline.current = currentDraftSignature;
  }, [selectedDetail?.id, editing, currentDraftSignature]);

  return <>
    
    <main className="container-fluid page-content lp-page">
      <div className="lp-intro"><p>Planeje a divisão e a cobertura prevista de VT e VR. O cálculo é uma previsão e não realiza nem registra pagamentos.</p><button type="button" className="btn btn-primary btn-sm" onClick={startNewPlan}>Novo planejamento</button></div>
      {error && <div className="alert alert-danger lp-alert" role="alert">{error}</div>}
      {notice && <div className="alert alert-success lp-alert" role="status">{notice}</div>}

      <section className="card lp-card lp-planner-card">
        <div className="lp-planner-heading"><div><span className="eyebrow">Benefício · primeira etapa</span><h2>Planejamento de {benefit}</h2></div><div className="btn-group lp-benefit-switch" role="group" aria-label="Tipo de benefício"><button type="button" className={`btn btn-sm ${benefit === "VT" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => changeBenefit("VT")}>VT</button><button type="button" className={`btn btn-sm ${benefit === "VR" ? "btn-primary" : "btn-outline-primary"}`} onClick={() => changeBenefit("VR")}>VR</button></div></div>
          {!canWrite && !selectedDetail && <p className="lp-readonly-note">Modo de consulta: selecione uma empresa ou Empresa geral para consultar os dados. Operador, Gestor ou Administrador pode calcular e salvar.</p>}
        {selectedDetail && !editing && <div className="lp-snapshot-actions"><p>Consulta do planejamento salvo: memória e valores exibidos são o snapshot original.</p>{canWrite ? <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setEditing({ id: selectedDetail.id, rowVersion: selectedDetail.rowVersion })}>Editar / recalcular</button> : <span className="badge text-bg-light border">Somente leitura</span>}</div>}
        {!selectedDetail || editing ? <>
          <label className="form-label lp-company-field">Empresa
            <select className="form-select" value={generalPlanMode ? GENERAL_COMPANY_OPTION : companyKey} onChange={event => event.target.value === GENERAL_COMPANY_OPTION ? chooseGeneralPlan() : chooseCompany(event.target.value)} required>
              <option value="">Selecione a empresa</option>
              <option value={GENERAL_COMPANY_OPTION}>Empresa geral · sem empresa específica</option>
              {data?.companies?.filter(item => item.active).map(item => <option value={item.key} key={item.key}>{item.code ? `${item.code} · ` : ""}{item.name} · ${item.headcount} ativo(s)</option>)}
            </select>
            <small>{generalPlanMode ? "Planejamento sem empresa ou colaborador específicos; feriados nacionais são considerados." : "A empresa define a regra aplicável. Sem regra vigente para a admissão, o dia é escolhido neste planejamento."}</small>
          </label>

          <form className="lp-planner-form" onSubmit={runCalculation}>

          <label className="form-label">Data de admissão<input className="form-control" type="date" value={admission} disabled={!canWrite || (!generalPlanMode && !companyKey && !employee) || (Boolean(employee) && !canConfigure)} onChange={event => { setAdmission(event.target.value); setPreview(null); }} required={hasPlanningContext}/><small>{employee ? `Admissão do vínculo selecionado: ${isoDate(sourceAdmission)}.` : "Informe a data; não é necessário localizar um vínculo na integração."}</small></label>
          <fieldset className="lp-payday-choice"><legend>Pagamento normal</legend>{generalPlanMode ? <div className="lp-payment-options"><label className="form-check"><input className="form-check-input" type="radio" name="lp-payment-day" required checked={planPaymentDay === "5"} disabled={!canWrite} onChange={() => { setPlanPaymentDay("5"); setPreview(null); }}/><span>Dia 05</span></label><label className="form-check"><input className="form-check-input" type="radio" name="lp-payment-day" required checked={planPaymentDay === "30"} disabled={!canWrite} onChange={() => { setPlanPaymentDay("30"); setPreview(null); }}/><span>Dia 30</span></label></div> : companyPaymentDayKnown ? <strong className="lp-payment-locked">Dia {String(rule.payment_day).padStart(2, "0")} · regra da empresa</strong> : companyContextReady ? <div className="lp-payment-options"><label className="form-check"><input className="form-check-input" type="radio" name="lp-payment-day" required checked={planPaymentDay === "5"} disabled={!canWrite} onChange={() => { setPlanPaymentDay("5"); setPreview(null); }}/><span>Dia 05</span></label><label className="form-check"><input className="form-check-input" type="radio" name="lp-payment-day" required checked={planPaymentDay === "30"} disabled={!canWrite} onChange={() => { setPlanPaymentDay("30"); setPreview(null); }}/><span>Dia 30</span></label></div> : <span className="text-secondary">{companyKey ? "Consultando regra da empresa…" : "Selecione uma empresa primeiro."}</span>}<small>{companyPaymentDayKnown && !generalPlanMode ? `Vigente desde ${isoDate(rule.valid_from)}; aplicado sem alteração manual.` : generalPlanMode ? "Escolha o dia para este planejamento; a escolha não cria regra permanente." : companyContextReady ? "Sem regra vigente para esta admissão; escolha o dia neste planejamento." : "A seleção da empresa determina a regra aplicável."}</small></fieldset>

          {manualRuleMode && benefit === "VR" && <label className="form-label">Unidade do valor do VR<select className="form-select" value={manualVrMode} disabled={!canWrite} onChange={event => { setManualVrMode(event.target.value); if (event.target.value === "PER_DAY") setManualBenefitUses("1"); setPreview(null); }} required><option value="">Selecione</option><option value="PER_DAY">Valor total por dia</option><option value="PER_MEAL">Valor por refeição</option></select><small>Necessária para aplicar o valor diário informado.</small></label>}

          {canConfigure && employee && admission !== sourceAdmission && <label className="form-label lp-wide">Justificativa para corrigir a admissão do snapshot<input className="form-control" maxLength="500" value={admissionReason} onChange={event => setAdmissionReason(event.target.value)} required placeholder="Explique a origem da correção"/></label>}
          <fieldset className="lp-lot-choice"><legend>Quantidade de lotes</legend>{[1, 2, 3].map(count => <label className="form-check" key={count}><input className="form-check-input" type="radio" name="lp-lot-count" value={count} checked={lotCount === String(count)} disabled={!canWrite} onChange={event => { lotCountTouched.current = true; setLotCount(event.target.value); setPreview(null); }}/><span>{count} {count === 1 ? "lote" : "lotes"}</span></label>)}<small>Sugestão inicial: VT 3 lotes · VR 2 lotes. Você pode escolher 1, 2 ou 3.</small></fieldset>
          <label className="form-label">{benefit === "VT" ? "Tarifa por utilização do VT (R$)" : ((manualRuleMode ? manualVrMode : rule?.unit_mode) === "PER_DAY" ? "Valor diário do VR" : "Valor do VR por refeição")}
            {benefit === "VT" ? <input className="form-control" type="text" inputMode="decimal" value={vtTariff} onChange={event => { setVtTariff(event.target.value); setPreview(null); }} disabled={!canWrite || !hasPlanningContext} required={Boolean(canWrite && hasPlanningContext)} placeholder="Ex.: 5,75"/> : manualRuleMode ? <input className="form-control" type="text" inputMode="decimal" value={manualBenefitValue} onChange={event => { setManualBenefitValue(event.target.value); setPreview(null); }} disabled={!canWrite || !hasPlanningContext} required={Boolean(canWrite && hasPlanningContext)} placeholder="Ex.: 5,75"/> : <input className="form-control" type="text" value={rule ? moneyField(rule.unit_cents).replace(".", ",") : ""} readOnly disabled placeholder="Sem regra cadastrada"/>}
            <small>{benefit === "VT" ? "Informada manualmente neste planejamento; a tarifa varia conforme o trajeto." : manualRuleMode ? "Informe o valor do VR deste planejamento." : rule?.unit_mode === "PER_DAY" ? "Total do dia; não multiplica pela quantidade de refeições." : "Valor definido na regra vigente da empresa."}</small>
          </label>
          <label className="form-label">{benefit === "VT" ? "Utilizações por dia" : "Refeições por dia"}
            {benefit === "VT" ? <input className="form-control" type="number" min="1" max="20" step="1" value={vtUsesPerDay} onChange={event => { setVtUsesPerDay(event.target.value); setPreview(null); }} disabled={!canWrite || !hasPlanningContext} required={Boolean(canWrite && hasPlanningContext)} placeholder="Informe a quantidade"/> : manualRuleMode ? <input className="form-control" type="number" min="1" max="20" step="1" value={manualVrMode === "PER_DAY" ? "1" : manualBenefitUses} onChange={event => { setManualBenefitUses(event.target.value); setPreview(null); }} disabled={!canWrite || !hasPlanningContext || manualVrMode === "PER_DAY"} required={Boolean(canWrite && hasPlanningContext && manualVrMode !== "PER_DAY")} placeholder="Informe a quantidade"/> : <input className="form-control" type="number" value={rule?.unit_mode === "PER_DAY" ? "1" : rule?.uses_per_day ?? ""} readOnly disabled/>}
            <small>{benefit === "VT" ? "Quantidade de utilizações de transporte por dia de trabalho." : manualRuleMode ? manualVrMode === "PER_DAY" ? "Não se aplica; o valor informado já cobre o dia." : "Quantidade de refeições por dia." : rule?.unit_mode === "PER_DAY" ? "Não se aplica; a regra já informa o total diário." : "Quantidade definida na regra da empresa."}</small>
          </label>
          <label className="form-label">Escala de trabalho<select className="form-select" value={schedule} onChange={event => { const next = event.target.value; setSchedule(next); if (next === "WEEKDAYS_5X2") setScheduleStart(""); setPreview(null); setError(""); }} required={hasPlanningContext} disabled={!hasPlanningContext || !canWrite}><option value="">Selecione a escala</option>{schedules.map(([value]) => <option value={value} key={value}>{value === "WEEKDAYS_5X2" ? "5x2 — segunda a sexta" : value === "ROTATING_6X1" ? "6x1 — seis dias trabalhados e um de folga" : "6x2 — seis dias trabalhados e dois de folga"}</option>)}</select><small>Necessária para identificar dias trabalhados e folgas.</small></label>
          {rotatingSchedule && <label className="form-label">Início do ciclo da escala<input className="form-control" type="date" value={scheduleStart} onChange={event => { setScheduleStart(event.target.value); setPreview(null); }} required={hasPlanningContext} disabled={!hasPlanningContext || !canWrite}/><small>Informe uma data de referência para determinar as folgas da escala rotativa.</small></label>}
          <fieldset className="lp-bank-choice"><legend>Considerar Relação Bancária no pagamento do benefício?</legend><label className="form-check"><input className="form-check-input" type="radio" name="lp-respect-bank" checked={!respectBank} disabled={!canWrite} onChange={() => { setRespectBank(false); setPreview(null); }}/><span>Não</span></label><label className="form-check"><input className="form-check-input" type="radio" name="lp-respect-bank" checked={respectBank} disabled={!canWrite} onChange={() => { setRespectBank(true); setPreview(null); }}/><span>Sim</span></label><small>{respectBank ? `Só datas Sim da ${generalPlanMode ? "Relação Bancária geral" : "Relação Bancária aplicável à empresa"} podem ser candidatas; feriados aplicáveis prevalecem.` : "Desativada: aparece na comparação, mas não filtra as datas do cálculo."}</small></fieldset>
          {companyKey && !rule && <div className="lp-rule-missing lp-wide"><strong>Sem regra vigente de {benefit} para esta empresa.</strong><span>O dia de pagamento e as políticas são informados neste plano. Tarifa e utilizações do VT, ou valor e unidade do VR, também são preenchidos para o cálculo.</span></div>}
          <p className="lp-form-note lp-wide">A cobertura usa dias corridos; quantidade e valor consideram a escala. Pagamento do lote e corte de cobertura são datas diferentes.</p>
          <div className="lp-form-actions lp-wide"><button className="btn btn-primary" type="submit" disabled={!canWrite || busy || !hasPlanningContext || (!generalPlanMode && !companyContextReady) || (benefit === "VT" && (!Number.isInteger(parseMoney(vtTariff)) || parseMoney(vtTariff) <= 0 || !Number.isInteger(Number(vtUsesPerDay)) || Number(vtUsesPerDay) < 1 || Number(vtUsesPerDay) > 20))}>{busy ? <><span className="spinner-border spinner-border-sm me-2" aria-hidden="true"/>Calculando…</> : "Calcular melhor distribuição"}</button>{editing && <button className="btn btn-outline-secondary btn-sm" type="button" onClick={startNewPlan}>Cancelar edição</button>}</div>
          </form>
        </> : null}

        {!generalPlanMode && canConfigure && companyKey && <details className="lp-rule-details">
          <summary>{rule ? `Regra da empresa · editar ${benefit}` : `Cadastrar regra de ${benefit} para esta empresa`}</summary>
          <form className="lp-rule-form" onSubmit={saveRule}>
            <label className="form-label">Início da vigência<input className="form-control" type="date" value={ruleDraft.valid_from} onChange={event => setRuleDraft(value => ({ ...value, valid_from: event.target.value }))} required/><small>Versões anteriores permanecem no histórico.</small></label>
            <label className="form-label">Dia fixo de pagamento<select className="form-select" value={ruleDraft.payment_day} onChange={event => setRuleDraft(value => ({ ...value, payment_day: event.target.value }))} required><option value="">Selecione</option><option value="5">Dia 05</option><option value="30">Dia 30</option></select></label>
            <label className="form-label">Ajuste em fim de semana/feriado<select className="form-select" value={ruleDraft.payment_adjustment} onChange={event => setRuleDraft(value => ({ ...value, payment_adjustment: event.target.value }))} required><option value="">Selecione a regra da empresa</option><option value="PREVIOUS_WORKDAY">Antecipar para o dia útil anterior</option><option value="NEXT_WORKDAY">Postergar para o próximo dia útil</option><option value="NO_ADJUSTMENT">Data fixa, sem ajuste (confirmado)</option></select></label>
            {benefit === "VR" && <label className="form-label lp-wide">Unidade do valor do VR<select className="form-select" value={ruleDraft.unit_mode} onChange={event => setRuleDraft(value => ({ ...value, unit_mode: event.target.value, uses_per_day: event.target.value === "PER_DAY" ? "1" : "" }))} required><option value="">Confirme com a empresa</option><option value="PER_DAY">Valor total por dia</option><option value="PER_MEAL">Valor por refeição</option></select><small>Escolha a regra real da empresa. No modo diário, o sistema não multiplica o valor pelas refeições.</small></label>}
            {benefit === "VT" ? <p className="lp-rule-origin lp-wide">Tarifa e utilizações diárias do VT variam conforme o trajeto de cada colaborador e serão informadas no planejamento individual. Esta regra guarda somente pagamento, ajuste de data e feriados.</p> : <><label className="form-label">{ruleDraft.unit_mode === "PER_DAY" ? "Valor total por dia (R$)" : "Valor por refeição (R$)"}<input className="form-control" type="text" inputMode="decimal" autoComplete="off" value={ruleDraft.amount} onChange={event => setRuleDraft(value => ({ ...value, amount: event.target.value }))} required placeholder="Ex.: 5,75"/><small>Aceita valores em reais como 5,75 ou 1.234,56.</small></label>
            <label className="form-label">Refeições por dia<input className="form-control" type="number" min="1" max="20" step="1" value={ruleDraft.unit_mode === "PER_DAY" ? "1" : ruleDraft.uses_per_day} onChange={event => setRuleDraft(value => ({ ...value, uses_per_day: event.target.value }))} required disabled={ruleDraft.unit_mode === "PER_DAY"}/><small>{ruleDraft.unit_mode === "PER_DAY" ? "Não se aplica, pois o valor informado já cobre o dia." : "Informe a quantidade confirmada na regra da empresa."}</small></label></>}
            {ruleDraft.payment_day === "30" && <label className="form-label">Quando fevereiro não tem dia 30<select className="form-select" value={ruleDraft.february_30_policy} onChange={event => setRuleDraft(value => ({ ...value, february_30_policy: event.target.value }))}><option value="UNRESOLVED">Pedir definição se ocorrer</option><option value="LAST_DAY">Usar o último dia de fevereiro</option><option value="NEXT_MONTH">Usar o dia 30 do mês seguinte</option></select><small>Sem confirmação, o cálculo da competência ambígua será bloqueado.</small></label>}
            <label className="form-label">Admissão exatamente no dia do corte<select className="form-select" value={ruleDraft.admission_on_cutoff_policy} onChange={event => setRuleDraft(value => ({ ...value, admission_on_cutoff_policy: event.target.value }))}><option value="UNRESOLVED">Pedir definição se ocorrer</option><option value="INCLUDE_CURRENT">Incluir no ciclo atual</option><option value="NEXT_CYCLE">Iniciar no ciclo seguinte</option></select><small>Regra por empresa; sem confirmação, essa data é bloqueada.</small></label>
            {benefit === "VR" && parseMoney(ruleDraft.amount) === 0 && <label className="form-label lp-rule-reason">Justificativa para valor zero<input className="form-control" maxLength="500" value={ruleDraft.zero_value_reason} onChange={event => setRuleDraft(value => ({ ...value, zero_value_reason: event.target.value }))} required/></label>}
            <label className="form-label lp-rule-reason">Motivo da configuração/alteração<input className="form-control" maxLength="500" value={ruleDraft.change_reason} onChange={event => setRuleDraft(value => ({ ...value, change_reason: event.target.value }))} required placeholder="Ex.: confirmado com o financeiro em 14/10"/><small>Autor, data, vigência e revisões ficam auditados.</small></label>
            <p className="lp-rule-origin">A regra salva é específica desta empresa e benefício. O seletor Empresa geral cria um planejamento avulso; não é uma configuração de fallback.</p>
            <button type="submit" className="btn btn-outline-primary btn-sm" disabled={busy}>{busy ? "Salvando…" : "Salvar regra da empresa"}</button>
          </form>
        </details>}
      </section>

        {preview && result && <>
          <section className="card lp-card lp-result-card">
            <div className="lp-section-heading"><div><span className="lp-step-label">PRÉVIA · AINDA NÃO SALVA</span><h2>4. Resultado e memória do cálculo</h2><p>Reveja a proposta; a previsão não executa crédito nem pagamento bancário.</p></div><span className="badge text-bg-primary">{result.lots.length} lote(s)</span></div>
            <div className="lp-result-summary"><div><span>{result.payment_source === "GENERAL" ? "Escopo" : employee ? "Colaborador" : "Identificação"}</span><strong>{result.payment_source === "GENERAL" ? "Empresa geral" : employee?.name || subjectName.trim() || selectedDetail?.employeeName || "Sem identificação"}</strong><small>{result.payment_source === "GENERAL" ? "Sem vínculo individual" : employee?.companyName || company?.name || selectedDetail?.companyName || "Empresa"}</small></div><div><span>Data de admissão</span><strong>{isoDate(admission)}</strong></div><div><span>Corte inclusivo da cobertura</span><strong>{isoDate(result.cutoff_date)}</strong><small>Fim do intervalo coberto</small></div><div><span>Pagamento nominal</span><strong>{isoDate(result.nominal_payroll_date)}</strong><small>{result.payment_source === "GENERAL" ? `Empresa geral · dia ${String(result.payment_day_used).padStart(2, "0")}` : result.payment_source === "MANUAL" ? `Escolha manual · dia ${String(result.payment_day_used).padStart(2, "0")}` : `Regra da empresa · dia ${String(result.payment_day_used).padStart(2, "0")}`}</small></div><div><span>Data operacional prevista</span><strong>{isoDate(result.effective_payroll_date)}</strong><small>Após ajuste de expediente</small></div><div><span>Dias de benefício</span><strong>{eligibility || result.eligible_dates?.length || 0}</strong><small>{benefit} · {benefit === "VT" ? "tarifa informada neste planejamento" : "parâmetros informados neste planejamento/regra"}</small></div><div><span>Total previsto</span><strong>{money(result.total_cents)}</strong></div></div>
            {result.pending_items?.length > 0 && <div className="alert alert-warning lp-alert" role="alert"><strong>Prévia parcial; não pode ser salva como definitiva.</strong><ul className="mb-0">{result.pending_items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
            <div className="table-responsive lp-table-wrap"><table className="table table-sm align-middle"><thead><tr><th>Lote</th><th>Data sugerida de processamento</th><th>Período coberto (inclusivo)</th><th>Dias elegíveis</th><th>{benefit === "VT" ? "Tarifa por uso" : result.lots[0]?.unit_mode === "PER_DAY" ? "Valor por dia" : "Valor por refeição"}</th><th>{benefit === "VT" ? "Usos/dia · total" : result.lots[0]?.unit_mode === "PER_DAY" ? "Base diária" : "Refeições/dia · total"}</th><th>Valor previsto</th></tr></thead><tbody>{result.lots.map(lot => <tr key={lot.number}><th scope="row">{lot.number}</th><td>{isoDate(lot.pay_date)}</td><td>{isoDate(lot.from)} a {isoDate(lot.to)}</td><td>{lot.eligible_days}</td><td>{money(lot.unit_cents)}</td><td>{lot.uses_per_day} × {lot.quantity}</td><td>{money(lot.amount_cents)}</td></tr>)}</tbody><tfoot><tr><th colSpan="6">Total previsto</th><th>{money(result.total_cents)}</th></tr></tfoot></table></div>
            <CalendarPreview result={result}/>
            <details className="lp-memory" open>
              <summary>Memória completa e decisões usadas</summary>
              <ul>{result.memory.map((line, index) => <li key={index}>{line}</li>)}</ul>
              {result.rejected_processing_dates?.length > 0 && (
                <details>
                  <summary>Datas de processamento descartadas ({result.rejected_processing_dates.length})</summary>
                  <ul>{result.rejected_processing_dates.map(item => <li key={item.day}>{isoDate(item.day)} · {item.reason}</li>)}</ul>
                </details>
              )}
            </details>
            {selectedDetail?.configurationChanged && <div className="alert alert-warning lp-alert">A configuração da empresa mudou desde este snapshot. Os valores salvos continuam sendo os usados no cálculo original.</div>}
            {selectedDetail?.revisions?.length > 0 && <details className="lp-revision-history"><summary>Histórico de versões ({selectedDetail.revisions.length} anterior(es))</summary><div className="lp-revision-list">{selectedDetail.revisions.map(revision => <PlanRevision key={revision.rowVersion} revision={revision}/>)}</div></details>}
            {canWrite && (!selectedDetail || editing) && <div className="lp-save-row"><label className="form-label">Nome deste planejamento<input className="form-control" maxLength="120" value={title} onChange={event => setTitle(event.target.value)} placeholder={`${employee?.name || selectedDetail?.employeeName || "Colaborador"} · ${benefit} · ${result.cutoff_date}`}/></label><div><button className="btn btn-success" type="button" onClick={savePlan} disabled={busy || !preview?.calculationToken || preview.formSignature !== currentInputSignature || result.can_save === false}>{busy ? "Salvando…" : editing ? "Salvar alterações" : "Salvar planejamento"}</button>{editing && <button className="btn btn-outline-secondary btn-sm ms-2" type="button" onClick={() => openPlan(selectedDetail.id)} disabled={busy}>Cancelar edição</button>}</div><small className="text-secondary">O planejamento salvo guarda esta versão do cálculo. Se fixado, não expira; ao desafixar, começa novo prazo de 4 dias corridos no horário de São Paulo.</small>{(!preview?.calculationToken || preview.formSignature !== currentInputSignature) && <small className="lp-save-blocked">Recalcule com os dados atuais antes de salvar.</small>}{result.can_save === false && <small className="lp-save-blocked">Confirme município e UF da empresa antes de salvar uma previsão definitiva.</small>}</div>}
          </section>
        </>}
      <details className="lp-data-note"><summary>Fonte cadastral e limitações</summary><p>Dados do snapshot V2: atualização mais recente em {stamp(data?.snapshotAt)}. Esta instalação não possui API de RH conectada, então os dados exibidos não são em tempo real.</p>{data?.limitations?.map((item, index) => <p key={index}>{item}</p>)}</details>
      <section className="lp-saved-section">
        <div className="lp-section-heading"><div><span className="eyebrow">Persistidos no sistema</span><h2>Planejamentos salvos</h2><p>{data?.planStats?.current ?? 0} ativos · {data?.planStats?.pinned ?? 0} fixados · {data?.planStats?.expiringSoon ?? 0} expiram em 24 horas · {data?.planStats?.expired ?? 0} expirados</p></div><span>{data?.plans?.length || 0} registro(s) nesta seleção</span></div>
        <details className="lp-list-filter-details"><summary>Filtrar planejamentos</summary><div className="lp-list-filters" role="search" aria-label="Filtrar planejamentos salvos">
          <label>Pesquisar colaborador, matrícula, empresa ou competência<input className="form-control form-control-sm" type="search" value={planQuery} onChange={event => { setPlanQuery(event.target.value); setPlanPage(0); }} placeholder="Buscar planejamentos"/></label>
          <label>Benefício<select className="form-select form-select-sm" value={planBenefit} onChange={event => { setPlanBenefit(event.target.value); setPlanPage(0); }}><option value="">VT e VR</option><option value="VT">VT</option><option value="VR">VR</option></select></label>
          <label>Status<select className="form-select form-select-sm" value={planStatus} onChange={event => { setPlanStatus(event.target.value); setPlanPage(0); }}><option value="current">Ativos e fixados</option><option value="pinned">Fixados</option><option value="expired">Expirados</option><option value="all">Todos não excluídos</option></select></label>
          <label>Autor<select className="form-select form-select-sm" value={planAuthor} onChange={event => { setPlanAuthor(event.target.value); setPlanPage(0); }}><option value="">Todos</option>{data?.planAuthors?.map(author => <option value={author.id} key={author.id}>{author.name}</option>)}</select></label>
          <label>Ordenar por<select className="form-select form-select-sm" value={planSort} onChange={event => { setPlanSort(event.target.value); setPlanPage(0); }}><option value="recent">Mais recentes</option><option value="employee">Nome do colaborador</option><option value="cutoff">Corte mais próximo</option><option value="expiry">Expiração mais próxima</option></select></label>
          <button type="button" className="btn btn-outline-secondary btn-sm lp-clear-filters" onClick={() => { setPlanQuery(""); setPlanBenefit(""); setPlanStatus("current"); setPlanAuthor(""); setPlanSort("recent"); setPlanPage(0); }}>Limpar filtros</button>
        </div></details>
        {visiblePlans.length ? <div className="lp-saved-list">{visiblePlans.map(plan => <article className={`card lp-saved-card${plan.pinned ? " is-pinned" : ""}`} key={plan.id}>
          <button className="lp-saved-open" type="button" onClick={() => openPlan(plan.id)} disabled={busy} aria-label={`Abrir planejamento de ${plan.employeeName}`}><span className="lp-saved-kicker">{plan.pinned ? "FIXADO · NÃO EXPIRA" : `${plan.benefitType} · ${plan.lotCount} LOTE(S)`}</span><strong>{plan.employeeName} · {plan.companyName}</strong><span>{plan.benefitType} · admissão {isoDate(plan.admissionDate)} · corte {isoDate(plan.cutoffDate)} · pagamento {isoDate(plan.paymentDate)}</span><span>{plan.lots.map(lot => `L${lot.number}: ${isoDate(lot.from)}–${isoDate(lot.to)} · ${lot.eligible_days} dia(s) · ${money(lot.amount_cents)}`).join(" | ")}</span><small>Total: {money(plan.totalCents)} · salvo por {plan.savedBy || "usuário local"} em {stamp(plan.savedAt)}</small></button>
          <div className="lp-saved-actions"><button type="button" className="btn btn-outline-primary btn-sm" onClick={() => openPlan(plan.id)} disabled={busy}>Abrir detalhes</button>{canWrite && <><button type="button" className="btn btn-outline-secondary btn-sm" title={plan.pinned ? "Remover fixação" : "Fixar planejamento"} onClick={() => togglePin(plan)} disabled={busy}>{plan.pinned ? "📌 Desafixar" : "📌 Fixar"}</button><button type="button" className="btn btn-outline-danger btn-sm" onClick={() => deletePlan(plan)} disabled={busy}>Excluir</button></>}</div>
          <div className="lp-expiry">{expiryLabel(plan)}</div>
        </article>)}</div> : <div className="card lp-empty-card">{loading ? "Carregando planejamentos…" : "Nenhum planejamento corresponde aos filtros selecionados."}</div>}
        {sortedPlans.length > pageSize && <nav className="lp-pagination" aria-label="Páginas de planejamentos"><button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setPlanPage(value => Math.max(0, value - 1))} disabled={planPage === 0}>Anterior</button><span>Página {planPage + 1} de {totalPlanPages}</span><button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setPlanPage(value => Math.min(totalPlanPages - 1, value + 1))} disabled={planPage + 1 >= totalPlanPages}>Próxima</button></nav>}
      </section>
    </main>
  </>;
}
