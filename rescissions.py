"""Fluxo auditável da documentação de desligamentos.

O módulo controla o prazo operacional de dez dias definido pela empresa. Ele não
substitui a análise jurídica de quais verbas ou documentos se aplicam a cada caso.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_REQUIREMENTS = (
    ("TRCT", "Termo de Rescisão do Contrato de Trabalho (TRCT)"),
    ("PAGAMENTO", "Comprovante de pagamento das verbas rescisórias"),
    ("FICHA_FINANCEIRA", "Ficha financeira do funcionário · arquivo interno"),
    ("EXAME_DEMISSIONAL", "Exame médico demissional"),
    ("FGTS", "FGTS / comprovante de movimentação"),
    ("SEGURO_DESEMPREGO", "Requerimento do seguro-desemprego, quando aplicável"),
)
INTERNAL_REQUIREMENTS = {"FICHA_FINANCEIRA"}
CHANNELS = {"EMAIL", "WHATSAPP", "AMBOS"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RescissionService:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _case_status(
        requirements: list[dict[str, Any]], deliveries: list[dict[str, Any]], *, current_status: str = "",
    ) -> str:
        if current_status in {"CONCLUIDO", "CANCELADO"}:
            return current_status
        registered = any(item["status"] == "REGISTRADO" for item in deliveries)
        if registered:
            # A mensagem é a atividade principal. A aceitação pelo canal conclui
            # o controle; documentos, resposta e ticket são evidências opcionais.
            return "CONCLUIDO"
        mandatory = [item for item in requirements if item["mandatory"]]
        pending = [item for item in mandatory if item["status"] == "PENDENTE"]
        return "EM_DOCUMENTACAO" if pending else "PRONTO_PARA_ENVIO"

    @staticmethod
    def _deadline_state(deadline: str) -> tuple[int, str]:
        remaining = (date.fromisoformat(deadline) - date.today()).days
        if remaining < 0:
            return remaining, "VENCIDO"
        if remaining <= 2:
            return remaining, "URGENTE"
        return remaining, "NO_PRAZO"

    def _event(self, db: sqlite3.Connection, case_id: int, event_type: str, actor: str, **details: Any) -> None:
        db.execute(
            "INSERT INTO rh_rescission_events(rescission_case_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)",
            (case_id, event_type, actor, json.dumps(details, ensure_ascii=False), _now()),
        )

    def _ensure_requirements(self, db: sqlite3.Connection, case_id: int, actor: str) -> None:
        """Acrescenta requisitos novos em casos antigos sem alterar decisões já registradas."""
        now = _now()
        db.executemany(
            """INSERT OR IGNORE INTO rh_rescission_requirements(
                   rescission_case_id,code,label,mandatory,updated_by,updated_at
               ) VALUES(?,?,?,?,?,?)""",
            [
                (case_id, code, label, 0 if code in INTERNAL_REQUIREMENTS else 1, actor, now)
                for code, label in DEFAULT_REQUIREMENTS
            ],
        )
        for code, label in DEFAULT_REQUIREMENTS:
            if code in INTERNAL_REQUIREMENTS:
                db.execute(
                    """UPDATE rh_rescission_requirements SET mandatory=0,label=?
                       WHERE rescission_case_id=? AND code=?""",
                    (label, case_id, code),
                )
            elif code == "FGTS":
                # Atualiza somente a descrição visual, preservando vínculo,
                # decisão, obrigatoriedade e histórico já registrados.
                db.execute(
                    """UPDATE rh_rescission_requirements SET label=?
                       WHERE rescission_case_id=? AND code=?""",
                    (label, case_id, code),
                )

    def open_case(
        self,
        company_id: int,
        employee_id: int,
        *,
        dismissal_date: str,
        reason_code: str = "",
        reason_label: str = "",
        selecty_work_id: str = "",
        actor: str,
    ) -> int:
        try:
            dismissal = date.fromisoformat(str(dismissal_date)[:10])
        except ValueError as exc:
            raise ValueError("Informe uma data de demissão válida para iniciar a rescisão.") from exc
        deadline = (dismissal + timedelta(days=10)).isoformat()
        now = _now()
        with self.connect() as db:
            employee = db.execute(
                "SELECT company_id,tipo_contrato,data_admissao FROM employees WHERE id=?", (employee_id,),
            ).fetchone()
            if not employee or int(employee["company_id"]) != int(company_id):
                raise LookupError("Funcionário não pertence à empresa informada.")
            admission_date = str(employee["data_admissao"] or "").strip()
            if admission_date and dismissal.isoformat() < admission_date:
                raise ValueError("A data de demissão não pode ser anterior à admissão deste vínculo.")
            existing = db.execute("SELECT id FROM rh_rescission_cases WHERE employee_id=?", (employee_id,)).fetchone()
            if existing:
                case_id = int(existing["id"])
                db.execute(
                    """UPDATE rh_rescission_cases SET dismissal_date=?,deadline_date=?,reason_code=?,reason_label=?,
                       selecty_work_id=?,updated_at=? WHERE id=?""",
                    (dismissal.isoformat(), deadline, reason_code[:32], reason_label[:160], selecty_work_id[:80], now, case_id),
                )
                self._ensure_requirements(db, case_id, actor)
                self._event(db, case_id, "PRAZO_RECALCULADO", actor, dismissal_date=dismissal.isoformat(), deadline_date=deadline)
                self._apply_contract_exemptions(db, case_id, str(employee["tipo_contrato"] or ""), actor)
                details = self._details(db, case_id)
                db.execute("UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?", (self._case_status(details["requirements"], details["deliveries"], current_status=str(details["status"])), now, case_id))
                return case_id
            cursor = db.execute(
                """INSERT INTO rh_rescission_cases(company_id,employee_id,dismissal_date,deadline_date,reason_code,reason_label,
                   selecty_work_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (company_id, employee_id, dismissal.isoformat(), deadline, reason_code[:32], reason_label[:160], selecty_work_id[:80], actor, now, now),
            )
            case_id = int(cursor.lastrowid)
            db.executemany(
                """INSERT INTO rh_rescission_requirements(rescission_case_id,code,label,mandatory,updated_by,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                [
                    (case_id, code, label, 0 if code in INTERNAL_REQUIREMENTS else 1, actor, now)
                    for code, label in DEFAULT_REQUIREMENTS
                ],
            )
            self._apply_contract_exemptions(db, case_id, str(employee["tipo_contrato"] or ""), actor)
            self._event(db, case_id, "CENTRAL_INICIADA", actor, dismissal_date=dismissal.isoformat(), deadline_date=deadline)
            details = self._details(db, case_id)
            db.execute("UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?", (self._case_status(details["requirements"], details["deliveries"], current_status=str(details["status"])), now, case_id))
            return case_id

    def _apply_contract_exemptions(self, db: sqlite3.Connection, case_id: int, contract_type: str, actor: str) -> None:
        """Elimina burocracia para temporários sem apagar a trilha de auditoria."""
        normalized = " ".join(contract_type.upper().split())
        if "TEMPOR" not in normalized:
            return
        now = _now()
        for code, label in (
            ("EXAME_DEMISSIONAL", "Dispensa automática: contrato temporário."),
            ("SEGURO_DESEMPREGO", "Dispensa automática: contrato temporário."),
        ):
            cursor = db.execute(
                """UPDATE rh_rescission_requirements SET status='DISPENSADO',exception_reason=?,updated_by=?,updated_at=?
                   WHERE rescission_case_id=? AND code=? AND status='PENDENTE'""",
                (label, actor, now, case_id, code),
            )
            if cursor.rowcount:
                self._event(db, case_id, "DOCUMENTO_DISPENSADO_AUTOMATICAMENTE", actor, code=code, reason=label)

    def _details(self, db: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
        row = db.execute(
            """SELECT r.*,e.nome_completo,e.codigo_folha,e.cpf,e.email,e.celular,e.tipo_contrato,
                      d.tomador_servico,e.cargo_id
               FROM rh_rescission_cases r JOIN employees e ON e.id=r.employee_id
               LEFT JOIN employee_admission_details d ON d.employee_id=e.id WHERE r.id=?""",
            (case_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        requirements = [dict(item) for item in db.execute(
            """SELECT q.*,ed.nome_original,ed.nome_padronizado,ed.tipo_documento
               FROM rh_rescission_requirements q LEFT JOIN employee_documents ed ON ed.id=q.document_id
               WHERE q.rescission_case_id=? ORDER BY q.id""", (case_id,)
        )]
        deliveries = [dict(item) for item in db.execute(
            "SELECT * FROM rh_rescission_delivery_events WHERE rescission_case_id=? ORDER BY id DESC", (case_id,)
        )]
        delivery_documents: dict[int, list[str]] = {}
        for item in db.execute(
            """SELECT m.delivery_event_id,coalesce(d.nome_original,d.nome_padronizado,'Arquivo removido') AS document_name
               FROM rh_rescission_delivery_documents m
               JOIN employee_documents d ON d.id=m.document_id
               WHERE m.delivery_event_id IN (SELECT id FROM rh_rescission_delivery_events WHERE rescission_case_id=?)
               ORDER BY m.delivery_event_id,d.nome_original""",
            (case_id,),
        ):
            delivery_documents.setdefault(int(item["delivery_event_id"]), []).append(str(item["document_name"]))
        for item in deliveries:
            item["documents"] = delivery_documents.get(int(item["id"]), [])
        result["requirements"] = requirements
        events = [dict(item) for item in db.execute(
            "SELECT * FROM rh_rescission_events WHERE rescission_case_id=? ORDER BY id DESC LIMIT 50", (case_id,)
        )]
        delivery_evidence: dict[int, dict[str, Any]] = {}
        receipt_confirmations: dict[int, dict[str, Any]] = {}
        for event in events:
            try:
                details = json.loads(str(event.get("details_json") or "{}"))
            except json.JSONDecodeError:
                details = {}
            event["details"] = details
            delivery_id = int(details.get("delivery_id") or 0)
            if delivery_id and event["event_type"] in {"ENTREGA_REGISTRADA", "ENTREGA_COM_ERRO"}:
                delivery_evidence.setdefault(delivery_id, details)
            if delivery_id and event["event_type"] == "RECEBIMENTO_CONFIRMADO":
                receipt_confirmations.setdefault(delivery_id, details | {
                    "confirmed_at": event["created_at"], "confirmed_by": event["actor"],
                })
        for item in deliveries:
            delivery_id = int(item["id"])
            item["evidence"] = delivery_evidence.get(delivery_id, {})
            item["receipt_confirmation"] = receipt_confirmations.get(delivery_id)
        result["deliveries"] = deliveries
        result["events"] = events
        result["documents"] = [dict(item) for item in db.execute(
            """SELECT id,nome_original,nome_padronizado,tipo_documento,mime_type,caminho_storage,
                      hash_sha256,tamanho_bytes,data_importacao
               FROM employee_documents WHERE employee_id=? ORDER BY id DESC""",
            (result["employee_id"],),
        )]
        thread = db.execute("SELECT * FROM rh_rescission_whatsapp_threads WHERE rescission_case_id=?", (case_id,)).fetchone()
        result["whatsapp_thread"] = dict(thread) if thread else None
        result["days_remaining"], result["deadline_state"] = self._deadline_state(str(result["deadline_date"]))
        return result

    def save_whatsapp_thread(self, case_id: int, details: dict[str, Any]) -> None:
        now = _now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO rh_rescission_whatsapp_threads(rescission_case_id,connection_id,contact_id,ticket_id,queue_id,user_id,whatsapp_id,status,last_protocol,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(rescission_case_id) DO UPDATE SET connection_id=excluded.connection_id,contact_id=excluded.contact_id,
                   ticket_id=excluded.ticket_id,queue_id=excluded.queue_id,user_id=excluded.user_id,whatsapp_id=excluded.whatsapp_id,
                   status=excluded.status,last_protocol=excluded.last_protocol,updated_at=excluded.updated_at""",
                (case_id, int(details.get("connection_id") or 0), str(details.get("contact_id") or ""), str(details.get("ticket_id") or ""),
                 int(details.get("queue_id") or 0), int(details.get("user_id") or 0), int(details.get("whatsapp_id") or 0),
                 str(details.get("status") or "ABERTO"), str(details.get("protocol") or ""), now, now),
            )

    def reconcile_whatsapp_ticket(
        self, case_id: int, *, ticket_id: str, provider_status: str, actor: str,
    ) -> str:
        """Move o controle local conforme o estado confirmado pelo canal externo."""
        normalized = " ".join(str(provider_status or "").strip().lower().split())
        closed_states = {"closed", "resolved", "completed", "finished", "concluded", "concluido", "concluído"}
        is_closed = normalized in closed_states
        now = _now()
        with self.connect() as db:
            thread = db.execute(
                "SELECT * FROM rh_rescission_whatsapp_threads WHERE rescission_case_id=?", (case_id,),
            ).fetchone()
            if not thread or str(thread["ticket_id"]) != str(ticket_id):
                raise LookupError("Ticket do canal de comunicação não encontrado nesta rescisão.")
            previous_thread_status = str(thread["status"] or "")
            thread_status = "CONCLUIDO" if is_closed else "AGUARDANDO_CONFIRMACAO"
            db.execute(
                "UPDATE rh_rescission_whatsapp_threads SET status=?,updated_at=? WHERE rescission_case_id=?",
                (thread_status, now, case_id),
            )
            case = db.execute("SELECT status FROM rh_rescission_cases WHERE id=?", (case_id,)).fetchone()
            if not case:
                raise LookupError("Rescisão não encontrada.")
            previous_case_status = str(case["status"] or "")
            if is_closed:
                case_status = "CONCLUIDO"
            else:
                details = self._details(db, case_id)
                case_status = self._case_status(
                    details["requirements"], details["deliveries"], current_status=previous_case_status,
                )
            db.execute(
                "UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?",
                (case_status, now, case_id),
            )
            if previous_thread_status != thread_status or previous_case_status != case_status:
                self._event(
                    db, case_id, "TICKET_CANAL_SINCRONIZADO", actor,
                    ticket_id=str(ticket_id), provider_status=normalized,
                    previous_status=previous_case_status, status=case_status,
                )
        return case_status

    def get(self, case_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._details(db, case_id)

    def apply_contract_exemptions(self, case_id: int, *, actor: str = "sistema") -> None:
        """Aplica a regra de temporários também aos casos já abertos."""
        with self.connect() as db:
            row = db.execute(
                """SELECT r.id,e.tipo_contrato FROM rh_rescission_cases r
                   JOIN employees e ON e.id=r.employee_id WHERE r.id=?""", (case_id,),
            ).fetchone()
            if not row:
                raise LookupError("Rescisão não encontrada.")
            self._ensure_requirements(db, case_id, actor)
            self._apply_contract_exemptions(db, case_id, str(row["tipo_contrato"] or ""), actor)
            details = self._details(db, case_id)
            db.execute(
                "UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?",
                (self._case_status(details["requirements"], details["deliveries"], current_status=str(details["status"])), _now(), case_id),
            )

    def ensure_cases_for_dismissed(
        self, company_id: int, *, actor: str = "sistema", date_from: str = "", date_to: str = "",
    ) -> int:
        """Inclui no controle os desligamentos já registrados no prontuário.

        A rotina é idempotente: não cria duplicidade para funcionários que já
        possuem uma central de rescisão aberta.
        """
        clauses, params = [
            "e.company_id=?", "trim(e.data_demissao)<>''", "r.id IS NULL",
            "(trim(e.data_admissao)='' OR e.data_demissao>=e.data_admissao)",
        ], [company_id]
        if date_from:
            date.fromisoformat(date_from[:10]); clauses.append("e.data_demissao>=?"); params.append(date_from[:10])
        if date_to:
            date.fromisoformat(date_to[:10]); clauses.append("e.data_demissao<=?"); params.append(date_to[:10])
        with self.connect() as db:
            candidates = [dict(row) for row in db.execute(
                """SELECT e.id,e.data_demissao,coalesce(d.motivo_demissao_codigo,'') AS reason_code,
                          coalesce(d.motivo_demissao_comentarios,'') AS reason_label,
                          coalesce(s.external_id,'') AS selecty_work_id
                   FROM employees e
                   LEFT JOIN employee_admission_details d ON d.employee_id=e.id
                   LEFT JOIN employee_source_identities s ON s.employee_id=e.id AND s.source='SELECTY'
                   LEFT JOIN rh_rescission_cases r ON r.employee_id=e.id
                   WHERE """ + " AND ".join(clauses) + " ORDER BY e.data_demissao,e.id",
                params,
            )]
        created = 0
        for item in candidates:
            try:
                self.open_case(
                    company_id, int(item["id"]), dismissal_date=str(item["data_demissao"]),
                    reason_code=str(item["reason_code"]), reason_label=str(item["reason_label"]),
                    selecty_work_id=str(item["selecty_work_id"]), actor=actor,
                )
                created += 1
            except ValueError:
                # Datas antigas preenchidas fora do padrão não devem impedir a
                # consulta dos demais desligamentos; serão corrigidas no prontuário.
                continue
        return created

    def _case_filters(
        self, company_id: int, *, search: str = "", tomador: str = "", reason: str = "", period: str = "",
        date_from: str = "", date_to: str = "",
    ) -> tuple[list[str], list[object]]:
        clauses, params = ["r.company_id=?"], [company_id]
        if search.strip():
            token = f"%{search.strip()}%"; clauses.append("(e.nome_completo LIKE ? OR e.codigo_folha LIKE ? OR e.cpf LIKE ?)"); params.extend([token] * 3)
        if tomador.strip(): clauses.append("coalesce(d.tomador_servico,'')=?"); params.append(tomador.strip())
        if reason.strip(): clauses.append("r.reason_code=?"); params.append(reason.strip())
        if date_from.strip():
            date.fromisoformat(date_from[:10]); clauses.append("r.dismissal_date >= ?"); params.append(date_from[:10])
        if date_to.strip():
            date.fromisoformat(date_to[:10]); clauses.append("r.dismissal_date <= ?"); params.append(date_to[:10])
        today = date.today()
        if period == "VENCIDOS":
            clauses.append("r.status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND r.deadline_date < ?"); params.append(today.isoformat())
        elif period == "URGENTES":
            clauses.append("r.status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND r.deadline_date BETWEEN ? AND ?"); params.extend([today.isoformat(), (today + timedelta(days=2)).isoformat()])
        elif period == "NO_PRAZO":
            clauses.append("r.status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND r.deadline_date BETWEEN ? AND ?"); params.extend([today.isoformat(), (today + timedelta(days=10)).isoformat()])
        elif period == "PENDENTES":
            clauses.append("r.status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO')")
        elif period == "ENTREGUES":
            clauses.append("r.status='ENTREGUE'")
        elif period == "CONCLUIDOS":
            clauses.append("r.status='CONCLUIDO'")
        return clauses, params

    def list_cases(
        self, company_id: int, *, search: str = "", tomador: str = "", reason: str = "", period: str = "",
        date_from: str = "", date_to: str = "", limit: int | None = None, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, params = self._case_filters(
            company_id, search=search, tomador=tomador, reason=reason, period=period,
            date_from=date_from, date_to=date_to,
        )
        suffix = " ORDER BY CASE WHEN r.status='CONCLUIDO' THEN 1 ELSE 0 END,r.deadline_date,e.nome_completo"
        if limit is not None:
            suffix += " LIMIT ? OFFSET ?"
            params.extend([max(1, min(int(limit), 100)), max(0, int(offset))])
        with self.connect() as db:
            rows = [dict(item) for item in db.execute(
                """SELECT r.*,e.nome_completo,e.codigo_folha,e.cpf,d.tomador_servico,e.cargo_id,
                   (SELECT count(*) FROM rh_rescission_requirements q WHERE q.rescission_case_id=r.id AND q.mandatory=1 AND q.status='PENDENTE') pending_documents,
                   (SELECT count(*) FROM rh_rescission_delivery_events dly WHERE dly.rescission_case_id=r.id AND dly.status='REGISTRADO') delivery_count,
                   (SELECT max(coalesce(dly.completed_at,dly.created_at)) FROM rh_rescission_delivery_events dly WHERE dly.rescission_case_id=r.id AND dly.status='REGISTRADO') last_delivery_at
                   FROM rh_rescission_cases r JOIN employees e ON e.id=r.employee_id
                   LEFT JOIN employee_admission_details d ON d.employee_id=e.id WHERE """ + " AND ".join(clauses) +
                suffix, params
            )]
        for row in rows:
            row["days_remaining"], row["deadline_state"] = self._deadline_state(str(row["deadline_date"]))
            if row["status"] == "CONCLUIDO":
                row["operational_phase"] = "CONCLUIDO"
            elif row["status"] == "ENTREGUE":
                row["operational_phase"] = "ENTREGUE"
            elif row["deadline_state"] == "VENCIDO":
                row["operational_phase"] = "VENCIDO"
            else:
                row["operational_phase"] = "NO_PRAZO"
        return rows

    def count_cases(
        self, company_id: int, *, search: str = "", tomador: str = "", reason: str = "", period: str = "",
        date_from: str = "", date_to: str = "",
    ) -> int:
        clauses, params = self._case_filters(
            company_id, search=search, tomador=tomador, reason=reason, period=period,
            date_from=date_from, date_to=date_to,
        )
        with self.connect() as db:
            return int(db.execute("SELECT count(*) FROM rh_rescission_cases r JOIN employees e ON e.id=r.employee_id LEFT JOIN employee_admission_details d ON d.employee_id=e.id WHERE " + " AND ".join(clauses), params).fetchone()[0])

    def dashboard(self, company_id: int) -> dict[str, int]:
        today = date.today(); horizon = today + timedelta(days=10)
        with self.connect() as db:
            row = db.execute(
                """SELECT count(*) AS total,
                          sum(CASE WHEN status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') THEN 1 ELSE 0 END) AS pendentes,
                          sum(CASE WHEN status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND deadline_date BETWEEN ? AND ? THEN 1 ELSE 0 END) AS no_prazo,
                          sum(CASE WHEN status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND deadline_date BETWEEN ? AND ? THEN 1 ELSE 0 END) AS urgentes,
                          sum(CASE WHEN status IN ('EM_DOCUMENTACAO','PRONTO_PARA_ENVIO') AND deadline_date < ? THEN 1 ELSE 0 END) AS vencidos,
                          sum(CASE WHEN status='ENTREGUE' THEN 1 ELSE 0 END) AS entregues,
                          sum(CASE WHEN status='CONCLUIDO' THEN 1 ELSE 0 END) AS concluidos
                   FROM rh_rescission_cases WHERE company_id=?""",
                (today.isoformat(), horizon.isoformat(), today.isoformat(), (today + timedelta(days=2)).isoformat(), today.isoformat(), company_id),
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "pendentes", "no_prazo", "urgentes", "vencidos", "entregues", "concluidos")}

    def attach_document(self, case_id: int, requirement_id: int, document_id: int, *, actor: str) -> None:
        with self.connect() as db:
            case = db.execute("SELECT employee_id FROM rh_rescission_cases WHERE id=?", (case_id,)).fetchone()
            requirement = db.execute("SELECT id FROM rh_rescission_requirements WHERE id=? AND rescission_case_id=?", (requirement_id, case_id)).fetchone()
            document = db.execute("SELECT id FROM employee_documents WHERE id=? AND employee_id=?", (document_id, int(case["employee_id"]) if case else 0)).fetchone()
            if not case or not requirement or not document:
                raise LookupError("Documento ou requisito da rescisão não encontrado.")
            now = _now()
            db.execute("UPDATE rh_rescission_requirements SET document_id=?,status='ANEXADO',exception_reason='',updated_by=?,updated_at=? WHERE id=?", (document_id, actor, now, requirement_id))
            details = self._details(db, case_id)
            db.execute("UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?", (self._case_status(details["requirements"], details["deliveries"], current_status=str(details["status"])), now, case_id))
            self._event(db, case_id, "DOCUMENTO_VINCULADO", actor, requirement_id=requirement_id, document_id=document_id)

    def dispense_requirement(self, case_id: int, requirement_id: int, *, reason: str, actor: str) -> None:
        justification = " ".join(reason.split())[:500]
        if len(justification) < 8:
            raise ValueError("Informe a justificativa da dispensa documental.")
        with self.connect() as db:
            requirement = db.execute("SELECT id FROM rh_rescission_requirements WHERE id=? AND rescission_case_id=?", (requirement_id, case_id)).fetchone()
            if not requirement: raise LookupError("Requisito da rescisão não encontrado.")
            now = _now(); db.execute("UPDATE rh_rescission_requirements SET status='DISPENSADO',exception_reason=?,updated_by=?,updated_at=? WHERE id=?", (justification, actor, now, requirement_id))
            details = self._details(db, case_id); db.execute("UPDATE rh_rescission_cases SET status=?,updated_at=? WHERE id=?", (self._case_status(details["requirements"], details["deliveries"], current_status=str(details["status"])), now, case_id))
            self._event(db, case_id, "DOCUMENTO_DISPENSADO", actor, requirement_id=requirement_id, reason=justification)

    def register_delivery(
        self, case_id: int, *, channel: str, protocol: str = "", document_ids: set[int],
        actor: str, evidence: dict[str, Any] | None = None,
    ) -> str:
        selected = channel.strip().upper()
        if selected not in CHANNELS: raise ValueError("Selecione um canal de entrega válido.")
        reference = " ".join(protocol.split())[:240] or f"RH-RES-{case_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{selected}"
        full_evidence = dict(evidence or {})
        channel_evidence = dict(full_evidence.get("channels") or {})
        recorded_channels = [
            item for item in ("WHATSAPP", "EMAIL", "COPIA_EMAIL") if item in channel_evidence
        ] or [selected]

        def channel_reference(item: str) -> str:
            marker = item + ":"
            for part in reference.split(" | "):
                if part.strip().upper().startswith(marker):
                    return part.strip()[len(marker):][:240]
            return reference

        with self.connect() as db:
            details = self._details(db, case_id)
            if not details: raise LookupError("Rescisão não encontrada.")
            attached = {int(item["document_id"]) for item in details["requirements"] if item["status"] in {"ANEXADO", "ENTREGUE"} and item.get("document_id")}
            selected_documents = {int(item) for item in document_ids}
            if selected_documents - attached:
                raise ValueError("A seleção contém arquivo que não está vinculado ao checklist desta rescisão.")
            now = _now()
            for delivered_channel in recorded_channels:
                delivered_reference = channel_reference(delivered_channel)
                cursor = db.execute(
                    "INSERT INTO rh_rescission_delivery_events(rescission_case_id,channel,status,protocol,created_by,created_at,completed_at) VALUES(?,?, 'REGISTRADO',?,?,?,?)",
                    (case_id, delivered_channel, delivered_reference, actor, now, now),
                )
                delivery_id = int(cursor.lastrowid)
                db.executemany(
                    "INSERT INTO rh_rescission_delivery_documents(delivery_event_id,document_id,created_at) VALUES(?,?,?)",
                    [(delivery_id, document_id, now) for document_id in sorted(selected_documents)],
                )
                isolated_evidence = dict(full_evidence)
                if channel_evidence:
                    isolated_evidence["channels"] = {
                        delivered_channel: dict(channel_evidence.get(delivered_channel) or {})
                    }
                self._event(
                    db, case_id, "ENTREGA_REGISTRADA", actor, delivery_id=delivery_id,
                    channel=delivered_channel, protocol=delivered_reference,
                    document_ids=sorted(selected_documents), evidence=isolated_evidence,
                )
            if selected_documents:
                placeholders = ",".join("?" for _ in selected_documents)
                db.execute(
                    "UPDATE rh_rescission_requirements SET status='ENTREGUE',updated_by=?,updated_at=? WHERE rescission_case_id=? AND document_id IN (" + placeholders + ")",
                    [actor, now, case_id, *sorted(selected_documents)],
                )
            updated = self._details(db, case_id)
            db.execute(
                "UPDATE rh_rescission_cases SET status=?,preferred_channel=?,updated_at=? WHERE id=?",
                (self._case_status(updated["requirements"], updated["deliveries"], current_status=str(updated["status"])), selected, now, case_id),
            )
        return reference

    def validate_delivery_selection(self, case_id: int, document_ids: set[int]) -> None:
        """Valida o pacote antes que qualquer canal externo seja acionado."""
        with self.connect() as db:
            details = self._details(db, case_id)
        if not details:
            raise LookupError("Rescisão não encontrada.")
        attached = {int(item["document_id"]) for item in details["requirements"] if item["status"] in {"ANEXADO", "ENTREGUE"} and item.get("document_id")}
        selected = {int(item) for item in document_ids}
        if selected - attached:
            raise ValueError("A seleção contém arquivo que não está vinculado ao checklist desta rescisão.")

    def record_delivery_failure(
        self, case_id: int, *, channel: str, error: str, document_ids: set[int], actor: str,
        evidence: dict[str, Any] | None = None,
    ) -> int:
        selected = channel.strip().upper()
        if selected not in CHANNELS:
            selected = "WHATSAPP"
        now = _now()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM rh_rescission_cases WHERE id=?", (case_id,)).fetchone():
                raise LookupError("Rescisão não encontrada.")
            cursor = db.execute(
                """INSERT INTO rh_rescission_delivery_events(
                       rescission_case_id,channel,status,protocol,error_message,created_by,created_at,completed_at
                   ) VALUES(?,?,'ERRO','',?,?,?,?)""",
                (case_id, selected, " ".join(error.split())[:1000], actor, now, now),
            )
            delivery_id = int(cursor.lastrowid)
            self._event(
                db, case_id, "ENTREGA_COM_ERRO", actor, delivery_id=delivery_id,
                channel=selected, document_ids=sorted(document_ids), error=" ".join(error.split())[:1000],
                evidence=evidence or {},
            )
            return delivery_id

    def confirm_receipt(self, case_id: int, delivery_id: int, *, reference: str, actor: str) -> None:
        proof = " ".join(reference.split())[:500]
        if len(proof) < 5:
            raise ValueError("Informe a referência da confirmação recebida no WhatsApp ou e-mail.")
        with self.connect() as db:
            delivery = db.execute(
                """SELECT id,status FROM rh_rescission_delivery_events
                   WHERE id=? AND rescission_case_id=?""", (delivery_id, case_id),
            ).fetchone()
            if not delivery or str(delivery["status"]) != "REGISTRADO":
                raise LookupError("Entrega registrada não encontrada para confirmar o recebimento.")
            self._event(
                db, case_id, "RECEBIMENTO_CONFIRMADO", actor, delivery_id=delivery_id,
                confirmation_reference=proof,
            )
            now = _now()
            db.execute("UPDATE rh_rescission_cases SET status='CONCLUIDO',updated_at=? WHERE id=?", (now, case_id))
            db.execute(
                "UPDATE rh_rescission_whatsapp_threads SET status='CONCLUIDO',updated_at=? WHERE rescission_case_id=?",
                (now, case_id),
            )
