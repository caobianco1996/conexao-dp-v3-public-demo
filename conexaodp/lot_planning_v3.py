"""Business rules for the local V3 benefit-lot planner.

All dates are ISO calendar dates. Money is represented only in integer cents.
The schedule and unresolved company policies must be provided explicitly.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations

from .lot_planning import bank_state, valid_day


SCHEDULES = {"WEEKDAYS_5X2", "ROTATING_6X1", "ROTATING_6X2"}
BENEFITS = {"VT", "VR"}


def _days(start: str, end: str):
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        yield current.isoformat()
        current += timedelta(days=1)


def resolve_cutoff(admission: str, payroll_day: int, february_policy: str = "UNRESOLVED",
                   admission_cutoff_policy: str = "UNRESOLVED") -> tuple[str, str]:
    """Resolve the benefit cutoff without normalizing missing dates silently.

    The confirmed day-05 rule covers through the next month's day 05. The
    exceptional exact-admission-on-cutoff and February/day-30 cases require an
    explicit company policy.
    """
    admitted = date.fromisoformat(admission)
    if payroll_day == 5:
        current_nominal = date(admitted.year, admitted.month, 5)
        if admitted == current_nominal:
            if admission_cutoff_policy == "UNRESOLVED":
                raise ValueError("A admissão ocorreu exatamente no dia de corte 05. Defina se ela entra no ciclo atual ou no próximo.")
            if admission_cutoff_policy == "INCLUDE_CURRENT":
                return current_nominal.isoformat(), "Dia 05"
        following = date(admitted.year + (admitted.month == 12), 1 if admitted.month == 12 else admitted.month + 1, 5)
        return following.isoformat(), "Dia 05"

    if payroll_day != 30:
        raise ValueError("A regra de pagamento precisa ser dia fixo 05 ou 30.")
    month_start = date(admitted.year, admitted.month, 1)
    for offset in range(0, 15):
        month_index = month_start.month - 1 + offset
        year = month_start.year + month_index // 12
        month = month_index % 12 + 1
        following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        last_day = (following - timedelta(days=1)).day
        if last_day < 30:
            if february_policy == "UNRESOLVED":
                raise ValueError("O pagamento configurado para o dia 30 cai em fevereiro, onde esse dia não existe. Defina a regra da empresa para meses curtos.")
            if february_policy == "LAST_DAY":
                candidate = date(year, month, last_day)
                label = "Último dia do mês curto, conforme regra da empresa"
            elif february_policy == "NEXT_MONTH":
                continue
            else:
                raise ValueError("A regra de fevereiro para pagamento no dia 30 é inválida.")
        else:
            candidate = date(year, month, 30)
            label = "Dia 30"
        if candidate > admitted:
            return candidate.isoformat(), label
        if candidate == admitted:
            if admission_cutoff_policy == "UNRESOLVED":
                raise ValueError(f"A admissão ocorreu exatamente no corte {candidate:%d/%m/%Y}. Defina se ela entra no ciclo atual ou no próximo.")
            if admission_cutoff_policy == "INCLUDE_CURRENT":
                return candidate.isoformat(), label
    raise ValueError("Não foi possível resolver um corte de pagamento futuro com a regra cadastrada.")


def scheduled_work(day: str, schedule: str, schedule_start: str) -> bool:
    if schedule not in SCHEDULES:
        raise ValueError("Escolha uma escala suportada: 5x2, 6x1 ou 6x2.")
    current = date.fromisoformat(day)
    if schedule == "WEEKDAYS_5X2":
        return current.weekday() < 5
    if not valid_day(schedule_start):
        raise ValueError("Informe a data de início do ciclo da escala rotativa.")
    start = date.fromisoformat(schedule_start)
    cycle = 7 if schedule == "ROTATING_6X1" else 8
    offset = (current - start).days % cycle
    return 0 <= offset < 6


def _calendar_adjusted(target: str, policy: str, holiday_days: set[str], bank_rows: list[dict],
                       *, respect_bank: bool, client_id: str, unit_id: str,
                       exception_days: set[str] | None = None) -> str:
    if policy not in ("PREVIOUS_WORKDAY", "NEXT_WORKDAY", "NO_ADJUSTMENT"):
        raise ValueError("A política de pagamento da empresa ainda não foi configurada. Configure antecipação, postergação ou data fixa antes de calcular.")
    if policy == "NO_ADJUSTMENT":
        candidates = [target]
    else:
        direction = -1 if policy == "PREVIOUS_WORKDAY" else 1
        current = date.fromisoformat(target)
        candidates = []
        for _ in range(15):
            value = current.isoformat()
            if current.weekday() < 5 and value not in holiday_days:
                candidates.append(value)
            current += timedelta(days=direction)
    for value in candidates:
        if value in holiday_days:
            continue
        if respect_bank and bank_state(value, bank_rows, client_id, unit_id) != "YES" and value not in (exception_days or set()):
            continue
        if policy == "NO_ADJUSTMENT" or date.fromisoformat(value).weekday() < 5:
            return value
    if respect_bank:
        raise ValueError(f"Não há data válida de processamento para {target}: verifique feriados e os estados Sim/Não/Não definido da Relação Bancária.")
    raise ValueError(f"A data de pagamento {target} cai em fim de semana/feriado e a política configurada não encontrou uma data útil.")


def calculate_plan(*, inputs: dict, employee: dict | None, company_rule: dict,
                   bank_rows: list[dict] | None = None, holidays: list[dict] | None = None) -> dict:
    """Validate, calculate lots, and return an auditable calendar snapshot."""
    bank_rows, holidays = bank_rows or [], holidays or []
    employee = employee or {}
    benefit = str(inputs.get("benefit_type") or "").upper()
    if benefit not in BENEFITS:
        raise ValueError("Selecione VT ou VR.")
    admission = str(inputs.get("admission_date") or employee.get("admission_date") or "")
    if not valid_day(admission):
        raise ValueError("Informe uma data de admissão válida para este planejamento.")
    if employee.get("termination_date") and employee["termination_date"] < admission:
        raise ValueError("O vínculo está desligado antes da data de admissão informada.")
    today = date.today().isoformat()
    if admission > today:
        raise ValueError("A admissão é futura; não é possível calcular cobertura como se o vínculo já tivesse começado.")
    payment_source = str(inputs.get("payment_source") or "COMPANY").upper()
    if payment_source == "COMPANY" and not company_rule:
        raise ValueError("Não há regra vigente para esta empresa e benefício.")
    company_rule = company_rule or {}
    try:
        payroll_day = (int(inputs.get("payment_day_override")) if payment_source in ("GENERAL", "MANUAL")
                       else int(company_rule["payment_day"]))
        lot_count = int(inputs.get("lot_count"))
        unit_cents = int(inputs.get("unit_cents", company_rule["unit_cents"]) if benefit == "VT" else company_rule["unit_cents"])
        uses_per_day = int(inputs.get("uses_per_day", company_rule["uses_per_day"]) if benefit == "VT" else company_rule["uses_per_day"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Confira o dia de pagamento, a tarifa/unidade e a quantidade diária informados para este planejamento.")
    if payment_source not in ("COMPANY", "GENERAL", "MANUAL"):
        raise ValueError("Escolha uma origem de pagamento válida.")
    if payroll_day not in (5, 30):
        raise ValueError("A empresa deve usar pagamento fixo no dia 05 ou 30.")
    if lot_count not in (1, 2, 3):
        raise ValueError("Escolha 1, 2 ou 3 lotes. O sistema não altera a quantidade automaticamente.")
    if unit_cents < 0 or unit_cents > 100_000_000:
        raise ValueError("O valor do benefício precisa estar entre R$ 0,00 e R$ 1.000.000,00.")
    if unit_cents == 0 and not str(inputs.get("zero_value_reason") or "").strip():
        raise ValueError("Para usar valor zero, informe uma justificativa.")
    unit_mode = "PER_USE" if benefit == "VT" else str(company_rule.get("unit_mode") or "UNSPECIFIED")
    if benefit == "VT" and unit_cents <= 0:
        raise ValueError("Informe a tarifa por utilização do VT para este planejamento.")
    if benefit == "VR" and unit_mode not in ("PER_DAY", "PER_MEAL"):
        raise ValueError("Defina se o valor de VR é diário ou por refeição antes de calcular.")
    if benefit == "VR" and unit_mode == "PER_DAY":
        uses_per_day = 1
    if uses_per_day < 1 or uses_per_day > 20:
        raise ValueError("A quantidade diária deve ser um número inteiro entre 1 e 20.")
    schedule = str(inputs.get("schedule") or "")
    schedule_start = str(inputs.get("schedule_start") or "")
    if schedule not in SCHEDULES:
        raise ValueError("Selecione uma escala real suportada. Nenhuma escala padrão foi presumida.")
    if schedule == "WEEKDAYS_5X2":
        schedule_start = ""
    elif not valid_day(schedule_start):
        raise ValueError("Informe a data de início do ciclo da escala rotativa.")
    if schedule_start and schedule_start > admission:
        raise ValueError("O início do ciclo da escala não pode ser posterior à admissão neste planejamento.")
    holiday_rule = str(company_rule.get("holiday_benefit_rule") or "UNRESOLVED")
    if holiday_rule not in ("COUNT", "EXCLUDE"):
        raise ValueError("A regra de benefício em feriado ainda não foi definida para esta empresa.")
    payment_policy = str(company_rule.get("payment_adjustment") or "PREVIOUS_WORKDAY")
    february_policy = str(company_rule.get("february_30_policy") or "NEXT_MONTH")
    admission_cutoff_policy = str(company_rule.get("admission_on_cutoff_policy") or "UNRESOLVED")
    if february_policy == "UNRESOLVED":
        february_policy = "NEXT_MONTH"
    respect_bank = bool(inputs.get("respect_bank"))
    cutoff, nominal_payroll_label = resolve_cutoff(admission, payroll_day, february_policy, admission_cutoff_policy)
    exceptions = inputs.get("bank_exceptions") or []
    if exceptions and not respect_bank:
        raise ValueError("Ative a Relação Bancária antes de usar uma exceção autorizada.")
    exception_reasons = {}
    for item in exceptions:
        day, reason = str(item.get("day") or ""), str(item.get("reason") or "").strip()
        if not valid_day(day) or not admission <= day <= cutoff or len(reason) < 5 or day in exception_reasons:
            raise ValueError("Revise a data e a justificativa de cada exceção bancária autorizada.")
        exception_reasons[day] = reason
    holidays_by_day = {}
    for holiday in holidays:
        day = str(holiday.get("day") or holiday.get("date") or "")
        if valid_day(day):
            detail = {"name": str(holiday.get("name") or "Feriado"),
                      "scope": str(holiday.get("scope") or "Abrangência não identificada"),
                      "city": str(holiday.get("city") or ""), "state": str(holiday.get("state") or ""),
                      "source": str(holiday.get("source") or "")}
            holidays_by_day.setdefault(day, []).append(detail)
    holiday_days = set(holidays_by_day)
    nominal_payroll_date = cutoff
    effective_payroll_date = _calendar_adjusted(nominal_payroll_date, payment_policy, holiday_days, bank_rows,
                                               respect_bank=respect_bank,
                                               client_id=str(inputs.get("client_id") or ""),
                                               unit_id=str(inputs.get("unit_id") or ""),
                                               exception_days=set(exception_reasons))
    calendar_start = date.fromisoformat(admission).replace(day=1).isoformat()
    cutoff_value = date.fromisoformat(cutoff)
    month_end = (date(cutoff_value.year + (cutoff_value.month == 12),
                      1 if cutoff_value.month == 12 else cutoff_value.month + 1, 1) - timedelta(days=1))
    # In a 31-day month, keep day 31 visible as an explicit outside-cutoff day
    # when the configured business cutoff is day 30.
    comparison_end = month_end if cutoff_value.day == 30 and month_end.day == 31 else cutoff_value
    calendar_end = max(comparison_end.isoformat(), effective_payroll_date)
    calendar_days = list(_days(admission, cutoff))
    display_days = list(_days(calendar_start, calendar_end))
    eligible = []
    day_details = {}
    for day in display_days:
        work = scheduled_work(day, schedule, schedule_start)
        holiday_details = holidays_by_day.get(day, [])
        names = [item["name"] for item in holiday_details]
        in_period = admission <= day <= cutoff
        include = in_period and work and (not names or holiday_rule == "COUNT")
        state = bank_state(day, bank_rows, str(inputs.get("client_id") or ""), str(inputs.get("unit_id") or ""))
        reason = ("anterior à admissão; fora da cobertura" if day < admission else
                  "posterior ao corte; fora da cobertura" if day > cutoff else
                  "dia de benefício" if include else
                  "feriado excluído pela empresa" if work and names else
                  "folga da escala" if not work else "")
        day_details[day] = {"day": day, "work": work, "inPeriod": in_period, "eligible": include,
                            "holidayNames": names, "holidayDetails": holiday_details,
                            "bankState": state, "bankException": exception_reasons.get(day), "reason": reason}
        if include:
            eligible.append(day)
    if len(eligible) < lot_count * 2:
        raise ValueError(f"O período {admission} a {cutoff} tem apenas {len(eligible)} dias de benefício elegíveis; {lot_count} lote(s) exigem ao menos {lot_count * 2}. Reduza os lotes ou confira ciclo, escala e regra de feriados.")
    client_id, unit_id = str(inputs.get("client_id") or ""), str(inputs.get("unit_id") or "")
    usable = []
    rejected = []
    for day in calendar_days:
        if day in holiday_days:
            rejected.append({"day": day, "reason": "feriado"})
            continue
        current = date.fromisoformat(day)
        if respect_bank:
            state = bank_state(day, bank_rows, client_id, unit_id)
            if state != "YES" and day not in exception_reasons:
                rejected.append({"day": day, "reason": "Relação Bancária não marcada Sim" if state is None else "Relação Bancária marcada Não/Outra"})
                continue
            usable.append(day)
        elif current.weekday() < 5:
            usable.append(day)
        else:
            rejected.append({"day": day, "reason": "fim de semana; expediente não configurado para processamento"})
    first_options = [day for day in usable if date.fromisoformat(admission) < date.fromisoformat(day) <= date.fromisoformat(admission) + timedelta(days=4)]
    target = date.fromisoformat(admission) + timedelta(days=2)
    first_pay = min(first_options, key=lambda d: (abs((date.fromisoformat(d) - target).days), d), default="")
    if not first_pay:
        missing = sum(item["reason"] == "Relação Bancária não marcada Sim" for item in rejected)
        extra = f" Há {missing} dia(s) sem marcação Sim no intervalo consultado." if respect_bank else ""
        raise ValueError("Não há data de processamento válida entre 1 e 4 dias após a admissão." + extra)
    chosen_boundaries = []
    if lot_count > 1:
        candidates = [day for day in usable if date.fromisoformat(day) > date.fromisoformat(first_pay) and date.fromisoformat(day) < date.fromisoformat(cutoff)]
        target_counts = [len(eligible) * i / lot_count for i in range(1, lot_count)]
        best = None
        for option in combinations(candidates, lot_count - 1):
            boundaries = list(option)
            starts = [admission, *[(date.fromisoformat(day) + timedelta(days=1)).isoformat() for day in boundaries]]
            ends = [*boundaries, cutoff]
            segment_counts = [sum(starts[i] <= day <= ends[i] for day in eligible) for i in range(lot_count)]
            if any(count < 2 for count in segment_counts):
                continue
            # Choose processing dates that divide the eligible workdays as evenly
            # as possible. This score is based on cumulative workdays at each
            # boundary, so a long weekend does not distort the split by calendar
            # length alone.
            score = sum((sum(day <= boundary for day in eligible) - target_counts[j]) ** 2
                        for j, boundary in enumerate(boundaries))
            if best is None or score < best[0]:
                best = (score, boundaries)
        if not best:
            raise ValueError(f"Não há {lot_count - 1} datas distintas de processamento válidas para dividir a cobertura em lotes com ao menos 2 dias elegíveis cada. Confira os dias verdes e reduza os lotes se necessário.")
        chosen_boundaries = best[1]
    edges = [admission, *chosen_boundaries, cutoff]
    lots = []
    pay_dates = [first_pay, *chosen_boundaries]
    for index in range(lot_count):
        start = edges[index] if index == 0 else (date.fromisoformat(edges[index]) + timedelta(days=1)).isoformat()
        end = edges[index + 1]
        days = [day for day in eligible if start <= day <= end]
        if len(days) < 2:
            raise ValueError(f"O lote {index + 1} ficou com menos de dois dias de benefício elegíveis.")
        quantity = len(days) * uses_per_day
        lots.append({"number": index + 1, "from": start, "to": end, "pay_date": pay_dates[index],
                     "eligible_days": len(days), "excluded_days": sum(start <= d <= end for d in calendar_days if not day_details[d]["eligible"]),
                     "uses_per_day": uses_per_day, "quantity": quantity, "unit_cents": unit_cents,
                     "unit_mode": unit_mode, "amount_cents": unit_cents * quantity, "benefit_type": benefit})
        for day in calendar_days:
            if start <= day <= end:
                day_details[day]["lotNumber"] = index + 1
                day_details[day]["covered"] = day_details[day]["eligible"]
    for index, pay_date in enumerate(pay_dates):
        if pay_date in day_details:
            day_details[pay_date]["payDate"] = True
            day_details[pay_date]["paymentLot"] = index + 1
    if effective_payroll_date in day_details:
        day_details[effective_payroll_date]["payrollDate"] = True
    bank_period = inputs.get("bank_query_period") or {}
    consulted_period = (f"; consulta completa de {bank_period.get('from')} a {bank_period.get('through')}"
                        if bank_period.get("from") and bank_period.get("through") else "")
    memory = [
        (f"Fonte do colaborador: {employee.get('source') or 'snapshot local'}; identificador {employee.get('key') or ''}."
         if employee.get("key") else
         f"Planejamento sem vínculo integrado; identificação: {str(inputs.get('employee_name') or 'não informada').strip()}; empresa: {inputs.get('company_name') or 'Empresa geral'}."),
        (f"Pagamento nominal: dia {payroll_day:02d} escolhido manualmente para Empresa geral; corte de cobertura inclusivo em {cutoff}."
         if payment_source == "GENERAL" else
         f"Pagamento nominal: dia {payroll_day:02d} escolhido manualmente para esta empresa; corte de cobertura inclusivo em {cutoff}."
         if payment_source == "MANUAL" else
         f"Regra da empresa: pagamento nominal {nominal_payroll_label} ({company_rule.get('source') or 'configuração manual'}); corte de cobertura inclusivo em {cutoff}."),
        f"Pagamento nominal em {nominal_payroll_date}; data operacional após a política configurada: {effective_payroll_date} ({payment_policy}).",
        ("Feriados não contam como dias de benefício nesta previsão; o sistema não presume trabalho em feriado."
         if holiday_rule == "EXCLUDE" else "A regra cadastrada inclui feriados nos dias de benefício."),
        (f"Escala 5x2 (segunda a sexta) informada manualmente; dias de benefício elegíveis: {len(eligible)} de {len(calendar_days)} dias corridos."
         if schedule == "WEEKDAYS_5X2" else
         f"Escala {schedule} informada manualmente; início do ciclo {schedule_start}; dias de benefício elegíveis: {len(eligible)} de {len(calendar_days)} dias corridos."),
        (f"Benefício VT informado manualmente neste planejamento: {unit_cents} centavos por utilização × {uses_per_day} utilização(ões) por dia × dias elegíveis; sem desconto de folha aplicado."
         if benefit == "VT" else
         f"Benefício VR: {unit_cents} centavos por {'dia' if unit_mode == 'PER_DAY' else 'refeição'} × {uses_per_day} unidade(s) por dia × dias elegíveis; sem desconto de folha aplicado."),
        f"Relação Bancária {'foi usada' if respect_bank else 'não foi usada'} para escolher datas de processamento{consulted_period}; feriados tiveram prioridade e dias sem Sim não foram tratados como disponíveis.",
        "Critério proposto para as datas dos lotes: o primeiro processamento usa uma data operacional válida entre 1 e 4 dias após a admissão, mais próxima do segundo dia; os limites seguintes distribuem os dias elegíveis de trabalho de forma equilibrada. Revise a proposta conforme a rotina confirmada da empresa.",
    ]
    for lot in lots:
        memory.append(f"Lote {lot['number']}: cobertura inclusiva {lot['from']} a {lot['to']}; pagamento {lot['pay_date']}; {lot['eligible_days']} dia(s) elegíveis × {uses_per_day} × R$ {unit_cents / 100:.2f} = R$ {lot['amount_cents'] / 100:.2f}.")
    if rejected:
        memory.append("Datas de processamento descartadas (amostra): " + "; ".join(f"{row['day']}: {row['reason']}" for row in rejected[:12]) + (f"; e mais {len(rejected) - 12}" if len(rejected) > 12 else ""))
    for item in exceptions:
        memory.append(f"Exceção bancária autorizada em {item['day']}: {item['reason']}; estado original preservado no calendário.")
    bank_period = inputs.get("bank_query_period") or {}
    bank_start = str(bank_period.get("from") or calendar_start)
    bank_end = str(bank_period.get("through") or calendar_end)
    return {"cutoff_date": cutoff, "nominal_payroll_date": nominal_payroll_date,
            "payment_source": payment_source, "payment_day_used": payroll_day,
            "nominal_payroll_label": nominal_payroll_label,
            "effective_payroll_date": effective_payroll_date, "payment_adjustment": payment_policy, "lots": lots,
            "total_cents": sum(lot["amount_cents"] for lot in lots), "memory": memory,
            "eligible_dates": eligible, "calendar": list(day_details.values()), "rejected_processing_dates": rejected,
            "holiday_snapshot": holidays,
            "bank_query_period": {"from": bank_start, "through": bank_end},
            "bank_snapshot": [row for row in bank_rows if bank_start <= str(row.get("day") or "") <= bank_end],
            "bank_relation_enabled": respect_bank,
            "calculation_version": 1}
