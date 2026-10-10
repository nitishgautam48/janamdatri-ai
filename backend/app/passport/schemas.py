from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CONDITIONS = ["diabetes", "anemia", "hypertension", "thyroid", "heart_disease", "asthma", "epilepsy",
              "tuberculosis", "hiv", "hepatitis_b", "kidney_disease", "sickle_cell", "other"]
STEPS = ["about_you", "pregnancy_details", "medical_history", "reports", "emergency_contacts"]


class AboutYou(BaseModel):
    name: str | None = Field(None, max_length=120)
    age: int | None = Field(None, ge=12, le=60)
    language: str | None = Field(None, max_length=8, examples=["hi", "en", "bho"])
    village: str | None = Field(None, max_length=120)
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    district: str | None = Field(None, max_length=80)
    state: str | None = Field(None, max_length=80)
    address: str | None = Field(None, max_length=255)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    rch_id: str | None = Field(None, max_length=32)
    status: Literal["pregnant", "postnatal", "planning"] | None = None


class PregnancyIn(BaseModel):
    lmp: date | None = None
    edd: date | None = None
    edd_source: Literal["lmp", "ultrasound", "manual"] | None = None
    first_pregnancy: bool | None = None
    gravida: int | None = Field(None, ge=1, le=20)
    parity: int | None = Field(None, ge=0, le=20)
    multiple_pregnancy: bool | None = None
    anc_visits_completed: int | None = Field(None, ge=0, le=20)
    next_anc_date: date | None = None

    @field_validator("lmp")
    @classmethod
    def lmp_not_future(cls, v: date | None) -> date | None:
        if v and v > date.today():
            raise ValueError("LMP cannot be in the future")
        if v and (date.today() - v).days > 44 * 7:
            raise ValueError("LMP is more than 44 weeks ago")
        return v


class MedicalHistoryIn(BaseModel):
    conditions: list[str] | None = Field(None, description=f"Any of {CONDITIONS}")
    past_complications: bool | None = None
    complication_details: list[str] | None = None
    previous_c_section: bool | None = None
    allergies: list[str] | None = None
    current_medications: list[str] | None = None
    notes: str | None = Field(None, max_length=2000)

    @field_validator("conditions")
    @classmethod
    def known_conditions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        norm = [c.strip().lower().replace(" ", "_") for c in v]
        bad = [c for c in norm if c not in CONDITIONS]
        if bad:
            raise ValueError(f"Unknown conditions {bad}; use {CONDITIONS}")
        return sorted(set(norm))


class EmergencyContactIn(BaseModel):
    name: str = Field(max_length=120)
    relation: str | None = Field(None, max_length=40)
    phone: str
    is_primary: bool = False


class EmergencyContactOut(BaseModel):
    id: uuid.UUID
    name: str
    relation: str | None
    phone: str
    is_primary: bool


class PassportIn(BaseModel):
    """Every section is optional so the 5-step form can save as it goes."""

    mother_id: uuid.UUID | None = Field(None, description="Required when an ASHA worker creates/updates a passport")
    about: AboutYou | None = None
    pregnancy: PregnancyIn | None = None
    medical_history: MedicalHistoryIn | None = None
    emergency_contacts: list[EmergencyContactIn] | None = Field(None, description="Replaces the list when given")
    blood_group: str | None = Field(None, pattern=r"^(A|B|AB|O)[+-]$")
    height_cm: float | None = Field(None, gt=100, lt=220)
    pre_pregnancy_weight_kg: float | None = Field(None, gt=25, lt=200)
    preferred_facility_id: uuid.UUID | None = None
    skip_steps: list[Literal["medical_history", "reports", "emergency_contacts"]] | None = None

    @model_validator(mode="after")
    def edd_after_lmp(self):
        p = self.pregnancy
        if p and p.lmp and p.edd and p.edd <= p.lmp:
            raise ValueError("EDD must be after LMP")
        return self


class CompletionStep(BaseModel):
    key: str
    title: str
    title_hi: str
    done: bool
    skipped: bool = False


class Completion(BaseModel):
    profile_strength: int = Field(description="Profile completion (0–100). This is form progress, not a health score.")
    completed_steps: int
    total_steps: int
    next_step: str | None
    steps: list[CompletionStep]
