"""Conector server-side para a API externa VoxFy/Z-PRO."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def validate_base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "vox.selecty.app" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.startswith("/v2/api/external/")
            or parsed.path.count("/") != 4
            or not parsed.path.rsplit("/", 1)[-1].replace("-", "").isalnum()):
        raise ValueError("Informe a URL HTTPS da API VoxFy no formato /v2/api/external/{ID}.")
    return value


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_transform(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise RuntimeError("O armazenamento protegido da chave VoxFy exige o Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    entropy_buffer = ctypes.create_string_buffer(b"ConexaoDP.VoxFy.v1")
    entropy = _DataBlob(len(b"ConexaoDP.VoxFy.v1"), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
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
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
                                        None, None, flags, ctypes.byref(output))
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "Conexao DP VoxFy", ctypes.byref(entropy),
                                      None, None, flags, ctypes.byref(output))
    if not ok:
        raise OSError(ctypes.get_last_error(), "O Windows não conseguiu proteger a chave VoxFy.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect_secret(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 8192 or any(char in value for char in "\r\n\0"):
        raise ValueError("A chave VoxFy informada parece inválida.")
    return "dpapi:" + base64.b64encode(_dpapi_transform(value.encode("utf-8"), decrypt=False)).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        raise RuntimeError("A chave VoxFy salva está em um formato não reconhecido.")
    try:
        raw = base64.b64decode(value[6:], validate=True)
        return _dpapi_transform(raw, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Não foi possível abrir a chave VoxFy protegida.") from error


def test_connection(base_url: str, token: str, timeout: int = 30) -> tuple[dict[str, object], int]:
    try:
        base_url = validate_base_url(base_url)
    except ValueError as error:
        return {"connected": False, "message": str(error)}, 400
    token = str(token or "").strip()
    if not token or len(token) > 8192 or any(char in token for char in "\r\n"):
        return {"connected": False, "message": "Informe a chave Bearer da API VoxFy."}, 400
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return {"connected": False, "message": "Informe a chave Bearer da API VoxFy."}, 400

    request = Request(f"{base_url}/params/", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }, method="GET")
    try:
        with urlopen(request, timeout=max(3, min(int(timeout), 120))) as response:
            status = int(response.status)
        if 200 <= status < 300:
            return {"connected": True, "httpStatus": status,
                    "message": f"Conexão validada. A API respondeu HTTP {status} à consulta de parâmetros."}, 200
        return {"connected": False, "httpStatus": status,
                "message": f"A API respondeu HTTP {status} ao teste de conexão."}, 502
    except HTTPError as error:
        status = int(error.code)
        if status in (401, 403):
            message = "A API recusou a autenticação. Confira a chave Bearer e o acesso externo habilitado no VoxFy."
        else:
            message = f"A API respondeu HTTP {status} ao teste de conexão."
        return {"connected": False, "httpStatus": status, "message": message}, 502
    except (URLError, TimeoutError, OSError, ValueError):
        return {"connected": False,
                "message": "Não foi possível alcançar a API VoxFy. Confira a URL e a conexão do servidor."}, 502
