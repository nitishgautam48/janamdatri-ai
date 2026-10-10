from __future__ import annotations

from pydantic import BaseModel, Field


class FacilityIn(BaseModel):
    name: str = Field(max_length=160)
    facility_type: str = Field(pattern=r"^(SC|PHC|CHC|DH)$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str | None = None
    village: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    phone: str | None = None
    is_24x7: bool = False
    has_emergency: bool = False
    has_blood_bank: bool = False
    services: list[str] = []
    opd_hours: str | None = None
    active: bool = True


class GISMappingIn(BaseModel):
    pincode: str = Field(pattern=r"^\d{6}$")
    village: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    block: str | None = None
    district: str | None = None
    state: str | None = None
    sub_centre_code: str | None = None
    sector_code: str | None = None
