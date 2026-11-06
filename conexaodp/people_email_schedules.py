"""Administrator-managed email delivery of scheduled local HR movements."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import tempfile
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import email_delivery, smtp_credentials
from .sib_import import source_records

try:
    _TZ = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    _TZ = timezone(timedelta(hours=-3), "America/Sao_Paulo")

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_people_email_schedules (
    id INTEGER PRIMARY KEY,
    company_key TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    send_time TEXT NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'daily' CHECK(frequency IN ('daily','weekly','monthly')),
    weekday INTEGER NOT NULL DEFAULT 0 CHECK(weekday BETWEEN 0 AND 6),
    monthday INTEGER NOT NULL DEFAULT 1 CHECK(monthday BETWEEN 1 AND 31),
    test_interval_minutes INTEGER NOT NULL DEFAULT 0 CHECK(test_interval_minutes IN (0,2)),
    test_interval_one_minute INTEGER NOT NULL DEFAULT 0 CHECK(test_interval_one_minute IN (0,1)),
    include_admissions INTEGER NOT NULL DEFAULT 1 CHECK(include_admissions IN (0,1)),
    include_dismissals INTEGER NOT NULL DEFAULT 1 CHECK(include_dismissals IN (0,1)),
    include_active INTEGER NOT NULL DEFAULT 0 CHECK(include_active IN (0,1)),
    message_body TEXT NOT NULL DEFAULT '',
    report_columns_json TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    start_date TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    updated_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_people_email_schedules_due
    ON local_people_email_schedules(active, start_date, send_time);
CREATE TABLE IF NOT EXISTS local_people_email_runs (
    id INTEGER PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES local_people_email_schedules(id) ON DELETE CASCADE,
    report_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ENVIANDO','ENVIADO','ERRO','INCERTO')),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    admissions_count INTEGER NOT NULL DEFAULT 0,
    dismissals_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    UNIQUE(schedule_id, report_date)
);
CREATE INDEX IF NOT EXISTS local_people_email_runs_recent
    ON local_people_email_runs(schedule_id, report_date DESC);
"""
_LOGGER = logging.getLogger(__name__)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_REPORT_COLUMNS = ("registration", "name", "admission", "termination", "contract", "company_code", "company_name")
_REPORT_LABELS = {
    "registration": "Matrícula", "name": "Nome do funcionário", "admission": "Admissão",
    "termination": "Desligamento", "contract": "Tipo de contrato",
    "company_code": "Código da empresa", "company_name": "Empresa",
}


def column_options() -> list[dict[str, object]]:
    """Fields available to the administrator's independent email editor."""
    from .people_reports import EMPLOYEE_COLUMNS

    return [{"key": key, "label": label, "group": group, "type": kind,
             "administrative": private, "default": key in _REPORT_COLUMNS}
            for key, label, group, kind, private, _default in EMPLOYEE_COLUMNS]


def default_report_columns() -> list[str]:
    return list(_REPORT_COLUMNS)


def _validate_report_columns(columns: object) -> list[str]:
    options = column_options()
    allowed = {item["key"] for item in options}
    if not isinstance(columns, list) or not columns:
        raise ValueError("Selecione pelo menos uma coluna para os arquivos do envio automático.")
    if len(columns) > len(options) or any(not isinstance(key, str) or key not in allowed for key in columns):
        raise ValueError("Há colunas indisponíveis no envio automático. Selecione novamente as colunas.")
    chosen = set(columns)
    return [str(item["key"]) for item in options if item["key"] in chosen]


def _schedule_report_columns(schedule: dict) -> list[str]:
    stored = schedule.get("report_columns_json")
    if not stored:
        return default_report_columns()
    try:
        columns = json.loads(stored)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("As colunas deste agendamento precisam ser selecionadas novamente.") from error
    return _validate_report_columns(columns)


def _attachment_sentence(include_active: bool, include_admissions: bool,
                         include_dismissals: bool) -> str:
    lists = []
    if include_active:
        lists.append("ativos atuais")
    if include_admissions:
        lists.append("admissões")
    if include_dismissals:
        lists.append("desligamentos")
    if not lists:
        return "Nenhuma lista foi selecionada para envio."
    if len(lists) == 1:
        return f"Segue em anexo a lista de {lists[0]} da empresa selecionada."
    joined = ", ".join(lists[:-1]) + " e " + lists[-1]
    return f"Seguem em anexo as listas de {joined} da empresa selecionada."


def _greeting_for_time(send_time: str) -> str:
    hour = int(str(send_time or "08:00").split(":", 1)[0])
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"


def _update_greeting(message: str, send_time: str) -> str:
    lines = str(message or "").split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^\s*(?:bom dia|boa tarde|boa noite)\b", line, re.IGNORECASE):
            prefix = re.match(r"^(\s*)", line).group(1)
            lines[index] = prefix + _greeting_for_time(send_time) + ","
        break
    return "\n".join(lines)


def _default_message(include_active: bool, include_admissions: bool,
                     include_dismissals: bool, send_time: str = "08:00") -> str:
    return (f"{_greeting_for_time(send_time)},\n\n"
            "Espero que esteja bem.\n\n"
            f"{_attachment_sentence(include_active, include_admissions, include_dismissals)}\n\n"
            "Para mais informações, sigo à disposição.\n\n"
            "Atenciosamente,")


def _matches_day(frequency: str, weekday: int, monthday: int, day: date) -> bool:
    if frequency == "weekly":
        return day.weekday() == weekday
    if frequency == "monthly":
        return day.day == min(monthday, calendar.monthrange(day.year, day.month)[1])
    return True


def _next_datetime(send_time: str, frequency: str = "daily", weekday: int = 0,
                   monthday: int = 1, now: datetime | None = None,
                   not_before: date | None = None) -> datetime:
    current = now or datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    hour, minute = map(int, send_time.split(":"))
    first_day = max(current.date(), not_before or current.date())
    for offset in range(64):
        day = first_day + timedelta(days=offset)
        if not _matches_day(frequency, weekday, monthday, day):
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=_TZ)
        if candidate >= current:
            return candidate
    raise ValueError("Não foi possível calcular a próxima data do agendamento.")


def _next_start(send_time: str, now: datetime | None = None, frequency: str = "daily",
                weekday: int = 0, monthday: int = 1) -> str:
    return _next_datetime(send_time, frequency, weekday, monthday, now).date().isoformat()


def _report_window(frequency: str, scheduled_day: date) -> tuple[date, date]:
    end = scheduled_day - timedelta(days=1)
    if frequency == "weekly":
        return end - timedelta(days=6), end
    if frequency == "monthly":
        first_of_this_month = scheduled_day.replace(day=1)
        end = first_of_this_month - timedelta(days=1)
        return end.replace(day=1), end
    return end, end


def _test_interval_minutes(schedule) -> int:
    try:
        interval = int(schedule["test_interval_minutes"] or 0)
    except (KeyError, IndexError, TypeError):
        interval = 0
    try:
        one_minute = int(schedule["test_interval_one_minute"] or 0)
    except (KeyError, IndexError, TypeError):
        one_minute = 0
    if interval == 2:
        return 2
    if one_minute == 1:
        return 1
    return 0


def _next_test_datetime(now: datetime | None = None, interval_minutes: int = 1) -> datetime:
    current = now or datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    interval = max(1, int(interval_minutes))
    minutes_until_next = interval - (current.minute % interval)
    return current.replace(second=0, microsecond=0) + timedelta(minutes=minutes_until_next)


def validate(payload: dict[str, object]) -> dict[str, object]:
    company_key = str(payload.get("companyKey") or "").strip()
    raw_recipients = payload.get("recipientEmails", payload.get("recipientEmail", ""))
    if isinstance(raw_recipients, (list, tuple)):
        recipient_parts = [str(item).strip() for item in raw_recipients if str(item).strip()]
    else:
        recipient_parts = [item.strip() for item in re.split(r"[,;\s]+", str(raw_recipients or "")) if item.strip()]
    recipients = list(dict.fromkeys(item.lower() for item in recipient_parts))
    report_columns = _validate_report_columns(payload.get("reportColumns", default_report_columns()))
    send_time = str(payload.get("sendTime") or "").strip()
    frequency = str(payload.get("frequency") or "daily").strip().lower()
    try:
        weekday = int(payload.get("weekday", 0))
        monthday = int(payload.get("monthday", 1))
    except (TypeError, ValueError) as error:
        raise ValueError("Escolha o dia do envio semanal ou mensal.") from error
    admissions = bool(payload.get("includeAdmissions"))
    dismissals = bool(payload.get("includeDismissals"))
    active = bool(payload.get("includeActive"))
    test_every_minute = bool(payload.get("testEveryMinute") or frequency == "test_1m")
    test_every_two_minutes = bool(payload.get("testEveryTwoMinutes") or frequency == "test_2m")
    if test_every_minute and test_every_two_minutes:
        raise ValueError("Escolha apenas um intervalo de teste.")
    if frequency in ("test_1m", "test_2m"):
        frequency = "daily"
    message_body = str(payload.get("messageBody") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not company_key or len(company_key) > 200:
        raise ValueError("Escolha uma empresa para o envio.")
    if not recipients or len(recipients) > 50 or len(", ".join(recipients)) > 13000:
        raise ValueError("Informe de 1 a 50 endereços de e-mail para receber o relatório.")
    if any(len(address) > 254 or any(char in address for char in "\r\n\0") or not _EMAIL.fullmatch(address)
           for address in recipients):
        raise ValueError("Confira os endereços de e-mail. Separe cada endereço por vírgula, ponto e vírgula ou linha.")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", send_time):
        raise ValueError("Informe um horário válido para o envio.")
    if frequency not in ("daily", "weekly", "monthly"):
        raise ValueError("Escolha uma frequência diária, semanal ou mensal.")
    if (test_every_minute or test_every_two_minutes) and frequency != "daily":
        raise ValueError("O envio de teste não pode ser combinado com outra frequência.")
    if frequency == "weekly" and not 0 <= weekday <= 6:
        raise ValueError("Escolha um dia da semana válido.")
    if frequency == "monthly" and not 1 <= monthday <= 31:
        raise ValueError("Escolha um dia do mês entre 1 e 31.")
    if not admissions and not dismissals and not active:
        raise ValueError("Marque ativos, admissões, desligamentos ou mais de uma opção.")
    if not message_body:
        message_body = _default_message(active, admissions, dismissals, send_time)
    message_body = _update_greeting(message_body, send_time)
    if len(message_body) > 4000 or any(ord(char) < 32 and char not in "\n\t" for char in message_body):
        raise ValueError("A mensagem deve ter até 4.000 caracteres e não pode conter caracteres de controle.")
    return {"companyKey": company_key, "recipientEmails": recipients, "recipientEmail": ", ".join(recipients),
            "sendTime": send_time, "frequency": frequency, "weekday": weekday, "monthday": monthday,
            "testEveryMinute": test_every_minute,
            "testEveryTwoMinutes": test_every_two_minutes,
            "includeAdmissions": admissions, "includeDismissals": dismissals, "includeActive": active,
            "reportColumns": report_columns,
            "messageBody": message_body,
            "active": bool(payload.get("active", True))}


def company_options(db, user):
    # The schedule editor only needs the SIB company catalog. Calling the full
    # People Analytics pipeline here rebuilt every link and report alongside
    # the page's own analytics request, which made the screen appear stuck.
    options = []
    for source_id, data in source_records(db, "companies"):
        name = str(data.get("name") or f"Empresa SIB {source_id}").strip()
        code = str(data.get("code") or source_id).strip()
        options.append({"value": "sib:" + source_id, "label": f"{code} · {name}" if code else name})
    return sorted(options, key=lambda item: item["label"].casefold())


def save(db, user, payload: dict[str, object], companies: list[dict[str, object]]) -> int:
    settings = validate(payload)
    valid_keys = {item["value"] for item in companies}
    if settings["companyKey"] not in valid_keys:
        raise ValueError("A empresa escolhida não está disponível na base local da V3.")
    is_test_schedule = settings["testEveryMinute"] or settings["testEveryTwoMinutes"]
    test_interval_minutes = 2 if settings["testEveryTwoMinutes"] else 0
    test_interval_one_minute = int(settings["testEveryMinute"])
    report_columns_json = json.dumps(settings["reportColumns"], ensure_ascii=False, separators=(",", ":"))
    start_date = (datetime.now(_TZ).date().isoformat() if is_test_schedule else
                  _next_start(str(settings["sendTime"]), frequency=str(settings["frequency"]),
                              weekday=int(settings["weekday"]), monthday=int(settings["monthday"])))
    schedule_id = int(payload.get("id") or 0)
    if schedule_id:
        result = db.execute("""UPDATE local_people_email_schedules SET company_key=?,recipient_email=?,send_time=?,
            frequency=?,weekday=?,monthday=?,test_interval_minutes=?,test_interval_one_minute=?,include_admissions=?,include_dismissals=?,include_active=?,message_body=?,report_columns_json=?,active=?,start_date=?,updated_by=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""", (settings["companyKey"], settings["recipientEmail"], settings["sendTime"],
            settings["frequency"], settings["weekday"], settings["monthday"], test_interval_minutes, test_interval_one_minute,
            int(settings["includeAdmissions"]), int(settings["includeDismissals"]), int(settings["includeActive"]), settings["messageBody"], report_columns_json, int(settings["active"]),
            start_date, user["id"], schedule_id))
        if not result.rowcount:
            raise LookupError("Agendamento não encontrado.")
        return schedule_id
    return db.execute("""INSERT INTO local_people_email_schedules
        (company_key,recipient_email,send_time,frequency,weekday,monthday,test_interval_minutes,test_interval_one_minute,include_admissions,include_dismissals,include_active,message_body,report_columns_json,active,start_date,created_by,updated_by)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (settings["companyKey"], settings["recipientEmail"], settings["sendTime"],
        settings["frequency"], settings["weekday"], settings["monthday"], test_interval_minutes, test_interval_one_minute,
        int(settings["includeAdmissions"]), int(settings["includeDismissals"]), int(settings["includeActive"]), settings["messageBody"], report_columns_json, int(settings["active"]),
        start_date, user["id"], user["id"])).lastrowid


def set_active(db, user, schedule_id: int, active: bool) -> None:
    row = db.execute("SELECT send_time,frequency,weekday,monthday,test_interval_minutes,test_interval_one_minute FROM local_people_email_schedules WHERE id=?", (schedule_id,)).fetchone()
    if not row:
        raise LookupError("Agendamento não encontrado.")
    start_date = (datetime.now(_TZ).date().isoformat() if _test_interval_minutes(row) else
                  _next_start(row["send_time"], frequency=row["frequency"], weekday=row["weekday"],
                              monthday=row["monthday"])) if active else None
    db.execute("""UPDATE local_people_email_schedules SET active=?,start_date=COALESCE(?,start_date),
        updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (int(active), start_date, user["id"], schedule_id))


def list_schedules(db, companies: list[dict[str, object]]) -> list[dict[str, object]]:
    labels = {item["value"]: item["label"] for item in companies}
    now = datetime.now(_TZ)
    results = []
    for row in db.execute("""SELECT s.*,
        r.report_date AS last_report_date,r.status AS last_status,r.finished_at AS last_finished_at,
        r.admissions_count AS last_admissions,r.dismissals_count AS last_dismissals,r.summary AS last_summary
        FROM local_people_email_schedules s LEFT JOIN local_people_email_runs r
          ON r.id=(SELECT id FROM local_people_email_runs WHERE schedule_id=s.id ORDER BY report_date DESC,id DESC LIMIT 1)
        ORDER BY s.active DESC,s.send_time,s.id"""):
        item = dict(row)
        recipients = [address.strip() for address in re.split(r"[,;\s]+", item["recipient_email"]) if address.strip()]
        test_interval = _test_interval_minutes(item)
        is_test_schedule = bool(test_interval)
        candidate = (_next_test_datetime(now, test_interval) if is_test_schedule else
                     _next_datetime(item["send_time"], item.get("frequency", "daily"),
                                    int(item.get("weekday", 0)), int(item.get("monthday", 1)),
                                    now, date.fromisoformat(item["start_date"])))
        item["companyLabel"] = labels.get(item["company_key"], item["company_key"])
        item["recipients"] = recipients
        item["frequency"] = item.get("frequency", "daily")
        item["weekday"] = int(item.get("weekday", 0))
        item["monthday"] = int(item.get("monthday", 1))
        item["testEveryMinute"] = test_interval == 1
        item["testEveryTwoMinutes"] = test_interval == 2
        item["includeAdmissions"] = bool(item["include_admissions"])
        item["includeDismissals"] = bool(item["include_dismissals"])
        item["includeActive"] = bool(item["include_active"])
        item["reportColumns"] = _schedule_report_columns(item)
        item["active"] = bool(item["active"])
        item["nextRunAt"] = candidate.isoformat(timespec="minutes") if item["active"] else ""
        results.append(item)
    return results


def _claim(db_path: Path, schedule_id: int, report_key: date | str, now: datetime) -> dict | None:
    from .database import connect
    report_date = report_key.isoformat() if isinstance(report_key, date) else str(report_key)
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM local_people_email_schedules WHERE id=? AND active=1", (schedule_id,)).fetchone()
        is_test_schedule = bool(row and _test_interval_minutes(row))
        if (not row or row["start_date"] > now.date().isoformat()
                or (not is_test_schedule and not _matches_day(row["frequency"], int(row["weekday"]), int(row["monthday"]), now.date()))):
            return None
        existing = db.execute("SELECT 1 FROM local_people_email_runs WHERE schedule_id=? AND report_date=?",
                              (schedule_id, report_date)).fetchone()
        if existing:
            return None
        run_id = db.execute("""INSERT INTO local_people_email_runs(schedule_id,report_date,status,started_at)
            VALUES(?,?,'ENVIANDO',?)""", (schedule_id, report_date, now.isoformat(timespec="seconds"))).lastrowid
        return {**dict(row), "run_id": run_id}


def _report_data(db, user, schedule: dict, report_start: date, report_end: date,
                 kind: str, label: str):
    from .people_analytics import get_data

    active_reference_date = datetime.now(_TZ).date()
    # Ativos são uma fotografia do estado atual; admissões e desligamentos
    # respeitam o intervalo definido pela frequência do agendamento.
    kind_end = active_reference_date if kind == "active" else report_end
    params = {
        "period": ["custom"], "start": [report_start.isoformat()], "end": [kind_end.isoformat()],
        "companies": [schedule["company_key"]], "report": [kind], "reportPage": ["1"],
        "reportColumnsSet": ["1"], "reportColumns": _schedule_report_columns(schedule),
    }
    data = get_data(db, params, user=user, report_export_all=True)
    report = data["reports"]["selected"]
    if report["pending"]:
        raise ValueError(report["pending"])
    report.update({
        "title": label,
        "description": f"Lista de {label.lower()} da empresa conforme o agendamento.",
        "activeSnapshotDate": active_reference_date.isoformat() if kind == "active" else "",
        "movementPeriodIncluded": kind != "active",
    })
    if kind == "active":
        report["notes"] = [*report.get("notes", []),
                           f"Ativos atuais: situação vigente em {active_reference_date.strftime('%d/%m/%Y')}, sem filtro pelo período do relatório."]
    else:
        report["notes"] = [*report.get("notes", []),
                           f"{label}: eventos ocorridos no período selecionado, conforme datas da base local da V3."]
    # Keep schedule dates in the criteria; the active snapshot date is listed
    # separately and is not limited by the movement period.
    data["filters"]["start"] = report_start.isoformat()
    data["filters"]["end"] = report_end.isoformat()
    return data


def _deliver(db_path: Path, schedule: dict, report_start: date, report_end: date,
             settings: dict, password: str, user: dict) -> None:
    from .database import connect
    run_id = schedule["run_id"]
    status = "ERRO"
    counts = {"active": 0, "admissions": 0, "dismissals": 0}
    summary = "O envio não foi concluído. Confira a configuração e o histórico."
    try:
        kinds = []
        if schedule["include_active"]:
            kinds.append(("active", "Ativos atuais", "ativos_atuais"))
        if schedule["include_admissions"]:
            kinds.append(("admissions", "Admissões", "admissoes"))
        if schedule["include_dismissals"]:
            kinds.append(("dismissals", "Desligamentos", "desligamentos"))
        if not kinds:
            raise ValueError("Selecione ao menos uma lista para o envio.")
        attachments = []
        company = None
        from .people_report_exports import create_workbook
        with connect(db_path) as db:
            for kind, label, filename_prefix in kinds:
                data = _report_data(db, user, schedule, report_start, report_end, kind, label)
                report = data["reports"]["selected"]
                counts[kind] = len(report["rows"])
                if company is None:
                    company = next((item for item in data["options"]["companies"]
                                    if item["value"] == schedule["company_key"]), None)
                if kind == "active":
                    filename = f"{filename_prefix}_{report['activeSnapshotDate']}.xlsx"
                else:
                    filename = f"{filename_prefix}_{report_start.isoformat()}_{report_end.isoformat()}.xlsx"
                # Use the current service user's private temp folder. The
                # database export folder can be owned by an elevated process.
                with tempfile.TemporaryDirectory(prefix="email-lista-") as temporary:
                    workbook_path = Path(temporary) / filename
                    create_workbook(data, workbook_path)
                    content = workbook_path.read_bytes()
                attachments.append({
                    "filename": filename,
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "content": content,
                })
        has_movements = bool(schedule["include_admissions"] or schedule["include_dismissals"])
        company_label = company["label"] if company else str(schedule["company_key"])
        period = report_start.strftime("%d/%m/%Y")
        if report_start != report_end:
            period += " a " + report_end.strftime("%d/%m/%Y")
        is_test_schedule = bool(schedule.get("test_interval_one_minute") or schedule.get("test_interval_minutes") == 2)
        greeting_time = (datetime.now(_TZ).strftime("%H:%M") if is_test_schedule
                         else str(schedule.get("send_time") or "08:00"))
        body = _update_greeting(str(schedule.get("message_body") or _default_message(
            bool(schedule["include_active"]), bool(schedule["include_admissions"]),
            bool(schedule["include_dismissals"]), greeting_time)), greeting_time)
        if has_movements:
            subject = f"Relatórios de pessoal · {company_label} · {period}"
        else:
            subject = f"Funcionários ativos · {company_label} · {datetime.now(_TZ).strftime('%d/%m/%Y')}"
        recipients = [address.strip() for address in re.split(r"[,;\s]+", schedule["recipient_email"]) if address.strip()]
        email_delivery.send_email(settings, password, recipients, subject, body, attachments,
                                  show_recipients=True, reply_to=str(settings["senderEmail"]))
        status = "ENVIADO"
        summary = (f"{counts['active']} ativo(s), {counts['admissions']} admissão(ões) e "
                   f"{counts['dismissals']} desligamento(s); {len(attachments)} arquivo(s) Excel.")
    except email_delivery.SMTPDeliveryError as error:
        status = "INCERTO" if error.possibly_accepted else "ERRO"
        summary = str(error)[:300]
    except (ValueError, RuntimeError, OSError) as error:
        summary = str(error)[:300] or summary
    except Exception:
        summary = "O envio não foi concluído. Confira os logs locais do serviço."
    finished = datetime.now(_TZ).isoformat(timespec="seconds")
    with connect(db_path) as db:
        db.execute("""UPDATE local_people_email_runs SET status=?,finished_at=?,admissions_count=?,
            dismissals_count=?,summary=? WHERE id=?""",
            (status, finished, counts["admissions"], counts["dismissals"], summary, run_id))


class Scheduler:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="V3-email-agendamentos-movimentacoes")

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self):
        from .database import connect
        while not self.stop_event.is_set():
            try:
                now = datetime.now(_TZ)
                today = now.date().isoformat()
                with connect(self.database) as db:
                    schedules = [dict(row) for row in db.execute("""SELECT * FROM local_people_email_schedules
                        WHERE active=1 AND (test_interval_one_minute=1 OR test_interval_minutes=2 OR (start_date<=? AND send_time<=?)) ORDER BY send_time,id""",
                        (today, now.strftime("%H:%M")))]
                    smtp = db.execute("SELECT * FROM local_smtp_settings WHERE id=1").fetchone()
                    # CSRF tokens belong to sessions, not users. This scheduler
                    # only needs the active administrator identity for report access.
                    admin = db.execute("SELECT id,role FROM users WHERE active=1 AND role='ADMIN' ORDER BY id LIMIT 1").fetchone()
                if schedules and smtp and smtp["active"] and admin:
                    password = smtp_credentials.get_password()
                    if password:
                        settings = email_delivery.validate_settings({
                            "connectionName": smtp["connection_name"], "smtpHost": smtp["smtp_host"],
                            "smtpPort": smtp["smtp_port"], "security": smtp["security"],
                            "username": smtp["username"], "senderEmail": smtp["sender_email"],
                            "active": smtp["active"], "timeoutSeconds": smtp["timeout_seconds"],
                        })
                        for schedule in schedules:
                            if self.stop_event.is_set():
                                break
                            test_interval = _test_interval_minutes(schedule)
                            test_schedule = bool(test_interval)
                            if test_interval == 2 and now.minute % 2:
                                continue
                            if not test_schedule and schedule["send_time"] > now.strftime("%H:%M"):
                                continue
                            if not test_schedule and not _matches_day(schedule.get("frequency", "daily"), int(schedule.get("weekday", 0)),
                                                                       int(schedule.get("monthday", 1)), now.date()):
                                continue
                            report_frequency = "daily" if test_schedule else schedule.get("frequency", "daily")
                            report_start, report_end = _report_window(report_frequency, now.date())
                            if test_schedule:
                                interval_slot = (now.minute // test_interval) * test_interval
                                interval_tag = ":2m" if test_interval == 2 else ""
                                report_key = now.strftime("%Y-%m-%d@%H:") + f"{interval_slot:02d}{interval_tag}"
                            else:
                                report_key = report_end
                            claimed = _claim(self.database, schedule["id"], report_key, now)
                            if claimed:
                                _deliver(self.database, claimed, report_start, report_end, settings, password, dict(admin))
            except Exception:
                # Keep the service alive; the next poll retries unfinished work
                # without duplicating a schedule/date already present in runs.
                _LOGGER.exception("Falha no ciclo do agendador de e-mails de pessoas.")
            self.stop_event.wait(20)
