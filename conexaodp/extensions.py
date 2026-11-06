"""Fila de prorrogações de contratos temporários recebidos da fonte de RH."""

from __future__ import annotations

from datetime import date, timedelta
from datetime import datetime
import textwrap
import uuid


def _days_until(value: str, today: date) -> int | None:
    try:
        return (date.fromisoformat(value) - today).days
    except (TypeError, ValueError):
        return None


def _pdf_text(value: str) -> str:
    encoded = value.encode("cp1252", "replace")
    return "".join(f"\\{char:03o}" if char < 32 or char > 126 else
                   "\\" + chr(char) if char in (40, 41, 92) else chr(char)
                   for char in encoded)


def _display_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return "Pendente no SIB"


def _value(value, fallback="Pendente no SIB") -> str:
    value = str(value or "").strip()
    return value if value else fallback


def _format_document(value: str, kind: str) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if kind == "CPF" and len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if kind == "CNPJ" and len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return _value(value)


def _number_words_pt(value: int) -> str:
    units = ("zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove")
    teens = ("dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove")
    tens = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa")
    hundreds = ("", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos")
    if value < 0 or value > 999:
        return str(value)
    if value < 10:
        return units[value]
    if value < 20:
        return teens[value - 10]
    if value < 100:
        return tens[value // 10] + (f" e {units[value % 10]}" if value % 10 else "")
    if value == 100:
        return "cem"
    remainder = value % 100
    base = hundreds[value // 100]
    return base + (f" e {_number_words_pt(remainder)}" if remainder else "")


def _wrapped(value: str, width: int) -> list[str]:
    return textwrap.wrap(str(value or ""), width=width, break_long_words=True,
                         break_on_hyphens=False) or [""]


def _representative_data(db, contract, signer_name: str) -> dict:
    base_name, _, role_hint = signer_name.partition(" · ")
    result = {"name": base_name or signer_name, "cpf": "", "role": role_hint,
              "channel": "", "order": "1"}
    company_key = str(contract.get("company_key") or "")
    if not company_key:
        return result
    for row in db.execute("""SELECT name,role,cpf,channel FROM local_clicksign_signers
        WHERE company_key=? AND active=1 ORDER BY signer_order,id""", (company_key,)):
        if str(row["name"] or "").strip().casefold() == result["name"].strip().casefold():
            result.update({"name": row["name"], "role": row["role"] or role_hint,
                           "cpf": row["cpf"], "channel": row["channel"]})
            break
    return result


def draft_preview_pdf(contract, signer_name: str, channel: str,
                      representative: dict | None = None) -> bytes:
    """Render the TPR-39 layout as a local draft; never a final contract."""
    pending = "Pendente no SIB"
    representative = representative or {"name": signer_name, "cpf": "", "role": "",
                                         "channel": "", "order": "1"}
    first_due = _display_date(contract.get("first_due_date"))
    final_due = _display_date(contract.get("second_due_date"))
    try:
        extra_days = (date.fromisoformat(contract["second_due_date"]) -
                      date.fromisoformat(contract["first_due_date"])).days
        if extra_days <= 0:
            extra_days = None
    except (TypeError, ValueError, KeyError):
        extra_days = None
    term = (f"{extra_days} ({_number_words_pt(extra_days)}) dias corridos"
            if extra_days is not None else pending)
    employer_name = _value(contract.get("employer_name"))
    employer_cnpj = _format_document(contract.get("employer_cnpj", ""), "CNPJ")
    employer_address = _value(contract.get("employer_address"))
    person_name = _value(contract.get("person_name"))
    person_cpf = _format_document(contract.get("cpf", ""), "CPF")
    person_function = _value(contract.get("worker_function"))
    registration = _value(contract.get("registration"))
    admission = _display_date(contract.get("admission_date"))
    tomadora_name = _value(contract.get("tomadora_name") or contract.get("company_name"))
    tomadora_cnpj = _format_document(contract.get("tomadora_cnpj", ""), "CNPJ")
    tomadora_address = _value(contract.get("tomadora_address"))
    work_location = _value(contract.get("work_location"))
    demand = _value(contract.get("complementary_demand"))
    representative_name = _value(representative.get("name"), "Pendente no cadastro Clicksign")
    representative_cpf = _format_document(representative.get("cpf", ""), "CPF")
    representative_role = _value(representative.get("role"), "Pendente no cadastro Clicksign")
    representative_channel = _value(representative.get("channel"), "Pendente no cadastro Clicksign")

    fields = [
        ("1. IDENTIFICAÇÃO DAS PARTES", [
            ("Empregadora", employer_name), ("CNPJ", employer_cnpj),
            ("Endereço", employer_address), ("Trabalhador(a)", person_name),
            ("CPF", person_cpf), ("Função", person_function),
            ("Matrícula / ID Folha", registration), ("Admissão", admission),
        ]),
        ("2. CONTRATO E PRESTAÇÃO DE SERVIÇOS", [
            ("Vínculo", _value(contract.get("contract_type"))),
            ("1º vencimento", first_due), ("Prazo adicional", term),
            ("Nova data final", final_due), ("Tomadora", tomadora_name),
            ("CNPJ da tomadora", tomadora_cnpj), ("Local da prestação", work_location),
            ("Demanda complementar", demand),
        ]),
        ("3. REPRESENTANTE DA EMPREGADORA", [
            ("Nome", representative_name), ("CPF", representative_cpf),
            ("Cargo / função", representative_role),
            ("Canal de assinatura", representative_channel),
            ("Ordem", "1 - remetente, assina primeiro"),
        ]),
    ]
    commands: list[str] = []
    def rect(x, y, width, height, fill=(1, 1, 1), stroke=(0.78, 0.83, 0.88)):
        commands.extend([f"{fill[0]} {fill[1]} {fill[2]} rg", f"{stroke[0]} {stroke[1]} {stroke[2]} RG",
                         "0.45 w", f"{x} {y-height} {width} {height} re B"])
    def draw_text(x, baseline, value, font="F1", size=8.5, color=(0.08, 0.16, 0.23)):
        commands.extend(["BT", f"{color[0]} {color[1]} {color[2]} rg", f"/{font} {size} Tf",
                         f"{x} {baseline} Td", f"({_pdf_text(value)}) Tj", "ET"])
    def draw_section(title, rows, y):
        draw_text(52, y, title, "F2", 10.2, (0.1, 0.25, 0.42))
        y -= 8
        label_x, table_width, label_width = 70, 455, 142
        for label, value in rows:
            label_lines = _wrapped(label, 25)
            value_lines = _wrapped(value, 66)
            row_height = max(18, max(len(label_lines), len(value_lines)) * 10 + 6)
            rect(label_x, y, label_width, row_height, (0.94, 0.96, 0.98))
            rect(label_x + label_width, y, table_width - label_width, row_height)
            baseline = y - 12
            for line in label_lines:
                draw_text(label_x + 5, baseline, line, "F2", 8.2, (0.12, 0.17, 0.22))
                baseline -= 10
            baseline = y - 12
            for line in value_lines:
                draw_text(label_x + label_width + 5, baseline, line, "F1", 8.2)
                baseline -= 10
            y -= row_height
        # Keep a clear gap between the last table border and the next section title.
        return y - 15

    draw_text(52, 807, "RASCUNHO TPR-39 · CONFERÊNCIA INTERNA · NÃO ENVIAR COMO CONTRATO", "F2", 8.3, (0.65, 0.14, 0.17))
    draw_text(52, 788, "TERMO DE PRORROGAÇÃO DE CONTRATO DE TRABALHO TEMPORÁRIO", "F2", 12.1, (0.08, 0.19, 0.31))
    draw_text(52, 773, "Campos cadastrais e do vínculo devem ser preenchidos pela integração SIB.", "F1", 8.1, (0.33, 0.42, 0.52))
    y = 755
    for title, rows in fields:
        y = draw_section(title, rows, y)

    days_text = term
    paragraph = (f"As partes ajustam a prorrogação do contrato temporário por {days_text}, "
                 f"a contar do primeiro vencimento em {first_due}, fixando a nova data final em {final_due}.")
    paragraphs = [paragraph,
        "Permanecem inalteradas e ratificadas todas as demais cláusulas e condições do contrato original que não conflitarem com este termo.",
        "A manifestação de vontade será formalizada por assinatura eletrônica na Clicksign. Data, hora, evidências de autenticação, integridade, signatários e identificação definitiva do envelope constarão da trilha de auditoria emitida pela plataforma."]
    for paragraph in paragraphs:
        for line in _wrapped(paragraph, 112):
            draw_text(52, y, line, "F1", 8.1, (0.1, 0.13, 0.16))
            y -= 11
        y -= 3
    y -= 9
    draw_text(52, y, "EMPREGADORA / REPRESENTANTE: ______________________________________________", "F1", 8.3)
    y -= 24
    draw_text(52, y, "TRABALHADOR(A): __________________________________________________________", "F1", 8.3)
    y -= 22
    draw_text(52, y, f"Teste local · canal selecionado para o destinatário: {channel} · ordem de assinatura: remetente antes do destinatário.", "F1", 7.2, (0.33, 0.42, 0.52))
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def get_extension(db, source_system: str, source_id: str, first_due_date: str):
    contract = db.execute("""SELECT * FROM local_temporary_contracts
        WHERE source_system=? AND source_id=? AND first_due_date=? AND active=1""",
        (source_system, source_id, first_due_date)).fetchone()
    if not contract:
        raise ValueError("Contrato temporário não encontrado ou alterado na origem.")
    contract = dict(contract)
    # Keep the draft tied to the imported SIB snapshot. Do not silently fill
    # contract fields from V2 or an unrelated local company profile.
    contract["tomadora_name"] = str(contract.get("tomadora_name") or contract.get("company_name") or "")
    extension = db.execute("""SELECT * FROM local_temporary_extensions
        WHERE source_system=? AND source_id=? AND first_due_date=?""",
        (source_system, source_id, first_due_date)).fetchone()
    return contract, extension


def simulate_action(db, *, source_system: str, source_id: str, first_due_date: str,
                    action: str, signer_name: str = "", channel: str = "") -> str:
    contract, extension = get_extension(db, source_system, source_id, first_due_date)
    if source_system not in ("TESTE_LOCAL", "REGISTRY") or (source_system == "TESTE_LOCAL" and not contract["is_test"]):
        raise ValueError("Este fluxo simulado está disponível apenas para registros locais autorizados.")
    if _days_until(contract["first_due_date"], date.today()) is None:
        raise ValueError("O primeiro vencimento precisa ser uma data válida.")
    status = extension["status"] if extension else "PENDING"
    if action == "prepare":
        if status not in ("PENDING", "PREPARED") or (extension and extension["envelope_id"]):
            raise ValueError("Já existe um pacote nesta simulação. A prévia não pode substituí-lo.")
        if signer_name != "Caio Caobianco · Assistente DP":
            raise ValueError("Selecione o representante de demonstração.")
        if channel not in ("WHATSAPP", "EMAIL"):
            raise ValueError("Selecione um canal para a pessoa.")
        if channel == "WHATSAPP" and not contract["whatsapp"]:
            raise ValueError("A pessoa precisa ter WhatsApp informado.")
        if channel == "EMAIL" and not contract["email"]:
            raise ValueError("A pessoa precisa ter e-mail informado.")
        representative = _representative_data(db, contract, signer_name)
        pdf = draft_preview_pdf(contract, signer_name,
                                ("WhatsApp" if channel == "WHATSAPP" else "E-mail") + "; documento concluído por e-mail",
                                representative)
        db.execute("""INSERT INTO local_temporary_extensions
            (source_system,source_id,first_due_date,status,signer_name,worker_channel,preview_pdf,prepared_at,updated_at)
            VALUES (?,?,?,'PREPARED',?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(source_system,source_id,first_due_date) DO UPDATE SET
                status='PREPARED',signer_name=excluded.signer_name,
                worker_channel=excluded.worker_channel,preview_pdf=excluded.preview_pdf,
                prepared_at=excluded.prepared_at,updated_at=CURRENT_TIMESTAMP""",
            (source_system, source_id, first_due_date, signer_name, channel, pdf, datetime.now().isoformat(timespec="seconds")))
        return "Prévia de simulação gerada. Confira o PDF antes de confirmar."
    if action == "send":
        if status != "PREPARED" or not extension or not extension["preview_pdf"]:
            raise ValueError("Gere e confira a prévia antes de confirmar.")
        if signer_name != extension["signer_name"] or channel != extension["worker_channel"]:
            raise ValueError("O representante ou canal mudou. Gere e confira uma nova prévia.")
        reference = "SIM-" + uuid.uuid4().hex[:12].upper()
        now = datetime.now().isoformat(timespec="seconds")
        changed = db.execute("""UPDATE local_temporary_extensions SET status='AWAITING_SIGNATURE',
            envelope_id=?,attempts=attempts+1,last_sent_at=?,
            sender_signed_at='',worker_signed_at='',recipient_invited_at='',
            provider_status='SIMULADO_AGUARDANDO_REMETENTE',updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='PREPARED'""", (reference, now, extension["id"]))
        if changed.rowcount != 1:
            raise ValueError("Este pacote já mudou de estado. Atualize a página.")
        verb = "reenviado" if extension["attempts"] > 0 else "iniciado"
        return f"Pacote simulado {verb} para o remetente. O destinatário será liberado somente após a assinatura dele. Nenhuma mensagem real foi enviada."
    if action == "return_to_send":
        if status != "AWAITING_SIGNATURE" or not extension:
            raise ValueError("Não há pacote aguardando assinatura para voltar à etapa de envio.")
        changed = db.execute("""UPDATE local_temporary_extensions SET status='PREPARED',envelope_id='',
            sender_signed_at='',worker_signed_at='',recipient_invited_at='',last_notice_at='',
            last_notice_target='',provider_status='SIMULADO_PREVIA_VALIDADA',
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='AWAITING_SIGNATURE'""",
            (extension["id"],))
        if changed.rowcount != 1:
            raise ValueError("Este pacote já mudou de estado. Atualize a página.")
        return "Simulação voltou para a etapa de envio. A prévia e o histórico de tentativas foram preservados; um novo envio reinicia pelo remetente."
    if action == "remind":
        if status != "AWAITING_SIGNATURE":
            raise ValueError("O lembrete só está disponível enquanto aguarda assinatura.")
        target = "destinatário" if extension["sender_signed_at"] else "remetente"
        db.execute("""UPDATE local_temporary_extensions SET last_notice_at=?,last_notice_target=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), target, extension["id"]))
        return f"Lembrete simulado para o {target}. Nenhuma mensagem foi enviada."
    if action in ("sign_sender", "sign_worker"):
        if status != "AWAITING_SIGNATURE":
            raise ValueError("Este pacote não está aguardando assinatura.")
        now = datetime.now().isoformat(timespec="seconds")
        if action == "sign_sender":
            if extension["sender_signed_at"]:
                return "A assinatura simulada do remetente já foi registrada."
            if extension["worker_signed_at"]:
                raise ValueError("Ordem de assinatura inconsistente; atualize a página.")
            db.execute("""UPDATE local_temporary_extensions SET sender_signed_at=?,recipient_invited_at=?,
                provider_status='SIMULADO_AGUARDANDO_DESTINATARIO',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND sender_signed_at=''""", (now, now, extension["id"]))
            return "Remetente assinou na simulação. A etapa do destinatário foi liberada agora; nenhuma mensagem real foi enviada."
        if not extension["sender_signed_at"] or not extension["recipient_invited_at"]:
            raise ValueError("O destinatário só pode assinar depois do remetente.")
        if extension["worker_signed_at"]:
            return "A assinatura simulada do destinatário já foi registrada."
        db.execute("""UPDATE local_temporary_extensions SET worker_signed_at=?,status='SIGNED',
            provider_status='SIMULADO_ASSINADO',updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND sender_signed_at<>'' AND recipient_invited_at<>'' AND worker_signed_at=''""",
            (now, extension["id"]))
        return "Destinatário assinou na simulação. As duas assinaturas foram concluídas."
    if action == "cancel":
        if status not in ("AWAITING_SIGNATURE", "PREPARED"):
            raise ValueError("Não há pacote aberto para cancelar.")
        db.execute("UPDATE local_temporary_extensions SET status='CANCELLED',provider_status='SIMULADO_CANCELADO',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (extension["id"],))
        return "Pacote de simulação cancelado."
    if action == "reset":
        if not extension:
            return "A simulação já está no início."
        db.execute("DELETE FROM local_temporary_extensions WHERE id=?", (extension["id"],))
        return "Simulação reiniciada."
    raise ValueError("Ação desconhecida.")


def queue_data(db, today: date, user=None) -> dict:
    """Use as datas da fonte; não presuma 180/270 dias a partir da admissão."""
    from .permissions import allowed_company, can, scope
    if user is not None and not can(user, 'extensions.view'):
        raise PermissionError('Seu perfil não permite consultar prorrogações.')
    can_manage = user is not None and can(user, 'extensions.manage')
    rows = db.execute("""
        SELECT c.source_system,c.source_id,c.person_name,c.registration,c.company_key,
               c.company_name,c.contract_type,c.admission_date,c.first_due_date,
               c.second_due_date,c.cpf,c.email,c.whatsapp,c.is_test,c.source_updated_at,c.active,
               c.source_state,c.source_message,c.proposed_due_date,
               e.status extension_status,e.envelope_id,e.attempts,e.last_sent_at,
               e.provider_status,e.signer_name,e.worker_channel,e.delivery_mode,e.delivery_environment,
               e.sender_channel,e.sender_name,e.sender_cpf,e.sender_email,e.sender_phone,e.recipient_email,
               e.prepared_at,e.last_notice_at,
               e.sender_signed_at,e.worker_signed_at,e.recipient_invited_at,e.last_notice_target,
               CASE WHEN e.preview_pdf IS NOT NULL THEN 1 ELSE 0 END has_preview
        FROM local_temporary_contracts c
        LEFT JOIN local_temporary_extensions e
          ON e.source_system=c.source_system AND e.source_id=c.source_id
         AND e.first_due_date=c.first_due_date
        WHERE c.active=1 OR e.status IN ('SIGNED','AWAITING_SIGNATURE','PREPARED','ERROR')
        ORDER BY CASE WHEN c.first_due_date='' THEN 1 ELSE 0 END,
                 c.first_due_date,c.person_name COLLATE NOCASE
    """).fetchall()
    items = []
    for row in rows:
        # Apply population scope before counts, companies, history or signers.
        if user is not None and not allowed_company(db, user, company_key=row['company_key'], name=row['company_name']):
            continue
        first = _days_until(row["first_due_date"], today)
        second = _days_until(row["second_due_date"], today)
        status = row["extension_status"] or "PENDING"
        if first is None or (second is not None and second < first):
            status = "INCOMPLETE"
        eligible = True
        message = row['source_message']
        if row['source_system'] == 'SIB':
            eligible = bool(row['active'] and 'TEMPOR' in row['contract_type'].upper() and first is not None and 0 <= first <= 30 and not row['second_due_date'] and row['source_state'] != 'REVIEW')
            if row['source_state'] == 'REVIEW' or not row['active']:
                status = 'SOURCE_REVIEW'
            elif row['second_due_date'] and not row['extension_status']:
                status = 'SOURCE_EXTENDED'
            elif first is not None and first < 0 and status != 'SIGNED':
                status = 'EXPIRED'
                message = 'Prazo ultrapassado. Preparação retroativa bloqueada, conforme fluxo SIC.'
            elif not eligible and status == 'PENDING':
                message = message or 'A preparação fica disponível nos 30 dias anteriores ao primeiro vencimento.'
            # A later source change cannot erase a provider-confirmed signature.
            # Origin eligibility still controls preparation, while the completed
            # package and its archived document remain visible in history.
            if row['extension_status'] == 'SIGNED':
                status = 'SIGNED'
        items.append({
            "key": f'{row["source_system"]}:{row["source_id"]}',
            "sourceSystem": row["source_system"],
            "employmentKey": 'sib:' + ':'.join(row['source_id'].split(':')[1:4]) if row['source_system'] == 'SIB' else '',
            "sourceId": row["source_id"],
            "name": row["person_name"],
            "registration": row["registration"],
            "companyKey": row["company_key"],
            "companyName": row["company_name"],
            "contractType": row["contract_type"],
            "admissionDate": row["admission_date"],
            "firstDueDate": row["first_due_date"],
            "secondDueDate": row["second_due_date"],
            "daysToFirst": first,
            "daysToSecond": second,
            "status": status,
            "hasCpf": bool(row["cpf"]),
            "hasEmail": bool(row["email"]),
            "hasWhatsapp": bool(row["whatsapp"]),
            "isTest": bool(row["is_test"]),
            "envelopeId": row["envelope_id"] or "",
            "attempts": row["attempts"] or 0,
            "lastSentAt": row["last_sent_at"] or "",
            "providerStatus": row["provider_status"] or "",
            "signerName": row["signer_name"] or "",
            "workerChannel": row["worker_channel"] or "",
            "deliveryMode": row["delivery_mode"] or "SIMULATED",
            "deliveryEnvironment": row["delivery_environment"] or "",
            "senderChannel": row["sender_channel"] or "",
            "senderName": row["sender_name"] or "",
            "senderCpf": row["sender_cpf"] or "",
            "senderEmail": row["sender_email"] or "",
            "senderPhone": row["sender_phone"] or "",
            "recipientEmail": row["recipient_email"] or "",
            "preparedAt": row["prepared_at"] or "",
            "lastNoticeAt": row["last_notice_at"] or "",
            "senderSignedAt": row["sender_signed_at"] or "",
            "workerSignedAt": row["worker_signed_at"] or "",
            "recipientInvitedAt": row["recipient_invited_at"] or "",
            "lastNoticeTarget": row["last_notice_target"] or "",
            "hasPreview": bool(row["has_preview"]),
            "sourceUpdatedAt": row["source_updated_at"],
            "eligible": eligible, "sourceState": row['source_state'], "sourceMessage": message,
            "proposedDueDate": row['proposed_due_date'],
            "history": [{'firstDueDate': previous['first_due_date'], 'status': previous['status'], 'providerStatus': previous['provider_status'], 'envelopeId': previous['envelope_id'], 'hasPreview': bool(previous['preview_pdf'])} for previous in db.execute('SELECT first_due_date,status,provider_status,envelope_id,preview_pdf FROM local_temporary_extensions WHERE source_system=? AND source_id=? ORDER BY id DESC LIMIT 20', (row['source_system'],row['source_id']))] if row['source_system'] == 'SIB' else [],
        })
        if user is not None and not can_manage:
            for field in ('senderCpf', 'senderEmail', 'senderPhone', 'recipientEmail'):
                items[-1].pop(field, None)
    # A canceled signature envelope still represents a contract that needs
    # processing; keep it in the due counts and allow a new package from this row.
    actionable = [item for item in items if item["status"] not in ("SIGNED", "INCOMPLETE", "SOURCE_EXTENDED", "SOURCE_REVIEW")]
    awaiting = [item for item in items if item["status"] == "AWAITING_SIGNATURE"]
    from .sib_operations import summary
    result = {
        "sync": summary(db) if user is None or scope(user) == 'ALL' else {},
        "canManage": can_manage,
        "canSimulate": can_manage,
        "unclassifiedCodes": [str(row[0]) for row in db.execute("SELECT DISTINCT json_extract(snapshot_json,'$.contract_code') FROM local_sib_operation_snapshots WHERE json_extract(snapshot_json,'$.contract_type')='' AND json_extract(snapshot_json,'$.termination_date')='' AND json_extract(snapshot_json,'$.contract_code')<>'' ORDER BY 1")] if user is not None and can(user, 'integrations.manage') else [],
        "today": today.isoformat(),
        "items": items,
        "companies": sorted({item["companyName"] for item in items if item["companyName"]}),
        "summary": {
            "overdue": sum(item["daysToFirst"] < 0 for item in actionable),
            "week": sum(0 <= item["daysToFirst"] <= 7 for item in actionable),
            "month": sum(0 <= item["daysToFirst"] <= 30 for item in actionable),
            "pending": sum(item["status"] in ("PENDING", "PREPARED", "ERROR", "CANCELLED") for item in actionable),
            "awaiting": len(awaiting),
            "finalMonth": sum(item["daysToSecond"] is not None and 0 <= item["daysToSecond"] <= 30 for item in items),
            "incomplete": sum(item["status"] == "INCOMPLETE" for item in items),
        },
    }
    # The optional module's migration runs at application initialization. This
    # narrow schema guard keeps older offline snapshots readable during upgrades.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_extension_workflows'").fetchone():
        from .extension_workflow import enrich_queue
        enrich_queue(db, result, can_manage=can_manage)
    return result


def prepare_sib_preview(db, source_id, first_due_date, signer_name, channel):
    """Prepare an internal TPR draft without inventing an effective extension or signatures."""
    contract, extension = get_extension(db, 'SIB', source_id, first_due_date)
    source = db.execute("SELECT source_id FROM local_sib_operation_snapshots WHERE json_extract(snapshot_json,'$.identity')=?", (source_id,)).fetchone()
    if not source or db.execute('SELECT 1 FROM local_sib_operation_jobs WHERE source_id=?', (source[0],)).fetchone():
        raise ValueError('A ficha SIB tem uma atualização pendente. Aguarde a conferência antes de preparar o rascunho.')
    days = _days_until(first_due_date, date.today())
    if contract['source_state'] == 'REVIEW' or 'TEMPOR' not in contract['contract_type'].upper() or contract['second_due_date'] or days is None or not 0 <= days <= 30:
        raise ValueError('Somente temporários ativos, sem segundo vencimento e na janela de 30 dias podem preparar o TPR.')
    if extension and (extension['envelope_id'] or extension['status'] == 'SIGNED'):
        raise ValueError('Este vínculo já possui pacote ou assinatura. Confira o histórico antes de criar outro.')
    from .clicksign import valid_cpf
    if not valid_cpf(contract['cpf']):
        raise ValueError('O CPF da origem precisa ser válido antes de preparar o termo.')
    if channel not in ('EMAIL','WHATSAPP') or not (contract['email'] if channel == 'EMAIL' else contract['whatsapp']):
        raise ValueError('Escolha um canal com contato cadastrado na origem.')
    if len(signer_name.strip().split()) < 2:
        raise ValueError('Informe o nome completo do representante para a conferência do rascunho.')
    proposal = (date.fromisoformat(first_due_date)+timedelta(days=90)).isoformat()
    draft = {**contract, 'second_due_date': proposal}
    pdf = draft_preview_pdf(draft, signer_name, channel)
    db.execute("""INSERT INTO local_temporary_extensions(source_system,source_id,first_due_date,status,provider_status,signer_name,worker_channel,preview_pdf,prepared_at)
       VALUES('SIB',?,?,'PREPARED','SIB_RASCUNHO_INTERNO',?,?,?,CURRENT_TIMESTAMP)
       ON CONFLICT(source_system,source_id,first_due_date) DO UPDATE SET status='PREPARED',provider_status='SIB_RASCUNHO_INTERNO',signer_name=excluded.signer_name,worker_channel=excluded.worker_channel,preview_pdf=excluded.preview_pdf,prepared_at=CURRENT_TIMESTAMP""", (source_id,first_due_date,signer_name,channel,pdf))
    return 'Rascunho interno preparado. A proposta de 90 dias não foi registrada como prorrogação efetiva nem enviada para assinatura.'


def sync_registry_employment(db, employment_id: int) -> None:
    """Espelha vínculo temporário local na mesma fila usada por Prorrogações."""
    row = db.execute(
        """SELECT e.*,p.name,p.cpf,p.email,p.whatsapp,p.is_test person_is_test
           FROM registry_employments e
           JOIN registry_people p ON p.id=CAST(substr(e.person_key,3) AS INTEGER)
           WHERE e.id=? AND e.person_key LIKE 'm:%'""", (int(employment_id),)
    ).fetchone()
    if (not row or "TEMPOR" not in str(row["contract_type"] or "").upper()
            or not row["first_due_date"] or not row["second_due_date"]):
        # A corrected source must retire its old queue entry, preserving envelopes
        # and history rather than leaving an actionable temporary contract behind.
        db.execute("UPDATE local_temporary_contracts SET active=0,source_updated_at=CURRENT_TIMESTAMP WHERE source_system='REGISTRY' AND source_id=?",
                   (str(employment_id),))
        return
    from .registry import employment_activity
    active, _ = employment_activity(row["admission_date"], row["termination_date"])
    db.execute(
        """INSERT INTO local_temporary_contracts(
               source_system,source_id,person_name,registration,company_key,company_name,contract_type,
               active,admission_date,first_due_date,second_due_date,cpf,email,whatsapp,is_test,source_updated_at
           ) VALUES('REGISTRY',?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(source_system,source_id) DO UPDATE SET person_name=excluded.person_name,
               registration=excluded.registration,company_name=excluded.company_name,contract_type=excluded.contract_type,
               active=excluded.active,admission_date=excluded.admission_date,first_due_date=excluded.first_due_date,
               second_due_date=excluded.second_due_date,cpf=excluded.cpf,email=excluded.email,whatsapp=excluded.whatsapp,
               is_test=excluded.is_test,source_updated_at=CURRENT_TIMESTAMP""",
        (str(row["id"]), str(row["name"]), str(row["registration"]), "", str(row["company"]),
         str(row["contract_type"]), int(active is True), str(row["admission_date"]),
         str(row["first_due_date"]), str(row["second_due_date"]), str(row["cpf"] or ""),
         str(row["email"] or ""), str(row["whatsapp"] or ""), int(bool(row["is_test"] or row["person_is_test"])))
    )
