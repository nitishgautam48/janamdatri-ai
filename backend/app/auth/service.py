"""Authentication flows for mothers, ASHA workers, admins and guests."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import otp
from app.auth.schemas import AshaVerifyRequest, TokenResponse, UserBrief
from app.core.config import settings
from app.core.enums import Role
from app.core.exceptions import Conflict, NotFound, Unauthorized, ValidationFailed
from app.core.ratelimit import hit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    mask_phone,
    normalize_phone,
    verify_secret,
)
from app.database.base import utcnow
from app.database.models import AshaWorker, Mother, User

GUEST_FEATURES = ["pregnancy_guide", "knowledge", "assistant", "nearby_care", "danger_signs", "emergency"]

# Shape checks for the prototype's mock registry validation.
ASHA_REG_RE = re.compile(r"^[A-Z]{2}-[A-Z]{2,4}-\d{3,6}$")
SC_RE = re.compile(r"^SC-\d{2,6}$")
SECTOR_RE = re.compile(r"^SEC-\d{1,4}$")


def _norm_code(v: str | None) -> str | None:
    return v.strip().upper() if v else None


def issue_tokens(user: User, *, is_new: bool = False, verified_worker: str | None = None) -> TokenResponse:
    access, exp = create_access_token(user.id, user.role)
    refresh, _ = create_refresh_token(user.id, user.role)
    return TokenResponse(
        access_token=access, refresh_token=refresh, expires_at=exp, role=user.role, is_new_user=is_new,
        user=UserBrief(id=user.id, role=user.role, name=user.full_name, phone_masked=mask_phone(user.phone),
                       language=user.language, verified_worker=verified_worker),
    )


def guest_token() -> TokenResponse:
    gid = f"guest-{uuid.uuid4().hex[:12]}"
    access, exp = create_access_token(gid, Role.GUEST)
    return TokenResponse(access_token=access, expires_at=exp, role=Role.GUEST,
                         user=UserBrief(id=None, role=Role.GUEST, name="Guest"), guest_features=GUEST_FEATURES)


# --- Mothers ---------------------------------------------------------------
def request_mother_otp(db: Session, raw_phone: str, purpose: str) -> tuple[str, str]:
    phone = normalize_phone(raw_phone)
    existing = db.scalar(select(User).where(User.phone == phone))
    is_asha_phone = db.scalar(select(AshaWorker.id).where(AshaWorker.phone == phone)) is not None
    if (existing and existing.role == Role.ASHA) or (not existing and is_asha_phone):
        raise Conflict("This number belongs to an ASHA worker. Please use ASHA worker login.", code="asha_account")
    if existing and existing.role != Role.MOTHER:
        raise Conflict("This number is registered as a health worker account", code="other_account")
    if purpose == "mother_login" and not existing:
        # Rural users need a clear answer rather than an OTP that never arrives.
        raise NotFound("No account for this number yet. Please sign up.", code="account_not_found")
    return phone, otp.issue_otp("mother", phone, phone)


def mother_signup(db: Session, raw_phone: str, code: str, name: str | None, language: str) -> TokenResponse:
    phone = normalize_phone(raw_phone)
    otp.verify_otp("mother", phone, code)
    user = db.scalar(select(User).where(User.phone == phone))
    if user:
        if user.role != Role.MOTHER:
            raise Conflict("This number belongs to another account type")
        # ASHA may have registered her already: claim the account.
        mother = user.mother
        if mother and name and not mother.name:
            mother.name = name
        user.phone_verified = True
        user.last_login_at = utcnow()
        db.commit()
        return issue_tokens(user, is_new=False)
    user = User(role=Role.MOTHER, phone=phone, full_name=name, language=language, phone_verified=True,
                last_login_at=utcnow())
    db.add(user)
    db.flush()
    db.add(Mother(user_id=user.id, name=name, language=language))
    db.commit()
    return issue_tokens(user, is_new=True)


def mother_login(db: Session, raw_phone: str, code: str) -> TokenResponse:
    phone = normalize_phone(raw_phone)
    otp.verify_otp("mother", phone, code)
    user = db.scalar(select(User).where(User.phone == phone, User.role == Role.MOTHER))
    if not user:
        raise NotFound("No account for this number. Please sign up.", code="account_not_found")
    if not user.is_active:
        raise Unauthorized("Account disabled", code="inactive_user")
    user.phone_verified = True
    user.last_login_at = utcnow()
    db.commit()
    return issue_tokens(user)


def pin_login(db: Session, raw_phone: str, pin: str) -> TokenResponse:
    phone = normalize_phone(raw_phone)
    hit(f"pin:{phone}", 5, 900)
    user = db.scalar(select(User).where(User.phone == phone, User.role == Role.MOTHER))
    if not user or not verify_secret(pin, user.pin_hash):
        raise Unauthorized("Incorrect PIN", code="pin_invalid")
    user.last_login_at = utcnow()
    db.commit()
    return issue_tokens(user)


# --- ASHA workers ----------------------------------------------------------
def asha_verify(db: Session, req: AshaVerifyRequest) -> tuple[AshaWorker, bool, str]:
    reg = _norm_code(req.asha_reg_no)
    sc, sector = _norm_code(req.sub_centre_code), _norm_code(req.sector_code)
    worker = db.scalar(select(AshaWorker).where(func.upper(AshaWorker.asha_reg_no) == reg))
    in_registry = worker is not None
    if worker:
        mismatches = [
            name for name, given, stored in (
                ("sub_centre_code", sc, worker.sub_centre_code),
                ("sector_code", sector, worker.sector_code),
                ("awc_code", _norm_code(req.awc_code), worker.awc_code),
                ("rch_id", _norm_code(req.rch_id), worker.rch_id),
            ) if given and stored and given != stored.upper()
        ]
        if mismatches:
            raise ValidationFailed("Details do not match the ASHA registry", code="asha_mismatch",
                                   details={"fields": mismatches})
    else:
        if not settings.asha_mock_registry:
            raise NotFound("ASHA registration number not found", code="asha_not_found")
        problems = [n for n, ok in (("asha_reg_no", ASHA_REG_RE.match(reg or "")),
                                    ("sub_centre_code", SC_RE.match(sc or "")),
                                    ("sector_code", SECTOR_RE.match(sector or ""))) if not ok]
        if problems:
            raise ValidationFailed("Please check the ID format (e.g. UP-BRL-04417, SC-2208, SEC-07)",
                                   code="asha_id_format", details={"fields": problems})
        if not req.name or not req.phone:
            raise ValidationFailed("Name and mobile number are needed for a new ASHA registration",
                                   code="asha_details_required")
        worker = AshaWorker(
            name=req.name, phone=normalize_phone(req.phone), asha_reg_no=reg, rch_id=_norm_code(req.rch_id),
            awc_code=_norm_code(req.awc_code), sub_centre_code=sc, sector_code=sector, village=req.village,
            villages_served=[req.village] if req.village else [], pincode=req.pincode, verified=False,
        )
        db.add(worker)
        db.commit()
    code = otp.issue_otp("asha", worker.asha_reg_no, worker.phone)
    return worker, in_registry, code


def asha_login(db: Session, reg_no: str, code: str) -> TokenResponse:
    reg = _norm_code(reg_no)
    worker = db.scalar(select(AshaWorker).where(func.upper(AshaWorker.asha_reg_no) == reg))
    if not worker:
        raise NotFound("ASHA registration number not found", code="asha_not_found")
    otp.verify_otp("asha", worker.asha_reg_no, code)
    user = db.get(User, worker.user_id) if worker.user_id else None
    is_new = user is None
    if user is None:
        user = db.scalar(select(User).where(User.phone == worker.phone))
        if user and user.role != Role.ASHA:
            raise Conflict("This mobile number is already used by another account type")
        if user is None:
            user = User(role=Role.ASHA, phone=worker.phone, full_name=worker.name, phone_verified=True)
            db.add(user)
            db.flush()
        worker.user_id = user.id
    worker.verified = True
    worker.last_active_at = utcnow()
    user.last_login_at = utcnow()
    db.commit()
    label = f"{worker.name} · {worker.sub_centre_name or worker.sub_centre_code}"
    return issue_tokens(user, is_new=is_new, verified_worker=label)


# --- Admin -----------------------------------------------------------------
def admin_login(db: Session, email: str, password: str) -> TokenResponse:
    hit(f"admin-login:{email.lower()}", 10, 900)
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower(), User.role == Role.ADMIN))
    if not user or not verify_secret(password, user.password_hash):
        raise Unauthorized("Incorrect email or password", code="bad_credentials")
    if not user.is_active:
        raise Unauthorized("Account disabled", code="inactive_user")
    user.last_login_at = utcnow()
    db.commit()
    return issue_tokens(user)
