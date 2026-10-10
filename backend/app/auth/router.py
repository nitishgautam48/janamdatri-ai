from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import DB, CurrentUser, bearer
from app.auth import service
from app.auth.schemas import (
    AdminLogin,
    AshaLogin,
    AshaVerifyRequest,
    AshaVerifySent,
    LogoutRequest,
    MotherLogin,
    MotherSignup,
    OTPRequest,
    OTPSent,
    PinLogin,
    PinSet,
    RefreshRequest,
    TokenResponse,
)
from app.core.config import settings
from app.core.enums import Role
from app.core.exceptions import Forbidden, Unauthorized
from app.core.ratelimit import limit
from app.core.security import decode_token, hash_secret, mask_phone, revoke_token
from app.database.models import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _otp_out(phone: str, code: str) -> dict:
    return {"phone_masked": mask_phone(phone), "expires_in": settings.otp_ttl_seconds,
            "delivered_to": settings.mailpit_ui_url if settings.sms_channel == "mailpit" else None,
            "dev_otp": code if (settings.otp_dev_echo and code) else None}


@router.post("/otp/request", response_model=OTPSent, dependencies=[Depends(limit("otp", 20, 3600))])
def request_otp(body: OTPRequest, db: DB):
    """Send a 6-digit OTP by SMS for mother login or sign up."""
    phone, code = service.request_mother_otp(db, body.phone, body.purpose)
    return OTPSent(**_otp_out(phone, code))


@router.post("/mother/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def mother_signup(body: MotherSignup, db: DB):
    return service.mother_signup(db, body.phone, body.otp, body.name, body.language)


@router.post("/mother/login", response_model=TokenResponse)
def mother_login(body: MotherLogin, db: DB):
    return service.mother_login(db, body.phone, body.otp)


@router.post("/guest", response_model=TokenResponse, dependencies=[Depends(limit("guest", 30, 3600))])
def guest():
    """Explore without an account: guide, questions, nearby care and danger signs. Nothing is saved."""
    return service.guest_token()


@router.post("/pin/set")
def set_pin(body: PinSet, db: DB, user: CurrentUser):
    """Shared/family phone mode: protect the account with a 4-digit PIN."""
    if user.role != Role.MOTHER:
        raise Forbidden("PIN login is for mothers on shared phones")
    user.pin_hash = hash_secret(body.pin)
    if user.mother:
        user.mother.shared_phone_mode = True
    db.commit()
    return {"pin_set": True}


@router.post("/pin/login", response_model=TokenResponse, dependencies=[Depends(limit("pin", 20, 900))])
def pin_login(body: PinLogin, db: DB):
    return service.pin_login(db, body.phone, body.pin)


@router.post("/asha/verify", response_model=AshaVerifySent, dependencies=[Depends(limit("asha-verify", 20, 3600))])
def asha_verify(body: AshaVerifyRequest, db: DB):
    """Validate ASHA Reg. No, RCH ID, AWC, Sub-Centre and Sector codes, then send an OTP to the registered phone."""
    worker, in_registry, code = service.asha_verify(db, body)
    return AshaVerifySent(asha_reg_no=worker.asha_reg_no, in_registry=in_registry, **_otp_out(worker.phone, code))


@router.post("/asha/login", response_model=TokenResponse)
def asha_login(body: AshaLogin, db: DB):
    return service.asha_login(db, body.asha_reg_no, body.otp)


@router.post("/admin/login", response_model=TokenResponse)
def admin_login(body: AdminLogin, db: DB):
    return service.admin_login(db, body.email, body.password)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: DB):
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if not user or not user.is_active:
        raise Unauthorized("Account not found or disabled", code="inactive_user")
    revoke_token(payload)  # rotate: each refresh token works once
    return service.issue_tokens(user)


@router.post("/logout")
def logout(body: LogoutRequest, creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if creds:
        try:
            revoke_token(decode_token(creds.credentials))
        except Unauthorized:
            pass
    if body.refresh_token:
        try:
            revoke_token(decode_token(body.refresh_token, expected_type="refresh"))
        except Unauthorized:
            pass
    return {"logged_out": True}
