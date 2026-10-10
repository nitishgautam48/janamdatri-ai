"""One-time passwords, stored hashed in Redis with expiry and attempt limits."""
from __future__ import annotations

import hmac

from app.core.cache import get_json, get_store, set_json
from app.core.config import settings
from app.core.exceptions import RateLimited, ServiceUnavailable, Unauthorized
from app.core.security import generate_otp, hash_otp
from app.notifications.sms import SMSDeliveryError, send_sms


def _key(purpose: str, subject: str) -> str:
    return f"otp:{purpose}:{subject}"


def issue_otp(purpose: str, subject: str, phone: str) -> str:
    """Create and SMS an OTP. ``subject`` is what the OTP proves (a phone or an ASHA ID)."""
    count = get_store().incr(f"otp-rate:{subject}", ex=3600)
    if count > settings.otp_requests_per_hour:
        raise RateLimited("Too many OTP requests. Please try again in an hour.")
    code = generate_otp()
    set_json(_key(purpose, subject), {"h": hash_otp(subject, code), "a": 0}, ex=settings.otp_ttl_seconds)
    try:
        send_sms(phone, f"{code} is your Janamdatri-AI verification code. Do not share it with anyone.", critical=True)
    except SMSDeliveryError as exc:
        get_store().delete(_key(purpose, subject))
        raise ServiceUnavailable("Could not deliver the OTP. Is Mailpit running? (run_local starts it automatically)",
                                 code="sms_undeliverable") from exc
    return code


def verify_otp(purpose: str, subject: str, code: str) -> None:
    key = _key(purpose, subject)
    data = get_json(key)
    if not data:
        raise Unauthorized("OTP expired or not requested. Please request a new one.", code="otp_expired")
    if data["a"] >= settings.otp_max_attempts:
        get_store().delete(key)
        raise Unauthorized("Too many wrong attempts. Request a new OTP.", code="otp_locked")
    if not hmac.compare_digest(data["h"], hash_otp(subject, code.strip())):
        data["a"] += 1
        set_json(key, data, ex=settings.otp_ttl_seconds)
        raise Unauthorized("Incorrect OTP", code="otp_invalid")
    get_store().delete(key)
