"""Portal local baseado no servidor HTTP da Gestão RH, com a identidade da V2.

É uma fundação verificável, não a migração funcional completa da V2.
"""

from __future__ import annotations

import html
import base64
import binascii
import io
import json
import calendar
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from .analytics import render as render_analytics
from .database import backup_database, connect
from .security import new_session, session_hash, verify_password
from .ui_api import home_data as next_home_data, processes_data as next_processes_data, calendar_data as next_calendar_data
from . import manuals as manuals_service
from . import sector as sector_service, sector_assets
from .messages import MessageError, get_messages_data, mutate_message
from . import chat as chat_service
from . import clicksign as clicksign_service
from . import voxfy as voxfy_service
from . import integrations as integrations_service
from . import sib_import as sib_import_service
from . import sib_scan as sib_scan_service
from . import sib_refresh as sib_refresh_service
from . import sib_auto_refresh as sib_auto_refresh_service
from . import sib_absences as sib_absences_service
from . import sib_discovery as sib_discovery_service
from . import sib_issues as sib_issues_service
from .sib_runtime import lease_active
from . import integration_secrets as integration_secret_store
from . import email_delivery as email_delivery_service
from . import smtp_credentials as smtp_password_store
from . import people_email_schedules as people_email_schedules_service
from . import rescissions as rescissions_service
from . import permissions as access_service
from . import registry_documents as registry_documents_service
from . import operational_http
from . import import_staging as import_staging_service
from .extensions import queue_data as temporary_extension_queue, get_extension as temporary_extension_get, simulate_action as temporary_extension_action
from .extensions import sync_registry_employment as sync_temporary_registry_employment
from .tables import export_csv as export_people_csv, render as render_people_tables
from .registry import project as project_registry, project_conflicts, render as render_registry, render_conflicts
from .companies import PROFILE_FIELDS, catalog as company_catalog, profile as company_profile, render as render_companies
from .registry_api import get_registry_data, get_registry_detail, get_registry_status
from .people_analytics import get_data as get_people_analytics
from .people_report_exports import ReportExports
from .ops_ui import render_chat, render_calendar, render_lots, render_calculators, render_taxes, render_home, render_home_cases, render_processes
from .calculators import calculate as calculate_payroll, CALCULATORS as PAYROLL_CALCULATORS
from .lot_planning import propose_lots
from . import lot_planning_api as lot_plans_api

STATIC = Path(__file__).resolve().parents[1] / "static"
CLICKSIGN_GLOBAL_KEY = "__global__"
# O ambiente Windows deste projeto não inclui a base IANA `tzdata`. A zona
# civil de São Paulo em 2026 usa UTC−03:00; usar o offset evita falha de carga.
SAO_PAULO = timezone(timedelta(hours=-3), "America/Sao_Paulo")


def _sao_paulo_today(now: datetime | None = None) -> str:
    instant = now or datetime.now(SAO_PAULO)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=SAO_PAULO)
    return instant.astimezone(SAO_PAULO).date().isoformat()


def _calculator_expiration_date(now: datetime | None = None) -> str:
    local_date = date.fromisoformat(_sao_paulo_today(now))
    return (local_date + timedelta(days=4)).isoformat()


REFERENCE_MANUAL_ASSETS = Path(__file__).resolve().parents[1] / "referencia_v2_site" / "public" / "secullum-illustrations"
KIND_LABELS = {"GERAL": "Geral", "ADMISSAO": "Admissões", "RESCISAO": "Rescisões", "PRORROGACAO": "Prorrogações", "AFASTAMENTO": "Afastamentos"}
STATUS_LABELS = {"ABERTO": "Aberto", "EM_ANDAMENTO": "Em andamento", "CONCLUIDO": "Concluído", "CANCELADO": "Cancelado"}
NAVIGATION = json.loads((STATIC / "navigation.json").read_text(encoding="utf-8"))
GROUPS = [("Principal", [(NAVIGATION["home"]["href"], NAVIGATION["home"]["label"])])] + [
    (group["label"], [(item["href"], item["label"]) for item in group["items"]])
    for group in NAVIGATION["groups"]
]
PAGE_LABELS = {url: (group, label) for group, links in GROUPS for url, label in links}
# Legacy destinations remain addressable without duplicating sidebar entries.
PAGE_LABELS.update({
    "/analytics/atualizar": ("Cadastros e dados", "Importação de arquivos"),
    "/analytics/admissoes": ("People Analytics", "Admissões"),
    "/analytics/desligamentos": ("People Analytics", "Desligamentos"),
    "/analytics/contratos": ("People Analytics", "Contratos ativos"),
    "/analytics-pro/painel": ("People Analytics", "Análise por empresa"),
    "/analytics-pro/avancadas": ("People Analytics", "Análise por empresa"),
})
LOCAL_CONTENT = {"/central-dp/processos": "PROCESSO", "/central-dp/mensagens": "MENSAGEM", "/central-dp/manuais": "MANUAL"}
FLOW = {"admissoes": "ADMISSAO", "rescisoes": "RESCISAO", "prorrogacoes": "PRORROGACAO", "afastamentos": "AFASTAMENTO"}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _navigation_active(path: str, href: str) -> bool:
    if href == NAVIGATION["home"]["href"]:
        return path in ("/", "/central-dp", href)
    return path == href or path.startswith(href + "/")


def _navigation_icon(name: str, class_name: str = "global-link-icon") -> str:
    parts = []
    for shape in NAVIGATION["icons"].get(name, []):
        tag = shape.get("tag")
        if tag not in {"path", "rect", "circle", "line", "polyline"}:
            continue
        attributes = " ".join(f'{esc(key)}="{esc(value)}"' for key, value in shape.get("attrs", {}).items()
                              if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", key))
        parts.append(f"<{tag} {attributes}/>")
    return (f'<svg class="{esc(class_name)}" viewBox="0 0 24 24" width="19" height="19" '
            'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{"".join(parts)}</svg>')


def _sidebar_link(item: dict, path: str, user) -> str:
    active = _navigation_active(path, item["href"])
    classes = "active" if active else ""
    if item["href"] == NAVIGATION["home"]["href"]:
        classes = ("global-home-link " + classes).strip()
    current = ' aria-current="page"' if active else ""
    if item["href"] == "/analytics/atualizar" and user["role"] == "ADMIN":
        badge = "<b>Novo</b>"
    elif item["href"] == "/central-dp/chat":
        badge = '<b class="sidebar-chat-badge" data-chat-unread hidden></b>'
    else:
        badge = ""
    return (f'<a href="{esc(item["href"])}" class="{classes}" data-nav-label="{esc(item["label"])}"{current}>'
            f'{_navigation_icon(item["icon"])}<span>{esc(item["label"])}</span>{badge}</a>')


def _sidebar_navigation(path: str, user) -> str:
    parts = ['<label class="global-nav-search"><span class="visually-hidden">Buscar página</span>'
             '<input id="global-nav-search" type="search" placeholder="Buscar página…" '
             'autocomplete="off" aria-label="Buscar página"></label>',
             _sidebar_link(NAVIGATION["home"], path, user)]
    for group in NAVIGATION["groups"]:
        active = any(_navigation_active(path, item["href"]) for item in group["items"])
        opened = " open" if active else ""
        chevron = ('<svg class="global-group-chevron" viewBox="0 0 24 24" width="14" height="14" '
                   'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
                   '<path d="m9 5 7 7-7 7"/></svg>')
        parts.append(
            f'<details class="global-nav-group" data-nav-group="{esc(group["id"])}" '
            f'data-active="{str(active).lower()}"{opened}>'
            f'<summary>{_navigation_icon(group["icon"])}<span>{esc(group["label"])}</span>{chevron}</summary>'
            f'<div class="global-nav-links">{"".join(_sidebar_link(item, path, user) for item in group["items"])}</div>'
            '</details>')
    parts.append('<p class="global-nav-empty" hidden>Nenhuma página encontrada.</p>')
    return "".join(parts)


def _advance_day_from_profile(profile_data):
    if not isinstance(profile_data, dict):
        return {"advanceStatus": "NO_RULE", "advanceDay": "", "advanceRaw": ""}
    raw = str(profile_data.get("advanceDay") or "").strip()
    normalized = re.sub(r"[\s_-]+", " ", raw).casefold()
    if normalized in ("nao possui", "não possui", "sem adiantamento", "nao tem", "não tem", "inexistente"):
        return {"advanceStatus": "NONE", "advanceDay": "", "advanceRaw": raw}
    match = re.fullmatch(r"(?:dia\s*)?(\d{1,2})", normalized)
    if match and 1 <= int(match.group(1)) <= 31:
        return {"advanceStatus": "HAS_DAY", "advanceDay": str(int(match.group(1))), "advanceRaw": raw}
    if raw:
        return {"advanceStatus": "UNKNOWN", "advanceDay": "", "advanceRaw": raw}
    return {"advanceStatus": "NO_RULE", "advanceDay": "", "advanceRaw": ""}


def _calculator_companies(db):
    items = []
    for company in company_catalog(db):
        profile_data, profile_source = company_profile(db, company["key"])
        advance = _advance_day_from_profile(profile_data)
        items.append({"key": company["key"], "name": company["name"], "source": company["source"],
                      "advanceStatus": advance["advanceStatus"], "advanceDay": advance["advanceDay"],
                      "advanceRaw": advance["advanceRaw"], "profileSource": profile_source})
    return items


def _prepare_calculator(db, calc_id, raw_inputs):
    valid_ids = {item[0] for item in PAYROLL_CALCULATORS} | {"advance"}
    if calc_id not in valid_ids:
        raise ValueError("Calculadora não reconhecida.")
    if not isinstance(raw_inputs, dict):
        raise ValueError("Informe os campos da calculadora.")
    inputs = {str(key): value for key, value in raw_inputs.items() if not str(key).startswith("_")}
    if calc_id == "advance":
        key = str(inputs.get("companyKey") or "")
        if key:
            selected = next((item for item in _calculator_companies(db) if item["key"] == key), None)
            if not selected:
                raise ValueError("A empresa selecionada não está disponível no cadastro atual. Atualize a lista e tente novamente.")
            inputs["_verifiedCompanyPolicy"] = {"advanceStatus": selected["advanceStatus"],
                "advanceDay": selected["advanceDay"], "companyName": selected["name"], "source": selected["profileSource"]}
        else:
            inputs["_verifiedCompanyPolicy"] = {}
    if calc_id in ("dsr", "dsr-discount") and inputs.get("city"):
        city_value = str(inputs["city"])
        if "|" not in city_value:
            raise ValueError("Localidade inválida.")
        state, city = city_value.split("|", 1)
        start, end = str(inputs.get("startDate") or ""), str(inputs.get("endDate") or "")
        try:
            date.fromisoformat(start); date.fromisoformat(end)
        except ValueError as error:
            raise ValueError("Informe as datas do período para carregar os feriados locais.") from error
        holidays = [row[0] for row in db.execute("SELECT DISTINCT day FROM local_calendar_holidays WHERE day BETWEEN ? AND ? AND ((state='' AND city='') OR (state=? AND city=?))", (start, end, state, city))]
        holidays.extend(filter(None, re.split(r"[;,\n]+", str(inputs.get("holidays") or ""))))
        inputs["holidays"] = ";".join(sorted(set(holidays)))
    wants_tax_tables = calc_id in ("inss", "irrf") or (calc_id == "net" and str(inputs.get("applyPublishedTaxes") or "NO").upper() == "YES")
    if wants_tax_tables:
        from .tax_tables import parameters_for_version, resolve
        competence = str(inputs.get("competence") or "")
        mode = str(inputs.get("mode") or "MONTHLY")
        if calc_id in ("inss", "net"):
            income_type = "EMPLOYEE_13TH" if mode == "THIRTEENTH" else "EMPLOYEE_MONTHLY"
            inss_version = resolve(db, "INSS", competence, "EMPLOYEE", income_type)
            if not inss_version:
                raise ValueError("Não há tabela INSS publicada para esta competência e categoria.")
            inss_parameters = parameters_for_version(inss_version)
            if mode not in inss_parameters.get("supportedModes", []):
                raise ValueError("O modo de cálculo não está habilitado nesta versão da tabela INSS.")
            inputs["_taxParameters"] = inss_parameters
            inputs["_taxVersion"] = inss_version["id"]
            inputs["_taxSource"] = inss_version["source"]
            inputs["_taxSourceUrl"] = inss_version["source_url"]
        if calc_id == "irrf" or calc_id == "net":
            payment_date = str(inputs.get("paymentDate") or "")
            irrf_version = resolve(db, "IRRF", payment_date, "EMPLOYEE", "SALARY_MONTHLY")
            if not irrf_version:
                raise ValueError("Não há tabela IRRF publicada para esta data de pagamento e tipo de rendimento.")
            irrf_parameters = parameters_for_version(irrf_version)
            if mode not in irrf_parameters.get("supportedModes", []):
                raise ValueError("O modo de cálculo não está habilitado nesta versão da tabela IRRF.")
            if calc_id == "irrf":
                inputs["_taxParameters"] = irrf_parameters
                inputs["_taxVersion"] = irrf_version["id"]
                inputs["_taxSource"] = irrf_version["source"]
                inputs["_taxSourceUrl"] = irrf_version["source_url"]
            else:
                inputs["_irrfParameters"] = irrf_parameters
                inputs["_irrfVersion"] = irrf_version["id"]
                inputs["_irrfSource"] = irrf_version["source"]
                inputs["_irrfSourceUrl"] = irrf_version["source_url"]
    return inputs


def _calculator_result_json(db, calc_id, raw_inputs):
    inputs = _prepare_calculator(db, calc_id, raw_inputs)
    result = calculate_payroll(calc_id, inputs)
    if inputs.get("_taxSource"):
        result.setdefault("memory", []).insert(1, f"Fonte da tabela publicada: {inputs['_taxSource']}; versão local: {inputs.get('_taxVersion', '—')}.")
    if inputs.get("_irrfSource"):
        result.setdefault("memory", []).insert(2, f"Fonte IRRF publicada: {inputs['_irrfSource']}; versão local: {inputs.get('_irrfVersion', '—')}.")
    return inputs, result


def _monthly_competence_after(competence: str) -> str:
    year, month = map(int, competence.split("-"))
    return f"{year+1:04d}-01" if month == 12 else f"{year:04d}-{month+1:02d}"


def _ensure_monthly_case(db, rule_id, competence, template, owner_id, actor_id, checklist):
    template=dict(template)
    rule=db.execute("SELECT day_of_month,active FROM local_case_recurrence_rules WHERE rule_id=?",(rule_id,)).fetchone()
    if not rule or not rule["active"] or db.execute("SELECT 1 FROM local_case_recurrence_instances WHERE rule_id=? AND competence=?",(rule_id,competence)).fetchone():
        return None
    year,month=map(int,competence.split("-")); day=min(int(rule["day_of_month"]),calendar.monthrange(year,month)[1]); due=f"{year:04d}-{month:02d}-{day:02d}"
    case_id=db.execute("INSERT INTO process_cases(kind,title,reference,due_date,created_by,owner_id,description,client_id,unit_id,priority,recurrence_rule_id,competence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (template["kind"],template["title"],f"Competência {competence}"[:100],due,actor_id,owner_id,template.get("description") or "",template.get("client_id"),template.get("unit_id"),template.get("priority") or "NORMAL",rule_id,competence)).lastrowid
    db.execute("INSERT INTO local_case_recurrence_instances(rule_id,competence,case_id,due_date) VALUES(?,?,?,?)",(rule_id,competence,case_id,due))
    for position,item in enumerate(checklist):
        db.execute("INSERT INTO case_checklist(case_id,title,required,position) VALUES(?,?,?,?)",(case_id,item["title"],int(item["required"]),position))
    db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'RECORRENCIA_GERADA',?)",(case_id,actor_id,f"Instância mensal {competence} gerada uma única vez."))
    event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,due_at,starts_at,category,notes,description,priority,created_by) VALUES(?,?,?,?,'Caso DP',?,?,?,?)",
        (template["title"],due,due,due+"T09:00",str(case_id),template.get("description") or "",template.get("priority") or "NORMAL",actor_id)).lastrowid
    db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)",("local-case:"+str(case_id),event_id))
    return case_id


class Portal(ThreadingHTTPServer):
    daemon_threads = True
    # A page opens several authenticated API calls together (chat, navigation,
    # registry). Keep enough pending connections for those bursts.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], database: Path):
        self.database = database
        self.report_exports = ReportExports(database)
        self.login_attempts: dict[str, list[float]] = {}
        super().__init__(address, Handler)
        self.sib_auto_refresh = sib_auto_refresh_service.Scheduler(database)
        self.sib_auto_refresh.start()
        self.people_email_scheduler = people_email_schedules_service.Scheduler(database)
        self.people_email_scheduler.start()
        from .operational_runtime import Scheduler as OperationalScheduler
        self.operational_scheduler = OperationalScheduler(database)
        self.operational_scheduler.start()
        from .people_analytics_cache import warm as warm_analytics
        warm_analytics(database)

    def server_close(self):
        self.operational_scheduler.stop()
        self.sib_auto_refresh.stop()
        self.people_email_scheduler.stop()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Portal

    def handle(self):
        try:
            super().handle()
        except PermissionError as error:
            self.respond_json({'error': str(error)}, 403)
            self.close_connection = True
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Navigation or a cancelled fetch can close the client mid-response.
            self.close_connection = True

    def _owner_id(self, raw: str):
        if raw == "":
            return None
        if not raw.isdigit():
            return False
        with connect(self.server.database) as db:
            return int(raw) if db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (int(raw),)).fetchone() else False

    @staticmethod
    def _valid_date(raw: str) -> bool:
        if not raw:
            return True
        try:
            date.fromisoformat(raw)
            return len(raw) == 10
        except ValueError:
            return False

    def log_message(self, format: str, *args: object) -> None:
        # Paths and form data can contain sensitive values; the base logger is disabled.
        return

    def respond(self, body: str, status: int = 200, headers: tuple[tuple[str, str], ...] = ()) -> None:
        if getattr(self, "_registry_json_action", False):
            message = html.unescape(re.sub(r"<[^>]*>", " ", body))
            message = " ".join(message.split())
            self.respond_json({"error": message or "Não foi possível concluir a ação."}, status)
            return
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def respond_json(self, value, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _messages_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 1_000_000 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict):
            self.respond_json({"error": "Conteúdo inválido."}, 400)
            return
        if not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        try:
            with connect(self.server.database) as db:
                result = mutate_message(db, user, payload)
            self.respond_json(result, 200 if payload.get("action") in ("update_model", "archive_model", "mark_read", "archive_alert") else 201)
        except MessageError as error:
            self.respond_json({"error": str(error)}, error.status)
        except sqlite3.IntegrityError:
            self.respond_json({"error": "Esta alteração entrou em conflito com outro salvamento. Atualize a lista antes de tentar novamente."}, 409)

    def _calendar_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 100_000 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict):
            self.respond_json({"error": "Conteúdo inválido."}, 400)
            return
        if not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        if user["role"] not in ("ADMIN", "GESTOR"):
            self.respond_json({"error": "Seu perfil pode consultar o calendário, mas não alterar os registros."}, 403)
            return

        action = str(payload.get("action") or "")
        try:
            with connect(self.server.database) as db:
                if action == "save_relation":
                    day = str(payload.get("day") or "")
                    state = str(payload.get("state") or "")
                    note = str(payload.get("note") or "").strip()
                    client_id = str(payload.get("clientId") or "")
                    if not self._valid_date(day) or not day or state not in ("", "YES", "NO", "OTHER") or len(note) > 500:
                        raise ValueError("Marcação inválida.")
                    if client_id:
                        valid_client = (db.execute("SELECT 1 FROM v2_source_rows WHERE table_name='clients' AND (source_id=? OR json_extract(payload,'$.id')=?)", (client_id, client_id)).fetchone()
                                        or (client_id.startswith("local-client:") and client_id.removeprefix("local-client:").isdigit() and db.execute("SELECT 1 FROM local_clients WHERE id=? AND active=1", (int(client_id.removeprefix("local-client:")),)).fetchone()))
                        if not valid_client or len(client_id) > 100:
                            raise ValueError("Cliente inválido para esta relação.")
                    scope_type, scope_key = ("CLIENT", client_id) if client_id else ("GENERAL", "")
                    previous = db.execute("SELECT state,note FROM local_bank_relations WHERE day=? AND scope_type=? AND scope_key=?", (day, scope_type, scope_key)).fetchone()
                    old_state = previous["state"] if previous else None
                    old_note = previous["note"] if previous else ""
                    if not previous:
                        for imported in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days' AND json_extract(payload,'$.day')=?", (day,)):
                            try: imported_item = json.loads(imported[0])
                            except (TypeError, ValueError): continue
                            imported_scope = str(imported_item.get("scope_type") or "GENERAL")
                            imported_client = str(imported_item.get("client_id") or "")
                            if (scope_type == "GENERAL" and imported_scope == "GENERAL") or (scope_type == "CLIENT" and imported_scope == "CLIENT" and imported_client == client_id):
                                imported_state = str(imported_item.get("state") or "").strip().upper()
                                old_state = {"SIM": "YES", "TRUE": "YES", "NAO": "NO", "NÃO": "NO"}.get(imported_state, imported_state if imported_state in ("YES", "NO", "OTHER") else None)
                                old_note = str(imported_item.get("note") or "")
                                break
                    if payload.get("quick"):
                        note = old_note
                    db.execute("INSERT INTO local_bank_relation_history(day,scope_type,scope_key,old_state,new_state,old_note,new_note,actor_id) VALUES(?,?,?,?,?,?,?,?)",
                               (day, scope_type, scope_key, old_state, state or None, old_note, note, user["id"]))
                    db.execute("INSERT INTO local_bank_relations(day,scope_type,scope_key,state,note,updated_by) VALUES(?,?,?,?,?,?) ON CONFLICT(day,scope_type,scope_key) DO UPDATE SET state=excluded.state,note=excluded.note,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",
                               (day, scope_type, scope_key, state or None, note, user["id"]))
                    result = {"ok": True, "day": day, "state": state, "note": note, "scopeType": scope_type, "scopeKey": scope_key}
                elif action == "save_month_note":
                    month = str(payload.get("month") or "")
                    body = str(payload.get("body") or "").strip()
                    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or len(body) > 3000:
                        raise ValueError("Observação mensal inválida.")
                    db.execute("INSERT INTO local_calendar_month_notes(month,body,updated_by) VALUES(?,?,?) ON CONFLICT(month) DO UPDATE SET body=excluded.body,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",
                               (month, body, user["id"]))
                    result = {"ok": True, "month": month, "body": body}
                elif action == "create_event":
                    title = str(payload.get("title") or "").strip()
                    day = str(payload.get("date") or "")
                    start_time = str(payload.get("time") or "09:00")
                    category = str(payload.get("category") or "Geral / interno").strip()
                    description = str(payload.get("description") or "").strip()
                    priority = str(payload.get("priority") or "NORMAL")
                    recurrence = str(payload.get("recurrence") or "NONE")
                    try: occurrences = int(payload.get("occurrences") or 1)
                    except (TypeError, ValueError): occurrences = 0
                    if not (1 <= len(title) <= 160 and self._valid_date(day) and day and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time)
                            and len(category) <= 80 and len(description) <= 4000 and priority in ("LOW", "NORMAL", "HIGH", "URGENT")
                            and recurrence in ("NONE", "DAILY", "WEEKLY", "MONTHLY") and 1 <= occurrences <= 24 and (recurrence != "NONE" or occurrences == 1)):
                        raise ValueError("Confira título, data, horário e repetição do evento.")
                    series_id = secrets.token_urlsafe(12) if occurrences > 1 else None
                    first_day = date.fromisoformat(day)
                    created = []
                    for index in range(occurrences):
                        current = first_day
                        if recurrence == "DAILY": current += timedelta(days=index)
                        elif recurrence == "WEEKLY": current += timedelta(days=index * 7)
                        elif recurrence == "MONTHLY":
                            month_index = first_day.month - 1 + index
                            year, month_number = first_day.year + month_index // 12, month_index % 12 + 1
                            current = first_day.replace(year=year, month=month_number, day=min(first_day.day, calendar.monthrange(year, month_number)[1]))
                        current_text = current.isoformat()
                        event_id = db.execute("INSERT INTO local_calendar_events(title,due_date,starts_at,due_at,category,notes,description,priority,series_id,recurrence_type,recurrence_index,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (title, current_text, f"{current_text}T{start_time}", current_text, category, description, description, priority, series_id, recurrence, index + 1, user["id"])).lastrowid
                        db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "CRIADO", f"Evento criado; série {series_id or 'único'}"))
                        created.append(event_id)
                    result = {"ok": True, "ids": created}
                elif action == "edit_event":
                    try: event_id = int(payload.get("eventId"))
                    except (TypeError, ValueError): raise ValueError("Evento inválido.")
                    title = str(payload.get("title") or "").strip()
                    day = str(payload.get("date") or "")
                    start_time = str(payload.get("time") or "09:00")
                    category = str(payload.get("category") or "").strip()
                    description = str(payload.get("description") or "").strip()
                    priority = str(payload.get("priority") or "NORMAL")
                    if not (1 <= len(title) <= 160 and self._valid_date(day) and day and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time)
                            and len(category) <= 80 and len(description) <= 4000 and priority in ("LOW", "NORMAL", "HIGH", "URGENT")):
                        raise ValueError("Confira título, data, horário e categoria do evento.")
                    row = db.execute("SELECT e.archived,m.source_id FROM local_calendar_events e LEFT JOIN local_calendar_source_map m ON m.local_event_id=e.id WHERE e.id=?", (event_id,)).fetchone()
                    if not row: raise LookupError("Evento não encontrado.")
                    if row["source_id"] or row["archived"]: raise ValueError("Este evento está vinculado a um registro importado ou arquivado e não pode ser editado.")
                    db.execute("UPDATE local_calendar_events SET title=?,due_date=?,due_at=?,starts_at=?,category=?,description=?,notes=?,priority=? WHERE id=?", (title, day, day, f"{day}T{start_time}", category, description, description, priority, event_id))
                    db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "EDITADO", title))
                    result = {"ok": True, "eventId": event_id}
                elif action in ("checklist_toggle", "checklist_add"):
                    try: event_id = int(payload.get("eventId"))
                    except (TypeError, ValueError): raise ValueError("Evento inválido.")
                    row = db.execute("SELECT e.archived,m.source_id FROM local_calendar_events e LEFT JOIN local_calendar_source_map m ON m.local_event_id=e.id WHERE e.id=?", (event_id,)).fetchone()
                    if not row: raise LookupError("Evento não encontrado.")
                    if row["source_id"] or row["archived"]: raise ValueError("O checklist deste registro vinculado deve ser alterado na tela de origem.")
                    if action == "checklist_toggle":
                        try: item_id = int(payload.get("itemId"))
                        except (TypeError, ValueError): raise ValueError("Item de checklist inválido.")
                        item = db.execute("SELECT title,done FROM local_calendar_checklist WHERE id=? AND event_id=?", (item_id, event_id)).fetchone()
                        if not item: raise LookupError("Item de checklist não encontrado.")
                        done = 0 if item["done"] else 1
                        db.execute("UPDATE local_calendar_checklist SET done=? WHERE id=?", (done, item_id))
                        db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "CHECKLIST", f"{item['title']}: {'concluído' if done else 'reaberto'}"))
                        result = {"ok": True, "eventId": event_id, "itemId": item_id, "done": bool(done)}
                    else:
                        title = str(payload.get("title") or "").strip()
                        if not 1 <= len(title) <= 180: raise ValueError("O item deve ter entre 1 e 180 caracteres.")
                        item_id = db.execute("INSERT INTO local_calendar_checklist(event_id,title,created_by) VALUES(?,?,?)", (event_id, title, user["id"])).lastrowid
                        db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "CHECKLIST_ADICIONADO", title))
                        result = {"ok": True, "eventId": event_id, "itemId": item_id}
                elif action in ("event_status", "archive_event"):
                    try: event_id = int(payload.get("eventId"))
                    except (TypeError, ValueError): raise ValueError("Evento inválido.")
                    row = db.execute("SELECT e.status,e.archived,m.source_id FROM local_calendar_events e LEFT JOIN local_calendar_source_map m ON m.local_event_id=e.id WHERE e.id=?", (event_id,)).fetchone()
                    if not row: raise LookupError("Evento não encontrado.")
                    if row["source_id"] or row["archived"]:
                        raise ValueError("Este evento está vinculado a um registro importado ou caso e não pode ser alterado por aqui.")
                    if action == "event_status":
                        status = "PENDENTE" if row["status"] == "CONCLUIDO" else "CONCLUIDO"
                        db.execute("UPDATE local_calendar_events SET status=? WHERE id=?", (status, event_id))
                        db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "STATUS", status))
                        result = {"ok": True, "eventId": event_id, "status": status}
                    else:
                        db.execute("UPDATE local_calendar_events SET archived=1 WHERE id=?", (event_id,))
                        db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)", (event_id, user["id"], "ARQUIVADO", "Evento arquivado (dados preservados)."))
                        result = {"ok": True, "eventId": event_id}
                else:
                    self.respond_json({"error": "Ação de calendário indisponível."}, 404)
                    return
            self.respond_json(result, 201 if action == "create_event" else 200)
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except LookupError as error:
            self.respond_json({"error": str(error)}, 404)
        except sqlite3.IntegrityError:
            self.respond_json({"error": "O registro mudou durante o salvamento. Atualize o calendário e tente novamente."}, 409)

    def _chat_payload(self):
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 32 * 1024 * 1024 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return None
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return None
        return user, payload

    def _rescissions_post(self) -> None:
        authenticated = self._chat_payload()
        if not authenticated:
            return
        user, payload = authenticated
        if not access_service.can(user, "rescissions.manage"):
            self.respond_json({"error": "Acesso somente para consulta."}, 403)
            return
        try:
            with connect(self.server.database) as db:
                if payload.get('action') != 'create_demo':
                    rescissions_service.get_case(db, int(payload.get('caseId') or 0), date.today(), user=user)
                elif user['role'] != 'ADMIN':
                    raise PermissionError('Somente administradores podem criar exemplos.')
                result = rescissions_service.mutate(db, payload, int(user["id"]))
                if result.get("case"):
                    result["case"] = rescissions_service.get_case(db, result["case"]["id"], date.today(), user=user)
            status = 201 if payload.get("action") == "create_demo" and result.get("created") else 200
            self.respond_json({**result, "csrf": user["csrf_token"]}, status)
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except LookupError as error:
            self.respond_json({"error": str(error)}, 404)
        except sqlite3.IntegrityError:
            self.respond_json({"error": "O caso foi atualizado por outra ação. Recarregue e tente novamente."}, 409)
        except sqlite3.DatabaseError:
            self.respond_json({"error": "Não foi possível registrar a ação local de rescisão."}, 500)

    def _rescissions_real_email_post(self, case_id: int) -> None:
        authenticated = self._chat_payload()
        if not authenticated:
            return
        user, payload = authenticated
        if not access_service.can(user, "rescissions.manage") or not access_service.can(user, "communications.send"):
            self.respond_json({"error": "Seu perfil não pode enviar documentos de rescisão."}, 403)
            return
        message = str(payload.get("message") or "").strip()[:5000]
        raw_ids = payload.get("documentIds")
        request_key = str(payload.get("requestId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{24,80}", request_key):
            self.respond_json({"error": "Atualize a página para iniciar uma tentativa identificada de envio."}, 400)
            return
        if not message:
            self.respond_json({"error": "Escreva a mensagem antes de enviar."}, 400)
            return
        if not isinstance(raw_ids, list):
            self.respond_json({"error": "Selecione os documentos que serão anexados."}, 400)
            return
        try:
            password = smtp_password_store.get_password()
            if not password:
                self.respond_json({"error": "A senha SMTP ainda não está salva. Cadastre-a em Central DP · E-mail de teste para habilitar os envios."}, 400)
                return
            selected_ids = {int(value) for value in raw_ids}
            if len(selected_ids) != len(raw_ids):
                raise ValueError("A seleção contém documentos repetidos ou inválidos.")
            with connect(self.server.database) as db:
                smtp_row = db.execute("SELECT * FROM local_smtp_settings WHERE id=1").fetchone()
                if not smtp_row:
                    self.respond_json({"error": "Configure e salve a conexão SMTP antes do envio."}, 400)
                    return
                if not smtp_row["active"]:
                    self.respond_json({"error": "Ative a conexão SMTP antes do envio."}, 400)
                    return
                settings = email_delivery_service.validate_settings({
                    "connectionName": smtp_row["connection_name"], "smtpHost": smtp_row["smtp_host"],
                    "smtpPort": smtp_row["smtp_port"], "security": smtp_row["security"],
                    "username": smtp_row["username"], "senderEmail": smtp_row["sender_email"],
                    "active": smtp_row["active"], "timeoutSeconds": smtp_row["timeout_seconds"],
                })
                rescissions_service.get_case(db, case_id, date.today(), user=user)
                case, document_names, attachments = rescissions_service.get_email_delivery_payload(
                    db, case_id, selected_ids, self.server.database.parent / "private-registry-documents",
                )
            recipient = str(case.get("email") or "").strip()
            if case["is_test"] and recipient.casefold() != email_delivery_service.TEST_ADDRESS.casefold():
                self.respond_json({"error": "O e-mail do cadastro de teste deve apontar para a caixa de teste configurada."}, 400)
                return
            if not recipient:
                self.respond_json({"error": "O cadastro desta pessoa não tem e-mail. Atualize o cadastro antes de enviar."}, 400)
                return
            safe_name = " ".join(str(case.get("name") or "Funcionário").split())[:160]
            prefix = "[TESTE V3] " if case["is_test"] else ""
            subject = f"{prefix}Documentos da rescisão · {safe_name}"
            body = (
                f"Olá, {safe_name}.\n\n{message}\n\n"
                f"Documentos anexados:\n" + "\n".join(f"- {name}" for name in document_names)
            )
            if case["is_test"]:
                body += "\n\nEste é um envio real para validar a entrega SMTP da Central DP V3. O cadastro do funcionário é fictício."
            protocol = f"RSC-{case_id}-{secrets.token_hex(6).upper()}"
            with connect(self.server.database) as db:
                db.execute('BEGIN IMMEDIATE')
                current = rescissions_service._case_row(db, case_id)
                if not current or current['status'] == 'CANCELADO' or current.get('source_snapshot_json') != case.get('source_snapshot_json'):
                    raise ValueError('O desligamento mudou durante a preparação. Confira os dados antes de enviar.')
                rescissions_service._source_ready(db, current)
                previous = db.execute(
                    "SELECT status,protocol FROM local_rescission_real_delivery_events WHERE request_key=? AND case_id=?",
                    (request_key, case_id),
                ).fetchone()
                if previous:
                    if previous["status"] == "ENVIADO":
                        self.respond_json({"ok": True, "sent": True, "csrf": user["csrf_token"],
                                           "message": f"Esta tentativa já foi aceita pelo servidor SMTP. Protocolo {previous['protocol']}."})
                    else:
                        self.respond_json({"error": "Esta tentativa já foi registrada. Confira o histórico antes de iniciar outra."}, 409)
                    return
                pending = db.execute(
                    "SELECT 1 FROM local_rescission_real_delivery_events WHERE case_id=? AND status='ENVIANDO' LIMIT 1",
                    (case_id,),
                ).fetchone()
                if pending:
                    self.respond_json({"error": "Existe um envio em andamento ou de resultado incerto. Confira o histórico antes de reenviar."}, 409)
                    return
                attempt_id = int(db.execute(
                    """INSERT INTO local_rescission_real_delivery_events
                       (case_id,status,target,message,documents_json,protocol,request_key,created_by)
                       VALUES(?,'ENVIANDO',?,?,?,?,?,?)""",
                    (case_id, recipient, message, json.dumps(document_names, ensure_ascii=False), protocol, request_key, user["id"]),
                ).lastrowid)
                db.execute('UPDATE local_rescission_real_delivery_events SET evidence_json=? WHERE id=?',
                           (json.dumps({'requirementIds': sorted(selected_ids)}), attempt_id))
            try:
                email_delivery_service.send_email(settings, password, recipient, subject, body, attachments)
            except (ValueError, email_delivery_service.SMTPDeliveryError) as error:
                safe_error = str(error)[:300]
                uncertain = isinstance(error, email_delivery_service.SMTPDeliveryError) and error.possibly_accepted
                with connect(self.server.database) as db:
                    db.execute("UPDATE local_rescission_real_delivery_events SET status=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                               ("ENVIANDO" if uncertain else "FALHOU", safe_error, attempt_id))
                    rescissions_service._event(db, case_id, "EMAIL_REAL_RESULTADO_INCERTO" if uncertain else "EMAIL_REAL_FALHOU", int(user["id"]),
                                               protocol=protocol, target=recipient, error=safe_error)
                    current_case = rescissions_service.get_case(db, case_id, date.today(), user=user)
                status = 502 if isinstance(error, email_delivery_service.SMTPDeliveryError) else 400
                self.respond_json({"error": safe_error, "case": current_case, "csrf": user["csrf_token"]}, status)
                return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_rescission_real_delivery_events SET status='ENVIADO',finished_at=CURRENT_TIMESTAMP WHERE id=?",
                           (attempt_id,))
                db.executemany(
                    "UPDATE local_rescission_requirements SET status='ENTREGUE',updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND case_id=?",
                    [(int(user["id"]), value, case_id) for value in selected_ids],
                )
                current_status = db.execute("SELECT status FROM local_rescission_cases WHERE id=?", (case_id,)).fetchone()
                if current_status and current_status["status"] not in ("CANCELADO", "CONCLUIDO"):
                    if case["is_test"]:
                        pending = db.execute("SELECT count(*) FROM local_rescission_requirements WHERE case_id=? AND code='TRCT' AND mandatory=1 AND status NOT IN ('ENTREGUE','DISPENSADO')", (case_id,)).fetchone()[0]
                    else:
                        pending = db.execute("SELECT count(*) FROM local_rescission_requirements WHERE case_id=? AND mandatory=1 AND status NOT IN ('ENTREGUE','DISPENSADO')", (case_id,)).fetchone()[0]
                    new_status = "CONCLUIDO" if not pending else "PRONTO_PARA_ENVIO"
                    db.execute("UPDATE local_rescission_cases SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, case_id))
                rescissions_service._event(db, case_id, "EMAIL_REAL_ENVIADO", int(user["id"]),
                                           protocol=protocol, target=recipient, documents=document_names)
                updated_case = rescissions_service.get_case(db, case_id, date.today(), user=user)
            self.respond_json({
                "ok": True, "sent": True, "case": updated_case, "csrf": user["csrf_token"],
                "message": f"O servidor SMTP aceitou o e-mail para o endereço cadastrado, com {len(document_names)} documento(s). Confirme o recebimento na caixa do destinatário. Protocolo {protocol}.",
            })
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except smtp_password_store.SMTPPasswordStoreError as error:
            self.respond_json({"error": str(error)}, 503)
        except LookupError as error:
            self.respond_json({"error": str(error)}, 404)
        except OSError:
            self.respond_json({"error": "Não foi possível ler um dos documentos selecionados."}, 500)
        except sqlite3.IntegrityError:
            self.respond_json({"error": "Tentativa de envio já registrada. Atualize o histórico antes de reenviar."}, 409)
        except sqlite3.DatabaseError:
            self.respond_json({"error": "Não foi possível registrar a tentativa de envio na trilha da rescisão."}, 500)

    def _rescissions_upload_post(self, case_id: int, requirement_id: int) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        if not access_service.can(user, "rescissions.manage") or not access_service.can(user, "documents.upload"):
            self.respond_json({"error": "Seu perfil não pode anexar documentos de rescisão."}, 403)
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > rescissions_service.MAX_DOCUMENT_BYTES:
            self.respond_json({"error": "O arquivo deve ter conteúdo e no máximo 10 MB."}, 400)
            return
        filename = unquote(self.headers.get("X-File-Name", ""))
        mime_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.respond_json({"error": "O upload foi interrompido. Tente novamente."}, 400)
            return
        try:
            with connect(self.server.database) as db:
                rescissions_service.get_case(db, case_id, date.today(), user=user)
                result = rescissions_service.upload_document(
                    db, case_id, requirement_id, int(user["id"]), filename, mime_type, raw,
                    self.server.database.parent / "private-registry-documents",
                )
                if result.get('case'):
                    result['case'] = rescissions_service.get_case(db, case_id, date.today(), user=user)
            self.respond_json({**result, "csrf": user["csrf_token"], "canManage": True}, 201)
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except LookupError as error:
            self.respond_json({"error": str(error)}, 404)
        except OSError:
            self.respond_json({"error": "Não foi possível guardar o arquivo no prontuário privado."}, 500)
        except sqlite3.DatabaseError:
            self.respond_json({"error": "Não foi possível registrar o documento no prontuário."}, 500)

    def _lot_plans_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 1_000_000 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict):
            self.respond_json({"error": "Conteúdo inválido."}, 400)
            return
        if not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        try:
            with connect(self.server.database) as db:
                # Serialize logical-duplicate and idempotency checks with writes.
                db.execute("BEGIN IMMEDIATE")
                result = lot_plans_api.mutate(db, user, payload, lot_plans_api.EXPIRATION_MODE)
            self.respond_json(result, 200 if payload.get("action") in ("calculate", "update", "pin", "delete", "save_rule") else 201)
        except lot_plans_api.LotPlanError as error:
            self.respond_json({"error": str(error), **error.details}, error.status)
        except sqlite3.IntegrityError:
            self.respond_json({"error": "O planejamento ou a regra mudou durante o salvamento. Atualize os dados e tente novamente."}, 409)
        except (ValueError, TypeError, KeyError, OverflowError):
            self.respond_json({"error": "Os parâmetros do planejamento são inválidos. Revise os dados informados."}, 400)

    def _calculators_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 256_000 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict):
            self.respond_json({"error": "Conteúdo inválido."}, 400)
            return
        if not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        action = str(payload.get("action") or "calculate")
        try:
            with connect(self.server.database) as db:
                if action == "pin":
                    try: saved_id = int(payload.get("id"))
                    except (TypeError, ValueError): raise ValueError("Cálculo inválido.")
                    row = db.execute("SELECT pinned FROM local_calculations WHERE id=? AND created_by=?", (saved_id, user["id"])).fetchone()
                    if not row:
                        self.respond_json({"error": "Cálculo não encontrado nesta conta."}, 404)
                        return
                    pinned = not bool(row["pinned"])
                    db.execute("UPDATE local_calculations SET pinned=? WHERE id=? AND created_by=?", (int(pinned), saved_id, user["id"]))
                    self.respond_json({"ok": True, "id": saved_id, "pinned": pinned})
                    return
                calc_id = str(payload.get("calculatorId") or "")
                raw_inputs = payload.get("inputs")
                if action not in ("calculate", "save"):
                    self.respond_json({"error": "Ação de calculadora indisponível."}, 404)
                    return
                inputs, calculation = _calculator_result_json(db, calc_id, raw_inputs)
                if action == "calculate":
                    self.respond_json({"result": calculation})
                    return
                title = str(payload.get("title") or "").strip()[:120]
                if not title:
                    descriptor = next((item for item in PAYROLL_CALCULATORS if item[0] == calc_id), None)
                    label = "Adiantamento salarial" if calc_id == "advance" else descriptor[1] if descriptor else "Cálculo DP"
                    competence = str(inputs.get("competence") or "")
                    title = f"{label}{' · ' + competence if competence else ''}"
                cursor = db.execute("INSERT INTO local_calculations(title,calculator_id,inputs_json,result_json,expires_at,created_by) VALUES(?,?,?,?,?,?)",
                    (title, calc_id, json.dumps(inputs, ensure_ascii=False), json.dumps(calculation, ensure_ascii=False), _calculator_expiration_date(), user["id"]))
                self.respond_json({"ok": True, "id": cursor.lastrowid, "title": title, "result": calculation}, 201)
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except (sqlite3.DatabaseError, TypeError, KeyError, OverflowError):
            self.respond_json({"error": "Não foi possível concluir este cálculo local. Revise os campos e tente novamente."}, 500)

    def _chat_post(self):
        auth = self._chat_payload()
        if not auth: return
        user, payload = auth
        path = urlsplit(self.path).path
        try:
            with connect(self.server.database) as db:
                if path == "/api/chat/profile":
                    result = {}
                    if "avatarId" in payload:
                        result["avatarId"] = chat_service.set_profile_avatar(db, user["id"], payload.get("avatarId"))
                    if "moodId" in payload:
                        result["moodId"] = chat_service.set_profile_mood(db, user["id"], payload.get("moodId"))
                elif path == "/api/chat/direct":
                    conversation_id = chat_service.create_direct(db, user["id"], payload.get("userId"))
                    result = {"id": conversation_id}
                elif path == "/api/chat/groups":
                    conversation_id = chat_service.create_group(db, user["id"], payload.get("memberIds"), payload.get("title"), str(payload.get("requestId") or ""))
                    result = {"id": conversation_id}
                else:
                    match = re.fullmatch(r"/api/chat/conversations/([^/]+)/(messages|read)", path)
                    if not match: raise chat_service.ChatError("Rota indisponível.", 404)
                    conversation_id, action = match.groups()
                    if action == "read":
                        result = {"unreadTotal": chat_service.mark_read(db,user["id"],conversation_id,payload.get("messageId"))}
                    else:
                        message_id = chat_service.send_message(db,user["id"],conversation_id,payload,self.server.database.parent/"private-chat")
                        result = {"id":message_id}
            self.respond_json(result,201)
        except chat_service.ChatError as error:
            self.respond_json({"error":error.message},error.status)
        except sqlite3.IntegrityError:
            self.respond_json({"error":"A solicitação entrou em conflito. Atualize e tente novamente."},409)
        except OSError:
            self.respond_json({"error":"Não foi possível guardar o anexo. A mensagem não foi enviada."},500)

    def redirect(self, path: str, cookie: str | None = None) -> None:
        if getattr(self, "_registry_json_action", False):
            self.respond_json({"ok": True, "redirect": path})
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"conexaodp_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def principal(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            token = jar.get("conexaodp_session")
        except Exception:
            return None
        if not token or len(token.value) > 128:
            return None
        with connect(self.server.database) as db:
            row = db.execute("SELECT u.id,u.name,u.email,u.role,s.csrf_token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (session_hash(token.value), int(time.time()))).fetchone()
            return access_service.enrich_user(db, row)

    def _access_guard(self, path, *, write=False):
        permission = access_service.required_permission(path, write)
        user = self.principal() if permission or write else None
        if not user:
            return False  # Existing route authentication handles anonymous requests.
        readonly_write = write and user['role'] == 'CONSULTA' and not path.startswith(('/api/chat', '/api/profile', '/sair'))
        if readonly_write or (permission and not access_service.can(user, permission)):
            if path.startswith('/api/'):
                self.respond_json({'error': 'Seu perfil não permite esta operação.'}, 403)
            else:
                self.page('Acesso restrito', '<section class="card"><h2>Acesso restrito</h2><p>Seu perfil não permite esta operação.</p><a href="/central-dp/inicio">Voltar ao início</a></section>', user, 403)
            return True
        if not write and access_service.scope(user) != 'ALL' and path.startswith(('/cadastros/', '/analytics/', '/analytics-pro/')):
            self.respond_json({'error': 'Use a interface principal para consultar este recorte.', 'url': 'http://localhost:8794'+self.path}, 403)
            return True
        return False

    def page(self, title: str, content: str, user=None, status: int = 200) -> None:
        sidebar = ""
        trail = ""
        script = ""
        if user:
            path = urlsplit(self.path).path
            sidebar = (
                '<button class="global-menu-backdrop" type="button" aria-label="Fechar menu" hidden></button>'
                '<aside id="global-navigation" class="global-sidebar" aria-label="Navegação principal">'
                '<div class="global-brand"><a href="/central-dp/inicio" aria-label="Conexão DP — início"><img src="/static/logo.svg" alt="Conexão Empregos"></a>'
                '<button class="global-mobile-close" type="button" aria-label="Fechar menu">×</button></div>'
                '<div class="global-snapshot is-idle" data-sib-status aria-live="polite"><i aria-hidden="true"></i><div><span>Consulta de cadastros SIB</span><strong>Verificando SIB…</strong></div></div>'
                f'<nav class="global-nav-scroll" aria-label="Seções da aplicação">{_sidebar_navigation(path, user)}</nav>'
                f'<footer><div class="global-footer-info"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V6c3 0 6-1.5 8-3 2 1.5 5 3 8 3z"/><path d="m9 12 2 2 4-4"/></svg><div><strong>Base local compartilhada</strong><span>Dados armazenados em SQLite</span><span>Versão V3 Python</span></div></div><details class="global-account"><summary>Minha conta · {esc(user["name"])}</summary><form action="/sair" method="post"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><button class="logout" type="submit">Sair</button></form></details></footer>'
                '</aside>'
            )
            group_name = next((group for group, links in GROUPS
                               if any(_navigation_active(path, href) for href, _label in links)), "Central DP")
            if path.startswith("/trabalho/"):
                group_name = PAGE_LABELS["/central-dp/casos"][0]
            trail = (f'<header class="global-topbar"><div class="global-topbar-title"><button class="global-menu-trigger" type="button" aria-label="Abrir menu" aria-expanded="false" aria-controls="global-navigation"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 5h16M4 12h16M4 19h16"/></svg></button><div><span>{esc(group_name)}</span><h1>{esc(title)}</h1></div></div>'
                     f'<div class="global-top-actions"><form method="post" action="/sair"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><button class="switch-account">Trocar conta</button></form>'
                     '<div class="quick-calc-wrap"><button class="top-icon-button quick-calc-toggle" type="button" aria-label="Abrir calculadora" title="Abrir calculadora" aria-expanded="false" aria-controls="quick-calculator"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="16" height="20" x="4" y="2" rx="2"/><path d="M8 6h8M16 14v4M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M8 18h.01M12 18h.01"/></svg></button><section id="quick-calculator" class="quick-calc" aria-label="Calculadora" hidden><header><strong>Calculadora</strong><button class="quick-calc-close" type="button" aria-label="Fechar calculadora">×</button></header><output class="quick-calc-display" aria-live="polite" aria-atomic="true">0</output><div class="quick-calc-keys" role="group" aria-label="Teclas da calculadora"><button type="button" data-calc-key="clear">C</button><button type="button" data-calc-key="backspace" aria-label="Apagar último dígito">⌫</button><button type="button" data-calc-key="percent">%</button><button type="button" class="operator" data-calc-key="/">÷</button><button type="button" data-calc-key="7">7</button><button type="button" data-calc-key="8">8</button><button type="button" data-calc-key="9">9</button><button type="button" class="operator" data-calc-key="*">×</button><button type="button" data-calc-key="4">4</button><button type="button" data-calc-key="5">5</button><button type="button" data-calc-key="6">6</button><button type="button" class="operator" data-calc-key="-">−</button><button type="button" data-calc-key="1">1</button><button type="button" data-calc-key="2">2</button><button type="button" data-calc-key="3">3</button><button type="button" class="operator" data-calc-key="+">+</button><button type="button" data-calc-key="sign">±</button><button type="button" data-calc-key="0">0</button><button type="button" data-calc-key="decimal">,</button><button type="button" class="equals" data-calc-key="equals">=</button></div><small>Você também pode usar o teclado.</small></section></div>'
                     '<button class="top-icon-button theme-button" type="button" aria-label="Ativar modo escuro" aria-pressed="false" title="Ativar modo escuro"><svg class="theme-moon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401"/></svg></button></div></header>')
            script = '<script defer src="/static/navigation.js"></script><link rel="stylesheet" href="/static/chat-widget.css"><script defer src="/static/chat-widget.js"></script>'
            if path.startswith("/rotinas/tributos"):
                taxes_css_version = (STATIC / "taxes.css").stat().st_mtime_ns if (STATIC / "taxes.css").exists() else 1
                script += f'<link rel="stylesheet" href="/static/taxes.css?v={taxes_css_version}"><script defer src="/static/taxes.js"></script>'
            if path == "/central-dp/casos":
                cases_css_version = (STATIC / "react-cases" / "cases.css").stat().st_mtime_ns
                cases_font_version = (STATIC / "react-cases" / "cases-font.css").stat().st_mtime_ns
                interactions_version = (STATIC / "cases-interactions.js").stat().st_mtime_ns
                script += f'<link rel="stylesheet" href="/static/react-cases/cases.css?v={cases_css_version}"><link rel="stylesheet" href="/static/react-cases/cases-font.css?v={cases_font_version}"><script defer src="/static/cases-interactions.js?v={interactions_version}"></script>'
        shell_css_version = (STATIC / "global-shell.css").stat().st_mtime_ns
        v3_css_version = (STATIC / "v3.css").stat().st_mtime_ns
        current_path = urlsplit(self.path).path
        body_class = (' class="chat-page-mode"' if user and current_path == "/central-dp/chat" else
                      ' class="admin-workspace"' if user and current_path.startswith("/administracao/") else "")
        if user and current_path.startswith("/administracao/"):
            refinement_version = (STATIC / "admin-refinement.css").stat().st_mtime_ns
            script += f'<link rel="stylesheet" href="/static/admin-refinement.css?v={refinement_version}">'
        body = f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" type="image/svg+xml" href="/static/favicon.svg"><title>{esc(title)} · Conexão DP</title><link rel="stylesheet" href="/static/global-shell.css?v={shell_css_version}"><link rel="stylesheet" href="/static/v3.css?v={v3_css_version}">{script}</head><body{body_class}><div class="global-shell">{sidebar}<main class="global-content">{trail}{content}</main></div></body></html>'
        self.respond(body, status)

    def require_user(self):
        user = self.principal()
        if not user:
            self.redirect("/entrar")
        return user

    def form(self) -> dict[str, str] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 65536 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            self.respond("<h1>Requisição inválida.</h1>", 400)
            return None
        try:
            data = parse_qs(self.rfile.read(length).decode("utf-8", "strict"), keep_blank_values=True, max_num_fields=2000)
            return {key: (",".join(value) if key in ("members", "done_ids") else value[0]) for key, value in data.items()}
        except (UnicodeError, ValueError):
            self.respond("<h1>Formulário inválido.</h1>", 400)
            return None

    def _clicksign_read_json(self, limit: int = 32768):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return None, None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return None, None
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return None, None
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente um administrador pode gerenciar a Clicksign."}, 403)
            return None, None
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return None, None
        return user, payload

    def _voxfy_read_json(self, limit: int = 16384):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return None, None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return None, None
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return None, None
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente um administrador pode gerenciar a conexão VoxFy."}, 403)
            return None, None
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return None, None
        return user, payload

    def _integration_read_json(self, limit: int = 32768):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return None, None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return None, None
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return None, None
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente Administrador pode configurar ou consultar integrações externas."}, 403)
            return None, None
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return None, None
        return user, payload

    def _integration_row(self):
        with connect(self.server.database) as db:
            return db.execute("SELECT * FROM local_integration_settings WHERE id=1").fetchone()

    def _integration_require_active(self, integration: str):
        row = self._integration_row()
        if not row or not row[f"{integration}_active"]:
            raise ValueError(f"Ative e salve a integração {integration.upper()} antes de consultar.")
        if integration == "sib":
            if not row["sib_api_key_protected"]:
                raise ValueError("Cadastre a chave da SIB Bridge nas configurações de Integrações.")
            return row, integrations_service.SibClient(integrations_service.SibConfig(
                base_url=row["sib_base_url"], api_key=integration_secret_store.unprotect_secret(row["sib_api_key_protected"]),
                timeout_seconds=int(row["sib_timeout_seconds"]),
                cf_client_id=integration_secret_store.unprotect_secret(row["sib_cf_client_id_protected"]),
                cf_client_secret=integration_secret_store.unprotect_secret(row["sib_cf_client_secret_protected"]),
                ca_bundle_path=row["sib_ca_bundle_path"],
            ))
        if not row["selecty_api_id_protected"] or not row["selecty_secret_protected"]:
            raise ValueError("Cadastre API ID e Secret Selecty nas configurações de Integrações.")
        return row, integrations_service.SelectyClient(integrations_service.SelectyConfig(
            api_id=integration_secret_store.unprotect_secret(row["selecty_api_id_protected"]),
            secret=integration_secret_store.unprotect_secret(row["selecty_secret_protected"]),
            base_url=integrations_service.SELECTY_BASE_URL,
            timeout_seconds=int(row["selecty_timeout_seconds"]),
        ))

    @staticmethod
    def _integration_event(db, integration: str, action: str, outcome: str, actor_id: int,
                           result_count: int = 0, summary: str = "") -> None:
        safe_summary = " ".join(str(summary or "").split())[:240]
        db.execute("""INSERT INTO local_integration_events
            (integration,action,outcome,result_count,summary,actor_id)
            VALUES(?,?,?,?,?,?)""",
            (integration.upper(), action[:40], outcome, max(0, int(result_count)), safe_summary, actor_id))

    def _integration_settings_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            with connect(self.server.database) as db:
                automatic = db.execute("SELECT lease_token,lease_until FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
                if (sib_scan_service.summary(db)["inFlight"] or sib_scan_service.summary(db)["autoResume"]
                        or sib_discovery_service.summary(db)["inFlight"]
                        or sib_issues_service.job_summary(db)["inFlight"] or sib_refresh_service.summary(db)["inFlight"]
                        or sib_absences_service.summary(db)["running"]
                        or (automatic and lease_active(automatic["lease_token"], automatic["lease_until"]))):
                    raise ValueError("Pare a busca SIB e aguarde a consulta terminar antes de alterar as configurações.")
            sib_url = integrations_service.validate_sib_base_url(payload.get("sibBaseUrl") or "") if payload.get("sibBaseUrl") else ""
            sib_timeout = int(payload.get("sibTimeoutSeconds") or 20)
            selecty_timeout = int(payload.get("selectyTimeoutSeconds") or 20)
            if not 3 <= sib_timeout <= 60 or not 3 <= selecty_timeout <= 60:
                raise ValueError("O timeout de cada integração deve ficar entre 3 e 60 segundos.")
            ca_bundle = str(payload.get("sibCaBundlePath") or "").strip()[:500]
            if ca_bundle and not Path(ca_bundle).is_file():
                raise ValueError("O arquivo CA PEM informado não foi encontrado no servidor.")
            new_secrets = {
                "sib_api_key_protected": str(payload.get("sibApiKey") or "").strip(),
                "sib_cf_client_id_protected": str(payload.get("sibCfClientId") or "").strip(),
                "sib_cf_client_secret_protected": str(payload.get("sibCfClientSecret") or "").strip(),
                "selecty_api_id_protected": str(payload.get("selectyApiId") or "").strip(),
                "selecty_secret_protected": str(payload.get("selectySecret") or "").strip(),
            }
            protected = {key: integration_secret_store.protect_secret(value) if value else "" for key, value in new_secrets.items()}
            with connect(self.server.database) as db:
                db.execute("BEGIN IMMEDIATE")
                automatic = db.execute("SELECT lease_token,lease_until FROM local_sib_auto_refresh_state WHERE id=1").fetchone()
                if (sib_scan_service.summary(db)["inFlight"] or sib_scan_service.summary(db)["autoResume"]
                        or sib_discovery_service.summary(db)["inFlight"]
                        or sib_issues_service.job_summary(db)["inFlight"] or sib_refresh_service.summary(db)["inFlight"]
                        or sib_absences_service.summary(db)["running"]
                        or (automatic and lease_active(automatic["lease_token"], automatic["lease_until"]))):
                    raise ValueError("Pare a busca SIB antes de alterar as configurações.")
                previous = db.execute("SELECT * FROM local_integration_settings WHERE id=1").fetchone()
                for key, flag in (("sib_api_key_protected", "removeSibApiKey"),
                                  ("sib_cf_client_id_protected", "removeSibCfClientId"),
                                  ("sib_cf_client_secret_protected", "removeSibCfClientSecret"),
                                  ("selecty_api_id_protected", "removeSelectyApiId"),
                                  ("selecty_secret_protected", "removeSelectySecret")):
                    if not protected[key] and not payload.get(flag):
                        protected[key] = previous[key] if previous else ""
                sib_active = int(bool(payload.get("sibActive")))
                selecty_active = int(bool(payload.get("selectyActive")))
                if sib_active and (not sib_url or not protected["sib_api_key_protected"]):
                    raise ValueError("Para ativar SIB, informe a URL HTTPS e a chave da Bridge.")
                if selecty_active and (not protected["selecty_api_id_protected"] or not protected["selecty_secret_protected"]):
                    raise ValueError("Para ativar Selecty, informe API ID e Secret.")
                db.execute("""INSERT INTO local_integration_settings
                    (id,sib_base_url,sib_active,sib_timeout_seconds,sib_api_key_protected,
                     sib_cf_client_id_protected,sib_cf_client_secret_protected,sib_ca_bundle_path,
                     selecty_base_url,selecty_active,selecty_timeout_seconds,selecty_api_id_protected,
                     selecty_secret_protected,updated_by,updated_at)
                    VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET sib_base_url=excluded.sib_base_url,sib_active=excluded.sib_active,
                     sib_timeout_seconds=excluded.sib_timeout_seconds,sib_api_key_protected=excluded.sib_api_key_protected,
                     sib_cf_client_id_protected=excluded.sib_cf_client_id_protected,
                     sib_cf_client_secret_protected=excluded.sib_cf_client_secret_protected,
                     sib_ca_bundle_path=excluded.sib_ca_bundle_path,selecty_base_url=excluded.selecty_base_url,
                     selecty_active=excluded.selecty_active,selecty_timeout_seconds=excluded.selecty_timeout_seconds,
                     selecty_api_id_protected=excluded.selecty_api_id_protected,
                     selecty_secret_protected=excluded.selecty_secret_protected,
                     updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (sib_url, sib_active, sib_timeout, protected["sib_api_key_protected"],
                     protected["sib_cf_client_id_protected"], protected["sib_cf_client_secret_protected"], ca_bundle,
                     integrations_service.SELECTY_BASE_URL, selecty_active, selecty_timeout,
                     protected["selecty_api_id_protected"], protected["selecty_secret_protected"], user["id"]))
            self.respond_json({"ok": True, "message": "Configurações salvas. As credenciais foram protegidas no servidor Windows."})
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar as configurações de integração."}, 400)

    def _integration_sib_test_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            _, client = self._integration_require_active("sib")
            result = client.health()
            with connect(self.server.database) as db:
                db.execute("UPDATE local_integration_settings SET sib_last_test_at=CURRENT_TIMESTAMP,sib_last_test_status='OK' WHERE id=1")
                self._integration_event(db, "SIB", "HEALTH", "OK", user["id"], 0, "Saúde da Bridge confirmada; nenhum funcionário consultado.")
            self.respond_json({"connected": True, "message": "SIB Bridge respondeu à verificação de saúde. Nenhum cadastro foi lido ou alterado.", "status": result.get("status", "ok")})
        except (OSError, RuntimeError, ValueError, integrations_service.IntegrationError, sqlite3.DatabaseError) as error:
            try:
                with connect(self.server.database) as db:
                    db.execute("UPDATE local_integration_settings SET sib_last_test_at=CURRENT_TIMESTAMP,sib_last_test_status='ERRO' WHERE id=1")
                    self._integration_event(db, "SIB", "HEALTH", "ERRO", user["id"], 0, str(error))
            except sqlite3.DatabaseError:
                pass
            self.respond_json({"connected": False, "message": str(error) or "Não foi possível testar a SIB Bridge."}, 502)

    def _integration_sib_import_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            with connect(self.server.database) as db:
                if (sib_scan_service.summary(db)["inFlight"] or sib_scan_service.summary(db)["autoResume"]
                        or sib_discovery_service.summary(db)["inFlight"]
                        or sib_issues_service.job_summary(db)["inFlight"] or sib_refresh_service.summary(db)["inFlight"]):
                    raise ValueError("Pare a busca por matrículas antes de usar a listagem de catálogo.")
            _, client = self._integration_require_active("sib")
            result = sib_import_service.import_next(self.server.database, client, user["id"], restart=payload.get("restart") is True)
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível trazer o lote SIB. O progresso anterior foi preservado."}, 502)

    def _integration_sib_scan_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            action = str(payload.get("action") or "next")
            if action == "pause":
                client = None
            else:
                _, client = self._integration_require_active("sib")
            result = sib_scan_service.control(self.server.database, client, user["id"], action,
                start_id=payload.get("startId", 0), end_id=payload.get("endId", 999999))
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível iniciar a busca SIB."}, 400)

    def _integration_sib_refresh_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            action = str(payload.get("action") or "start")
            client = None if action == "pause" else self._integration_require_active("sib")[1]
            result = sib_refresh_service.control(self.server.database, client, user["id"], action)
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível atualizar os cadastros SIB."}, 400)

    def _integration_sib_auto_refresh_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            action = str(payload.get("action") or "pause")
            client = None if action == "pause" else self._integration_require_active("sib")[1]
            result = sib_auto_refresh_service.control(self.server.database, client, user["id"], action)
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível controlar a atualização automática SIB."}, 400)

    def _integration_sib_discovery_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            action = str(payload.get("action") or "resume")
            client = None if action == "pause" else self._integration_require_active("sib")[1]
            result = sib_discovery_service.control(self.server.database, client, user["id"], action)
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível controlar a descoberta de cadastros SIB."}, 400)

    def _integration_sib_presence_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            if payload.get('action') != 'confirm_cancellation':
                raise ValueError('Comando de ocorrência inválido.')
            from . import sib_presence
            with connect(self.server.database) as db:
                db.execute('BEGIN IMMEDIATE')
                result = sib_presence.confirm_cancellation(db, user['id'], str(payload.get('sourceId') or ''))
            self.respond_json(result)
        except (ValueError, PermissionError) as error:
            self.respond_json({'error': str(error)}, 400)

    def _integration_sib_issues_post(self) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        try:
            client = None if payload.get("action") == "pause" else self._integration_require_active("sib")[1]
            result = sib_issues_service.control(self.server.database, client, user["id"], payload)
            self.respond_json(result)
        except (OSError, RuntimeError, ValueError, PermissionError, LookupError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível tratar esta ocorrência."}, 400)

    def _integration_sib_employee_post(self, *, batch: bool = False, resolve: bool = False, absences: bool = False) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        action = "CONSULTA_LOTE" if batch else "CONCILIACAO" if resolve else "AFASTAMENTOS" if absences else "CONSULTA_VINCULO"
        try:
            _, client = self._integration_require_active("sib")
            if resolve:
                result = client.resolve(cpf=str(payload.get("cpf") or ""),
                                        admission_date=str(payload.get("admissionDate") or ""),
                                        taker_cnpj=str(payload.get("takerCnpj") or ""))
                count = 1
            elif batch:
                raw_ids = payload.get("idsFolha") or []
                if isinstance(raw_ids, str):
                    raw_ids = re.split(r"[\r\n,; ]+", raw_ids)
                if not isinstance(raw_ids, list):
                    raise ValueError("Informe a lista de IDs Folha para consultar em lote.")
                result = client.get_batch([str(item) for item in raw_ids])
                count = len(result)
            elif absences:
                result = client.get_absences(str(payload.get("idFolha") or ""))
                count = len(result)
            else:
                result = client.get_employee(str(payload.get("idFolha") or ""))
                count = 1
            with connect(self.server.database) as db:
                self._integration_event(db, "SIB", action, "OK", user["id"], count,
                                        f"Consulta concluída; {count} registro(s). Nenhuma gravação na V3 ou no SIB.")
            self.respond_json({"ok": True, "item": result if not (batch or absences) else None,
                               "items": result if (batch or absences) else None,
                               "count": count, "message": "Consulta somente leitura concluída. Nenhum cadastro foi atualizado."})
        except (OSError, RuntimeError, ValueError, integrations_service.IntegrationError, sqlite3.DatabaseError) as error:
            try:
                with connect(self.server.database) as db:
                    self._integration_event(db, "SIB", action, "ERRO", user["id"], 0, str(error))
            except sqlite3.DatabaseError:
                pass
            self.respond_json({"error": str(error) or "Não foi possível consultar a SIB Bridge."}, 502)

    def _integration_selecty_post(self, *, action: str) -> None:
        user, payload = self._integration_read_json()
        if payload is None:
            return
        event_action = "LISTA_CONTRATACOES" if action == "list" else "DETALHE_CONTRATACAO" if action == "detail" else "AUTH_E_LEITURA"
        try:
            _, client = self._integration_require_active("selecty")
            if action == "list":
                result = client.list_payroll(status=str(payload.get("status") or "exported"),
                                             page=int(payload.get("page") or 1),
                                             cpf=str(payload.get("cpf") or ""),
                                             vacancy_id=str(payload.get("vacancyId") or ""))
                count = len(result["items"])
            elif action == "detail":
                result = client.payroll_details(str(payload.get("workId") or ""))
                count = 1
            else:
                result = client.list_payroll(status="exported", page=1)
                count = len(result["items"])
            with connect(self.server.database) as db:
                self._integration_event(db, "SELECTY", event_action, "OK", user["id"], count,
                                        f"Consulta somente leitura concluída; {count} registro(s).")
                if action == "test":
                    db.execute("UPDATE local_integration_settings SET selecty_last_test_at=CURRENT_TIMESTAMP,selecty_last_test_status='OK' WHERE id=1")
            if action == "test":
                self.respond_json({"connected": True, "message": f"Autenticação Selecty confirmada; consulta de leitura retornou {count} registro(s)."})
            else:
                self.respond_json({"ok": True, **result, "message": "Consulta Selecty somente leitura concluída; nenhuma etapa externa ou cadastro foi alterado."})
        except (OSError, RuntimeError, ValueError, integrations_service.IntegrationError, sqlite3.DatabaseError) as error:
            try:
                with connect(self.server.database) as db:
                    self._integration_event(db, "SELECTY", event_action, "ERRO", user["id"], 0, str(error))
                    if action == "test":
                        db.execute("UPDATE local_integration_settings SET selecty_last_test_at=CURRENT_TIMESTAMP,selecty_last_test_status='ERRO' WHERE id=1")
            except sqlite3.DatabaseError:
                pass
            if action == "test":
                self.respond_json({"connected": False, "message": str(error) or "Não foi possível testar a Selecty."}, 502)
            else:
                self.respond_json({"error": str(error) or "Não foi possível consultar a Selecty."}, 502)

    def _email_read_json(self, limit: int = 16384):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return None, None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return None, None
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return None, None
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente um administrador pode configurar o envio de e-mail."}, 403)
            return None, None
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente outra vez."}, 403)
            return None, None
        return user, payload

    def _people_email_schedules_get(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente um administrador pode configurar os agendamentos de e-mail."}, 403)
            return
        try:
            with connect(self.server.database) as db:
                companies = people_email_schedules_service.company_options(db, user)
                schedules = people_email_schedules_service.list_schedules(db, companies)
                smtp = db.execute("SELECT active FROM local_smtp_settings WHERE id=1").fetchone()
            smtp_ready = bool(smtp and smtp["active"] and smtp_password_store.has_password())
            self.respond_json({"csrf": user["csrf_token"], "companies": companies,
                               "schedules": schedules, "smtpReady": smtp_ready,
                               "columnOptions": people_email_schedules_service.column_options(),
                               "defaultReportColumns": people_email_schedules_service.default_report_columns()})
        except (ValueError, sqlite3.DatabaseError, smtp_password_store.SMTPPasswordStoreError):
            self.respond_json({"error": "Não foi possível carregar os agendamentos de e-mail."}, 500)

    def _people_email_schedules_post(self) -> None:
        user, payload = self._email_read_json(limit=24576)
        if payload is None:
            return
        action = str(payload.get("action") or "save")
        try:
            with connect(self.server.database) as db:
                if action == "save":
                    if bool(payload.get("active", True)):
                        smtp = db.execute("SELECT active FROM local_smtp_settings WHERE id=1").fetchone()
                        if not smtp or not smtp["active"] or not smtp_password_store.has_password():
                            raise ValueError("Configure, salve e ative o SMTP em E-mail de teste antes de ativar os agendamentos.")
                    companies = people_email_schedules_service.company_options(db, user)
                    schedule_id = people_email_schedules_service.save(db, user, payload, companies)
                    message = "Agendamento salvo."
                elif action in ("activate", "pause"):
                    schedule_id = int(payload.get("id") or 0)
                    if schedule_id <= 0:
                        raise ValueError("Agendamento inválido.")
                    if action == "activate":
                        smtp = db.execute("SELECT active FROM local_smtp_settings WHERE id=1").fetchone()
                        if not smtp or not smtp["active"] or not smtp_password_store.has_password():
                            raise ValueError("Configure, salve e ative o SMTP em E-mail de teste antes de ativar os agendamentos.")
                    people_email_schedules_service.set_active(db, user, schedule_id, action == "activate")
                    message = "Agendamento ativado." if action == "activate" else "Agendamento pausado."
                else:
                    raise ValueError("Escolha uma ação válida para o agendamento.")
            self.respond_json({"ok": True, "id": schedule_id, "message": message})
        except LookupError as error:
            self.respond_json({"error": str(error)}, 404)
        except smtp_password_store.SMTPPasswordStoreError as error:
            self.respond_json({"error": str(error)}, 503)
        except (ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar o agendamento."}, 400)

    def _email_settings_post(self) -> None:
        user, payload = self._email_read_json()
        if payload is None:
            return
        try:
            settings = email_delivery_service.validate_settings(payload)
            password = str(payload.get("password") or "")
            if password:
                smtp_password_store.save_password(password)
            with connect(self.server.database) as db:
                db.execute("""INSERT INTO local_smtp_settings
                    (id,connection_name,smtp_host,smtp_port,security,username,sender_email,active,timeout_seconds,updated_by,updated_at)
                    VALUES(1,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET connection_name=excluded.connection_name,
                    smtp_host=excluded.smtp_host,smtp_port=excluded.smtp_port,security=excluded.security,
                    username=excluded.username,sender_email=excluded.sender_email,active=excluded.active,
                    timeout_seconds=excluded.timeout_seconds,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (settings["connectionName"], settings["smtpHost"], settings["smtpPort"], settings["security"],
                     settings["username"], settings["senderEmail"], settings["active"], settings["timeoutSeconds"], user["id"]))
            configured = smtp_password_store.has_password()
            message = "Configuração SMTP salva. A senha está protegida no Gerenciador de Credenciais do Windows." if configured else "Configuração SMTP salva. Informe a senha SMTP neste painel para habilitar os envios."
            self.respond_json({"ok": True, "credentialConfigured": configured, "message": message})
        except smtp_password_store.SMTPPasswordStoreError as error:
            self.respond_json({"error": str(error)}, 503)
        except (ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar a configuração SMTP."}, 400)

    def _email_send_test_post(self) -> None:
        user, payload = self._email_read_json(limit=32768)
        if payload is None:
            return
        try:
            password = smtp_password_store.get_password()
            if not password:
                self.respond_json({"error": "Salve a senha SMTP no painel E-mail de teste antes de enviar."}, 400)
                return
            with connect(self.server.database) as db:
                row = db.execute("SELECT * FROM local_smtp_settings WHERE id=1").fetchone()
            if not row:
                self.respond_json({"error": "Salve a configuração SMTP antes de enviar o teste."}, 400)
                return
            if not row["active"]:
                self.respond_json({"error": "Ative e salve a conexão SMTP antes do envio de teste."}, 400)
                return
            settings = {
                "connectionName": row["connection_name"], "smtpHost": row["smtp_host"],
                "smtpPort": row["smtp_port"], "security": row["security"],
                "username": row["username"], "senderEmail": row["sender_email"],
                "active": row["active"], "timeoutSeconds": row["timeout_seconds"],
            }
            attachment = Path(__file__).resolve().parents[1] / "output" / "pdf" / "pacote-documentos-rescisao-teste.pdf"
            email_delivery_service.send_test_email(settings, password, attachment)
            with connect(self.server.database) as db:
                db.execute("UPDATE local_smtp_settings SET last_test_sent_at=CURRENT_TIMESTAMP WHERE id=1")
            self.respond_json({"ok": True, "sent": True,
                               "message": f"E-mail de teste enviado para {email_delivery_service.TEST_ADDRESS}, com o PDF fictício anexado."})
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except smtp_password_store.SMTPPasswordStoreError as error:
            self.respond_json({"error": str(error)}, 503)
        except email_delivery_service.SMTPDeliveryError as error:
            self.respond_json({"error": str(error)}, 502)
        except sqlite3.DatabaseError:
            self.respond_json({"error": "Não foi possível atualizar o histórico da configuração SMTP."}, 500)

    def _voxfy_settings_post(self) -> None:
        user, payload = self._voxfy_read_json()
        if payload is None:
            return
        try:
            name = str(payload.get("connectionName") or "VoxFy - WhatsApp principal").strip()[:100]
            base_url = voxfy_service.validate_base_url(str(payload.get("apiBaseUrl") or ""))
            timeout = int(payload.get("timeoutSeconds", 30))
            if not 3 <= timeout <= 120:
                raise ValueError("O timeout deve ficar entre 3 e 120 segundos.")
            secret = str(payload.get("accessToken") or "").strip()
            if secret:
                protected = voxfy_service.protect_secret(secret)
            else:
                protected = ""
            with connect(self.server.database) as db:
                previous = db.execute("SELECT access_token_protected FROM local_voxfy_settings WHERE id=1").fetchone()
                if not protected and not payload.get("removeAccessToken"):
                    protected = previous["access_token_protected"] if previous else ""
                active = int(bool(payload.get("active")))
                db.execute("""INSERT INTO local_voxfy_settings
                    (id,connection_name,api_base_url,active,timeout_seconds,access_token_protected,updated_by,updated_at)
                    VALUES(1,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET connection_name=excluded.connection_name,
                    api_base_url=excluded.api_base_url,active=excluded.active,timeout_seconds=excluded.timeout_seconds,
                    access_token_protected=excluded.access_token_protected,updated_by=excluded.updated_by,
                    updated_at=CURRENT_TIMESTAMP""",
                    (name or "VoxFy - WhatsApp principal", base_url, active, timeout, protected, user["id"]))
            self.respond_json({"ok": True, "message": "Configuração VoxFy salva."})
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar a configuração VoxFy."}, 400)

    def _voxfy_test_post(self) -> None:
        user, payload = self._voxfy_read_json()
        if payload is None:
            return
        try:
            with connect(self.server.database) as db:
                row = db.execute("SELECT * FROM local_voxfy_settings WHERE id=1").fetchone()
            if not row:
                self.respond_json({"connected": False, "message": "Salve a configuração VoxFy antes de testar."}, 400)
                return
            token = voxfy_service.unprotect_secret(row["access_token_protected"])
            result, status = voxfy_service.test_connection(row["api_base_url"], token, row["timeout_seconds"])
            if result.get("connected"):
                with connect(self.server.database) as db:
                    db.execute("UPDATE local_voxfy_settings SET last_test_at=CURRENT_TIMESTAMP WHERE id=1")
            self.respond_json(result, status)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"connected": False, "message": str(error) or "Não foi possível testar a conexão VoxFy."}, 400)

    def _temporary_extension_post(self) -> None:
        user, payload = self._clicksign_read_json(limit=8192)
        if payload is None:
            return
        source_system = str(payload.get("sourceSystem") or "")[:60]
        source_id = str(payload.get("sourceId") or "")[:100]
        first_due_date = str(payload.get("firstDueDate") or "")[:10]
        action = str(payload.get("action") or "")[:30]
        try:
            with connect(self.server.database) as db:
                if source_system == 'SIB' and action == 'prepare':
                    from .extensions import prepare_sib_preview
                    message = prepare_sib_preview(db, source_id, first_due_date, str(payload.get('signerName') or '')[:180], str(payload.get('channel') or '')[:20])
                elif action == 'classify_sib_code':
                    if not access_service.can(user, 'integrations.manage'):
                        raise PermissionError('Seu perfil não pode classificar códigos da origem.')
                    code, label = str(payload.get('code') or '')[:20], str(payload.get('label') or '')[:40]
                    if not re.fullmatch(r'\d{1,6}', code) or label not in ('Temporário','CLT','Estágio','Outro'):
                        raise ValueError('Selecione um código SIB e uma classificação válida.')
                    db.execute("INSERT INTO local_sib_contract_types(code,label,confirmed_by) VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET label=excluded.label,confirmed_by=excluded.confirmed_by,confirmed_at=CURRENT_TIMESTAMP", (code,label,user['id']))
                    db.execute("INSERT INTO local_sib_operation_jobs(source_id) SELECT source_id FROM local_sib_records WHERE dataset='employments' AND CAST(json_extract(raw_json,'$.VIN_FUN') AS TEXT)=? ON CONFLICT(source_id) DO UPDATE SET next_due=0,error=''", (code,))
                    message = 'Classificação confirmada. As filas serão recalculadas a partir dos dados de origem.'
                else:
                    message = temporary_extension_action(
                    db, source_system=source_system, source_id=source_id,
                    first_due_date=first_due_date, action=action,
                    signer_name=str(payload.get("signerName") or "")[:180],
                    channel=str(payload.get("channel") or "")[:20],
                )
                queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
            self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"), "message": message})
        except ValueError as error:
            self.respond_json({"error": str(error)}, 400)
        except sqlite3.DatabaseError:
            self.respond_json({"error": "Não foi possível atualizar a simulação."}, 500)

    def _temporary_extension_real_post(self) -> None:
        authenticated = self._chat_payload()
        if not authenticated:
            return
        user, payload = authenticated
        source_system = str(payload.get("sourceSystem") or "")[:60]
        source_id = str(payload.get("sourceId") or "")[:100]
        first_due_date = str(payload.get("firstDueDate") or "")[:10]
        action = str(payload.get("action") or "")[:20]
        if source_system == 'SIB':
            try:
                from .extension_workflow import action as workflow_action
                message = workflow_action(self.server.database, payload, int(user['id']), self.server.database.parent / 'private-registry-documents', user=user)
                self.respond_json({'ok': True, 'message': message, 'csrf': user['csrf_token']})
            except PermissionError as error:
                self.respond_json({'error': str(error)}, 403)
            except (ValueError, LookupError) as error:
                self.respond_json({'error': str(error)}, 400)
            except (RuntimeError, OSError) as error:
                self.respond_json({'error': str(error)[:500]}, 502)
            return
        if source_system != "TESTE_LOCAL" or action not in ("prepare", "send", "refresh"):
            self.respond_json({"error": "O envio real está disponível somente para os temporários de teste desta fila."}, 400)
            return

        try:
            with connect(self.server.database) as db:
                contract, extension = temporary_extension_get(db, source_system, source_id, first_due_date)
                if not contract["is_test"]:
                    self.respond_json({"error": "Este registro não está marcado como temporário de teste."}, 400)
                    return
                config = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?",
                                    (CLICKSIGN_GLOBAL_KEY,)).fetchone()
                if not config or not config["active"]:
                    self.respond_json({"error": "Ative e salve a conexão geral da Clicksign antes de preparar o teste real."}, 400)
                    return
                token = clicksign_service.unprotect_secret(config["access_token_protected"]) if config["access_token_protected"] else os.environ.get("CLICKSIGN_API_TOKEN", "").strip()
                if not token:
                    self.respond_json({"error": "O Access Token da Clicksign não está configurado na conta geral."}, 400)
                    return
                allowed_channels = config["allowed_channels"]
                environment = config["environment"]
                timeout = int(config["timeout_seconds"])

                if action == "prepare":
                    sender_name = str(payload.get("senderName") or "").strip()[:180]
                    sender_cpf = re.sub(r"\D", "", str(payload.get("senderCpf") or ""))
                    sender_email = str(payload.get("senderEmail") or "").strip().lower()[:254]
                    sender_phone = re.sub(r"\D", "", str(payload.get("senderPhone") or ""))
                    sender_channel = str(payload.get("senderChannel") or "").strip().upper()
                    recipient_email = str(payload.get("recipientEmail") or "").strip().lower()[:254]
                    recipient_channel = str(payload.get("recipientChannel") or "").strip().upper()
                    if len(sender_name.split()) < 2 or not clicksign_service.valid_cpf(sender_cpf):
                        self.respond_json({"error": "Informe o nome completo e um CPF válido para o remetente."}, 400)
                        return
                    if sender_channel not in ("EMAIL", "WHATSAPP") or recipient_channel not in ("EMAIL", "WHATSAPP"):
                        self.respond_json({"error": "Escolha WhatsApp ou e-mail para cada convite."}, 400)
                        return
                    if allowed_channels != "BOTH" and any(channel != allowed_channels for channel in (sender_channel, recipient_channel)):
                        allowed_label = "e-mail" if allowed_channels == "EMAIL" else "WhatsApp"
                        self.respond_json({"error": f"A Clicksign permite apenas {allowed_label}. Ajuste os canais escolhidos na configuração."}, 400)
                        return
                    for label, email in (("remetente", sender_email), ("destinatário", recipient_email)):
                        if not email or "@" not in email or any(char.isspace() for char in email):
                            self.respond_json({"error": f"Informe o e-mail de {label}; ele receberá o documento finalizado."}, 400)
                            return
                    if not clicksign_service.valid_cpf(contract["cpf"]):
                        self.respond_json({"error": "O CPF fictício deste temporário precisa ser válido para o teste real."}, 400)
                        return
                    if sender_channel == "WHATSAPP" and len(sender_phone) not in (10, 11):
                        self.respond_json({"error": "Informe o WhatsApp do remetente com DDD."}, 400)
                        return
                    if recipient_channel == "WHATSAPP" and len(re.sub(r"\D", "", contract["whatsapp"] or "")) not in (10, 11):
                        self.respond_json({"error": "O temporário não tem WhatsApp válido cadastrado."}, 400)
                        return
                    if extension and extension["delivery_mode"] == "CLICKSIGN_REAL" and extension["envelope_id"] and extension["status"] not in ("CANCELLED", "ERROR"):
                        self.respond_json({"error": "Já existe um envelope real da Clicksign para este temporário. Não criei outro pacote."}, 409)
                        return
                    pdf = clicksign_service.build_test_pdf(sender_name, contract["person_name"])
                    now = datetime.now(SAO_PAULO).isoformat(timespec="seconds")
                    db.execute("""INSERT INTO local_temporary_extensions
                        (source_system,source_id,first_due_date,status,envelope_id,attempts,last_sent_at,
                         provider_status,signer_name,worker_channel,delivery_mode,delivery_environment,
                         sender_channel,sender_name,sender_cpf,sender_email,sender_phone,recipient_email,
                         preview_pdf,prepared_at,last_notice_at,sender_signed_at,worker_signed_at,
                         recipient_invited_at,last_notice_target,updated_at)
                        VALUES(?,?,?,'PREPARED','',0,'','TESTE_REAL_PREVIA_PRONTA',?,?,
                               'CLICKSIGN_REAL',?,?,?,?,?,?,?,?,?, '', '', '', '', '',CURRENT_TIMESTAMP)
                        ON CONFLICT(source_system,source_id,first_due_date) DO UPDATE SET
                         status='PREPARED',envelope_id='',attempts=0,last_sent_at='',
                         provider_status='TESTE_REAL_PREVIA_PRONTA',signer_name=excluded.signer_name,
                         worker_channel=excluded.worker_channel,delivery_mode='CLICKSIGN_REAL',
                         delivery_environment=excluded.delivery_environment,sender_channel=excluded.sender_channel,
                         sender_name=excluded.sender_name,sender_cpf=excluded.sender_cpf,
                         sender_email=excluded.sender_email,sender_phone=excluded.sender_phone,
                         recipient_email=excluded.recipient_email,preview_pdf=excluded.preview_pdf,
                         prepared_at=excluded.prepared_at,last_notice_at='',sender_signed_at='',
                         worker_signed_at='',recipient_invited_at='',last_notice_target='',
                         updated_at=CURRENT_TIMESTAMP""",
                        (source_system, source_id, first_due_date, sender_name, recipient_channel,
                         environment, sender_channel, sender_name, sender_cpf, sender_email, sender_phone,
                         recipient_email, pdf, now))
                    queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                    self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"),
                                       "message": "Prévia de teste real pronta. O PDF é demonstrativo, sem validade contratual."}, 200)
                    return

                if not extension or extension["delivery_mode"] != "CLICKSIGN_REAL":
                    self.respond_json({"error": "Prepare o teste real nesta linha antes de continuar."}, 400)
                    return
                if action == "refresh":
                    if not extension["envelope_id"] or extension["envelope_id"].startswith("SIM-"):
                        self.respond_json({"error": "Este registro ainda não possui envelope real da Clicksign."}, 400)
                        return
                    envelope_state = clicksign_service.get_envelope_status(
                        environment, token, extension["envelope_id"], timeout=timeout)
                    status_by_provider = {"running": "AWAITING_SIGNATURE", "closed": "SIGNED",
                                          "canceled": "CANCELLED", "draft": "PREPARED"}
                    mapped_status = status_by_provider.get(envelope_state, extension["status"])
                    db.execute("UPDATE local_temporary_extensions SET status=?,provider_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                               (mapped_status, f"CLICKSIGN_{envelope_state.upper()}", extension["id"]))
                    queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                    self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"),
                                       "message": f"Status consultado na Clicksign: {envelope_state}."}, 200)
                    return

                if extension["status"] != "PREPARED" or not extension["preview_pdf"]:
                    self.respond_json({"error": "Confira a prévia antes de enviar o teste real."}, 400)
                    return
                if extension["provider_status"] == "CLICKSIGN_ENVIANDO":
                    self.respond_json({"error": "O envio já está em andamento. Aguarde antes de tentar novamente."}, 409)
                    return
                changed = db.execute("UPDATE local_temporary_extensions SET provider_status='CLICKSIGN_ENVIANDO',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='PREPARED' AND delivery_mode='CLICKSIGN_REAL' AND provider_status<>'CLICKSIGN_ENVIANDO'",
                                     (extension["id"],))
                if changed.rowcount != 1:
                    self.respond_json({"error": "Este pacote mudou de estado. Atualize a página."}, 409)
                    return
                # Copy all values before leaving the transaction for the network call.
                sending = dict(extension)

            try:
                result = clicksign_service.send_test_signature(
                    environment, token, sender_name=sending["sender_name"],
                    sender_email=sending["sender_email"], sender_cpf=sending["sender_cpf"],
                    sender_phone=sending["sender_phone"], sender_channel=sending["sender_channel"],
                    recipient_name=contract["person_name"], recipient_email=sending["recipient_email"],
                    recipient_phone=re.sub(r"\D", "", contract["whatsapp"] or ""),
                    recipient_channel=sending["worker_channel"], recipient_cpf=re.sub(r"\D", "", contract["cpf"] or ""),
                    timeout=timeout, envelope_name=f"TESTE REAL - Prorrogação - {contract['person_name']}",
                    document_pdf=bytes(sending["preview_pdf"]),
                )
                envelope_id = result["envelopeId"]
                provider_status = "CLICKSIGN_AGUARDANDO_REMETENTE"
                final_status = "AWAITING_SIGNATURE"
                sent_at = datetime.now(SAO_PAULO).isoformat(timespec="seconds")
            except clicksign_service.ClicksignApiError as error:
                envelope_id = error.envelope_id
                if error.activated and envelope_id:
                    with connect(self.server.database) as db:
                        db.execute("""UPDATE local_temporary_extensions SET status='AWAITING_SIGNATURE',
                            envelope_id=?,attempts=attempts+1,last_sent_at=?,
                            provider_status='CLICKSIGN_NOTIFICACAO_NAO_CONFIRMADA',updated_at=CURRENT_TIMESTAMP
                            WHERE source_system=? AND source_id=? AND first_due_date=?""",
                            (envelope_id, datetime.now(SAO_PAULO).isoformat(timespec="seconds"),
                             source_system, source_id, first_due_date))
                        queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                    self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"),
                                       "error": str(error) + " O envelope foi mantido nesta linha para conferência; não reenvie outro pacote."}, 502)
                    return
                with connect(self.server.database) as db:
                    db.execute("UPDATE local_temporary_extensions SET status='PREPARED',provider_status='CLICKSIGN_ERRO',updated_at=CURRENT_TIMESTAMP WHERE source_system=? AND source_id=? AND first_due_date=? AND delivery_mode='CLICKSIGN_REAL'",
                               (source_system, source_id, first_due_date))
                    queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"), "error": str(error)}, 502)
                return
            except (OSError, RuntimeError, ValueError) as error:
                # A local failure after reserving this row must not strand it in
                # CLICKSIGN_ENVIANDO or make the user think an invitation went out.
                with connect(self.server.database) as db:
                    db.execute("""UPDATE local_temporary_extensions SET status='PREPARED',
                        provider_status='CLICKSIGN_ERRO',updated_at=CURRENT_TIMESTAMP
                        WHERE source_system=? AND source_id=? AND first_due_date=?
                          AND delivery_mode='CLICKSIGN_REAL' AND provider_status='CLICKSIGN_ENVIANDO'""",
                        (source_system, source_id, first_due_date))
                    queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"),
                                   "error": str(error) or "Não foi possível concluir o envio à Clicksign."}, 502)
                return

            with connect(self.server.database) as db:
                changed = db.execute("""UPDATE local_temporary_extensions SET status=?,envelope_id=?,
                    attempts=attempts+1,last_sent_at=?,provider_status=?,sender_signed_at='',worker_signed_at='',
                    recipient_invited_at='',last_notice_at='',last_notice_target='',updated_at=CURRENT_TIMESTAMP
                    WHERE source_system=? AND source_id=? AND first_due_date=?
                      AND delivery_mode='CLICKSIGN_REAL' AND provider_status='CLICKSIGN_ENVIANDO'""",
                    (final_status, envelope_id, sent_at, provider_status, source_system, source_id, first_due_date))
                if changed.rowcount != 1:
                    self.respond_json({"error": "A Clicksign aceitou o envelope, mas a fila mudou. Consulte o envelope antes de qualquer novo envio."}, 409)
                    return
                queue = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
            self.respond_json({**queue, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage"),
                               "message": result["message"], "environmentLabel": "Sandbox" if environment == "sandbox" else "Produção"}, 201)
        except clicksign_service.ClicksignApiError as error:
            self.respond_json({"error": str(error)}, 502)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível preparar o envio real."}, 400)

    def _clicksign_test_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        if user["role"] != "ADMIN":
            self.respond_json({"error": "Somente um administrador pode testar a integração."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 16_384 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        company_key = str(payload.get("companyKey") or "")[:150]
        if not company_key:
            self.respond_json({"error": "Selecione o escopo da conexão Clicksign."}, 400)
            return
        try:
            with connect(self.server.database) as db:
                company = next((item for item in company_catalog(db) if item["key"] == company_key), None)
                if company_key != CLICKSIGN_GLOBAL_KEY and not company:
                    self.respond_json({"error": "A empresa selecionada não está mais disponível."}, 404)
                    return
                row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (company_key,)).fetchone()
                global_row = row if company_key == CLICKSIGN_GLOBAL_KEY else db.execute(
                    "SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (CLICKSIGN_GLOBAL_KEY,)
                ).fetchone()
                effective_row = row or global_row
                token_row = row if row and row["access_token_protected"] else (
                    global_row if global_row and global_row["access_token_protected"] else None
                )
            global_config = clicksign_service.configuration()
            environment = effective_row["environment"] if effective_row else global_config["environment"]
            token = clicksign_service.unprotect_secret(token_row["access_token_protected"]) if token_row else None
            timeout = int(effective_row["timeout_seconds"]) if effective_row else 20
            result, status = clicksign_service.test_connection(environment, token, timeout)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"connected": False, "message": str(error) or "Não foi possível abrir a configuração Clicksign."}, 400)
            return
        self.respond_json(result, status)

    def _clicksign_settings_post(self) -> None:
        user, payload = self._clicksign_read_json()
        if payload is None:
            return
        company_key = str(payload.get("companyKey") or "")[:150]
        try:
            with connect(self.server.database) as db:
                company = next((item for item in company_catalog(db) if item["key"] == company_key), None)
                if company_key != CLICKSIGN_GLOBAL_KEY and not company:
                    self.respond_json({"error": "Selecione uma empresa cadastrada."}, 400)
                    return
                previous = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (company_key,)).fetchone()
                environment = str(payload.get("environment") or "sandbox").strip().lower()
                channels = str(payload.get("allowedChannels") or "BOTH").strip().upper()
                if environment not in clicksign_service.API_ROOTS or channels not in ("BOTH", "EMAIL", "WHATSAPP"):
                    self.respond_json({"error": "Ambiente ou canal permitido inválido."}, 400)
                    return
                name = str(payload.get("connectionName") or "Clicksign").strip()[:100] or "Clicksign"
                webhook = str(payload.get("webhookUrl") or "").strip()[:500]
                if webhook:
                    parsed = urlsplit(webhook)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                        self.respond_json({"error": "Informe uma URL pública HTTPS válida para o webhook."}, 400)
                        return
                try:
                    timeout = int(payload.get("timeoutSeconds", 20))
                    refresh = int(payload.get("refreshMinutes", 15))
                except (TypeError, ValueError):
                    self.respond_json({"error": "Timeout e intervalo de atualização precisam ser números."}, 400)
                    return
                if not 3 <= timeout <= 120 or not 1 <= refresh <= 1440:
                    self.respond_json({"error": "Timeout deve ficar entre 3 e 120 segundos e atualização entre 1 e 1440 minutos."}, 400)
                    return
                active = int(bool(payload.get("active")))
                old_token = previous["access_token_protected"] if previous else ""
                old_hmac = previous["hmac_secret_protected"] if previous else ""
                token_value = str(payload.get("accessToken") or "").strip()
                hmac_value = str(payload.get("hmacSecret") or "").strip()
                if payload.get("removeAccessToken"):
                    old_token = ""
                elif token_value:
                    old_token = clicksign_service.protect_secret(token_value)
                if payload.get("removeHmacSecret"):
                    old_hmac = ""
                elif hmac_value:
                    old_hmac = clicksign_service.protect_secret(hmac_value)
                db.execute("""INSERT INTO local_clicksign_company_settings
                    (company_key,connection_name,environment,active,timeout_seconds,allowed_channels,refresh_minutes,
                     webhook_url,access_token_protected,hmac_secret_protected,updated_by,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(company_key) DO UPDATE SET connection_name=excluded.connection_name,
                    environment=excluded.environment,active=excluded.active,timeout_seconds=excluded.timeout_seconds,
                    allowed_channels=excluded.allowed_channels,refresh_minutes=excluded.refresh_minutes,
                    webhook_url=excluded.webhook_url,access_token_protected=excluded.access_token_protected,
                    hmac_secret_protected=excluded.hmac_secret_protected,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (company_key, name, environment, active, timeout, channels, refresh, webhook,
                     old_token, old_hmac, user["id"]))
            message = "Configuração geral da conta salva." if company_key == CLICKSIGN_GLOBAL_KEY else "Configuração da empresa salva."
            self.respond_json({"ok": True, "message": message})
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar a configuração."}, 400)

    def _clicksign_send_test_post(self) -> None:
        user, payload = self._clicksign_read_json(limit=16_384)
        if payload is None:
            return
        company_key = str(payload.get("companyKey") or CLICKSIGN_GLOBAL_KEY)[:150]
        sender_name = str(payload.get("senderName") or "").strip()[:180]
        sender_email = str(payload.get("senderEmail") or "").strip().lower()[:254]
        sender_phone = re.sub(r"\D", "", str(payload.get("senderPhone") or ""))
        sender_channel = str(payload.get("senderChannel") or "WHATSAPP").strip().upper()
        sender_cpf = re.sub(r"\D", "", str(payload.get("senderCpf") or ""))
        recipient_name = str(payload.get("recipientName") or "").strip()[:180]
        recipient_email = str(payload.get("recipientEmail") or "").strip().lower()[:254]
        recipient_phone = re.sub(r"\D", "", str(payload.get("recipientPhone") or ""))
        recipient_channel = str(payload.get("recipientChannel") or "WHATSAPP").strip().upper()
        recipient_cpf = re.sub(r"\D", "", str(payload.get("recipientCpf") or ""))
        if len(sender_name.split()) < 2 or len(recipient_name.split()) < 2:
            self.respond_json({"error": "Informe nome e sobrenome do remetente e do destinatário."}, 400)
            return
        if any(not email or "@" not in email or any(char.isspace() for char in email)
               for email in (sender_email, recipient_email)):
            self.respond_json({"error": "Informe um e-mail válido para cada signatário; os dois receberão o documento finalizado."}, 400)
            return
        if sender_channel not in ("EMAIL", "WHATSAPP") or recipient_channel not in ("EMAIL", "WHATSAPP"):
            self.respond_json({"error": "Escolha WhatsApp ou e-mail para o convite de cada signatário."}, 400)
            return
        if sender_channel == "WHATSAPP" and len(sender_phone) not in (10, 11):
            self.respond_json({"error": "Informe o WhatsApp do remetente com DDD e 10 ou 11 dígitos."}, 400)
            return
        if recipient_channel == "WHATSAPP" and len(recipient_phone) not in (10, 11):
            self.respond_json({"error": "Informe o WhatsApp do destinatário com DDD e 10 ou 11 dígitos."}, 400)
            return
        if not clicksign_service.valid_cpf(sender_cpf) or not clicksign_service.valid_cpf(recipient_cpf):
            self.respond_json({"error": "Informe CPFs válidos para o remetente e o destinatário."}, 400)
            return
        try:
            with connect(self.server.database) as db:
                company = next((item for item in company_catalog(db) if item["key"] == company_key), None)
                if company_key != CLICKSIGN_GLOBAL_KEY and not company:
                    self.respond_json({"error": "A empresa selecionada não está mais disponível."}, 404)
                    return
                company_row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (company_key,)).fetchone()
                global_row = company_row if company_key == CLICKSIGN_GLOBAL_KEY else db.execute(
                    "SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (CLICKSIGN_GLOBAL_KEY,)
                ).fetchone()
                effective_row = company_row or global_row
                token_row = company_row if company_row and company_row["access_token_protected"] else (
                    global_row if global_row and global_row["access_token_protected"] else None
                )
            environment = effective_row["environment"] if effective_row else clicksign_service.configuration()["environment"]
            if effective_row and not effective_row["active"]:
                self.respond_json({"error": "Ative e salve a conexão Clicksign antes de enviar o documento de teste."}, 400)
                return
            allowed_channels = effective_row["allowed_channels"] if effective_row else "BOTH"
            if any(allowed_channels != "BOTH" and channel != allowed_channels
                   for channel in (sender_channel, recipient_channel)):
                allowed_label = "e-mail" if allowed_channels == "EMAIL" else "WhatsApp"
                self.respond_json({"error": f"A configuração permite apenas {allowed_label}. Ajuste os canais dos signatários ou libere Ambos."}, 400)
                return
            token = clicksign_service.unprotect_secret(token_row["access_token_protected"]) if token_row else os.environ.get("CLICKSIGN_API_TOKEN", "").strip()
            if not token:
                self.respond_json({"error": "Salve o Access Token na configuração geral da Clicksign antes do envio."}, 400)
                return
            timeout = int(effective_row["timeout_seconds"]) if effective_row else 20
            result = clicksign_service.send_test_signature(
                environment, token, sender_name=sender_name, sender_email=sender_email, sender_cpf=sender_cpf,
                sender_phone=sender_phone, sender_channel=sender_channel,
                recipient_name=recipient_name, recipient_email=recipient_email,
                recipient_phone=recipient_phone, recipient_channel=recipient_channel, recipient_cpf=recipient_cpf,
                timeout=timeout,
            )
            self.respond_json({**result, "environment": environment,
                               "environmentLabel": "Sandbox" if environment == "sandbox" else "Produção"}, 201)
        except clicksign_service.ClicksignApiError as error:
            self.respond_json({"error": str(error)}, 502)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as error:
            self.respond_json({"error": str(error) or "Não foi possível preparar o envio de teste."}, 400)

    def _clicksign_signer_post(self) -> None:
        user, payload = self._clicksign_read_json()
        if payload is None:
            return
        company_key = str(payload.get("companyKey") or "")[:150]
        if company_key == CLICKSIGN_GLOBAL_KEY:
            self.respond_json({"error": "Selecione uma empresa para cadastrar signatários."}, 400)
            return
        action = str(payload.get("action") or "save")
        try:
            with connect(self.server.database) as db:
                company = next((item for item in company_catalog(db) if item["key"] == company_key), None)
                if not company:
                    self.respond_json({"error": "Selecione uma empresa cadastrada."}, 400)
                    return
                signer_id = int(payload.get("signerId") or 0)
                if action in ("delete", "toggle"):
                    row = db.execute("SELECT id FROM local_clicksign_signers WHERE id=? AND company_key=?", (signer_id, company_key)).fetchone()
                    if not row:
                        self.respond_json({"error": "Signatário não encontrado nesta empresa."}, 404)
                        return
                    if action == "delete":
                        db.execute("DELETE FROM local_clicksign_signers WHERE id=? AND company_key=?", (signer_id, company_key))
                    else:
                        db.execute("UPDATE local_clicksign_signers SET active=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND company_key=?",
                                   (int(bool(payload.get("active"))), signer_id, company_key))
                    db.commit()
                    self.respond_json({"ok": True, "message": "Cadastro atualizado."})
                    return
                if action != "save":
                    self.respond_json({"error": "Ação de signatário inválida."}, 400)
                    return
                name = str(payload.get("name") or "").strip()[:180]
                role = str(payload.get("role") or "").strip()[:120]
                cpf = re.sub(r"\D", "", str(payload.get("cpf") or ""))[:14]
                birth_date = str(payload.get("birthDate") or "").strip()
                email = str(payload.get("email") or "").strip()[:254]
                whatsapp = re.sub(r"[^\d+() -]", "", str(payload.get("whatsapp") or ""))[:40]
                channel = str(payload.get("channel") or "BOTH").strip().upper()
                authorized_at = str(payload.get("authorizedAt") or "").strip()
                try:
                    order = int(payload.get("order") or 1)
                except (TypeError, ValueError):
                    self.respond_json({"error": "A ordem do signatário precisa ser um número."}, 400)
                    return
                if not name or channel not in ("BOTH", "EMAIL", "WHATSAPP") or not 1 <= order <= 100:
                    self.respond_json({"error": "Informe o nome, canal permitido e ordem entre 1 e 100."}, 400)
                    return
                if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
                    self.respond_json({"error": "Informe um e-mail válido."}, 400)
                    return
                for value, label in ((birth_date, "nascimento"), (authorized_at, "autorização")):
                    if value:
                        try:
                            date.fromisoformat(value)
                        except ValueError:
                            self.respond_json({"error": f"Informe uma data de {label} válida."}, 400)
                            return
                active = int(bool(payload.get("active", True)))
                auto_signature = int(bool(payload.get("autoSignature")))
                if signer_id:
                    result = db.execute("""UPDATE local_clicksign_signers SET name=?,role=?,cpf=?,birth_date=?,email=?,whatsapp=?,
                        channel=?,signer_order=?,active=?,auto_signature=?,authorized_at=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND company_key=?""",
                        (name, role, cpf, birth_date, email, whatsapp, channel, order, active, auto_signature,
                         authorized_at, signer_id, company_key))
                    if not result.rowcount:
                        self.respond_json({"error": "Signatário não encontrado nesta empresa."}, 404)
                        return
                else:
                    db.execute("""INSERT INTO local_clicksign_signers
                        (company_key,name,role,cpf,birth_date,email,whatsapp,channel,signer_order,active,auto_signature,authorized_at,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (company_key, name, role, cpf, birth_date, email, whatsapp, channel, order, active,
                         auto_signature, authorized_at, user["id"]))
            self.respond_json({"ok": True, "message": "Signatário salvo."})
        except (sqlite3.DatabaseError, ValueError) as error:
            self.respond_json({"error": str(error) or "Não foi possível salvar o signatário."}, 400)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if self._access_guard(path):
            return
        if operational_http.get(self, path):
            return
        if path == '/api/access':
            user = self.principal()
            if not user:
                self.respond_json({'error': 'Sua sessão expirou.'}, 401)
                return
            with connect(self.server.database) as db:
                self.respond_json(access_service.page_data(db, user))
            return
        if path == "/api/service/status":
            self.respond_json({"service": "conexao-dp-v3", "pid": os.getpid(), "sibRecoveryVersion": 2, "sibPresenceVersion": 1})
            return
        export_match = re.fullmatch(r"/api/ui/people-reports/export/([a-f0-9]{32})(/download)?", path)
        if export_match:
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                if export_match[2]:
                    filename, content = self.server.report_exports.get(export_match[1], user, download=True)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.respond_json(self.server.report_exports.get(export_match[1], user))
            except LookupError as error:
                self.respond_json({"error": str(error)}, 404)
            except PermissionError as error:
                self.respond_json({"error": str(error)}, 403)
            except ValueError as error:
                self.respond_json({"error": str(error)}, 409)
            return
        if path == '/api/ui/sector' or re.fullmatch(r'/api/ui/sector/\d+', path):
            user = self.principal()
            if not user:
                self.respond_json({'error':'Sua sessão expirou. Entre novamente.'},401)
                return
            try:
                with connect(self.server.database) as db:
                    params = parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    result = (sector_service.overview(db,params,user) if path == '/api/ui/sector'
                              else {'item':sector_service.detail(db,path.rsplit('/',1)[1],user,params)})
                self.respond_json({**result,'csrf':user['csrf_token']})
            except PermissionError as error:
                self.respond_json({'error':str(error)},403)
            except ValueError as error:
                self.respond_json({'error':str(error)},400)
            except sqlite3.DatabaseError:
                self.respond_json({'error':'Não foi possível consultar os dados do setor agora.'},503)
            return
        if re.fullmatch(r'/api/ui/sector/source/\d+/download',path) or re.fullmatch(r'/api/ui/sector/media/\d+/[^/]+',path):
            user = self.principal()
            if not user:
                self.respond_json({'error':'Sua sessão expirou. Entre novamente.'},401)
                return
            try:
                with connect(self.server.database) as db:
                    root = self.server.database.parent / 'private-sector'
                    parts = path.split('/')
                    if parts[4] == 'source':
                        filename,body = sector_assets.original(db,parts[5],user,root)
                        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    else:
                        body,mime = sector_assets.image(db,parts[5],parts[6],user,root)
                        filename = ''
                self.send_response(200)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                if filename:self.send_header('Content-Disposition',"attachment; filename=origem-processos.xlsx; filename*=UTF-8''"+quote(filename))
                self.end_headers()
                self.wfile.write(body)
            except PermissionError as error:
                self.respond_json({'error':str(error)},403)
            except (ValueError,OSError,sqlite3.DatabaseError):
                self.respond_json({'error':'O material de origem protegido não está disponível.'},404)
            return
        if path == "/api/ui/people-analytics":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                with connect(self.server.database) as db:
                    # One consistent WAL snapshot; importing new batches can continue.
                    db.execute("BEGIN")
                    result = get_people_analytics(db, parse_qs(urlsplit(self.path).query, keep_blank_values=True), user=user,
                                                  use_cache=True)
                self.respond_json(result)
            except ValueError as error:
                self.respond_json({"error": str(error)}, 400)
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível ler a base local de indicadores. Tente novamente."}, 503)
            return
        if path == "/api/ui/absences":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if not access_service.can(user, 'absences.view'):
                self.respond_json({"error": "Seu perfil não permite consultar afastamentos."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    db.execute('BEGIN')
                    self.respond_json(sib_absences_service.page_data(db, parse_qs(urlsplit(self.path).query), user))
            except (sqlite3.DatabaseError, ValueError):
                self.respond_json({"error": "Não foi possível ler os afastamentos locais."}, 503)
            return
        if path == "/api/ui/registry-status":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                with connect(self.server.database) as db:
                    self.respond_json(get_registry_status(db, user))
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível atualizar o status dos cadastros agora."}, 503)
            return
        if path == "/api/ui/registry":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            view = (params.get("view") or [""])[0]
            try:
                with connect(self.server.database) as db:
                    self.respond_json(get_registry_data(db, view, params, user))
            except ValueError as error:
                self.respond_json({"error": str(error)}, 400)
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar os cadastros agora."}, 500)
            return
        if path == "/api/ui/registry-detail":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            view = (params.get("view") or [""])[0]
            try:
                with connect(self.server.database) as db:
                    self.respond_json(get_registry_detail(db, view, params, user))
            except ValueError as error:
                self.respond_json({"error": str(error)}, 400)
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar os detalhes do cadastro agora."}, 500)
            return
        issue_match = re.fullmatch(r"/api/integrations/sib/issues(?:/(\d{1,6}))?", path)
        if issue_match:
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente Administrador pode tratar ocorrências SIB."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    if issue_match.group(1):
                        result = sib_issues_service.detail(db, issue_match.group(1))
                    else:
                        params = parse_qs(urlsplit(self.path).query)
                        page = (params.get("page") or ["1"])[0]
                        status = (params.get("status") or ["open"])[0]
                        query = (params.get("q") or [""])[0].strip()
                        if not page.isdigit() or len(page)>8 or status not in ("open", "resolved", "all") or (query and (not query.isdigit() or len(query)>6)):
                            raise ValueError("Filtro de ocorrências inválido.")
                        result = sib_issues_service.list_issues(db, page=int(page), status=status, query=str(int(query)) if query else "")
                self.respond_json({**result, "csrf": user["csrf_token"]})
            except LookupError as error:
                self.respond_json({"error": str(error)}, 404)
            except (ValueError, sqlite3.DatabaseError) as error:
                self.respond_json({"error": str(error) or "Não foi possível carregar as ocorrências."}, 400)
            return
        if path == '/api/integrations/sib/occurrences':
            user = self.principal()
            if not user:
                self.respond_json({'error': 'Sua sessão expirou.'}, 401)
                return
            if user['role'] != 'ADMIN':
                self.respond_json({'error': 'Somente Administrador pode consultar ocorrências SIB.'}, 403)
                return
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            try:
                from . import sib_presence
                page = (params.get('page') or ['1'])[0]
                if not page.isdigit() or len(page) > 7:
                    raise ValueError('Página inválida.')
                with connect(self.server.database) as db:
                    result = sib_presence.list_occurrences(db, status=(params.get('status') or ['MISSING'])[0],
                        page=int(page), query=(params.get('q') or [''])[0])
                self.respond_json({**result, 'csrf': user['csrf_token']})
            except (ValueError, sqlite3.DatabaseError) as error:
                self.respond_json({'error': str(error) or 'Não foi possível carregar as ocorrências.'}, 400)
            return
        if path == "/api/integrations/sib/changes":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente Administrador pode consultar o histórico detalhado SIB."}, 403)
                return
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            page_value = (params.get("page") or ["1"])[0]
            query = (params.get("q") or [""])[0].strip()
            if not page_value.isdigit() or len(page_value) > 8 or len(query) > 120:
                self.respond_json({"error": "Filtro do histórico de alterações inválido."}, 400)
                return
            try:
                with connect(self.server.database) as db:
                    result = sib_refresh_service.list_changes(db, page=int(page_value), query=query)
                self.respond_json({**result, "csrf": user["csrf_token"]})
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar o histórico detalhado SIB."}, 503)
            return
        if path == "/api/ui/import-batches" or re.fullmatch(r"/api/ui/import-batches/\d+", path):
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente Administrador pode consultar lotes de importação."}, 403)
                return
            with connect(self.server.database) as db:
                if path == "/api/ui/import-batches":
                    batches = [dict(row) for row in db.execute(
                        "SELECT id,source_kind,source_name,company_scope,status,row_count,error_count,created_at,applied_at FROM local_import_batches ORDER BY id DESC LIMIT 50"
                    )]
                    self.respond_json({"batches": batches, "csrf": user["csrf_token"]})
                else:
                    try:
                        self.respond_json(import_staging_service.preview_batch(db, int(path.rsplit("/", 1)[-1])))
                    except LookupError as error:
                        self.respond_json({"error": str(error)}, 404)
            return
        if path == "/api/email/settings":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente um administrador pode consultar a configuração SMTP."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    row = db.execute("SELECT * FROM local_smtp_settings WHERE id=1").fetchone()
                settings = dict(email_delivery_service.DEFAULTS)
                if row:
                    settings.update({
                        "connectionName": row["connection_name"], "smtpHost": row["smtp_host"],
                        "smtpPort": row["smtp_port"], "security": row["security"],
                        "username": row["username"], "senderEmail": row["sender_email"],
                        "active": bool(row["active"]), "timeoutSeconds": row["timeout_seconds"],
                    })
                self.respond_json({**settings, "csrf": user["csrf_token"], "saved": bool(row),
                                   "credentialConfigured": smtp_password_store.has_password(),
                                   "lastTestSentAt": row["last_test_sent_at"] if row else ""})
            except (sqlite3.DatabaseError, smtp_password_store.SMTPPasswordStoreError):
                self.respond_json({"error": "Não foi possível carregar a configuração SMTP."}, 500)
            return
        if path == "/api/email/people-schedules":
            self._people_email_schedules_get()
            return
        if path == "/api/ui/rescissions":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            one = lambda key: (params.get(key) or [""])[0].strip()
            try:
                with connect(self.server.database) as db:
                    payload = rescissions_service.list_cases(
                        db, date.fromisoformat(_sao_paulo_today()), search=one("search")[:120],
                        company=one("company")[:160], reason=one("reason")[:1024], phase=one("phase")[:20],
                        date_from=one("dateFrom")[:10], date_to=one("dateTo")[:10],
                        page=max(1, min(1000000, int(one('page') or '1'))), user=user,
                    )
                self.respond_json({**payload, "csrf": user["csrf_token"],
                                   "canManage": access_service.can(user, "rescissions.manage"),
                                   "canSend": access_service.can(user, "communications.send"),
                                   "canConfigure": access_service.can(user, "integrations.manage"),
                                   "canCreateDemo": user["role"] == "ADMIN",
                                   "zproConfigured": bool(payload.get('zproConfigured', False)),
                                   "smtpPasswordConfigured": smtp_password_store.has_password()})
            except (ValueError, sqlite3.DatabaseError, smtp_password_store.SMTPPasswordStoreError):
                self.respond_json({"error": "Não foi possível carregar a Central de Rescisões."}, 500)
            return
        case_match = re.fullmatch(r"/api/ui/rescissions/(\d+)", path)
        if case_match:
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                with connect(self.server.database) as db:
                    case = rescissions_service.get_case(db, int(case_match.group(1)), date.fromisoformat(_sao_paulo_today()), user=user)
                if not case:
                    self.respond_json({"error": "Rescisão não encontrada."}, 404)
                    return
                can_manage = access_service.can(user, "rescissions.manage")
                if not access_service.can(user, "documents.view"):
                    case["available_documents"] = []
                self.respond_json({"case": case, "csrf": user["csrf_token"],
                                   "canManage": can_manage})
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar os detalhes da rescisão."}, 500)
            return
        document_match = re.fullmatch(r"/api/ui/person-documents/(\d+)", path)
        if document_match:
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if not access_service.can(user, "documents.view"):
                self.respond_json({"error": "Seu perfil não pode consultar documentos do prontuário."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    row, target = registry_documents_service.get_document(
                        db, int(document_match.group(1)), self.server.database.parent / "private-registry-documents", user=user)
                    raw = target.read_bytes()
                safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", row["filename"]).strip(" .") or "documento"
                self.send_response(200)
                self.send_header("Content-Type", row["mime_type"])
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.end_headers()
                self.wfile.write(raw)
            except LookupError as error:
                self.respond_json({"error": str(error)}, 404)
            except OSError:
                self.respond_json({"error": "Não foi possível ler o arquivo privado."}, 500)
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível consultar o prontuário."}, 500)
            return
        if path == "/api/ui/temporary-extensions/preview":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            source_system = (params.get("sourceSystem") or [""])[0][:60]
            source_id = (params.get("sourceId") or [""])[0][:100]
            first_due_date = (params.get("firstDueDate") or [""])[0][:10]
            filename = 'rascunho-prorrogacao.pdf'
            try:
                with connect(self.server.database) as db:
                    contract, extension = temporary_extension_get(db, source_system, source_id, first_due_date)
                    if not access_service.allowed_company(db, user, company_key=contract.get('company_key', ''), name=contract.get('company_name', '')):
                        raise PermissionError('Esta prorrogação está fora do seu recorte de acesso.')
                    allowed_local_simulation = source_system in ("REGISTRY", "SIB") and extension and extension["delivery_mode"] == "SIMULATED"
                    pdf = bytes(extension["preview_pdf"]) if (contract["is_test"] or allowed_local_simulation) and extension and extension["preview_pdf"] else None
                    filename = "teste-real-clicksign.pdf" if extension and extension["delivery_mode"] == "CLICKSIGN_REAL" else "previa-prorrogacao-simulacao.pdf"
            except (ValueError, sqlite3.DatabaseError):
                pdf = None
                if source_system == 'SIB':
                    with connect(self.server.database) as db:
                        allowed_contract = db.execute("SELECT company_key,company_name FROM local_temporary_contracts WHERE source_system='SIB' AND source_id=?", (source_id,)).fetchone()
                        if not allowed_contract or not access_service.allowed_company(db, user, company_key=allowed_contract['company_key'], name=allowed_contract['company_name']):
                            raise PermissionError('Esta prorrogação está fora do seu recorte de acesso.')
                        saved = db.execute("SELECT preview_pdf FROM local_temporary_extensions WHERE source_system='SIB' AND source_id=? AND first_due_date=?", (source_id,first_due_date)).fetchone()
                        pdf = bytes(saved['preview_pdf']) if saved and saved['preview_pdf'] else None
            if not pdf:
                self.respond_json({"error": "Prévia não encontrada."}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'inline; filename="{filename}"')
            self.send_header("Content-Length", str(len(pdf)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(pdf)
            return
        if path == "/api/ui/temporary-extensions":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                with connect(self.server.database) as db:
                    payload = temporary_extension_queue(db, date.fromisoformat(_sao_paulo_today()), user=user)
                self.respond_json({**payload, "csrf": user["csrf_token"], "canSimulate": access_service.can(user, "extensions.manage")})
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar a fila de prorrogações."}, 500)
            return
        if path == "/api/voxfy/settings":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente um administrador pode consultar a conexão VoxFy."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    row = db.execute("SELECT * FROM local_voxfy_settings WHERE id=1").fetchone()
                self.respond_json({
                    "csrf": user["csrf_token"],
                    "connectionName": row["connection_name"] if row else "VoxFy - WhatsApp principal",
                    "apiBaseUrl": row["api_base_url"] if row and row["api_base_url"] else "https://vox.selecty.app/v2/api/external/80d4bcf0-9a92-436f-a677-07ec8ff07ff9",
                    "active": bool(row["active"]) if row else False,
                    "timeoutSeconds": row["timeout_seconds"] if row else 30,
                    "tokenConfigured": bool(row["access_token_protected"]) if row else False,
                    "lastTestAt": row["last_test_at"] if row else "",
                })
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar a configuração VoxFy."}, 500)
            return
        if path == "/api/integrations/settings":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente Administrador pode gerenciar integrações externas."}, 403)
                return
            try:
                with connect(self.server.database) as db:
                    row = db.execute("SELECT * FROM local_integration_settings WHERE id=1").fetchone()
                    sib_import_status = sib_import_service.summary(db)
                    sib_scan_status = sib_scan_service.summary(db)
                    sib_refresh_status = sib_refresh_service.summary(db)
                    sib_auto_refresh_status = sib_auto_refresh_service.summary(db)
                    sib_discovery_status = sib_discovery_service.summary(db)
                    sib_issue_job = sib_issues_service.job_summary(db)
                    sib_absences_status = sib_absences_service.summary(db)
                    events = [dict(item) for item in db.execute("""SELECT e.id,e.integration,e.action,e.outcome,e.result_count,e.summary,e.created_at,u.name AS actor
                        FROM local_integration_events e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT 30""")]
                self.respond_json({
                    "csrf": user["csrf_token"],
                    "backend": {"pid": os.getpid(), "sibRecoveryVersion": 2, "sibPresenceVersion": 1},
                    "sib": {"baseUrl": row["sib_base_url"] if row else "", "active": bool(row["sib_active"]) if row else False,
                            "timeoutSeconds": row["sib_timeout_seconds"] if row else 20,
                            "apiKeyConfigured": bool(row["sib_api_key_protected"]) if row else False,
                            "cfClientIdConfigured": bool(row["sib_cf_client_id_protected"]) if row else False,
                            "cfClientSecretConfigured": bool(row["sib_cf_client_secret_protected"]) if row else False,
                            "caBundlePath": row["sib_ca_bundle_path"] if row else "",
                            "lastTestAt": row["sib_last_test_at"] if row else "",
                            "lastTestStatus": row["sib_last_test_status"] if row else ""},
                    "selecty": {"baseUrl": integrations_service.SELECTY_BASE_URL,
                                "active": bool(row["selecty_active"]) if row else False,
                                "timeoutSeconds": row["selecty_timeout_seconds"] if row else 20,
                                "apiIdConfigured": bool(row["selecty_api_id_protected"]) if row else False,
                                "secretConfigured": bool(row["selecty_secret_protected"]) if row else False,
                                "lastTestAt": row["selecty_last_test_at"] if row else "",
                                "lastTestStatus": row["selecty_last_test_status"] if row else ""},
                    "events": events,
                    "sibImport": sib_import_status,
                    "sibScan": sib_scan_status,
                    "sibRefresh": sib_refresh_status,
                    "sibAutoRefresh": sib_auto_refresh_status,
                    "sibDiscovery": sib_discovery_status,
                    "sibIssueJob": sib_issue_job,
                    "sibAbsences": sib_absences_status,
                })
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível carregar as configurações de integração."}, 500)
            return
        if path == "/api/clicksign/settings":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            requested = (params.get("companyKey") or [""])[0][:150]
            try:
                with connect(self.server.database) as db:
                    companies = company_catalog(db)
                    companies_json = [{"key": item["key"], "name": item["name"], "code": item["code"], "source": item["source"]} for item in companies]
                    selected = next((item for item in companies if item["key"] == requested), None)
                    use_global = requested == CLICKSIGN_GLOBAL_KEY
                    if selected is None and companies and not use_global:
                        selected = companies[0]
                    if selected is None:
                        use_global = True
                    config_key = CLICKSIGN_GLOBAL_KEY if use_global else selected["key"]
                    company_row = db.execute("SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (config_key,)).fetchone()
                    global_row = company_row if use_global else db.execute(
                        "SELECT * FROM local_clicksign_company_settings WHERE company_key=?", (CLICKSIGN_GLOBAL_KEY,)
                    ).fetchone()
                    row = company_row or global_row
                    token_row = company_row if company_row and company_row["access_token_protected"] else (
                        global_row if global_row and global_row["access_token_protected"] else None
                    )
                    hmac_row = company_row if company_row and company_row["hmac_secret_protected"] else (
                        global_row if global_row and global_row["hmac_secret_protected"] else None
                    )
                    signers = []
                    if selected and user["role"] == "ADMIN":
                        for signer in db.execute("""SELECT id,name,role,cpf,birth_date,email,whatsapp,channel,signer_order,active,
                            auto_signature,authorized_at FROM local_clicksign_signers WHERE company_key=? ORDER BY signer_order,id""", (selected["key"],)):
                            signers.append({"id": signer["id"], "name": signer["name"], "role": signer["role"], "cpf": signer["cpf"],
                                            "birthDate": signer["birth_date"], "email": signer["email"], "whatsapp": signer["whatsapp"],
                                            "channel": signer["channel"], "order": signer["signer_order"], "active": bool(signer["active"]),
                                            "autoSignature": bool(signer["auto_signature"]), "authorizedAt": signer["authorized_at"]})
                global_config = clicksign_service.configuration()
                environment = row["environment"] if row else global_config["environment"]
                env_token_configured = bool(os.environ.get("CLICKSIGN_API_TOKEN", "").strip())
                can_manage = user["role"] == "ADMIN"
                token_configured = bool(token_row) if can_manage else False
                configured = token_configured or (env_token_configured if can_manage else False)
                visible_environment = environment if can_manage else "sandbox"
                if token_row is company_row and not use_global:
                    token_source = "Empresa"
                elif token_row is not None:
                    token_source = "Conta Clicksign"
                elif can_manage and env_token_configured:
                    token_source = "Servidor"
                else:
                    token_source = "Não configurado"
                self.respond_json({
                    "csrf": user["csrf_token"], "canManage": can_manage, "companies": companies_json,
                    "currentUser": {"name": user["name"], "email": user["email"]},
                    "companyKey": config_key, "companyName": selected["name"] if selected else "Conta Clicksign (geral)",
                    "globalScope": use_global,
                    "environment": visible_environment, "environmentLabel": "Sandbox" if visible_environment == "sandbox" else "Produção",
                    "connectionName": row["connection_name"] if row and can_manage else "Clicksign", "active": bool(row["active"]) if row and can_manage else False,
                    "timeoutSeconds": row["timeout_seconds"] if row and can_manage else 20,
                    "allowedChannels": row["allowed_channels"] if row and can_manage else "BOTH",
                    "refreshMinutes": row["refresh_minutes"] if row and can_manage else 15,
                    "webhookUrl": row["webhook_url"] if row and can_manage else "",
                    "tokenConfigured": token_configured, "tokenStoredHere": bool(company_row and company_row["access_token_protected"]) if can_manage else False,
                    "hmacConfigured": bool(hmac_row) if can_manage else False,
                    "hmacStoredHere": bool(company_row and company_row["hmac_secret_protected"]) if can_manage else False,
                    "configured": configured,
                    "tokenSource": token_source,
                    "canUseProtectedStorage": os.name == "nt", "signers": signers,
                })
            except (sqlite3.DatabaseError, TypeError, ValueError):
                self.respond_json({"error": "Não foi possível carregar a configuração Clicksign desta empresa."}, 500)
            return
        if path == "/api/clicksign/status":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            self.respond_json({**clicksign_service.configuration(), "csrf": user["csrf_token"], "canTest": user["role"] == "ADMIN"})
            return
        if path == "/api/ui/calculators":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            saved_id = (params.get("id") or [""])[0]
            try:
                with connect(self.server.database) as db:
                    companies = _calculator_companies(db)
                    locations = [dict(row) for row in db.execute("SELECT DISTINCT state,city FROM local_calendar_holidays WHERE city<>'' ORDER BY state,city")]
                    today = _sao_paulo_today()
                    rows = db.execute("SELECT id,title,calculator_id,result_json,pinned,expires_at,created_at FROM local_calculations WHERE created_by=? AND (pinned=1 OR expires_at>=?) ORDER BY pinned DESC,created_at DESC LIMIT 100", (user["id"], today)).fetchall()
                    saved = []
                    for row in rows:
                        try: value = json.loads(row["result_json"]).get("value", "—")
                        except (TypeError, ValueError): value = "—"
                        saved.append({"id": row["id"], "title": row["title"], "calculatorId": row["calculator_id"],
                                      "value": value, "pinned": bool(row["pinned"]), "expiresAt": row["expires_at"], "createdAt": row["created_at"]})
                    selected = None
                    if saved_id.isdigit():
                        row = db.execute("SELECT id,title,calculator_id,inputs_json,result_json,pinned,expires_at,created_at FROM local_calculations WHERE id=? AND created_by=? AND (pinned=1 OR expires_at>=?)", (int(saved_id), user["id"], today)).fetchone()
                        if row:
                            selected = {"id": row["id"], "title": row["title"], "calculatorId": row["calculator_id"],
                                        "inputs": json.loads(row["inputs_json"]), "result": json.loads(row["result_json"]),
                                        "pinned": bool(row["pinned"]), "expiresAt": row["expires_at"], "createdAt": row["created_at"]}
                self.respond_json({"csrf": user["csrf_token"], "calculators": [{"id": item[0], "title": item[1], "fields": [{"key": field[0], "label": field[1], "default": field[2]} for field in item[2]]} for item in PAYROLL_CALCULATORS],
                                   "companies": companies, "locations": locations, "saved": saved, "selected": selected})
            except (sqlite3.DatabaseError, ValueError, TypeError, KeyError):
                self.respond_json({"error": "Não foi possível carregar as calculadoras locais."}, 500)
            return
        if path == "/api/ui/lot-plans":
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = {key: (values[0] if values else "") for key, values in parse_qs(urlsplit(self.path).query).items()}
            try:
                with connect(self.server.database) as db:
                    db.execute("BEGIN IMMEDIATE")
                    lot_plans_api.note_expired(db, user)
                    payload = lot_plans_api.workspace(db, user, params)
                self.respond_json(payload)
            except (ValueError, TypeError, sqlite3.DatabaseError):
                self.respond_json({"error": "Não foi possível carregar os planejamentos locais."}, 500)
            return
        if path.startswith("/api/chat"):
            user = self.principal()
            if not user:
                self.respond_json({"error":"Sua sessão expirou. Entre novamente."},401)
                return
            try:
                params=parse_qs(urlsplit(self.path).query)
                with connect(self.server.database) as db:
                    if path == "/api/chat":
                        self.respond_json({"csrf":user["csrf_token"],"userId":user["id"],"userName":user["name"],"avatarId":chat_service.get_profile_avatar(db,user["id"]),"moodId":chat_service.get_profile_mood(db,user["id"]),"unreadTotal":chat_service.unread_count(db,user["id"])})
                    elif path == "/api/chat/conversations":
                        self.respond_json({"items":chat_service.list_conversations(db,user["id"],(params.get("q") or [""])[0]),"unreadTotal":chat_service.unread_count(db,user["id"])})
                    elif path == "/api/chat/people":
                        self.respond_json({"items":chat_service.people(db,user["id"])})
                    elif path == "/api/chat/unread":
                        self.respond_json({"unreadTotal":chat_service.unread_count(db,user["id"])})
                    elif path.startswith("/api/chat/files/"):
                        attachment_id=path.rsplit("/",1)[-1]
                        row,target=chat_service.get_attachment(db,user["id"],attachment_id,self.server.database.parent/"private-chat")
                        data=target.read_bytes()
                        chat_service.audit_attachment_access(db,user["id"],attachment_id)
                        safe_name=re.sub(r"[^A-Za-z0-9._ -]","_",row["filename"]).strip(" .") or "anexo"
                        self.send_response(200); self.send_header("Content-Type",row["mime_type"]); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","private, no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Content-Disposition",f'attachment; filename="{safe_name}"'); self.end_headers(); self.wfile.write(data)
                    else:
                        match=re.fullmatch(r"/api/chat/conversations/([^/]+)/messages",path)
                        if not match: raise chat_service.ChatError("Rota indisponível.",404)
                        result=chat_service.get_messages(db,user["id"],match.group(1),(params.get("before") or [None])[0],(params.get("since") or [None])[0],(params.get("limit") or [50])[0])
                        self.respond_json(result)
            except chat_service.ChatError as error:
                self.respond_json({"error":error.message},error.status)
            except (ValueError,TypeError):
                self.respond_json({"error":"Parâmetros inválidos."},400)
            return
        if path == "/api/ui/manuals" or path.startswith("/api/ui/manuals/"):
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            try:
                params = parse_qs(urlsplit(self.path).query)
                if path == "/api/ui/manuals":
                    payload = manuals_service.list_manuals(params, dict(user))
                else:
                    match = re.fullmatch(r"/api/ui/manuals/([^/]+)", path)
                    if not match:
                        self.respond_json({"error": "Manual não encontrado."}, 404)
                        return
                    manual = manuals_service.get_manual(unquote(match.group(1)), dict(user))
                    if manual is None:
                        self.respond_json({"error": "Manual não encontrado ou sem permissão para leitura."}, 404)
                        return
                    payload = {"manual": manual}
            except Exception:
                self.respond_json({"error": "Não foi possível consultar os manuais locais agora. Tente novamente."}, 500)
                return
            self.respond_json(payload)
            return
        if path == "/api/ui/cases" or re.fullmatch(r"/api/ui/cases/\d+", path):
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            if path == "/api/ui/cases":
                with connect(self.server.database) as db:
                    if "home_filter" in params or "home_source" in params:
                        html = render_home_cases(db, user, params)
                    else:
                        from .cases_ui import render as render_cases
                        html = render_cases(db, user, params)
                self.respond_json({"html": html})
            else:
                self.case_page(user, int(path.rsplit("/", 1)[-1]), fragment=True)
            return
        if path in ("/api/ui/home", "/api/ui/processes", "/api/ui/messages", "/api/ui/calendar", "/api/ui/snapshot"):
            user = self.principal()
            if not user:
                self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
                return
            params = parse_qs(urlsplit(self.path).query)
            with connect(self.server.database) as db:
                if path.endswith("/home"):
                    payload = next_home_data(db, user, (params.get("scope") or ["mine"])[0])
                elif path.endswith("/processes"):
                    payload = next_processes_data(db, params, user)
                elif path.endswith("/calendar"):
                    payload = next_calendar_data(db, params, user)
                elif path.endswith("/snapshot"):
                    row = db.execute(
                        "SELECT imported_at,row_count FROM v2_import_log WHERE source_name='funcionarios_ativos.csv' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    sib_scan_status = sib_scan_service.summary(db)
                    sib_refresh_status = sib_refresh_service.summary(db)
                    sib_issue_job = sib_issues_service.job_summary(db)
                    payload = {"activeImportAt": str(row["imported_at"]) if row else None,
                               "activeImportRows": int(row["row_count"]) if row else 0,
                               "sibScan": {key: sib_scan_status[key] for key in ("started", "running", "inFlight", "done", "autoResume", "retrying")},
                               "sibAutoRefresh": {"running": sib_auto_refresh_service.summary(db)["running"]},
                               "sibRefresh": {key: sib_refresh_status[key] for key in ("started", "running", "inFlight", "done")},
                               "sibIssueJob": {key: sib_issue_job[key] for key in ("running", "inFlight")}}
                else:
                    payload = get_messages_data(db, user, params)
            self.respond_json(payload)
            return
        if path.startswith("/manual-assets/secullum-illustrations/"):
            filename = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"SECULLUM-PDF-\d{3}\.jpg", filename):
                self.respond("Not found", 404)
                return
            target = (REFERENCE_MANUAL_ASSETS / filename).resolve()
            if not target.is_relative_to(REFERENCE_MANUAL_ASSETS.resolve()) or not target.is_file():
                self.respond("Not found", 404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/static/logo.svg", "/static/favicon.svg", "/static/global-shell.css", "/static/v3.css", "/static/admin-refinement.css", "/static/navigation.js", "/static/cases-interactions.js", "/static/import-staging.js", "/static/chat-widget.js", "/static/chat-widget.css", "/static/taxes.css", "/static/taxes.js") or path.startswith("/static/react-cases/"):
            target = (STATIC / path.removeprefix("/static/")).resolve()
            if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                self.respond("Not found", 404)
                return
            data = target.read_bytes()
            self.send_response(200)
            content_type = "image/svg+xml" if path.endswith("svg") else "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/entrar":
            if self.principal():
                self.redirect("/central-dp/inicio")
                return
            content = '<div class="v2-page"><section class="v2-card v2-login"><img src="/static/logo.svg" alt="Conexão DP"><h1>Entrar na Conexão DP</h1><p>V3 Python · instalação local separada</p><form method="post" action="/entrar"><label>E-mail<input name="email" type="email" autocomplete="username" required></label><label>Senha<input name="password" type="password" autocomplete="current-password" required></label><button type="submit">Entrar</button></form></section></div>'
            self.page("Entrar", content)
            return
        user = self.require_user()
        if not user:
            return
        if path == "/analytics/tabelas/exportar":
            parameters = parse_qs(urlsplit(self.path).query)
            with connect(self.server.database) as db:
                data = export_people_csv(db, parameters)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="conexao_dp_tabelas.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/", "/central-dp", "/central-dp/inicio"):
            with connect(self.server.database) as db:
                content = render_home(db,user,parse_qs(urlsplit(self.path).query))
            self.page("Início", content, user)
        elif path == "/central-dp/casos" or path.startswith("/trabalho/"):
            params=parse_qs(urlsplit(self.path).query)
            if path=="/central-dp/casos" and ("home_filter" in params or "home_source" in params):
                with connect(self.server.database) as db:
                    self.page("Casos e checklists",render_home_cases(db,user,params),user)
                return
            kind = FLOW.get(path.removeprefix("/trabalho/")) if path.startswith("/trabalho/") else None
            if path.startswith("/trabalho/") and not kind:
                self.page("Não encontrado", '<div class="workspace"><h1>Página não encontrada</h1></div>', user, 404)
                return
            self.cases_page(user, kind)
        elif path.startswith("/central-dp/casos/") and path.rsplit("/", 1)[-1].isdigit():
            self.case_page(user, int(path.rsplit("/", 1)[-1]))
        elif path in PAGE_LABELS:
            self.module_page(user, path)
        else:
            self.page("Não implementado", '<div class="workspace"><h1>Esta função ainda não foi portada</h1><p>A versão Python ainda não reproduz todas as funções da V2. Nenhum dado foi importado automaticamente.</p><a href="/central-dp">Voltar</a></div>', user, 404)

    def module_page(self, user, path: str) -> None:
        group, title = PAGE_LABELS[path]
        body = ""
        rich_page = path in ("/central-dp/chat", "/rotinas/calendario", "/rotinas/planejamento-lotes", "/rotinas/calculadoras", "/rotinas/tributos")
        with connect(self.server.database) as db:
            if path == "/central-dp/processos":
                body = render_processes(db, parse_qs(urlsplit(self.path).query), user)
            elif path == "/central-dp/chat":
                body = render_chat(db, user, parse_qs(urlsplit(self.path).query))
            elif path == "/rotinas/calendario":
                body = render_calendar(db, user, parse_qs(urlsplit(self.path).query))
            elif path == "/rotinas/planejamento-lotes":
                body = render_lots(db, user, parse_qs(urlsplit(self.path).query))
            elif path == "/rotinas/calculadoras":
                db.execute("DELETE FROM local_calculations WHERE pinned=0 AND expires_at<?", (_sao_paulo_today(),))
                body = render_calculators(db, user, parse_qs(urlsplit(self.path).query))
            elif path == "/rotinas/tributos":
                body = render_taxes(db,user,parse_qs(urlsplit(self.path).query))
            elif path == "/analytics/tabelas":
                body = render_people_tables(db, parse_qs(urlsplit(self.path).query), esc)
            elif path in ("/cadastros/pessoas", "/cadastros/vinculos"):
                body = render_registry(db, path, parse_qs(urlsplit(self.path).query), esc,
                                       user["csrf_token"], user["role"] == "ADMIN")
            elif path == "/cadastros/conflitos":
                body = render_conflicts(db, parse_qs(urlsplit(self.path).query), esc,
                                        user["csrf_token"], user["role"] == "ADMIN")
            elif path == "/cadastros/empresas":
                body = render_companies(db, parse_qs(urlsplit(self.path).query), esc,
                                        user["csrf_token"], user["role"] == "ADMIN")
            elif path == "/analytics/empresas":
                rows = db.execute("SELECT company,COUNT(*) total,SUM(in_active_export) active FROM v2_employments GROUP BY company ORDER BY active DESC,total DESC").fetchall()
                entries = "".join(f'<tr><td>{esc(r["company"])}</td><td>{r["active"]}</td><td>{r["total"]}</td></tr>' for r in rows)
                body = f'<p>{len(rows)} empresa(s) nas exportações disponíveis.</p><div class="table-wrap"><table><thead><tr><th>Empresa</th><th>Ativos</th><th>Vínculos</th></tr></thead><tbody>{entries}</tbody></table></div>'
            elif path.startswith("/analytics/") or path.startswith("/analytics-pro/"):
                body = render_analytics(db, path, esc, user["role"] == "ADMIN")
            elif path == "/administracao/contas":
                rows = db.execute("SELECT name,email,role,active,created_at FROM users ORDER BY name").fetchall()
                entries = "".join(f'<tr><td>{esc(r["name"])}</td><td>{esc(r["email"])}</td><td>{esc(r["role"])}</td><td>{"Ativa" if r["active"] else "Inativa"}</td><td>{esc(r["created_at"])}</td></tr>' for r in rows)
                body = f'<div class="table-wrap"><table><thead><tr><th>Nome</th><th>E-mail</th><th>Papel</th><th>Situação</th><th>Desde</th></tr></thead><tbody>{entries}</tbody></table></div>'
            elif path == "/administracao/backup":
                backups = sorted(self.server.database.parent.glob(self.server.database.stem + ".*.sqlite3"), reverse=True)
                entries = "".join(f'<li>{esc(p.name)} · {p.stat().st_size:,} bytes</li>' for p in backups)
                action = f'<form method="post" action="/administracao/backup"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><button>Criar backup agora</button></form>' if user["role"] == "ADMIN" else ''
                body = f'<p>Banco atual: {esc(self.server.database.name)}</p>{action}<h2>Cópias locais</h2><ul class="history">{entries or "<li>Nenhuma cópia registrada.</li>"}</ul>'
            else:
                source = {
                    "/central-dp/processos": "procedures", "/central-dp/mensagens": "quick_messages",
                    "/central-dp/manuais": "manuals", "/central-dp/chat": "conversations",
                    "/rotinas/calendario": "calendar_events",
                    "/administracao/arquivos": "private_files", "/administracao/auditoria": "audit_events",
                    "/rotinas/planejamento-lotes": "benefit_plans", "/rotinas/calculadoras": "calculator_saves",
                    "/rotinas/tributos": "official_revisions",
                }.get(path)
                if source:
                    selected_id = (parse_qs(urlsplit(self.path).query).get("id") or [""])[0][:128]
                    count = db.execute("SELECT COUNT(*) FROM v2_source_rows WHERE table_name=?", (source,)).fetchone()[0]
                    page_size = 25 if path.startswith("/administracao/") else 100
                    page_value = (parse_qs(urlsplit(self.path).query).get("page") or ["1"])[0]
                    page_count = max(1, (count + page_size - 1) // page_size)
                    page_number = min(max(1, int(page_value)), page_count) if page_value.isdigit() and len(page_value) <= 8 else 1
                    records = db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name=? ORDER BY source_id LIMIT ? OFFSET ?", (source,page_size,(page_number-1)*page_size)).fetchall()
                    data = [(r["source_id"], json.loads(r["payload"])) for r in records]
                    keys = {"procedures": ("title","category","system","status"), "quick_messages": ("title","category","body"), "manuals": ("title","category","status"), "conversations": ("title","kind","created_at"), "calendar_events": ("title","due_at","status"), "conflicts": ("kind","status","created_at"), "private_files": ("filename","mime_type","created_at"), "audit_events": ("action","subject_type","created_at"), "benefit_plans": ("title","created_at"), "calculator_saves": ("title","calculator_name","created_at"), "official_revisions": ("kind","competence","status")}.get(source, ("id",))
                    field_labels = {"title":"Título", "category":"Categoria", "system":"Sistema", "status":"Situação",
                                    "body":"Mensagem", "kind":"Tipo", "created_at":"Data do registro", "due_at":"Prazo",
                                    "filename":"Arquivo", "mime_type":"Tipo do arquivo", "action":"Ação",
                                    "subject_type":"Registro", "calculator_name":"Cálculo", "competence":"Competência"}
                    headings = "".join(f'<th>{esc(field_labels.get(k,k.replace("_"," ").title()))}</th>' for k in keys)
                    entries = "".join('<tr><td><a href="'+path+'?id='+quote(source_id)+'">Abrir</a></td>' + ''.join(f'<td>{esc(str(item.get(k) or "—")[:500])}</td>' for k in keys) + '</tr>' for source_id,item in data)
                    detail = ""
                    if selected_id:
                        selected = db.execute("SELECT payload FROM v2_source_rows WHERE table_name=? AND source_id=?", (source, selected_id)).fetchone()
                        if selected:
                            item = json.loads(selected[0])
                            fields = "".join(f'<dt>{esc(k.replace("_"," ").title())}</dt><dd>{esc(str(v) if v is not None else "—")}</dd>' for k,v in item.items() if k not in ("cpf_hash","cpf_encrypted","object_key","checksum"))
                            detail = f'<section class="card"><h2>Detalhes</h2><dl class="source-detail">{fields}</dl><a href="{path}">← Voltar à lista</a></section>'
                    body = f'<p>{count} registro(s) do snapshot operacional da V2.</p>{detail}<div class="table-wrap"><table><thead><tr><th></th>{headings}</tr></thead><tbody>{entries or "<tr><td>Nenhum registro.</td></tr>"}</tbody></table></div>'
                    if page_count > 1:
                        previous = f'<a href="{path}?page={page_number-1}">← Anterior</a>' if page_number > 1 else '<span></span>'
                        following = f'<a href="{path}?page={page_number+1}">Próxima →</a>' if page_number < page_count else '<span></span>'
                        body += f'<nav class="data-pages" aria-label="Páginas de registros">{previous}<span>Página {page_number} de {page_count}</span>{following}</nav>'
                    if path == "/administracao/auditoria" and user["role"] == "ADMIN":
                        events = db.execute("SELECT e.action,e.conversation_id,e.message_id,e.created_at,u.name FROM local_chat_audit e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT 100").fetchall()
                        chat_rows = "".join(f'<tr><td>{esc(e["created_at"])}</td><td>{esc(e["name"])}</td><td>{esc(e["action"])}</td><td>{esc(e["conversation_id"] or "—")}</td><td>{esc(e["message_id"] or "—")}</td></tr>' for e in events)
                        body += f'<section class="card"><h2>Atividade do chat · metadados sem conteúdo</h2><div class="table-wrap"><table><thead><tr><th>Data</th><th>Ator</th><th>Ação</th><th>Conversa</th><th>Mensagem</th></tr></thead><tbody>{chat_rows or "<tr><td colspan=5>Nenhuma atividade do chat.</td></tr>"}</tbody></table></div></section>'
                    if path in LOCAL_CONTENT:
                        local_kind = LOCAL_CONTENT[path]
                        local_rows = db.execute("SELECT id,title,category,body,updated_at FROM local_content WHERE kind=? ORDER BY updated_at DESC", (local_kind,)).fetchall()
                        local_entries = "".join(f'<tr><td><a href="{path}?local={r["id"]}">{esc(r["title"])}</a></td><td>{esc(r["category"])}</td><td>{esc(r["updated_at"])}</td></tr>' for r in local_rows)
                        local_id = (parse_qs(urlsplit(self.path).query).get("local") or [""])[0]
                        if local_id.isdigit():
                            selected_local = db.execute("SELECT title,category,body FROM local_content WHERE id=? AND kind=?", (int(local_id), local_kind)).fetchone()
                            if selected_local:
                                body += f'<section class="card"><h2>{esc(selected_local["title"])}</h2><p>{esc(selected_local["category"])}</p><div class="document-body">{esc(selected_local["body"])}</div></section>'
                        body += f'<section class="card"><h2>Registros locais</h2><div class="table-wrap"><table><thead><tr><th>Título</th><th>Categoria</th><th>Atualizado</th></tr></thead><tbody>{local_entries or "<tr><td colspan=3>Nenhum registro local.</td></tr>"}</tbody></table></div></section>'
                        body += f'<section class="card"><h2>Novo {esc(title.lower())}</h2><form method="post" action="{path}" class="fields"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><label>Título<input name="title" maxlength="160" required></label><label>Categoria<input name="category" maxlength="80"></label><label class="full-field">Conteúdo<textarea name="body" rows="8" maxlength="50000" required></textarea></label><button>Salvar</button></form></section>'
                    if path == "/rotinas/calendario":
                        local_events = db.execute("SELECT id,title,due_date,category,status FROM local_calendar_events ORDER BY due_date,id").fetchall()
                        event_entries = "".join(f'<tr><td>{esc(e["due_date"])}</td><td>{esc(e["title"])}</td><td>{esc(e["category"])}</td><td>{esc(e["status"])}</td><td><form method="post" action="/rotinas/calendario/{e["id"]}/concluir"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><button>{"Reabrir" if e["status"]=="CONCLUIDO" else "Concluir"}</button></form></td></tr>' for e in local_events)
                        body += f'<section class="card"><h2>Atividades locais</h2><div class="table-wrap"><table><thead><tr><th>Data</th><th>Atividade</th><th>Categoria</th><th>Situação</th><th></th></tr></thead><tbody>{event_entries or "<tr><td colspan=5>Nenhuma atividade local.</td></tr>"}</tbody></table></div></section>'
                        body += f'<section class="card"><h2>Novo evento</h2><form method="post" action="/rotinas/calendario" class="fields"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><label>Título<input name="title" maxlength="160" required></label><label>Prazo<input name="due_date" type="date" required></label><label>Categoria<input name="category" maxlength="80"></label><label class="full-field">Observações<textarea name="notes" rows="4" maxlength="2000"></textarea></label><button>Salvar evento</button></form></section>'
                else:
                    body = '<p>Esta área ainda não dispõe de dados locais suficientes para reproduzir a operação da V2.</p>'
        if path in ("/cadastros/pessoas", "/cadastros/vinculos", "/cadastros/empresas") and user["role"] == "ADMIN":
            with connect(self.server.database) as db:
                scan = sib_scan_service.summary(db)
            status = "Em consulta" if scan["running"] else "Finalizando consulta ativa" if scan["inFlight"] else "Pausada" if scan["started"] and not scan["done"] else "Faixa consultada" if scan["done"] else "Pronta para iniciar"
            stop = (f'<form method="post" action="/cadastros/sib/parar"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><input type="hidden" name="return_to" value="{esc(path)}"><button>Parar busca SIB</button></form>') if scan["running"] else ""
            body = (f'<section class="card"><h2>Cadastros SIB · {status}</h2><p>{scan["records"]} vínculo(s) e {scan["companies"]} empresa(s) trazidos · {scan["checked"]} matrículas consultadas.</p><p>Ocorrências e retornos inconclusivos ficam centralizados em Conflitos.</p><div class="row"><a href="/central-dp/integracoes#sib-scan">Iniciar ou continuar importação em lotes de 50</a><a href="/cadastros/conflitos#sib-issues">Conferir ocorrências em Conflitos</a>{stop}<a href="{esc(self.path)}">Atualizar cadastros</a></div></section>') + body
        module_body = body if rich_page or path in ("/central-dp/processos", "/cadastros/pessoas", "/cadastros/vinculos", "/cadastros/conflitos", "/cadastros/empresas") else f'<section class="card">{body}</section>'
        content = f'<div class="workspace module-workspace"><p class="eyebrow">{esc(group)}</p><h1>{esc(title)}</h1>{module_body}<p class="module-source">Fonte: exportações da V2 publicadas em 09/10/2026 e banco SQLite local.</p></div>'
        self.page(title, content, user)

    def cases_page(self, user, kind: str | None = None) -> None:
        params = parse_qs(urlsplit(self.path).query)
        if not kind:
            from .cases_ui import render as render_cases
            with connect(self.server.database) as db:
                self.page("Casos e checklists", render_cases(db, user, params), user)
            return
        search = (params.get("q") or [""])[0][:100]
        where, values = [], []
        if kind:
            where.append("kind=?")
            values.append(kind)
        if search:
            where.append("(title LIKE ? OR reference LIKE ?)")
            values.extend([f"%{search}%", f"%{search}%"])
        clause = " WHERE " + " AND ".join(where) if where else ""
        selected_status = (params.get("status") or [""])[0]
        if selected_status in STATUS_LABELS:
            where.append("status=?")
            values.append(selected_status)
            clause = " WHERE " + " AND ".join(where)
        else:
            selected_status = ""
        with connect(self.server.database) as db:
            rows = db.execute("SELECT id,kind,title,reference,status,due_date,owner_id FROM process_cases" + clause + " ORDER BY id DESC LIMIT 100", values).fetchall()
            users = db.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
            v2_cases = [json.loads(r[0]) for r in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='cases' ORDER BY source_id LIMIT 100").fetchall()] if not kind else []
            from_process = (params.get("from_process") or [""])[0][:128]
            process_row = db.execute("SELECT payload FROM v2_source_rows WHERE table_name='procedures' AND source_id=?", (from_process,)).fetchone() if from_process else None
            process_prefill = json.loads(process_row[0]) if process_row else {}
        title = KIND_LABELS[kind] if kind else "Casos e checklists"
        user_names = {row["id"]: row["name"] for row in users}
        entries = "".join(f'<tr><td><a href="/central-dp/casos/{row["id"]}">{esc(row["title"])}</a></td><td>{esc(KIND_LABELS[row["kind"]])}</td><td>{esc(row["reference"])}</td><td>{esc(STATUS_LABELS[row["status"]])}</td><td>{esc(user_names.get(row["owner_id"], "Sem responsável"))}</td><td>{esc(row["due_date"] or "—")}</td></tr>' for row in rows) or '<tr><td colspan="6">Nenhum caso nesta seleção.</td></tr>'
        options = "".join(f'<option value="{key}"{" selected" if key==kind else ""}>{esc(label)}</option>' for key, label in KIND_LABELS.items())
        statuses = '<option value="">Todas as situações</option>' + "".join(f'<option value="{key}"{" selected" if key==selected_status else ""}>{esc(label)}</option>' for key,label in STATUS_LABELS.items())
        owner_options = '<option value="">Sem responsável</option>' + "".join(f'<option value="{row["id"]}">{esc(row["name"])}</option>' for row in users)
        old_entries = "".join(f'<tr><td>{esc(r.get("title"))}</td><td>{esc(r.get("priority"))}</td><td>{esc(r.get("status"))}</td><td>{esc(r.get("due_at") or "—")}</td></tr>' for r in v2_cases)
        old_section = f'<section class="card"><h2>Casos importados da V2</h2><div class="table-wrap"><table><thead><tr><th>Caso</th><th>Prioridade</th><th>Situação</th><th>Prazo</th></tr></thead><tbody>{old_entries or "<tr><td colspan=4>Nenhum caso no snapshot.</td></tr>"}</tbody></table></div></section>' if not kind else ''
        process_code = from_process.rsplit(":", 1)[-1] if process_prefill else ""
        process_reference = f"Processo {process_code}"[:100] if process_code else ""
        content = f'<div class="workspace"><p class="eyebrow">CENTRAL DP</p><h1>{esc(title)}</h1><p>Demandas, responsáveis, prazos e etapas auditadas.</p>{old_section}<section class="card"><h2>Casos locais</h2><form method="get" class="row"><label>Buscar<input name="q" value="{esc(search)}" placeholder="Título ou referência"></label><label>Situação<select name="status">{statuses}</select></label><button>Filtrar</button></form><div class="table-wrap"><table><thead><tr><th>Caso</th><th>Tipo</th><th>Referência</th><th>Situação</th><th>Responsável</th><th>Prazo</th></tr></thead><tbody>{entries}</tbody></table></div></section><section class="card" id="novo"><h2>Novo caso</h2>{f'<p class="notice">Caso iniciado a partir do processo {esc(process_prefill.get("title"))}.</p>' if process_prefill else ''}<form method="post" action="/central-dp/casos" class="fields"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}"><label>Tipo<select name="kind">{options}</select></label><label>Título<input name="title" maxlength="160" required value="{esc(process_prefill.get("title") or "")}"></label><label>Referência<input name="reference" maxlength="100" value="{esc(process_reference)}"></label><label>Prazo<input name="due_date" type="date"></label><label>Responsável<select name="owner_id">{owner_options}</select></label><button>Criar caso</button></form></section></div>'
        self.page(title, content, user)

    def case_page(self, user, case_id: int, fragment: bool = False) -> None:
        query=parse_qs(urlsplit(self.path).query)
        return_scope=(query.get("return_home_scope") or [""])[0]
        back_href=f"/central-dp/inicio?scope={'team' if return_scope=='team' else 'mine'}" if return_scope in ("mine","team") else "/central-dp/casos"
        with connect(self.server.database) as db:
            case = db.execute("SELECT * FROM process_cases WHERE id=?", (case_id,)).fetchone()
            if not case:
                if fragment:
                    self.respond_json({"error": "Caso não encontrado."}, 404)
                else:
                    self.page("Não encontrado", '<div class="workspace"><h1>Caso não encontrado</h1></div>', user, 404)
                return
            if user["role"] not in ("ADMIN","GESTOR") and case["owner_id"]!=user["id"] and case["created_by"]!=user["id"]:
                if fragment:
                    self.respond_json({"error": "Este caso está fora do seu escopo."}, 403)
                else:
                    self.page("Acesso negado", '<div class="workspace"><h1>Este caso está fora do seu escopo.</h1><a href="/central-dp/casos">Voltar aos casos atribuídos</a></div>', user, 403)
                return
            checklist = db.execute("SELECT id,title,done,required FROM case_checklist WHERE case_id=? ORDER BY position,id", (case_id,)).fetchall()
            events = db.execute("SELECT action,detail,created_at FROM case_events WHERE case_id=? ORDER BY id DESC LIMIT 30", (case_id,)).fetchall()
            users = db.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
            recurrence = db.execute("SELECT active FROM local_case_recurrence_rules WHERE rule_id=?",(case["recurrence_rule_id"],)).fetchone() if case["recurrence_rule_id"] else None
            client = db.execute("SELECT json_extract(payload,'$.name') FROM v2_source_rows WHERE table_name='clients' AND (source_id=? OR json_extract(payload,'$.id')=?) LIMIT 1",(case["client_id"] or "",case["client_id"] or "")).fetchone() if case["client_id"] else None
            local_client = db.execute("SELECT name FROM local_clients WHERE 'local:'||id=?",(case["client_id"],)).fetchone() if case["client_id"] and not client else None
        return_field=f'<input type="hidden" name="return_home_scope" value="{return_scope}">' if return_scope in ("mine","team") else ""
        lines = "".join(f'<li><form method="post" action="/central-dp/casos/{case_id}/check/{row["id"]}"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}">{return_field}<button class="check" title="Alternar conclusão">{"☑" if row["done"] else "☐"}</button> {esc(row["title"])} {"<small>Obrigatório</small>" if row["required"] else ""}</form></li>' for row in checklist) or "<li>Nenhum item ainda.</li>"
        history = "".join(f'<li><time>{esc(row["created_at"])}</time> · {esc(row["action"])} · {esc(row["detail"])}</li>' for row in events)
        status_options = "".join(f'<option value="{key}"{" selected" if key==case["status"] else ""}>{esc(label)}</option>' for key, label in STATUS_LABELS.items())
        owner_options = "".join(f'<option value="{row["id"]}"{" selected" if row["id"]==case["owner_id"] else ""}>{esc(row["name"])}</option>' for row in users)
        client_name=(client[0] if client and client[0] else local_client[0] if local_client else "Geral / interno")
        content = f'<div class="workspace"><p class="eyebrow">CASOS E CHECKLISTS</p><h1>{esc(case["title"])}</h1><p>{esc(KIND_LABELS[case["kind"]])} · {esc(case["reference"])} · Prazo: {esc(case["due_date"] or "não definido")}{(" · Competência "+esc(case["competence"])) if case["competence"] else ""}</p><a href="{back_href}">← Voltar</a><section class="card"><h2>Dados do caso</h2><p>Cliente: {esc(client_name)} · Unidade: {esc(case["unit_id"] or "—")}</p><form class="fields" method="post" action="/central-dp/casos/{case_id}/editar"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}">{return_field}<label>Título<input name="title" maxlength="160" value="{esc(case["title"])}" required></label><label>Referência<input name="reference" maxlength="100" value="{esc(case["reference"])}"></label><label>Prioridade<select name="priority">{"".join(f'<option value="{k}"{" selected" if case["priority"]==k else ""}>{v}</option>' for k,v in (("LOW","Baixa"),("NORMAL","Normal"),("HIGH","Alta"),("URGENT","Urgente")))}</select></label><label>Prazo<input name="due_date" type="date" value="{esc(case["due_date"] or "")}"></label><label>Responsável<select name="owner_id"><option value="">Sem responsável</option>{owner_options}</select></label><label class="case-option"><input type="checkbox" name="renew_monthly" value="1"{" checked" if recurrence and recurrence["active"] else ""}> Renovar mensalmente ao concluir</label><label class="case-create-description">Descrição<textarea name="description" rows="3" maxlength="4000">{esc(case["description"] or "")}</textarea></label><button>Salvar dados</button></form></section><section class="card" id="checklist"><h2>Checklist</h2><ul class="checklist">{lines}</ul><form class="row" method="post" action="/central-dp/casos/{case_id}/itens"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}">{return_field}<label>Novo item<input name="title" maxlength="180" required></label><label><input type="checkbox" name="required" value="1"> Obrigatório</label><button>Adicionar</button></form></section><section class="card"><h2>Situação</h2><form class="row" method="post" action="/central-dp/casos/{case_id}/status"><input type="hidden" name="csrf" value="{esc(user["csrf_token"])}">{return_field}<select name="status">{status_options}</select><button>Salvar</button></form></section><section class="card"><h2>Histórico</h2><ul class="history">{history}</ul></section></div>'
        if fragment:
            self.respond_json({"title": str(case["title"]), "html": content})
        else:
            self.page(str(case["title"]), content, user)

    def _registry_action_post(self) -> None:
        user = self.principal()
        if not user:
            self.respond_json({"error": "Sua sessão expirou. Entre novamente."}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 65_536 or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond_json({"error": "Requisição inválida."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "strict"))
        except (UnicodeError, ValueError):
            self.respond_json({"error": "Conteúdo JSON inválido."}, 400)
            return
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("csrf") or ""), user["csrf_token"]):
            self.respond_json({"error": "Sessão expirada. Atualize a página e tente novamente."}, 403)
            return
        target = str(payload.get("target") or "")
        allowed_targets = {
            "/cadastros/pessoas", "/cadastros/vinculos", "/cadastros/revisoes",
            "/cadastros/empresas", "/cadastros/empresas/ficha", "/cadastros/sib/parar",
        }
        fields = payload.get("fields")
        if target not in allowed_targets or not isinstance(fields, dict):
            self.respond_json({"error": "Ação de cadastro inválida."}, 400)
            return
        normalized = {}
        for key, value in fields.items():
            if not isinstance(key, str) or not isinstance(value, (str, int, float)):
                self.respond_json({"error": "Campos da ação inválidos."}, 400)
                return
            normalized[key] = str(value)
        normalized["csrf"] = str(payload["csrf"])
        encoded = urlencode(normalized).encode("utf-8")
        if len(encoded) > 65_536:
            self.respond_json({"error": "Os dados enviados excederam o limite permitido."}, 413)
            return
        self._registry_json_action = True
        self.path = target
        self.rfile = io.BytesIO(encoded)
        if self.headers.get("Content-Length") is None:
            self.headers.add_header("Content-Length", str(len(encoded)))
        else:
            self.headers.replace_header("Content-Length", str(len(encoded)))
        if self.headers.get("Content-Type") is None:
            self.headers.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            self.headers.replace_header("Content-Type", "application/x-www-form-urlencoded")
        # Reuse the existing validated local form handlers, including their
        # role checks, date validation, audit trail, and SQLite-only writes.
        self.do_POST()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith('/webhooks/clicksign/') and operational_http.post(self, path):
            return
        if self._access_guard(path, write=True):
            return
        if operational_http.post(self, path):
            return
        if path == '/api/access':
            authenticated = self._chat_payload()
            if not authenticated:
                return
            user, payload = authenticated
            try:
                with connect(self.server.database) as db:
                    result = access_service.save(db, user, payload)
                self.respond_json({**result, 'message': 'Permissões atualizadas. O novo acesso já vale nas próximas consultas.'})
            except PermissionError as error:
                self.respond_json({'error': str(error)}, 403)
            except (ValueError, TypeError) as error:
                self.respond_json({'error': str(error)}, 400)
            return
        if path == "/api/ui/people-reports/export":
            authenticated = self._chat_payload()
            if not authenticated:
                return
            user, payload = authenticated
            try:
                self.respond_json(self.server.report_exports.start(user, payload.get("query")), 202)
            except PermissionError as error:
                self.respond_json({"error": str(error)}, 403)
            except ValueError as error:
                self.respond_json({"error": str(error)}, 400)
            return
        if path == '/api/ui/sector' or re.fullmatch(r'/api/ui/sector/credentials/\d+/reveal',path):
            authenticated = self._chat_payload()
            if not authenticated:return
            user,payload = authenticated
            try:
                with connect(self.server.database) as db:
                    if path == '/api/ui/sector':
                        result = sector_service.mutate(db,payload,user)
                    else:
                        result = sector_service.reveal_credential(db,path.split('/')[5],user)
                self.respond_json(result)
            except PermissionError as error:
                self.respond_json({'error':str(error)},403)
            except ValueError as error:
                self.respond_json({'error':str(error)},400)
            except sqlite3.DatabaseError:
                self.respond_json({'error':'Não foi possível concluir a operação do setor agora.'},503)
            return
        if path == "/api/ui/registry-actions":
            self._registry_action_post()
            return
        if path == "/api/ui/import-batches" or re.fullmatch(r"/api/ui/import-batches/\d+", path):
            authenticated = self._chat_payload()
            if not authenticated:
                return
            user, payload = authenticated
            if user["role"] != "ADMIN":
                self.respond_json({"error": "Somente Administrador pode preparar ou aplicar lotes."}, 403)
                return
            try:
                if path == "/api/ui/import-batches":
                    encoded = str(payload.get("contentBase64") or "")
                    if len(encoded) > 14 * 1024 * 1024:
                        raise ValueError("O CSV excede o limite de 10 MB.")
                    content = base64.b64decode(encoded, validate=True)
                    with connect(self.server.database) as db:
                        result = import_staging_service.stage_v2_csv(
                            db, str(payload.get("sourceName") or ""), content,
                            str(payload.get("companyScope") or "").strip(), int(user["id"]),
                        )
                else:
                    batch_id = int(path.rsplit("/", 1)[-1])
                    action = str(payload.get("action") or "")
                    if action == "apply":
                        with connect(self.server.database) as db:
                            batch = import_staging_service.preview_batch(db, batch_id)
                        if not batch["canApply"]:
                            raise ValueError("O lote precisa estar pendente e sem conflitos antes de aplicar.")
                        backup_database(self.server.database, "before-approved-csv-import")
                        with connect(self.server.database) as db:
                            result = import_staging_service.apply_batch(db, batch_id, int(user["id"]))
                    elif action == "reject":
                        with connect(self.server.database) as db:
                            result = import_staging_service.reject_batch(db, batch_id, int(user["id"]))
                    else:
                        raise ValueError("Escolha aplicar ou rejeitar o lote.")
                self.respond_json(result)
            except import_staging_service.StalePreviewError as error:
                self.respond_json({"error": str(error)}, 409)
            except (ValueError, binascii.Error) as error:
                self.respond_json({"error": str(error)}, 400)
            except LookupError as error:
                self.respond_json({"error": str(error)}, 404)
            except sqlite3.IntegrityError:
                self.respond_json({"error": "O lote ou cadastro mudou durante a operação. Atualize a prévia."}, 409)
            except sqlite3.DatabaseError:
                self.respond_json({"error": "Não foi possível salvar o lote de importação."}, 500)
            return
        if path == "/api/email/settings":
            self._email_settings_post()
            return
        if path == "/api/email/people-schedules":
            self._people_email_schedules_post()
            return
        if path == "/api/email/send-test":
            self._email_send_test_post()
            return
        upload_match = re.fullmatch(r"/api/ui/rescissions/(\d+)/requirements/(\d+)/upload", path)
        if upload_match:
            self._rescissions_upload_post(int(upload_match.group(1)), int(upload_match.group(2)))
            return
        real_email_match = re.fullmatch(r"/api/ui/rescissions/(\d+)/deliver-email", path)
        if real_email_match:
            self._rescissions_real_email_post(int(real_email_match.group(1)))
            return
        if path == "/api/ui/rescissions":
            self._rescissions_post()
            return
        if path == "/api/integrations/settings":
            self._integration_settings_post()
            return
        if path == "/api/integrations/sib/test":
            self._integration_sib_test_post()
            return
        if path == "/api/integrations/sib/employee":
            self._integration_sib_employee_post()
            return
        if path == "/api/integrations/sib/import":
            self._integration_sib_import_post()
            return
        if path == "/api/integrations/sib/scan":
            self._integration_sib_scan_post()
            return
        if path == "/api/integrations/sib/refresh":
            self._integration_sib_refresh_post()
            return
        if path == "/api/integrations/sib/auto-refresh":
            self._integration_sib_auto_refresh_post()
            return
        if path == "/api/integrations/sib/discovery":
            self._integration_sib_discovery_post()
            return
        if path == '/api/integrations/sib/occurrences':
            self._integration_sib_presence_post()
            return
        if path == "/api/integrations/sib/issues":
            self._integration_sib_issues_post()
            return
        if path == "/api/integrations/sib/batch":
            self._integration_sib_employee_post(batch=True)
            return
        if path == "/api/integrations/sib/resolve":
            self._integration_sib_employee_post(resolve=True)
            return
        if path == "/api/integrations/sib/absences/sync":
            user, payload = self._integration_read_json()
            if payload is None:
                return
            try:
                with connect(self.server.database) as db:
                    sib_absences_service.control(db, str(payload.get('action') or 'start'))
                self.respond_json({'ok': True, 'message': 'Monitoramento atualizado. O progresso foi preservado.'})
            except (ValueError, sqlite3.DatabaseError) as error:
                self.respond_json({'error': str(error)}, 400)
            return
        if path == "/api/integrations/sib/absences":
            self._integration_sib_employee_post(absences=True)
            return
        if path == "/api/integrations/selecty/test":
            self._integration_selecty_post(action="test")
            return
        if path == "/api/integrations/selecty/payroll":
            self._integration_selecty_post(action="list")
            return
        if path == "/api/integrations/selecty/detail":
            self._integration_selecty_post(action="detail")
            return
        if path == "/api/voxfy/settings":
            self._voxfy_settings_post()
            return
        if path == "/api/voxfy/test":
            self._voxfy_test_post()
            return
        if path == "/api/ui/temporary-extensions/real":
            self._temporary_extension_real_post()
            return
        if path == "/api/ui/temporary-extensions":
            self._temporary_extension_post()
            return
        if path == "/api/clicksign/test":
            self._clicksign_test_post()
            return
        if path == "/api/clicksign/send-test":
            self._clicksign_send_test_post()
            return
        if path == "/api/clicksign/settings":
            self._clicksign_settings_post()
            return
        if path == "/api/clicksign/signers":
            self._clicksign_signer_post()
            return
        if path == "/api/ui/calculators":
            self._calculators_post()
            return
        if path.startswith("/api/chat/"):
            self._chat_post()
            return
        if path == "/api/ui/messages":
            self._messages_post()
            return
        if path == "/api/ui/calendar":
            self._calendar_post()
            return
        if path == "/api/ui/lot-plans":
            self._lot_plans_post()
            return
        values = self.form()
        if values is None:
            return
        if path == "/entrar":
            ip = self.client_address[0]
            now = time.monotonic()
            self.server.login_attempts[ip] = [t for t in self.server.login_attempts.get(ip, []) if t > now - 600]
            if len(self.server.login_attempts[ip]) >= 10:
                self.respond("<h1>Tentativas excessivas. Aguarde dez minutos.</h1>", 429)
                return
            self.server.login_attempts[ip].append(now)
            with connect(self.server.database) as db:
                user = db.execute("SELECT * FROM users WHERE email=? AND active=1", (values.get("email", "").strip().lower(),)).fetchone()
                if user and verify_password(values.get("password", ""), user["password_salt"], user["password_hash"]):
                    token, digest, csrf, expires = new_session()
                    db.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)", (digest, user["id"], csrf, expires))
                    self.server.login_attempts.pop(ip, None)
                    self.redirect("/central-dp/inicio", self.cookie(token, 8 * 60 * 60))
                    return
            self.page("Acesso negado", '<div class="v2-page"><section class="v2-card"><h1>Credenciais inválidas</h1><a href="/entrar">Tentar novamente</a></section></div>', status=401)
            return
        user = self.require_user()
        if not user:
            return
        if not secrets.compare_digest(values.get("csrf", ""), user["csrf_token"]):
            self.respond("<h1>Formulário expirado.</h1>", 403)
            return
        if path == "/cadastros/sib/parar":
            if user["role"] != "ADMIN":
                self.respond("<h1>Somente Administrador pode controlar a importação.</h1>", 403)
                return
            sib_scan_service.control(self.server.database, None, user["id"], "pause")
            target = values.get("return_to", "")
            self.redirect(target if target in ("/cadastros/pessoas", "/cadastros/empresas", "/cadastros/vinculos") else "/cadastros/pessoas")
            return
        if path == "/central-dp/casos/v2/salvar":
            if user["role"] not in ("ADMIN", "GESTOR", "OPERADOR"):
                self.respond("<h1>Acesso somente para consulta.</h1>", 403)
                return
            source_id=values.get("source_case_id", "")[:180]
            title=values.get("new_item", "").strip()
            status=values.get("status", "")
            forward=values.get("submit_action")=="forward"
            if forward: status="DONE"
            owner=self._owner_id(values.get("owner_id", ""))
            next_owner=self._owner_id(values.get("next_owner_id", ""))
            due=values.get("due_date", "")
            next_due=values.get("next_due_date", "")
            if status not in ("OPEN","IN_PROGRESS","WAITING","DONE","CANCELLED","REOPENED") or values.get("priority","NORMAL") not in ("LOW","NORMAL","HIGH","URGENT") or owner is False or next_owner is False or (forward and (next_owner is None or not next_due)) or not self._valid_date(due) or not self._valid_date(next_due) or (values.get("renew_monthly")=="1" and not due) or len(title)>180 or len(values.get("comment", ""))>2000:
                self.respond("<h1>Não foi possível salvar. Confira situação, responsável e datas.</h1>",400)
                return
            with connect(self.server.database) as db:
                src=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='cases' AND source_id=?",(source_id,)).fetchone()
                if not src:
                    self.respond("<h1>Caso não encontrado.</h1>",404)
                    return
                original=json.loads(src["payload"])
                previous=db.execute("SELECT status,owner_id,priority,due_date,due_popup_enabled,renew_monthly,next_owner_id,next_due_date,deleted_at FROM local_v2_case_overrides WHERE source_case_id=?",(source_id,)).fetchone()
                if previous and previous["deleted_at"]:
                    self.respond("<h1>Este caso está arquivado. Restaure-o antes de editar.</h1>",409); return
                before_status=previous["status"] if previous and previous["status"] else original.get("status","OPEN")
                old_assignment=db.execute("SELECT owner_id FROM local_case_assignments WHERE source_case_id=?",(source_id,)).fetchone()
                old_owner=previous["owner_id"] if previous and previous["owner_id"] is not None else (old_assignment[0] if old_assignment else None)
                if user["role"]=="OPERADOR" and (old_owner!=user["id"] or owner!=old_owner):
                    self.respond("<h1>Operadores só podem editar casos atribuídos a eles, sem alterar o responsável.</h1>",403); return
                old_done={str(x["id"]):x["done"] for x in db.execute("SELECT id,done FROM local_v2_checklist_items WHERE source_case_id=?",(source_id,))}
                chosen=set(values.get("done_ids", "").split(",")) - {""}
                if status=="DONE":
                    required={str(x[0]) for x in db.execute("SELECT id FROM local_v2_checklist_items WHERE source_case_id=? AND required=1",(source_id,))}
                    if not required.issubset(chosen):
                        self.respond("<h1>Conclua os itens obrigatórios do checklist antes de encerrar.</h1>",409)
                        return
                db.execute("""INSERT INTO local_v2_case_overrides(source_case_id,status,owner_id,priority,due_date,due_popup_enabled,renew_monthly,next_owner_id,next_due_date,updated_by,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(source_case_id) DO UPDATE SET status=excluded.status,owner_id=excluded.owner_id,priority=excluded.priority,due_date=excluded.due_date,due_popup_enabled=excluded.due_popup_enabled,renew_monthly=excluded.renew_monthly,next_owner_id=excluded.next_owner_id,next_due_date=excluded.next_due_date,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (source_id,status,owner,values.get("priority","NORMAL"),due or None,int(values.get("popup")=="1"),int(values.get("renew_monthly")=="1"),next_owner,next_due or None,user["id"]))
                recurrence_rule_id="v2:"+source_id
                if values.get("renew_monthly")=="1":
                    db.execute("INSERT INTO local_case_recurrence_rules(rule_id,source_case_id,day_of_month,active,created_by) VALUES(?,?,?,?,?) ON CONFLICT(rule_id) DO UPDATE SET day_of_month=excluded.day_of_month,active=1,updated_at=CURRENT_TIMESTAMP",(recurrence_rule_id,source_id,int(due[8:10]),1,user["id"]))
                else:
                    db.execute("UPDATE local_case_recurrence_rules SET active=0,updated_at=CURRENT_TIMESTAMP WHERE source_case_id=?",(source_id,))
                changes=[]
                for label,before,after in (("responsável",str(old_owner or ""),str(owner or "")),("prioridade",str((previous["priority"] if previous else original.get("priority")) or "NORMAL"),values.get("priority","NORMAL")),("prazo",str((previous["due_date"] if previous else str(original.get("due_at") or "")[:10]) or ""),due),("alerta",str(bool(previous["due_popup_enabled"]) if previous else bool(original.get("due_popup_enabled"))),str(values.get("popup")=="1")),("recorrência mensal",str(bool(previous["renew_monthly"]) if previous else bool(original.get("renew_monthly"))),str(values.get("renew_monthly")=="1")),("próxima etapa",str(previous["next_due_date"] or "" if previous else ""),next_due)):
                    if before!=after: changes.append(f"{label} atualizado")
                if changes:
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"DADOS_ATUALIZADOS",", ".join(changes)))
                for item_id, was_done in old_done.items():
                    is_done=int(item_id in chosen)
                    if is_done!=int(was_done):
                        db.execute("UPDATE local_v2_checklist_items SET done=? WHERE id=? AND source_case_id=?",(is_done,item_id,source_id))
                        db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"CHECKLIST",item_id+ (" concluído" if is_done else " reaberto")))
                if title:
                    new_id="local:"+secrets.token_hex(12)
                    db.execute("INSERT INTO local_v2_checklist_items(id,source_case_id,title,required,position,created_by) VALUES(?,?,?,?,?,?)",(new_id,source_id,title,int(values.get("new_required")=="1"),999999,user["id"]))
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"ITEM_ADICIONADO",title))
                comment=values.get("comment", "").strip()
                if comment:
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"COMENTARIO",comment))
                if before_status!=status:
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"SITUACAO",status))
                if owner is not None:
                    db.execute("INSERT INTO local_case_assignments(source_case_id,owner_id,assigned_by) VALUES(?,?,?) ON CONFLICT(source_case_id) DO UPDATE SET owner_id=excluded.owner_id,assigned_by=excluded.assigned_by",(source_id,owner,user["id"]))
                else:
                    db.execute("DELETE FROM local_case_assignments WHERE source_case_id=?",(source_id,))
                if forward and not db.execute("SELECT 1 FROM local_v2_case_handoffs WHERE source_case_id=?",(source_id,)).fetchone():
                    parent_title=str(original.get("title") or "Caso V2")[:130]
                    next_title=("Próxima etapa: "+parent_title)[:160]
                    target_id=db.execute("INSERT INTO process_cases(kind,title,reference,due_date,created_by,owner_id) VALUES('GERAL',?,?,?,?,?)",(next_title,("Encaminhamento "+source_id)[:100],next_due or None,user["id"],next_owner)).lastrowid
                    db.execute("INSERT INTO local_v2_case_handoffs(source_case_id,target_case_id,created_by) VALUES(?,?,?)",(source_id,target_id,user["id"]))
                    db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'ENCAMINHADO_DE_CASO_V2',?)",(target_id,user["id"],"Origem V2: "+source_id))
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],"ENCAMINHADO",f"Caso V3 #{target_id} atribuído à próxima pessoa."))
                    if next_due:
                        event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,due_at,starts_at,category,notes,created_by) VALUES(?,?,?,?,'Caso DP',?,?)",(next_title,next_due,next_due,next_due+"T09:00",str(target_id),user["id"])).lastrowid
                        db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)",("local-case:"+str(target_id),event_id))
                # Keep a single locally editable calendar projection per source case.
                if due:
                    event=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?",("case:"+source_id,)).fetchone()
                    if event:
                        db.execute("UPDATE local_calendar_events SET title=?,due_date=?,due_at=?,starts_at=?||'T09:00',status=? WHERE id=?",(str(original.get("title") or "Caso DP")[:160],due,due,due,"CONCLUIDO" if status=="DONE" else "PENDENTE",event[0]))
                    else:
                        event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,due_at,starts_at,category,notes,status,created_by) VALUES(?,?,?,?,'Caso DP',?,?,?)",(str(original.get("title") or "Caso DP")[:160],due,due,due+"T09:00",source_id,"CONCLUIDO" if status=="DONE" else "PENDENTE",user["id"])).lastrowid
                        db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)",("case:"+source_id,event_id))
                else:
                    event=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?",("case:"+source_id,)).fetchone()
                    if event:
                        db.execute("UPDATE local_calendar_events SET archived=1 WHERE id=?",(event[0],))
                if status=="DONE" and values.get("renew_monthly")=="1" and due:
                    next_competence=_monthly_competence_after(due[:7])
                    checklist_template=db.execute("SELECT title,required,position FROM local_v2_checklist_items WHERE source_case_id=? ORDER BY position,id",(source_id,)).fetchall()
                    new_case_id=_ensure_monthly_case(db,recurrence_rule_id,next_competence,{"kind":"GERAL","title":str(original.get("title") or "Caso mensal")[:160],"description":str(original.get("description") or ""),"client_id":original.get("client_id"),"unit_id":original.get("unit_id"),"priority":values.get("priority","NORMAL")},owner,user["id"],checklist_template)
                    if new_case_id:
                        db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?, 'RECORRENCIA_GERADA',?)",(source_id,user["id"],f"Instância {next_competence} criada como caso local V3 #{new_case_id}."))
            return_scope=values.get("scope","mine") if values.get("scope") in ("mine","team") else "mine"
            result_view="done" if status=="DONE" else (values.get("view","active") if values.get("view") in ("active","done") else "active")
            self.redirect(f"/central-dp/casos?scope={return_scope}&view={result_view}&saved=1")
            return
        if path == "/central-dp/casos/v2/arquivar":
            if user["role"]!="ADMIN": self.respond("<h1>Somente ADMIN pode arquivar ou restaurar casos.</h1>",403); return
            source_id=values.get("source_case_id","")[:180]; action=values.get("archive_action","")
            if action not in ("delete","restore"):
                self.respond("<h1>Ação de arquivamento inválida.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("BEGIN IMMEDIATE")
                source=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='cases' AND source_id=?",(source_id,)).fetchone()
                if not source: self.respond("<h1>Caso não encontrado.</h1>",404); return
                original=json.loads(source["payload"]); overlay=db.execute("SELECT * FROM local_v2_case_overrides WHERE source_case_id=?",(source_id,)).fetchone()
                current_status=(overlay["status"] if overlay and overlay["status"] else original.get("status") or "OPEN")
                if overlay:
                    if action=="restore":
                        db.execute("UPDATE local_v2_case_overrides SET deleted_at=NULL,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE source_case_id=?",(user["id"],source_id))
                    else:
                        db.execute("UPDATE local_v2_case_overrides SET deleted_at=CURRENT_TIMESTAMP,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE source_case_id=?",(user["id"],source_id))
                else:
                    assignment=db.execute("SELECT owner_id FROM local_case_assignments WHERE source_case_id=?",(source_id,)).fetchone()
                    due_value=str(original.get("due_at") or "")[:10] or None
                    if action=="restore":
                        db.execute("INSERT INTO local_v2_case_overrides(source_case_id,status,owner_id,priority,due_date,deleted_at,updated_by) VALUES(?,?,?,?,?,NULL,?)",(source_id,current_status,assignment[0] if assignment else None,original.get("priority") or "NORMAL",due_value,user["id"]))
                    else:
                        db.execute("INSERT INTO local_v2_case_overrides(source_case_id,status,owner_id,priority,due_date,deleted_at,updated_by) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP,?)",(source_id,current_status,assignment[0] if assignment else None,original.get("priority") or "NORMAL",due_value,user["id"]))
                event_action="CASO_RESTAURADO" if action=="restore" else "CASO_ARQUIVADO"
                message="Caso restaurado à lista operacional; snapshot e histórico preservados." if action=="restore" else "Caso arquivado reversivelmente por ADMIN; nenhum dado de origem foi apagado."
                db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?,?,?)",(source_id,user["id"],event_action,message))
                mapped=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?",("case:"+source_id,)).fetchone()
                if mapped:
                    db.execute("UPDATE local_calendar_events SET archived=? WHERE id=?",(0 if action=="restore" else 1,mapped[0]))
                    if action=="restore": db.execute("UPDATE local_calendar_events SET status=? WHERE id=?",("CONCLUIDO" if current_status in ("DONE","CONCLUIDO") else "PENDENTE",mapped[0]))
            return_scope="team" if user["role"]=="ADMIN" else (values.get("scope") if values.get("scope") in ("mine","team") else "mine")
            next_view="deleted" if action=="delete" else ("done" if current_status in ("DONE","CONCLUIDO") else "active")
            self.redirect(f"/central-dp/casos?scope={return_scope}&view={next_view}")
            return
        if path == "/central-dp/casos/v2/alerta":
            if user["role"] not in ("ADMIN","GESTOR","OPERADOR"):
                self.respond("<h1>Acesso somente para consulta.</h1>",403); return
            source_id=values.get("source_case_id","")[:180]
            action=values.get("alert_action","")
            if action not in ("acknowledge","close","reopen","help"):
                self.respond("<h1>Ação de alerta inválida.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("BEGIN IMMEDIATE")
                source=db.execute("SELECT payload FROM v2_source_rows WHERE table_name='cases' AND source_id=?",(source_id,)).fetchone()
                overlay=db.execute("SELECT status,owner_id,due_date,due_popup_enabled,deleted_at FROM local_v2_case_overrides WHERE source_case_id=?",(source_id,)).fetchone()
                assignment=db.execute("SELECT owner_id FROM local_case_assignments WHERE source_case_id=?",(source_id,)).fetchone()
                if not source or not overlay or overlay["deleted_at"] or not overlay["due_popup_enabled"] or (overlay["owner_id"] if overlay["owner_id"] is not None else assignment[0] if assignment else None)!=user["id"]:
                    self.respond("<h1>Este alerta não está disponível para sua conta.</h1>",403); return
                case_status=overlay["status"] or json.loads(source["payload"]).get("status","OPEN")
                if case_status in ("DONE","CONCLUIDO","CANCELLED","CANCELADO"):
                    self.respond("<h1>O caso já não possui um alerta operacional ativo.</h1>",409); return
                due_text=(overlay["due_date"] or json.loads(source["payload"]).get("due_at") or "")[:10]
                try: due_day=date.fromisoformat(due_text)
                except ValueError: due_day=None
                if due_day is None or (due_day-date.today()).days>7:
                    self.respond("<h1>O prazo saiu da janela de alerta; atualize a lista de casos.</h1>",409); return
                current=db.execute("SELECT acknowledged_at FROM local_v2_case_alerts WHERE source_case_id=? AND user_id=?",(source_id,user["id"])).fetchone()
                if action=="acknowledge":
                    db.execute("INSERT INTO local_v2_case_alerts(source_case_id,user_id,acknowledged_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(source_case_id,user_id) DO UPDATE SET acknowledged_at=COALESCE(local_v2_case_alerts.acknowledged_at,CURRENT_TIMESTAMP),closed_at=NULL",(source_id,user["id"]))
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?, 'ALERTA_CIENTE','Ciência registrada; o caso segue pendente.')",(source_id,user["id"]))
                elif action=="close":
                    if not current or not current["acknowledged_at"]:
                        self.respond("<h1>Confirme primeiro que tomou ciência deste alerta.</h1>",409); return
                    db.execute("UPDATE local_v2_case_alerts SET closed_at=CURRENT_TIMESTAMP WHERE source_case_id=? AND user_id=?",(source_id,user["id"]))
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?, 'ALERTA_FECHADO','Aviso fechado; o caso continua em aberto.')",(source_id,user["id"]))
                elif action=="reopen":
                    db.execute("UPDATE local_v2_case_alerts SET closed_at=NULL WHERE source_case_id=? AND user_id=?",(source_id,user["id"]))
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?, 'ALERTA_REABERTO','Aviso reaberto enquanto o caso permanece pendente.')",(source_id,user["id"]))
                else:
                    message=values.get("help_message","").strip()[:1000]
                    target=self._owner_id(values.get("help_to",""))
                    if len(message)<3 or target is False:
                        self.respond("<h1>Descreva a dependência e confira a pessoa selecionada.</h1>",400); return
                    target_name=db.execute("SELECT name FROM users WHERE id=?",(target,)).fetchone()[0] if target else "equipe"
                    db.execute("INSERT INTO local_v2_case_events(source_case_id,actor_id,action,detail) VALUES(?,?, 'AJUDA_SOLICITADA',?)",(source_id,user["id"],f"Para {target_name}: {message}"))
                self.redirect(f"/central-dp/casos?scope=mine&view=active&alert_updated=1&open={quote(source_id,safe='')}#caso-{quote(source_id,safe='')}")
            return
        if path == "/sair":
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            token = jar.get("conexaodp_session")
            if token:
                with connect(self.server.database) as db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash(token.value),))
            self.redirect("/entrar", self.cookie("", 0))
            return
        if path == "/administracao/backup":
            if user["role"] != "ADMIN":
                self.respond("<h1>Acesso restrito.</h1>", 403)
                return
            backup_database(self.server.database)
            self.redirect(path)
            return
        if path == "/central-dp/chat":
            action = values.get("action", "")
            with connect(self.server.database) as db:
                if action == "create":
                    members = sorted({int(v) for v in values.get("members", "").split(",") if v.isdigit() and int(v) != user["id"]})
                    valid = {row[0] for row in db.execute(f"SELECT id FROM users WHERE active=1 AND id IN ({','.join('?' for _ in members)})", members).fetchall()} if members else set()
                    if not members or valid != set(members):
                        self.respond("<h1>Selecione pelo menos um participante ativo.</h1>", 400); return
                    title = values.get("title", "").strip()[:100]
                    if len(members)==1 and not title:
                        direct_key=":".join(map(str,sorted([user["id"],members[0]])))
                        existing=db.execute("SELECT id FROM local_conversations WHERE direct_key=?",(direct_key,)).fetchone()
                        if existing:
                            self.redirect("/central-dp/chat?id="+quote(existing["id"])); return
                        kind="DIRECT"
                    elif len(members)>=2 and title:
                        direct_key=None; kind="GROUP"
                    else:
                        self.respond("<h1>Conversa direta exige uma pessoa; grupo exige nome e pelo menos duas outras pessoas.</h1>",400); return
                    conv_id = secrets.token_urlsafe(18)
                    db.execute("INSERT INTO local_conversations(id,kind,title,direct_key,created_by) VALUES(?,?,?,?,?)", (conv_id,kind,title,direct_key,user["id"]))
                    db.executemany("INSERT INTO local_conversation_members(conversation_id,user_id) VALUES(?,?)", [(conv_id, user["id"]), *((conv_id, member) for member in members)])
                elif action == "send":
                    conv_id, body = values.get("conversation_id", ""), values.get("body", "").strip()
                    if not 1 <= len(body) <= 5000 or not db.execute("SELECT 1 FROM local_conversation_members WHERE conversation_id=? AND user_id=?", (conv_id,user["id"])).fetchone():
                        self.respond("<h1>Mensagem inválida ou conversa sem acesso.</h1>", 403); return
                    db.execute("INSERT INTO local_messages(conversation_id,sender_id,body) VALUES(?,?,?)", (conv_id,user["id"],body))
                    db.execute("UPDATE local_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conv_id,))
                    db.execute("UPDATE local_conversation_members SET last_read_at=CURRENT_TIMESTAMP WHERE conversation_id=? AND user_id=?", (conv_id,user["id"]))
                    self.redirect("/central-dp/chat?id="+quote(conv_id)); return
                else:
                    self.respond("<h1>Ação de chat inválida.</h1>", 400); return
            self.redirect("/central-dp/chat?id="+quote(conv_id)); return
        if path == "/rotinas/planejamento-lotes":
            from .calculators import number as parse_number, money
            try:
                title=values.get("title", "").strip()[:120]
                admission=values.get("admission", "")
                payroll_day=int(values.get("payroll_day", "5")); lot_count=int(values.get("lot_count", "2"))
                unit_cents=int(parse_number(values.get("unit_value", ""), "Valor unitário")*100)
                uses=int(values.get("uses_per_day", "2")); consider=values.get("consider_bank") == "yes"
                schedule=values.get("schedule", "")
                schedule_start=values.get("schedule_start", "")
                if not title or schedule not in ("", "WEEKDAYS_5X2", "ROTATING_6X1", "ROTATING_6X2"):
                    raise ValueError("Confira o título e a escala selecionada.")
                client_id=values.get("client_id", "").strip(); unit_id=values.get("unit_id", "").strip()
                if len(client_id)>100 or len(unit_id)>100: raise ValueError("Empresa ou unidade inválida.")
                with connect(self.server.database) as db:
                    bank=[dict(r) for r in db.execute("SELECT day,scope_type,scope_key,state FROM local_bank_relations WHERE state IS NOT NULL")]
                    for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days'"):
                        old=json.loads(row[0]); scope=old.get("scope_type", "GENERAL")
                        bank.append({"day":old.get("day"),"scope_type":scope,"scope_key":str(old.get("unit_id") or "") if scope=="UNIT" else str(old.get("client_id") or "") if scope=="CLIENT" else "","state":old.get("state")})
                    holidays=[json.loads(r[0]).get("day") for r in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calendar_holidays'") if json.loads(r[0]).get("day")]
                    result=propose_lots(admission=admission,payroll_day=payroll_day,lot_count=lot_count,unit_value_cents=unit_cents,uses_per_day=uses,consider_bank=consider,bank_rows=bank,holidays=holidays,schedule=schedule,schedule_start=schedule_start,client_id=client_id,unit_id=unit_id)
                    inputs={"admission":admission,"payroll_day":payroll_day,"lot_count":lot_count,"unit_value":money(parse_number(values["unit_value"])),"uses_per_day":uses,"consider_bank":consider,"schedule":schedule,"schedule_start":schedule_start,"client_id":client_id,"unit_id":unit_id}
                    cursor=db.execute("INSERT INTO local_benefit_plans(title,inputs_json,result_json,created_by) VALUES(?,?,?,?)",(title,json.dumps(inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),user["id"]))
                self.redirect("/rotinas/planejamento-lotes?id="+str(cursor.lastrowid)); return
            except (ValueError, KeyError) as error:
                self.respond(f"<h1>Não foi possível calcular o planejamento</h1><p>{esc(error)}</p><a href=/rotinas/planejamento-lotes>Voltar</a>",400); return
        if path.startswith("/rotinas/planejamento-lotes/") and path.endswith("/validar"):
            plan_id=path.strip("/").split("/")[2]
            if not plan_id.isdigit(): self.respond("<h1>Planejamento inválido.</h1>",400); return
            try:
                from .lot_planning import validate_lots
                with connect(self.server.database) as db:
                    plan=db.execute("SELECT inputs_json,result_json FROM local_benefit_plans WHERE id=? AND created_by=?",(int(plan_id),user["id"])).fetchone()
                    if not plan: self.respond("<h1>Planejamento não encontrado.</h1>",404); return
                    inputs=json.loads(plan["inputs_json"]); old_result=json.loads(plan["result_json"])
                    lots=[{"number":i,"from":values.get(f"from_{i}",""),"to":values.get(f"to_{i}",""),"pay_date":values.get(f"pay_date_{i}","")} for i in range(1,int(inputs["lot_count"])+1)]
                    bank=[dict(r) for r in db.execute("SELECT day,scope_type,scope_key,state FROM local_bank_relations WHERE state IS NOT NULL")]
                    for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='bank_relation_days'"):
                        old=json.loads(row[0]); scope=old.get("scope_type","GENERAL")
                        bank.append({"day":old.get("day"),"scope_type":scope,"scope_key":str(old.get("unit_id") or "") if scope=="UNIT" else str(old.get("client_id") or "") if scope=="CLIENT" else "","state":old.get("state")})
                    holidays=[json.loads(r[0]).get("day") for r in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='calendar_holidays'") if json.loads(r[0]).get("day")]
                    from .calculators import number as parse_number
                    result=validate_lots(admission=inputs["admission"],cutoff=old_result["cutoff_date"],lots=lots,expected_count=int(inputs["lot_count"]),unit_value_cents=int(parse_number(inputs["unit_value"])*100),uses_per_day=int(inputs["uses_per_day"]),consider_bank=bool(inputs["consider_bank"]),bank_rows=bank,holidays=holidays,schedule=inputs.get("schedule", ""),schedule_start=inputs.get("schedule_start", ""),client_id=inputs.get("client_id", ""),unit_id=inputs.get("unit_id", ""))
                    result["effective_payroll_date"]=old_result.get("effective_payroll_date","")
                    db.execute("UPDATE local_benefit_plans SET result_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(result,ensure_ascii=False),int(plan_id)))
                self.redirect("/rotinas/planejamento-lotes?id="+plan_id); return
            except (ValueError,KeyError,TypeError,json.JSONDecodeError) as error:
                self.respond(f"<h1>Datas do lote não validadas</h1><p>{esc(error)}</p><a href=/rotinas/planejamento-lotes?id={plan_id}>Voltar ao plano</a>",400); return
        if path == "/rotinas/calculadoras":
            calc_id=values.get("calculator_id", "")
            if calc_id not in {item[0] for item in PAYROLL_CALCULATORS}:
                self.respond("<h1>Calculadora não reconhecida.</h1>",400); return
            inputs={k:v for k,v in values.items() if k not in ("csrf","action","calculator_id")}
            try:
                with connect(self.server.database) as db:
                    inputs,result=_calculator_result_json(db,calc_id,inputs)
                    rendered=render_calculators(db,user,{},(calc_id,result,inputs))
                group,title=PAGE_LABELS[path]
                self.page(title,f'<div class="workspace module-workspace"><p class="eyebrow">{esc(group)}</p><h1>{esc(title)}</h1>{rendered}</div>',user)
            except ValueError as error:
                with connect(self.server.database) as db:
                    rendered=render_calculators(db,user,{"calc":[calc_id]},None,str(error))
                group,title=PAGE_LABELS[path]
                self.page(title,f'<div class="workspace module-workspace"><p class="eyebrow">{esc(group)}</p><h1>{esc(title)}</h1>{rendered}</div>',user,400)
            return
        if path == "/rotinas/calculadoras/salvar":
            try:
                calc_id=values["calculator_id"]
                payload=json.loads(values["inputs_json"])
                from .calculators import CALCULATORS
                if calc_id not in {item[0] for item in CALCULATORS} or not isinstance(payload,dict):
                    raise ValueError
                with connect(self.server.database) as db:
                    prepared,result=_calculator_result_json(db,calc_id,payload)
                    saved_inputs={key:value for key,value in prepared.items() if not str(key).startswith("_")}
                    db.execute("INSERT INTO local_calculations(title,calculator_id,inputs_json,result_json,expires_at,created_by) VALUES(?,?,?,?,?,?)",(values.get("title","Cálculo")[:120],calc_id,json.dumps(saved_inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),_calculator_expiration_date(),user["id"]))
                self.redirect("/rotinas/calculadoras?calc="+quote(calc_id)); return
            except (ValueError,KeyError,json.JSONDecodeError):
                self.respond("<h1>Cálculo inválido.</h1>",400); return
        if path.startswith("/rotinas/calculadoras/") and path.endswith("/pin"):
            calc_id=path.strip("/").split("/")[2]
            if not calc_id.isdigit(): self.respond("<h1>Cálculo inválido.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_calculations SET pinned=1-pinned WHERE id=? AND created_by=?",(int(calc_id),user["id"]))
            self.redirect("/rotinas/calculadoras"); return
        if path.startswith("/rotinas/tributos/"):
            from .tax_tables import (approve_candidate, create_candidate, edit_candidate,
                publish_candidate, reject_candidate, simulate as simulate_taxes,
                validate_candidate, verify_simulation_snapshot)
            from .tax_ui import render_taxes as render_tax_page
            action = values.get("action", "")
            view = "simulator" if path.endswith("/simular") else "candidates"
            selected_id = values.get("id", "")
            if path != "/rotinas/tributos/simular" and user["role"] != "ADMIN":
                self.respond("<h1>Acesso restrito.</h1>", 403)
                return
            try:
                if path == "/rotinas/tributos/candidata":
                    try:
                        parameters = json.loads(values.get("parameters_json", ""))
                    except (TypeError, json.JSONDecodeError) as error:
                        raise ValueError("O JSON de parâmetros é inválido.") from error
                    with connect(self.server.database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        version_id = create_candidate(db, user["id"], kind=values.get("kind", ""),
                            valid_from=values.get("valid_from", ""), valid_to=values.get("valid_to", ""),
                            category=values.get("category", ""), income_type=values.get("income_type", ""),
                            source_title=values.get("source_title", ""), source_agency=values.get("source_agency", ""),
                            source_url=values.get("source_url", ""), source_accessed_at=values.get("source_accessed_at", ""),
                            justification=values.get("justification", ""), parameters=parameters)
                    self.redirect(f"/rotinas/tributos?view=candidates&id={version_id}&notice=candidate-created")
                    return
                if path == "/rotinas/tributos/editar":
                    try:
                        parameters = json.loads(values.get("parameters_json", ""))
                    except (TypeError, json.JSONDecodeError) as error:
                        raise ValueError("O JSON de parâmetros é inválido.") from error
                    version_id = int(selected_id)
                    with connect(self.server.database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        edit_candidate(db, user["id"], version_id, valid_from=values.get("valid_from", ""),
                            valid_to=values.get("valid_to", ""), category=values.get("category", ""),
                            income_type=values.get("income_type", ""), source_title=values.get("source_title", ""),
                            source_agency=values.get("source_agency", ""), source_url=values.get("source_url", ""),
                            source_accessed_at=values.get("source_accessed_at", ""),
                            justification=values.get("justification", ""), parameters=parameters)
                    self.redirect(f"/rotinas/tributos?view=candidates&id={version_id}&notice=candidate-updated")
                    return
                if path == "/rotinas/tributos/validar":
                    version_id = int(selected_id)
                    with connect(self.server.database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        report = validate_candidate(db, user["id"], version_id)
                    notice = "candidate-valid" if report["state"] == "VALIDATED" else "candidate-invalid"
                    self.redirect(f"/rotinas/tributos?view=candidates&id={version_id}&notice={notice}")
                    return
                if path == "/rotinas/tributos/acao":
                    version_id = int(selected_id)
                    with connect(self.server.database) as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = db.execute("SELECT * FROM local_tax_versions WHERE id=?", (version_id,)).fetchone()
                        if not row:
                            self.respond("<h1>Candidata não encontrada.</h1>", 404)
                            return
                        if action == "approve":
                            approve_candidate(db, user["id"], version_id)
                            notice = "candidate-approved"
                        elif action == "publish":
                            publish_candidate(db, user["id"], version_id)
                            notice = "version-published"
                        elif action == "reject":
                            reject_candidate(db, user["id"], version_id, values.get("reason", ""))
                            notice = "candidate-rejected"
                        else:
                            raise ValueError("Ação de revisão indisponível.")
                    self.redirect(f"/rotinas/tributos?view=candidates&id={version_id}&notice={notice}")
                    return
                if path == "/rotinas/tributos/simular":
                    if action == "calculate":
                        inputs = {key: value for key, value in values.items() if key not in ("csrf", "action")}
                        with connect(self.server.database) as db:
                            simulation = simulate_taxes(db, inputs)
                        with connect(self.server.database) as db:
                            rendered = render_tax_page(db, user, {"view": ["simulator"]}, simulation=simulation)
                        self.page("Tributos", rendered, user)
                        return
                    if action == "save":
                        try:
                            inputs = json.loads(values.get("inputs_json", ""))
                            versions = json.loads(values.get("versions_json", ""))
                        except (TypeError, json.JSONDecodeError) as error:
                            raise ValueError("Resultado desatualizado. Recalcule antes de salvar.") from error
                        if not isinstance(inputs, dict) or not isinstance(versions, dict) or not verify_simulation_snapshot(user["csrf_token"], inputs, versions, values.get("snapshot_signature")):
                            raise ValueError("Resultado desatualizado. Recalcule antes de salvar.")
                        with connect(self.server.database) as db:
                            db.execute("BEGIN IMMEDIATE")
                            simulation = simulate_taxes(db, inputs, pinned_versions=versions)
                            if simulation.get("errors") or not any(simulation.get("results", {}).values()):
                                raise ValueError("Resultado desatualizado. Recalcule antes de salvar.")
                            title = str(values.get("title") or "").strip()[:120] or "Simulação tributária"
                            inss_id = simulation.get("versions", {}).get("inss")
                            irrf_id = simulation.get("versions", {}).get("irrf")
                            cursor = db.execute("""INSERT INTO local_tax_simulations(title,inss_version_id,irrf_version_id,
                                competence,payment_date,inputs_json,result_json,created_by) VALUES(?,?,?,?,?,?,?,?)""",
                                (title, inss_id, irrf_id, str(inputs.get("competence") or ""),
                                 str(inputs.get("paymentDate") or ""), json.dumps(simulation.get("inputs", {}), ensure_ascii=False),
                                 json.dumps(simulation, ensure_ascii=False), user["id"]))
                            saved_id = cursor.lastrowid
                        self.redirect(f"/rotinas/tributos?view=simulator&saved_id={saved_id}&notice=snapshot-saved")
                        return
                    if action == "delete":
                        try:
                            simulation_id = int(selected_id)
                        except (TypeError, ValueError) as error:
                            raise ValueError("Memória não encontrada nesta conta.") from error
                        with connect(self.server.database) as db:
                            deleted = db.execute("DELETE FROM local_tax_simulations WHERE id=? AND created_by=?", (simulation_id, user["id"]))
                            if deleted.rowcount != 1:
                                self.respond("<h1>Memória não encontrada nesta conta.</h1>", 404)
                                return
                        self.redirect("/rotinas/tributos?view=simulator&notice=snapshot-deleted")
                        return
                    raise ValueError("Ação de simulação indisponível.")
                self.respond("<h1>Rota tributária não encontrada.</h1>", 404)
                return
            except sqlite3.IntegrityError:
                message = "Outra revisão foi publicada enquanto você analisava esta candidata. Reabra a comparação."
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                message = str(error) or "Não foi possível concluir esta ação tributária. Revise os parâmetros."
            with connect(self.server.database) as db:
                rendered = render_tax_page(db, user, {"view": [view], "id": [selected_id]}, error=message)
            self.page("Tributos", rendered, user, 409 if "publicada enquanto" in message else 400)
            return
        if path == "/rotinas/tributos/criar":
            if user["role"]!="ADMIN": self.respond("<h1>Acesso restrito.</h1>",403); return
            try:
                from .tax_tables import validate
                kind=values.get("kind",""); competence=values.get("competence",""); source=values.get("source","").strip()
                params=validate(kind,competence,source,values.get("parameters_json",""))
                with connect(self.server.database) as db:
                    version=(db.execute("SELECT COALESCE(MAX(version),0)+1 FROM local_tax_versions WHERE kind=? AND competence=?",(kind,competence)).fetchone()[0])
                    cur=db.execute("INSERT INTO local_tax_versions(kind,competence,version,status,source,parameters_json,created_by) VALUES(?,?,?,'DRAFT',?,?,?)",(kind,competence,version,source,json.dumps(params,ensure_ascii=False),user["id"]))
                    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail) VALUES(?,?,'CREATED','Rascunho criado com validação estrutural.')",(cur.lastrowid,user["id"]))
                self.redirect("/rotinas/tributos?id="+str(cur.lastrowid)); return
            except (ValueError,KeyError,json.JSONDecodeError) as error:
                self.respond(f"<h1>Não foi possível criar a tabela</h1><p>{esc(error)}</p><a href=/rotinas/tributos>Voltar</a>",400); return
        if path == "/rotinas/tributos/acao":
            if user["role"]!="ADMIN": self.respond("<h1>Acesso restrito.</h1>",403); return
            try:
                version_id=int(values.get("id","")); action=values.get("action","")
                with connect(self.server.database) as db:
                    row=db.execute("SELECT * FROM local_tax_versions WHERE id=?",(version_id,)).fetchone()
                    if not row: self.respond("<h1>Tabela não encontrada.</h1>",404); return
                    if action=="approve" and row["status"]=="DRAFT":
                        db.execute("UPDATE local_tax_versions SET status='APPROVED',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(user["id"],version_id)); detail="Tabela aprovada."
                    elif action=="publish" and row["status"]=="APPROVED":
                        # Close the preceding version on the month before the new effective month.
                        year,month=map(int,row["competence"].split("-")); end_month=f"{year-1:04d}-12" if month==1 else f"{year:04d}-{month-1:02d}"
                        db.execute("UPDATE local_tax_versions SET status='SUPERSEDED',effective_to=? WHERE kind=? AND status='PUBLISHED' AND competence=?",(end_month,row["kind"],row["competence"]))
                        db.execute("UPDATE local_tax_versions SET effective_to=? WHERE kind=? AND status='PUBLISHED' AND competence<? AND (effective_to IS NULL OR effective_to>=?)",(end_month,row["kind"],row["competence"],row["competence"]))
                        db.execute("UPDATE local_tax_versions SET status='PUBLISHED',published_by=?,published_at=CURRENT_TIMESTAMP WHERE id=?",(user["id"],version_id)); detail="Tabela publicada para sua competência."
                    elif action=="reject" and row["status"] in ("DRAFT","APPROVED"):
                        reason=values.get("reason","").strip()[:500]
                        if len(reason)<3: raise ValueError("Informe o motivo da rejeição.")
                        db.execute("UPDATE local_tax_versions SET status='REJECTED',rejection_reason=? WHERE id=?",(reason,version_id)); detail=reason
                    else: raise ValueError("Transição de status inválida.")
                    db.execute("INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail) VALUES(?,?,?,?)",(version_id,user["id"],action.upper(),detail))
                self.redirect("/rotinas/tributos?id="+str(version_id)); return
            except (ValueError,TypeError) as error:
                self.respond(f"<h1>Não foi possível atualizar a versão</h1><p>{esc(error)}</p><a href=/rotinas/tributos>Voltar</a>",400); return
        if path.startswith("/rotinas/planejamento-lotes/") and path.endswith("/pin"):
            plan_id=path.strip("/").split("/")[2]
            if not plan_id.isdigit(): self.respond("<h1>Planejamento inválido.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_benefit_plans SET pinned=1-pinned,updated_at=CURRENT_TIMESTAMP WHERE id=? AND created_by=?",(int(plan_id),user["id"]))
            self.redirect("/rotinas/planejamento-lotes"); return
        if path in ("/cadastros/empresas", "/cadastros/empresas/ficha"):
            if not access_service.can(user, "registry.manage"):
                self.respond("<h1>Acesso restrito.</h1>", 403)
                return
            with connect(self.server.database) as db:
                items = company_catalog(db)
                if path == "/cadastros/empresas":
                    name = values.get("name", "").strip()
                    code = values.get("code", "").strip()
                    if not access_service.allowed_company(db, user, code=code, name=name):
                        raise PermissionError("Esta empresa está fora do seu recorte de acesso.")
                    if not 2 <= len(name) <= 200 or len(code) > 40 or any(item["name"].casefold() == name.casefold() or (code and item["code"] == code) for item in items):
                        self.respond("<h1>Empresa inválida ou já cadastrada.</h1>", 400)
                        return
                    client_id = db.execute("INSERT INTO local_clients(name,code,created_by) VALUES(?,?,?)", (name,code,user["id"])).lastrowid
                    self.redirect(f"/cadastros/empresas?id=local:{client_id}")
                    return
                key = values.get("client_key", "")
                if key not in {item["key"] for item in items}:
                    self.respond("<h1>Empresa não encontrada.</h1>", 404)
                    return
                access_service.require_subject(db, user, "empresas", key)
                current, _ = company_profile(db, key)
                for field, _, limit in PROFILE_FIELDS:
                    value = values.get(field, "").strip()
                    if len(value) > limit:
                        self.respond("<h1>Campo inválido.</h1>", 400)
                        return
                    current[field] = value
                db.execute("""INSERT INTO local_company_profiles(client_key,profile_json,updated_by)
                    VALUES(?,?,?) ON CONFLICT(client_key) DO UPDATE SET
                    profile_json=excluded.profile_json,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (key,json.dumps(current,ensure_ascii=False),user["id"]))
                self.redirect("/cadastros/empresas?id=" + quote(key))
                return
        if path in ("/cadastros/pessoas", "/cadastros/vinculos", "/cadastros/revisoes"):
            if not access_service.can(user, "registry.manage"):
                self.respond("<h1>Acesso restrito.</h1>", 403)
                return
            with connect(self.server.database) as db:
                if path == "/cadastros/pessoas":
                    name = values.get("name", "").strip()
                    cpf = re.sub(r"\D", "", values.get("cpf", ""))
                    email = values.get("email", "").strip().lower()
                    whatsapp = re.sub(r"\D", "", values.get("whatsapp", ""))
                    registration = values.get("registration", "").strip()
                    company = values.get("company", "").strip()
                    if not access_service.allowed_company(db, user, name=company):
                        raise PermissionError("Esta empresa está fora do seu recorte de acesso.")
                    unit = values.get("unit", "").strip()
                    contract = values.get("contract_type", "").strip()
                    admission = values.get("admission_date", "")
                    first_due = values.get("first_due_date", "")
                    second_due = values.get("second_due_date", "")
                    termination = values.get("termination_date", "")
                    reason_code = values.get("dismissal_reason_code", "").strip()
                    reason_label = values.get("dismissal_reason", "").strip()
                    allowed_reasons = {"PEDIDO_EMPREGADO", "SEM_JUSTA_CAUSA", "JUSTA_CAUSA", "TERMINO_CONTRATO", "ACORDO", "FALECIMENTO", "OUTRO"}
                    temp = "TEMPOR" in contract.upper()
                    invalid = (
                        not 2 <= len(name) <= 160 or len(cpf) not in (0, 11) or len(email) > 254
                        or len(whatsapp) > 15 or not 1 <= len(registration) <= 80
                        or not 1 <= len(company) <= 160 or len(unit) > 160
                        or contract not in ("CLT", "Temporário", "Prazo determinado", "Estágio", "Outro")
                        or not admission or not self._valid_date(admission) or not self._valid_date(termination)
                        or (termination and termination < admission)
                        or not self._valid_date(first_due) or not self._valid_date(second_due)
                        or (temp and (not first_due or not second_due))
                        or ((first_due or second_due) and (not first_due or not second_due or second_due <= first_due or first_due < admission))
                        or (termination and reason_code not in allowed_reasons)
                        or (reason_code and reason_code not in allowed_reasons)
                        or len(reason_label) > 160
                    )
                    if invalid:
                        self.respond("<h1>Cadastro inválido.</h1><p>Confira o CPF, os contatos, o vínculo, as datas, os vencimentos do temporário e o motivo do desligamento.</p>", 400)
                        return
                    person_id = db.execute("INSERT INTO registry_people(name,cpf,email,whatsapp,created_by) VALUES(?,?,?,?,?)",
                                            (name, cpf, email, whatsapp, user["id"])).lastrowid
                    employment_id = db.execute("""INSERT INTO registry_employments
                        (person_key,registration,company,unit,contract_type,admission_date,termination_date,
                         first_due_date,second_due_date,dismissal_reason_code,dismissal_reason,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (f"m:{person_id}",registration,company,unit,contract,admission,termination or None,
                         first_due,second_due,reason_code,reason_label,user["id"])).lastrowid
                    if termination:
                        rescissions_service.ensure_case(db, int(employment_id), int(user["id"]))
                    sync_temporary_registry_employment(db, int(employment_id))
                    self.redirect(f"/cadastros/pessoas?id=m:{person_id}")
                    return
                for access_key in ('person_key','subject_key'):
                    if values.get(access_key):
                        access_service.require_subject(db, user, 'pessoas', values[access_key])
                if values.get('kind') == 'LINK_EMPLOYMENT':
                    access_service.require_subject(db, user, 'pessoas', values.get('value', ''))
                people, employments, _ = project_registry(db)
                if path == "/cadastros/vinculos":
                    person_key = values.get("person_key", "")
                    registration = values.get("registration", "").strip()
                    company = values.get("company", "").strip()
                    if not access_service.allowed_company(db, user, name=company):
                        raise PermissionError("Esta empresa está fora do seu recorte de acesso.")
                    unit = values.get("unit", "").strip()
                    contract = values.get("contract_type", "").strip()
                    admission = values.get("admission_date", "")
                    termination = values.get("termination_date", "")
                    first_due = values.get("first_due_date", "")
                    second_due = values.get("second_due_date", "")
                    reason_code = values.get("dismissal_reason_code", "").strip()
                    reason_label = values.get("dismissal_reason", "").strip()
                    allowed_reasons = {"PEDIDO_EMPREGADO", "SEM_JUSTA_CAUSA", "JUSTA_CAUSA", "TERMINO_CONTRATO", "ACORDO", "FALECIMENTO", "OUTRO"}
                    temp = "TEMPOR" in contract.upper()
                    if (person_key not in people or not 1 <= len(registration) <= 80 or not 1 <= len(company) <= 160
                            or len(unit) > 160 or not 1 <= len(contract) <= 80 or not admission or not self._valid_date(admission)
                            or not self._valid_date(termination) or (termination and termination < admission)
                            or not self._valid_date(first_due) or not self._valid_date(second_due)
                            or (temp and (not first_due or not second_due))
                            or ((first_due or second_due) and (not first_due or not second_due or second_due <= first_due or first_due < admission))
                            or (termination and reason_code not in allowed_reasons)
                            or (reason_code and reason_code not in allowed_reasons) or len(reason_label) > 160):
                        self.respond("<h1>Vínculo inválido.</h1>", 400)
                        return
                    employment_id = db.execute("""INSERT INTO registry_employments
                        (person_key,registration,company,unit,contract_type,admission_date,termination_date,
                         first_due_date,second_due_date,dismissal_reason_code,dismissal_reason,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (person_key,registration,company,unit,contract,admission,termination or None,
                         first_due,second_due,reason_code,reason_label,user["id"])).lastrowid
                    if termination:
                        rescissions_service.ensure_case(db, int(employment_id), int(user["id"]))
                    sync_temporary_registry_employment(db, int(employment_id))
                    self.redirect("/cadastros/pessoas?id=" + quote(person_key))
                    return
                kind = values.get("kind", "")
                subject = values.get("subject_key", "")
                value = values.get("value", "").strip()
                note = values.get("note", "").strip()
                valid_employment = next((item for item in employments if item["key"] == subject and item["source"] == "V2"), None)
                valid_conflict = any(item["key"] == subject for item in project_conflicts(db)[0]) if kind == "CONFLICT_REVIEW" else False
                if (not 1 <= len(note) <= 1000 or (kind == "PERSON_NAME" and (subject not in people or not 2 <= len(value) <= 160))
                        or (kind == "LINK_EMPLOYMENT" and (not valid_employment or value not in people or value == valid_employment["person_key"]))
                        or (kind == "CONFLICT_REVIEW" and (not valid_conflict or value not in ("PENDING", "REVIEWED", "RESOLVED", "REOPENED")))
                        or kind not in ("PERSON_NAME", "LINK_EMPLOYMENT", "CONFLICT_REVIEW")):
                    self.respond("<h1>Revisão inválida.</h1>", 400)
                    return
                db.execute("INSERT INTO registry_reviews(kind,subject_key,value,note,actor_id) VALUES(?,?,?,?,?)",
                           (kind,subject,value,note,user["id"]))
                destination = {"PERSON_NAME":"/cadastros/pessoas", "LINK_EMPLOYMENT":"/cadastros/vinculos", "CONFLICT_REVIEW":"/cadastros/conflitos"}[kind]
                self.redirect(destination + "?id=" + quote(subject))
                return
        if path in LOCAL_CONTENT:
            title = values.get("title", "").strip()
            category = values.get("category", "").strip()
            body = values.get("body", "").strip()
            if not 1 <= len(title) <= 160 or len(category) > 80 or not 1 <= len(body) <= 50000:
                self.respond("<h1>Conteúdo inválido.</h1>", 400)
                return
            with connect(self.server.database) as db:
                db.execute("INSERT INTO local_content(kind,title,category,body,created_by) VALUES(?,?,?,?,?)", (LOCAL_CONTENT[path], title, category, body, user["id"]))
            self.redirect(path)
            return
        if path == "/rotinas/calendario":
            title = values.get("title", "").strip()
            due_date = values.get("due_date", "")
            category = values.get("category", "").strip()
            description = values.get("description", "").strip()
            recurrence=values.get("recurrence_type", "NONE")
            try: occurrences=int(values.get("repeat_count", "1"))
            except ValueError: occurrences=-1
            if not 1 <= len(title) <= 160 or not due_date or not self._valid_date(due_date) or len(category)>80 or len(description)>4000 or recurrence not in ("NONE","DAILY","WEEKLY","MONTHLY") or not 1<=occurrences<=24 or (recurrence=="NONE" and occurrences!=1):
                self.respond("<h1>Evento inválido.</h1>", 400)
                return
            repetitions=occurrences-1
            series_id=secrets.token_urlsafe(12) if repetitions else None
            with connect(self.server.database) as db:
                for index in range(occurrences):
                    current=date.fromisoformat(due_date)
                    if recurrence=="DAILY": current+=timedelta(days=index)
                    elif recurrence=="WEEKLY": current+=timedelta(days=index*7)
                    elif recurrence=="MONTHLY":
                        month=current.month-1+index
                        year=current.year+month//12; month=month%12+1
                        import calendar as month_calendar
                        current=current.replace(year=year,month=month,day=min(current.day,month_calendar.monthrange(year,month)[1]))
                    stamp=secrets.token_hex(10) if series_id else None
                    event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,starts_at,due_at,category,notes,description,priority,series_id,recurrence_type,recurrence_index,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (title,current.isoformat(),current.isoformat()+"T"+((values.get("starts_at") or "09:00")[:5]),current.isoformat(),category,description,description,values.get("priority","NORMAL"),series_id,recurrence,index+1,user["id"])).lastrowid
                    db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(event_id,user["id"],"CRIADO",f"Evento criado; série {series_id or 'único'}"))
            self.redirect(path)
            return
        if path == "/rotinas/calendario/banco":
            day,state,note=values.get("day",""),values.get("state","") or None,values.get("note","").strip()
            scope,key=values.get("scope_type","GENERAL"),values.get("scope_key","").strip()
            if scope not in ("GENERAL","CLIENT","UNIT") or (scope=="GENERAL" and key): self.respond("<h1>Escopo inválido.</h1>",400); return
            if not self._valid_date(day) or state not in (None,"YES","NO","OTHER") or len(note)>500 or len(key)>100:
                self.respond("<h1>Marcação inválida.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("INSERT INTO local_bank_relations(day,scope_type,scope_key,state,note,updated_by) VALUES(?,?,?,?,?,?) ON CONFLICT(day,scope_type,scope_key) DO UPDATE SET state=excluded.state,note=excluded.note,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",(day,scope,key,state,note,user["id"]))
            self.redirect("/rotinas/calendario?bank_day="+quote(day)+"&scope_type="+quote(scope)+"&scope_key="+quote(key)); return
        if path.startswith("/rotinas/calendario/check/"):
            item=path.rsplit("/",1)[-1]
            if not item.isdigit(): self.respond("<h1>Item inválido.</h1>",400); return
            with connect(self.server.database) as db:
                row=db.execute("SELECT event_id,title,done FROM local_calendar_checklist WHERE id=?",(int(item),)).fetchone()
                if row:
                    db.execute("UPDATE local_calendar_checklist SET done=1-done WHERE id=?",(int(item),))
                    db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(row["event_id"],user["id"],"CHECKLIST",f"{row['title']}: {'concluído' if not row['done'] else 'reaberto'}"))
            self.redirect("/rotinas/calendario"); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/concluir"):
            event_id=path.strip("/").split("/")[2]
            if not event_id.isdigit(): self.respond("<h1>Evento inválido.</h1>",400); return
            with connect(self.server.database) as db:
                row=db.execute("SELECT status FROM local_calendar_events WHERE id=? AND archived=0",(int(event_id),)).fetchone()
                if not row: self.respond("<h1>Evento não encontrado.</h1>",404); return
                new_status="CONCLUIDO" if row["status"]!="CONCLUIDO" else "PENDENTE"
                db.execute("UPDATE local_calendar_events SET status=? WHERE id=?",(new_status,int(event_id)))
                db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(int(event_id),user["id"],"STATUS",new_status))
            self.redirect("/rotinas/calendario"); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/editar"):
            event_id=path.strip("/").split("/")[2]; title=values.get("title","").strip(); due=values.get("due_date",""); desc=values.get("description","").strip()
            if not event_id.isdigit() or not title or len(title)>160 or not self._valid_date(due) or len(desc)>4000:
                self.respond("<h1>Dados de evento inválidos.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_calendar_events SET title=?,due_date=?,starts_at=?,due_at=?,category=?,description=?,notes=?,priority=? WHERE id=? AND archived=0",(title,due,due+"T"+(values.get("starts_at") or "09:00")[:5],due,values.get("category","")[:80],desc,desc,values.get("priority","NORMAL"),int(event_id)))
                db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(int(event_id),user["id"],"EDITADO",title))
            self.redirect("/rotinas/calendario?id="+event_id); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/arquivar"):
            event_id=path.strip("/").split("/")[2]
            if not event_id.isdigit(): self.respond("<h1>Evento inválido.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_calendar_events SET archived=1 WHERE id=?",(int(event_id),))
                db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(int(event_id),user["id"],"ARQUIVADO","Evento arquivado (dados preservados)."))
            self.redirect("/rotinas/calendario"); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/restaurar"):
            event_id=path.strip("/").split("/")[2]
            if not event_id.isdigit(): self.respond("<h1>Evento inválido.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("UPDATE local_calendar_events SET archived=0 WHERE id=?",(int(event_id),))
                db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(int(event_id),user["id"],"RESTAURADO","Evento restaurado ao calendário."))
            self.redirect("/rotinas/calendario"); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/checklist"):
            event_id=path.strip("/").split("/")[2]; label=values.get("title","").strip()
            if not event_id.isdigit() or not label or len(label)>180: self.respond("<h1>Item inválido.</h1>",400); return
            with connect(self.server.database) as db:
                db.execute("INSERT INTO local_calendar_checklist(event_id,title,created_by) VALUES(?,?,?)",(int(event_id),label,user["id"]))
                db.execute("INSERT INTO local_calendar_history(event_id,actor_id,action,detail) VALUES(?,?,?,?)",(int(event_id),user["id"],"CHECKLIST_ADICIONADO",label))
            self.redirect("/rotinas/calendario?id="+event_id); return
        if path.startswith("/rotinas/calendario/") and path.endswith("/concluir"):
            event_id = path.split("/")[3]
            if not event_id.isdigit():
                self.respond("<h1>Evento inválido.</h1>", 400)
                return
            with connect(self.server.database) as db:
                result = db.execute("UPDATE local_calendar_events SET status=CASE status WHEN 'PENDENTE' THEN 'CONCLUIDO' ELSE 'PENDENTE' END WHERE id=?", (int(event_id),))
                if not result.rowcount:
                    self.respond("<h1>Evento não encontrado.</h1>", 404)
                    return
            self.redirect("/rotinas/calendario")
            return
        if path in ("/central-dp/casos", "/central-dp/casos/criar"):
            if user["role"] not in ("ADMIN","GESTOR","OPERADOR"):
                self.respond("<h1>Acesso somente para consulta.</h1>",403); return
            kind, title = values.get("kind", ""), values.get("title", "").strip()
            description=values.get("description", "").strip()
            client_id=values.get("client_id", "").strip() or None
            unit_id=values.get("unit_id", "").strip() or None
            due_date=values.get("due_date", "")
            priority=values.get("priority", "NORMAL")
            renew_monthly=values.get("renew_monthly")=="1"
            popup=values.get("popup")=="1"
            next_due_date=values.get("next_due_date", "")
            checklist_lines=[]
            for raw in values.get("checklist", "").splitlines():
                item=raw.strip()
                if not item: continue
                required=item.startswith("*")
                if required: item=item[1:].strip()
                if item: checklist_lines.append((item,required))
            if kind not in KIND_LABELS or not (1 <= len(title) <= 160) or len(description)>4000 or len(values.get("checklist", ""))>6000 or len(checklist_lines)>100 or any(len(item)>180 for item,_ in checklist_lines) or priority not in ("LOW","NORMAL","HIGH","URGENT") or not self._valid_date(due_date) or not self._valid_date(next_due_date) or (renew_monthly and not due_date) or (popup and not due_date):
                self.respond("<h1>Caso inválido.</h1>", 400)
                return
            owner_id = self._owner_id(values.get("owner_id", ""))
            next_owner_id = self._owner_id(values.get("next_owner_id", ""))
            if owner_id is False or next_owner_id is False:
                self.respond("<h1>Responsável inválido.</h1>", 400)
                return
            if user["role"]=="OPERADOR":
                if owner_id not in (None,user["id"]) or next_owner_id not in (None,user["id"]): self.respond("<h1>Operadores não podem atribuir casos a outras pessoas.</h1>",403); return
                owner_id=user["id"]
            with connect(self.server.database) as db:
                request_id=values.get("request_id", "").strip()[:80] or secrets.token_urlsafe(24)
                db.execute("BEGIN IMMEDIATE")
                previous_request=db.execute("SELECT case_id,actor_id FROM local_case_creation_requests WHERE request_id=?",(request_id,)).fetchone()
                if previous_request:
                    if previous_request["actor_id"]!=user["id"]:
                        self.respond("<h1>Esta solicitação já foi utilizada.</h1>",409); return
                    self.redirect(f"/central-dp/casos/{previous_request['case_id']}"); return
                if client_id and not (db.execute("SELECT 1 FROM v2_source_rows WHERE table_name='clients' AND (source_id=? OR json_extract(payload,'$.id')=?)",(client_id,client_id)).fetchone() or (client_id.startswith("local:") and db.execute("SELECT 1 FROM local_clients WHERE 'local:'||id=?",(client_id,)).fetchone())):
                    self.respond("<h1>Cliente inválido.</h1>",400); return
                if unit_id and not db.execute("SELECT 1 FROM v2_source_rows WHERE table_name='units' AND (source_id=? OR json_extract(payload,'$.id')=?)",(unit_id,unit_id)).fetchone():
                    self.respond("<h1>Unidade inválida.</h1>",400); return
                recurrence_rule_id="manual:"+secrets.token_urlsafe(16) if renew_monthly else None
                cursor = db.execute("INSERT INTO process_cases(kind,title,reference,due_date,created_by,owner_id,description,client_id,unit_id,priority,recurrence_rule_id,competence,due_popup_enabled,next_owner_id,next_due_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (kind,title,values.get("reference", "")[:100],due_date or None,user["id"],owner_id,description,client_id,unit_id,priority,recurrence_rule_id,due_date[:7] if renew_monthly else None,int(popup),next_owner_id,next_due_date or None))
                case_id = cursor.lastrowid
                db.execute("INSERT INTO local_case_creation_requests(request_id,case_id,actor_id) VALUES(?,?,?)",(request_id,case_id,user["id"]))
                if recurrence_rule_id:
                    db.execute("INSERT INTO local_case_recurrence_rules(rule_id,day_of_month,created_by) VALUES(?,?,?)",(recurrence_rule_id,int(due_date[8:10]),user["id"]))
                    db.execute("INSERT INTO local_case_recurrence_instances(rule_id,competence,case_id,due_date) VALUES(?,?,?,?)",(recurrence_rule_id,due_date[:7],case_id,due_date))
                db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'CRIADO',?)", (case_id,user["id"],f'Responsável: {owner_id or "não definido"}'))
                if client_id or unit_id or description:
                    db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'DETALHES_INICIAIS',?)",(case_id,user["id"],"Caso vinculado e descrito."))
                for position,(item,required) in enumerate(checklist_lines):
                    db.execute("INSERT INTO case_checklist(case_id,title,required,position) VALUES(?,?,?,?)",(case_id,item,int(required),position))
                if checklist_lines:
                    db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'CHECKLIST_CRIADO',?)",(case_id,user["id"],f"{len(checklist_lines)} item(ns) adicionados."))
                if due_date:
                    event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,due_at,starts_at,category,notes,description,priority,created_by) VALUES(?,?,?,?,'Caso DP',?,?,?,?)",(title,due_date,due_date,due_date+"T09:00",str(case_id),description,priority,user["id"])).lastrowid
                    db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)",("local-case:"+str(case_id),event_id))
            return_scope=values.get("return_home_scope","")
            suffix=f"?return_home_scope={return_scope}" if return_scope in ("mine","team") else ""
            self.redirect(f"/central-dp/casos/{case_id}{suffix}")
            return
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["central-dp", "casos"] and parts[2].isdigit():
            if user["role"] not in ("ADMIN","GESTOR","OPERADOR"):
                self.respond("<h1>Acesso somente para consulta.</h1>",403); return
            case_id = int(parts[2])
            with connect(self.server.database) as db:
                access=db.execute("SELECT owner_id,created_by FROM process_cases WHERE id=?",(case_id,)).fetchone()
                if not access:
                    self.respond("<h1>Caso não encontrado.</h1>",404); return
                if user["role"] not in ("ADMIN","GESTOR") and access["owner_id"]!=user["id"] and access["created_by"]!=user["id"]:
                    self.respond("<h1>Este caso está fora do seu escopo.</h1>",403); return
                if not db.execute("SELECT 1 FROM process_cases WHERE id=?", (case_id,)).fetchone():
                    self.respond("<h1>Caso não encontrado.</h1>", 404)
                    return
                if parts[3] == "check" and parts[4].isdigit():
                    item_id = int(parts[4])
                    result = db.execute("UPDATE case_checklist SET done=1-done WHERE id=? AND case_id=?", (item_id,case_id))
                    if result.rowcount:
                        db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'CHECKLIST',?)", (case_id,user["id"],str(item_id)))
                    else:
                        self.respond("<h1>Item não encontrado.</h1>", 404)
                        return
                    self.redirect(f"/central-dp/casos/{case_id}")
                    return
        if len(parts) == 4 and parts[:2] == ["central-dp", "casos"] and parts[2].isdigit():
            if user["role"] not in ("ADMIN","GESTOR","OPERADOR"):
                self.respond("<h1>Acesso somente para consulta.</h1>",403); return
            case_id = int(parts[2])
            with connect(self.server.database) as db:
                current = db.execute("SELECT * FROM process_cases WHERE id=?", (case_id,)).fetchone()
                if not current:
                    self.respond("<h1>Caso não encontrado.</h1>", 404)
                    return
                if user["role"] not in ("ADMIN","GESTOR") and current["owner_id"]!=user["id"] and current["created_by"]!=user["id"]:
                    self.respond("<h1>Este caso está fora do seu escopo.</h1>",403); return
                if parts[3] == "editar":
                    title = values.get("title", "").strip()
                    reference = values.get("reference", "").strip()[:100]
                    due_date = values.get("due_date", "")
                    description=values.get("description", "").strip()[:4000]
                    priority=values.get("priority", "NORMAL")
                    renew_monthly=values.get("renew_monthly")=="1"
                    owner_id = self._owner_id(values.get("owner_id", ""))
                    if not 1 <= len(title) <= 160 or not self._valid_date(due_date) or owner_id is False or priority not in ("LOW","NORMAL","HIGH","URGENT") or (renew_monthly and not due_date):
                        self.respond("<h1>Dados inválidos. Confira título, prazo e responsável.</h1>", 400)
                        return
                    changes = []
                    prior_rule=db.execute("SELECT active FROM local_case_recurrence_rules WHERE rule_id=?",(current["recurrence_rule_id"],)).fetchone() if current["recurrence_rule_id"] else None
                    for field, before, after in (("título", current["title"], title), ("referência", current["reference"], reference), ("descrição", current["description"] or "", description), ("prioridade", current["priority"], priority), ("prazo", current["due_date"] or "", due_date), ("responsável", str(current["owner_id"] or ""), str(owner_id or "")), ("renovação mensal",str(bool(prior_rule and prior_rule["active"])),str(renew_monthly))):
                        if before != after:
                            changes.append(f"{field}: {before or '—'} → {after or '—'}")
                    if changes:
                        db.execute("UPDATE process_cases SET title=?,reference=?,description=?,priority=?,due_date=?,owner_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (title,reference,description,priority,due_date or None,owner_id,case_id))
                        db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'DADOS_ATUALIZADOS',?)", (case_id,user["id"],"; ".join(changes)[:500]))
                        calendar=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?",("local-case:"+str(case_id),)).fetchone()
                        if due_date and calendar:
                            db.execute("UPDATE local_calendar_events SET title=?,due_date=?,due_at=?,starts_at=?||'T09:00',description=?,priority=?,archived=0 WHERE id=?",(title,due_date,due_date,due_date,description,priority,calendar[0]))
                        elif due_date:
                            event_id=db.execute("INSERT INTO local_calendar_events(title,due_date,due_at,starts_at,category,notes,description,priority,created_by) VALUES(?,?,?,?,'Caso DP',?,?,?,?)",(title,due_date,due_date,due_date+"T09:00",str(case_id),description,priority,user["id"])).lastrowid
                            db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)",("local-case:"+str(case_id),event_id))
                        elif calendar:
                            db.execute("UPDATE local_calendar_events SET archived=1 WHERE id=?",(calendar[0],))
                    if renew_monthly:
                        rule_id=current["recurrence_rule_id"] or ("manual:"+secrets.token_urlsafe(16))
                        if not current["recurrence_rule_id"]:
                            db.execute("UPDATE process_cases SET recurrence_rule_id=?,competence=? WHERE id=?",(rule_id,due_date[:7],case_id))
                            db.execute("INSERT INTO local_case_recurrence_instances(rule_id,competence,case_id,due_date) VALUES(?,?,?,?)",(rule_id,due_date[:7],case_id,due_date))
                        db.execute("INSERT INTO local_case_recurrence_rules(rule_id,day_of_month,active,created_by) VALUES(?,?,1,?) ON CONFLICT(rule_id) DO UPDATE SET day_of_month=excluded.day_of_month,active=1,updated_at=CURRENT_TIMESTAMP",(rule_id,int(due_date[8:10]),user["id"]))
                    elif current["recurrence_rule_id"]:
                        db.execute("UPDATE local_case_recurrence_rules SET active=0,updated_at=CURRENT_TIMESTAMP WHERE rule_id=?",(current["recurrence_rule_id"],))
                elif parts[3] == "itens":
                    title = values.get("title", "").strip()
                    if not 1 <= len(title) <= 180:
                        self.respond("<h1>Item inválido.</h1>", 400)
                        return
                    required=int(values.get("required")=="1")
                    position=db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM case_checklist WHERE case_id=?",(case_id,)).fetchone()[0]
                    db.execute("INSERT INTO case_checklist(case_id,title,required,position) VALUES(?,?,?,?)", (case_id,title,required,position))
                    db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'ITEM_ADICIONADO',?)", (case_id,user["id"],title+(" (obrigatório)" if required else "")))
                elif parts[3] == "status":
                    status = values.get("status", "")
                    if status not in STATUS_LABELS:
                        self.respond("<h1>Situação inválida.</h1>", 400)
                        return
                    if status != current["status"]:
                        if status=="CONCLUIDO" and db.execute("SELECT 1 FROM case_checklist WHERE case_id=? AND required=1 AND done=0 LIMIT 1",(case_id,)).fetchone():
                            self.respond("<h1>Conclua os itens obrigatórios do checklist antes de encerrar.</h1>",409); return
                        db.execute("UPDATE process_cases SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,case_id))
                        db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'SITUACAO',?)", (case_id,user["id"],status))
                        calendar=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?",("local-case:"+str(case_id),)).fetchone()
                        if calendar:
                            db.execute("UPDATE local_calendar_events SET status=? WHERE id=?",("CONCLUIDO" if status=="CONCLUIDO" else "PENDENTE",calendar[0]))
                        if status=="CONCLUIDO" and current["recurrence_rule_id"] and current["due_date"]:
                            competence=current["competence"] or current["due_date"][:7]
                            next_competence=_monthly_competence_after(competence)
                            checks=db.execute("SELECT title,required,position FROM case_checklist WHERE case_id=? ORDER BY position,id",(case_id,)).fetchall()
                            created_id=_ensure_monthly_case(db,current["recurrence_rule_id"],next_competence,current,current["owner_id"],user["id"],checks)
                            if created_id:
                                db.execute("INSERT INTO case_events(case_id,actor_id,action,detail) VALUES(?,?,'RECORRENCIA_GERADA',?)",(case_id,user["id"],f"Instância {next_competence} criada como caso #{created_id}."))
                else:
                    self.respond("<h1>Não encontrado.</h1>", 404)
                    return
            self.redirect(f"/central-dp/casos/{case_id}")
            return
        self.respond("<h1>Não encontrado.</h1>", 404)


def serve(database: Path, host: str = "127.0.0.1", port: int = 8792) -> None:
    from .sib_runtime import database_server_lock
    with database_server_lock(database), Portal((host, port), database) as server:
        print(f"Conexão DP V3 Python: http://{host}:{server.server_address[1]}/entrar", flush=True)
        server.serve_forever()
