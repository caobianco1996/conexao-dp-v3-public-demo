"""Secure storage for the SMTP password used by the local V3 service.

On Windows the secret is stored in the current user's Windows Credential
Manager. It is never returned to the browser or written to SQLite.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


_TARGET = "ConexaoDP/PublicDemo/V3/SMTP/primary"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class SMTPPasswordStoreError(RuntimeError):
    """A safe, user-facing error from the operating-system credential store."""


class _CredentialAttributeW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_byte)),
    ]


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CredentialAttributeW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise SMTPPasswordStoreError("O armazenamento seguro da senha SMTP requer o Windows Credential Manager.")
    try:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    except OSError as error:
        raise SMTPPasswordStoreError("Não foi possível acessar o Gerenciador de Credenciais do Windows.") from error
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(_CredentialW))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [wintypes.LPVOID]
    advapi32.CredFree.restype = None
    return advapi32


def save_password(password: str) -> None:
    value = str(password or "")
    if not value or len(value) > 1024 or any(char in value for char in "\r\n\0"):
        raise ValueError("Informe uma senha SMTP válida para salvar.")
    encoded = value.encode("utf-8")
    if len(encoded) > 5120:
        raise ValueError("A senha SMTP excede o limite aceito pelo Gerenciador de Credenciais do Windows.")
    advapi32 = _api()
    blob = ctypes.create_string_buffer(encoded)
    credential = _CredentialW(
        Flags=0,
        Type=_CRED_TYPE_GENERIC,
        TargetName=_TARGET,
        Comment="Senha da conexão SMTP da Central DP V3",
        LastWritten=wintypes.FILETIME(0, 0),
        CredentialBlobSize=len(encoded),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE,
        AttributeCount=0,
        Attributes=None,
        TargetAlias=None,
        UserName="SMTP Demonstração",
    )
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise SMTPPasswordStoreError("O Windows não conseguiu salvar a senha SMTP no Gerenciador de Credenciais.")


def get_password() -> str:
    advapi32 = _api()
    credential_ptr = ctypes.POINTER(_CredentialW)()
    if not advapi32.CredReadW(_TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(credential_ptr)):
        error_code = ctypes.get_last_error()
        if error_code == _ERROR_NOT_FOUND:
            return ""
        raise SMTPPasswordStoreError("O Windows não conseguiu ler a senha SMTP salva.")
    try:
        credential = credential_ptr.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        try:
            return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-8")
        except UnicodeError as error:
            raise SMTPPasswordStoreError("A senha SMTP salva no Windows está inválida; salve-a novamente.") from error
    finally:
        advapi32.CredFree(credential_ptr)


def has_password() -> bool:
    return bool(get_password())


def remove_password() -> None:
    advapi32 = _api()
    if not advapi32.CredDeleteW(_TARGET, _CRED_TYPE_GENERIC, 0):
        error_code = ctypes.get_last_error()
        if error_code != _ERROR_NOT_FOUND:
            raise SMTPPasswordStoreError("O Windows não conseguiu remover a senha SMTP do Gerenciador de Credenciais.")
