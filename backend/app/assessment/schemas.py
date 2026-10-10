from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.assessment.symptoms import SYMPTOMS


class Vitals(BaseModel):
    bp_systolic: int | None = Field(None, ge=60, le=260)
    bp_diastolic: int | None = Field(None, ge=30, le=180)
    weight_kg: float | None = Field(None, ge=25, le=200)
    hemoglobin: float | None = Field(None, ge=2, le=20, description="g/dL")
    blood_sugar_fasting: float | None = Field(None, ge=20, le=600)
    blood_sugar_random: float | None = Field(None, ge=20, le=600)
    blood_sugar_ogtt_2h: float | None = Field(None, ge=20, le=600)
    temperature_c: float | None = Field(None, ge=33, le=43)
    pulse: int | None = Field(None, ge=30, le=220)
    urine_protein: str | None = Field(None, pattern=r"^(nil|trace|1\+|2\+|3\+|4\+)$")
    fetal_heart_rate: int | None = Field(None, ge=50, le=250)

    @model_validator(mode="after")
    def bp_pair(self):
        if (self.bp_systolic is None) != (self.bp_diastolic is None):
            raise ValueError("Give both systolic and diastolic BP, or neither")
        if self.bp_systolic is not None and self.bp_diastolic >= self.bp_systolic:
            raise ValueError("Diastolic BP must be lower than systolic")
        return self


class HistoryAnswers(BaseModel):
    taking_ifa: bool | None = Field(None, description="Are you taking iron tablets daily?")
    bleeding_this_week: bool | None = Field(None, description="Any bleeding this week?")
    baby_moving_normally: bool | None = Field(None, description="Is baby moving as usual?")


class AssessmentIn(BaseModel):
    mother_id: uuid.UUID | None = Field(None, description="Required for ASHA workers")
    vitals: Vitals = Vitals()
    symptoms: list[str] = []
    history: HistoryAnswers = HistoryAnswers()
    epds: list[int] | None = Field(None, description="10 EPDS answers, each 0–3 (optional)")
    use_latest_reports: bool = Field(True, description="Fill missing Hb / sugar / BP from recent report findings")

    @field_validator("symptoms")
    @classmethod
    def known_symptoms(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SYMPTOMS]
        if bad:
            raise ValueError(f"Unknown symptoms {bad}")
        return sorted(set(v))

    @field_validator("epds")
    @classmethod
    def epds_shape(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and (len(v) != 10 or any(i not in (0, 1, 2, 3) for i in v)):
            raise ValueError("EPDS needs exactly 10 answers scored 0–3")
        return v


class Finding(BaseModel):
    text: str
    text_hi: str | None = None
    level: str | None = None


class CareLevelOut(BaseModel):
    key: str
    label: str
    label_hi: str
    rank: int = Field(description="Position on the 4-step ladder (0 routine … 3 immediate). Not a score.")


class AssessmentResultOut(BaseModel):
    assessment_id: uuid.UUID
    mother_id: uuid.UUID
    date: datetime
    pregnancy_week: int | None
    result: CareLevelOut
    ladder: list[str]
    what_we_noticed: list[Finding]
    possible_concerns: list[Finding]
    recommendations: list[Finding]
    referral_suggestion: str | None
    follow_up: str | None
    nearest_facility: dict | None
    asha_informed: bool
    inputs_used: dict


class SymptomOption(BaseModel):
    code: str
    label: str
    label_hi: str
