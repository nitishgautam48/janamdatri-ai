"""Shared string enums. Stored as plain strings in the database for portable migrations."""
from __future__ import annotations

from enum import IntEnum, StrEnum


class Role(StrEnum):
    MOTHER = "mother"
    ASHA = "asha"
    ADMIN = "admin"
    GUEST = "guest"


class MotherStatus(StrEnum):
    PREGNANT = "pregnant"
    POSTNATAL = "postnatal"
    PLANNING = "planning"


class CareLevel(IntEnum):
    """The danger ladder. Higher is more urgent; the worst signal wins."""

    ROUTINE_MONITORING = 0
    NEEDS_ATTENTION = 1
    MEDICAL_REVIEW = 2
    IMMEDIATE_CARE = 3

    @property
    def key(self) -> str:
        return CARE_LEVEL_KEYS[self]

    @property
    def label(self) -> str:
        return CARE_LEVEL_LABELS[self]

    @property
    def label_hi(self) -> str:
        return CARE_LEVEL_LABELS_HI[self]

    @classmethod
    def from_key(cls, key: str) -> CareLevel:
        for lvl, k in CARE_LEVEL_KEYS.items():
            if k == key:
                return lvl
        raise ValueError(f"unknown care level {key!r}")


CARE_LEVEL_KEYS = {
    CareLevel.ROUTINE_MONITORING: "routine_monitoring",
    CareLevel.NEEDS_ATTENTION: "needs_attention",
    CareLevel.MEDICAL_REVIEW: "medical_review",
    CareLevel.IMMEDIATE_CARE: "immediate_care",
}
CARE_LEVEL_LABELS = {
    CareLevel.ROUTINE_MONITORING: "Routine Monitoring",
    CareLevel.NEEDS_ATTENTION: "Needs Attention",
    CareLevel.MEDICAL_REVIEW: "Medical Review Recommended",
    CareLevel.IMMEDIATE_CARE: "Immediate Care Recommended",
}
CARE_LEVEL_LABELS_HI = {
    CareLevel.ROUTINE_MONITORING: "सब ठीक है · अपनी योजना जारी रखें",
    CareLevel.NEEDS_ATTENTION: "ध्यान दें · एक हफ़्ते में दोबारा जाँच",
    CareLevel.MEDICAL_REVIEW: "डॉक्टर से जाँच कराएँ · 2 दिन के अंदर",
    CareLevel.IMMEDIATE_CARE: "तुरंत अस्पताल जाएँ · अभी",
}


class FacilityType(StrEnum):
    SUB_CENTRE = "SC"
    PHC = "PHC"
    CHC = "CHC"
    DISTRICT_HOSPITAL = "DH"


# Severity routing: routine → PHC, medical review → CHC, emergency → district hospital.
CARE_LEVEL_FACILITY = {
    CareLevel.ROUTINE_MONITORING: FacilityType.PHC,
    CareLevel.NEEDS_ATTENTION: FacilityType.PHC,
    CareLevel.MEDICAL_REVIEW: FacilityType.CHC,
    CareLevel.IMMEDIATE_CARE: FacilityType.DISTRICT_HOSPITAL,
}


class ReportType(StrEnum):
    CBC = "cbc"
    PRESCRIPTION = "prescription"
    ULTRASOUND = "ultrasound"
    BLOOD = "blood"
    LAB = "lab"
    OTHER = "other"


class ReportStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class MessageType(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    REPORT = "report"
    LOCATION = "location"
    REFERRAL = "referral"
    SYSTEM = "system"


class NotificationType(StrEnum):
    ANC_DUE = "anc_due"
    MEDICATION = "medication"
    HYDRATION = "hydration"
    ASHA_MESSAGE = "asha_message"
    EMERGENCY = "emergency"
    REFERRAL_UPDATE = "referral_update"
    WELLBEING = "wellbeing"
    SYSTEM = "system"


class EmergencyStatus(StrEnum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ReferralStatus(StrEnum):
    GENERATED = "generated"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Mood(StrEnum):
    GOOD = "good"
    OKAY = "okay"
    LOW = "low"
    WORRIED = "worried"
