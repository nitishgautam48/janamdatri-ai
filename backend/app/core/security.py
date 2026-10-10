"""Password/PIN hashing, JWT issue/verify, and phone masking."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.cache import get_store
from app.core.config import settings
from app.core.exceptions import Unauthorized, ValidationFailed


# --- hashing ---------------------------------------------------------------
def hash_secret(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=12 if settings.is_production else 6)).decode()


def verify_secret(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except ValueError:
        return False


def hash_otp(phone: str, code: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def generate_otp() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(settings.otp_length))


# --- phone numbers ---------------------------------------------------------
_PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(raw: str) -> str:
    """Return an Indian mobile number as ``+91XXXXXXXXXX`` or raise."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _PHONE_RE.match(digits):
        raise ValidationFailed("Enter a valid 10-digit Indian mobile number", code="invalid_phone")
    return f"+91{digits}"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return f"+91 ••••• {digits[-4:]}" if len(digits) >= 4 else "•••••"


# --- JWT -------------------------------------------------------------------
def _encode(claims: dict[str, Any], lifetime: timedelta) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    exp = now + lifetime
    payload = {**claims, "iat": int(now.timestamp()), "exp": int(exp.timestamp()), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), exp


def create_access_token(user_id: uuid.UUID | str, role: str, **extra: Any) -> tuple[str, datetime]:
    lifetime = (
        timedelta(hours=settings.guest_token_hours)
        if role == "guest"
        else timedelta(minutes=settings.access_token_minutes)
    )
    return _encode({"sub": str(user_id), "role": role, "typ": "access", **extra}, lifetime)


def create_refresh_token(user_id: uuid.UUID | str, role: str) -> tuple[str, datetime]:
    return _encode({"sub": str(user_id), "role": role, "typ": "refresh"}, timedelta(days=settings.refresh_token_days))


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Session expired, please log in again", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token", code="invalid_token") from exc
    if payload.get("typ") != expected_type:
        raise Unauthorized("Wrong token type", code="invalid_token")
    if get_store().get(f"revoked:{payload.get('jti')}"):
        raise Unauthorized("Token has been revoked", code="token_revoked")
    return payload


def revoke_token(payload: dict[str, Any]) -> None:
    ttl = max(int(payload["exp"] - datetime.now(UTC).timestamp()), 1)
    get_store().set(f"revoked:{payload['jti']}", "1", ex=ttl)


def random_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
