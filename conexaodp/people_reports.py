"""Local, read-only HR reports. Missing source data never implies zero events."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import math
import re

from .registry import normalize
from .sib_record_mapping import _date as source_date

CATALOG = (
    ("employees", "Quadro", "Relação de funcionários", "Matrícula, nome, empresa, vínculo e situação na data de corte."),
    ("active", "Quadro", "Funcionários ativos", "Vínculos em atividade na data de corte."),
    ("terminated", "Quadro", "Funcionários desligados", "Vínculos encerrados até a data de corte."),
    ("companies", "Quadro", "Quadro por empresa", "Ativos, admissões, desligamentos e saldo por empresa."),
    ("tenure", "Quadro", "Tempo de empresa", "Faixas de permanência dos vínculos na data de corte."),
    ("due", "Quadro", "Vencimentos de contrato", "Primeiro e segundo vencimentos informados para o período."),
    ("admissions", "Movimentações", "Admissões", "Entradas ocorridas no período selecionado."),
    ("dismissals", "Movimentações", "Demissões", "Saídas efetivas no período selecionado."),
    ("history", "Movimentações", "Histórico funcional", "Revisões de cadastro auditadas localmente na V3."),
    ("rehires", "Movimentações", "Histórico de vínculos e recontratações", "Múltiplos vínculos associados à mesma pessoa identificada."),
    ("vacations", "Movimentações", "Funcionários em férias", "Férias em andamento na data de corte."),
    ("absences", "Movimentações", "Funcionários afastados", "Afastamentos em andamento na data de corte."),
    ("turnover", "Indicadores", "Turnover mensal", "Admissões, desligamentos, quadro médio e índice de cada mês."),
    ("departments", "Indicadores", "Quadro por departamento", "Distribuição por departamento informado ou código original."),
    ("jobs", "Indicadores", "Quadro por cargo", "Distribuição por função informada ou código original."),
    ("sex", "Indicadores", "Distribuição por sexo", "Composição pelo sexo declarado nas fichas disponíveis."),
    ("age", "Indicadores", "Distribuição etária", "Idade calculada na data de corte, por pessoa identificada."),
    ("salary", "Indicadores", "Faixa salarial", "Faixas do salário mensal informado nas fichas."),
    ("contracts", "Indicadores", "Tipos de contrato", "Distribuição dos tipos de contrato recebidos."),
    ("quality", "Indicadores", "Qualidade e cobertura dos cadastros", "Lacunas de preenchimento e datas em conferência."),
)
SENSITIVE = {"sex", "age", "salary", "absences"}
COHORT_REPORTS = {"tenure", "departments", "jobs", "sex", "age", "salary", "contracts"}
EMPLOYEE_REPORTS = {"employees", "active", "terminated", "admissions", "dismissals"}
# Stable field identifiers are shared by the preview and the complete XLSX export.
EMPLOYEE_COLUMNS = (
    ("registration", "Matrícula", "Funcionário", "text", False, True),
    ("name", "Nome do funcionário", "Funcionário", "text", False, True),
    ("cpf", "CPF", "Funcionário", "text", True, False),
    ("birth", "Data de nascimento", "Funcionário", "date", True, False),
    ("sex", "Sexo declarado", "Funcionário", "text", True, False),
    ("email", "E-mail do funcionário", "Funcionário", "text", False, False),
    ("phone", "Telefone do funcionário", "Funcionário", "text", False, False),
    ("admission", "Admissão", "Vínculo", "date", False, True),
    ("termination", "Desligamento", "Vínculo", "date", False, True),
    ("status", "Situação no corte", "Vínculo", "text", False, True),
    ("contract", "Tipo de contrato", "Vínculo", "text", False, True),
    ("department", "Departamento", "Vínculo", "text", False, False),
    ("job", "Cargo / função", "Vínculo", "text", False, False),
    ("first_due", "Primeiro vencimento", "Vínculo", "date", False, False),
    ("second_due", "Segundo vencimento", "Vínculo", "date", False, False),
    ("salary", "Salário mensal informado (R$)", "Vínculo", "currency", True, False),
    ("employer_code", "Código da empresa da folha", "Vínculo", "text", False, False),
    ("branch_code", "Filial da folha", "Vínculo", "text", False, False),
    ("reason", "Motivo / código de saída", "Vínculo", "text", False, False),
    ("source", "Origem", "Vínculo", "text", False, True),
    ("company_code", "Código da empresa", "Empresa", "text", False, True),
    ("company_name", "Nome da empresa", "Empresa", "text", False, True),
    ("company_cnpj", "CNPJ da empresa", "Empresa", "text", False, False),
    ("company_city", "Cidade da empresa", "Empresa", "text", False, False),
    ("company_state", "UF da empresa", "Empresa", "text", False, False),
    ("company_email", "E-mail da empresa", "Empresa", "text", False, False),
    ("company_phone", "Telefone da empresa", "Empresa", "text", False, False),
    ("company_address", "Endereço da empresa", "Empresa", "text", False, False),
    ("company_number", "Número do endereço da empresa", "Empresa", "text", False, False),
    ("company_complement", "Complemento da empresa", "Empresa", "text", False, False),
    ("company_neighborhood", "Bairro da empresa", "Empresa", "text", False, False),
    ("company_postal", "CEP da empresa", "Empresa", "text", False, False),
)
ADMIN_COLUMN_KEYS = {item[0] for item in EMPLOYEE_COLUMNS if item[4]}


def _document(value, length):
    text = _label(value)
    digits = re.sub(r"\D", "", text)
    if len(digits) != length:
        return text
    if length == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def _column_selection(options, params):
    from .people_analytics import _many, _one
    selected = _many(params, "reportColumns") if _one(params, "reportColumnsSet") == "1" else [item["key"] for item in options if item["default"]]
    allowed = {item["key"] for item in options}
    if not selected:
        raise ValueError("Selecione pelo menos uma coluna para o relatório.")
    if any(key not in allowed for key in selected):
        raise ValueError("Há colunas indisponíveis neste relatório ou para o seu acesso. Selecione novamente as colunas.")
    return [item for item in options if item["key"] in selected]


def _employee_table(db, records, originals, params, end, admin):
    from .people_analytics import _status, MISSING
    from .companies import profile
    options = [{"key": key, "label": label, "group": group, "type": kind, "administrative": private, "default": default}
               for key, label, group, kind, private, default in EMPLOYEE_COLUMNS if admin or not private]
    chosen = _column_selection(options, params)
    keys = {item["key"] for item in chosen}
    company_fields = {"company_cnpj": "cnpj", "company_city": "city", "company_state": "state",
                      "company_email": "email", "company_phone": "phone", "company_address": "address",
                      "company_number": "addressNumber", "company_complement": "addressComplement",
                      "company_neighborhood": "neighborhood", "company_postal": "postalCode"}
    profiles = {key: profile(db, key)[0] for key in {row["companyKey"] for row in records}} if keys & company_fields.keys() else {}
    local_contacts = {f'm:{row["id"]}': dict(row) for row in db.execute("SELECT id,cpf,email,whatsapp FROM registry_people")} if keys & {"cpf", "email", "phone"} else {}
    labels = {"active": "Ativo", "terminated": "Desligado", "future": "Admissão futura", "unknown": "A conferir"}
    table = []
    for row in sorted(records, key=lambda row: (normalize(row["name"]), row["registration"])):
        raw = originals.get(row["key"], {})
        local = local_contacts.get(row["person_key"], {})
        department = row["departments"] if row["departments"] != MISSING else (f'Departamento (código SIB) {raw["department_code"]}' if _label(raw.get("department_code")) else MISSING)
        job = row["jobs"] if row["jobs"] != MISSING else (f'Função (código SIB) {raw["job_code"]}' if _label(raw.get("job_code")) else MISSING)
        values = {"registration": row["registration"], "name": row["name"],
                  "admission": _date_text(row["admission"]), "termination": _date_text(row["termination"]),
                  "status": labels[_status(row, end)], "contract": row["contracts"], "source": row["source"],
                  "company_code": row["companyCode"], "company_name": row["company"],
                  "department": department, "job": job, "reason": row["reasons"],
                  "first_due": _date_text(_parsed(row.get("first_due_date"))), "second_due": _date_text(_parsed(row.get("second_due_date"))),
                  "email": raw.get("email") or local.get("email"), "phone": raw.get("phone") or local.get("whatsapp"),
                  "employer_code": raw.get("employer_code"), "branch_code": raw.get("branch_code")}
        if admin:
            values.update(cpf=_document(raw.get("cpf") or local.get("cpf"), 11), birth=_date_text(_parsed(raw.get("birth"))),
                          sex={"M": "Masculino", "F": "Feminino"}.get(_label(raw.get("sex")).upper(), _label(raw.get("sex"))),
                          salary=_amount(raw.get("salary")))
        company = profiles.get(row["companyKey"], {})
        values.update({key: company.get(field) for key, field in company_fields.items() if key in keys})
        if "company_cnpj" in keys:
            values["company_cnpj"] = _document(values.get("company_cnpj"), 14)
        table.append([value if isinstance(value, (int, float)) else _label(value) or MISSING for value in (values.get(item["key"]) for item in chosen)])
    return table, options, chosen


def _parsed(value):
    result = source_date(value)
    return date.fromisoformat(result) if result else None


def _amount(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError):
        return None


def _originals(db):
    # Explicit report fields only; never expose arbitrary raw fields or bank data.
    return {"sib:" + row["source_id"]: dict(row) for row in db.execute("""SELECT source_id,
        COALESCE(json_extract(data_json,'$.birthDate'),json_extract(raw_json,'$.DATA_NASC_FUN')) birth,
        COALESCE(json_extract(data_json,'$.sex'),json_extract(raw_json,'$.SEX_FUN')) sex,
        COALESCE(json_extract(data_json,'$.monthlySalary'),json_extract(raw_json,'$.SALARIO_MES_FUN')) salary,
        json_extract(raw_json,'$.DEP_FUN') department_code,
        json_extract(raw_json,'$.COD_FUN_FUN') job_code,
        json_extract(data_json,'$.cpf') cpf,
        json_extract(data_json,'$.email') email,
        json_extract(data_json,'$.phone') phone,
        json_extract(data_json,'$.employerCode') employer_code,
        json_extract(data_json,'$.branchCode') branch_code
        FROM local_sib_records WHERE dataset='employments'""")}


def _label(value):
    return str("" if value is None else value).strip()


def _date_text(value):
    return value.strftime("%d/%m/%Y") if value else "—"


def _distribution(counts, total, order=None):
    keys = order or sorted(counts, key=lambda value: (-counts[value], normalize(value)))
    return [[label, counts[label], round(counts[label] / total * 100, 1) if total else 0]
            for label in keys if counts[label] or order]


def build_reports(db, rows, companies, start, end, params, *, admin=False, export_all=False):
    from .people_analytics import _active, _buckets, _date, _metrics, _one, _status, _timeline, MISSING

    report_id = _one(params, "report")
    if report_id and report_id not in {item[0] for item in CATALOG}:
        raise ValueError("Relatório não encontrado.")
    originals = _originals(db)
    available = Counter()
    for row in rows:
        raw = originals.get(row["key"], {})
        available["sex"] += bool(_label(raw.get("sex")))
        birth = _parsed(raw.get("birth"))
        available["age"] += bool(birth and 0 <= (end - birth).days <= 111 * 366)
        available["salary"] += _amount(raw.get("salary")) is not None
        available["departments"] += row["departments"] != MISSING or bool(_label(raw.get("department_code")))
        available["jobs"] += row["jobs"] != MISSING or bool(_label(raw.get("job_code")))
        available["contracts"] += row["contracts"] != MISSING
    catalog = []
    for key, group, title, description in CATALOG:
        state, caption = "ready", "Disponível"
        if key == "vacations":
            state, caption = "missing", "Dados ainda não importados"
        elif key in SENSITIVE and not admin:
            state, caption = "restricted", "Acesso administrativo"
        elif key in available or key == "contracts":
            state, caption = ("partial", "Dados parciais") if available[key] < len(rows) else ("ready", "Disponível")
        if key == "history":
            caption = "Histórico local da V3"
        if key == 'absences' and admin:
            state, caption = 'partial', 'Consulte a cobertura da sincronização'
        catalog.append({"id": key, "group": group, "title": title, "description": description,
                        "state": state, "caption": caption, "administrative": key in SENSITIVE})
    result = {"catalog": catalog, "selected": None}
    if not report_id:
        return result
    selected = next(item for item in catalog if item["id"] == report_id)
    population = _one(params, "reportPopulation", "active")
    if population not in ("active", "period", "all"):
        population = "active"
    if population == "active":
        cohort = [row for row in rows if _active(row, end)]
        cohort_label = "Vínculos ativos na data de corte"
    elif population == "period":
        cohort = [row for row in rows if row["reliable"] and row["admission"] and row["admission"] <= end and (not row["termination"] or row["termination"] >= start)]
        cohort_label = "Vínculos com vigência no período"
    else:
        cohort = [row for row in rows if not row["admission"] or row["admission"] <= end]
        cohort_label = "Cadastros até a data de corte, incluindo situação a conferir"
    table, columns, notes = [], [], []
    column_options, chosen_columns = [], []
    unit, pending = "vínculos", ""
    if selected["state"] == "restricted":
        pending = "Este relatório usa campos da ficha original cujo acesso é administrativo. Entre com uma conta autorizada."
    elif report_id == 'absences':
        from .sib_absences import records as absence_records, analytics as absence_analytics
        subjects = {row['key']: row for row in rows}
        coverage = absence_analytics(db, set(subjects), end.isoformat())
        columns = ['Matrícula', 'Nome', 'Empresa', 'Início do afastamento', 'Fim do afastamento', 'Código do motivo', 'Motivo']
        for item in absence_records(db, keys=set(subjects), cutoff=end.isoformat()):
            if item['status'] == 'active':
                person = subjects[item['employment_key']]
                table.append([person['registration'], person['name'], person['company'], item['start_date'], item['end_date'], item['reason_code'], item['reason']])
        unit = 'afastamentos confirmados'
        notes.extend([f"{coverage['confirmed']} de {coverage['total']} vínculos SIB com consulta confirmada; {coverage['unknown']} sem confirmação. A ausência de dados não equivale a zero.",
                      'Situação na data de corte: início até o corte e fim ausente ou igual/posterior ao corte. O último dia informado está incluído.',
                      'Retornos ambíguos e registros que desapareceram da origem ficam para conferência e não entram na contagem. Afastamentos simultâneos do mesmo vínculo podem gerar mais de uma linha.'])
        if not coverage['confirmed'] and not table:
            pending = 'A sincronização ainda não confirmou afastamentos para esta seleção. Consulte o progresso na central de afastamentos.'
    elif report_id == "vacations":
        pending = "A base local ainda não armazena períodos de férias."
        pending += " Não é possível confirmar quem está nessa situação nem tratar a ausência dos dados como zero. A geração de relatórios não inicia consultas adicionais ao SIB."
    elif report_id in EMPLOYEE_REPORTS:
        labels = {"active": "Ativo", "terminated": "Desligado", "future": "Admissão futura", "unknown": "A conferir"}
        records = rows
        if report_id == "active": records = [row for row in rows if _active(row, end)]
        if report_id == "terminated": records = [row for row in rows if _status(row, end) == "terminated"]
        if report_id == "admissions": records = [row for row in rows if row["admission"] and start <= row["admission"] <= end]
        if report_id == "dismissals": records = [row for row in rows if row["reliable"] and row["termination"] and start <= row["termination"] <= end]
        table, column_options, chosen_columns = _employee_table(db, records, originals, params, end, admin)
        columns = [item["label"] for item in chosen_columns]
        notes.append("Relação por vínculo/matrícula. Recontratações aparecem separadas; situação calculada pelas datas disponíveis.")
        notes.append("Somente as colunas selecionadas entram na prévia e no Excel. Campos ausentes são identificados como não informados; não são consultados novamente no SIB.")
        if any(item["key"] == "salary" for item in chosen_columns):
            notes.append("Salário mensal nominal da ficha importada; não representa folha paga nem reconstituição salarial histórica.")
        if report_id in ("employees", "active", "terminated"):
            notes.append("Este relatório usa a data de corte; a data inicial não limita o cadastro ou os desligamentos anteriores ao corte.")
    elif report_id == "companies":
        columns = ["Código", "Empresa", "Ativos no início", "Ativos no corte", "Admissões", "Desligamentos", "Saldo"]
        grouped = defaultdict(list)
        for row in rows: grouped[row["companyKey"]].append(row)
        for key in sorted(grouped, key=lambda key: normalize(companies[key]["name"])):
            metrics = _metrics(grouped[key], start, end)
            table.append([companies[key]["code"], companies[key]["name"], *[metrics[key] for key in ("opening", "active", "admitted", "terminated", "balance")]])
        unit = "empresas"
    elif report_id == "turnover":
        columns = ["Mês", "Ativos no início", "Admissões", "Desligamentos", "Saldo", "Ativos no fim", "Quadro médio", "Turnover (%)"]
        opening = sum(_active(row, start - timedelta(days=1)) for row in rows)
        for point in _timeline(rows, _buckets(start, end, "month")):
            average = (opening + point["active"]) / 2
            turnover = round((point["admitted"] + point["terminated"]) / 2 / average * 100, 2) if average else None
            table.append([point["label"], opening, point["admitted"], point["terminated"], point["balance"], point["active"], round(average, 1), turnover])
            opening = point["active"]
        notes.extend(["Turnover = ((admissões + desligamentos) / 2) / quadro médio × 100. Quadro médio = (ativos no início + ativos no fim) / 2.", "Sem quadro médio, o índice é não calculável (—). O primeiro e o último mês podem representar intervalos parciais, conforme as datas selecionadas."])
        unit = "meses"
    elif report_id in ("departments", "jobs", "contracts"):
        counts = Counter()
        for row in cohort:
            value = row[report_id]
            if value == MISSING and report_id != "contracts":
                field = "department_code" if report_id == "departments" else "job_code"
                code = _label(originals.get(row["key"], {}).get(field))
                value = ("Departamento" if report_id == "departments" else "Função") + f" (código SIB) {code}" if code else MISSING
            counts[value] += 1
        columns = [{"departments": "Departamento", "jobs": "Cargo / função", "contracts": "Tipo de contrato"}[report_id], "Vínculos", "Participação (%)"]
        table = _distribution(counts, len(cohort))
        notes.append("Sem descrição, os códigos originais são identificados como códigos do SIB; nomes de departamentos, funções e tipos de contrato não são presumidos.")
        unit = "grupos"
    elif report_id == "tenure":
        labels = ("Até 30 dias", "31 a 90 dias", "91 a 180 dias", "181 a 365 dias", "1 a 2 anos", "2 a 5 anos", "Mais de 5 anos", "Não calculável")
        counts = Counter()
        for row in cohort:
            if not row["admission"] or not row["reliable"]:
                counts["Não calculável"] += 1
                continue
            last = min(end, row["termination"]) if row["termination"] else end
            days = (last - row["admission"]).days
            counts[labels[0 if days <= 30 else 1 if days <= 90 else 2 if days <= 180 else 3 if days <= 365 else 4 if days <= 730 else 5 if days <= 1826 else 6]] += 1
        columns, table, unit = ["Tempo de vínculo", "Vínculos", "Participação (%)"], _distribution(counts, len(cohort), labels), "faixas"
        notes.append("Tempo desde a admissão até a data de corte ou a saída, o que ocorrer primeiro. Recontratações não são somadas como um vínculo único.")
    elif report_id in ("sex", "age", "salary"):
        demographic = {}
        for row in sorted(cohort, key=lambda row: (row["admission"] or date.min, row["key"])):
            demographic[row["person_key"]] = row
        sample = cohort if report_id == "salary" else list(demographic.values())
        counts = Counter()
        salary_labels = ("Até R$ 1.500", "Acima de R$ 1.500 até R$ 2.500", "Acima de R$ 2.500 até R$ 4.000", "Acima de R$ 4.000 até R$ 6.000", "Acima de R$ 6.000 até R$ 10.000", "Acima de R$ 10.000", "Não informado")
        age_labels = ("Até 17 anos", "18 a 24 anos", "25 a 34 anos", "35 a 44 anos", "45 a 54 anos", "55 a 64 anos", "65 anos ou mais", "Não calculável")
        for row in sample:
            raw = originals.get(row["key"], {})
            if report_id == "sex":
                value = _label(raw.get("sex")).upper()
                label = {"M": "Masculino", "F": "Feminino", "": "Não informado"}.get(value, "Outro código declarado: " + value)
            elif report_id == "age":
                birth = _parsed(raw.get("birth"))
                age = end.year - birth.year - ((end.month, end.day) < (birth.month, birth.day)) if birth else -1
                label = age_labels[7 if not 0 <= age <= 110 else 0 if age < 18 else 1 if age < 25 else 2 if age < 35 else 3 if age < 45 else 4 if age < 55 else 5 if age < 65 else 6]
            else:
                amount = _amount(raw.get("salary"))
                label = salary_labels[6 if amount is None else 0 if amount <= 1500 else 1 if amount <= 2500 else 2 if amount <= 4000 else 3 if amount <= 6000 else 4 if amount <= 10000 else 5]
            counts[label] += 1
        columns = [{"sex": "Sexo declarado", "age": "Faixa etária", "salary": "Salário mensal informado"}[report_id], "Vínculos" if report_id == "salary" else "Pessoas identificadas", "Participação (%)"]
        table = _distribution(counts, len(sample), age_labels if report_id == "age" else salary_labels if report_id == "salary" else None)
        if report_id == "salary":
            notes.append("Valores mensais nominais do campo de salário mensal da ficha importada. Não é folha paga nem reconstituição salarial histórica; valores ausentes ou não positivos são não informados.")
        else:
            notes.append("Uma pessoa por agrupamento disponível em Vínculos, usando sua ficha mais recente na população escolhida. Ausência de identidade confirmada pode impedir deduplicação.")
            notes.append("Idade calculada na data de corte." if report_id == "age" else "Distribuição conforme o campo de sexo declarado na ficha, sem inferência por nome.")
        unit = "faixas" if report_id != "sex" else "grupos"
    elif report_id == "rehires":
        grouped = defaultdict(list)
        for row in rows:
            if not row["admission"] or row["admission"] <= end: grouped[row["person_key"]].append(row)
        columns = ["Nome", "Matrículas", "Empresas", "Vínculos", "Ativos no corte", "Primeira admissão", "Última admissão"]
        for group in sorted(grouped.values(), key=lambda group: normalize(group[0]["name"])):
            if len(group) < 2: continue
            admissions = sorted(row["admission"] for row in group if row["admission"])
            table.append([group[0]["name"], ", ".join(str(row["registration"]) for row in group), "; ".join(sorted({row["company"] for row in group})), len(group), sum(_active(row, end) for row in group), _date_text(admissions[0] if admissions else None), _date_text(admissions[-1] if admissions else None)])
        notes.append("Múltiplos vínculos associados à mesma identidade na V3. Não confirma, por si só, recontratação: vínculos simultâneos também aparecem. A data inicial não elimina vínculos anteriores do histórico.")
        unit = "pessoas identificadas"
    elif report_id == "due":
        columns = ["Matrícula", "Nome", "Empresa", "Vencimento", "Etapa", "Situação no corte"]
        for row in rows:
            for field, label in (("first_due_date", "Primeiro vencimento"), ("second_due_date", "Segundo vencimento")):
                due = _date(row.get(field))
                if due and start <= due <= end:
                    table.append([row["registration"], row["name"], row["company"], due.isoformat(), label, {"active": "Ativo", "terminated": "Desligado", "future": "Admissão futura", "unknown": "A conferir"}[_status(row, end)]])
        table.sort(key=lambda row: (row[3], normalize(row[1])))
        for row in table: row[3] = _date_text(date.fromisoformat(row[3]))
        notes.append("Datas recebidas nas fichas. A presença de vencimento não define automaticamente o tipo do contrato nem confirma prorrogação. A seleção de datas segue o período do relatório.")
        unit = "vencimentos"
    elif report_id == "history":
        subjects = {}
        for row in rows:
            subjects[row["key"]] = row
            subjects[row["person_key"]] = row
        columns = ["Data da revisão", "Matrícula", "Nome", "Empresa", "Alteração", "Responsável", "Justificativa"]
        kinds = {"PERSON_NAME": "Revisão de nome", "LINK_EMPLOYMENT": "Associação de vínculo"}
        for item in db.execute("SELECT r.*,u.name actor FROM registry_reviews r JOIN users u ON u.id=r.actor_id WHERE r.kind IN ('PERSON_NAME','LINK_EMPLOYMENT') ORDER BY r.created_at DESC,r.id DESC"):
            row = subjects.get(item["subject_key"])
            # Local timestamps are stored in UTC; filter/report using São Paulo.
            from datetime import datetime
            instant = datetime.fromisoformat(item["created_at"]) - timedelta(hours=3)
            if row and start <= instant.date() <= end:
                table.append([instant.strftime("%d/%m/%Y %H:%M"), row["registration"], row["name"], row["company"], kinds[item["kind"]], item["actor"], item["note"]])
        notes.extend(["Apenas revisões de nome e associação de vínculo auditadas localmente na V3.", "Atualizações das fichas SIB não mantêm versões anteriores neste histórico. Este relatório não representa a trilha completa de alterações do Bitti."])
        unit = "revisões locais"
    elif report_id == "quality":
        columns = ["Verificação", "Vínculos", "Participação (%)"]
        checks = {"Admissão ausente ou inválida": lambda row: not row["admission"], "Datas em conferência": lambda row: not row["reliable"], "Contrato não informado": lambda row: row["contracts"] == MISSING, "Departamento sem descrição": lambda row: row["departments"] == MISSING, "Cargo sem descrição": lambda row: row["jobs"] == MISSING}
        table = [[label, sum(check(row) for row in rows), round(sum(check(row) for row in rows) / len(rows) * 100, 1) if rows else 0] for label, check in checks.items()]
        notes.append("Os critérios podem se sobrepor: não some as contagens como se fossem pessoas distintas. Considera todos os cadastros que atendem aos filtros, mesmo fora do período.")
        unit = "verificações"
    if columns and not column_options:
        def kind(index, label):
            if label.endswith("(%)"): return "percent"
            if label == "Mês": return "month"
            if label == "Data da revisão": return "datetime"
            if any(word in label.lower() for word in ("admissão", "vencimento")): return "date"
            return "number" if any(isinstance(row[index], (int, float)) for row in table) else "text"
        column_options = [{"key": f"metric_{index}", "label": label, "group": "Dados do relatório", "type": kind(index, label), "default": True, "administrative": False} for index, label in enumerate(columns)]
        chosen_columns = _column_selection(column_options, params)
        indexes = [column_options.index(item) for item in chosen_columns]
        columns = [item["label"] for item in chosen_columns]
        table = [[row[index] for index in indexes] for row in table]
    try:
        page = max(1, min(int(_one(params, "reportPage", "1")), max(1, (len(table) + 49) // 50)))
    except ValueError:
        page = 1
    result["selected"] = {**selected, "columns": columns, "rows": table if export_all else table[(page - 1) * 50:page * 50],
                          "total": len(table), "page": page, "pageSize": 50, "unit": unit, "notes": notes,
                          "pending": pending, "population": population, "populationLabel": cohort_label,
                          "populationSize": len(cohort), "hasPopulation": report_id in COHORT_REPORTS,
                          "columnOptions": column_options, "columnKeys": [item["key"] for item in chosen_columns],
                          "columnTypes": [item["type"] for item in chosen_columns],
                          "chartReady": all(item["key"] in {column["key"] for column in chosen_columns} for item in column_options[:3]) and len(column_options) >= 3}
    return result

