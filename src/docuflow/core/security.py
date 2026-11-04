import hashlib
import secrets
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash

from docuflow.core.config import get_settings
from docuflow.core.database import utcnow

TokenType = Literal["access", "refresh"]

_password_hash = PasswordHash.recommended()  # Argon2id


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def create_token(subject: UUID, token_type: TokenType, *, jti: UUID | None = None) -> str:
    settings = get_settings()
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"  # noqa: S105
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = utcnow()
    payload = {
        "sub": str(subject),
        "type": token_type,
        "jti": str(jti or uuid4()),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT. Raises jwt.PyJWTError on any problem."""
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp", "type", "jti"]},
    )
    if payload["type"] != expected_type:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash). Only the hash is stored."""
    prefix = f"dfk_{secrets.token_hex(4)}"
    full_key = f"{prefix}_{secrets.token_urlsafe(32)}"
    return full_key, prefix, hash_api_key(full_key)


def hash_api_key(key: str) -> str:
    # API keys are high-entropy random strings, so a fast hash is fine (unlike passwords).
    return hashlib.sha256(key.encode()).hexdigest()


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"
