"""Projeção local de pessoas e vínculos sem alterar a fotografia da V2."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from .sib_import import source_records


def employment_activity(admission, termination):
    """Situação pelas datas explícitas; códigos de situação SIB não são presumidos."""
    def parsed(value):
        try:
            return date.fromisoformat(str(value)) if value else None
        except (TypeError, ValueError):
            return None
    start, end = parsed(admission), parsed(termination)
    if (admission and not start) or (termination and not end) or (start and end and end < start):
        return None, "Situação a conferir"
    today = date.today()
    if end and end <= today:
        return False, "Desligado"
    if start and start > today:
        return False, "Admissão futura"
    if start:
        return True, "Ativo"
    return None, "Sem saída registrada"


def sib_activity(data):
    if data.get('contractCancelled'):
        return False, 'Contrato cancelado'
    if data.get('sibPresence') == 'EXCLUDED':
        return False, 'Excluído no SIB'
    if data.get("activityDatesValid") is False or data.get("status") == "Datas em conferência":
        return None, "Situação a conferir"
    return employment_activity(data.get("admissionDate"), data.get("dismissalDate") or data.get("terminationDate"))


_PRIVATE_FIELD = re.compile(r"SENHA|PASSWORD|SECRET|TOKEN|API_?KEY|CHAVE_?API", re.I)
_FIELD_LABELS = {
    'sibPresence': 'Presença no SIB', 'contractCancelled': 'Contrato cancelado antes do início',
    'sibLastSeen': 'Última confirmação no SIB', 'sibMissingSince': 'Ausente desde',
    "sourceId": "Identificador na origem", "registration": "Código do funcionário", "name": "Nome",
    "registrationCode": "Matrícula com filial",
    "code": "Código", "companyId": "Código da empresa", "cpf": "CPF", "cnpj": "CNPJ",
    "companyCode": "Empresa da folha", "branchCode": "Filial da folha", "email": "E-mail", "phone": "Telefone",
    "admissionDate": "Admissão", "dismissalDate": "Desligamento", "terminationDate": "Desligamento",
    "firstDueDate": "Primeiro vencimento", "secondDueDate": "Segundo vencimento", "contractType": "Tipo de contrato",
    "jobTitle": "Cargo", "department": "Departamento", "statusSource": "Situação informada pelo SIB", "sourceStatus": "Código de situação no SIB",
    "contractTypeCode": "Código do vínculo SIB", "departmentCode": "Código do departamento SIB", "jobCode": "Código do cargo SIB",
    "VIN_FUN": "Código do vínculo SIB", "COD_FUN_FUN": "Código do cargo SIB",
    "SIG_FUN_FUN": "Prefixo do cargo SIB", "DEP_FUN": "Departamento SIB",
    "active": "Ativo pelas datas", "legalName": "Razão social", "postalCode": "CEP", "address": "Endereço",
    "addressNumber": "Número", "addressComplement": "Complemento", "neighborhood": "Bairro", "city": "Cidade", "state": "UF",
    "bankCode": "Banco", "bankBranch": "Agência", "bankBranchDigit": "Dígito da agência", "bankAccount": "Conta bancária",
    "bankDigit": "Dígito da conta", "bankAccountType": "Tipo da conta", "pisNis": "PIS / NIS",
    "payrollDay": "Dia do pagamento", "advanceDay": "Dia do adiantamento", "pointStart": "Início do fechamento do ponto",
    "pointEnd": "Fim do fechamento do ponto", "updatedAt": "Atualizado em", "createdAt": "Criado em",
    "COD_FUN": "Código do funcionário", "EMP_FUN": "Empresa da folha", "FIL_FUN": "Filial da folha",
    "NOM_FUN": "Nome", "CPF_FUN": "CPF", "COD_CLI_FUN": "Código da empresa tomadora", "DATA_ADM_FUN": "Admissão",
    "DATA_DEM_FUN": "Desligamento", "DATA_VEN1_FUN": "Primeiro vencimento", "DATA_VEN2_FUN": "Segundo vencimento",
    "EMAIL_FUN": "E-mail", "TEL_CEL_FUN": "Telefone celular", "NUM_PIS_FUN": "PIS / NIS",
    "COD_CLI": "Código da empresa", "NOM_CLI": "Razão social", "NCGC_CLI": "CNPJ — raiz", "ESTA_CLI": "CNPJ — estabelecimento",
    "DC_CLI": "CNPJ — dígitos", "STATUS_GRAV_FUN": "Código de situação no SIB", "SIT_CLI": "Código de situação da empresa no SIB",
}


def categorized_fields(value, *, company=False):
    """Todos os campos não vazios, agrupados sem expor colunas de credenciais."""
    categories = {key: [] for key in ("Identificação", "Contato e endereço", "Operação e contrato" if company else "Vínculo e contrato", "Bancário e documentos", "Outros dados")}
    def visit(item, path=()):
        if isinstance(item, dict):
            for key, child in item.items():
                if not _PRIVATE_FIELD.search(str(key)) and str(key) not in ("schemaVersion", "originalSib"):
                    yield from visit(child, (*path, str(key)))
        elif isinstance(item, list):
            for index, child in enumerate(item, 1):
                yield from visit(child, (*path, str(index)))
        elif item not in (None, "") and path:
            label = _FIELD_LABELS.get(path[-1], " ".join(path[-1].split("_")).capitalize())
            yield path, label, "Sim" if item is True else "Não" if item is False else str(item)
    for path, label, item in visit(value):
        terms = normalize(" ".join(path) + " " + label)
        if any(part in terms for part in ("banc", "bank", "conta", "agencia", "pis", "nis", "ctps", "carteira", "document", "num_rg", "cnh", "titulo", "reservista")):
            group = "Bancário e documentos"
        elif any(part in terms for part in ("email", "e-mail", "mail", "fone", "tel_", "phone", "whatsapp", "endereco", "address", "bairro", "neighborhood", "cidade", "city", "postal", "cep", "uf_", "state", "complement", "numero")):
            group = "Contato e endereço"
        elif any(part in terms for part in ("admiss", "admission", "demiss", "deslig", "dismiss", "termination", "contrat", "contract", "venc", "due", "cargo", "jobtitle", "depart", "salari", "remuner", "pagamento", "payroll", "adiant", "advance", "point", "ponto", "ativo", "active", "situacao", "status", "jornada", "hora_")):
            group = "Operação e contrato" if company else "Vínculo e contrato"
        elif any(part in terms for part in ("nome", "name", "cpf", "cnpj", "nasc", "birth", "sexo", "mae", "pai", "nacional", "natural", "civil", "codigo", "identif", "sourceid", "registration", "code", "cod_fun", "emp_fun", "fil_fun", "cod_cli")):
            group = "Identificação"
        else:
            group = "Outros dados"
        categories[group].append((label, item, " · ".join(path)))
    return {key: fields for key, fields in categories.items() if fields}


def render_categories(value, escape, *, company=False):
    groups = categorized_fields(value, company=company)
    return '<div class="registry-categories">' + ''.join(
        f'<details class="registry-category"{" open" if index == 0 else ""}><summary>{escape(title)} <small>{len(fields)} campo(s)</small></summary><dl class="registry-dl">' +
        ''.join(f'<dt title="{escape(original)}">{escape(label)}</dt><dd>{escape(item)}</dd>' for label, item, original in fields) + '</dl></details>'
        for index, (title, fields) in enumerate(groups.items())) + '</div>'


def _render_sib_details(db, employment_keys, escape):
    output = []
    for key in employment_keys:
        if not key.startswith("sib:"):
            continue
        row = db.execute("SELECT data_json,raw_json FROM local_sib_records WHERE dataset='employments' AND source_id=?", (key[4:],)).fetchone()
        if row:
            data, raw = json.loads(row["data_json"]), json.loads(row["raw_json"])
            code = str(data.get("registration") or key[4:])
            fields = {**raw, **data}
            output.append(f'<section class="registry-source-detail"><h3>Cadastro SIB · {escape(code)}</h3>{render_categories(fields, escape)}</section>')
    return ''.join(output)


def normalize(value: str | None) -> str:
    plain = unicodedata.normalize("NFD", value or "")
    return " ".join("".join(ch for ch in plain if not unicodedata.combining(ch)).casefold().split())


def _person_key(registration: str, company: str, name: str) -> str:
    raw = "\x1f".join(normalize(part) for part in (registration, company, name))
    return "p:" + hashlib.sha256(raw.encode()).hexdigest()


def project(db, *, sib_records=None):
    """Agrupa vínculos pela identidade provisória adotada pela V2."""
    latest = {}
    history = defaultdict(list)
    for row in db.execute("SELECT r.*,u.name actor FROM registry_reviews r JOIN users u ON u.id=r.actor_id ORDER BY r.id"):
        latest[(row["kind"], row["subject_key"])] = row
        history[row["subject_key"]].append(row)
    people = {}
    employments = []
    for row in db.execute("SELECT * FROM v2_employments"):
        employment_id = "v:" + row["external_key"]
        default_key = _person_key(row["registration"], row["company"], row["name"])
        link = latest.get(("LINK_EMPLOYMENT", employment_id))
        person_key = link["value"] if link else default_key
        person = people.setdefault(person_key, {"key": person_key, "name": row["name"], "verified": bool(link),
                                               "cpf": "", "email": "", "whatsapp": "", "is_test": False,
                                               "employments": []})
        person["verified"] = person["verified"] or bool(link)
        activity, status = snapshot_activity(row["in_active_export"], row["admission_date"], row["termination_date"])
        item = {"key": employment_id, "person_key": person_key, "name": row["name"],
                "registration": row["registration"], "company": row["company"], "department": row["department"],
                "job_title": row["job_title"], "contract_type": row["contract_type"],
                "admission_date": row["admission_date"], "termination_date": row["termination_date"],
                "status": status, "active": activity, "source": "V2"}
        employments.append(item)
        person["employments"].append(item)
    for row in db.execute("SELECT * FROM registry_people"):
        key = f'm:{row["id"]}'
        people[key] = {"key": key, "name": row["name"], "verified": True,
                       "cpf": row["cpf"] or "", "email": row["email"] or "",
                       "whatsapp": row["whatsapp"] or "", "is_test": bool(row["is_test"]),
                       "employments": people.get(key, {}).get("employments", [])}
    sib_companies = dict(sib_records["companies"] if sib_records is not None else source_records(db, "companies"))
    for source_id, data in (sib_records["employments"] if sib_records is not None else source_records(db, "employments")):
        cpf = re.sub(r"\D", "", str(data.get("cpf") or ""))
        valid_cpf = len(cpf) == 11 and len(set(cpf)) > 1
        if valid_cpf:
            for size in (9, 10):
                digit = (sum(int(cpf[i]) * (size + 1 - i) for i in range(size)) * 10 % 11) % 10
                valid_cpf = valid_cpf and digit == int(cpf[size])
        person_key = "sib:p:" + (hashlib.sha256(cpf.encode()).hexdigest() if valid_cpf else source_id)
        person = people.setdefault(person_key, {"key": person_key, "name": data.get("name") or f"Cadastro SIB {source_id}",
            "verified": valid_cpf, "cpf": cpf, "email": data.get("email") or "", "whatsapp": data.get("phone") or "",
            "is_test": False, "employments": []})
        company_id = str(data.get("companyId") or "")
        company = sib_companies.get(company_id, {})
        activity, activity_label = sib_activity(data)
        item = {"key": "sib:" + source_id, "person_key": person_key, "name": person["name"],
                "registration": data.get("registration") or "", "company": company.get("name") or f"Empresa SIB {company_id}",
                "registration_code": data.get('registrationCode') or data.get('registration') or '',
                "branch_code": data.get('branchCode') or '',
                "company_key": "sib:" + company_id, "department": data.get("department") or "", "job_title": data.get("jobTitle") or "",
                "department_code": data.get('departmentCode') or '', "job_code": data.get('jobCode') or '',
                "contract_type_code": data.get('contractTypeCode') or '',
                "contract_type": data.get("contractType") or "", "admission_date": data.get("admissionDate") or "",
                "termination_date": data.get("dismissalDate") or data.get("terminationDate") or "",
                "first_due_date": data.get("firstDueDate") or "", "second_due_date": data.get("secondDueDate") or "",
                "status": activity_label, "active": activity, "source": "SIB", "is_test": False}
        item.update(contract_cancelled=bool(data.get('contractCancelled')), sib_presence=data.get('sibPresence', 'PRESENT'))
        employments.append(item)
        person["employments"].append(item)
    for row in db.execute("SELECT * FROM registry_employments"):
        key = row["person_key"]
        person = people.get(key)
        if not person:
            continue
        activity, activity_label = employment_activity(row["admission_date"], row["termination_date"])
        item = {"key": f'local:{row["id"]}', "person_key": key, "name": person["name"],
                "registration": row["registration"], "company": row["company"], "department": row["unit"],
                "job_title": "", "contract_type": row["contract_type"], "admission_date": row["admission_date"],
                "termination_date": row["termination_date"], "status": activity_label, "active": activity,
                "first_due_date": row["first_due_date"] or "", "second_due_date": row["second_due_date"] or "",
                "dismissal_reason_code": row["dismissal_reason_code"] or "", "dismissal_reason": row["dismissal_reason"] or "",
                "is_test": bool(row["is_test"] or person.get("is_test")),
                "source": "Cadastro local"}
        employments.append(item)
        person["employments"].append(item)
    for person in people.values():
        review = latest.get(("PERSON_NAME", person["key"]))
        if review:
            person["name"] = review["value"]
            person["verified"] = True
    return people, employments, history


def project_conflicts(db):
    """Detecta as inconsistências que podem ser avaliadas sem CPF."""
    people, employments, history = project(db)
    found = {}
    code_names = defaultdict(set)
    for item in employments:
        if item["source"] != "V2":
            continue
        code_names[(normalize(item["registration"]), normalize(item["company"]))].add(normalize(item["name"]))
        if not item["admission_date"] or (item["termination_date"] and item["termination_date"] < item["admission_date"]):
            key = "DATA_INCOMPATIVEL|" + item["key"]
            found[key] = ("Datas incompatíveis", f'{item["name"]}: admissão ausente ou saída anterior à admissão')
        if normalize(item["contract_type"]) in ("", "nao informado", "verificar"):
            key = "CLASSIFICACAO_INDEFINIDA|" + item["key"]
            found[key] = ("Classificação indefinida", f'{item["name"]}: tipo de contrato requer revisão')
    for row in db.execute("SELECT external_key,name,in_active_export,termination_date FROM v2_employments WHERE in_active_export=1 AND termination_date IS NOT NULL"):
        key = "SITUACAO_DIVERGENTE|v:" + row["external_key"]
        found[key] = ("Situação divergente", f'{row["name"]}: consta como ativo e também tem data de saída')
    for (code, company), names in code_names.items():
        if len(names) > 1:
            key = "MATRICULA_CONFLITANTE|" + hashlib.sha256(f"{code}|{company}".encode()).hexdigest()
            found[key] = ("Matrícula conflitante", "Código e cliente associados a nomes diferentes")
    for person in people.values():
        if len(person["employments"]) > 1 and not person["verified"]:
            key = "POSSIVEL_RECONTRATACAO|" + person["key"]
            found[key] = ("Possível vínculo incorreto", f'{person["name"]}: {len(person["employments"])} admissões no mesmo agrupamento provisório')
    latest = {}
    for row in db.execute("SELECT * FROM registry_reviews WHERE kind='CONFLICT_REVIEW' ORDER BY id"):
        latest[row["subject_key"]] = row["value"]
    conflicts = [{"key": key, "kind": kind, "detail": detail, "status": latest.get(key, "PENDING")}
                 for key, (kind, detail) in found.items()]
    conflicts.sort(key=lambda item: (item["status"] != "PENDING", item["kind"], item["key"]))
    return conflicts, history


def _date(value):
    return "/".join(reversed(value.split("-"))) if value else "—"


def snapshot_activity(in_active_export, admission, termination):
    activity, label = employment_activity(admission, termination)
    if in_active_export:
        if label in ("Desligado", "Situação a conferir"):
            return None, "Situação a conferir"
        return (False, label) if label == "Admissão futura" else (True, "Ativo")
    if termination:
        return activity, label
    return (False, label) if label == "Admissão futura" else (None, "Sem saída registrada")


def person_activity(person):
    """Uma pessoa aparece em um grupo, preservando o histórico dos seus vínculos."""
    statuses = {item["status"] for item in person["employments"]}
    if "Ativo" in statuses:
        return "Ativo"
    if not statuses or any(status not in ("Desligado", "Admissão futura") for status in statuses):
        return "Situação a conferir"
    if "Admissão futura" in statuses:
        return "Admissão futura"
    return "Desligado"


def render(db, path, parameters, escape, csrf, can_manage):
    people, employments, history = project(db)
    is_people = path.endswith("/pessoas")
    title = "Pessoas" if is_people else "Vínculos"
    search = (parameters.get("q") or [""])[0].strip()[:100]
    status = (parameters.get("status") or ["all"])[0]
    if status not in ("all", "Ativo", "Desligado", "Admissão futura", "Sem saída registrada", "Situação a conferir"):
        status = "all"
    selected = (parameters.get("id") or [""])[0][:150]
    person_filter = (parameters.get("person") or [""])[0][:150] if not is_people else ""
    history_person = people.get(person_filter) if person_filter else None
    rows = list(people.values()) if is_people else employments
    if person_filter:
        rows = [item for item in rows if item["person_key"] == person_filter]
    needle = normalize(search)
    activity_labels = {"Ativo": "Ativa", "Desligado": "Desligada", "Admissão futura": "Admissão futura", "Situação a conferir": "Situação a conferir"}
    people_counts = defaultdict(int)
    if is_people:
        for person in rows:
            text = " ".join([person["name"], person.get("cpf", "")] + [f'{item["registration"]} {item["company"]}' for item in person["employments"]])
            if needle in normalize(text):
                people_counts[person_activity(person)] += 1
    def matches(row):
        if is_people:
            text = " ".join([row["name"], row.get("cpf", "")] + [f'{item["registration"]} {item["company"]}' for item in row["employments"]])
            relevant = status == "all" or person_activity(row) == ("Situação a conferir" if status == "Sem saída registrada" else status)
        else:
            text = " ".join(str(row.get(key) or "") for key in ("name", "registration", "company", "contract_type"))
            relevant = status == "all" or row["status"] == status
        return relevant and needle in normalize(text)
    filtered = [item for item in rows if matches(item)]
    filtered.sort(key=lambda item: normalize(item["name"]) if is_people else (item["admission_date"] or ""), reverse=not is_people)
    total_pages = max(1, (len(filtered) + 49) // 50)
    requested_page = (parameters.get("page") or ["1"])[0]
    page = max(1, min(int(requested_page), total_pages)) if requested_page.isdigit() and len(requested_page) <= 8 else 1
    visible = filtered[(page - 1) * 50:page * 50]
    common_parameters = {"q": search, "status": status}
    if person_filter:
        common_parameters["person"] = person_filter
    def link(item):
        return path + "?" + urlencode({**common_parameters, "page": page, "id": item["key"]})
    pagination = '<nav class="registry-pagination" aria-label="Páginas de cadastros">'
    if page > 1:
        pagination += f'<a href="{escape(path + "?" + urlencode({**common_parameters,"page":page-1}))}">Anterior</a>'
    pagination += f'<span>Página {page} de {total_pages} · {len(filtered)} registro(s)</span>'
    if page < total_pages:
        pagination += f'<a href="{escape(path + "?" + urlencode({**common_parameters,"page":page+1}))}">Próxima</a>'
    pagination += '</nav>'
    if is_people:
        def person_codes(item):
            codes = list(dict.fromkeys(str(e["registration"]) for e in item["employments"] if e.get("registration")))
            return ", ".join(codes) if codes else "Local " + item["key"].removeprefix("m:")
        entries = "".join(f'<a class="registry-item registry-record-card{" selected" if item["key"]==selected else ""}" href="{escape(link(item))}"><div class="registry-card-title"><span class="registry-code">{escape(person_codes(item))}</span><strong>{escape(item["name"])}{" <em>DADOS DE TESTE</em>" if item.get("is_test") else ""}</strong></div><span class="registry-activity registry-activity-{"with-active" if person_activity(item)=="Ativo" else "without-active" if person_activity(item)=="Desligado" else "unknown"}">{escape(activity_labels[person_activity(item)])}</span><span>{len(item["employments"])} vínculo(s) · {escape(item["employments"][0]["company"] if item["employments"] else "Cadastro local")}</span></a>' for item in visible)
        chosen = next((item for item in filtered if item["key"] == selected), None)
    else:
        entries = "".join(f'<a class="registry-item registry-record-card{" selected" if item["key"]==selected else ""}" href="{escape(link(item))}"><div class="registry-card-title"><span class="registry-code">{escape(item["registration"] or "Sem código")}</span><strong>{escape(item["name"])}</strong></div><span>{escape(item["company"])} · {escape(item["contract_type"] or "Contrato não classificado")} · {escape(item["status"])}</span></a>' for item in visible)
        chosen = next((item for item in filtered if item["key"] == selected), None)
    status_options = (("all","Todas"),("Ativo","Ativas"),("Desligado","Desligadas"),("Admissão futura","Admissões futuras"),("Situação a conferir","Situação a conferir")) if is_people else (("all","Todas"),("Ativo","Ativo"),("Desligado","Desligado"),("Admissão futura","Admissão futura"),("Sem saída registrada","Sem saída registrada"),("Situação a conferir","Situação a conferir"))
    if is_people and status == "Sem saída registrada":
        status = "Situação a conferir"
    search_form = (f'<form class="row" method="get"><label>Buscar<input name="q" value="{escape(search)}" placeholder="Nome, matrícula, cliente ou tipo"></label>'
                   '<label>Situação<select name="status">' + ''.join(f'<option value="{escape(item)}"{" selected" if item==status else ""}>{escape(label)}</option>' for item,label in
                   status_options) +
                   '</select></label><button>Filtrar</button></form>')
    if person_filter:
        search_form = search_form.replace('<form class="row" method="get">', f'<form class="row" method="get"><input type="hidden" name="person" value="{escape(person_filter)}">', 1)
    history_heading = ""
    if person_filter:
        if history_person:
            history_heading = (f'<section class="card"><h2>Histórico de vínculos · {escape(history_person["name"])}</h2><p>Admissões e matrículas desta pessoa disponíveis na V3, da mais recente para a mais antiga.</p><a href="{escape("/cadastros/pessoas?" + urlencode({"id": person_filter, "status": "all"}))}">Voltar ao cadastro da pessoa</a> · <a href="/cadastros/vinculos">Ver vínculos de todas as pessoas</a></section>')
        else:
            history_heading = '<section class="card"><p>A pessoa deste histórico não foi encontrada.</p><a href="/cadastros/vinculos">Ver vínculos de todas as pessoas</a></section>'
    people_groups = ""
    if is_people:
        people_groups = '<nav class="registry-company-groups registry-people-groups" aria-label="Pessoas por situação">' + ''.join(
            f'<a class="registry-group-link{" selected" if value == status else ""}" href="{escape(path + "?" + urlencode({"q": search, "status": value}))}"{" aria-current=\"page\"" if value == status else ""}>{escape(label)} <strong>{sum(people_counts.values()) if value == "all" else people_counts[value]}</strong></a>'
            for value, label in status_options) + '</nav><p class="registry-criterion">Ativas têm pelo menos um vínculo ativo. Desligadas têm todos os vínculos encerrados. Admissões futuras e situações sem informação suficiente aparecem em grupos próprios.</p>'
    detail = '<p>Selecione um item à esquerda.</p>'
    if chosen and is_people:
        employee_rows = "".join(f'<tr><td><a href="{escape("/cadastros/vinculos?" + urlencode({"person": chosen["key"], "id": e["key"]}))}">{escape(e["registration"])}</a></td><td>{escape(e["company"])}</td><td>{escape(e["contract_type"])}</td><td>{_date(e["admission_date"])}</td><td>{_date(e["first_due_date"]) if e.get("first_due_date") else "—"} / {_date(e["second_due_date"]) if e.get("second_due_date") else "—"}</td><td>{_date(e["termination_date"])}</td></tr>' for e in sorted(chosen["employments"], key=lambda item: item.get("admission_date") or "", reverse=True))
        detail = (f'<h3>Identificação e contato</h3><dl class="registry-dl"><dt>Código(s)</dt><dd>{escape(person_codes(chosen))}</dd><dt>Nome</dt><dd>{escape(chosen["name"])}</dd><dt>CPF</dt><dd>{escape(chosen["cpf"] or "Não informado")}</dd><dt>E-mail</dt><dd>{escape(chosen["email"] or "Não informado")}</dd><dt>WhatsApp</dt><dd>{escape(chosen["whatsapp"] or "Não informado")}</dd><dt>Identificação</dt><dd>{"Cadastro de teste" if chosen.get("is_test") else "CPF validado" if chosen["key"].startswith("sib:") and chosen["verified"] else "Conferida localmente" if chosen["verified"] else "Provisória"}</dd><dt>Vínculos</dt><dd>{len(chosen["employments"])}</dd></dl><h3>Vínculos associados</h3>'
                  f'<div class="table-wrap"><table><thead><tr><th>Matrícula</th><th>Cliente</th><th>Contrato</th><th>Admissão</th><th>1º / 2º vencimento</th><th>Saída</th></tr></thead><tbody>{employee_rows}</tbody></table></div>')
        detail = f'<p class="registry-criterion">Situação atual: <strong>{escape(activity_labels[person_activity(chosen)])}</strong></p>' + detail
        detail = (f'<a class="registry-related-link" href="{escape("/cadastros/vinculos?" + urlencode({"person": chosen["key"]}))}">Ver histórico em Vínculos</a>') + detail
        if can_manage:
            detail += _render_sib_details(db, [item["key"] for item in chosen["employments"]], escape)
            detail += (f'<details><summary>Novo vínculo / recontratação</summary><form class="fields" method="post" action="/cadastros/vinculos"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="person_key" value="{escape(selected)}">'
                       '<label>Matrícula<input name="registration" maxlength="80" required></label><label>Cliente / empresa<input name="company" maxlength="160" required></label><label>Unidade<input name="unit" maxlength="160"></label><label>Tipo de contrato<select name="contract_type" required><option value="CLT">CLT</option><option value="Temporário">Temporário</option><option value="Prazo determinado">Prazo determinado</option><option value="Estágio">Estágio</option><option value="Outro">Outro</option></select></label><label>Admissão<input type="date" name="admission_date" required></label><label>1º vencimento temporário<input type="date" name="first_due_date"></label><label>2º vencimento temporário<input type="date" name="second_due_date"></label><label>Saída / demissão<input type="date" name="termination_date"></label><label>Motivo<select name="dismissal_reason_code"><option value="">Não desligado</option><option value="PEDIDO_EMPREGADO">A pedido do empregado</option><option value="SEM_JUSTA_CAUSA">Dispensa sem justa causa</option><option value="JUSTA_CAUSA">Dispensa por justa causa</option><option value="TERMINO_CONTRATO">Término de contrato</option><option value="ACORDO">Rescisão por acordo</option><option value="FALECIMENTO">Falecimento</option><option value="OUTRO">Outro</option></select></label><label>Detalhe do motivo<input name="dismissal_reason" maxlength="160"></label><button>Salvar vínculo</button></form></details>'
                       f'<details><summary>Corrigir nome com justificativa</summary><form class="fields" method="post" action="/cadastros/revisoes"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="kind" value="PERSON_NAME"><input type="hidden" name="subject_key" value="{escape(selected)}"><label>Nome<input name="value" value="{escape(chosen["name"])}" maxlength="160" required></label><label>Justificativa<input name="note" maxlength="1000" required></label><button>Registrar correção</button></form></details>')
        if can_manage:
            local_person_id = int(chosen["key"][2:]) if chosen["key"].startswith("m:") and chosen["key"][2:].isdigit() else 0
            documents = db.execute(
                "SELECT id,filename,mime_type,size_bytes,created_at FROM local_registry_documents WHERE person_id=? ORDER BY id DESC",
                (local_person_id,),
            ).fetchall() if local_person_id else []
            document_rows = "".join(
                f'<li><a href="/api/ui/person-documents/{row["id"]}">{escape(row["filename"])}</a> · {row["size_bytes"]} bytes · {escape(row["created_at"])}</li>'
                for row in documents
            )
            detail += ('<section class="registry-person-documents"><h3>Documentos do prontuário</h3>'
                       f'<ul>{document_rows}</ul>' if document_rows else
                       '<section class="registry-person-documents"><h3>Documentos do prontuário</h3><p>Nenhum documento local anexado. Os arquivos importados na rescisão também aparecem aqui.</p>')
            detail += '</section>'
    elif chosen:
        detail = '<dl class="registry-dl">' + ''.join(f'<dt>{escape(label)}</dt><dd>{escape(_date(chosen[key]) if key.endswith("_date") else chosen[key] or "—")}</dd>' for label,key in
                 (("Pessoa relacionada","name"),("Matrícula","registration"),("Cliente","company"),("Contrato","contract_type"),("Admissão","admission_date"),("Desligamento","termination_date"),("Situação","status"),("Fonte","source"))) + '</dl>'
        detail = (f'<a class="registry-related-link" href="{escape("/cadastros/vinculos?" + urlencode({"person": chosen["person_key"]}))}">Ver todos os vínculos desta pessoa</a>') + detail
        if chosen["source"] == "SIB":
            detail += '<p class="registry-criterion">Ativo pelas datas: admissão já ocorrida e desligamento ausente ou futuro. Datas insuficientes ficam em conferência.</p>'
            if can_manage:
                detail += _render_sib_details(db, [chosen["key"]], escape)
        if can_manage and chosen["source"] == "V2":
            options = "".join(f'<option value="{escape(p["key"])}">{escape(p["name"])} · {escape(p["employments"][0]["company"] if p["employments"] else "Cadastro local")}</option>' for p in sorted(people.values(), key=lambda p:normalize(p["name"])) if p["key"] != chosen["person_key"])
            detail += (f'<details><summary>Associar a outra pessoa</summary><p>Confira a identidade antes de associar um vínculo. A planilha original permanecerá intacta.</p><form class="fields" method="post" action="/cadastros/revisoes"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="kind" value="LINK_EMPLOYMENT"><input type="hidden" name="subject_key" value="{escape(selected)}"><label>Pessoa<select name="value" required><option value="">Selecione</option>{options}</select></label><label>Justificativa<input name="note" maxlength="1000" required></label><button>Registrar associação</button></form></details>')
    if chosen and history.get(selected):
        detail += '<h3>Histórico de decisões</h3><ul class="history">' + ''.join(f'<li>{escape(r["kind"])} · {escape(r["value"])} · {escape(r["actor"])} · {escape(r["created_at"])}<br>{escape(r["note"])}</li>' for r in reversed(history[selected])) + '</ul>'
    new_person = (f'<details class="card"><summary>Nova pessoa e vínculo</summary><p>O cadastro local fica disponível para a fila de prorrogações se for temporário e tiver vencimentos. Ao informar uma saída, a rescisão é aberta automaticamente.</p><form class="fields" method="post" action="/cadastros/pessoas"><input type="hidden" name="csrf" value="{escape(csrf)}"><h3>Identificação e contato</h3><label>Nome completo<input name="name" maxlength="160" required placeholder="Ex.: Pessoa Exemplo 01"></label><label>CPF<input name="cpf" inputmode="numeric" maxlength="14" placeholder="Ex.: 000.000.000-00"></label><label>E-mail<input name="email" type="email" maxlength="254" placeholder="Ex.: demo@example.com"></label><label>WhatsApp<input name="whatsapp" inputmode="tel" maxlength="20" placeholder="Ex.: 00000000000"></label><h3>Vínculo de trabalho</h3><label>Matrícula<input name="registration" maxlength="80" required placeholder="Ex.: TESTE-0001"></label><label>Empresa / cliente<input name="company" maxlength="160" required placeholder="Ex.: Empresa Exemplo Ltda."></label><label>Unidade<input name="unit" maxlength="160" placeholder="Ex.: Unidade Centro"></label><label>Tipo de contrato<select name="contract_type" required><option value="CLT">CLT</option><option value="Temporário">Temporário</option><option value="Prazo determinado">Prazo determinado</option><option value="Estágio">Estágio</option><option value="Outro">Outro</option></select></label><label>Admissão<input name="admission_date" type="date" required></label><label>1º vencimento temporário<input name="first_due_date" type="date"></label><label>2º vencimento temporário<input name="second_due_date" type="date"></label><small>Preencha os dois vencimentos para incluir um temporário em Prorrogações.</small><h3>Desligamento, se já ocorrido</h3><label>Data de desligamento<input name="termination_date" type="date"></label><label>Motivo<select name="dismissal_reason_code"><option value="">Selecione se houver saída</option><option value="PEDIDO_EMPREGADO">A pedido do empregado</option><option value="SEM_JUSTA_CAUSA">Dispensa sem justa causa</option><option value="JUSTA_CAUSA">Dispensa por justa causa</option><option value="TERMINO_CONTRATO">Término de contrato</option><option value="ACORDO">Rescisão por acordo</option><option value="FALECIMENTO">Falecimento</option><option value="OUTRO">Outro</option></select></label><label>Detalhe do motivo<input name="dismissal_reason" maxlength="160" placeholder="Observação opcional"></label><button>Salvar pessoa e vínculo</button></form></details>') if is_people and can_manage else ""
    return (f'<p>{"Cadastro lógico de pessoas e seus vínculos ao longo do tempo." if is_people else "Cada admissão representa um vínculo distinto, inclusive em uma recontratação."}</p>'
            '<p class="registry-notice">Os cadastros SIB, locais e da V2 mantêm sua origem. Pessoas SIB são agrupadas por CPF válido; confira a identidade antes de associar vínculos de outras origens.</p>'
            f'{history_heading}{new_person}{search_form}{people_groups}<div class="registry-layout"><section class="card"><h2>{title} · {len(filtered)}</h2><p>Selecione um registro para consultar detalhes e histórico.</p><div class="registry-items">{entries or "<p>Nenhum resultado.</p>"}</div>{pagination}</section>'
            f'<section class="card"><h2>Detalhes e histórico</h2>{detail}</section></div>')


def render_conflicts(db, parameters, escape, csrf, can_manage):
    conflicts, history = project_conflicts(db)
    search = (parameters.get("q") or [""])[0].strip()[:100]
    status = (parameters.get("status") or ["all"])[0]
    if status not in ("all", "PENDING", "REVIEWED", "RESOLVED", "REOPENED"):
        status = "all"
    selected = (parameters.get("id") or [""])[0][:150]
    filtered = [item for item in conflicts if (status == "all" or item["status"] == status)
                and normalize(search) in normalize(item["kind"] + " " + item["detail"])]
    entries = "".join(f'<a class="registry-item{" selected" if item["key"]==selected else ""}" href="/cadastros/conflitos?{urlencode({"q":search,"status":status,"id":item["key"]})}"><strong>{escape(item["kind"])} · {escape(item["status"])}</strong><span>{escape(item["detail"])}</span></a>' for item in filtered[:150])
    chosen = next((item for item in conflicts if item["key"] == selected), None)
    detail = '<p>Selecione um conflito à esquerda.</p>'
    if chosen:
        detail = (f'<dl class="registry-dl"><dt>Tipo</dt><dd>{escape(chosen["kind"])}</dd><dt>Descrição</dt><dd>{escape(chosen["detail"])}</dd><dt>Situação</dt><dd>{escape(chosen["status"])}</dd></dl>')
        if can_manage:
            options = ''.join(f'<option value="{value}"{" selected" if value==chosen["status"] else ""}>{label}</option>' for value,label in
                              (("PENDING","Pendente"),("REVIEWED","Revisado, pendente de correção"),("RESOLVED","Resolvido após conferência"),("REOPENED","Reabrir")))
            detail += (f'<form class="fields" method="post" action="/cadastros/revisoes"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="kind" value="CONFLICT_REVIEW"><input type="hidden" name="subject_key" value="{escape(selected)}"><label>Decisão<select name="value">{options}</select></label><label>Justificativa<input name="note" maxlength="1000" required></label><button>Registrar decisão</button></form>')
        if history.get(selected):
            detail += '<h3>Histórico de decisões</h3><ul class="history">' + ''.join(f'<li>{escape(r["value"])} · {escape(r["actor"])} · {escape(r["created_at"])}<br>{escape(r["note"])}</li>' for r in reversed(history[selected])) + '</ul>'
    statuses = ''.join(f'<option value="{value}"{" selected" if value==status else ""}>{label}</option>' for value,label in
                       (("all","Todas"),("PENDING","Pendente"),("REVIEWED","Revisado"),("RESOLVED","Resolvido"),("REOPENED","Reaberto")))
    return (f'<p>Inconsistências exigem decisão explícita; a revisão não altera as planilhas.</p><form class="row" method="get"><label>Buscar<input name="q" value="{escape(search)}" placeholder="Tipo ou descrição"></label><label>Situação<select name="status">{statuses}</select></label><button>Filtrar</button></form>'
            f'<div class="registry-layout"><section class="card"><h2>Conflitos · {len(filtered)}</h2><div class="registry-items">{entries or "<p>Nenhum conflito nesta seleção.</p>"}</div>{"<p>Mostrando os primeiros 150. Refine a busca.</p>" if len(filtered)>150 else ""}</section><section class="card"><h2>Detalhes e histórico</h2>{detail}</section></div>')

