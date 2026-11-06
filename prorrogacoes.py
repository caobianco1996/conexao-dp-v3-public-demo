"""Fila de prorrogações contratuais pronta para a integração Clicksign.

Este serviço não altera o contrato nem envia dados a provedores externos. Ele apenas
identifica vínculos temporários vencidos no primeiro marco e registra a intenção de
envio de um TPR, preservando o fluxo homologado de cadastro e contrato.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import re
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalized_clicksign_phone(value: object) -> str:
    """Converte telefone brasileiro ao formato nacional exigido pela Clicksign.

    O cadastro pode conter máscara e o indicativo do país (55). A API da
    Clicksign espera somente DDD + número, com 10 ou 11 dígitos; por isso o
    indicativo é removido, sem alterar o telefone originalmente cadastrado.
    """
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("55") and len(digits) in {12, 13}:
        digits = digits[2:]
    if len(digits) not in {10, 11}:
        raise ValueError("Telefone/WhatsApp deve conter DDD e 8 ou 9 dígitos.")
    ddd = int(digits[:2])
    if not 11 <= ddd <= 99:
        raise ValueError("Telefone/WhatsApp possui DDD inválido.")
    return digits


def normalize_cpf(value: object) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def valid_cpf(value: object) -> bool:
    cpf = normalize_cpf(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11: return False
    for position in (9, 10):
        digit = (sum(int(cpf[index]) * (position + 1 - index) for index in range(position)) * 10) % 11
        if (0 if digit == 10 else digit) != int(cpf[position]): return False
    return True


class ContractExtensionService:
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
    def _is_temporary(value: object) -> bool:
        normalized = str(value or "").strip().upper()
        return "TEMPOR" in normalized

    def eligible(self, company_id: int = 0, *, reference_date: str = "", alert_days: int = 30) -> list[dict[str, Any]]:
        """Retorna temporários ativos que vencem em até 30 dias e ainda não foram prorrogados."""
        start = date.fromisoformat(reference_date) if reference_date else date.today()
        end = start + timedelta(days=alert_days)
        where = ["upper(trim(COALESCE(e.situacao,''))) IN ('','ATIVO')", "trim(COALESCE(e.data_demissao,''))=''",
                 "trim(COALESCE(d.data_1_vencimento,''))<>''", "trim(COALESCE(d.data_1_vencimento,''))<=?",
                 "(trim(COALESCE(d.data_2_vencimento,''))='' OR r.id IS NOT NULL)"]
        args: list[object] = [end.isoformat()]
        if company_id:
            where.append("e.company_id=?")
            args.append(int(company_id))
        with self.connect() as db:
            rows = db.execute(
                """SELECT e.id AS employee_id,e.company_id,e.codigo_folha,e.nome_completo,e.cpf,e.celular,e.telefone,
                          e.email,e.tipo_contrato,d.vinculo,e.data_admissao,d.data_1_vencimento,d.data_2_vencimento,
                          r.id AS request_id,r.status AS request_status,r.created_at AS request_created_at,
                          r.error_message,r.operational_status,r.delivery_channel,r.employer_signer_id,
                          r.clicksign_envelope_id,r.clicksign_signer_id,r.sent_at,r.last_attempt_at,r.attempt_count,r.notification_status,
                           r.provider_status,r.last_notification_at,r.previewed_at,r.last_status_sync_at,r.status_sync_error,
                           r.signed_employee_document_id,r.signed_document_path,r.finalized_at,r.canceled_at,r.canceled_by,
                           r.cancellation_reason,r.non_signature_at,r.non_signature_document_id,r.non_signature_document_path,
                           r.non_signature_reason,s.name employer_signer_name
                   FROM employees e JOIN employee_admission_details d ON d.employee_id=e.id
                   LEFT JOIN rh_contract_extension_requests r
                     ON r.id=(SELECT r2.id FROM rh_contract_extension_requests r2
                              WHERE r2.employee_id=e.id AND r2.document_type='TPR' AND r2.first_expiration_date=d.data_1_vencimento
                              ORDER BY CASE WHEN trim(r2.clicksign_envelope_id)<>'' THEN 1 ELSE 0 END DESC,r2.id DESC LIMIT 1)
                   LEFT JOIN rh_clicksign_signers s ON s.id=r.employer_signer_id
                   WHERE """ + " AND ".join(where) + " ORDER BY d.data_1_vencimento,e.nome_completo",
                args,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            if not self._is_temporary(row["vinculo"] or row["tipo_contrato"]):
                continue
            item = dict(row)
            days = (date.fromisoformat(str(item["data_1_vencimento"])) - start).days
            item["days_until_expiration"] = days
            item["deadline_status"] = "VENCIDO_BLOQUEADO" if days < 0 else "URGENTE" if days <= 7 else "NO_PRAZO"
            phone = item["celular"] or item["telefone"]
            try:
                item["clicksign_phone"] = normalized_clicksign_phone(phone)
                item["phone_valid"] = True
            except ValueError:
                item["clicksign_phone"] = ""
                item["phone_valid"] = False
            result.append(item)
        return result

    def pending_count(self, company_id: int = 0) -> int:
        pending={"PENDENTE","PENDENTE_ENVIO","PREVIA_VALIDADA","ENVIANDO","ENVIADO","AGUARDANDO_ASSINATURA","ASSINADO","ERRO"}
        return sum(1 for row in self.eligible(company_id) if str(row.get("operational_status") or row.get("request_status") or "PENDENTE").upper() in pending)

    def history_for_employee(self, employee_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows=db.execute("""SELECT r.*,s.name employer_signer_name
                FROM rh_contract_extension_requests r
                LEFT JOIN rh_clicksign_signers s ON s.id=r.employer_signer_id
                WHERE r.employee_id=? ORDER BY r.id DESC""",(employee_id,)).fetchall()
        return [dict(row) for row in rows]

    def register_tpr(self, employee_id: int, *, actor: str) -> int:
        now = _now()
        with self.connect() as db:
            row = db.execute(
                """SELECT e.id,e.company_id,e.codigo_folha,e.nome_completo,e.cpf,e.celular,e.telefone,e.situacao,e.tipo_contrato,
                          d.vinculo,d.data_1_vencimento,d.data_2_vencimento
                   FROM employees e JOIN employee_admission_details d ON d.employee_id=e.id WHERE e.id=?""",
                (employee_id,),
            ).fetchone()
            if not row:
                raise LookupError("Funcionário não encontrado.")
            situation = str(row["situacao"] or "").strip().upper()
            if situation not in {"", "ATIVO"} or not self._is_temporary(row["vinculo"] or row["tipo_contrato"]):
                raise ValueError("Somente contratos temporários ativos podem entrar na fila de prorrogação.")
            first = str(row["data_1_vencimento"] or "")
            if not first or str(row["data_2_vencimento"] or ""):
                raise ValueError("O funcionário não possui 1º vencimento pendente de prorrogação.")
            deadline = date.fromisoformat(first)
            if deadline < date.today() or deadline > date.today() + timedelta(days=30):
                raise ValueError("A prorrogação só pode ser preparada entre hoje e 30 dias antes do 1º vencimento.")
            if not str(row["cpf"] or "").strip():
                raise ValueError("CPF obrigatório para registrar o envio à Clicksign.")
            try:
                normalized_clicksign_phone(row["celular"] or row["telefone"])
            except ValueError as exc:
                raise ValueError(f"Telefone/WhatsApp obrigatório e inválido para envio à Clicksign: {exc}") from exc
            existing = db.execute(
                "SELECT id FROM rh_contract_extension_requests WHERE employee_id=? AND document_type='TPR' AND first_expiration_date=?",
                (employee_id, first),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = db.execute(
                """INSERT INTO rh_contract_extension_requests(
                       company_id,employee_id,document_type,status,first_expiration_date,created_by,created_at,updated_at
                   ) VALUES(?,?, 'TPR','PENDENTE_ENVIO',?,?,?,?)""",
                (row["company_id"], employee_id, first, actor, now, now),
            )
            request_id = int(cursor.lastrowid)
            self._audit(db, actor, "PRORROGACAO_TPR_REGISTRADA", request_id, {
                "employee_id": employee_id, "codigo_folha": row["codigo_folha"], "primeiro_vencimento": first,
            })
            return request_id

    def _request_context(self, request_id: int, employer_signer_id: int = 0) -> dict[str, Any]:
        with self.connect() as db:
            request=db.execute("SELECT * FROM rh_contract_extension_requests WHERE id=?",(request_id,)).fetchone()
            if not request: raise LookupError("Solicitação de prorrogação não encontrada.")
            signer_id=employer_signer_id or int(request["employer_signer_id"] or 0)
            row=db.execute("""SELECT r.*,e.nome_completo,e.cpf,e.celular,e.telefone,e.email employee_email,e.funcao,
                                    e.codigo_folha,e.matricula,e.data_admissao,e.tipo_contrato,d.vinculo,d.tomador_servico,
                                    d.tomador_cnpj,d.local_trabalho,d.motivo_contratacao,d.demanda_servico,c.legal_name,c.main_cnpj,
                                    COALESCE((SELECT trim(ct.address || CASE WHEN ct.city<>'' THEN ', '||ct.city ELSE '' END || CASE WHEN ct.state<>'' THEN '/'||ct.state ELSE '' END || CASE WHEN ct.postal_code<>'' THEN ' - CEP '||ct.postal_code ELSE '' END) FROM company_tax_ids ct WHERE ct.company_id=c.id AND ct.cnpj=c.main_cnpj ORDER BY ct.id LIMIT 1),'') employer_address,
                                    s.id selected_signer_id,s.name representative_name,s.email representative_email,s.cpf representative_cpf,
                                    s.birthday representative_birthday,s.phone representative_phone,s.send_channel signer_channel,
                                    s.auto_signature,s.job_title representative_job_title,s.signing_order,s.auto_signature_authorized_at,
                                    COALESCE(cfg.allowed_employee_channels,'AMBOS') allowed_employee_channels
                             FROM rh_contract_extension_requests r JOIN employees e ON e.id=r.employee_id
                             JOIN employee_admission_details d ON d.employee_id=e.id JOIN companies c ON c.id=r.company_id
                             LEFT JOIN rh_clicksign_signers s ON s.id=? AND s.company_id=r.company_id AND s.active=1 AND s.deleted_at=''
                             LEFT JOIN rh_clicksign_settings cfg ON cfg.company_id=r.company_id
                             WHERE r.id=?""",(signer_id,request_id)).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _validate_context(row: dict[str, Any], employee_channel: str) -> None:
        channel=str(employee_channel or "").upper()
        if channel not in {"EMAIL","WHATSAPP","AMBOS"}: raise ValueError("Selecione o canal do funcionário.")
        allowed=str(row.get("allowed_employee_channels") or "AMBOS").upper()
        if allowed != "AMBOS" and channel != allowed: raise ValueError(f"O canal {channel} não está permitido na configuração desta empresa.")
        required={"Razão social da empregadora":row.get("legal_name"),"CNPJ da empregadora":row.get("main_cnpj"),"Endereço da empregadora":row.get("employer_address"),
                  "Nome do trabalhador":row.get("nome_completo"),"CPF do trabalhador":row.get("cpf"),"Função":row.get("funcao"),"ID Folha ou matrícula":row.get("codigo_folha") or row.get("matricula"),
                  "Data de admissão":row.get("data_admissao"),"1º vencimento":row.get("first_expiration_date"),"Tomadora":row.get("tomador_servico"),"CNPJ da tomadora":row.get("tomador_cnpj"),
                  "Local da prestação":row.get("local_trabalho"),"Demanda de serviço":row.get("demanda_servico") or row.get("motivo_contratacao"),"Representante":row.get("representative_name"),
                  "CPF do representante":row.get("representative_cpf"),"Cargo do representante":row.get("representative_job_title"),"E-mail do representante":row.get("representative_email")}
        missing=[label for label,value in required.items() if not str(value or "").strip()]
        if missing: raise ValueError("Complete antes da prévia: " + ", ".join(missing) + ".")
        if not valid_cpf(row.get("cpf")) or not valid_cpf(row.get("representative_cpf")): raise ValueError("CPF do trabalhador ou representante inválido.")
        if channel in {"EMAIL","AMBOS"} and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",str(row.get("employee_email") or "").strip()): raise ValueError("E-mail válido do funcionário é obrigatório para o canal escolhido.")
        if channel in {"WHATSAPP","AMBOS"}: normalized_clicksign_phone(row.get("celular") or row.get("telefone"))
        representative_channel=str(row.get("signer_channel") or row.get("representative_channel") or "").upper()
        if representative_channel not in {"EMAIL","WHATSAPP","AMBOS","NENHUM"}: raise ValueError("Canal do representante inválido.")
        if representative_channel=="NENHUM" and not row.get("auto_signature"): raise ValueError("Representante sem autoassinatura precisa de canal de assinatura.")
        if representative_channel in {"WHATSAPP","AMBOS"}: normalized_clicksign_phone(row.get("representative_phone"))
        if row.get("auto_signature") and (not row.get("representative_birthday") or not row.get("auto_signature_authorized_at")): raise ValueError("Autoassinatura do representante não possui nascimento e autorização prévia completos.")

    def prepare_preview(self, request_id: int, *, employer_signer_id: int, employee_channel: str, actor: str, storage_root: Path) -> Path:
        from .tpr import build_tpr_pdf
        row=self._request_context(request_id,employer_signer_id); self._validate_context(row,employee_channel)
        if row.get("clicksign_envelope_id"): raise ValueError("Já existe envelope Clicksign; a prévia não pode ser substituída.")
        row["representative_channel"]=row.get("signer_channel") or row.get("representative_channel") or "EMAIL"
        pdf,end=build_tpr_pdf(employee=row,company=row,representative=row,first_expiration=str(row["first_expiration_date"]),envelope_id="A SER GERADO")
        storage_root.mkdir(parents=True,exist_ok=True); target=storage_root/f"TPR_{request_id}_{str(row['first_expiration_date']).replace('-','')}.pdf"; target.write_bytes(pdf); now=_now()
        with self.connect() as db:
            db.execute("""UPDATE rh_contract_extension_requests SET employer_signer_id=?,delivery_channel=?,representative_channel=?,second_expiration_date=?,tpr_storage_path=?,previewed_at=?,operational_status='PREVIA_VALIDADA',updated_at=?,error_message='' WHERE id=?""",
                       (employer_signer_id,employee_channel.upper(),row.get("representative_channel") or "EMAIL",end,str(target),now,now,request_id))
            self._audit(db,actor,"PRORROGACAO_TPR_PREVIA",request_id,{"signer_id":employer_signer_id,"channel":employee_channel.upper()})
        return target

    def dispatch_tpr(self, request_id: int, *, actor: str, clicksign, storage_root: Path) -> None:
        """Envia somente uma prévia validada e preserva IDs parciais a cada etapa."""
        from .clicksign import ClicksignWorkflowError
        row=self._request_context(request_id); self._validate_context(row,str(row.get("delivery_channel") or ""))
        if row.get("operational_status") not in {"PREVIA_VALIDADA","ERRO"}: raise ValueError("Gere e confira a prévia antes do envio.")
        if row.get("clicksign_envelope_id"): raise ValueError("Já existe envelope para esta solicitação; sincronize ou use reenvio.")
        target=Path(str(row.get("tpr_storage_path") or ""));
        if not target.is_file(): target=self.prepare_preview(request_id,employer_signer_id=int(row["selected_signer_id"]),employee_channel=str(row["delivery_channel"]),actor=actor,storage_root=storage_root)
        pdf=target.read_bytes(); phone=normalized_clicksign_phone(row.get("celular") or row.get("telefone")); now=_now()
        with self.connect() as db: db.execute("UPDATE rh_contract_extension_requests SET attempt_count=attempt_count+1,last_attempt_at=?,operational_status='ENVIANDO',updated_at=? WHERE id=?",(now,now,request_id))
        def progress(values: dict[str,str]) -> None:
            with self.connect() as db:
                db.execute("""UPDATE rh_contract_extension_requests SET clicksign_envelope_id=COALESCE(NULLIF(?,''),clicksign_envelope_id),clicksign_document_id=COALESCE(NULLIF(?,''),clicksign_document_id),clicksign_signer_id=COALESCE(NULLIF(?,''),clicksign_signer_id),clicksign_representative_signer_id=COALESCE(NULLIF(?,''),clicksign_representative_signer_id),provider_status=COALESCE(NULLIF(?,''),provider_status),updated_at=? WHERE id=?""",
                           (values.get("envelope_id",""),values.get("document_id",""),values.get("employee_signer_id",""),values.get("representative_signer_id",""),values.get("provider_status",""),_now(),request_id))
        try:
            external=clicksign.create_tpr_envelope(int(row["company_id"]),name=f"TPR - {row['nome_completo']} - {row['first_expiration_date']}",pdf=pdf,
                employee={"name":row["nome_completo"],"email":str(row["employee_email"]),"phone":phone,"cpf":normalize_cpf(row["cpf"])},
                representative={"name":row["representative_name"],"email":row["representative_email"],"phone":normalized_clicksign_phone(row.get("representative_phone")) if row.get("representative_phone") else "","channel":row.get("signer_channel") or row["representative_channel"],"cpf":normalize_cpf(row["representative_cpf"]),"birthday":row["representative_birthday"],"auto_signature":bool(row["auto_signature"]),"signing_order":row["signing_order"]},employee_channel=str(row["delivery_channel"]),progress_callback=progress)
        except ClicksignWorkflowError as exc:
            progress(exc.progress)
            with self.connect() as db:
                db.execute("UPDATE rh_contract_extension_requests SET status='ERRO',operational_status='ERRO',error_message=?,provider_response_json=?,updated_at=? WHERE id=?",(str(exc)[:900],json.dumps(exc.progress,ensure_ascii=False),_now(),request_id)); self._audit(db,actor,"CLICKSIGN_TPR_ERRO",request_id,{"reason":str(exc)[:300]})
            raise
        with self.connect() as db:
            sent=_now(); fallback=external.get("auto_signature_fallback")=="1"; notification_status="ACEITA_PELA_CLICKSIGN_ASSINATURA_MANUAL" if fallback else "ACEITA_PELA_CLICKSIGN"
            db.execute("""UPDATE rh_contract_extension_requests SET status='AGUARDANDO_ASSINATURA',operational_status='AGUARDANDO_ASSINATURA',activated_at=?,last_notification_at=?,notification_status=?,provider_status=?,provider_response_json=?,sent_at=?,updated_at=?,error_message='' WHERE id=?""",
                       (sent,sent,notification_status,external.get("provider_status","running"),json.dumps(external,ensure_ascii=False),sent,sent,request_id))
            if fallback:
                db.execute("UPDATE rh_clicksign_signers SET auto_signature=0,auto_signature_authorized_at='',updated_at=? WHERE id=? AND company_id=?",(sent,row["selected_signer_id"],row["company_id"]))
            self._audit(db,actor,"CLICKSIGN_TPR_ENVIADO",request_id,{"envelope_id":external.get("envelope_id"),"channel":row["delivery_channel"],"signer_id":row["selected_signer_id"],"auto_signature_fallback":fallback})

    def recover_failed_draft(self, request_id: int, *, actor: str, clicksign) -> None:
        row=self._request_context(request_id); envelope=str(row.get("clicksign_envelope_id") or "")
        if row.get("operational_status")!="ERRO" or not envelope: raise ValueError("A recuperação exige uma solicitação com erro e envelope parcial.")
        result=clicksign.delete_draft_envelope(int(row["company_id"]),envelope); now=_now()
        auto_term_error="termo assinado" in str(row.get("error_message") or "").lower() and "assinatura autom" in str(row.get("error_message") or "").lower()
        with self.connect() as db:
            if auto_term_error and row.get("selected_signer_id"):
                db.execute("UPDATE rh_clicksign_signers SET auto_signature=0,auto_signature_authorized_at='',updated_at=? WHERE id=? AND company_id=?",(now,row["selected_signer_id"],row["company_id"]))
            db.execute("""UPDATE rh_contract_extension_requests SET status='PENDENTE_ENVIO',operational_status='PREVIA_VALIDADA',clicksign_envelope_id='',clicksign_document_id='',clicksign_signer_id='',clicksign_representative_signer_id='',provider_status='RASCUNHO_REMOVIDO',provider_response_json=?,notification_status='',error_message='',updated_at=? WHERE id=?""",
                       (json.dumps(result,ensure_ascii=False),now,request_id))
            self._audit(db,actor,"CLICKSIGN_TPR_RASCUNHO_RECUPERADO",request_id,{"envelope_id":envelope,"auto_signature_desativada":auto_term_error})

    def resend(self, request_id: int, *, actor: str, clicksign) -> None:
        row=self._request_context(request_id)
        envelope=str(row.get("clicksign_envelope_id") or ""); signer=str(row.get("clicksign_signer_id") or "")
        if not envelope or not signer: raise ValueError("O reenvio exige envelope e signatário Clicksign já vinculados.")
        last=str(row.get("last_notification_at") or "")
        if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()<60: raise ValueError("Aguarde ao menos 1 minuto entre notificações.")
        result=clicksign.resend_notification(int(row["company_id"]),envelope,signer); now=_now()
        with self.connect() as db:
            db.execute("UPDATE rh_contract_extension_requests SET status='AGUARDANDO_ASSINATURA',operational_status='AGUARDANDO_ASSINATURA',provider_status='running',attempt_count=CASE WHEN attempt_count<1 THEN 2 ELSE attempt_count+1 END,last_attempt_at=?,last_notification_at=?,notification_status='REENVIO_ACEITO_PELA_CLICKSIGN',provider_response_json=?,error_message='',updated_at=? WHERE id=?",(now,now,json.dumps(result,ensure_ascii=False)[:3000],now,request_id)); self._audit(db,actor,"CLICKSIGN_TPR_REENVIADO",request_id,{"envelope_id":envelope,"estado_local_anterior":row.get("operational_status")})

    def cancel_package(self, request_id: int, *, actor: str, reason: str, clicksign) -> None:
        row=self._request_context(request_id); reason=" ".join(str(reason or "").split())[:500]
        if len(reason)<5: raise ValueError("Informe o motivo do cancelamento com pelo menos 5 caracteres.")
        if row.get("operational_status") in {"CONCLUIDO","CANCELADO"} or row.get("finalized_at") or row.get("signed_employee_document_id"):
            raise ValueError("Pacote concluído ou já cancelado não pode ser cancelado novamente.")
        envelope=str(row.get("clicksign_envelope_id") or ""); document_id=str(row.get("clicksign_document_id") or "")
        if not envelope: raise ValueError("Solicitação ainda não possui envelope Clicksign para cancelamento.")
        result=clicksign.cancel_package(int(row["company_id"]),envelope,document_id); now=_now(); provider=str(result.get("provider_status") or "canceled")
        with self.connect() as db:
            db.execute("""UPDATE rh_contract_extension_requests SET status='CANCELADO',operational_status='CANCELADO',provider_status=?,provider_response_json=?,canceled_at=?,canceled_by=?,cancellation_reason=?,status_sync_error='',error_message='',updated_at=? WHERE id=?""",
                       (provider,json.dumps(result,ensure_ascii=False)[:10000],now,actor,reason,now,request_id))
            db.execute("""INSERT INTO employee_history(employee_id,campo,valor_anterior,valor_novo,data_efetiva,documento_id,usuario,origem,criado_em)
                SELECT ?,?,?,?,?,NULL,?,?,? WHERE NOT EXISTS (
                    SELECT 1 FROM employee_history WHERE employee_id=? AND campo='prorrogacao_clicksign'
                    AND valor_novo='CANCELADO' AND criado_em=?
                )""",(int(row["employee_id"]),"prorrogacao_clicksign",str(row.get("operational_status") or ""),"CANCELADO",now[:10],actor,"CLICKSIGN",now,int(row["employee_id"]),now))
            self._audit(db,actor,"CLICKSIGN_TPR_CANCELADO",request_id,{"envelope_id":envelope,"document_id":document_id,"motivo":reason,"provider_status":provider})

    def register_non_signature(self, request_id: int, *, actor: str, clicksign, documents, storage_root: Path, provider_details: dict | None = None) -> int:
        """Encerra pacote vencido e arquiva evidência, sem efetivar a prorrogação."""
        from .tpr import build_non_signature_report

        row=self._request_context(request_id)
        if row.get("non_signature_document_id"):
            return int(row["non_signature_document_id"])
        if row.get("operational_status") in {"CONCLUIDO","ASSINADO","RECUSADO"} or row.get("signed_employee_document_id"):
            raise ValueError("Pacote assinado, concluído ou recusado não pode ser encerrado como não assinado.")
        deadline=date.fromisoformat(str(row.get("first_expiration_date") or ""))
        if date.today() <= deadline:
            raise ValueError("O prazo de assinatura ainda não terminou.")
        envelope=str(row.get("clicksign_envelope_id") or ""); document_id=str(row.get("clicksign_document_id") or "")
        if not envelope or not document_id:
            raise ValueError("Pacote sem envelope e documento Clicksign não pode gerar prova de ausência de assinatura.")
        details=provider_details or clicksign.envelope_details(int(row["company_id"]),envelope)
        provider=str(details.get("data",{}).get("attributes",{}).get("status") or row.get("provider_status") or "")
        if provider=="closed":
            raise ValueError("A Clicksign informa que o envelope foi concluído; sincronize e arquive o documento assinado.")
        cancellation=clicksign.cancel_package(int(row["company_id"]),envelope,document_id) if provider!="canceled" else {"provider_status":"canceled","already_canceled":True}
        reason=f"Ausência de assinatura até o encerramento do prazo em {deadline.strftime('%d/%m/%Y')}."
        snapshot={"provider_status":str(cancellation.get("provider_status") or "canceled"),"envelope":details,"cancellation":cancellation}
        payload=build_non_signature_report(context=row,provider_snapshot=snapshot,reason=reason)
        digest=hashlib.sha256(payload).hexdigest(); employee_id=int(row["employee_id"]); now=_now()
        with self.connect() as db:
            existing=db.execute("SELECT id,caminho_storage FROM employee_documents WHERE employee_id=? AND hash_sha256=? ORDER BY id LIMIT 1",(employee_id,digest)).fetchone()
        if existing:
            employee_document_id=int(existing["id"]); stored_path=str(existing["caminho_storage"] or "")
        else:
            storage_root.mkdir(parents=True,exist_ok=True)
            target=storage_root/f"TPR_{request_id}_NAO_ASSINADO_{deadline.strftime('%Y%m%d')}.pdf"
            target.write_bytes(payload)
            registration=documents.import_file(employee_id,target,document_type="ADITIVO_CONTRATUAL",actor=actor,
                document_date=now[:10],visibility="RH",standardized_filename=f"TPR_{employee_id}_{deadline.strftime('%Y%m%d')}_NAO_ASSINADO.pdf",
                classification_code="TPR_NAO_ASSINADO",category="Prorrogação contratual",
                description=f"Relatório de providências e ausência de assinatura no prazo · Clicksign · envelope {envelope}",
                classification_confidence=100,status="ATIVO")
            employee_document_id=int(registration.document_id)
            with documents.connect() as db:
                stored=db.execute("SELECT caminho_storage FROM employee_documents WHERE id=?",(employee_document_id,)).fetchone()
                stored_path=str(stored["caminho_storage"] if stored else "")
                db.execute("UPDATE employee_documents SET origem='CLICKSIGN',subtipo_documento='TPR_NAO_ASSINADO',atualizado_em=? WHERE id=?",(now,employee_document_id))
        with self.connect() as db:
            db.execute("""UPDATE rh_contract_extension_requests SET status='CANCELADO',operational_status='NAO_ASSINADO',
                provider_status='canceled',provider_response_json=?,non_signature_at=?,non_signature_document_id=?,
                non_signature_document_path=?,non_signature_reason=?,status_sync_error='',error_message='',updated_at=? WHERE id=?""",
                (json.dumps(snapshot,ensure_ascii=False)[:10000],now,employee_document_id,stored_path,reason,now,request_id))
            db.execute("""INSERT INTO employee_history(employee_id,campo,valor_anterior,valor_novo,data_efetiva,documento_id,usuario,origem,criado_em)
                VALUES(?,?,?,?,?,?,?,?,?)""",(employee_id,"prorrogacao_clicksign",str(row.get("operational_status") or ""),"NAO_ASSINADO",deadline.isoformat(),employee_document_id,actor,"CLICKSIGN",now))
            self._audit(db,actor,"CLICKSIGN_TPR_NAO_ASSINADO",request_id,{"employee_id":employee_id,"document_id":employee_document_id,"envelope_id":envelope,"prazo":deadline.isoformat()})
        return employee_document_id

    def finalize_completed(self, request_id: int, *, actor: str, clicksign, documents, storage_root: Path) -> int:
        row=self._request_context(request_id)
        if row.get("operational_status") != "CONCLUIDO": raise ValueError("Somente uma prorrogação concluída pode ser arquivada no prontuário.")
        if row.get("signed_employee_document_id") and row.get("finalized_at"):
            return int(row["signed_employee_document_id"])
        envelope=str(row.get("clicksign_envelope_id") or ""); document_id=str(row.get("clicksign_document_id") or "")
        if not envelope or not document_id: raise ValueError("A prorrogação concluída não possui envelope e documento Clicksign para arquivamento.")
        payload,provider_filename,_=clicksign.download_signed_document(int(row["company_id"]),envelope,document_id)
        digest=hashlib.sha256(payload).hexdigest(); employee_id=int(row["employee_id"]); now=_now()
        with self.connect() as db:
            existing=db.execute("SELECT id,caminho_storage FROM employee_documents WHERE employee_id=? AND tipo_documento='ADITIVO_CONTRATUAL' AND hash_sha256=? ORDER BY id LIMIT 1",(employee_id,digest)).fetchone()
        imported=False
        if existing:
            employee_document_id=int(existing["id"]); signed_path=str(existing["caminho_storage"])
        else:
            storage_root.mkdir(parents=True,exist_ok=True)
            target=storage_root/f"TPR_{request_id}_{digest[:12]}_ASSINADO.pdf"
            if not target.exists(): target.write_bytes(payload)
            registration=documents.import_file(
                employee_id,target,document_type="ADITIVO_CONTRATUAL",actor=actor,
                document_date=str(row.get("completed_at") or now)[:10],visibility="RH",
                standardized_filename=f"TPR_{employee_id}_{str(row.get('second_expiration_date') or '').replace('-','')}_ASSINADO.pdf",
                classification_code="TPR",category="Prorrogação contratual",
                description=f"Termo de prorrogação assinado eletronicamente · Clicksign · envelope {envelope} · arquivo {provider_filename}",
                classification_confidence=100,status="ATIVO",
            )
            employee_document_id=int(registration.document_id); imported=True
            with documents.connect() as db:
                stored=db.execute("SELECT caminho_storage FROM employee_documents WHERE id=?",(employee_document_id,)).fetchone()
                signed_path=str(stored["caminho_storage"] if stored else "")
        expected=str(row.get("second_expiration_date") or "")
        if not expected: raise ValueError("A prorrogação concluída não possui o novo vencimento de 90 dias calculado.")
        with self.connect() as db:
            current=db.execute("SELECT data_2_vencimento FROM employee_admission_details WHERE employee_id=?",(employee_id,)).fetchone()
            current_value=str(current["data_2_vencimento"] or "") if current else ""
            if current_value and current_value != expected:
                raise ValueError(f"O cadastro já possui 2º vencimento divergente ({current_value}); o fechamento automático foi bloqueado.")
            if not current_value:
                db.execute("UPDATE employee_admission_details SET data_2_vencimento=? WHERE employee_id=?",(expected,employee_id))
                db.execute("INSERT INTO employee_history(employee_id,campo,valor_anterior,valor_novo,data_efetiva,documento_id,usuario,origem,criado_em) VALUES(?,?,?,?,?,?,?,?,?)",(employee_id,"data_2_vencimento","",expected,expected,employee_document_id,actor,"CLICKSIGN",now))
            if imported:
                db.execute("UPDATE employee_documents SET origem='CLICKSIGN',subtipo_documento='TPR_ASSINADO',atualizado_em=? WHERE id=?",(now,employee_document_id))
            db.execute("UPDATE rh_contract_extension_requests SET signed_employee_document_id=?,signed_document_path=?,finalized_at=?,status_sync_error='',updated_at=? WHERE id=?",(employee_document_id,signed_path,now,now,request_id))
            self._audit(db,actor,"PRORROGACAO_TPR_FINALIZADA",request_id,{"employee_id":employee_id,"document_id":employee_document_id,"envelope_id":envelope,"segundo_vencimento":expected,"documento_importado":imported})
        return employee_document_id

    def synchronize(self, request_id: int, *, actor: str, clicksign, documents=None, storage_root: Path | None = None) -> str:
        row=self._request_context(request_id); envelope=str(row.get("clicksign_envelope_id") or "")
        if not envelope: raise ValueError("Solicitação ainda não possui envelope Clicksign.")
        details=clicksign.envelope_details(int(row["company_id"]),envelope); events=clicksign.envelope_events(int(row["company_id"]),envelope,"signature_started")
        provider=str(details.get("data",{}).get("attributes",{}).get("status") or ""); viewed=bool(events.get("data"))
        if provider=="running" and documents is not None and storage_root is not None and date.today()>date.fromisoformat(str(row.get("first_expiration_date") or "")):
            self.register_non_signature(request_id,actor=actor,clicksign=clicksign,documents=documents,storage_root=storage_root,provider_details=details)
            return "NAO_ASSINADO"
        operational={"draft":"ERRO","running":"AGUARDANDO_ASSINATURA","closed":"CONCLUIDO","canceled":"CANCELADO"}.get(provider,"ERRO"); local={"CONCLUIDO":"CONCLUIDO","CANCELADO":"CANCELADO","ERRO":"ERRO"}.get(operational,"AGUARDANDO_ASSINATURA"); now=_now()
        with self.connect() as db:
            db.execute("UPDATE rh_contract_extension_requests SET status=?,operational_status=?,provider_status=?,provider_response_json=?,notification_status=CASE WHEN ? THEN 'VISUALIZADA_PELO_SIGNATARIO' ELSE notification_status END,completed_at=CASE WHEN ?='CONCLUIDO' AND completed_at='' THEN ? ELSE completed_at END,attempt_count=CASE WHEN attempt_count=0 AND trim(clicksign_envelope_id)<>'' THEN 1 ELSE attempt_count END,last_attempt_at=CASE WHEN last_attempt_at='' AND sent_at<>'' THEN sent_at ELSE last_attempt_at END,last_status_sync_at=?,status_sync_error='',updated_at=? WHERE id=?",(local,operational,provider,json.dumps({"envelope":details,"signature_started":events},ensure_ascii=False)[:10000],viewed,operational,now,now,now,request_id)); self._audit(db,actor,"CLICKSIGN_TPR_SINCRONIZADO",request_id,{"provider_status":provider,"visualizada":viewed})
        if operational=="CONCLUIDO" and documents is not None and storage_root is not None:
            try:
                self.finalize_completed(request_id,actor=actor,clicksign=clicksign,documents=documents,storage_root=storage_root)
            except Exception as exc:
                with self.connect() as db:
                    db.execute("UPDATE rh_contract_extension_requests SET status_sync_error=?,updated_at=? WHERE id=?",(str(exc)[:900],_now(),request_id))
                    self._audit(db,actor,"PRORROGACAO_TPR_FINALIZACAO_ERRO",request_id,{"reason":str(exc)[:300]})
                raise
        return operational

    def synchronize_all(self, company_id: int, *, actor: str, clicksign, documents, storage_root: Path) -> dict[str,int]:
        with self.connect() as db:
            request_ids=[int(row[0]) for row in db.execute("""SELECT id FROM rh_contract_extension_requests
                WHERE company_id=? AND trim(clicksign_envelope_id)<>'' AND (
                    operational_status IN ('PENDENTE_ENVIO','PREVIA_VALIDADA','ENVIANDO','ENVIADO','AGUARDANDO_ASSINATURA','ASSINADO','ERRO') OR
                    (operational_status='CONCLUIDO' AND (finalized_at='' OR signed_employee_document_id IS NULL))
                ) ORDER BY id""",(company_id,))]
        result={"consultados":0,"concluidos":0,"arquivados":0,"erros":0}
        for request_id in request_ids:
            try:
                status=self.synchronize(request_id,actor=actor,clicksign=clicksign,documents=documents,storage_root=storage_root)
                result["consultados"]+=1
                if status=="CONCLUIDO": result["concluidos"]+=1
                with self.connect() as db:
                    finalized=db.execute("SELECT finalized_at FROM rh_contract_extension_requests WHERE id=?",(request_id,)).fetchone()
                if finalized and finalized[0]: result["arquivados"]+=1
            except Exception:
                result["erros"]+=1
        return result

    @staticmethod
    def _deep_value(payload: object, keys: set[str]) -> str:
        if isinstance(payload,dict):
            for key,value in payload.items():
                if key.lower() in keys and isinstance(value,(str,int)): return str(value)
            for value in payload.values():
                found=ContractExtensionService._deep_value(value,keys)
                if found: return found
        elif isinstance(payload,list):
            for value in payload:
                found=ContractExtensionService._deep_value(value,keys)
                if found: return found
        return ""

    def process_webhook(self, company_id: int, raw: bytes, content_hmac: str, *, secret: str) -> tuple[bool,str]:
        if not secret: raise PermissionError("Webhook Clicksign sem segredo configurado.")
        supplied=str(content_hmac or "").strip().lower(); supplied=supplied.split("=",1)[1] if supplied.startswith("sha256=") else supplied
        expected=hmac.new(secret.encode("utf-8"),raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied,expected): raise PermissionError("Assinatura HMAC do webhook inválida.")
        payload=json.loads(raw.decode("utf-8")); envelope=self._deep_value(payload,{"envelope_id","envelope_key"})
        if not envelope:
            relationships=payload.get("data",{}).get("relationships",{}) if isinstance(payload,dict) else {}; envelope=str(relationships.get("envelope",{}).get("data",{}).get("id") or "")
        event_name=self._deep_value(payload,{"event","event_name","name"}) or str(payload.get("data",{}).get("type") or "")
        provider_status=self._deep_value(payload,{"status"}); event_key=self._deep_value(payload,{"event_id","id"}) or hashlib.sha256(raw).hexdigest(); now=_now()
        combined=(event_name+" "+provider_status).lower()
        viewed="signature_started" in combined
        operational=""
        if any(word in combined for word in ("refus","declin")): operational="RECUSADO"
        elif any(word in combined for word in ("cancel","canceled")): operational="CANCELADO"
        elif any(word in combined for word in ("closed","complete","finaliz")): operational="CONCLUIDO"
        elif any(word in combined for word in ("signed","assin")): operational="ASSINADO"
        elif any(word in combined for word in ("running","activat")): operational="AGUARDANDO_ASSINATURA"
        with self.connect() as db:
            cursor=db.execute("""INSERT OR IGNORE INTO rh_clicksign_webhook_events(company_id,event_key,event_name,envelope_id,payload_json,received_at)
                                 VALUES(?,?,?,?,?,?)""",(company_id,event_key,event_name,envelope,json.dumps(payload,ensure_ascii=False)[:50000],now))
            if not cursor.rowcount: return False,"DUPLICADO"
            request=db.execute("SELECT id,operational_status FROM rh_contract_extension_requests WHERE company_id=? AND clicksign_envelope_id=?",(company_id,envelope)).fetchone()
            result="SEM_SOLICITACAO"
            if request and viewed:
                db.execute("UPDATE rh_contract_extension_requests SET notification_status='VISUALIZADA_PELO_SIGNATARIO',provider_response_json=?,updated_at=? WHERE id=?",(json.dumps(payload,ensure_ascii=False)[:10000],now,request["id"])); result="VISUALIZADA"
            if request and operational:
                current=str(request["operational_status"] or "")
                if current not in {"CONCLUIDO","CANCELADO","RECUSADO","NAO_ASSINADO"}:
                    local={"CONCLUIDO":"CONCLUIDO","CANCELADO":"CANCELADO","RECUSADO":"CANCELADO","ASSINADO":"ASSINADO"}.get(operational,"AGUARDANDO_ASSINATURA")
                    db.execute("""UPDATE rh_contract_extension_requests SET status=?,operational_status=?,provider_status=?,provider_response_json=?,completed_at=CASE WHEN ?='CONCLUIDO' THEN ? ELSE completed_at END,updated_at=? WHERE id=?""",
                               (local,operational,provider_status or event_name,json.dumps(payload,ensure_ascii=False)[:10000],operational,now,now,request["id"])); result="ATUALIZADO"
                else: result="FINAL_PRESERVADO"
            db.execute("UPDATE rh_clicksign_webhook_events SET processed_at=?,result=? WHERE company_id=? AND event_key=?",(now,result,company_id,event_key))
        return True,result

    @staticmethod
    def _audit(db: sqlite3.Connection, actor: str, action: str, request_id: int, details: dict[str, Any]) -> None:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_logs'").fetchone():
            return
        db.execute(
            """INSERT INTO audit_logs(username,action,endpoint,source,occurred_at,result,external_identifier,details_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (actor, action, "/prorrogacoes", "RH", _now(), "SUCCESS", str(request_id), json.dumps(details, ensure_ascii=False)),
        )
