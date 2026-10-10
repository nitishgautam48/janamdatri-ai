from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OTPRequest(BaseModel):
    phone: str = Field(examples=["9876544210"])
    purpose: Literal["mother_login", "mother_signup"] = "mother_login"


class OTPSent(BaseModel):
    sent: bool = True
    phone_masked: str
    expires_in: int
    resend_after: int = 30
    delivered_to: str | None = Field(None, description="Where the SMS can be read locally (Mailpit inbox URL)")
    dev_otp: str | None = Field(None, description="The OTP itself, only when OTP_DEV_ECHO=true (automated tests)")


class MotherSignup(BaseModel):
    phone: str
    otp: str = Field(min_length=4, max_length=8)
    name: str | None = Field(None, max_length=120)
    language: str = Field("hi", max_length=8)


class MotherLogin(BaseModel):
    phone: str
    otp: str = Field(min_length=4, max_length=8)


class AshaVerifyRequest(BaseModel):
    asha_reg_no: str = Field(examples=["UP-BRL-04417"], max_length=32)
    rch_id: str | None = Field(None, max_length=32)
    awc_code: str | None = Field(None, max_length=32)
    sub_centre_code: str = Field(examples=["SC-2208"], max_length=32)
    sector_code: str = Field(examples=["SEC-07"], max_length=32)
    # Only needed when the ID is not in the registry yet (prototype self-registration)
    name: str | None = Field(None, max_length=120)
    phone: str | None = None
    village: str | None = None
    pincode: str | None = Field(None, pattern=r"^\d{6}$")


class AshaVerifySent(OTPSent):
    asha_reg_no: str
    in_registry: bool


class AshaLogin(BaseModel):
    asha_reg_no: str
    otp: str = Field(min_length=4, max_length=8)


class AdminLogin(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=6, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class PinSet(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")


class PinLogin(BaseModel):
    phone: str
    pin: str = Field(pattern=r"^\d{4}$")


class UserBrief(BaseModel):
    id: uuid.UUID | None
    role: str
    name: str | None = None
    phone_masked: str | None = None
    language: str | None = None
    verified_worker: str | None = Field(None, description="e.g. 'Sunita Devi · Rampur Sub Centre' for ASHA")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_at: datetime
    role: str
    is_new_user: bool = False
    user: UserBrief
    guest_features: list[str] | None = None
