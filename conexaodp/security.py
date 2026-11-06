"""Senhas e sessões locais sem dependência externa."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ITERATIONS = 480_000
SESSION_SECONDS = 8 * 60 * 60


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(password_hash(password, salt), expected)


def new_session() -> tuple[str, bytes, str, int]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).digest(), secrets.token_urlsafe(32), int(time.time()) + SESSION_SECONDS


def session_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
