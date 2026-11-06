"""Calculadoras e tabelas tributárias congeladas na referência V2 2.0.158."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, getcontext

getcontext().prec = 28
ZERO = Decimal("0")


def _yes(value):
    return value is True or str(value).strip().casefold() in ("1", "true", "yes", "sim", "on")


def number(value, field="Valor"):
    raw = str(value if value is not None else "").strip().replace("R$", "").replace("%", "").replace(" ", "")
    if not raw:
        raise ValueError(f"Informe {field.lower()}.")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        result = Decimal(raw)
    except Exception as error:
        raise ValueError(f"{field} deve ser um número válido.") from error
    if not result.is_finite():
        raise ValueError(f"{field} deve ser um número válido.")
    return result


def nonnegative(value, field):
    parsed = number(value, field)
    if parsed < 0:
        raise ValueError(f"{field} não pode ser negativo.")
    return parsed


def positive(value, field):
    parsed = nonnegative(value, field)
    if parsed == 0:
        raise ValueError(f"{field} deve ser maior que zero.")
    return parsed


def fmt(value, places=8):
    rendered = format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def money(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def result(value, memory, details=None):
    return {"value": money(value), "exact": format(value, ".12f"), "memory": memory, "details": details or {}}


def _civil_date(value, label):
    try:
        parsed = date.fromisoformat(str(value or ""))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Informe {label.lower()} com uma data válida.") from error
    if parsed.isoformat() != str(value):
        raise ValueError(f"Informe {label.lower()} com uma data válida.")
    return parsed


def _advance_salary(data):
    """Manual advance simulation; company profiles currently supply only an advance day."""
    competence = str(data.get("competence") or "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", competence):
        raise ValueError("Informe a competência no formato mês e ano.")
    year, month = map(int, competence.split("-"))
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    admission = _civil_date(data.get("admissionDate"), "data de admissão")
    if admission > month_end:
        raise ValueError("A data de admissão está fora da competência selecionada. Confira o período.")
    payment = _civil_date(data.get("paymentDate"), "data prevista de pagamento")
    if payment.year != year or payment.month != month:
        raise ValueError("A data prevista de pagamento deve estar dentro da competência selecionada.")
    method = str(data.get("method") or "")
    methods = {"FULL", "PROJECTED", "ACCUMULATED", "HOURS"}
    if method not in methods:
        raise ValueError("Selecione explicitamente o método de cálculo.")
    verified = data.get("_verifiedCompanyPolicy") or {}
    has_registered_advance = verified.get("advanceStatus") == "HAS_DAY"
    company_has_no_advance = verified.get("advanceStatus") == "NONE"
    # The imported company profile has an advance date, but it has no verified rate,
    # eligible base or proportionality method. The actual calculation remains manual.
    if not has_registered_advance and not _yes(data.get("manualSimulationConfirmed")):
        if company_has_no_advance:
            raise ValueError("Esta empresa não possui adiantamento salarial cadastrado. Confirme para abrir uma simulação manual fora da regra cadastrada.")
        raise ValueError("A regra da empresa não informa o método e o percentual do adiantamento. Confirme que esta é uma simulação manual.")
    if has_registered_advance and not _yes(data.get("manualSimulationConfirmed")):
        raise ValueError("O cadastro informa a data do adiantamento, mas não informa percentual nem base. Confirme a simulação manual para continuar.")

    percentage = nonnegative(data.get("percentage"), "Percentual do adiantamento")
    if percentage > 100:
        raise ValueError("O percentual do adiantamento não pode ultrapassar 100%.")
    mode = str(data.get("entryMode") or "")
    if mode == "MONTHLY":
        reference_salary = positive(data.get("monthlySalary"), "Salário mensal")
        hourly_rate = None
        monthly_hours = None
    elif mode == "HOURLY_REFERENCE":
        hourly_rate = positive(data.get("hourlyRate"), "Salário-hora")
        monthly_hours = positive(data.get("monthlyHours"), "Horas mensais de referência")
        reference_salary = hourly_rate * monthly_hours
    elif mode == "HOURLY_ACTUAL":
        hourly_rate = positive(data.get("hourlyRate"), "Salário-hora")
        monthly_hours = None
        reference_salary = None
    else:
        raise ValueError("Selecione a modalidade de remuneração.")

    if mode == "HOURLY_ACTUAL" and method != "HOURS":
        raise ValueError("Para horas apuradas ou projetadas, selecione o método por horas.")
    if method == "HOURS" and mode != "HOURLY_ACTUAL":
        raise ValueError("O método por horas exige a modalidade de horas apuradas ou projetadas.")

    divisor = positive(data.get("dayDivisor"), "Divisor de dias") if method in ("PROJECTED", "ACCUMULATED") else None
    cutoff = _civil_date(data.get("cutoffDate"), "data de corte")
    if cutoff.year != year or cutoff.month != month:
        raise ValueError("A data de corte deve estar dentro da competência.")
    if cutoff < admission and method == "ACCUMULATED":
        raise ValueError("A data de corte é anterior à admissão. Confira a regra da empresa.")
    if method == "ACCUMULATED":
        pass
    elif method == "HOURS":
        period_start = _civil_date(data.get("hoursPeriodStart"), "início do período de horas")
        period_end = _civil_date(data.get("hoursPeriodEnd"), "fim do período de horas")
        if period_start < admission or period_start > period_end or period_end > month_end:
            raise ValueError("Confira o período das horas em relação à admissão e à competência.")
        eligible_hours = nonnegative(data.get("eligibleHours"), "Horas elegíveis")
    else:
        period_start = max(month_start, admission)
        period_end = month_end
    if method == "FULL" and admission >= month_start and admission <= month_end and not _yes(data.get("fullPeriodApproved")):
        raise ValueError("Confirme que a regra selecionada permite o adiantamento integral para esta admissão na competência.")

    if method == "FULL":
        salary_base = reference_salary
        applied_days = None
        formula_label = "Salário de referência integral × percentual selecionado"
        period_start, period_end = max(month_start, admission), month_end
    elif method in ("PROJECTED", "ACCUMULATED"):
        period_start = max(month_start, admission)
        period_end = cutoff if method == "ACCUMULATED" else month_end
        if period_end < period_start:
            raise ValueError("A admissão ocorreu depois do período elegível; esta fórmula não gera base acumulada.")
        civil_days = (period_end - period_start).days + 1
        complete_month = period_start == month_start and period_end == month_end
        # A selected 30-day divisor keeps a complete month at one monthly salary in
        # both 28/29- and 31-day months. A partial month retains its inclusive count.
        applied_days = int(divisor) if complete_month else min(civil_days, int(divisor))
        salary_base = reference_salary * Decimal(applied_days) / divisor
        formula_label = "Remuneração mensal de referência × dias inclusivos ÷ divisor informado"
    else:
        period_start = _civil_date(data.get("hoursPeriodStart"), "início do período de horas")
        period_end = _civil_date(data.get("hoursPeriodEnd"), "fim do período de horas")
        salary_base = hourly_rate * eligible_hours
        applied_days = None
        formula_label = "Salário-hora informado × horas elegíveis informadas"

    component_labels = (
        ("insalubrity", "Insalubridade"), ("hazardPay", "Periculosidade"),
        ("nightPay", "Adicional noturno"), ("overtime", "Horas extras"),
        ("variableDsr", "DSR de variáveis"), ("commissions", "Comissões"),
        ("bonus", "Prêmios ou bonificações"), ("otherComponents", "Outras parcelas"),
    )
    included, excluded = [], []
    component_total = ZERO
    for key, label in component_labels:
        amount_raw = str(data.get(f"{key}Amount") or "").strip()
        choice = str(data.get(f"{key}Treatment") or "UNSPECIFIED")
        if not amount_raw:
            if choice == "INCLUDED":
                raise ValueError(f"Informe o valor da parcela {label.lower()} marcada para inclusão.")
            continue
        amount = nonnegative(amount_raw, f"Valor de {label.lower()}")
        if amount == 0:
            continue
        if choice == "UNSPECIFIED":
            if key == "insalubrity":
                raise ValueError("A insalubridade foi informada, mas a política de adiantamento não definiu sua inclusão na base.")
            raise ValueError(f"Indique se {label.lower()} entra ou não na base desta simulação.")
        if choice not in ("INCLUDED", "EXCLUDED"):
            raise ValueError(f"Tratamento de {label.lower()} inválido.")
        if choice == "INCLUDED":
            component_total += amount
            included.append({"name": label, "amount": money(amount), "source": "Valor informado para esta simulação"})
        else:
            excluded.append({"name": label, "amount": money(amount), "reason": "Marcada fora da base desta simulação"})

    calculation_base = salary_base + component_total
    percentage_amount = calculation_base * percentage / Decimal(100)
    previous = nonnegative(data.get("previousAdvance") or 0, "Adiantamento anterior")
    due = max(ZERO, percentage_amount - previous)
    system_value_raw = str(data.get("systemValue") or "").strip()
    comparison = None
    if system_value_raw:
        external = nonnegative(system_value_raw, "Valor informado no sistema")
        difference = due - external
        comparison = {"systemName": str(data.get("systemName") or "Sistema externo").strip()[:80] or "Sistema externo",
                      "systemValue": money(external), "scenarioValue": money(due), "difference": money(difference),
                      "absoluteDifference": money(abs(difference)), "differencePercent": money(abs(difference) / external * 100) if external else None,
                      "status": "CONCILIATED" if difference == 0 else "DIVERGENT",
                      "observation": "Os dados informados não permitem identificar a causa da diferença." if difference else "Os valores informados coincidem nesta simulação."}

    policy_day = verified.get("advanceDay") or ""
    if has_registered_advance and str(policy_day).isdigit() and payment.day != int(policy_day):
        if not _yes(data.get("paymentDateOverrideConfirmed")) or not str(data.get("paymentDateOverrideReason") or "").strip():
            raise ValueError(f"O cadastro informa pagamento do adiantamento no dia {policy_day}. Para usar outra data nesta simulação, confirme a alteração e informe o motivo.")
    company_name = verified.get("companyName") or "Sem empresa vinculada"
    company_source = verified.get("source") or "Sem cadastro consultado"
    warnings = ["Simulação manual; o cadastro consultado não define percentual, método nem composição da base.",
                "Não foram aplicados INSS ou IRRF. O adiantamento simulado não é processamento nem pagamento em folha.",
                "Não há teto empresarial de adiantamento confirmado nos dados consultados."]
    if company_has_no_advance:
        warnings.insert(0, "Esta empresa está cadastrada como sem adiantamento; o cálculo foi liberado como simulação manual fora da regra cadastrada.")
    elif has_registered_advance:
        warnings.insert(0, f"O cadastro informa o dia {policy_day} para o adiantamento, mas não informa percentual ou método. Data da simulação: {payment.isoformat()}.")
    if admission > cutoff:
        warnings.append("A admissão ocorreu após a data de corte. A projeção escolhida usa o restante da competência e continua sendo uma hipótese manual.")
    if data.get("entryMode") == "HOURLY_ACTUAL" and str(data.get("hoursAreProjected") or "") == "1":
        warnings.append("As horas são projetadas para período futuro; o resultado é uma estimativa.")
    if previous > 0:
        warnings.append(f"Adiantamento anterior de R$ {money(previous)} abatido uma única vez do valor desta parcela.")

    start_label, end_label = period_start.strftime("%d/%m/%Y"), period_end.strftime("%d/%m/%Y")
    memory = [f"Competência: {month_start.strftime('%m/%Y')}.",
              f"Empresa: {company_name}; origem consultada: {company_source}.",
              f"Data prevista de pagamento: {payment.strftime('%d/%m/%Y')}; data de corte: {cutoff.strftime('%d/%m/%Y')} (limite inclusivo; separado do pagamento).",
              f"Método explicitamente escolhido: {formula_label}.",
              f"Período da base: {start_label} a {end_label} (datas civis, inclusão do primeiro e último dia)."]
    if reference_salary is not None:
        if mode == "HOURLY_REFERENCE":
            memory.append(f"Salário-hora R$ {fmt(hourly_rate)} × {fmt(monthly_hours)} horas mensais de referência = R$ {fmt(reference_salary)}; ambos informados manualmente.")
        else:
            memory.append(f"Salário mensal de referência: R$ {fmt(reference_salary)}; origem: informado manualmente.")
    else:
        memory.append(f"Salário-hora R$ {fmt(hourly_rate)} × {fmt(eligible_hours)} horas elegíveis = R$ {fmt(salary_base)}; horas informadas manualmente.")
    if applied_days is not None:
        civil_days = (period_end - period_start).days + 1
        memory.append(f"Dias civis inclusivos no período: {civil_days}; dias aplicados ao divisor: {applied_days}; divisor informado: {fmt(divisor)}.")
        if period_start == month_start and period_end == month_end:
            memory.append("Competência integral: base limitada a um salário de referência; não há proporção de 31/30 nem redução de 28/30 ou 29/30.")
        memory.append(f"Base salarial da simulação: R$ {fmt(reference_salary)} × {applied_days} ÷ {fmt(divisor)} = R$ {fmt(salary_base)}.")
    included_text = ", ".join("{} R$ {}".format(part["name"], part["amount"]) for part in included) or "nenhuma"
    excluded_text = ", ".join("{} R$ {}".format(part["name"], part["amount"]) for part in excluded) or "nenhuma informada"
    memory.extend([f"Parcelas adicionais incluídas manualmente: {included_text}.",
                   f"Parcelas fora da base: {excluded_text}.",
                   f"Base do percentual: R$ {fmt(calculation_base)}.",
                   f"Percentual informado: {fmt(percentage)}%; valor bruto calculado: R$ {fmt(calculation_base)} × {fmt(percentage)} ÷ 100 = R$ {fmt(percentage_amount)}.",
                   f"Adiantamento anterior compensado: R$ {fmt(previous)}; resultado antes do arredondamento: R$ {fmt(due)}.",
                   f"Arredondamento: somente no resultado final para centavos (ROUND_HALF_UP). Valor estimado: R$ {money(due)}."])
    if comparison:
        memory.append(f"Comparação com {comparison['systemName']}: R$ {comparison['systemValue']}; diferença desta simulação: R$ {comparison['difference']}. A divergência ainda não identifica a causa.")
    return {"value": money(due), "exact": format(due, ".12f"), "memory": memory,
            "details": {"type": "advance", "status": "MANUAL_ESTIMATE", "label": "Adiantamento estimado · Simulação manual",
                        "competence": competence, "companyName": company_name, "companySource": company_source,
                        "registeredAdvanceDay": policy_day, "paymentDate": payment.isoformat(), "cutoffDate": cutoff.isoformat(),
                        "periodStart": period_start.isoformat(), "periodEnd": period_end.isoformat(), "daysApplied": applied_days,
                        "dayDivisor": fmt(divisor) if divisor is not None else "", "method": method, "formula": formula_label,
                        "entryMode": mode, "referenceSalary": money(reference_salary) if reference_salary is not None else None,
                        "salaryBase": money(salary_base), "includedComponents": included, "excludedComponents": excluded,
                        "base": money(calculation_base), "percentage": fmt(percentage), "grossAdvance": money(percentage_amount),
                        "previousAdvance": money(previous), "comparison": comparison, "warnings": warnings,
                        "systemDetails": {key: str(data.get(key) or "") for key in ("systemHourlyRate", "systemMonthlyHours", "systemPeriod", "systemRubrics", "systemNotes")}}}


def _salary_hour(data):
    salary = nonnegative(data.get("salary"), "Salário base")
    divisor = positive(data.get("divisor"), "Divisor mensal")
    manual = str(data.get("manualSalaryHour", "")).strip()
    return number(manual, "Salário-hora manual") if manual else salary / divisor


def _dates(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as error:
        raise ValueError("Informe as datas inicial e final do período.") from error
    if last < first:
        raise ValueError("O início do período deve ser anterior ao fim.")
    days = []
    current = first
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def calculate(calc_id: str, data: dict):
    if calc_id == "advance":
        return _advance_salary(data)
    if calc_id in ("overtime", "direct", "night", "nighthe"):
        hour = _salary_hour(data) if calc_id != "direct" else nonnegative(data.get("salaryHour"), "Valor da hora")
        if calc_id == "overtime":
            hours, pct = nonnegative(data.get("hours"), "Horas"), nonnegative(data.get("percentage"), "Percentual")
            total = hour * hours * (1 + pct / 100)
            return result(total, [f"Salário-hora: {fmt(hour)}", f"Multiplicador de HE: 1 + {fmt(pct)} ÷ 100", f"Cálculo: {fmt(hour)} × {fmt(hours)} × (1 + {fmt(pct)} ÷ 100) = {fmt(total)}"])
        if calc_id == "direct":
            hours, pct = nonnegative(data.get("hours"), "Horas"), nonnegative(data.get("percentage"), "Percentual")
            total = hour * hours * (1 + pct / 100)
            return result(total, [f"Cálculo: {fmt(hour)} × {fmt(hours)} × (1 + {fmt(pct)} ÷ 100) = {fmt(total)}"])
        if calc_id == "night":
            hours, pct = nonnegative(data.get("hours"), "Horas noturnas"), nonnegative(data.get("percentage"), "Percentual noturno")
            total = hour * hours * pct / 100
            return result(total, [f"Adicional noturno: {fmt(hour)} × {fmt(hours)} × {fmt(pct)}% = {fmt(total)}"])
        if str(data.get("applyReduction") or "").casefold() not in ("0", "1", "sim", "não", "nao", "yes", "no", "true", "false"):
            raise ValueError("Selecione se a redução ficta será aplicada.")
        if data.get("nightPremiumBasis") not in ("NORMAL", "OVERTIME_HOUR"):
            raise ValueError("Selecione a base do adicional noturno.")
        hours, pct, night_pct = nonnegative(data.get("clockHours"), "Horas noturnas"), nonnegative(data.get("overtimePercentage"), "Percentual de HE"), nonnegative(data.get("nightPercentage"), "Percentual noturno")
        reduced_minutes = positive(data.get("reducedMinutes"), "Duração da hora noturna")
        considered = hours * 60 / reduced_minutes if _yes(data.get("applyReduction")) else hours
        normal = hour * considered
        overtime = normal * pct / 100
        basis = normal + overtime if data.get("nightPremiumBasis") == "OVERTIME_HOUR" else normal
        night = basis * night_pct / 100
        total = normal + overtime + night
        return result(total, [f"Horas consideradas: {fmt(considered)}", f"Hora normal: {fmt(normal)}", f"Adicional de HE: {fmt(overtime)}", f"Adicional noturno: {fmt(night)}", f"Total: {fmt(total)}"])
    if calc_id in ("reduced", "interval"):
        if str(data.get("applyReduction") or "").casefold() not in ("0", "1", "sim", "não", "nao", "yes", "no", "true", "false"):
            raise ValueError("Selecione se a redução ficta será aplicada.")
        if calc_id == "interval":
            def clock(text):
                if not re.fullmatch(r"\d{1,2}:\d{2}", str(text)):
                    raise ValueError("Entrada e saída devem usar HH:MM.")
                h, m = map(int, text.split(":"))
                if h > 23 or m > 59:
                    raise ValueError("Horário inválido.")
                return h * 60 + m
            start, end = clock(data.get("start")), clock(data.get("end"))
            minutes = end - start if end >= start else 1440 - start + end
            minutes = Decimal(minutes) - nonnegative(data.get("breakMinutes", 0), "Intervalo") + nonnegative(data.get("extensionMinutes", 0), "Prorrogação")
            if minutes < 0:
                raise ValueError("O intervalo não pode superar o período informado.")
            hours = minutes / 60
        else:
            hours = nonnegative(data.get("clockHours"), "Horas de relógio")
        reduced_minutes = positive(data.get("reducedMinutes"), "Duração da hora noturna")
        if reduced_minutes > 60:
            raise ValueError("A duração da hora noturna não pode ultrapassar 60 minutos.")
        equivalent = hours * 60 / reduced_minutes if _yes(data.get("applyReduction")) else hours
        return {"value": fmt(equivalent), "equivalentHours": fmt(equivalent), "memory": [f"Horas de relógio: {fmt(hours)}", f"Horas equivalentes: {fmt(equivalent)}"]}
    if calc_id in ("dsr", "dsr-discount"):
        days = _dates(data.get("startDate", ""), data.get("endDate", ""))
        holidays = set(filter(None, re.split(r"[;,\n]+", str(data.get("holidays", "")))))
        if calc_id == "dsr":
            total = nonnegative(data.get("variableTotal"), "Total da verba")
            work = sum(d.weekday() != 6 and d.isoformat() not in holidays for d in days)
            rest = sum(d.weekday() == 6 or d.isoformat() in holidays for d in days)
            if not work:
                raise ValueError("O período não tem dias de trabalho para calcular o DSR.")
            due = total / work * rest
            return result(due, [f"Dias trabalhados: {work}", f"Repousos e feriados: {rest}", f"Estimativa DSR: {fmt(total)} ÷ {work} × {rest} = {fmt(due)}", "Estimativa: confira escala, feriados e norma coletiva aplicável."])
        salary = nonnegative(data.get("salary"), "Salário")
        divisor = positive(data.get("divisorDays"), "Divisor em dias")
        valid = {d.isoformat(): d for d in days}
        absences = sorted(set(filter(None, re.split(r"[;,\n]+", str(data.get("absenceDates", ""))))))
        weeks = set()
        for absence in absences:
            if absence not in valid:
                raise ValueError(f"A falta {absence} está fora do período informado.")
            day = valid[absence]
            if day.weekday() == 6 or absence in holidays:
                raise ValueError(f"A data {absence} é repouso ou feriado, não dia de falta para esta estimativa.")
            weeks.add((day + timedelta(days=(6 - day.weekday()) % 7)).isoformat())
        daily = salary / divisor
        due = daily * len(weeks)
        return result(due, [f"Faltas não justificadas: {', '.join(absences) if absences else 'nenhuma'}", f"Repousos semanais afetados: {len(weeks)}", f"Desconto DSR estimado: {fmt(daily)} × {len(weeks)} = {fmt(due)}", "Estimativa: confira escala, justificativas e norma coletiva aplicável."])
    if calc_id == "averages":
        values = [nonnegative(v, "Valor mensal") for v in re.split(r"[;\n]+", str(data.get("values", ""))) if v.strip()]
        months_value = number(data.get("totalMonths"), "Meses no período")
        if months_value != months_value.to_integral_value():
            raise ValueError("A quantidade de meses deve ser um número inteiro.")
        months = int(months_value)
        if months < 1:
            raise ValueError("A quantidade total de meses deve ser maior que zero.")
        informed = sum(v != 0 for v in values)
        divide_by = data.get("divideBy")
        if divide_by not in ("ALL_MONTHS", "INFORMED_MONTHS"):
            raise ValueError("Selecione o critério de divisão da média.")
        divisor = months if divide_by == "ALL_MONTHS" else informed
        if not divisor:
            raise ValueError("Informe pelo menos um mês com valor.")
        total = sum(values, ZERO)
        return result(total / divisor, [f"Soma: {fmt(total)}", f"Divisor: {divisor}", f"Média: {fmt(total)} ÷ {divisor}"])
    if calc_id in ("unhealthy", "danger"):
        base = nonnegative(data.get("base", data.get("salary", 0)), "Base de cálculo")
        pct = nonnegative(data.get("percentage"), "Percentual informado")
        condition = str(data.get("conditionStatus") or "")
        if condition not in ("CONFIRMED", "HYPOTHETICAL"):
            raise ValueError("Informe se a condição está reconhecida ou se este cálculo é apenas hipotético.")
        validity = str(data.get("validity") or "").strip()
        if not validity:
            raise ValueError("Informe o período ou a vigência considerada para a base.")
        source = str(data.get("source") or "").strip()
        value = base * pct / 100
        status_label = "condição informada como reconhecida" if condition == "CONFIRMED" else "hipótese de simulação; condição não confirmada"
        memory = [f"Base informada: R$ {fmt(base)}; vigência/período informado: {validity}.",
                  f"Percentual digitado: {fmt(pct)}%; cálculo matemático: R$ {fmt(base)} × {fmt(pct)} ÷ 100 = R$ {fmt(value)}.",
                  f"Condição: {status_label}; fonte informada: {source or 'não informada'}.",
                  "O cálculo não reconhece por si só o direito ao adicional nem valida a base jurídica ou a vigência."]
        return result(value, memory, {"conditionStatus": condition, "validity": validity, "source": source})
    if calc_id in ("absence", "delay", "proportional"):
        salary = nonnegative(data.get("salary"), "Salário")
        if calc_id == "absence":
            divisor = positive(data.get("divisorDays"), "Divisor em dias")
            units = nonnegative(data.get("days"), "Dias de falta")
            value = salary / divisor * units
        elif calc_id == "proportional":
            divisor = positive(data.get("divisorDays"), "Divisor em dias")
            units = nonnegative(data.get("entitledDays"), "Dias de direito")
            value = salary / divisor * units
        else:
            divisor = positive(data.get("divisor"), "Divisor mensal")
            hour = salary / divisor
            value = hour * nonnegative(data.get("hours"), "Horas") + hour / 60 * nonnegative(data.get("minutes", 0), "Minutos")
        return result(value, [f"Valor calculado: {fmt(value)}"])
    if calc_id == "thirteenth":
        salary = nonnegative(data.get("salary"), "Salário")
        months = nonnegative(data.get("months"), "Avos")
        if months > 12:
            raise ValueError("Avos não pode ultrapassar 12.")
        base = salary + nonnegative(data.get("averages", 0), "Médias") + nonnegative(data.get("otherBase", 0), "Outros valores")
        gross = base * months / 12
        first = gross * nonnegative(data.get("firstInstallmentPercentage"), "Percentual da primeira parcela") / 100
        deductions = nonnegative(data.get("deductions", 0), "Descontos")
        installment = data.get("installment")
        if installment not in ("TOTAL", "FIRST", "SECOND"):
            raise ValueError("Selecione a parcela do 13º salário.")
        value = first if installment == "FIRST" else gross - first - deductions if installment == "SECOND" else gross - deductions
        return result(value, [f"Base: {fmt(base)}", f"13º proporcional: {fmt(gross)}", f"Primeira parcela: {fmt(first)}", f"Parcela {installment}; descontos informados: {fmt(deductions)}"])
    if calc_id == "vacation":
        base = nonnegative(data.get("salary"), "Salário") + nonnegative(data.get("averages", 0), "Médias") + nonnegative(data.get("additions", 0), "Adicionais")
        divisor = positive(data.get("divisorDays"), "Divisor em dias")
        vacation = base / divisor * nonnegative(data.get("vacationDays"), "Dias de férias")
        third = vacation / 3
        include_abono = str(data.get("includeAbono") or "")
        include_third = str(data.get("includeThirdOnAbono") or "")
        if include_abono not in ("0", "1") or include_third not in ("0", "1"):
            raise ValueError("Informe se haverá abono e se o terço será aplicado a ele.")
        abono = base / divisor * nonnegative(data.get("sellDays", 0), "Dias vendidos") if _yes(include_abono) else ZERO
        abono_third = abono / 3 if _yes(include_abono) and _yes(include_third) else ZERO
        gross = vacation + third + abono + abono_third
        value = gross - nonnegative(data.get("deductions", 0), "Descontos")
        return result(value, [f"Remuneração de férias: {fmt(vacation)}", f"1/3: {fmt(third)}", f"Abono: {fmt(abono)}; 1/3 sobre abono: {fmt(abono_third)}", f"Estimativa líquida: {fmt(value)}"])
    if calc_id == "transport":
        salary, pct, benefit = nonnegative(data.get("salary"), "Salário"), nonnegative(data.get("percentage"), "Percentual"), nonnegative(data.get("benefit"), "Benefício")
        limit = salary * pct / 100
        value = min(limit, benefit)
        return result(value, [f"Limite: {fmt(limit)}", f"Benefício: {fmt(benefit)}", f"Desconto aplicado: {fmt(value)}"])
    if calc_id in ("net", "termination"):
        def amounts(raw):
            return [nonnegative(x, "Valor") for x in re.split(r"[;\n]+", str(raw or "")) if x.strip()]
        earnings, deductions = amounts(data.get("earnings", "")), amounts(data.get("deductions", ""))
        gross, discounts = sum(earnings, ZERO), sum(deductions, ZERO)
        memory = [f"Proventos/verbas informados: {fmt(gross)}", f"Descontos informados fora dos tributos: {fmt(discounts)}"]
        tax_details = {}
        tax_total = ZERO
        if calc_id == "net" and str(data.get("applyPublishedTaxes") or "NO").upper() == "YES":
            if not data.get("_taxParameters") or not data.get("_irrfParameters"):
                raise ValueError("Não há as duas tabelas publicadas necessárias para esta referência.")
            tax_base = nonnegative(data.get("taxableRemuneration"), "Base salarial tributável")
            inss_input = {**data, "remuneration": str(tax_base), "mode": "MONTHLY"}
            inss_result = _inss(inss_input)
            inss_deductible = data.get("inssDeductible")
            if inss_deductible in (None, ""):
                has_other_links = (nonnegative(data.get("otherRemuneration", 0) or 0, "Bases de outros vínculos") > 0
                                   or nonnegative(data.get("contributionAlreadyWithheld", 0) or 0, "INSS já retido") > 0)
                if has_other_links or inss_result.get("estimatedContributionForThisLink") is None:
                    raise ValueError("Com outros vínculos, informe o INSS efetivamente descontado neste pagamento e a origem comprovada; o saldo agregado não é atribuído automaticamente a este vínculo.")
                inss_deductible = inss_result["estimatedContributionForThisLink"]
                paycheck_inss = Decimal(inss_deductible)
            else:
                inss_deductible = nonnegative(inss_deductible, "INSS dedutível para IRRF")
                source = str(data.get("inssDeductibleSource") or "").strip()
                has_other_links = (nonnegative(data.get("otherRemuneration", 0) or 0, "Bases de outros vínculos") > 0
                                   or nonnegative(data.get("contributionAlreadyWithheld", 0) or 0, "INSS já retido") > 0)
                if has_other_links and not source:
                    raise ValueError("Informe a origem comprovada do INSS dedutível deste vínculo.")
                paycheck_inss = inss_deductible
            irrf_input = {**data, "remuneration": str(tax_base), "inss": str(inss_deductible),
                          "_taxParameters": data["_irrfParameters"], "_taxVersion": data.get("_irrfVersion"),
                          "mode": "MONTHLY"}
            irrf_result = _irrf(irrf_input)
            tax_total = paycheck_inss + Decimal(irrf_result["finalTax"])
            tax_details = {"inss": inss_result, "irrf": irrf_result, "inssForThisPayment": money(paycheck_inss),
                           "inssVersion": data.get("_taxVersion"), "irrfVersion": data.get("_irrfVersion"),
                           "competence": data.get("competence"), "paymentDate": data.get("paymentDate")}
            memory.extend([f"Base salarial tributável informada: {fmt(tax_base)}.",
                f"INSS progressivo teórico publicado v{data.get('_taxVersion')}: R$ {inss_result['totalContributionTheoretical']}.",
                f"INSS considerado neste pagamento: R$ {money(paycheck_inss)}; outros vínculos e retenções não foram distribuídos automaticamente.",
                f"IRRF publicado v{data.get('_irrfVersion')}: {fmt(Decimal(irrf_result['finalTax']))}."])
        value = gross - discounts - tax_total
        memory.extend([f"Resultado líquido estimado: {fmt(value)}", "A simulação não altera a folha nem gera obrigação de recolhimento."])
        if calc_id == "termination":
            memory.append("Nenhuma parcela rescisória foi incluída automaticamente; confira as verbas aplicáveis.")
        return {**result(value, memory), "taxDetails": tax_details}
    if calc_id == "inss":
        return _inss(data)
    if calc_id == "irrf":
        return _irrf(data)
    raise ValueError("Calculadora não reconhecida.")


def _validate_tax_competence(data):
    competence=str(data.get("competence", ""))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", competence):
        raise ValueError("Informe uma competência válida (AAAA-MM).")
    if data.get("mode") not in ("MONTHLY", "THIRTEENTH"):
        raise ValueError("Escolha o modo mensal ou 13º salário.")


def _inss(data):
    _validate_tax_competence(data)
    if not data.get("_taxParameters"):
        raise ValueError("Não há tabela INSS publicada para esta competência e categoria.")
    if data.get("mode") not in data["_taxParameters"].get("supportedModes", []):
        raise ValueError("Esta versão não suporta o modo de cálculo selecionado.")
    from .tax_tables import calculate_inss
    computed = calculate_inss(data["_taxParameters"], data.get("remuneration"),
        data.get("otherRemuneration", 0), data.get("contributionAlreadyWithheld", 0))
    computed["memory"].insert(0, f"Tabela INSS publicada: competência {data.get('competence')} · versão local {data.get('_taxVersion','—')} · categoria empregado.")
    computed["versionId"] = data.get("_taxVersion")
    computed["source"] = data.get("_taxSource", "")
    computed["mode"] = data.get("mode")
    return computed


def _irrf(data):
    if data.get("mode", "MONTHLY") != "MONTHLY":
        raise ValueError("A apuração de IRRF para 13º ainda não tem uma versão publicada e validada neste módulo.")
    if not data.get("paymentDate"):
        raise ValueError("Informe a data efetiva de pagamento para selecionar a tabela IRRF.")
    try:
        parsed_payment_date = date.fromisoformat(str(data["paymentDate"]))
    except (TypeError, ValueError) as error:
        raise ValueError("Informe uma data efetiva de pagamento válida.") from error
    if parsed_payment_date.isoformat() != str(data["paymentDate"]):
        raise ValueError("Informe uma data efetiva de pagamento válida.")
    if not data.get("_taxParameters"):
        raise ValueError("Não há tabela IRRF publicada para esta data de pagamento e tipo de rendimento.")
    if data.get("mode") not in data["_taxParameters"].get("supportedModes", []):
        raise ValueError("Esta versão não suporta o modo de cálculo selecionado.")
    from .tax_tables import calculate_irrf
    computed = calculate_irrf(data["_taxParameters"], data.get("remuneration"),
        data.get("inss", 0), data.get("dependents", 0), data.get("alimony", 0),
        data.get("otherLegalDeductions", 0), data.get("deductionMethod", "AUTO"),
        data.get("incomeType", "SALARY_MONTHLY"))
    competence = str(data.get("competence") or "não informada (não usada para selecionar o IRRF)")
    computed["memory"].insert(0, f"Tabela IRRF publicada: pagamento em {data.get('paymentDate')} · versão local {data.get('_taxVersion','—')} · competência previdenciária: {competence}.")
    computed["versionId"] = data.get("_taxVersion")
    computed["source"] = data.get("_taxSource", "")
    computed["paymentDate"] = data.get("paymentDate")
    return computed


CALCULATORS = [
    ("overtime", "Hora extra", [("salary", "Salário base", "3000"), ("divisor", "Divisor mensal", "220"), ("hours", "Horas", "1"), ("percentage", "Adicional (%)", "50")]),
    ("direct", "Hora extra direta", [("salaryHour", "Valor da hora", ""), ("hours", "Horas", ""), ("percentage", "Adicional (%)", "50")]),
    ("night", "Adicional noturno", [("salary", "Salário base", "3000"), ("divisor", "Divisor mensal", "220"), ("hours", "Horas noturnas", ""), ("percentage", "Adicional (%)", "25")]),
    ("reduced", "Hora noturna reduzida", [("clockHours", "Horas de relógio", ""), ("reducedMinutes", "Minutos por hora noturna", "52.5"), ("applyReduction", "Aplicar redução (sim/não)", "sim")]),
    ("interval", "Intervalo noturno", [("start", "Início (HH:MM)", "22:00"), ("end", "Fim (HH:MM)", "05:00"), ("breakMinutes", "Intervalo em minutos", "0"), ("extensionMinutes", "Prorrogação em minutos", "0"), ("reducedMinutes", "Minutos por hora noturna", "52.5"), ("applyReduction", "Aplicar redução (sim/não)", "")]),
    ("nighthe", "HE noturna", [("salary", "Salário base", "3000"), ("divisor", "Divisor mensal", "220"), ("clockHours", "Horas noturnas", ""), ("overtimePercentage", "Adicional de HE (%)", "50"), ("nightPercentage", "Adicional noturno (%)", "25"), ("reducedMinutes", "Minutos por hora noturna", "52.5"), ("applyReduction", "Aplicar redução (sim/não)", "sim"), ("nightPremiumBasis", "Adicional noturno sobre (NORMAL/OVERTIME_HOUR)", "NORMAL")]),
    ("dsr", "DSR", [("variableTotal", "Valor variável", ""), ("startDate", "Início do período", ""), ("endDate", "Fim do período", ""), ("city", "Localidade dos feriados", ""), ("holidays", "Feriados ISO separados por ponto e vírgula (opcional)", "")]),
    ("dsr-discount", "Desconto de DSR", [("salary", "Salário mensal", ""), ("divisorDays", "Divisor em dias", ""), ("startDate", "Início do período", ""), ("endDate", "Fim do período", ""), ("city", "Localidade dos feriados", ""), ("absenceDates", "Faltas ISO separadas por ponto e vírgula", ""), ("holidays", "Feriados ISO separados por ponto e vírgula (opcional)", "")]),
    ("averages", "Médias", [("values", "Valores mensais separados por vírgula", ""), ("totalMonths", "Meses no período", "12"), ("divideBy", "Divisor (ALL_MONTHS/INFORMED_MONTHS)", "ALL_MONTHS")]),
    ("unhealthy", "Insalubridade", [("base", "Base informada", ""), ("percentage", "Percentual informado", ""), ("conditionStatus", "Condição", ""), ("validity", "Vigência/período da base", ""), ("source", "Fonte ou documento (opcional)", "")]),
    ("danger", "Periculosidade", [("base", "Base informada", ""), ("percentage", "Percentual informado", ""), ("conditionStatus", "Condição", ""), ("validity", "Vigência/período da base", ""), ("source", "Fonte ou documento (opcional)", ""), ("exposureHours", "Horas de exposição (informativo)", "")]),
    ("absence", "Faltas", [("salary", "Salário", ""), ("days", "Dias de falta", ""), ("divisorDays", "Divisor em dias", "30")]),
    ("delay", "Atrasos", [("salary", "Salário", ""), ("divisor", "Divisor mensal", "220"), ("hours", "Horas", "0"), ("minutes", "Minutos", "0")]),
    ("proportional", "Salário proporcional", [("salary", "Salário mensal", ""), ("divisorDays", "Divisor em dias", "30"), ("entitledDays", "Dias de direito", "")]),
    ("thirteenth", "13º salário", [("salary", "Salário", ""), ("months", "Avos", ""), ("averages", "Médias", "0"), ("otherBase", "Outros valores", "0"), ("installment", "Parcela (TOTAL/FIRST/SECOND)", "TOTAL"), ("firstInstallmentPercentage", "Percentual da 1ª parcela", "50"), ("deductions", "Descontos informados", "0")]),
    ("vacation", "Férias", [("salary", "Salário", ""), ("averages", "Médias", "0"), ("additions", "Adicionais", "0"), ("vacationDays", "Dias de férias", "30"), ("sellDays", "Dias vendidos", "0"), ("includeAbono", "Incluir abono (sim/não)", "não"), ("includeThirdOnAbono", "1/3 sobre abono (sim/não)", "não"), ("deductions", "Descontos informados", "0")]),
    ("transport", "Vale-transporte", [("salary", "Salário", ""), ("percentage", "Desconto máximo (%)", "6"), ("benefit", "Valor concedido", "")]),
    ("net", "Salário líquido", [("earnings", "Proventos separados por ponto e vírgula ou nova linha", ""), ("deductions", "Descontos sem INSS/IRRF quando usar tabela publicada", ""), ("applyPublishedTaxes", "Aplicar INSS e IRRF de tabelas publicadas?", "NO"), ("competence", "Competência previdenciária (INSS)", ""), ("paymentDate", "Data efetiva do pagamento (IRRF)", ""), ("taxableRemuneration", "Base tributável informada para INSS/IRRF", ""), ("otherRemuneration", "Bases de outros vínculos na mesma competência", ""), ("contributionAlreadyWithheld", "INSS informado como já retido em outros vínculos", ""), ("inssDeductible", "INSS efetivamente descontado neste pagamento", ""), ("inssDeductibleSource", "Origem comprovada do INSS neste pagamento", ""), ("dependents", "Dependentes para dedução legal", ""), ("alimony", "Pensão alimentícia dedutível informada", ""), ("otherLegalDeductions", "Outras deduções legais informadas", ""), ("deductionMethod", "Comparar deduções legais e desconto simplificado", "AUTO")]),
    ("termination", "Rescisão assistida", [("earnings", "Verbas informadas separadas por vírgula", ""), ("deductions", "Descontos informados separados por vírgula", "")]),
    ("inss", "INSS progressivo por competência (empregado)", [("competence", "Competência previdenciária", ""), ("mode", "Apuração", "MONTHLY"), ("remuneration", "Remuneração deste vínculo", ""), ("otherRemuneration", "Bases agregadas de outros vínculos na mesma competência", ""), ("contributionAlreadyWithheld", "INSS já retido em outros vínculos", "")]),
    ("irrf", "IRRF mensal por data de pagamento", [("competence", "Competência da remuneração (contexto INSS)", ""), ("paymentDate", "Data efetiva de pagamento para IRRF", ""), ("mode", "Tipo de apuração", "MONTHLY"), ("remuneration", "Rendimentos tributáveis mensais", ""), ("inss", "INSS dedutível informado", ""), ("dependents", "Dependentes", ""), ("alimony", "Pensão alimentícia dedutível informada", ""), ("otherLegalDeductions", "Outras deduções legais informadas", ""), ("deductionMethod", "Comparar métodos de dedução", "AUTO")]),
]
