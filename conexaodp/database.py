"""Banco local isolado da V2 e da Central Jurídica."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .sib_import import SCHEMA as SIB_IMPORT_SCHEMA
from .sib_scan import SCHEMA as SIB_SCAN_SCHEMA
from .sib_refresh import SCHEMA as SIB_REFRESH_SCHEMA
from .sib_auto_refresh import SCHEMA as SIB_AUTO_REFRESH_SCHEMA
from .sib_discovery import SCHEMA as SIB_DISCOVERY_SCHEMA
from .sib_issues import SCHEMA as SIB_ISSUES_SCHEMA
from .sib_absences import SCHEMA as SIB_ABSENCES_SCHEMA
from .sib_operations import SCHEMA as SIB_OPERATIONS_SCHEMA
from .people_email_schedules import SCHEMA as PEOPLE_EMAIL_SCHEDULES_SCHEMA
from .sector import SCHEMA as SECTOR_SCHEMA
from .sector_import import SCHEMA as SECTOR_IMPORT_SCHEMA

try:
    _SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # Windows Python may not bundle the IANA database.
    _SAO_PAULO_TZ = timezone(timedelta(hours=-3), "America/Sao_Paulo")


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('ADMIN','GESTOR','OPERADOR')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    avatar_id TEXT NOT NULL DEFAULT 'man-1',
    mood_id TEXT NOT NULL DEFAULT 'serene',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash BLOB PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS process_cases (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('GERAL','ADMISSAO','RESCISAO','PRORROGACAO','AFASTAMENTO')),
    title TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ABERTO' CHECK(status IN ('ABERTO','EM_ANDAMENTO','CONCLUIDO','CANCELADO')),
    due_date TEXT,
    owner_id INTEGER REFERENCES users(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS cases_status_due ON process_cases(status,due_date);
CREATE TABLE IF NOT EXISTS local_case_creation_requests (
    request_id TEXT PRIMARY KEY,
    case_id INTEGER NOT NULL UNIQUE REFERENCES process_cases(id) ON DELETE RESTRICT,
    actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_case_recurrence_rules (
    rule_id TEXT PRIMARY KEY,
    source_case_id TEXT UNIQUE,
    day_of_month INTEGER NOT NULL CHECK(day_of_month BETWEEN 1 AND 31),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_case_recurrence_instances (
    rule_id TEXT NOT NULL REFERENCES local_case_recurrence_rules(rule_id) ON DELETE RESTRICT,
    competence TEXT NOT NULL,
    case_id INTEGER NOT NULL UNIQUE REFERENCES process_cases(id) ON DELETE RESTRICT,
    due_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(rule_id,competence)
);
CREATE TABLE IF NOT EXISTS local_case_assignments (
    source_case_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_case_assignments_owner ON local_case_assignments(owner_id);
CREATE TABLE IF NOT EXISTS case_checklist (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES process_cases(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES process_cases(id) ON DELETE RESTRICT,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Operational overlays for imported V2 cases. The immutable source snapshot remains untouched.
CREATE TABLE IF NOT EXISTS local_v2_case_overrides (
    source_case_id TEXT PRIMARY KEY,
    status TEXT,
    owner_id INTEGER REFERENCES users(id),
    priority TEXT,
    due_date TEXT,
    due_popup_enabled INTEGER NOT NULL DEFAULT 0 CHECK(due_popup_enabled IN (0,1)),
    renew_monthly INTEGER NOT NULL DEFAULT 0 CHECK(renew_monthly IN (0,1)),
    next_owner_id INTEGER REFERENCES users(id),
    next_due_date TEXT,
    deleted_at TEXT,
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_v2_checklist_items (
    id TEXT PRIMARY KEY,
    source_case_id TEXT NOT NULL,
    source_item_id TEXT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
    required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
    assignee_id INTEGER REFERENCES users(id),
    position INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_case_id,source_item_id)
);
CREATE INDEX IF NOT EXISTS local_v2_checklist_case ON local_v2_checklist_items(source_case_id,position);
CREATE TABLE IF NOT EXISTS local_v2_case_events (
    id INTEGER PRIMARY KEY,
    source_case_id TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_v2_case_events_case ON local_v2_case_events(source_case_id,id);
CREATE TABLE IF NOT EXISTS local_v2_case_handoffs (
    source_case_id TEXT PRIMARY KEY,
    target_case_id INTEGER NOT NULL UNIQUE REFERENCES process_cases(id) ON DELETE RESTRICT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_v2_case_alerts (
    id INTEGER PRIMARY KEY,
    source_case_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    acknowledged_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_case_id,user_id)
);
CREATE TABLE IF NOT EXISTS v2_employments (
    external_key TEXT PRIMARY KEY,
    registration TEXT NOT NULL,
    name TEXT NOT NULL,
    company TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    job_title TEXT NOT NULL DEFAULT '',
    contract_type TEXT NOT NULL DEFAULT '',
    admission_date TEXT,
    termination_date TEXT,
    termination_reason_code TEXT NOT NULL DEFAULT '',
    termination_reason TEXT NOT NULL DEFAULT '',
    in_active_export INTEGER NOT NULL DEFAULT 0,
    in_terminated_export INTEGER NOT NULL DEFAULT 0,
    in_admitted_export INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS v2_employments_name ON v2_employments(name);
CREATE INDEX IF NOT EXISTS v2_employments_company ON v2_employments(company);
CREATE INDEX IF NOT EXISTS v2_employments_dates ON v2_employments(admission_date,termination_date);
CREATE TABLE IF NOT EXISTS v2_import_log (
    id INTEGER PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_name, source_sha256)
);
CREATE TABLE IF NOT EXISTS local_import_batches (
    id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK(source_kind='V2_CSV'),
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    company_scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'STAGED' CHECK(status IN ('STAGED','APPLIED','REJECTED')),
    row_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT,
    UNIQUE(source_kind,source_name,source_sha256,company_scope)
);
CREATE TABLE IF NOT EXISTS local_import_items (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES local_import_batches(id) ON DELETE RESTRICT,
    line_number INTEGER NOT NULL,
    external_key TEXT NOT NULL DEFAULT '',
    normalized_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    expected_hash TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT 'REVIEW' CHECK(action IN ('NEW','UPDATE','NOOP','REVIEW')),
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    applied_at TEXT,
    UNIQUE(batch_id,line_number)
);
CREATE INDEX IF NOT EXISTS local_import_items_batch ON local_import_items(batch_id,id);
CREATE TABLE IF NOT EXISTS v2_source_rows (
    table_name TEXT NOT NULL,
    source_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(table_name, source_id)
);
CREATE TABLE IF NOT EXISTS local_content (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('PROCESSO','MENSAGEM','MANUAL')),
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Quick message and alert overlays are additive: imported V2 source rows remain immutable.
CREATE TABLE IF NOT EXISTS local_quick_messages (
    id TEXT PRIMARY KEY,
    source_id TEXT UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    client_id TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    normalized_text TEXT NOT NULL DEFAULT '',
    source_creator_id TEXT NOT NULL DEFAULT '',
    source_updated_by_id TEXT NOT NULL DEFAULT '',
    source_created_at TEXT,
    source_updated_at TEXT,
    created_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    updated_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS local_quick_messages_updated ON local_quick_messages(archived_at,updated_at DESC);
CREATE INDEX IF NOT EXISTS local_quick_messages_category ON local_quick_messages(category,archived_at);
CREATE TABLE IF NOT EXISTS local_dp_alerts (
    id TEXT PRIMARY KEY,
    source_id TEXT UNIQUE,
    source_sender_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    audience_type TEXT NOT NULL DEFAULT 'ALL' CHECK(audience_type IN ('ALL','INDIVIDUAL','GROUP')),
    created_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_from_v2 INTEGER NOT NULL DEFAULT 0 CHECK(imported_from_v2 IN (0,1)),
    sender_archived_at TEXT,
    request_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS local_dp_alerts_sender ON local_dp_alerts(created_by,created_at DESC);
CREATE TABLE IF NOT EXISTS local_dp_alert_recipients (
    alert_id TEXT NOT NULL REFERENCES local_dp_alerts(id) ON DELETE RESTRICT,
    source_user_id TEXT NOT NULL DEFAULT '',
    user_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    recipient_name TEXT NOT NULL DEFAULT '',
    recipient_email TEXT NOT NULL DEFAULT '',
    read_at TEXT,
    archived_at TEXT,
    PRIMARY KEY(alert_id,source_user_id)
);
CREATE INDEX IF NOT EXISTS local_dp_alert_recipient_inbox ON local_dp_alert_recipients(user_id,archived_at,read_at,alert_id);
CREATE TABLE IF NOT EXISTS local_messaging_events (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_messaging_events_entity ON local_messaging_events(entity_type,entity_id,id);
CREATE TABLE IF NOT EXISTS local_calendar_events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    due_date TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDENTE' CHECK(status IN ('PENDENTE','CONCLUIDO')),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_calendar_due ON local_calendar_events(due_date,status);
CREATE TABLE IF NOT EXISTS local_conversations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('DIRECT','GROUP')),
    title TEXT NOT NULL DEFAULT '',
    direct_key TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_conversation_members (
    conversation_id TEXT NOT NULL REFERENCES local_conversations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_at TEXT,
    PRIMARY KEY(conversation_id,user_id)
);
CREATE TABLE IF NOT EXISTS local_messages (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES local_conversations(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 5000),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_messages_conversation ON local_messages(conversation_id,id);
CREATE TABLE IF NOT EXISTS local_bank_relations (
    day TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'GENERAL' CHECK(scope_type IN ('GENERAL','CLIENT','UNIT')),
    scope_key TEXT NOT NULL DEFAULT '',
    state TEXT CHECK(state IN ('YES','NO','OTHER') OR state IS NULL),
    note TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(day,scope_type,scope_key)
);
CREATE TABLE IF NOT EXISTS local_bank_relation_history (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT '',
    old_state TEXT,
    new_state TEXT,
    old_note TEXT NOT NULL DEFAULT '',
    new_note TEXT NOT NULL DEFAULT '',
    actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_bank_relation_history_day ON local_bank_relation_history(day,scope_type,scope_key,id DESC);
CREATE TABLE IF NOT EXISTS local_calendar_month_notes (
    month TEXT PRIMARY KEY,
    body TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_calendar_history (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_calendar_source_map (
    source_id TEXT PRIMARY KEY,
    local_event_id INTEGER NOT NULL UNIQUE REFERENCES local_calendar_events(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS local_calendar_holidays (
    day TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY(day,name,state,city)
);
CREATE TABLE IF NOT EXISTS local_calendar_checklist (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES local_calendar_events(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_benefit_plans (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    employee_key TEXT,
    company_key TEXT,
    unit_key TEXT NOT NULL DEFAULT '',
    benefit_type TEXT,
    competence TEXT,
    expires_at TEXT,
    deleted_at TEXT,
    deleted_by INTEGER REFERENCES users(id),
    saved_by INTEGER REFERENCES users(id),
    source_snapshot_json TEXT NOT NULL DEFAULT '{}',
    row_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    expiration_noted_at TEXT,
    pinned_at TEXT,
    pinned_by INTEGER REFERENCES users(id),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_company_benefit_rules (
    id INTEGER PRIMARY KEY,
    company_key TEXT NOT NULL,
    unit_key TEXT NOT NULL DEFAULT '',
    benefit_type TEXT NOT NULL CHECK(benefit_type IN ('VT','VR')),
    payment_day INTEGER NOT NULL CHECK(payment_day IN (5,30)),
    payment_adjustment TEXT NOT NULL CHECK(payment_adjustment IN ('PREVIOUS_WORKDAY','NEXT_WORKDAY','NO_ADJUSTMENT')),
    holiday_benefit_rule TEXT NOT NULL CHECK(holiday_benefit_rule IN ('COUNT','EXCLUDE')),
    unit_cents INTEGER NOT NULL CHECK(unit_cents BETWEEN 0 AND 100000000),
    uses_per_day INTEGER NOT NULL CHECK(uses_per_day BETWEEN 1 AND 20),
    unit_mode TEXT NOT NULL DEFAULT 'UNSPECIFIED' CHECK(unit_mode IN ('UNSPECIFIED','PER_USE','PER_DAY','PER_MEAL')),
    february_30_policy TEXT NOT NULL DEFAULT 'UNRESOLVED' CHECK(february_30_policy IN ('UNRESOLVED','LAST_DAY','NEXT_MONTH')),
    admission_on_cutoff_policy TEXT NOT NULL DEFAULT 'UNRESOLVED' CHECK(admission_on_cutoff_policy IN ('UNRESOLVED','INCLUDE_CURRENT','NEXT_CYCLE')),
    zero_value_reason TEXT NOT NULL DEFAULT '',
    valid_from TEXT,
    source TEXT NOT NULL DEFAULT 'MANUAL',
    updated_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_key,unit_key,benefit_type)
);
CREATE TABLE IF NOT EXISTS local_company_benefit_rule_history (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES local_company_benefit_rules(id) ON DELETE RESTRICT,
    old_json TEXT NOT NULL,
    new_json TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_benefit_plan_audit (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_benefit_plan_revisions (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES local_benefit_plans(id) ON DELETE RESTRICT,
    row_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    saved_by INTEGER REFERENCES users(id),
    saved_at TEXT NOT NULL,
    UNIQUE(plan_id,row_version)
);
CREATE TABLE IF NOT EXISTS local_benefit_plan_idempotency (
    request_key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    plan_id INTEGER NOT NULL REFERENCES local_benefit_plans(id) ON DELETE RESTRICT,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_calculations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    calculator_id TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    expires_at TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_calculations_expiry ON local_calculations(pinned,expires_at);
CREATE TABLE IF NOT EXISTS local_tax_versions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('INSS','IRRF')),
    competence TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT','APPROVED','PUBLISHED','REJECTED','SUPERSEDED')),
    workflow_state TEXT NOT NULL DEFAULT 'COLLECTED',
    category TEXT NOT NULL DEFAULT 'EMPLOYEE',
    income_type TEXT NOT NULL DEFAULT 'SALARY_MONTHLY',
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT,
    source TEXT NOT NULL,
    source_title TEXT NOT NULL DEFAULT '',
    source_agency TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_accessed_at TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    validation_json TEXT NOT NULL DEFAULT '{}',
    parameters_json TEXT NOT NULL,
    rejection_reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    published_by INTEGER REFERENCES users(id),
    previous_version_id INTEGER REFERENCES local_tax_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    approved_at TEXT,
    published_at TEXT,
    effective_to TEXT,
    UNIQUE(kind,competence,version)
);
CREATE TABLE IF NOT EXISTS local_tax_history (
    id INTEGER PRIMARY KEY,
    tax_version_id INTEGER NOT NULL REFERENCES local_tax_versions(id) ON DELETE RESTRICT,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL DEFAULT 'SUCCESS',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_tax_simulations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    inss_version_id INTEGER REFERENCES local_tax_versions(id) ON DELETE RESTRICT,
    irrf_version_id INTEGER REFERENCES local_tax_versions(id) ON DELETE RESTRICT,
    competence TEXT NOT NULL DEFAULT '',
    payment_date TEXT NOT NULL DEFAULT '',
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_tax_simulations_owner ON local_tax_simulations(created_by,created_at DESC);
CREATE INDEX IF NOT EXISTS local_content_kind ON local_content(kind,updated_at);
CREATE INDEX IF NOT EXISTS v2_source_table ON v2_source_rows(table_name);
CREATE TABLE IF NOT EXISTS registry_people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    cpf TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS registry_employments (
    id INTEGER PRIMARY KEY,
    person_key TEXT NOT NULL,
    registration TEXT NOT NULL,
    company TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    contract_type TEXT NOT NULL DEFAULT '',
    admission_date TEXT NOT NULL,
    termination_date TEXT,
    first_due_date TEXT NOT NULL DEFAULT '',
    second_due_date TEXT NOT NULL DEFAULT '',
    dismissal_reason_code TEXT NOT NULL DEFAULT '',
    dismissal_reason TEXT NOT NULL DEFAULT '',
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS registry_employments_person ON registry_employments(person_key);
CREATE TABLE IF NOT EXISTS local_registry_documents (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES registry_people(id) ON DELETE CASCADE,
    storage_key TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_registry_documents_person ON local_registry_documents(person_id,id DESC);
CREATE TABLE IF NOT EXISTS local_rescission_cases (
    id INTEGER PRIMARY KEY,
    employment_id INTEGER UNIQUE REFERENCES registry_employments(id) ON DELETE CASCADE,
    dismissal_date TEXT NOT NULL,
    deadline_date TEXT NOT NULL,
    reason_code TEXT NOT NULL DEFAULT '',
    reason_label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'EM_DOCUMENTACAO' CHECK(status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO','CONCLUIDO','CANCELADO')),
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_rescission_cases_deadline ON local_rescission_cases(status,deadline_date);
CREATE TABLE IF NOT EXISTS local_rescission_requirements (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES local_rescission_cases(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
    status TEXT NOT NULL DEFAULT 'PENDENTE' CHECK(status IN ('PENDENTE','ANEXADO','DISPENSADO','ENTREGUE')),
    document_name TEXT NOT NULL DEFAULT '',
    registry_document_id INTEGER REFERENCES local_registry_documents(id) ON DELETE SET NULL,
    exception_reason TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(case_id,code)
);
CREATE TABLE IF NOT EXISTS local_rescission_delivery_events (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES local_rescission_cases(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK(channel IN ('EMAIL','WHATSAPP')),
    status TEXT NOT NULL DEFAULT 'SIMULADO' CHECK(status IN ('SIMULADO','ERRO')),
    target TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    documents_json TEXT NOT NULL DEFAULT '[]',
    protocol TEXT NOT NULL DEFAULT '',
    is_simulated INTEGER NOT NULL DEFAULT 1 CHECK(is_simulated=1),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_rescission_real_delivery_events (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES local_rescission_cases(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'EMAIL' CHECK(channel='EMAIL'),
    status TEXT NOT NULL CHECK(status IN ('ENVIANDO','ENVIADO','FALHOU')),
    target TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    documents_json TEXT NOT NULL DEFAULT '[]',
    protocol TEXT NOT NULL UNIQUE,
    request_key TEXT,
    error_message TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS local_rescission_real_deliveries_case ON local_rescission_real_delivery_events(case_id,id DESC);
CREATE TABLE IF NOT EXISTS local_rescission_events (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES local_rescission_cases(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_rescission_events_case ON local_rescission_events(case_id,id DESC);
CREATE TABLE IF NOT EXISTS registry_reviews (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('PERSON_NAME','LINK_EMPLOYMENT','CONFLICT_REVIEW')),
    subject_key TEXT NOT NULL,
    value TEXT NOT NULL,
    note TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS registry_reviews_subject ON registry_reviews(subject_key,id);
CREATE TABLE IF NOT EXISTS local_clients (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_company_profiles (
    client_key TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_clicksign_company_settings (
    company_key TEXT PRIMARY KEY,
    connection_name TEXT NOT NULL DEFAULT 'Clicksign',
    environment TEXT NOT NULL DEFAULT 'sandbox' CHECK(environment IN ('sandbox','production')),
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    timeout_seconds INTEGER NOT NULL DEFAULT 20 CHECK(timeout_seconds BETWEEN 3 AND 120),
    allowed_channels TEXT NOT NULL DEFAULT 'BOTH' CHECK(allowed_channels IN ('BOTH','EMAIL','WHATSAPP')),
    refresh_minutes INTEGER NOT NULL DEFAULT 15 CHECK(refresh_minutes BETWEEN 1 AND 1440),
    webhook_url TEXT NOT NULL DEFAULT '',
    access_token_protected TEXT NOT NULL DEFAULT '',
    hmac_secret_protected TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_voxfy_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    connection_name TEXT NOT NULL DEFAULT 'VoxFy - WhatsApp principal',
    api_base_url TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    timeout_seconds INTEGER NOT NULL DEFAULT 30 CHECK(timeout_seconds BETWEEN 3 AND 120),
    access_token_protected TEXT NOT NULL DEFAULT '',
    last_test_at TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_integration_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    sib_base_url TEXT NOT NULL DEFAULT '',
    sib_active INTEGER NOT NULL DEFAULT 0 CHECK(sib_active IN (0,1)),
    sib_timeout_seconds INTEGER NOT NULL DEFAULT 20 CHECK(sib_timeout_seconds BETWEEN 3 AND 60),
    sib_api_key_protected TEXT NOT NULL DEFAULT '',
    sib_cf_client_id_protected TEXT NOT NULL DEFAULT '',
    sib_cf_client_secret_protected TEXT NOT NULL DEFAULT '',
    sib_ca_bundle_path TEXT NOT NULL DEFAULT '',
    sib_last_test_at TEXT NOT NULL DEFAULT '',
    sib_last_test_status TEXT NOT NULL DEFAULT '',
    selecty_base_url TEXT NOT NULL DEFAULT 'https://api.selecty.app/v2',
    selecty_active INTEGER NOT NULL DEFAULT 0 CHECK(selecty_active IN (0,1)),
    selecty_timeout_seconds INTEGER NOT NULL DEFAULT 20 CHECK(selecty_timeout_seconds BETWEEN 3 AND 60),
    selecty_api_id_protected TEXT NOT NULL DEFAULT '',
    selecty_secret_protected TEXT NOT NULL DEFAULT '',
    selecty_last_test_at TEXT NOT NULL DEFAULT '',
    selecty_last_test_status TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_integration_events (
    id INTEGER PRIMARY KEY,
    integration TEXT NOT NULL CHECK(integration IN ('SIB','SELECTY')),
    action TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('OK','ERRO')),
    result_count INTEGER NOT NULL DEFAULT 0 CHECK(result_count >= 0),
    summary TEXT NOT NULL DEFAULT '',
    actor_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_integration_events_recent ON local_integration_events(integration,id DESC);
CREATE TABLE IF NOT EXISTS local_smtp_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    connection_name TEXT NOT NULL DEFAULT 'SMTP Demonstração',
    smtp_host TEXT NOT NULL DEFAULT '',
    smtp_port INTEGER NOT NULL DEFAULT 587 CHECK(smtp_port BETWEEN 1 AND 65535),
    security TEXT NOT NULL DEFAULT 'STARTTLS' CHECK(security IN ('SSL_TLS','STARTTLS')),
    username TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT 'demo@example.com',
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    timeout_seconds INTEGER NOT NULL DEFAULT 30 CHECK(timeout_seconds BETWEEN 3 AND 120),
    last_test_sent_at TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_clicksign_signers (
    id INTEGER PRIMARY KEY,
    company_key TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    cpf TEXT NOT NULL DEFAULT '',
    birth_date TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'BOTH' CHECK(channel IN ('BOTH','EMAIL','WHATSAPP')),
    signer_order INTEGER NOT NULL DEFAULT 1 CHECK(signer_order BETWEEN 1 AND 100),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    auto_signature INTEGER NOT NULL DEFAULT 0 CHECK(auto_signature IN (0,1)),
    authorized_at TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS local_clicksign_signers_company ON local_clicksign_signers(company_key,active,signer_order,id);
CREATE TABLE IF NOT EXISTS local_temporary_contracts (
    source_system TEXT NOT NULL,
    source_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    registration TEXT NOT NULL DEFAULT '',
    company_key TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    contract_type TEXT NOT NULL DEFAULT 'Temporário',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    admission_date TEXT NOT NULL DEFAULT '',
    first_due_date TEXT NOT NULL DEFAULT '',
    second_due_date TEXT NOT NULL DEFAULT '',
    cpf TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    employer_name TEXT NOT NULL DEFAULT '',
    employer_cnpj TEXT NOT NULL DEFAULT '',
    employer_address TEXT NOT NULL DEFAULT '',
    tomadora_name TEXT NOT NULL DEFAULT '',
    tomadora_cnpj TEXT NOT NULL DEFAULT '',
    tomadora_address TEXT NOT NULL DEFAULT '',
    worker_function TEXT NOT NULL DEFAULT '',
    work_location TEXT NOT NULL DEFAULT '',
    complementary_demand TEXT NOT NULL DEFAULT '',
    is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)),
    source_updated_at TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_system,source_id)
);
CREATE INDEX IF NOT EXISTS local_temporary_contracts_queue ON local_temporary_contracts(active,first_due_date,company_key);
CREATE TABLE IF NOT EXISTS local_temporary_extensions (
    id INTEGER PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_id TEXT NOT NULL,
    first_due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PREPARED','AWAITING_SIGNATURE','SIGNED','ERROR','CANCELLED')),
    envelope_id TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_sent_at TEXT NOT NULL DEFAULT '',
    provider_status TEXT NOT NULL DEFAULT '',
    signer_name TEXT NOT NULL DEFAULT '',
    worker_channel TEXT NOT NULL DEFAULT '',
    delivery_mode TEXT NOT NULL DEFAULT 'SIMULATED' CHECK(delivery_mode IN ('SIMULATED','CLICKSIGN_REAL')),
    delivery_environment TEXT NOT NULL DEFAULT '',
    sender_channel TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    sender_cpf TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT '',
    sender_phone TEXT NOT NULL DEFAULT '',
    recipient_email TEXT NOT NULL DEFAULT '',
    preview_pdf BLOB,
    prepared_at TEXT NOT NULL DEFAULT '',
    last_notice_at TEXT NOT NULL DEFAULT '',
    sender_signed_at TEXT NOT NULL DEFAULT '',
    worker_signed_at TEXT NOT NULL DEFAULT '',
    recipient_invited_at TEXT NOT NULL DEFAULT '',
    last_notice_target TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_system,source_id,first_due_date),
    FOREIGN KEY(source_system,source_id) REFERENCES local_temporary_contracts(source_system,source_id)
);
CREATE INDEX IF NOT EXISTS local_temporary_extensions_status ON local_temporary_extensions(status,first_due_date);
"""


class Connection(sqlite3.Connection):
    """Confirma ou reverte a transação e libera o arquivo ao sair do bloco."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def _message_search_text(*values: object) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    return " ".join("".join(char for char in unicodedata.normalize("NFKD", text)
                           if not unicodedata.combining(char)).split())


def _message_tags(value: object) -> str:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = [part for part in str(parsed or "").split(",")]
    tags = list(dict.fromkeys(str(part).strip() for part in parsed if str(part).strip()))[:30]
    return json.dumps(tags, ensure_ascii=False)


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the populated SQLite file before the first additive V3 planner
    # migration. No source snapshot or user record is rewritten by this step.
    if path.exists():
        try:
            with closing(sqlite3.connect(path)) as probe:
                tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row[1] for row in probe.execute("PRAGMA table_info(local_benefit_plans)")} if "local_benefit_plans" in tables else set()
                rule_columns = {row[1] for row in probe.execute("PRAGMA table_info(local_company_benefit_rules)")} if "local_company_benefit_rules" in tables else set()
                tax_columns = {row[1] for row in probe.execute("PRAGMA table_info(local_tax_versions)")} if "local_tax_versions" in tables else set()
                rescission_columns = {row[1] for row in probe.execute('PRAGMA table_info(local_rescission_cases)')} if 'local_rescission_cases' in tables else set()
                tax_required = {"workflow_state", "category", "income_type", "valid_from", "valid_to", "source_title", "source_agency", "source_url", "source_accessed_at", "content_hash", "parser_version", "justification", "validation_json", "updated_by", "previous_version_id", "updated_at"}
            required_rule_columns = {"valid_from", "unit_mode", "february_30_policy", "admission_on_cutoff_policy"}
            if "employee_key" not in columns or "local_company_benefit_rules" not in tables or not required_rule_columns.issubset(rule_columns):
                backup_database(path, "before-lot-planning-v3")
            elif not tax_required.issubset(tax_columns):
                backup_database(path, "before-tax-tables-v3")
            elif 'local_sib_records' in tables and 'local_sib_absences' not in tables:
                backup_database(path, 'before-sib-absences-v3')
            elif 'local_rescission_cases' in tables and 'source_system' not in rescission_columns:
                backup_database(path, 'before-sib-operations-v3')
            elif 'local_sib_records' in tables and 'local_user_access' not in tables:
                backup_database(path, 'before-operational-dossiers-v3')
        except sqlite3.DatabaseError:
            # Let the normal initializer report the original database error.
            pass
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        from .sib_operations import migrate as migrate_sib_operations, add_columns as add_sib_operation_columns
        migrate_sib_operations(db)
        db.executescript(SCHEMA + SIB_IMPORT_SCHEMA + SIB_SCAN_SCHEMA + SIB_ISSUES_SCHEMA
                         + SIB_REFRESH_SCHEMA + SIB_AUTO_REFRESH_SCHEMA + SIB_DISCOVERY_SCHEMA
                         + PEOPLE_EMAIL_SCHEDULES_SCHEMA + SIB_ABSENCES_SCHEMA + SIB_OPERATIONS_SCHEMA
                         + SECTOR_SCHEMA + SECTOR_IMPORT_SCHEMA)
        add_sib_operation_columns(db)
        from .permissions import migrate as migrate_permissions
        from .registry_documents import migrate as migrate_registry_documents
        migrate_permissions(db)
        migrate_registry_documents(db)
        from .extension_workflow import migrate as migrate_extension_workflow
        from .rescission_workflow import migrate as migrate_rescission_workflow
        migrate_extension_workflow(db)
        migrate_rescission_workflow(db)
        people_email_columns = {row[1] for row in db.execute("PRAGMA table_info(local_people_email_schedules)")}
        for name, declaration in {
            "frequency": "TEXT NOT NULL DEFAULT 'daily' CHECK(frequency IN ('daily','weekly','monthly'))",
            "weekday": "INTEGER NOT NULL DEFAULT 0 CHECK(weekday BETWEEN 0 AND 6)",
            "monthday": "INTEGER NOT NULL DEFAULT 1 CHECK(monthday BETWEEN 1 AND 31)",
            "test_interval_minutes": "INTEGER NOT NULL DEFAULT 0 CHECK(test_interval_minutes IN (0,2))",
            "test_interval_one_minute": "INTEGER NOT NULL DEFAULT 0 CHECK(test_interval_one_minute IN (0,1))",
            "include_active": "INTEGER NOT NULL DEFAULT 0 CHECK(include_active IN (0,1))",
            "message_body": "TEXT NOT NULL DEFAULT ''",
            "report_columns_json": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in people_email_columns:
                db.execute(f"ALTER TABLE local_people_email_schedules ADD COLUMN {name} {declaration}")
        # Local people records gain contact and temporary-term fields without
        # rewriting or importing any source-system snapshot.
        for table, columns in {
            "local_sib_auto_refresh_state": {
                "enabled": "INTEGER NOT NULL DEFAULT 1",
            },
            "local_sib_scan_state": {
                "continuous": "INTEGER NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "retry_at": "REAL NOT NULL DEFAULT 0",
                "empty_streak": "INTEGER NOT NULL DEFAULT 0",
                "auto_stopped": "INTEGER NOT NULL DEFAULT 0",
            },
            "local_sib_discovery_state": {
                "monitor_version": "INTEGER NOT NULL DEFAULT 0",
                "queue_kind": "TEXT NOT NULL DEFAULT ''",
                "queue_lane": "TEXT NOT NULL DEFAULT ''",
                "fresh_due_at": "REAL NOT NULL DEFAULT 0",
                "fresh_last_at": "REAL NOT NULL DEFAULT 0",
                "intern_cursor": "INTEGER NOT NULL DEFAULT 0",
                "probe_offset": "INTEGER NOT NULL DEFAULT 0",
                "queue_json": "TEXT NOT NULL DEFAULT '[]'",
                "queue_index": "INTEGER NOT NULL DEFAULT 0",
                "activity": "TEXT NOT NULL DEFAULT ''",
                "current_registration": "TEXT NOT NULL DEFAULT ''",
                "current_batch_json": "TEXT NOT NULL DEFAULT '[]'",
                "gap_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "catalog_supported": "INTEGER NOT NULL DEFAULT -1",
                "catalog_progress_json": "TEXT NOT NULL DEFAULT '{}'",
                "catalog_next_at": "REAL NOT NULL DEFAULT 0",
                "catalog_last_completed_at": "REAL NOT NULL DEFAULT 0",
                "catalog_error": "TEXT NOT NULL DEFAULT ''",
            },
            "local_sib_issue_attempts": {"request_id": "TEXT NOT NULL DEFAULT ''"},
            "registry_people": {
                "cpf": "TEXT NOT NULL DEFAULT ''",
                "email": "TEXT NOT NULL DEFAULT ''",
                "whatsapp": "TEXT NOT NULL DEFAULT ''",
                "is_test": "INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1))",
            },
            "registry_employments": {
                "first_due_date": "TEXT NOT NULL DEFAULT ''",
                "second_due_date": "TEXT NOT NULL DEFAULT ''",
                "dismissal_reason_code": "TEXT NOT NULL DEFAULT ''",
                "dismissal_reason": "TEXT NOT NULL DEFAULT ''",
                "is_test": "INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1))",
            },
        }.items():
            existing_columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in existing_columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        delivery_columns = {row[1] for row in db.execute("PRAGMA table_info(local_rescission_real_delivery_events)")}
        if "request_key" not in delivery_columns:
            db.execute("ALTER TABLE local_rescission_real_delivery_events ADD COLUMN request_key TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS local_rescission_real_request_key ON local_rescission_real_delivery_events(request_key) WHERE request_key IS NOT NULL")
        import_columns = {row[1] for row in db.execute("PRAGMA table_info(local_import_items)")}
        for name in ("before_json", "after_json"):
            if name not in import_columns:
                db.execute(f"ALTER TABLE local_import_items ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")
        # Employee records marked as test must route only to the operator's test inbox.
        db.execute("UPDATE registry_people SET email='demo@example.com' WHERE is_test=1 AND email<>'demo@example.com'")
        # SMTP login is configured by the operator. Initialization must never
        # rewrite it because credentials can belong to either Locaweb account.
        temporary_columns = {row[1] for row in db.execute("PRAGMA table_info(local_temporary_contracts)")}
        if "is_test" not in temporary_columns:
            db.execute("ALTER TABLE local_temporary_contracts ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1))")
        for name in ("employer_name", "employer_cnpj", "employer_address", "tomadora_name", "tomadora_cnpj", "tomadora_address", "worker_function", "work_location", "complementary_demand"):
            if name not in temporary_columns:
                db.execute(f"ALTER TABLE local_temporary_contracts ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        extension_columns = {row[1] for row in db.execute("PRAGMA table_info(local_temporary_extensions)")}
        for name, declaration in {
            "signer_name": "TEXT NOT NULL DEFAULT ''",
            "worker_channel": "TEXT NOT NULL DEFAULT ''",
            "delivery_mode": "TEXT NOT NULL DEFAULT 'SIMULATED'",
            "delivery_environment": "TEXT NOT NULL DEFAULT ''",
            "sender_channel": "TEXT NOT NULL DEFAULT ''",
            "sender_name": "TEXT NOT NULL DEFAULT ''",
            "sender_cpf": "TEXT NOT NULL DEFAULT ''",
            "sender_email": "TEXT NOT NULL DEFAULT ''",
            "sender_phone": "TEXT NOT NULL DEFAULT ''",
            "recipient_email": "TEXT NOT NULL DEFAULT ''",
            "preview_pdf": "BLOB",
            "prepared_at": "TEXT NOT NULL DEFAULT ''",
            "last_notice_at": "TEXT NOT NULL DEFAULT ''",
            "sender_signed_at": "TEXT NOT NULL DEFAULT ''",
            "worker_signed_at": "TEXT NOT NULL DEFAULT ''",
            "recipient_invited_at": "TEXT NOT NULL DEFAULT ''",
            "last_notice_target": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in extension_columns:
                db.execute(f"ALTER TABLE local_temporary_extensions ADD COLUMN {name} {declaration}")
        # Additive migration for the versioned tax workflow. Existing version IDs,
        # JSON parameters, snapshots and history rows are retained in place.
        tax_columns = {row[1] for row in db.execute("PRAGMA table_info(local_tax_versions)")}
        for name, declaration in {
            "workflow_state": "TEXT NOT NULL DEFAULT 'COLLECTED'",
            "category": "TEXT NOT NULL DEFAULT 'EMPLOYEE'",
            "income_type": "TEXT NOT NULL DEFAULT 'SALARY_MONTHLY'",
            "valid_from": "TEXT NOT NULL DEFAULT ''",
            "valid_to": "TEXT",
            "source_title": "TEXT NOT NULL DEFAULT ''",
            "source_agency": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "source_accessed_at": "TEXT NOT NULL DEFAULT ''",
            "content_hash": "TEXT NOT NULL DEFAULT ''",
            "parser_version": "TEXT NOT NULL DEFAULT ''",
            "justification": "TEXT NOT NULL DEFAULT ''",
            "validation_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_by": "INTEGER REFERENCES users(id)",
            "previous_version_id": "INTEGER REFERENCES local_tax_versions(id) ON DELETE RESTRICT",
            "updated_at": "TEXT",
        }.items():
            if name not in tax_columns:
                db.execute(f"ALTER TABLE local_tax_versions ADD COLUMN {name} {declaration}")
        tax_history_columns = {row[1] for row in db.execute("PRAGMA table_info(local_tax_history)")}
        for name, declaration in {
            "before_json": "TEXT NOT NULL DEFAULT '{}'",
            "after_json": "TEXT NOT NULL DEFAULT '{}'",
            "outcome": "TEXT NOT NULL DEFAULT 'SUCCESS'",
        }.items():
            if name not in tax_history_columns:
                db.execute(f"ALTER TABLE local_tax_history ADD COLUMN {name} {declaration}")
        db.execute("""UPDATE local_tax_versions SET
            valid_from=CASE WHEN valid_from='' THEN competence||'-01' ELSE valid_from END,
            workflow_state=CASE
              WHEN status='PUBLISHED' THEN 'PUBLISHED'
              WHEN status='SUPERSEDED' THEN 'SUPERSEDED'
              WHEN status='APPROVED' THEN 'APPROVED'
              WHEN status='REJECTED' THEN 'REJECTED'
              WHEN workflow_state='COLLECTED' THEN 'COLLECTED'
              ELSE workflow_state END""")
        db.execute("CREATE INDEX IF NOT EXISTS local_tax_versions_reference ON local_tax_versions(kind,category,income_type,status,valid_from,version)")
        plan_columns = {row[1] for row in db.execute("PRAGMA table_info(local_benefit_plans)")}
        for name, declaration in {
            "employee_key": "TEXT", "company_key": "TEXT", "unit_key": "TEXT NOT NULL DEFAULT ''",
            "benefit_type": "TEXT", "competence": "TEXT", "expires_at": "TEXT", "deleted_at": "TEXT",
            "deleted_by": "INTEGER REFERENCES users(id)", "saved_by": "INTEGER REFERENCES users(id)",
            "source_snapshot_json": "TEXT NOT NULL DEFAULT '{}'", "row_version": "INTEGER NOT NULL DEFAULT 1",
            "idempotency_key": "TEXT", "expiration_noted_at": "TEXT", "pinned_at": "TEXT",
            "pinned_by": "INTEGER REFERENCES users(id)",
        }.items():
            if name not in plan_columns:
                db.execute(f"ALTER TABLE local_benefit_plans ADD COLUMN {name} {declaration}")
        db.execute("CREATE INDEX IF NOT EXISTS local_benefit_plans_owner ON local_benefit_plans(deleted_at,pinned,updated_at)")
        db.execute("CREATE INDEX IF NOT EXISTS local_benefit_plans_cycle ON local_benefit_plans(employee_key,benefit_type,competence,deleted_at)")
        rule_columns = {row[1] for row in db.execute("PRAGMA table_info(local_company_benefit_rules)")}
        for name, declaration in {
            "valid_from": "TEXT",
            "unit_mode": "TEXT NOT NULL DEFAULT 'UNSPECIFIED'",
            "february_30_policy": "TEXT NOT NULL DEFAULT 'UNRESOLVED'",
            "admission_on_cutoff_policy": "TEXT NOT NULL DEFAULT 'UNRESOLVED'",
        }.items():
            if name not in rule_columns:
                db.execute(f"ALTER TABLE local_company_benefit_rules ADD COLUMN {name} {declaration}")
        # VT's stored amount is always a fare per use. Existing VR rows remain
        # explicitly unresolved because their historical unit was never stored.
        db.execute("UPDATE local_company_benefit_rules SET unit_mode='PER_USE' WHERE benefit_type='VT' AND unit_mode='UNSPECIFIED'")
        # Give any pre-existing, non-pinned V3 plan an explicit four-local-day
        # transition window once. Persist the resulting instant in UTC.
        now_local = datetime.now(_SAO_PAULO_TZ)
        expiry_utc = (now_local + timedelta(days=4)).astimezone(timezone.utc).isoformat(timespec="seconds")
        legacy_plans = db.execute("""SELECT id FROM local_benefit_plans
            WHERE employee_key IS NOT NULL AND deleted_at IS NULL AND pinned=0 AND expires_at IS NULL""").fetchall()
        if legacy_plans:
            db.executemany("UPDATE local_benefit_plans SET expires_at=? WHERE id=? AND expires_at IS NULL",
                           [(expiry_utc, row["id"]) for row in legacy_plans])
            actor = db.execute("SELECT id FROM users WHERE active=1 ORDER BY CASE role WHEN 'ADMIN' THEN 0 WHEN 'GESTOR' THEN 1 ELSE 2 END,id LIMIT 1").fetchone()
            if actor:
                db.executemany("INSERT INTO local_benefit_plan_audit(plan_id,actor_id,action,metadata_json) VALUES(?,?, 'MIGRATION_EXPIRY',?)",
                               [(row["id"], actor["id"], json.dumps({"expiresAt": expiry_utc, "migration": "four-local-day-grace"}, ensure_ascii=False))
                                for row in legacy_plans])
        rescission_requirement_columns = {row[1] for row in db.execute("PRAGMA table_info(local_rescission_requirements)")}
        if "registry_document_id" not in rescission_requirement_columns:
            db.execute("ALTER TABLE local_rescission_requirements ADD COLUMN registry_document_id INTEGER REFERENCES local_registry_documents(id) ON DELETE SET NULL")
        from .chat import ensure_schema as ensure_chat_schema
        ensure_chat_schema(db)
        # Preserve each imported checklist identity and its initial state in an
        # editable overlay, exactly once. Original JSON source rows stay immutable.
        actor = db.execute("SELECT id FROM users WHERE active=1 ORDER BY CASE role WHEN 'ADMIN' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
        if actor:
            # Copy the published V2 messages and alert receipts into an operational
            # overlay exactly once. The imported source rows are never updated or deleted.
            for source in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='quick_messages'").fetchall():
                payload = json.loads(source['payload'])
                message_id = "v2:" + str(source['source_id'])
                tags_json = _message_tags(payload.get('tags'))
                text_index = _message_search_text(payload.get('title'), payload.get('body'), payload.get('category'), tags_json)
                db.execute("""INSERT OR IGNORE INTO local_quick_messages
                    (id,source_id,title,body,category,client_id,tags_json,normalized_text,source_creator_id,
                     source_updated_by_id,source_created_at,source_updated_at,archived_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (message_id, str(source['source_id']), str(payload.get('title') or 'Mensagem importada'),
                     str(payload.get('body') or ''), str(payload.get('category') or ''),
                     str(payload.get('client_id') or '') or None, tags_json, text_index,
                     str(payload.get('created_by') or ''), str(payload.get('updated_by') or ''),
                     payload.get('created_at'), payload.get('updated_at'), payload.get('archived_at')))
            for source in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='dp_alerts'").fetchall():
                payload = json.loads(source['payload'])
                alert_id = "v2:" + str(source['source_id'])
                audience = str(payload.get('audience') or 'ALL').upper()
                if audience not in ('ALL', 'INDIVIDUAL', 'GROUP'):
                    audience = 'ALL'
                db.execute("""INSERT OR IGNORE INTO local_dp_alerts
                    (id,source_id,source_sender_id,title,body,audience_type,created_at,imported_from_v2)
                    VALUES(?,?,?,?,?,?,?,1)""",
                    (alert_id, str(source['source_id']), str(payload.get('created_by') or ''),
                     str(payload.get('title') or 'Alerta importado'), str(payload.get('body') or ''),
                     audience, payload.get('created_at') or datetime.now().isoformat(timespec='seconds')))
            for source in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='dp_alert_recipients'").fetchall():
                payload = json.loads(source['payload'])
                alert_source_id = str(payload.get('alert_id') or '')
                if not alert_source_id:
                    continue
                alert_id = "v2:" + alert_source_id
                db.execute("""INSERT OR IGNORE INTO local_dp_alert_recipients
                    (alert_id,source_user_id,read_at)
                    SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM local_dp_alerts WHERE id=?)""",
                    (alert_id, str(payload.get('user_id') or source['source_id']), payload.get('read_at'), alert_id))
            for row in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='case_checklist'").fetchall():
                item=json.loads(row['payload'])
                case_id=str(item.get('case_id') or '')
                if not case_id:
                    continue
                db.execute("""INSERT OR IGNORE INTO local_v2_checklist_items
                    (id,source_case_id,source_item_id,title,done,required,assignee_id,position,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?)""",(str(row['source_id']),case_id,str(item.get('id') or row['source_id']),str(item.get('title') or 'Etapa')[:180],int(bool(item.get('done'))),int(bool(item.get('required'))),None,int(item.get('position') or 0),actor['id']))
        columns = {row[1] for row in db.execute("PRAGMA table_info(local_calendar_events)")}
        additions = {
            "starts_at": "TEXT", "due_at": "TEXT", "description": "TEXT NOT NULL DEFAULT ''",
            "priority": "TEXT NOT NULL DEFAULT 'NORMAL'", "series_id": "TEXT",
            "recurrence_type": "TEXT NOT NULL DEFAULT 'NONE'", "recurrence_index": "INTEGER NOT NULL DEFAULT 1",
            "archived": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE local_calendar_events ADD COLUMN {name} {declaration}")
        db.execute("UPDATE local_calendar_events SET starts_at=COALESCE(starts_at,due_date||'T09:00')")
        # Additive fields for locally created cases; existing rows and V2 snapshots stay intact.
        case_columns = {row[1] for row in db.execute("PRAGMA table_info(process_cases)")}
        for name, declaration in {
            "description": "TEXT NOT NULL DEFAULT ''",
            "client_id": "TEXT",
            "unit_id": "TEXT",
            "priority": "TEXT NOT NULL DEFAULT 'NORMAL'",
            "recurrence_rule_id": "TEXT",
            "competence": "TEXT",
            "due_popup_enabled": "INTEGER NOT NULL DEFAULT 0 CHECK(due_popup_enabled IN (0,1))",
            "next_owner_id": "INTEGER REFERENCES users(id)",
            "next_due_date": "TEXT",
        }.items():
            if name not in case_columns:
                db.execute(f"ALTER TABLE process_cases ADD COLUMN {name} {declaration}")
        checklist_columns = {row[1] for row in db.execute("PRAGMA table_info(case_checklist)")}
        for name, declaration in {
            "required": "INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1))",
            "position": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in checklist_columns:
                db.execute(f"ALTER TABLE case_checklist ADD COLUMN {name} {declaration}")
        conversation_columns={row[1] for row in db.execute("PRAGMA table_info(local_conversations)")}
        if "direct_key" not in conversation_columns:
            db.execute("ALTER TABLE local_conversations ADD COLUMN direct_key TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS local_conversations_direct ON local_conversations(direct_key) WHERE direct_key IS NOT NULL")
        actor = db.execute("SELECT id FROM users WHERE active=1 ORDER BY CASE role WHEN 'ADMIN' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
        if actor:
            from .tax_tables import INSS_2026, IRRF_2026, legacy_2026_matches
            tax_sources = {
                "INSS": ("Tabela de contribuição mensal — empregado, doméstico e avulso", "Instituto Nacional do Seguro Social", "https://www.gov.br/inss/pt-br/direitos-e-deveres/inscricao-e-contribuicao/tabela-de-contribuicao-mensal"),
                "IRRF": ("Tributação de 2026 — incidência mensal e redução", "Receita Federal do Brasil", "https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda/tabelas/2026"),
            }
            accessed_at = datetime.now(_SAO_PAULO_TZ).isoformat(timespec="seconds")
            for kind,parameters in (("INSS",INSS_2026),("IRRF",IRRF_2026)):
                source_title,source_agency,source_url = tax_sources[kind]
                source = f"{source_title} · conferida na fonte oficial em {accessed_at[:10]}"
                canonical=json.dumps(parameters,ensure_ascii=False,sort_keys=True,separators=(",", ":"))
                digest=__import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
                db.execute("""INSERT OR IGNORE INTO local_tax_versions(
                    kind,competence,version,status,workflow_state,category,income_type,valid_from,valid_to,source,
                    source_title,source_agency,source_url,source_accessed_at,content_hash,parser_version,
                    parameters_json,created_by,approved_by,published_by,approved_at,published_at)
                    VALUES(?, '2026-01',1,'PUBLISHED','PUBLISHED','EMPLOYEE','SALARY_MONTHLY','2026-01-01','2026-12-31',?,?,?,?,?,'manual-v1',?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (kind,source,source_title,source_agency,source_url,accessed_at,digest,canonical,
                     actor['id'],actor['id'],actor['id'],accessed_at))
                # Backfill provenance only when the preserved local parameters exactly
                # match the fully checked 2026 official table.
                legacy=db.execute("SELECT * FROM local_tax_versions WHERE kind=? AND competence='2026-01' AND version=1",(kind,)).fetchone()
                if legacy:
                    existing=None
                    try:
                        existing=json.loads(legacy["parameters_json"])
                        existing_canonical=json.dumps(existing,ensure_ascii=False,sort_keys=True,separators=(",", ":"))
                    except (TypeError,ValueError):
                        existing_canonical=""
                    if legacy_2026_matches(kind, existing) and not legacy["source_url"]:
                        preserved_digest=__import__("hashlib").sha256(existing_canonical.encode("utf-8")).hexdigest()
                        exact_snapshot=existing_canonical==canonical
                        parser_version="manual-v1" if exact_snapshot else "legacy-v2-preserved-v3-compatible"
                        audit_action="SOURCE_VERIFIED" if exact_snapshot else "SOURCE_VERIFIED_LEGACY_COMPAT"
                        audit_detail=("Tabela local comparada integralmente com a referência oficial; identidade e parâmetros da revisão preservados." if exact_snapshot else
                            "Faixas e valores de 2026 comparados com a fonte oficial. JSON original preservado; a resolução V3 completa somente elegibilidade mensal e arredondamento técnico ausentes na exportação V2.")
                        db.execute("""UPDATE local_tax_versions SET valid_from='2026-01-01',valid_to='2026-12-31',category='EMPLOYEE',
                            income_type='SALARY_MONTHLY',source=?,source_title=?,source_agency=?,source_url=?,
                            source_accessed_at=?,content_hash=?,parser_version=?,
                            workflow_state='PUBLISHED' WHERE id=?""",
                            (source,source_title,source_agency,source_url,accessed_at,preserved_digest,parser_version,legacy["id"]))
                        db.execute("""INSERT INTO local_tax_history(tax_version_id,actor_id,action,detail,after_json)
                            VALUES(?,?,?,?,?)""",
                            (legacy["id"],actor['id'],audit_action,audit_detail,json.dumps({"source_url":source_url,"source_accessed_at":accessed_at,"content_hash":preserved_digest,"parser_version":parser_version},ensure_ascii=False)))
                    elif legacy_2026_matches(kind, existing) and legacy["valid_to"] is None:
                        db.execute("UPDATE local_tax_versions SET valid_to='2026-12-31' WHERE id=?", (legacy["id"],))
            # Publish the imported V2 calendar as editable local records, retaining every source row.
            source_events = db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='calendar_events'").fetchall()
            for source in source_events:
                if db.execute("SELECT 1 FROM local_calendar_source_map WHERE source_id=?", (source['source_id'],)).fetchone():
                    continue
                payload = json.loads(source['payload'])
                due = (payload.get('due_at') or payload.get('starts_at') or payload.get('created_at') or datetime.now().date().isoformat())[:10]
                starts = payload.get('starts_at') or (due + 'T09:00')
                status = 'CONCLUIDO' if payload.get('status') in ('DONE','COMPLETED') else 'PENDENTE'
                event_id = db.execute("""INSERT INTO local_calendar_events
                    (title,due_date,starts_at,due_at,category,notes,description,status,priority,series_id,recurrence_type,created_by,archived)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(payload.get('title') or 'Evento importado')[:160],due,starts,payload.get('due_at'),str(payload.get('category') or '')[:80],str(payload.get('notes') or '')[:2000],str(payload.get('description') or '')[:4000],status,str(payload.get('priority') or 'NORMAL')[:20],str(payload.get('series_id') or '') or None,str(payload.get('recurrence') or 'NONE'),actor['id'],int(payload.get('status')=='ARCHIVED'))).lastrowid
                db.execute("INSERT INTO local_calendar_source_map(source_id,local_event_id) VALUES(?,?)", (source['source_id'],event_id))
            # Import checklist items once and keep the read-only V2 snapshot alongside them.
            for source in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='calendar_checklist'").fetchall():
                payload=json.loads(source['payload'])
                mapped=db.execute("SELECT local_event_id FROM local_calendar_source_map WHERE source_id=?", (str(payload.get('event_id')),)).fetchone()
                if mapped and not db.execute("SELECT 1 FROM local_calendar_checklist WHERE event_id=? AND title=?", (mapped['local_event_id'],payload.get('title',''))).fetchone():
                    db.execute("INSERT INTO local_calendar_checklist(event_id,title,done,created_by) VALUES(?,?,?,?)",(mapped['local_event_id'],str(payload.get('title') or 'Item importado')[:180],int(bool(payload.get('done'))),actor['id']))
            for source in db.execute("SELECT source_id,payload FROM v2_source_rows WHERE table_name='calendar_holidays'").fetchall():
                payload=json.loads(source['payload'])
                if payload.get('day'):
                    db.execute("INSERT OR IGNORE INTO local_calendar_holidays(day,name,state,city,source_id) VALUES(?,?,?,?,?)",(payload['day'],str(payload.get('name') or '')[:160],str(payload.get('state') or '')[:40],str(payload.get('city') or '')[:100],source['source_id']))
        from .people_analytics_cache import install as install_analytics_cache
        install_analytics_cache(db)


def backup_database(path: Path, label: str = "manual") -> Path:
    """Cria uma cópia consistente sem interromper as conexões WAL do servidor."""
    target_path = path.with_name(path.stem + f".{label}-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + path.suffix)
    with connect(path) as source, closing(sqlite3.connect(target_path)) as target:
        source.backup(target)
    return target_path
