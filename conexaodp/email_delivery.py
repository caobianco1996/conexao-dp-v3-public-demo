"""Envio manual de e-mail SMTP para a própria conta DP e para os casos de rescisão."""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path


TEST_ADDRESS = "demo@example.com"
DEFAULTS: dict[str, object] = {
    "connectionName": "SMTP Demonstração",
    "smtpHost": "",
    "smtpPort": 587,
    "security": "STARTTLS",
    "username": "",
    "senderEmail": TEST_ADDRESS,
    "active": False,
    "timeoutSeconds": 30,
}


class SMTPDeliveryError(RuntimeError):
    """Falha de SMTP com mensagem segura para exibir na interface."""

    def __init__(self, message: str, *, possibly_accepted: bool = False) -> None:
        super().__init__(message)
        self.possibly_accepted = possibly_accepted


def validate_settings(payload: dict[str, object]) -> dict[str, object]:
    name = str(payload.get("connectionName") or "SMTP Locaweb").strip()[:100]
    host = str(payload.get("smtpHost") or "").strip().rstrip(".")
    port_raw = payload.get("smtpPort", 465)
    security = str(payload.get("security") or "").strip().upper()
    username = str(payload.get("username") or "").strip()
    sender = str(payload.get("senderEmail") or "").strip().lower()
    try:
        port = int(port_raw)
        timeout = int(payload.get("timeoutSeconds", 30))
    except (TypeError, ValueError) as error:
        raise ValueError("Informe uma porta e um tempo limite válidos.") from error

    if not host or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("Informe um servidor SMTP válido.")
    if not 1 <= port <= 65535:
        raise ValueError("A porta SMTP deve ficar entre 1 e 65535.")
    if security not in ("SSL_TLS", "STARTTLS"):
        raise ValueError("Escolha SSL/TLS ou STARTTLS.")
    if security == "SSL_TLS" and port == 587:
        raise ValueError("A porta 587 normalmente usa STARTTLS; ajuste a segurança ou a porta.")
    if security == "STARTTLS" and port == 465:
        raise ValueError("A porta 465 normalmente usa SSL/TLS; ajuste a segurança ou a porta.")
    if not username or len(username) > 254 or any(char in username for char in "\r\n\0"):
        raise ValueError("Informe o usuário SMTP.")
    if parseaddr(sender)[1] != sender or sender != TEST_ADDRESS:
        raise ValueError(f"O remetente desta conexão deve ser {TEST_ADDRESS}.")
    if not 3 <= timeout <= 120:
        raise ValueError("O tempo limite deve ficar entre 3 e 120 segundos.")

    return {
        "connectionName": name or "SMTP Locaweb",
        "smtpHost": host,
        "smtpPort": port,
        "security": security,
        "username": username,
        "senderEmail": sender,
        "active": int(bool(payload.get("active"))),
        "timeoutSeconds": timeout,
    }


def send_test_email(settings: dict[str, object], password: str, attachment: Path) -> None:
    if not attachment.is_file():
        raise SMTPDeliveryError("O PDF fictício de teste não foi encontrado no servidor.")
    send_email(
        settings,
        password,
        TEST_ADDRESS,
        "[TESTE V3] Entrega de documentos por e-mail",
        "Este é um teste real de envio de e-mail da Central DP V3 para a própria conta.\n\n"
        "O pacote PDF anexado contém apenas documentos fictícios, marcados como teste e sem validade legal.\n"
        "Nenhuma pessoa, empresa, assinatura ou verba real está representada.\n\n"
        "Se você recebeu esta mensagem, a conexão SMTP e o envio do anexo funcionaram.",
        [{"filename": "pacote-documentos-rescisao-teste.pdf", "mime_type": "application/pdf",
          "content": attachment.read_bytes()}],
    )


def send_email(
    settings: dict[str, object], password: str, recipient: str | list[str], subject: str,
    body: str, attachments: list[dict[str, object]], *, show_recipients: bool = False,
    reply_to: str | None = None,
    allow_empty_attachments: bool = False,
) -> None:
    """Send a real SMTP message; the password exists only for this call.

    Follow-up messages may explicitly allow no attachments. Existing document
    delivery callers still require at least one attachment by default.
    """
    password = str(password or "")
    if not password or len(password) > 1024 or any(char in password for char in "\r\n\0"):
        raise ValueError("Salve uma senha SMTP válida na configuração de e-mail antes de enviar.")
    addresses = [str(item).strip().lower() for item in recipient] if isinstance(recipient, list) else [str(recipient or "").strip().lower()]
    addresses = list(dict.fromkeys(addresses))
    if (not addresses or len(addresses) > 50 or any(
            len(address) > 254 or any(char in address for char in "\r\n\0")
            or parseaddr(address)[1] != address
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address)
            for address in addresses)):
        raise ValueError("Informe endereços de e-mail de destino válidos.")
    reply_address = str(reply_to or "").strip().lower()
    if reply_address and (
        len(reply_address) > 254 or any(char in reply_address for char in "\r\n\0")
        or parseaddr(reply_address)[1] != reply_address
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", reply_address)
    ):
        raise ValueError("Informe um endereço de resposta válido.")
    if not isinstance(attachments, list) or (not attachments and not allow_empty_attachments) or len(attachments) > 20:
        raise ValueError("Selecione ao menos um documento anexado ao caso.")
    safe_subject = " ".join(str(subject or "").split())[:180]
    if not safe_subject:
        raise ValueError("Informe o assunto do e-mail.")
    message = EmailMessage()
    message["From"] = str(settings["senderEmail"])
    multiple_recipients = len(addresses) > 1
    # Keep existing delivery flows private by default. Scheduled HR reports can
    # explicitly show their configured recipients in the message header.
    message["To"] = ", ".join(addresses) if show_recipients else (
        "Undisclosed recipients:;" if multiple_recipients else addresses[0]
    )
    message["Subject"] = safe_subject
    if reply_address:
        message["Reply-To"] = reply_address
    message.set_content(str(body or "")[:20000])
    total_size = 0
    for item in attachments:
        filename = Path(str(item.get("filename") or "documento").replace("\\", "/")).name
        filename = "".join(char if char >= " " and char not in "\r\n\0" else "_" for char in filename)[:180]
        mime_type = str(item.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        content = item.get("content")
        if not filename or not isinstance(content, bytes) or not content:
            raise ValueError("Um dos documentos selecionados está vazio ou indisponível.")
        if "/" not in mime_type:
            raise ValueError("O tipo de um dos anexos é inválido.")
        maintype, subtype = mime_type.split("/", 1)
        if (not re.fullmatch(r"[a-z0-9!#$&^_.+-]+", maintype)
                or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+", subtype)):
            raise ValueError("O tipo de um dos anexos é inválido.")
        total_size += len(content)
        if total_size > 18 * 1024 * 1024:
            raise ValueError("Os anexos somam mais de 18 MB. Reduza a seleção antes de enviar.")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    host = str(settings["smtpHost"])
    port = int(settings["smtpPort"])
    timeout = int(settings["timeoutSeconds"])
    context = ssl.create_default_context()
    submitting = False
    try:
        if settings["security"] == "SSL_TLS":
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
                smtp.login(str(settings["username"]), password)
                submitting = True
                smtp.send_message(message, to_addrs=addresses if multiple_recipients else None)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(str(settings["username"]), password)
                submitting = True
                smtp.send_message(message, to_addrs=addresses if multiple_recipients else None)
    except smtplib.SMTPAuthenticationError as error:
        raise SMTPDeliveryError("A Locaweb recusou a autenticação. Confira usuário e senha SMTP.") from error
    except smtplib.SMTPRecipientsRefused as error:
        raise SMTPDeliveryError("O servidor SMTP recusou o endereço de destino.") from error
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError) as error:
        message = ("O resultado do envio SMTP é incerto. Confira a caixa de saída e o histórico antes de tentar novamente."
                   if submitting else "Não foi possível conectar ao servidor SMTP. Confira servidor, porta e segurança.")
        raise SMTPDeliveryError(message, possibly_accepted=submitting) from error
    except smtplib.SMTPException as error:
        message = ("O resultado do envio SMTP é incerto. Confira a caixa de saída e o histórico antes de tentar novamente."
                   if submitting else "O servidor SMTP não concluiu o envio. Confira a configuração antes de tentar novamente.")
        raise SMTPDeliveryError(message, possibly_accepted=submitting) from error
