"""Reconhece filas em execução sem manter ativa uma tarefa cujo processo encerrou."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def database_server_lock(database):
    """Keep a single server per SQLite file, across ports and network sandboxes."""
    lock_path = Path(str(Path(database).resolve()) + ".server.lock")
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("A V3 já está aberta usando este banco. Use a instância existente.") from error
        try:
            handle.seek(1)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def worker_token():
    return f"{os.getpid()}:{uuid4().hex}"


def lease_active(token, until):
    if not token or float(until) <= time.time():
        return False
    owner, separator, _ = str(token).partition(":")
    if not separator or not owner.isdigit():
        return True  # Leases anteriores à identificação do processo continuam com prazo limitado.
    pid = int(owner)
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # INVALID_PARAMETER: PID inexistente.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # Apenas POSIX: consulta de existência, sem envio de sinal.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
