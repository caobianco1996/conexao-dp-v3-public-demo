"""Map full registrations read through the existing SIB Bridge 6.6 API."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


_CREDENTIAL_COLUMN = re.compile(r"SENHA|PASSWORD|SECRET|TOKEN|API_?KEY|CHAVE_?API", re.IGNORECASE)
_JOB_TITLE_CATALOG_PATH = Path(__file__).with_name("sib_job_titles.json")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _code(value: Any) -> str:
    candidate = _text(value) if not isinstance(value, bool) else ""
    if not re.fullmatch(r"-?\d{1,10}", candidate):
        raise ValueError("A ficha SIB contém uma chave de cadastro inválida.")
    return str(int(candidate))


def _date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    candidate = _text(value)
    if not candidate or candidate in ("0", "00000000", "0000-00-00"):
        return ""
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    formats = ("%Y%m%d", "%d%m%Y", "%d/%m/%Y", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S")
    if re.fullmatch(r"\d{8}", candidate) and not candidate.startswith(("19", "20")):
        formats = ("%d%m%Y", "%Y%m%d", *formats[2:])
    for pattern in formats:
        try:
            return datetime.strptime(candidate, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _clean(child) for key, child in value.items() if not _CREDENTIAL_COLUMN.search(str(key))}
    if isinstance(value, list):
        return [_clean(child) for child in value]
    return value


def _digits(value: Any, length: int = 0) -> str:
    result = re.sub(r"\D", "", _text(value))
    return result.zfill(length) if result and length else result


@lru_cache(maxsize=1)
def _job_title_catalog() -> dict[tuple[str, str], str]:
    """Load SIB's local function catalog, indexed by its composite function key."""
    try:
        payload = json.loads(_JOB_TITLE_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        prefix = _text(item.get("prefix"))
        raw_code = _text(item.get("code"))
        title = _text(item.get("name"))
        if re.fullmatch(r"\d{1,4}", raw_code) and title:
            result[(prefix, str(int(raw_code)))] = title
    return result


def employment_fields(data: Mapping[str, Any], raw: Mapping[str, Any] | None = None,
                      contract_types: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep descriptions separate from SIB reference codes, including older imports."""
    raw, contract_types = raw or {}, contract_types or {}

    def code(value, *, allow_zero=False):
        text = _text(value)
        if text.isdigit():
            text = str(int(text))
        return text if text != '0' or allow_zero else ''

    contract_code = code(data.get('contractTypeCode') if _text(data.get('contractTypeCode'))
                         else raw.get('VIN_FUN'), allow_zero=True)
    intern = code(data.get('internCode') or raw.get('CODIGO_ESTAGIARIO_FUN'))
    # VIN_FUN is the confirmed modality. An optional internship number must not
    # override a modality already confirmed by the administrator.
    contract = (contract_types.get(contract_code) or ('Estágio' if intern else
                _text(data.get('contractType') or raw.get('contract_type'))))
    department = _text(data.get('department') or raw.get('DEP_FUN'))
    department_code = code(data.get('departmentCode'))
    if department.isdigit():
        department_code, department = department_code or code(department), ''
    branch = code(data.get('branchCode') or raw.get('FIL_FUN'))
    registration = code(data.get('registration') or raw.get('COD_FUN'))
    registration_code = f"{branch.zfill(2)}-{registration}" if branch and registration else registration
    job_code = code(data.get('jobCode'), allow_zero=True) or code(raw.get('COD_FUN_FUN'), allow_zero=True)
    job_title = _text(data.get('jobTitle'))
    if not job_title and job_code:
        job_title = _job_title_catalog().get((_text(raw.get('SIG_FUN_FUN')), job_code), '')
    if job_code == '0' and not job_title:
        job_code = ''
    return {'branchCode': branch, 'registrationCode': registration_code,
            'contractType': contract, 'contractTypeCode': contract_code,
            'department': department, 'departmentCode': department_code,
            'jobTitle': job_title,
            'jobCode': job_code}


def classified_employee(db, item):
    """Bind confirmed modalities to each import without changing its physical key."""
    data = dict(item['data'])
    data.update(employment_fields(data, item['raw'], dict(db.execute('SELECT code,label FROM local_sib_contract_types'))))
    return {**item, 'data': data}


def _activity(admission: str, dismissal: str, original_admission: Any, original_dismissal: Any) -> tuple[bool | None, str]:
    today = date.today().isoformat()
    if ((_text(original_admission) and not admission)
            or (_text(original_dismissal) not in ("", "0", "00000000", "0000-00-00") and not dismissal)
            or (admission and dismissal and dismissal < admission)):
        return None, "Datas em conferência"
    if dismissal and dismissal <= today:
        return False, "Desligado"
    if admission and admission > today:
        return False, "Admissão futura"
    if admission:
        return True, "Ativo por datas"
    return None, "Situação em conferência"


def map_raw_employee(payload: Mapping[str, Any], expected_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return catalog-compatible employee and associated company, never guess identity."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("raw_employee"), Mapping):
        raise ValueError("A Bridge não devolveu os campos originais da ficha SIB.")
    original = payload["raw_employee"]
    if any(key not in original for key in ("EMP_FUN", "FIL_FUN", "COD_FUN")):
        raise ValueError("A ficha não identifica empresa, filial e matrícula do SIB.")
    employer, branch, registration = (_code(original[key]) for key in ("EMP_FUN", "FIL_FUN", "COD_FUN"))
    if registration != _code(expected_id):
        raise ValueError("A matrícula da ficha SIB difere da matrícula consultada.")
    canonical = payload.get("employee")
    if not isinstance(canonical, Mapping) or _code(canonical.get("id_folha")) != registration:
        raise ValueError("Os dados da ficha SIB não correspondem à matrícula consultada.")
    if not _text(original.get("NOM_FUN") or canonical.get("name")):
        raise ValueError("O cadastro SIB não contém nome de funcionário.")
    intern_code = ""
    try:
        intern_code = _code(original.get("CODIGO_ESTAGIARIO_FUN", 0))
    except ValueError:
        # This optional source field must not invalidate an otherwise complete
        # employee record when an older Bridge omits it or returns a new format.
        pass
    is_intern = bool(intern_code and intern_code != "0")
    # EMP/FIL/COD are the identity actually returned by the Bridge. The optional
    # internship code is metadata and can change without creating a new link.
    source_id = ":".join((employer, branch, registration))
    raw = _clean(original)
    admission_value = original.get("DATA_ADM_FUN", canonical.get("admission_date"))
    dismissal_value = original.get("DATA_DEM_FUN", canonical.get("termination_date"))
    admission, dismissal = _date(admission_value), _date(dismissal_value)
    canonical_cpf, raw_cpf = _digits(canonical.get('cpf'), 11), _digits(original.get('CPF_FUN'), 11)
    canonical_admission = _date(canonical.get('admission_date'))
    if ((canonical_cpf and raw_cpf and canonical_cpf != raw_cpf)
            or (canonical_admission and admission and canonical_admission != admission)):
        raise ValueError('A ficha completa não corresponde ao CPF e à admissão do vínculo consultado.')
    active, status = _activity(admission, dismissal, admission_value, dismissal_value)
    company_value = original.get("COD_CLI_FUN", canonical.get("client_code"))
    company_id = _code(company_value) if _text(company_value) else ""
    data = {
        "sourceId": source_id, "employerCode": employer, "branchCode": branch,
        "registration": registration, "name": _text(original.get("NOM_FUN") or canonical.get("name")),
        "cpf": _digits(original.get("CPF_FUN", canonical.get("cpf")), 11),
        "companyId": company_id, "admissionDate": admission, "dismissalDate": dismissal,
        "firstDueDate": _date(original.get("DATA_VEN1_FUN", canonical.get("contract_end_date"))),
        "secondDueDate": _date(original.get("DATA_VEN2_FUN", canonical.get("extension_date"))),
        "sourceStatus": _text(original.get("STATUS_GRAV_FUN", canonical.get("status"))),
        "email": _text(original.get("EMAIL_FUN")), "phone": _text(original.get("TEL_CEL_FUN")),
        "contractType": "Estágio" if is_intern else "", "internCode": intern_code if is_intern else "",
        "contractTypeCode": _text(original.get('VIN_FUN')),
        "department": _text(canonical.get('department')),
        "jobTitle": _text(canonical.get('job_title')),
        "active": active, "status": status,
        "activityDatesValid": status != "Datas em conferência",
        "statusBasis": "Datas de admissão e desligamento do SIB",
        "bankCode": _text(original.get("BAN_CTA_BAN_FUN", canonical.get("bank_code"))),
        "bankBranch": _text(original.get("AGE_CTA_BAN_FUN", canonical.get("bank_branch"))),
        "bankBranchDigit": _text(original.get("DAG_CTA_BAN_FUN", canonical.get("bank_branch_digit"))),
        "bankAccount": _text(original.get("NUM_CTA_BAN_FUN", canonical.get("bank_account"))),
        "bankDigit": _text(original.get("DIG_CTA_BAN_FUN", canonical.get("bank_digit"))),
        "bankAccountType": _text(original.get("TIP_CTA_BAN_FUN", canonical.get("bank_account_type"))),
        "pisNis": _text(original.get("NUM_PIS_FUN", canonical.get("pis_nis"))),
        "terminationType": _text(original.get("COD_RES_FUN", canonical.get("termination_type_code"))),
        "dismissalReason": _text(canonical.get("termination_reason_source_text")),
        "dismissalReasonCode": _text(canonical.get("termination_reason_source_code")),
    }
    data.update(employment_fields(data, raw))
    employee_item = {"source_id": source_id, "data": data, "raw": raw}
    client = payload.get("client")
    if not isinstance(client, Mapping) or not company_id:
        return employee_item, None
    if _code(client.get("code")) != company_id:
        raise ValueError("A empresa devolvida pela Bridge difere da empresa vinculada ao funcionário.")
    company_raw = client.get("raw")
    if not isinstance(company_raw, Mapping):
        # A partial lookup must not replace a previously imported full company.
        return employee_item, None
    if "COD_CLI" in company_raw and _code(company_raw["COD_CLI"]) != company_id:
        raise ValueError("A ficha da empresa SIB não corresponde ao código do vínculo.")
    if not _text(company_raw.get("NOM_CLI") or client.get("legal_name")):
        return employee_item, None
    cnpj = _digits(client.get("cnpj"))
    if not cnpj and all(key in company_raw for key in ("NCGC_CLI", "ESTA_CLI", "DC_CLI")):
        parts = [_digits(company_raw[key]) for key in ("NCGC_CLI", "ESTA_CLI", "DC_CLI")]
        if all(part and len(part) <= length for part, length in zip(parts, (8, 4, 2))):
            cnpj = "".join(part.zfill(length) for part, length in zip(parts, (8, 4, 2)))
    company_data = {
        "sourceId": company_id, "code": company_id,
        "name": _text(company_raw.get("NOM_CLI") or client.get("legal_name")),
        "cnpj": cnpj, "active": None, "sourceStatus": _text(company_raw.get("SIT_CLI")),
        "address": _text(company_raw.get("END_CLI")), "addressNumber": _text(company_raw.get("NUM_END_CLI")),
        "addressComplement": _text(company_raw.get("COMPL_END_CLI")),
        "neighborhood": _text(company_raw.get("BAIRRO_CLI")), "city": _text(company_raw.get("CIDADE_CLI")),
        "state": _text(company_raw.get("UF_CLI")), "postalCode": _text(company_raw.get("CEP_CLI")),
        "email": _text(company_raw.get("EMAIL_CLI")), "phone": _text(company_raw.get("TEL_CLI")),
        "registrationCoverage": "Empresa associada a funcionário encontrado no SIB",
    }
    return employee_item, {"source_id": company_id, "data": company_data, "raw": _clean(company_raw)}
