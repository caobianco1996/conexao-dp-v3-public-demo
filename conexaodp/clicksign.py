"""Cliente mínimo e seguro para validar a conexão Clicksign API v3."""

from __future__ import annotations

import json
import os
import base64
import ctypes
import re
import unicodedata
from ctypes import wintypes
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener


API_ROOTS = {
    "sandbox": "https://sandbox.clicksign.com/api/v3",
    "production": "https://app.clicksign.com/api/v3",
}


def configuration(environment: str | None = None, token: str | None = None) -> dict[str, object]:
    environment = (environment if environment is not None else os.environ.get("CLICKSIGN_API_ENVIRONMENT", "sandbox")).strip().lower()
    token = (token if token is not None else os.environ.get("CLICKSIGN_API_TOKEN", "")).strip()
    environment_valid = environment in API_ROOTS
    return {
        "configured": bool(token),
        "environment": environment if environment_valid else "sandbox",
        "environmentLabel": "Sandbox" if environment == "sandbox" else "Produção" if environment == "production" else "Inválido",
        "configurationError": None if environment_valid else "CLICKSIGN_API_ENVIRONMENT deve ser sandbox ou production.",
    }


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_transform(value: bytes, *, decrypt: bool) -> bytes:
    """Protect Clicksign credentials for the current Windows user using DPAPI."""
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise RuntimeError("O armazenamento protegido de credenciais exige o Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    entropy_bytes = b"ConexaoDP.Clicksign.v1"
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    entropy = _DataBlob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                         ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
                                         wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
                                           wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source_buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy), None, None, flags, ctypes.byref(output)
        )
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "Conexao DP Clicksign", ctypes.byref(entropy), None, None, flags, ctypes.byref(output)
        )
    if not ok:
        raise OSError(ctypes.get_last_error(), "O Windows não conseguiu proteger a credencial da Clicksign.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect_secret(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) > 8192 or any(char in value for char in "\r\n\0"):
        raise ValueError("A credencial informada parece inválida.")
    return "dpapi:" + base64.b64encode(_dpapi_transform(value.encode("utf-8"), decrypt=False)).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        raise RuntimeError("A credencial salva está em um formato não reconhecido.")
    try:
        raw = base64.b64decode(value[6:], validate=True)
        return _dpapi_transform(raw, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Não foi possível abrir a credencial protegida da Clicksign.") from error


def test_connection(environment: str | None = None, token: str | None = None, timeout: int = 15) -> tuple[dict[str, object], int]:
    """Faz uma consulta GET sem criar ou alterar envelopes."""
    config = configuration(environment, token)
    if config["configurationError"]:
        return {"connected": False, "message": config["configurationError"]}, 400
    token = (token if token is not None else os.environ.get("CLICKSIGN_API_TOKEN", "")).strip()
    if not token:
        return {"connected": False, "message": "O Access Token da Clicksign não está configurado no servidor."}, 400
    if len(token) > 4096 or any(char in token for char in "\r\n"):
        return {"connected": False, "message": "O Access Token configurado parece inválido."}, 400

    root = API_ROOTS[str(config["environment"])]
    url = f"{root}/envelopes?{urlencode({'page[size]': '1'})}"
    request = Request(url, headers={
        "Authorization": token,
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }, method="GET")
    try:
        with urlopen(request, timeout=max(3, min(120, int(timeout)))) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(payload, dict):
            return {"connected": False, "message": "A Clicksign respondeu em um formato inesperado."}, 502
        data = payload.get("data")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        count = meta.get("record_count")
        if count is None:
            count = len(data) if isinstance(data, list) else 0
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            count = 0
        return {
            "connected": True,
            "environment": config["environment"],
            "environmentLabel": config["environmentLabel"],
            "envelopeCount": count,
            "message": "Conexão validada. A consulta não criou nem alterou envelopes.",
        }, 200
    except HTTPError as error:
        if error.code == 401:
            message = "A Clicksign não aceitou o Access Token. Confira o ambiente e a associação do e-mail à API."
        elif error.code == 403:
            message = "A Clicksign recusou o acesso desta conta. Confira as permissões do token."
        elif error.code == 429:
            message = "A Clicksign limitou temporariamente as consultas. Aguarde um pouco e tente novamente."
        else:
            message = f"A Clicksign respondeu com erro HTTP {error.code}. Tente novamente mais tarde."
        return {"connected": False, "environmentLabel": config["environmentLabel"], "message": message}, 502
    except (URLError, TimeoutError, OSError):
        return {
            "connected": False,
            "environmentLabel": config["environmentLabel"],
            "message": "Não foi possível alcançar a Clicksign. Confira a conexão com a internet e tente novamente.",
        }, 502
    except (UnicodeError, json.JSONDecodeError):
        return {
            "connected": False,
            "environmentLabel": config["environmentLabel"],
            "message": "A resposta da Clicksign não pôde ser interpretada.",
        }, 502


class ClicksignApiError(RuntimeError):
    def __init__(self, message: str, *, envelope_id: str = "", activated: bool = False):
        super().__init__(message)
        self.envelope_id = envelope_id
        self.activated = activated


class ClicksignWorkflowError(ClicksignApiError):
    """Keep every confirmed provider identifier when a later step fails."""

    def __init__(self, message: str, progress: dict):
        super().__init__(message, envelope_id=progress.get("envelope_id", ""),
                         activated=bool(progress.get("activated")))
        self.progress = dict(progress)


def _api_request(environment: str, token: str, path: str, *, method: str, body: dict | None, timeout: int) -> dict:
    root = API_ROOTS[environment]
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(f"{root}{path}", data=raw_body, headers={
        "Authorization": token,
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }, method=method)
    try:
        with urlopen(request, timeout=max(3, min(120, int(timeout)))) as response:
            raw = response.read(1_000_000)
    except HTTPError as error:
        raw = error.read(200_000)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = {}
        errors = payload.get("errors") if isinstance(payload, dict) else None
        first_error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        detail = first_error.get("detail")
        if error.code == 401:
            message = "A Clicksign recusou o token. Confira ambiente e permissões da credencial."
        elif error.code == 403:
            message = "A conta não tem permissão para realizar esta operação na Clicksign."
        elif error.code == 422 and detail:
            message = str(detail)[:500]
        else:
            message = f"A Clicksign recusou uma etapa do envio (HTTP {error.code})."
            if error.code == 400:
                explanation = next((str(first_error[key]) for key in ("detail", "title", "code")
                                    if isinstance(first_error.get(key), str) and first_error[key].strip()), "")
                if explanation:
                    explanation = re.sub(r"[\w.+-]+@[\w.-]+", "[e-mail]", explanation)
                    explanation = re.sub(r"\b\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}\b", "[CPF]", explanation)
                    explanation = re.sub(r"\b\d{10,11}\b", "[número]", explanation)
                    message += f" Motivo: {explanation[:300]}"
        raise ClicksignApiError(message) from None
    except (URLError, TimeoutError, OSError):
        raise ClicksignApiError("Não foi possível alcançar a Clicksign durante o envio.") from None
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ClicksignApiError("A Clicksign retornou uma resposta inválida durante o envio.") from None
    if not isinstance(payload, dict):
        raise ClicksignApiError("A Clicksign retornou uma resposta inválida durante o envio.")
    return payload


def valid_cpf(cpf: str) -> bool:
    if len(cpf) != 11 or not cpf.isdigit() or len(set(cpf)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(digit) * weight for digit, weight in zip(cpf[:length], range(length + 1, 1, -1)))
        check = (total * 10) % 11
        if (0 if check == 10 else check) != int(cpf[length]):
            return False
    return True


def _test_pdf(sender_name: str, recipient_name: str, generated_at: str) -> bytes:
    def pdf_text(value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines = [
        "CLICKSIGN - TESTE DE INTEGRACAO",
        "",
        "Este documento foi criado somente para testar o envio e a assinatura.",
        "Nao contem clausulas de trabalho, pagamento ou autorizacao operacional.",
        "",
        f"Remetente que assina: {sender_name}",
        f"Destinatario que assina: {recipient_name}",
        f"Gerado em: {generated_at}",
        "",
        "Documento de teste; nao use como contrato ou declaracao trabalhista.",
    ]
    commands = ["BT", "/F1 18 Tf", "72 760 Td"]
    first = True
    for line in lines:
        if not first:
            commands.append("0 -30 Td")
        size = "18" if first else "11"
        if not first:
            commands.append(f"/F1 {size} Tf")
        commands.append(f"({pdf_text(line)}) Tj")
        first = False
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def build_test_pdf(sender_name: str, recipient_name: str) -> bytes:
    """Build a clearly non-contractual PDF for a real Clicksign delivery test."""
    import datetime
    generated_at = datetime.datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    return _test_pdf(sender_name, recipient_name, generated_at)


def get_envelope_status(environment: str, token: str, envelope_id: str, *, timeout: int = 20) -> str:
    """Read one envelope's status after a user requests a manual refresh."""
    if environment not in API_ROOTS or not token or not envelope_id:
        raise ValueError("Ambiente, token ou identificador do envelope inválido.")
    payload = _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}",
                           method="GET", timeout=timeout, body=None)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    status = str(attributes.get("status") or "").strip().lower()
    if status not in {"draft", "running", "closed", "canceled"}:
        raise ClicksignApiError("A Clicksign não retornou um status reconhecido para o envelope.")
    return status


def send_test_signature(environment: str, token: str, *, sender_name: str, sender_email: str, sender_cpf: str,
                        sender_phone: str, sender_channel: str, recipient_name: str, recipient_email: str,
                        recipient_phone: str, recipient_channel: str, recipient_cpf: str,
                        timeout: int = 20, envelope_name: str = "",
                        document_pdf: bytes | None = None) -> dict[str, str]:
    """Create a non-contractual test envelope with selectable invite channels."""
    if environment not in API_ROOTS:
        raise ValueError("Ambiente Clicksign inválido.")
    if not token or len(token) > 4096 or any(char in token for char in "\r\n"):
        raise ValueError("Access Token Clicksign ausente ou inválido.")
    if any(not name or len(name) > 180 or len(name.split()) < 2 for name in (sender_name, recipient_name)):
        raise ValueError("Informe nome e sobrenome do remetente e do destinatário.")
    for label, email in (("remetente", sender_email), ("destinatário", recipient_email)):
        if not email or len(email) > 254 or "@" not in email or any(char.isspace() for char in email):
            raise ValueError(f"Informe um e-mail válido para o {label}; ele receberá o documento finalizado.")
    channels = {"EMAIL": "email", "WHATSAPP": "whatsapp"}
    if sender_channel not in channels or recipient_channel not in channels:
        raise ValueError("Escolha WhatsApp ou e-mail para o convite de cada signatário.")
    for label, channel, phone in (("remetente", sender_channel, sender_phone),
                                  ("destinatário", recipient_channel, recipient_phone)):
        if channel == "WHATSAPP" and (not phone.isdigit() or len(phone) not in (10, 11)):
            raise ValueError(f"Informe o WhatsApp do {label} com DDD e 10 ou 11 dígitos.")
    if not valid_cpf(sender_cpf) or not valid_cpf(recipient_cpf):
        raise ValueError("Informe CPFs válidos para o remetente e o destinatário.")

    import datetime
    generated_at = datetime.datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    envelope_id = ""
    activated = False
    signer_ids = []
    stage = "criação do envelope"
    pdf_bytes = document_pdf or _test_pdf(sender_name, recipient_name, generated_at)
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("A prévia do teste não é um PDF válido.")
    try:
        envelope = _api_request(environment, token, "/envelopes", method="POST", timeout=timeout, body={
            "data": {"type": "envelopes", "attributes": {
                "name": (envelope_name or "Teste de integração Clicksign - duas assinaturas")[:180],
                "locale": "pt-BR",
            }}
        })
        envelope_id = str((envelope.get("data") or {}).get("id") or "")
        if not envelope_id:
            raise ClicksignApiError("A Clicksign não retornou o identificador do envelope.")

        stage = "inclusão do PDF"
        pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
        document = _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}/documents",
            method="POST", timeout=timeout, body={"data": {"type": "documents", "attributes": {
                "filename": "Teste real de assinatura Clicksign.pdf",
                "content_base64": f"data:application/pdf;base64,{pdf_base64}",
            }}})
        document_id = str((document.get("data") or {}).get("id") or "")
        if not document_id:
            raise ClicksignApiError("A Clicksign não retornou o identificador do documento.")

        def clicksign_cpf(cpf: str) -> str:
            return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

        parties = (
            {"name": sender_name, "documentation": clicksign_cpf(sender_cpf), "email": sender_email,
             "phone_number": sender_phone, "channel": channels[sender_channel]},
            {"name": recipient_name, "documentation": clicksign_cpf(recipient_cpf), "email": recipient_email,
             "phone_number": recipient_phone, "channel": channels[recipient_channel]},
        )
        for party_index, party in enumerate(parties):
            party_label = "remetente" if party_index == 0 else "destinatário"
            channel = party["channel"]
            signer_attributes = {
                "name": party["name"],
                "documentation": party["documentation"],
                "has_documentation": True,
                "refusable": False,
                "group": 1 if party_index == 0 else 2,
                "communicate_events": {
                    "signature_request": channel,
                    "signature_reminder": "none",
                    "document_signed": "email",
                },
            }
            signer_attributes["email"] = party["email"]
            if channel == "whatsapp":
                signer_attributes["phone_number"] = party["phone_number"]
            stage = f"cadastro do signatário {party_label}"
            signer = _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}/signers",
                method="POST", timeout=timeout, body={"data": {"type": "signers", "attributes": signer_attributes}})
            signer_id = str((signer.get("data") or {}).get("id") or "")
            if not signer_id:
                raise ClicksignApiError("A Clicksign não retornou o identificador de um signatário.")
            signer_ids.append(signer_id)

            relationships = {"document": {"data": {"type": "documents", "id": document_id}},
                             "signer": {"data": {"type": "signers", "id": signer_id}}}
            for attributes in ({"action": "agree", "role": "sign"},
                               {"action": "provide_evidence", "auth": channel}):
                stage = f"requisito {attributes['action']} do {party_label}"
                _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}/requirements",
                    method="POST", timeout=timeout, body={"data": {"type": "requirements", "attributes": attributes,
                                                                      "relationships": relationships}})

        stage = "ativação do envelope"
        _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}", method="PATCH", timeout=timeout,
            body={"data": {"id": envelope_id, "type": "envelopes", "attributes": {"status": "running"}}})
        activated = True
        try:
            notification = _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}/notifications",
                method="POST", timeout=timeout,
                body={"data": {"type": "notifications", "attributes": {"message": None}}})
        except ClicksignApiError as error:
            raise ClicksignApiError(
                f"O envelope {envelope_id} foi ativado, mas a notificação não foi confirmada: {error} "
                "Confira o envelope antes de tentar novamente.", envelope_id=envelope_id, activated=True
            ) from None
        summary = ((notification.get("data") or {}).get("attributes") or {}).get("summary")
        if isinstance(summary, list):
            notified_by_signer = {
                str(item.get("signer_id")): item.get("notified")
                for item in summary if isinstance(item, dict)
            }
            if notified_by_signer.get(signer_ids[0]) is not True:
                raise ClicksignApiError(
                    f"O envelope {envelope_id} foi ativado, mas a Clicksign não confirmou a notificação "
                    "do remetente. Confira o envelope antes de tentar novamente.", envelope_id=envelope_id, activated=True
                )
            if notified_by_signer.get(signer_ids[1]) is True:
                raise ClicksignApiError(
                    f"O envelope {envelope_id} foi ativado, mas a Clicksign informou notificação "
                    "ao destinatário antes da assinatura do remetente. Confira a ordem do envelope.",
                    envelope_id=envelope_id, activated=True
                )
            sender_label = "WhatsApp" if sender_channel == "WHATSAPP" else "e-mail"
            recipient_label = "WhatsApp" if recipient_channel == "WHATSAPP" else "e-mail"
            message = (f"A Clicksign confirmou o convite ao remetente por {sender_label}. Depois que ele assinar, "
                       f"o destinatário receberá o convite por {recipient_label}. Após a conclusão, ambos receberão "
                       "o documento finalizado por e-mail.")
        else:
            message = ("A Clicksign aceitou o pedido de notificação do grupo ativo. O remetente está no grupo 1 "
                       "e o destinatário no grupo 2. O documento finalizado foi configurado para envio por e-mail "
                       "aos dois signatários.")
        return {"envelopeId": envelope_id,
                "message": message}
    except ClicksignApiError as error:
        if envelope_id and not activated:
            try:
                _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}", method="DELETE", timeout=timeout, body=None)
            except ClicksignApiError:
                pass
        if not activated:
            raise ClicksignApiError(f"Falha em {stage}: {error}", envelope_id=envelope_id,
                                    activated=activated) from None
        raise


def envelope_details(environment: str, token: str, envelope_id: str, *, timeout: int = 20) -> dict:
    if environment not in API_ROOTS or not token or not envelope_id:
        raise ValueError("Conexão ou envelope Clicksign inválido.")
    return _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}",
                        method="GET", body=None, timeout=timeout)


def envelope_signers(environment: str, token: str, envelope_id: str, *, timeout: int = 20) -> list:
    payload = _api_request(environment, token, f"/envelopes/{quote(envelope_id, safe='')}/signers",
                           method="GET", body=None, timeout=timeout)
    return payload.get("data") if isinstance(payload.get("data"), list) else []


def create_tpr_signature(environment: str, token: str, *, name: str, pdf: bytes,
                         employee: dict, representative: dict, employee_channel: str,
                         progress_callback, timeout: int = 20) -> dict:
    """Send one reviewed TPR. Never delete an uncertain or partially created package."""
    if environment not in API_ROOTS or not token or not pdf.startswith(b"%PDF-"):
        raise ValueError("Conexão ou documento Clicksign inválido.")
    progress = {}

    def save(**values):
        progress.update(values)
        progress_callback(dict(progress))

    def request(path, *, method="POST", body=None):
        return _api_request(environment, token, path, method=method, body=body, timeout=timeout)

    def identifier(payload, label):
        value = str((payload.get("data") or {}).get("id") or "")
        if not value:
            raise ClicksignApiError(f"A Clicksign não confirmou o identificador de {label}.")
        return value

    try:
        save(stage="CREATING_ENVELOPE")
        envelope_id = identifier(request("/envelopes", body={"data": {"type": "envelopes", "attributes": {
            "name": name[:180], "locale": "pt-BR"}}}), "envelope")
        save(envelope_id=envelope_id, stage="ADDING_DOCUMENT")
        prefix = f"/envelopes/{quote(envelope_id, safe='')}"
        document_id = identifier(request(prefix + "/documents", body={"data": {"type": "documents", "attributes": {
            "filename": "TPR - Termo de prorrogação.pdf",
            "content_base64": "data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii")}}}), "documento")
        save(document_id=document_id)
        for label, party, channel, group in (("representative", representative, representative["channel"], 1),
                                              ("employee", employee, employee_channel, 2)):
            channel = channel.lower()
            if channel not in ("email", "whatsapp") or not valid_cpf(party["cpf"]):
                raise ValueError("Signatário ou canal de assinatura inválido.")
            cpf = party["cpf"]
            attributes = {"name": party["name"], "email": party["email"],
                          "documentation": f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}",
                          "has_documentation": True, "refusable": True, "group": group,
                          "communicate_events": {"signature_request": channel,
                                                 "signature_reminder": "none", "document_signed": "email"}}
            if party.get("birthday"):
                attributes["birthday"] = party["birthday"]
            if channel == "whatsapp":
                attributes["phone_number"] = party["phone"]
            save(stage="ADDING_" + label.upper())
            signer_id = identifier(request(prefix + "/signers", body={"data": {"type": "signers", "attributes": attributes}}), "signatário")
            save(**{label + "_signer_id": signer_id})
            relationships = {"document": {"data": {"type": "documents", "id": document_id}},
                             "signer": {"data": {"type": "signers", "id": signer_id}}}
            for attrs in ({"action": "agree", "role": "sign"}, {"action": "provide_evidence", "auth": channel}):
                save(stage="REQUIREMENT_" + label.upper())
                request(prefix + "/requirements", body={"data": {"type": "requirements", "attributes": attrs,
                                                                  "relationships": relationships}})
        save(stage="ACTIVATING")
        request(prefix, method="PATCH", body={"data": {"type": "envelopes", "id": envelope_id,
                                                        "attributes": {"status": "running"}}})
        details = envelope_details(environment, token, envelope_id, timeout=timeout)
        if str((details.get("data") or {}).get("attributes", {}).get("status") or "") != "running":
            raise ClicksignApiError("A Clicksign ainda não confirmou a ativação. Consulte o pacote antes de notificar.")
        save(activated=True, stage="NOTIFYING", provider_status="running")
        notification = request(prefix + "/notifications", body={"data": {"type": "notifications", "attributes": {}}})
        summary = (notification.get("data") or {}).get("attributes", {}).get("summary")
        if isinstance(summary, list):
            delivered = {str(row.get("signer_id")): row.get("notified") for row in summary if isinstance(row, dict)}
            if delivered.get(progress["representative_signer_id"]) is not True:
                raise ClicksignApiError("O envelope foi ativado, mas o convite ao representante não foi confirmado.")
            if delivered.get(progress["employee_signer_id"]) is True:
                raise ClicksignApiError("A Clicksign notificou o trabalhador antes de confirmar a primeira assinatura. Confira a ordem do pacote.")
        save(stage="AWAITING_SIGNATURE", notification_accepted=True)
        return progress
    except (ClicksignApiError, ValueError, OSError, RuntimeError) as error:
        raise ClicksignWorkflowError(str(error), progress) from error


def remind_tpr(environment: str, token: str, envelope_id: str, signer_id: str, *, timeout: int = 20):
    if get_envelope_status(environment, token, envelope_id, timeout=timeout) != "running":
        raise ValueError("Lembretes exigem um envelope em andamento.")
    if not signer_id:
        raise ValueError("Signatário do lembrete não identificado.")
    return _api_request(environment, token,
                        f"/envelopes/{quote(envelope_id, safe='')}/signers/{quote(signer_id, safe='')}/notifications",
                        method="POST", body={"data": {"type": "notifications", "attributes": {}}}, timeout=timeout)


def cancel_tpr(environment: str, token: str, envelope_id: str, document_id: str, *, timeout: int = 20) -> str:
    status = get_envelope_status(environment, token, envelope_id, timeout=timeout)
    prefix = f"/envelopes/{quote(envelope_id, safe='')}"
    if status == "closed":
        raise ValueError("Uma prorrogação já assinada não pode ser cancelada por este fluxo.")
    if status == "canceled":
        return "canceled"
    if status == "draft":
        _api_request(environment, token, prefix, method="DELETE", body=None, timeout=timeout)
        return "draft_deleted"
    if status != "running" or not document_id:
        raise ValueError("O documento não está identificado para cancelar o pacote. Confira a Clicksign.")
    path = prefix + f"/documents/{quote(document_id, safe='')}"
    _api_request(environment, token, path, method="PATCH", timeout=timeout,
                  body={"data": {"type": "documents", "id": document_id, "attributes": {"status": "canceled"}}})
    confirmed = _api_request(environment, token, path, method="GET", body=None, timeout=timeout)
    if str((confirmed.get("data") or {}).get("attributes", {}).get("status") or "") != "canceled":
        raise ClicksignApiError("O cancelamento ainda não foi confirmado pela Clicksign.")
    return "canceled"


def download_signed_tpr(environment: str, token: str, envelope_id: str, document_id: str, *, timeout: int = 20) -> bytes:
    """Only the authenticated provider can supply the signed PDF reference."""
    if get_envelope_status(environment, token, envelope_id, timeout=timeout) != "closed":
        raise ValueError("A Clicksign ainda não confirmou todas as assinaturas.")
    details = _api_request(environment, token,
                           f"/envelopes/{quote(envelope_id, safe='')}/documents/{quote(document_id, safe='')}",
                           method="GET", body=None, timeout=timeout)
    data = details.get("data") or {}
    if str((data.get("attributes") or {}).get("status") or "").lower() != "closed":
        raise ValueError("O documento assinado ainda não foi disponibilizado.")
    links = data.get("links") or {}
    reference = str((links.get("files") or {}).get("signed") or "")

    def allowed(url):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        suffixes = ("clicksign.com", "amazonaws.com", "cloudfront.net")
        return parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443) and any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)

    if not allowed(reference):
        raise ValueError("O provedor não retornou um endereço autorizado para o PDF assinado.")

    class SignedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not allowed(newurl):
                raise ValueError("Redirecionamento do PDF assinado não autorizado.")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    limit = 10 * 1024 * 1024
    try:
        request = Request(reference, headers={"Accept": "application/pdf", "User-Agent": "ConexaoDP/3"})
        with build_opener(SignedRedirect()).open(request, timeout=timeout) as response:
            if int(response.headers.get("Content-Length") or 0) > limit:
                raise ValueError("O PDF assinado excede o limite de 10 MB do prontuário.")
            raw = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ClicksignApiError("Não foi possível baixar o documento assinado. Uma nova consulta poderá retomar o arquivamento.") from error
    if len(raw) > limit or not raw.startswith(b"%PDF-"):
        raise ValueError("O arquivo assinado excede o limite ou não é um PDF válido.")
    return raw
