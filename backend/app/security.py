"""Authentication & authorization primitives.

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library (no native
build dependencies, works identically on Windows and Linux). Tokens are JWTs
signed with the application secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings

_PBKDF2_ROUNDS = 240_000
_HASH_PREFIX = "pbkdf2_sha256"


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str, *, rounds: int = _PBKDF2_ROUNDS, salt: bytes | None = None) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{_HASH_PREFIX}${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        prefix, rounds_s, salt_b64, hash_b64 = stored.split("$")
        if prefix != _HASH_PREFIX:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(rounds_s)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def create_access_token(subject: str, *, role: str, user_id: int, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "uid": user_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(UTC),
        "iss": "manufacturing-sop-ai-auditor",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer="manufacturing-sop-ai-auditor",
    )


# --------------------------------------------------------------------------- #
# Secret-at-rest helper for provider API keys
# --------------------------------------------------------------------------- #
def _key_stream(nonce: bytes, length: int) -> bytes:
    """Derive a keystream from the app secret (HMAC-SHA256 in counter mode)."""
    settings = get_settings()
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(
            settings.secret_key.encode("utf-8"), nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        counter += 1
    return out[:length]


def encrypt_secret(plaintext: str) -> str:
    """Obfuscate a credential at rest.

    This protects against casual disclosure (DB dumps, backups, screenshots).
    For multi-tenant production deployments use a KMS / OS keyring instead; see
    docs/limitations.md.
    """
    if not plaintext:
        return ""
    nonce = secrets.token_bytes(12)
    data = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _key_stream(nonce, len(data)), strict=False))
    tag = hmac.new(get_settings().secret_key.encode("utf-8"), nonce + ct, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        blob = base64.urlsafe_b64decode(token.encode())
        nonce, tag, ct = blob[:12], blob[12:28], blob[28:]
        expected = hmac.new(get_settings().secret_key.encode("utf-8"), nonce + ct, hashlib.sha256).digest()[
            :16
        ]
        if not hmac.compare_digest(tag, expected):
            return ""
        return bytes(a ^ b for a, b in zip(ct, _key_stream(nonce, len(ct)), strict=False)).decode("utf-8")
    except Exception:
        return ""


def mask_secret(value: str) -> str:
    """Return a log/UI-safe representation of a credential."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"
