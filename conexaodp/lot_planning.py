"""Regras de proposta e validação de lotes alinhadas à V2 2.0.158."""

from __future__ import annotations

from datetime import date, timedelta


def valid_day(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def add_days(value: str, count: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=count)).isoformat()


def is_weekend(value: str) -> bool:
    return date.fromisoformat(value).weekday() >= 5


def preceding_weekday(value: str) -> str:
    current = date.fromisoformat(value)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def next_payroll_cutoff(admission: str, payroll_day: int) -> str:
    if not valid_day(admission):
        raise ValueError("Informe uma data de admissão válida.")
    if payroll_day not in (5, 30):
        raise ValueError("O dia de pagamento deve ser 5 ou 30.")
    admitted = date.fromisoformat(admission)
    next_month = date(admitted.year + (admitted.month == 12), 1 if admitted.month == 12 else admitted.month + 1, 1)
    if payroll_day == 5:
        # A regra confirmada para pagamento no dia 05 cobre até o próximo dia 05.
        return date(next_month.year, next_month.month, 5).isoformat()

    def cutoff_for(year: int, month: int) -> date:
        following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        last = (following - timedelta(days=1)).day
        # Dia 30 é o corte mesmo em meses com 31 dias; em fevereiro usa o último dia existente.
        return date(year, month, min(30, last))

    cutoff = cutoff_for(admitted.year, admitted.month)
    if preceding_weekday(cutoff.isoformat()) > admission:
        return cutoff.isoformat()
    return cutoff_for(next_month.year, next_month.month).isoformat()


def normal_payroll_date(cutoff: str, payroll_day: int) -> str:
    if not valid_day(cutoff):
        raise ValueError("Informe uma data de corte válida.")
    current = date.fromisoformat(cutoff)
    payment = date(current.year, current.month, min(payroll_day, (date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1) - timedelta(days=1)).day))
    if payment.isoformat() > cutoff:
        payment = current
    return preceding_weekday(payment.isoformat())


def bank_state(day: str, rows: list[dict], client_id: str = "", unit_id: str = "") -> str | None:
    relevant = [row for row in rows if row["day"] == day]
    for scope, key in (("UNIT", unit_id), ("CLIENT", client_id), ("GENERAL", "")):
        if scope == "GENERAL" or key:
            match = next((row for row in relevant if row.get("scope_type") == scope and (row.get("scope_key") or "") == key), None)
            if match:
                return match.get("state")
    return None


def _count_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if valid_day(start) and valid_day(end) and end >= start else 0


def _scheduled_days(start: str, end: str, schedule: str = "WEEKDAYS_5X2", schedule_start: str = "") -> int:
    if schedule == "WEEKDAYS_5X2":
        current, total = date.fromisoformat(start), 0
        while current.isoformat() <= end:
            total += current.weekday() < 5
            current += timedelta(days=1)
        return total
    if not valid_day(schedule_start):
        raise ValueError("Informe a data em que começa a escala.")
    cycle = 7 if schedule == "ROTATING_6X1" else 8
    current, total = date.fromisoformat(start), 0
    while current.isoformat() <= end:
        offset = (current - date.fromisoformat(schedule_start)).days
        total += offset % cycle < 6
        current += timedelta(days=1)
    return total


def price_lots(lots: list[dict], unit_value_cents: int, uses_per_day: int, schedule: str = "", schedule_start: str = "") -> list[dict]:
    priced = []
    for lot in lots:
        days = _count_days(lot["from"], lot["to"]) if not schedule else _scheduled_days(lot["from"], lot["to"], schedule, schedule_start)
        priced.append({**lot, "days": days, "rides": days * uses_per_day, "amount_cents": days * uses_per_day * unit_value_cents})
    return priced


def propose_lots(*, admission: str, payroll_day: int, lot_count: int, unit_value_cents: int,
                 uses_per_day: int, consider_bank: bool, bank_rows: list[dict] | None = None,
                 holidays: list[str] | None = None, schedule: str = "", schedule_start: str = "", client_id: str = "", unit_id: str = "") -> dict:
    if not valid_day(admission):
        raise ValueError("Informe uma data de admissão válida.")
    if payroll_day not in (5, 30):
        raise ValueError("O dia de pagamento deve ser 5 ou 30.")
    if not isinstance(unit_value_cents, int) or unit_value_cents < 0:
        raise ValueError("Informe um valor unitário válido.")
    if not isinstance(uses_per_day, int) or uses_per_day < 1:
        raise ValueError("As utilizações diárias devem ser um número inteiro positivo.")
    if schedule and schedule != "WEEKDAYS_5X2" and not valid_day(schedule_start):
        raise ValueError("Informe a data de início da escala.")
    if not isinstance(lot_count, int) or lot_count not in (1, 2, 3):
        raise ValueError("Escolha de 1 a 3 lotes.")
    cutoff = next_payroll_cutoff(admission, payroll_day)
    payroll_date = normal_payroll_date(cutoff, payroll_day)
    days = _count_days(admission, cutoff)
    if days < 2:
        raise ValueError("O período até o próximo pagamento normal precisa ter pelo menos 2 dias.")
    if days < lot_count * 2:
        raise ValueError(f"Não é possível distribuir este período em {lot_count} lotes com 2 dias mínimos. Escolha menos lotes.")
    bank_rows, holidays = bank_rows or [], set(holidays or [])
    candidates = []
    if consider_bank or holidays:
        cur = add_days(admission, 1)
        while cur <= cutoff:
            if not is_weekend(cur) and cur not in holidays and (not consider_bank or bank_state(cur, bank_rows, client_id, unit_id) == "YES"):
                candidates.append(cur)
            cur = add_days(cur, 1)
        first_options = [day for day in candidates if day <= add_days(admission, 4)]
        target = date.fromisoformat(add_days(admission, 2))
        first_pay = min(first_options, key=lambda d: (abs((date.fromisoformat(d) - target).days), d), default="")
        if not first_pay:
            raise ValueError("Não há data útil disponível nos 4 primeiros dias após a admissão. Ajuste feriados ou Relação Bancária.")
        if lot_count == 1:
            parts = [{"number": 1, "from": admission, "to": cutoff}]
        else:
            boundary_candidates = [d for d in candidates if d > first_pay]
            boundaries: list[str] = []
            first_target = add_days(admission, 3) if lot_count == 3 else add_days(admission, max(3, int(days / 3 + 0.5) - 1))
            first = min((d for d in boundary_candidates if add_days(admission, 1) <= d <= add_days(cutoff, -(lot_count * 2 - 2))),
                        key=lambda d: (abs((date.fromisoformat(d) - date.fromisoformat(first_target)).days), d), default="")
            if not first:
                raise ValueError("Não há data útil disponível para dividir os lotes. Marque mais dias como SIM ou reduza a quantidade.")
            boundaries.append(first)
            if lot_count == 3:
                remaining = _count_days(first, cutoff)
                target2 = add_days(first, max(2, (remaining + 2) // 3 - 1))
                second = min((d for d in boundary_candidates if d >= add_days(first, 2) and d <= add_days(cutoff, -1) and d not in boundaries),
                             key=lambda d: (abs((date.fromisoformat(d) - date.fromisoformat(target2)).days), d), default="")
                if not second:
                    raise ValueError("Não há segunda data útil para o terceiro lote. Reduza a quantidade de lotes.")
                boundaries.append(second)
            edges = [admission, *boundaries, cutoff]
            parts = [{"number": i + 1, "from": edges[i] if i == 0 else add_days(edges[i], 1), "to": edges[i + 1]} for i in range(lot_count)]
            if any(_count_days(part["from"], part["to"]) < 2 for part in parts):
                raise ValueError("As datas disponíveis não deixam pelo menos 2 dias de cobertura em cada lote.")
    else:
        if lot_count == 1:
            sizes = [days]
        elif lot_count == 2:
            first = max(2, min(days - 2, days // 3))
            sizes = [first, days - first]
        else:
            first = min(5, days - 4)
            second = min(6, days - first - 2)
            sizes = [first, second, days - first - second]
        parts, cursor = [], admission
        for i, size in enumerate(sizes):
            end = add_days(cursor, size - 1)
            parts.append({"number": i + 1, "from": cursor, "to": end})
            cursor = add_days(end, 1)
    payable, used_pay_dates = [], set()
    for index, part in enumerate(parts):
        if index == 0:
            if consider_bank:
                options = [d for d in candidates if d <= add_days(admission, 4)]
                target = date.fromisoformat(add_days(admission, 2))
                pay_date = min(options, key=lambda d: (abs((date.fromisoformat(d) - target).days), d), default="")
            else:
                options = [add_days(admission, offset) for offset in range(1, 5)]
                options = [d for d in options if d <= cutoff and not is_weekend(d) and d not in holidays]
                target = date.fromisoformat(add_days(admission, 2))
                pay_date = min(options, key=lambda d: (abs((date.fromisoformat(d) - target).days), d), default="")
            if not pay_date:
                raise ValueError("Não foi encontrada data útil para o pagamento do primeiro lote.")
        else:
            pay_date = parts[index - 1]["to"]
            if consider_bank and (bank_state(pay_date, bank_rows, client_id, unit_id) != "YES" or pay_date in holidays):
                raise ValueError(f"O corte do lote {part['number']} precisa ser uma data verde e sem feriado na Relação Bancária.")
        if pay_date in used_pay_dates:
            raise ValueError("Dois lotes não podem ter pagamento na mesma data. Ajuste a divisão dos períodos.")
        used_pay_dates.add(pay_date)
        payable.append({**part, "pay_date": pay_date})
    priced = price_lots(payable, unit_value_cents, uses_per_day, schedule, schedule_start)
    if schedule and any(lot["days"] == 0 for lot in priced):
        raise ValueError("A escala não tem dias trabalhados em um dos períodos.")
    return {"cutoff_date": cutoff, "effective_payroll_date": payroll_date, "lots": priced,
            "total_cents": sum(lot["amount_cents"] for lot in priced)}


def validate_lots(*, admission: str, cutoff: str, lots: list[dict], expected_count: int,
                  unit_value_cents: int, uses_per_day: int, consider_bank: bool,
                  bank_rows: list[dict] | None = None, holidays: list[str] | None = None,
                  schedule: str = "", schedule_start: str = "", client_id: str = "", unit_id: str = "") -> dict:
    if not valid_day(admission) or not valid_day(cutoff) or cutoff < admission:
        raise ValueError("O período de cobertura é inválido.")
    if len(lots) != expected_count or expected_count not in (1, 2, 3):
        raise ValueError("A quantidade de lotes foi alterada; recalcule a sugestão.")
    if schedule and schedule != "WEEKDAYS_5X2" and not valid_day(schedule_start):
        raise ValueError("Informe a data de início da escala.")
    bank_rows, holiday_dates = bank_rows or [], set(holidays or [])
    expected_from, used_pay_dates, validated = admission, set(), []
    for index, lot in enumerate(lots):
        if not all(valid_day(lot.get(key, "")) for key in ("from", "to", "pay_date")):
            raise ValueError(f"Preencha as datas do lote {index+1}.")
        if lot.get("number") != index+1 or lot["from"] != expected_from:
            raise ValueError("Os lotes não podem se sobrepor nem deixar dias sem cobertura.")
        if _count_days(lot["from"], lot["to"]) < 2:
            raise ValueError(f"O lote {index+1} precisa cobrir pelo menos 2 dias.")
        if lot["pay_date"] > cutoff:
            raise ValueError(f"O pagamento do lote {index+1} não pode ultrapassar o corte normal.")
        if lot["pay_date"] in used_pay_dates:
            raise ValueError("Dois lotes não podem ter pagamento na mesma data.")
        used_pay_dates.add(lot["pay_date"])
        if index == 0:
            if not add_days(admission,1) <= lot["pay_date"] <= add_days(admission,4) or is_weekend(lot["pay_date"]):
                raise ValueError("O pagamento do primeiro lote deve ocorrer entre 1 e 4 dias após a admissão, em dia útil.")
        elif lot["pay_date"] != lots[index-1]["to"]:
            raise ValueError(f"O pagamento do lote {index+1} deve ocorrer no corte do lote anterior.")
        if lot["pay_date"] in holiday_dates:
            raise ValueError(f"A data {lot['pay_date']} é feriado.")
        if consider_bank and bank_state(lot["pay_date"],bank_rows,client_id,unit_id)!="YES":
            raise ValueError(f"A data de pagamento do lote {index+1} não está marcada como SIM na Relação Bancária.")
        validated.append(lot)
        expected_from=add_days(lot["to"],1)
    if expected_from != add_days(cutoff,1):
        raise ValueError("Os lotes devem cobrir todo o período até o próximo pagamento normal.")
    priced=price_lots(validated,unit_value_cents,uses_per_day,schedule,schedule_start)
    if schedule and any(lot["days"]==0 for lot in priced):
        raise ValueError("A escala não tem dias trabalhados em um dos períodos. Revise a divisão dos lotes.")
    return {"cutoff_date":cutoff,"lots":priced,"total_cents":sum(lot["amount_cents"] for lot in priced)}
