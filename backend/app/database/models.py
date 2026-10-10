"""ORM models for every Janamdatri-AI table.

Kept in one module so cross-table relationships resolve without import cycles.
Enumerations are stored as strings (see ``app.core.enums``).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, Timestamped, UUIDPk, utcnow


def _fk(target: str, *, nullable: bool = True, ondelete: str = "SET NULL", index: bool = True):
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(UUIDPk, Timestamped, Base):
    __tablename__ = "users"

    role: Mapped[str] = mapped_column(String(16), index=True)
    phone: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    pin_hash: Mapped[str | None] = mapped_column(String(255))  # shared/family phone mode
    full_name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(8), default="hi")
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mother: Mapped[Mother | None] = relationship(back_populates="user", uselist=False)
    asha: Mapped[AshaWorker | None] = relationship(back_populates="user", uselist=False)


class AshaWorker(UUIDPk, Timestamped, Base):
    __tablename__ = "asha_workers"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(16))
    asha_reg_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    rch_id: Mapped[str | None] = mapped_column(String(32))
    awc_code: Mapped[str | None] = mapped_column(String(32))
    sub_centre_code: Mapped[str | None] = mapped_column(String(32), index=True)
    sub_centre_name: Mapped[str | None] = mapped_column(String(120))
    sector_code: Mapped[str | None] = mapped_column(String(32), index=True)
    village: Mapped[str | None] = mapped_column(String(120), index=True)
    villages_served: Mapped[list] = mapped_column(JSON, default=list)
    district: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(6), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    max_caseload: Mapped[int] = mapped_column(Integer, default=60)
    anm_name: Mapped[str | None] = mapped_column(String(120))
    anm_phone: Mapped[str | None] = mapped_column(String(16))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="asha")


class Mother(UUIDPk, Timestamped, Base):
    __tablename__ = "mothers"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(120))
    age: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(8), default="hi")
    village: Mapped[str | None] = mapped_column(String(120), index=True)
    district: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(6), index=True)
    address: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    sub_centre_code: Mapped[str | None] = mapped_column(String(32))
    sector_code: Mapped[str | None] = mapped_column(String(32))
    rch_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pregnant")
    assigned_asha_id: Mapped[uuid.UUID | None] = _fk("asha_workers.id")
    backup_asha_id: Mapped[uuid.UUID | None] = _fk("asha_workers.id")
    registered_by_asha_id: Mapped[uuid.UUID | None] = _fk("asha_workers.id", index=False)
    assignment_reason: Mapped[str | None] = mapped_column(String(255))
    preferred_facility_id: Mapped[uuid.UUID | None] = _fk("facilities.id", index=False)
    shared_phone_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User | None] = relationship(back_populates="mother")
    assigned_asha: Mapped[AshaWorker | None] = relationship(foreign_keys=[assigned_asha_id])
    backup_asha: Mapped[AshaWorker | None] = relationship(foreign_keys=[backup_asha_id])
    passport: Mapped[HealthPassport | None] = relationship(back_populates="mother", uselist=False, cascade="all, delete-orphan")
    pregnancy: Mapped[PregnancyProfile | None] = relationship(back_populates="mother", uselist=False, cascade="all, delete-orphan")
    medical_history: Mapped[MedicalHistory | None] = relationship(back_populates="mother", uselist=False, cascade="all, delete-orphan")
    emergency_contacts: Mapped[list[EmergencyContact]] = relationship(
        back_populates="mother", cascade="all, delete-orphan", order_by="EmergencyContact.created_at"
    )


# ---------------------------------------------------------------------------
# Maternal Health Passport
# ---------------------------------------------------------------------------
class HealthPassport(UUIDPk, Timestamped, Base):
    __tablename__ = "health_passports"

    mother_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mothers.id", ondelete="CASCADE"), unique=True
    )
    passport_number: Mapped[str] = mapped_column(String(20), unique=True)
    blood_group: Mapped[str | None] = mapped_column(String(4))
    height_cm: Mapped[float | None] = mapped_column(Float)
    pre_pregnancy_weight_kg: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    skipped_steps: Mapped[list] = mapped_column(JSON, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)

    mother: Mapped[Mother] = relationship(back_populates="passport")


class PregnancyProfile(UUIDPk, Timestamped, Base):
    __tablename__ = "pregnancy_profiles"

    mother_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mothers.id", ondelete="CASCADE"), unique=True
    )
    lmp: Mapped[date | None] = mapped_column(Date)
    edd: Mapped[date | None] = mapped_column(Date)
    edd_source: Mapped[str] = mapped_column(String(16), default="lmp")  # lmp | ultrasound | manual
    first_pregnancy: Mapped[bool | None] = mapped_column(Boolean)
    gravida: Mapped[int | None] = mapped_column(Integer)
    parity: Mapped[int | None] = mapped_column(Integer)
    multiple_pregnancy: Mapped[bool] = mapped_column(Boolean, default=False)
    anc_visits_completed: Mapped[int] = mapped_column(Integer, default=0)
    next_anc_date: Mapped[date | None] = mapped_column(Date, index=True)
    delivery_date: Mapped[date | None] = mapped_column(Date)

    mother: Mapped[Mother] = relationship(back_populates="pregnancy")


class MedicalHistory(UUIDPk, Timestamped, Base):
    __tablename__ = "medical_history"

    mother_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mothers.id", ondelete="CASCADE"), unique=True
    )
    conditions: Mapped[list] = mapped_column(JSON, default=list)  # diabetes, anemia, hypertension, thyroid…
    past_complications: Mapped[bool | None] = mapped_column(Boolean)
    complication_details: Mapped[list] = mapped_column(JSON, default=list)
    previous_c_section: Mapped[bool] = mapped_column(Boolean, default=False)
    allergies: Mapped[list] = mapped_column(JSON, default=list)
    current_medications: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)

    mother: Mapped[Mother] = relationship(back_populates="medical_history")


class EmergencyContact(UUIDPk, Timestamped, Base):
    __tablename__ = "emergency_contacts"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    name: Mapped[str] = mapped_column(String(120))
    relation: Mapped[str | None] = mapped_column(String(40))
    phone: Mapped[str] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    mother: Mapped[Mother] = relationship(back_populates="emergency_contacts")


# ---------------------------------------------------------------------------
# Reports & OCR
# ---------------------------------------------------------------------------
class Report(UUIDPk, Timestamped, Base):
    __tablename__ = "reports"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    uploaded_by_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    report_type: Mapped[str] = mapped_column(String(16), default="other")
    title: Mapped[str | None] = mapped_column(String(120))
    file_path: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    report_date: Mapped[date | None] = mapped_column(Date)
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(255))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    findings: Mapped[list[ReportFinding]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportFinding.created_at"
    )


class ReportFinding(UUIDPk, Base):
    __tablename__ = "report_findings"

    report_id: Mapped[uuid.UUID] = _fk("reports.id", nullable=False, ondelete="CASCADE")
    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(String(24))  # lab_value | vital | medicine | diagnosis | recommendation
    code: Mapped[str] = mapped_column(String(40), index=True)  # hemoglobin, bp, blood_sugar_fasting, …
    label: Mapped[str] = mapped_column(String(120))
    value_text: Mapped[str | None] = mapped_column(String(255))
    value_num: Mapped[float | None] = mapped_column(Float)
    value_num2: Mapped[float | None] = mapped_column(Float)  # e.g. diastolic BP
    unit: Mapped[str | None] = mapped_column(String(24))
    flag: Mapped[str | None] = mapped_column(String(16))  # low | normal | high | critical
    reference_range: Mapped[str | None] = mapped_column(String(60))
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_note: Mapped[str | None] = mapped_column(String(255))
    observed_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="findings")


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------
class Assessment(UUIDPk, Timestamped, Base):
    __tablename__ = "assessments"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    performed_by_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    source: Mapped[str] = mapped_column(String(16), default="self")  # self | asha | visit
    pregnancy_week: Mapped[int | None] = mapped_column(Integer)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)

    result: Mapped[AssessmentResult | None] = relationship(
        back_populates="assessment", uselist=False, cascade="all, delete-orphan"
    )


class AssessmentResult(UUIDPk, Base):
    __tablename__ = "assessment_results"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessments.id", ondelete="CASCADE"), unique=True
    )
    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    category: Mapped[str] = mapped_column(String(24), index=True)
    level: Mapped[int] = mapped_column(Integer)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    what_we_noticed: Mapped[list] = mapped_column(JSON, default=list)
    possible_concerns: Mapped[list] = mapped_column(JSON, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    referral_suggestion: Mapped[str | None] = mapped_column(Text)
    facility_type: Mapped[str | None] = mapped_column(String(8))
    facility: Mapped[dict | None] = mapped_column(JSON)
    follow_up: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="result")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
class ConversationMessage(UUIDPk, Base):
    __tablename__ = "conversation_history"

    user_id: Mapped[uuid.UUID] = _fk("users.id", nullable=False, ondelete="CASCADE")
    mother_id: Mapped[uuid.UUID | None] = _fk("mothers.id", ondelete="CASCADE")
    channel: Mapped[str] = mapped_column(String(16), index=True)  # assistant | emotional
    thread_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(8))
    input_mode: Mapped[str] = mapped_column(String(8), default="text")
    safety_flags: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EmotionalSession(UUIDPk, Timestamped, Base):
    __tablename__ = "emotional_sessions"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    user_id: Mapped[uuid.UUID] = _fk("users.id", nullable=False, ondelete="CASCADE")
    mood_checkin: Mapped[str | None] = mapped_column(String(16))
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # internal, -1..1, never shown as a score
    signals: Mapped[dict] = mapped_column(JSON, default=dict)  # stress/isolation/anxiety/distress counts
    concern_level: Mapped[str] = mapped_column(String(16), default="none")  # none | mild | elevated | high
    themes: Mapped[list] = mapped_column(JSON, default=list)
    memory_summary: Mapped[str | None] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    asha_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Companion
# ---------------------------------------------------------------------------
class CompanionPlan(UUIDPk, Timestamped, Base):
    __tablename__ = "companion_plans"
    __table_args__ = (UniqueConstraint("mother_id", "plan_date", name="uq_companion_plan_day"),)

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    pregnancy_week: Mapped[int | None] = mapped_column(Integer)
    tasks: Mapped[list] = mapped_column(JSON, default=list)
    meals: Mapped[list] = mapped_column(JSON, default=list)
    medications: Mapped[list] = mapped_column(JSON, default=list)
    hydration_goal: Mapped[int] = mapped_column(Integer, default=8)
    hydration_count: Mapped[int] = mapped_column(Integer, default=0)
    anc_preparation: Mapped[dict] = mapped_column(JSON, default=dict)
    weekly_insights: Mapped[list] = mapped_column(JSON, default=list)
    tip: Mapped[str | None] = mapped_column(Text)
    tip_hi: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(16), default="rules")


# ---------------------------------------------------------------------------
# Mother ↔ ASHA messaging
# ---------------------------------------------------------------------------
class Message(UUIDPk, Base):
    __tablename__ = "messages"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    asha_id: Mapped[uuid.UUID] = _fk("asha_workers.id", nullable=False, ondelete="CASCADE")
    sender_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    sender_role: Mapped[str] = mapped_column(String(16))
    message_type: Mapped[str] = mapped_column(String(16), default="text")
    body: Mapped[str | None] = mapped_column(Text)
    attachment_path: Mapped[str | None] = mapped_column(String(255))
    attachment_content_type: Mapped[str | None] = mapped_column(String(80))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    transcript: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    report_id: Mapped[uuid.UUID | None] = _fk("reports.id", index=False)
    referral_id: Mapped[uuid.UUID | None] = _fk("referrals.id", index=False)
    quick_action: Mapped[str | None] = mapped_column(String(40))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------------------
# GIS
# ---------------------------------------------------------------------------
class Facility(UUIDPk, Timestamped, Base):
    __tablename__ = "facilities"

    name: Mapped[str] = mapped_column(String(160))
    facility_type: Mapped[str] = mapped_column(String(8), index=True)  # SC | PHC | CHC | DH | HOSP | CLINIC
    address: Mapped[str | None] = mapped_column(String(255))
    village: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(80), index=True)
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(6), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_24x7: Mapped[bool] = mapped_column(Boolean, default=False)
    has_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    has_blood_bank: Mapped[bool] = mapped_column(Boolean, default=False)
    services: Mapped[list] = mapped_column(JSON, default=list)
    opd_hours: Mapped[str | None] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="registry", server_default="registry")  # registry | osm
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # e.g. osm:node/123


class GISMapping(UUIDPk, Timestamped, Base):
    """Locality → coordinates and health-system codes (pincode / village gazetteer)."""

    __tablename__ = "gis_mappings"
    __table_args__ = (UniqueConstraint("pincode", "village", name="uq_gis_pincode_village"),)

    pincode: Mapped[str] = mapped_column(String(6), index=True)
    village: Mapped[str] = mapped_column(String(120), index=True)
    block: Mapped[str | None] = mapped_column(String(80))
    district: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    sub_centre_code: Mapped[str | None] = mapped_column(String(32))
    sector_code: Mapped[str | None] = mapped_column(String(32))


# ---------------------------------------------------------------------------
# Referrals, visits, notifications, emergencies, analytics, audit
# ---------------------------------------------------------------------------
class Referral(UUIDPk, Timestamped, Base):
    __tablename__ = "referrals"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    assessment_id: Mapped[uuid.UUID | None] = _fk("assessments.id")
    created_by_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    referral_number: Mapped[str] = mapped_column(String(24), unique=True)
    care_level: Mapped[str] = mapped_column(String(24))
    facility_id: Mapped[uuid.UUID | None] = _fk("facilities.id")
    facility_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    patient_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    pregnancy_week: Mapped[int | None] = mapped_column(Integer)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="generated")
    share_token: Mapped[str] = mapped_column(String(64), unique=True)
    share_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    facility_notified: Mapped[bool] = mapped_column(Boolean, default=False)


class Visit(UUIDPk, Timestamped, Base):
    __tablename__ = "visits"

    mother_id: Mapped[uuid.UUID] = _fk("mothers.id", nullable=False, ondelete="CASCADE")
    asha_id: Mapped[uuid.UUID] = _fk("asha_workers.id", nullable=False, ondelete="CASCADE")
    visit_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    visit_type: Mapped[str] = mapped_column(String(24), default="home")  # home | anc | postnatal | followup
    bp_systolic: Mapped[int | None] = mapped_column(Integer)
    bp_diastolic: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    hemoglobin: Mapped[float | None] = mapped_column(Float)
    symptoms: Mapped[list] = mapped_column(JSON, default=list)
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    voice_note_path: Mapped[str | None] = mapped_column(String(255))
    next_visit_date: Mapped[date | None] = mapped_column(Date, index=True)
    assessment_id: Mapped[uuid.UUID | None] = _fk("assessments.id", index=False)


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = _fk("users.id", nullable=False, ondelete="CASCADE")
    type: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[str] = mapped_column(String(8), default="normal")  # low | normal | high | urgent
    channels: Mapped[list] = mapped_column(JSON, default=list)  # in_app | sms | push
    dedupe_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EmergencyEvent(UUIDPk, Timestamped, Base):
    __tablename__ = "emergency_events"

    mother_id: Mapped[uuid.UUID | None] = _fk("mothers.id")
    user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    public_token: Mapped[str] = mapped_column(String(64), unique=True)  # lets anonymous callers update location
    emergency_type: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    caller_phone: Mapped[str | None] = mapped_column(String(16))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_text: Mapped[str | None] = mapped_column(String(255))
    pincode: Mapped[str | None] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    escalation_chain: Mapped[list] = mapped_column(JSON, default=list)
    nearest_facility: Mapped[dict | None] = mapped_column(JSON)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = _fk("users.id", index=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalyticsSnapshot(UUIDPk, Base):
    __tablename__ = "analytics"
    __table_args__ = (UniqueConstraint("snapshot_date", "scope", "scope_key", name="uq_analytics_snapshot"),)

    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="global")  # global | district | sub_centre
    scope_key: Mapped[str] = mapped_column(String(64), default="all")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(UUIDPk, Base):
    __tablename__ = "audit_logs"

    actor_user_id: Mapped[uuid.UUID | None] = _fk("users.id")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
