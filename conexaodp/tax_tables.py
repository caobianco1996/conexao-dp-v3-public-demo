"""Versioned, offline INSS/IRRF tables and decimal calculation services."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


INSS_2026 = {
    "schema": "inss-progressive-v1",
    "ceiling": "8475.55",
    "bands": [
        {"from": "0.00", "to": "1621.00", "rate": "7.5"},
        {"from": "1621.01", "to": "2902.84", "rate": "9"},
        {"from": "2902.85", "to": "4354.27", "rate": "12"},
        {"from": "4354.28", "to": "8475.55", "rate": "14"},
    ],
    "rounding": [{"stage": "FINAL_TOTAL", "scale": 2, "mode": "HALF_UP"}],
    "supportedModes": ["MONTHLY", "THIRTEENTH"],
}
IRRF_2026 = {
    "schema": "irrf-pipeline-v1",
    "dependentDeduction": "189.59",
    "simplifiedDiscount": "607.20",
    "bands": [
        {"from": "0.00", "to": "2428.80", "rate": "0", "deduction": "0.00"},
        {"from": "2428.81", "to": "2826.65", "rate": "7.5", "deduction": "182.16"},
        {"from": "2826.66", "to": "3751.05", "rate": "15", "deduction": "394.16"},
        {"from": "3751.06", "to": "4664.68", "rate": "22.5", "deduction": "675.49"},
        {"from": "4664.69", "to": None, "rate": "27.5", "deduction": "908.73"},
    ],
    "reductions": [
        {"from": "0.00", "to": "5000.00", "kind": "ZERO_TAX", "maximum": "312.89", "eligibleIncomeTypes": ["SALARY_MONTHLY"], "order": 1},
        {"from": "5000.01", "to": "7350.00", "kind": "LINEAR", "constant": "978.62", "coefficient": "0.133145", "eligibleIncomeTypes": ["SALARY_MONTHLY"], "order": 2},
    ],
    "rounding": [
        {"stage": "PRE_REDUCTION_TAX", "scale": 2, "mode": "HALF_UP"},
        {"stage": "FINAL_TAX", "scale": 2, "mode": "HALF_UP"},
    ],
    "supportedModes": ["MONTHLY"],
}

INSS_SOURCE_URL = "https://www.gov.br/inss/pt-br/direitos-e-deveres/inscricao-e-contribuicao/tabela-de-contribuicao-mensal"
IRRF_SOURCE_URL = "https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda/tabelas/2026"
SOURCE_TITLES = {
    "INSS": ("Tabela de contribuição mensal — empregado, doméstico e avulso", "Instituto Nacional do Seguro Social", INSS_SOURCE_URL),
    "IRRF": ("Tributação de 2026 — incidência mensal e redução", "Receita Federal do Brasil", IRRF_SOURCE_URL),
}
PARSER_VERSION = "manual-v1"


def _decimal(value, label="Valor") -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"Informe {label.lower()} numérico válido.") from error
    if not number.is_finite():
        raise ValueError(f"Informe {label.lower()} numérico válido.")
    return number


def _money(value, scale=2) -> str:
    quantum = Decimal(1).scaleb(-int(scale))
    return format(_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _normalized_parameters(parameters) -> str:
    return json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parameter_hash(parameters) -> str:
    return hashlib.sha256(_normalized_parameters(parameters).encode("utf-8")).hexdigest()


def simulation_snapshot_signature(csrf_token, inputs, versions):
    """Bind an explicitly displayed simulation to its inputs and version IDs."""
    payload = json.dumps({"inputs": inputs, "versions": versions}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hmac.new(str(csrf_token).encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_simulation_snapshot(csrf_token, inputs, versions, signature):
    expected = simulation_snapshot_signature(csrf_token, inputs, versions)
    return hmac.compare_digest(expected, str(signature or ""))


def legacy_2026_matches(kind, parameters):
    """Recognize the preserved V2 2026 seed without rewriting its JSON snapshot."""
    if not isinstance(parameters, dict):
        return False
    if kind == "INSS":
        return _normalized_parameters(parameters) == _normalized_parameters(INSS_2026)
    if kind != "IRRF":
        return False
    expected = IRRF_2026
    for key in ("schema", "dependentDeduction", "simplifiedDiscount", "bands"):
        if parameters.get(key) != expected.get(key):
            return False
    if parameters.get("supportedModes") != ["MONTHLY", "THIRTEENTH"]:
        return False
    actual_rules = parameters.get("reductions")
    expected_rules = expected.get("reductions")
    if not isinstance(actual_rules, list) or len(actual_rules) != len(expected_rules):
        return False
    fields = ("from", "to", "kind", "maximum", "constant", "coefficient", "order")
    if [{key: item.get(key) for key in fields if key in item} for item in actual_rules] != [
        {key: item.get(key) for key in fields if key in item} for item in expected_rules
    ]:
        return False
    rounding = parameters.get("rounding")
    return isinstance(rounding, list) and any(
        isinstance(rule, dict) and rule.get("stage") == "FINAL_TAX" and
        rule.get("scale") == 2 and rule.get("mode") == "HALF_UP" for rule in rounding)


def parameters_for_version(row):
    """Return the calculation projection for a row while retaining source JSON."""
    try:
        parameters = json.loads(row["parameters_json"])
    except (TypeError, ValueError, KeyError):
        raise ValueError("Os parâmetros desta versão não podem ser lidos.")
    if row["parser_version"] == "legacy-v2-preserved-v3-compatible" and row["kind"] == "IRRF":
        if not legacy_2026_matches("IRRF", parameters) or row["competence"] != "2026-01":
            raise ValueError("A versão legada mudou desde a conferência. Reabra a candidata para validar os parâmetros.")
        projected = dict(parameters)
        projected["reductions"] = [dict(rule, eligibleIncomeTypes=["SALARY_MONTHLY"])
                                    for rule in parameters["reductions"]]
        projected["rounding"] = list(IRRF_2026["rounding"])
        projected["supportedModes"] = ["MONTHLY"]
        return projected
    return parameters


def _check_date(value, label):
    try:
        parsed = date.fromisoformat(str(value or ""))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Informe {label} válida.") from error
    if len(str(value)) != 10:
        raise ValueError(f"Informe {label} válida.")
    return parsed


def _money_cent(value):
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _first_rounding(parameters, stage, fallback=2):
    for rule in parameters.get("rounding", []):
        if rule.get("stage") == stage:
            if rule.get("mode") != "HALF_UP" or int(rule.get("scale", -1)) != 2:
                raise ValueError("Esta simulação suporta arredondamento monetário HALF_UP em duas casas; revise a regra da tabela.")
            return 2
    if fallback is None:
        raise ValueError(f"A tabela não define o arredondamento em {stage}.")
    return fallback


def _validate_bands(kind, bands):
    if not isinstance(bands, list) or not bands or len(bands) > 100:
        raise ValueError("Informe todas as faixas da tabela.")
    previous_upper = None
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            raise ValueError("Cada faixa deve ser um objeto.")
        try:
            lower = _decimal(band["from"], "limite inicial")
            upper = None if band.get("to") is None else _decimal(band["to"], "limite final")
            rate = _decimal(band["rate"], "alíquota")
            if lower < 0 or lower != _money_cent(lower) or rate < 0 or rate > 100:
                raise ValueError
            if upper is not None and (upper < lower or upper != _money_cent(upper)):
                raise ValueError
            if index == 0 and lower != Decimal("0.00"):
                raise ValueError
            if previous_upper is None and index:
                raise ValueError
            if previous_upper is not None and lower != previous_upper + Decimal("0.01"):
                raise ValueError("As faixas devem ser contínuas, sem lacunas nem sobreposição.")
            if upper is None and index != len(bands) - 1:
                raise ValueError
            if kind == "IRRF":
                deduction = _decimal(band["deduction"], "parcela a deduzir")
                if deduction < 0:
                    raise ValueError
            previous_upper = upper
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            if isinstance(error, ValueError) and str(error):
                if "contínuas" in str(error):
                    raise
            raise ValueError("Cada faixa precisa ter limites em centavos, ordem crescente e alíquota válida.") from error
    return True


def validation_errors(kind, valid_from, category, income_type, source_url, parameters):
    errors = []
    if kind not in ("INSS", "IRRF"):
        errors.append("Selecione INSS ou IRRF.")
    if category != "EMPLOYEE":
        errors.append("Esta versão do motor aceita apenas a categoria empregado, doméstico ou avulso.")
    try:
        _check_date(valid_from, "vigência inicial")
    except ValueError as error:
        errors.append(str(error))
    if kind == "INSS" and income_type not in ("EMPLOYEE_MONTHLY", "EMPLOYEE_13TH"):
        errors.append("Escolha a natureza previdenciária mensal ou 13º.")
    if kind == "IRRF" and income_type != "SALARY_MONTHLY":
        errors.append("O conjunto oficial conferido nesta versão cobre salário mensal.")
    if not re.fullmatch(r"https://[^\s]+", str(source_url or "")):
        errors.append("Informe uma URL HTTPS da fonte da candidata.")
    if not isinstance(parameters, dict):
        errors.append("Informe os parâmetros da tabela em JSON.")
        return errors
    expected_schema = "inss-progressive-v1" if kind == "INSS" else "irrf-pipeline-v1"
    if parameters.get("schema") != expected_schema:
        errors.append(f"A tabela precisa usar o esquema {expected_schema}.")
    try:
        _validate_bands(kind, parameters.get("bands"))
    except ValueError as error:
        errors.append(str(error))
    try:
        modes = parameters.get("supportedModes")
        expected_modes = {"MONTHLY", "THIRTEENTH"} if kind == "INSS" else {"MONTHLY"}
        if not isinstance(modes, list) or not modes or any(mode not in expected_modes for mode in modes):
            raise ValueError
        if kind == "INSS" and not {"MONTHLY", "THIRTEENTH"}.issubset(set(modes)):
            raise ValueError
    except (TypeError, ValueError):
        errors.append("A tabela precisa declarar os modos de cálculo que suporta.")
    if kind == "INSS":
        try:
            ceiling = _decimal(parameters["ceiling"], "teto")
            if ceiling <= 0 or ceiling != _money_cent(ceiling) or Decimal(str(parameters["bands"][-1]["to"])) != ceiling:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
            errors.append("INSS exige teto em centavos igual ao limite final da última faixa.")
        if not isinstance(parameters.get("rounding"), list) or not any(rule.get("stage") == "FINAL_TOTAL" and rule.get("mode") == "HALF_UP" and rule.get("scale") == 2 for rule in parameters["rounding"] if isinstance(rule, dict)):
            errors.append("INSS exige regra de arredondamento do total final.")
    elif kind == "IRRF":
        try:
            if _decimal(parameters["dependentDeduction"]) < 0 or _decimal(parameters["simplifiedDiscount"]) < 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, InvalidOperation):
            errors.append("IRRF exige dedução por dependente e desconto simplificado não negativos.")
        reductions = parameters.get("reductions")
        needs_2026_reduction = str(valid_from or "").startswith("2026-")
        if not isinstance(reductions, list) or (needs_2026_reduction and not reductions):
            errors.append("IRRF exige o conjunto completo de reduções aplicável à vigência.")
        else:
            parsed_ranges = []
            for reduction in reductions:
                try:
                    if not isinstance(reduction, dict) or reduction.get("kind") not in ("ZERO_TAX", "LINEAR"):
                        raise ValueError
                    lower, upper = _decimal(reduction["from"]), _decimal(reduction["to"])
                    if lower < 0 or upper < lower:
                        raise ValueError
                    eligible = reduction.get("eligibleIncomeTypes")
                    if not isinstance(eligible, list) or not eligible or any(value != "SALARY_MONTHLY" for value in eligible):
                        raise ValueError
                    parsed_ranges.append((lower, upper))
                    if reduction["kind"] == "ZERO_TAX":
                        if _decimal(reduction["maximum"]) < 0:
                            raise ValueError
                    else:
                        if _decimal(reduction["constant"]) < 0 or _decimal(reduction["coefficient"]) < 0:
                            raise ValueError
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    errors.append("Revise limites, fórmula, elegibilidade e teto das reduções de IRRF.")
                    break
            parsed_ranges.sort()
            if any(parsed_ranges[index][0] <= parsed_ranges[index - 1][1] for index in range(1, len(parsed_ranges))):
                errors.append("As faixas de redução do IRRF não podem se sobrepor.")
        irrf_rounding = parameters.get("rounding")
        if not isinstance(irrf_rounding, list):
            irrf_rounding = []
            errors.append("IRRF exige regras de arredondamento explícitas.")
        stages = {rule.get("stage") for rule in irrf_rounding if isinstance(rule, dict) and rule.get("mode") == "HALF_UP" and rule.get("scale") == 2}
        if not {"PRE_REDUCTION_TAX", "FINAL_TAX"}.issubset(stages):
            errors.append("IRRF exige arredondamento do imposto preliminar e do resultado final.")
        bands = parameters.get("bands")
        if isinstance(bands, list) and bands and isinstance(bands[-1], dict) and bands[-1].get("to") is not None:
            errors.append("A última faixa do IRRF deve indicar limite superior sem teto.")
    return errors


def validate(kind: str, competence: str, source: str, parameters_json: str, *,
             valid_from: str | None = None, category="EMPLOYEE", income_type=None,
             source_url="https://example.invalid/source"):
    """Parse and structurally validate a candidate without publishing it."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(competence or "")):
        raise ValueError("Informe uma referência válida no formato AAAA-MM.")
    if not 3 <= len(str(source or "").strip()) <= 500:
        raise ValueError("Informe título ou ato normativo da fonte.")
    try:
        parameters = json.loads(parameters_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("O JSON de parâmetros é inválido.") from error
    effective = valid_from or f"{competence}-01"
    nature = income_type or ("EMPLOYEE_MONTHLY" if kind == "INSS" else "SALARY_MONTHLY")
    errors = validation_errors(kind, effective, category, nature, source_url, parameters)
    if errors:
        raise ValueError(errors[0])
    return parameters


def resolve(db, kind: str, reference: str, category="EMPLOYEE", income_type=None):
    """Resolve only a published local snapshot valid for the requested date/month."""
    if kind not in ("INSS", "IRRF"):
        raise ValueError("Selecione INSS ou IRRF.")
    if category != "EMPLOYEE":
        return None
    if kind == "INSS":
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(reference or "")):
            raise ValueError("Informe uma competência INSS válida (AAAA-MM).")
        target = f"{reference}-01"
        requested = income_type or "EMPLOYEE_MONTHLY"
        if requested == "EMPLOYEE_13TH":
            allowed_income = ("EMPLOYEE_13TH", "SALARY_AND_13TH", "SALARY_MONTHLY")
        elif requested == "EMPLOYEE_MONTHLY":
            allowed_income = ("EMPLOYEE_MONTHLY", "MONTHLY", "SALARY_MONTHLY", "SALARY_AND_13TH")
        else:
            allowed_income = (requested,)
    else:
        _check_date(reference, "data efetiva de pagamento")
        target = str(reference)
        allowed_income = (income_type or "SALARY_MONTHLY", "SALARY_AND_13TH")
    placeholders = ",".join("?" for _ in allowed_income)
    rows = db.execute(f"""SELECT * FROM local_tax_versions WHERE kind=? AND category=?
        AND income_type IN ({placeholders}) AND status IN ('PUBLISHED','SUPERSEDED')
        AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)
        AND (effective_to IS NULL OR substr(effective_to,1,7)>=substr(?,1,7))
        AND published_at IS NOT NULL
        ORDER BY valid_from DESC,version DESC,id DESC""",
        (kind, category, *allowed_income, target, target, target)).fetchall()
    return rows[0] if rows else None


def calculate_inss(parameters, remuneration, other_remuneration=0, contribution_already_withheld=0):
    base = _decimal(remuneration, "remuneração do vínculo")
    others = _decimal(other_remuneration, "remuneração dos outros vínculos")
    withheld = _decimal(contribution_already_withheld, "INSS já retido")
    if min(base, others, withheld) < 0:
        raise ValueError("Remunerações e contribuição já retida não podem ser negativas.")
    ceiling = _decimal(parameters["ceiling"], "teto previdenciário")
    combined = base + others
    assessed = min(combined, ceiling)
    total = Decimal("0")
    previous = Decimal("0")
    details = []
    for band in parameters["bands"]:
        lower = _decimal(band["from"])
        upper = _decimal(band["to"])
        rate = _decimal(band["rate"]) / Decimal("100")
        # Displayed lower bounds are inclusive in cents. Marginal progressivity
        # starts immediately after the prior band's upper bound.
        segment_start = previous
        segment_end = min(assessed, upper)
        taxable = max(Decimal("0"), segment_end - segment_start)
        contribution = taxable * rate
        if taxable:
            details.append({"from": format(segment_start, ".2f"), "to": format(segment_end, ".2f"),
                "taxable": format(taxable, ".2f"), "rate": format(_decimal(band["rate"]), "f"),
                "contributionUnrounded": format(contribution, ".6f")})
        total += contribution
        previous = upper
        if assessed <= upper:
            break
    total = _money_cent(total) if _first_rounding(parameters, "FINAL_TOTAL", None) == 2 else total
    due = max(Decimal("0"), total - withheld)
    attributable = total if others == 0 and withheld == 0 else None
    return {
        "base": _money(base), "otherBases": _money(others), "combinedBase": _money(combined),
        "assessedBase": _money(assessed), "ceiling": _money(ceiling),
        "totalContributionTheoretical": _money(total), "grossContribution": _money(total),
        "contributionsAlreadyInformed": _money(withheld), "contributionAlreadyWithheld": _money(withheld),
        "estimatedContributionForThisLink": _money(attributable) if attributable is not None else None,
        "contributionDue": _money(due), "value": _money(due), "bands": details,
        "warnings": (["Outros vínculos foram informados como total agregado; a parcela exata por fonte pagadora depende da ordem e dos comprovantes."] if others or withheld else []),
        "memory": [
            f"Categoria: empregado, doméstico ou trabalhador avulso · base deste vínculo: R$ {_money(base)}.",
            f"Bases de outros vínculos na mesma competência: R$ {_money(others)} · base conjunta: R$ {_money(combined)}.",
            *[f"Faixa de R$ {item['from']} a R$ {item['to']}: R$ {item['taxable']} × {item['rate']}% = R$ {item['contributionUnrounded']} antes do arredondamento final." for item in details],
            f"Base aplicada após teto de R$ {_money(ceiling)}: R$ {_money(assessed)}.",
            f"Contribuição progressiva total: R$ {_money(total)} · já retida em outros vínculos: R$ {_money(withheld)} · saldo teórico: R$ {_money(due)}.",
            "13º salário, quando selecionado, é apurado separadamente da remuneração mensal.",
        ],
    }


def _table_rate(parameters, base):
    for band in parameters["bands"]:
        upper = None if band.get("to") is None else _decimal(band["to"])
        if upper is None or base <= upper:
            return _decimal(band["rate"]), _decimal(band.get("deduction", "0")), band
    raise ValueError("A base excede as faixas cadastradas; revise a versão publicada.")


def calculate_irrf(parameters, remuneration, inss=0, dependents=0, alimony=0,
                   other_legal_deductions=0, deduction_method="AUTO", income_type="SALARY_MONTHLY"):
    gross = _decimal(remuneration, "rendimento tributável")
    previd = _decimal(inss, "INSS dedutível")
    pension = _decimal(alimony, "pensão alimentícia")
    other = _decimal(other_legal_deductions, "outras deduções legais")
    try:
        dependent_count = int(dependents)
    except (TypeError, ValueError) as error:
        raise ValueError("Informe uma quantidade inteira de dependentes.") from error
    if gross < 0 or min(previd, pension, other) < 0 or dependent_count < 0 or str(dependents).strip() not in (str(dependent_count), f"+{dependent_count}"):
        raise ValueError("Rendimentos e deduções não podem ser negativos; dependentes deve ser inteiro.")
    if income_type not in ("SALARY_MONTHLY",):
        raise ValueError("O IRRF para esse tipo de rendimento ainda não está coberto por uma versão publicada validada.")
    method = str(deduction_method or "AUTO")
    if method not in ("AUTO", "LEGAL", "SIMPLIFIED"):
        raise ValueError("Selecione como comparar as deduções do IRRF.")
    dependent_value = _decimal(parameters["dependentDeduction"])
    legal_parts = {"INSS dedutível": previd, "Dependentes": dependent_value * dependent_count,
                   "Pensão informada": pension, "Outras deduções legais informadas": other}
    legal_total = sum(legal_parts.values(), Decimal("0"))
    simplified = _decimal(parameters["simplifiedDiscount"])
    legal_base = max(Decimal("0"), gross - legal_total)
    simplified_base = max(Decimal("0"), gross - simplified)
    selected = method
    if method == "AUTO":
        # A tie deterministically uses legal deductions; both routes produce the same base.
        selected = "LEGAL" if legal_total >= simplified else "SIMPLIFIED"
    deduction = legal_total if selected == "LEGAL" else simplified
    base = legal_base if selected == "LEGAL" else simplified_base
    rate_pct, fixed, band = _table_rate(parameters, base)
    preliminary_raw = max(Decimal("0"), base * rate_pct / Decimal("100") - fixed)
    preliminary = _money_cent(preliminary_raw) if _first_rounding(parameters, "PRE_REDUCTION_TAX", None) == 2 else preliminary_raw
    reduction = Decimal("0")
    selected_reduction = None
    for rule in sorted(parameters.get("reductions", []), key=lambda item: int(item.get("order", 999))):
        if income_type not in rule.get("eligibleIncomeTypes", [income_type]):
            continue
        lower, upper = _decimal(rule["from"]), _decimal(rule["to"])
        if lower <= gross <= upper:
            if rule["kind"] == "ZERO_TAX":
                reduction = min(preliminary, _decimal(rule["maximum"]))
            elif rule["kind"] == "LINEAR":
                reduction = max(Decimal("0"), _decimal(rule["constant"]) - _decimal(rule["coefficient"]) * gross)
            selected_reduction = rule
            break
    reduction = min(preliminary, reduction)
    final = max(Decimal("0"), preliminary - reduction)
    final = _money_cent(final) if _first_rounding(parameters, "FINAL_TAX", None) == 2 else final
    return {
        "grossTaxableIncome": _money(gross), "inssDeductible": _money(previd),
        "legalDeductions": _money(legal_total), "simplifiedDeduction": _money(simplified),
        "legalBase": _money(legal_base), "simplifiedBase": _money(simplified_base),
        "deductionMethod": selected, "deductionTotal": _money(deduction), "base": _money(base),
        "rate": format(rate_pct, "f"), "fixedDeduction": _money(fixed),
        "preliminaryTax": _money(preliminary), "reduction": _money(reduction),
        "finalTax": _money(final), "value": _money(final), "band": dict(band),
        "reductionRule": dict(selected_reduction) if selected_reduction else None,
        "memory": [
            f"Rendimento tributável usado para a regra de redução: R$ {_money(gross)}.",
            f"Deduções legais informadas: INSS R$ {_money(previd)}, dependentes R$ {_money(legal_parts['Dependentes'])}, pensão R$ {_money(pension)}, outras R$ {_money(other)}; total R$ {_money(legal_total)}.",
            f"Desconto simplificado da versão: R$ {_money(simplified)}.",
            f"Base legal: R$ {_money(legal_base)} · base com simplificado: R$ {_money(simplified_base)} · método selecionado: {'Deduções legais' if selected == 'LEGAL' else 'Desconto simplificado'}.",
            f"Base IRRF: R$ {_money(base)} · faixa até {'sem limite superior' if band.get('to') is None else 'R$ '+_money(band['to'])} · alíquota {format(rate_pct, 'f')}% · parcela a deduzir R$ {_money(fixed)}.",
            f"IRRF antes da redução: R$ {_money(preliminary)}.",
            (f"Redução aplicada sobre o rendimento tributável (não sobre a base após deduções): R$ {_money(reduction)}." if selected_reduction else "Sem redução aplicável para este rendimento e referência."),
            f"IRRF final estimado: R$ {_money(final)}.",
        ],
    }


def official_2026_examples():
    """Deterministic examples published by RFB for monthly salary withholding."""
    cases = []
    for gross, inss, expected in (("4000.00", "373.41", "0.00"), ("5000.00", "509.60", "0.00"), ("6000.00", "649.60", "382.88")):
        result = calculate_irrf(IRRF_2026, gross, inss=inss, deduction_method="AUTO")
        cases.append({"gross": gross, "inss": inss, "expected": expected, "actual": result["value"], "ok": result["value"] == expected})
    return cases


def create_candidate(db, actor_id, *, kind, valid_from, valid_to, category, income_type,
                     source_title, source_agency, source_url, source_accessed_at,
                     justification, parameters):
    if kind not in ("INSS", "IRRF"):
        raise ValueError("Selecione INSS ou IRRF.")
    start = _check_date(valid_from, "vigência inicial")
    if valid_to:
        end = _check_date(valid_to, "vigência final")
        if end < start:
            raise ValueError("A vigência final não pode ser anterior à inicial.")
    if len(str(justification or "").strip()) < 8:
        raise ValueError("Explique o motivo desta candidata.")
    if len(str(source_title or "").strip()) < 4 or len(str(source_agency or "").strip()) < 3:
        raise ValueError("Informe o título e o órgão da fonte.")
    _check_date(source_accessed_at, "data de conferência da fonte")
    errors = validation_errors(kind, start.isoformat(), category, income_type, source_url, parameters)
    if not isinstance(parameters, dict):
        raise ValueError("Informe parâmetros JSON válidos.")
    canonical = _normalized_parameters(parameters)
    competence = start.strftime("%Y-%m")
    version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM local_tax_versions WHERE kind=? AND competence=? AND category=? AND income_type=?",
        (kind, competence, category, income_type)).fetchone()[0]
    previous = db.execute("""SELECT id FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND status IN ('PUBLISHED','SUPERSEDED') AND valid_from<=? AND published_at IS NOT NULL
        ORDER BY valid_from DESC,version DESC LIMIT 1""", (kind, category, income_type, start.isoformat())).fetchone()
    source = str(source_title).strip()[:500]
    cur = db.execute("""INSERT INTO local_tax_versions(kind,competence,version,status,workflow_state,category,income_type,
        valid_from,valid_to,source,source_title,source_agency,source_url,source_accessed_at,content_hash,
        parser_version,justification,validation_json,parameters_json,created_by,updated_by,previous_version_id)
        VALUES(?,?,?,'DRAFT','COLLECTED',?,?,?,?,?,?,?,?,?,?,?,?,'{}',?,?,?,?)""",
        (kind, competence, version, category, income_type, start.isoformat(), end.isoformat() if valid_to else None,
         source, str(source_title).strip()[:500], str(source_agency).strip()[:250], str(source_url).strip()[:1000],
         str(source_accessed_at or "").strip()[:50], parameter_hash(parameters), PARSER_VERSION,
         str(justification).strip()[:2000], canonical, actor_id, actor_id, previous["id"] if previous else None))
    version_id = cur.lastrowid
    report = {"state": "COLLECTED", "errors": errors, "checks": [], "parametersHash": parameter_hash(parameters)}
    db.execute("UPDATE local_tax_versions SET validation_json=? WHERE id=?", (json.dumps(report, ensure_ascii=False), version_id))
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,after_json) VALUES(?,?,'CREATED',?,?)",
        (version_id, actor_id, "Candidata coletada; ainda não validada, aprovada ou publicada.",
         json.dumps({"kind": kind, "valid_from": start.isoformat(), "source_url": source_url, "content_hash": report["parametersHash"]}, ensure_ascii=False)))
    return version_id


def edit_candidate(db, actor_id, version_id, *, valid_from, valid_to, category,
                   income_type, source_title, source_agency, source_url,
                   source_accessed_at, justification, parameters):
    row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(version_id),)).fetchone()
    if not row or row["status"] != "DRAFT" or row["workflow_state"] not in ("COLLECTED", "INVALID_CONTENT", "VALIDATED"):
        raise ValueError("Somente uma candidata ainda não aprovada pode ser editada.")
    start = _check_date(valid_from, "vigência inicial")
    if valid_to:
        end = _check_date(valid_to, "vigência final")
        if end < start:
            raise ValueError("A vigência final não pode ser anterior à inicial.")
    if len(str(justification or "").strip()) < 8:
        raise ValueError("Explique o motivo e as diferenças desta candidata.")
    if not isinstance(parameters, dict):
        raise ValueError("Informe parâmetros JSON válidos.")
    if len(str(source_title or "").strip()) < 4 or len(str(source_agency or "").strip()) < 3:
        raise ValueError("Informe o título e o órgão da fonte.")
    _check_date(source_accessed_at, "data de conferência da fonte")
    competence = start.strftime("%Y-%m")
    version = row["version"] if competence == row["competence"] else db.execute(
        "SELECT COALESCE(MAX(version),0)+1 FROM local_tax_versions WHERE kind=? AND competence=? AND category=? AND income_type=?",
        (row["kind"], competence, category, income_type)).fetchone()[0]
    previous = db.execute("""SELECT id FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND status IN ('PUBLISHED','SUPERSEDED') AND valid_from<=? AND published_at IS NOT NULL
        ORDER BY valid_from DESC,version DESC LIMIT 1""", (row["kind"], category, income_type, start.isoformat())).fetchone()
    before = {key: row[key] for key in ("competence", "version", "valid_from", "valid_to", "source_title", "source_url", "content_hash", "parameters_json")}
    hash_value = parameter_hash(parameters)
    source = str(source_title).strip()[:500]
    db.execute("""UPDATE local_tax_versions SET competence=?,version=?,category=?,income_type=?,valid_from=?,valid_to=?,
        source=?,source_title=?,source_agency=?,source_url=?,source_accessed_at=?,content_hash=?,parser_version=?,
        justification=?,parameters_json=?,validation_json='{}',workflow_state='COLLECTED',updated_by=?,updated_at=CURRENT_TIMESTAMP,
        previous_version_id=? WHERE id=?""",
        (competence, version, category, income_type, start.isoformat(), end.isoformat() if valid_to else None,
         source, str(source_title).strip()[:500], str(source_agency).strip()[:250], str(source_url).strip()[:1000],
         str(source_accessed_at or "").strip()[:50], hash_value, PARSER_VERSION, str(justification).strip()[:2000],
         _normalized_parameters(parameters), actor_id, previous["id"] if previous else None, row["id"]))
    after = {"competence": competence, "version": version, "valid_from": start.isoformat(),
             "valid_to": end.isoformat() if valid_to else None, "source_title": source,
             "source_url": source_url, "content_hash": hash_value, "parameters": parameters}
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'EDITED',?,?,?)",
        (row["id"], actor_id, "Candidata atualizada. A validação anterior foi removida e precisa ser refeita.",
         json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
    return row["id"]


def validate_candidate(db, actor_id, version_id):
    row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(version_id),)).fetchone()
    if not row:
        raise ValueError("Candidata não encontrada.")
    if row["status"] != "DRAFT" or row["workflow_state"] not in ("COLLECTED", "INVALID_CONTENT", "VALIDATED"):
        raise ValueError("Somente candidatas podem ser validadas.")
    try:
        parameters = json.loads(row["parameters_json"])
    except (TypeError, ValueError):
        parameters = None
    errors = validation_errors(row["kind"], row["valid_from"], row["category"], row["income_type"], row["source_url"], parameters)
    if row["valid_to"] and row["valid_to"] < row["valid_from"]:
        errors.append("A vigência final é anterior à inicial.")
    if row["previous_version_id"]:
        previous = db.execute("SELECT status FROM local_tax_versions WHERE id=?", (row["previous_version_id"],)).fetchone()
        if not previous or previous["status"] not in ("PUBLISHED", "SUPERSEDED"):
            errors.append("A versão anterior mudou durante a preparação. Reabra a comparação antes de validar.")
    if row["kind"] == "IRRF" and row["valid_from"].startswith("2026-") and parameters:
        for case in official_2026_examples():
            got = calculate_irrf(parameters, case["gross"], inss=case["inss"])["value"]
            case["actual"] = got
            case["ok"] = got == case["expected"]
            if not case["ok"]:
                errors.append(f"O gabarito oficial para rendimento R$ {case['gross']} diverge ({got} ≠ {case['expected']}).")
    checks = [{"name": "Estrutura das faixas", "ok": not any("faixa" in item.lower() or "intervalo" in item.lower() for item in errors)},
              {"name": "Fonte e vigência", "ok": bool(row["source_url"] and row["valid_from"])},
              {"name": "Parâmetros tributários obrigatórios", "ok": not errors}]
    if row["kind"] == "IRRF" and row["valid_from"].startswith("2026-") and parameters:
        checks.append({"name": "Exemplos oficiais de R$ 4.000, R$ 5.000 e R$ 6.000", "ok": all(item["ok"] for item in official_2026_examples())})
    state = "INVALID_CONTENT" if errors else "VALIDATED"
    report = {"state": state, "errors": errors, "checks": checks, "parametersHash": row["content_hash"],
              "validatedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
    db.execute("UPDATE local_tax_versions SET workflow_state=?,validation_json=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (state, json.dumps(report, ensure_ascii=False), actor_id, row["id"]))
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json,outcome) VALUES(?,?,?,?,?,?,?)",
        (row["id"], actor_id, "VALIDATE", "Candidata validada sem aprovação/publicação." if not errors else "Validação reprovada: " + " ".join(errors),
         json.dumps({"state": row["workflow_state"]}, ensure_ascii=False), json.dumps(report, ensure_ascii=False), "SUCCESS" if not errors else "INVALID"))
    return report


def approve_candidate(db, actor_id, version_id):
    row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(version_id),)).fetchone()
    if not row or row["status"] != "DRAFT" or row["workflow_state"] != "VALIDATED":
        raise ValueError("A candidata precisa estar validada para ser aprovada.")
    before = {"status": row["status"], "workflow_state": row["workflow_state"]}
    db.execute("UPDATE local_tax_versions SET status='APPROVED',workflow_state='APPROVED',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (actor_id, actor_id, row["id"]))
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'APPROVED','Versão aprovada; ainda não disponível para cálculo oficial.',?,?)",
        (row["id"], actor_id, json.dumps(before), json.dumps({"status": "APPROVED", "workflow_state": "APPROVED", "approved_by": actor_id})))


def publish_candidate(db, actor_id, version_id):
    row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(version_id),)).fetchone()
    if not row or row["status"] != "APPROVED" or row["workflow_state"] != "APPROVED":
        raise ValueError("A versão precisa estar aprovada antes da publicação.")
    try:
        parameters = json.loads(row["parameters_json"])
    except (TypeError, ValueError) as error:
        raise ValueError("Parâmetros inválidos; não é possível publicar.") from error
    errors = validation_errors(row["kind"], row["valid_from"], row["category"], row["income_type"], row["source_url"], parameters)
    if errors:
        raise ValueError("A versão aprovada não passou novamente pela validação. " + errors[0])
    current_same = db.execute("""SELECT id,version FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND valid_from=? AND status='PUBLISHED' ORDER BY version DESC LIMIT 1""",
        (row["kind"], row["category"], row["income_type"], row["valid_from"])).fetchone()
    if row["previous_version_id"]:
        previous = db.execute("SELECT id FROM local_tax_versions WHERE id=? AND status IN ('PUBLISHED','SUPERSEDED')", (row["previous_version_id"],)).fetchone()
        if not previous:
            raise ValueError("Outra revisão mudou enquanto você analisava. Reabra a comparação.")
        if current_same and current_same["id"] != row["previous_version_id"]:
            raise ValueError("Outra revisão foi publicada enquanto você analisava esta candidata. Reabra a comparação.")
    elif current_same:
        raise ValueError("Já existe revisão publicada nesta vigência. Reabra a comparação.")
    start = row["valid_from"]
    next_version = db.execute("""SELECT MIN(valid_from) FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND status='PUBLISHED' AND valid_from>?""", (row["kind"], row["category"], row["income_type"], start)).fetchone()[0]
    end = row["valid_to"]
    if next_version:
        previous_day = (date.fromisoformat(next_version) - timedelta(days=1)).isoformat()
        if end is None or end >= next_version:
            end = previous_day
    # Close older open validity windows and retain their snapshots and audit trail.
    preceding = db.execute("""SELECT * FROM local_tax_versions WHERE kind=? AND category=? AND income_type=?
        AND status='PUBLISHED' AND valid_from<? AND (valid_to IS NULL OR valid_to>=?)""",
        (row["kind"], row["category"], row["income_type"], start, start)).fetchall()
    for old in preceding:
        old_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        before = {"status": old["status"], "valid_to": old["valid_to"]}
        db.execute("UPDATE local_tax_versions SET status='SUPERSEDED',workflow_state='SUPERSEDED',valid_to=?,effective_to=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (old_end, old_end[:7], actor_id, old["id"]))
        db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'SUPERSEDED','Vigência encerrada pela nova publicação; versão e cálculos anteriores permanecem acessíveis.',?,?)",
            (old["id"], actor_id, json.dumps(before), json.dumps({"status": "SUPERSEDED", "valid_to": old_end})))
    # For a correction of the same start date, the new higher revision wins while
    # the replaced snapshot retains its original validity for historical lookup.
    if current_same:
        old = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (current_same["id"],)).fetchone()
        before = {"status": old["status"], "version": old["version"]}
        db.execute("UPDATE local_tax_versions SET status='SUPERSEDED',workflow_state='SUPERSEDED',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (actor_id, old["id"]))
        db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'SUPERSEDED','Revisão substituída por correção da mesma vigência.',?,?)",
            (old["id"], actor_id, json.dumps(before), json.dumps({"status": "SUPERSEDED", "superseded_by": row["id"]})))
    db.execute("""UPDATE local_tax_versions SET status='PUBLISHED',workflow_state='PUBLISHED',valid_to=?,
        published_by=?,published_at=CURRENT_TIMESTAMP,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='APPROVED'""",
        (end, actor_id, actor_id, row["id"]))
    if db.execute("SELECT changes()").fetchone()[0] != 1:
        raise ValueError("A publicação não foi concluída; atualize a revisão e tente novamente.")
    after = {"status": "PUBLISHED", "valid_from": start, "valid_to": end, "version": row["version"], "published_by": actor_id}
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'PUBLISHED',?,?,?)",
        (row["id"], actor_id, f"Publicada internamente para {start} a {end or 'vigência aberta'}.",
         json.dumps({"status": row["status"], "valid_from": row["valid_from"], "valid_to": row["valid_to"]}), json.dumps(after, ensure_ascii=False)))
    return after


def reject_candidate(db, actor_id, version_id, reason):
    reason = str(reason or "").strip()[:500]
    if len(reason) < 3:
        raise ValueError("Informe o motivo da rejeição.")
    row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (int(version_id),)).fetchone()
    if not row or row["status"] not in ("DRAFT", "APPROVED"):
        raise ValueError("Somente uma candidata ainda não publicada pode ser rejeitada.")
    before = {"status": row["status"], "workflow_state": row["workflow_state"]}
    db.execute("UPDATE local_tax_versions SET status='REJECTED',workflow_state='REJECTED',rejection_reason=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (reason, actor_id, row["id"]))
    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,before_json,after_json) VALUES(?,?,'REJECTED',?,?,?)",
        (row["id"], actor_id, reason, json.dumps(before), json.dumps({"status": "REJECTED", "reason": reason}, ensure_ascii=False)))


def simulate(db, inputs, pinned_versions=None):
    """Calculate an employee case using only published table rows.

    `pinned_versions` is used only when the user explicitly saves the just-seen
    result. It allows preserving a version selected before a later publication.
    Candidate parameters are never accepted from a browser request.
    """
    if not isinstance(inputs, dict):
        raise ValueError("Informe os parâmetros da simulação.")
    mode = str(inputs.get("mode") or "MONTHLY")
    if mode not in ("MONTHLY", "THIRTEENTH"):
        raise ValueError("Escolha remuneração mensal ou 13º salário.")
    do_inss = str(inputs.get("calculateInss") or "1") == "1"
    do_irrf = str(inputs.get("calculateIrrf") or ("0" if mode == "THIRTEENTH" else "1")) == "1"
    if not do_inss and not do_irrf:
        raise ValueError("Selecione INSS, IRRF ou ambos para calcular.")
    if mode == "THIRTEENTH" and do_irrf:
        raise ValueError("O IRRF de 13º exige uma tabela publicada específica; calcule o INSS separado ou desmarque o IRRF.")
    gross = _decimal(inputs.get("remuneration"), "remuneração tributável")
    if gross < 0:
        raise ValueError("A remuneração não pode ser negativa.")
    competence = str(inputs.get("competence") or "")
    payment_date = str(inputs.get("paymentDate") or "")
    if do_inss and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", competence):
        raise ValueError("Informe a competência previdenciária para o INSS.")
    if do_irrf:
        _check_date(payment_date, "data efetiva de pagamento")

    other_bases = Decimal("0")
    other_withheld = _decimal(inputs.get("contributionAlreadyWithheld", "0") or "0", "INSS retido em outros vínculos")
    links = []
    for index in range(1, 4):
        row_competence = str(inputs.get(f"other_{index}_competence") or "").strip()
        base_raw = str(inputs.get(f"other_{index}_base") or "").strip()
        withheld_raw = str(inputs.get(f"other_{index}_inss") or "").strip()
        source = str(inputs.get(f"other_{index}_source") or "").strip()[:120]
        if not any((row_competence, base_raw, withheld_raw, source)):
            continue
        if row_competence != competence:
            raise ValueError("Não some outros vínculos de uma competência diferente do INSS informado.")
        base = _decimal(base_raw or "0", "base de outro vínculo")
        withheld = _decimal(withheld_raw or "0", "INSS retido em outro vínculo")
        if min(base, withheld) < 0:
            raise ValueError("Bases e contribuições de outros vínculos não podem ser negativas.")
        other_bases += base
        other_withheld += withheld
        links.append({"competence": row_competence, "base": _money(base), "withheld": _money(withheld), "source": source})

    versions = {}
    results = {"inss": None, "irrf": None}
    errors = {}

    def select_version(kind, reference, income_type):
        pinned_id = (pinned_versions or {}).get(kind.lower())
        if pinned_id:
            row = db.execute("SELECT * FROM local_tax_versions WHERE id=? AND kind=? AND category='EMPLOYEE' AND status IN ('PUBLISHED','SUPERSEDED') AND published_at IS NOT NULL",
                             (int(pinned_id), kind)).fetchone()
            if not row:
                raise ValueError(f"A versão de {kind} usada pelo resultado anterior não está mais disponível.")
            if row["income_type"] not in (income_type, "SALARY_MONTHLY", "SALARY_AND_13TH"):
                raise ValueError(f"A versão fixada de {kind} não corresponde ao tipo deste cálculo.")
            return row
        return resolve(db, kind, reference, "EMPLOYEE", income_type)

    inss_version = None
    if do_inss:
        inss_income = "EMPLOYEE_13TH" if mode == "THIRTEENTH" else "EMPLOYEE_MONTHLY"
        try:
            inss_version = select_version("INSS", competence, inss_income)
            if not inss_version:
                errors["inss"] = "Não há tabela INSS publicada para esta competência e categoria."
            else:
                parameters = parameters_for_version(inss_version)
                if mode not in parameters.get("supportedModes", []):
                    errors["inss"] = "A versão publicada não habilita essa apuração do INSS."
                else:
                    results["inss"] = calculate_inss(parameters, gross, other_bases, other_withheld)
                    results["inss"].update({"versionId": inss_version["id"], "version": inss_version["version"],
                        "competence": competence, "mode": mode, "source": inss_version["source"],
                        "sourceUrl": inss_version["source_url"], "contentHash": inss_version["content_hash"]})
                    versions["inss"] = int(inss_version["id"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors["inss"] = str(error)
    irrf_version = None
    if do_irrf:
        try:
            irrf_version = select_version("IRRF", payment_date, "SALARY_MONTHLY")
            if not irrf_version:
                errors["irrf"] = "Não há tabela IRRF publicada para esta data de pagamento e tipo de rendimento."
            else:
                parameters = parameters_for_version(irrf_version)
                inss_source = str(inputs.get("inssSource") or "CALCULATED")
                if inss_source == "MANUAL":
                    irrf_inss = _decimal(inputs.get("manualInss"), "INSS dedutível informado")
                    inss_note = str(inputs.get("manualInssSource") or "").strip()
                    if not inss_note:
                        errors["irrf"] = "Informe a origem do INSS dedutível usado no IRRF."
                elif links or other_bases or other_withheld:
                    errors["irrf"] = "Com outros vínculos, informe o INSS dedutível deste vínculo e a origem comprovada; a distribuição teórica agregada não é atribuída automaticamente."
                elif results["inss"]:
                    irrf_inss = _decimal(results["inss"]["contributionDue"])
                    inss_note = "Calculado nesta simulação com a versão INSS identificada."
                elif inputs.get("manualInss") not in (None, ""):
                    irrf_inss = _decimal(inputs["manualInss"], "INSS dedutível informado")
                    inss_note = str(inputs.get("manualInssSource") or "").strip()
                    if not inss_note:
                        errors["irrf"] = "Informe a origem do INSS dedutível usado no IRRF."
                else:
                    errors["irrf"] = "Calcule o INSS ou informe o INSS dedutível e sua origem para continuar com IRRF."
                if "irrf" not in errors:
                    if irrf_inss < 0:
                        errors["irrf"] = "INSS dedutível não pode ser negativo."
                    else:
                        results["irrf"] = calculate_irrf(parameters, gross, irrf_inss,
                            inputs.get("dependents", "0") or "0", inputs.get("alimony", "0") or "0",
                            inputs.get("otherLegalDeductions", "0") or "0", inputs.get("deductionMethod", "AUTO") or "AUTO",
                            "SALARY_MONTHLY")
                        results["irrf"].update({"versionId": irrf_version["id"], "version": irrf_version["version"],
                            "competence": competence, "paymentDate": payment_date, "incomeType": "SALARY_MONTHLY",
                            "inssSource": inss_note, "source": irrf_version["source"], "sourceUrl": irrf_version["source_url"],
                            "contentHash": irrf_version["content_hash"]})
                        versions["irrf"] = int(irrf_version["id"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors["irrf"] = str(error)

    snapshot_inputs = {key: value for key, value in inputs.items() if not str(key).startswith("_")}
    return {"inputs": snapshot_inputs, "results": results, "errors": errors, "versions": versions,
            "otherLinks": links, "complete": not errors and any(results.values()),
            "notices": ["Simulação hipotética sem identificação de colaborador; não é lançamento nem recolhimento.",
                        "O cálculo previdenciário isolado ou com dados agregados de outros vínculos pode não reproduzir a distribuição por fonte pagadora."]}
