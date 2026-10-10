"""Pregnancy date maths (Naegele's rule) and trimester helpers."""
from __future__ import annotations

from datetime import date, timedelta

GESTATION_DAYS = 280


def edd_from_lmp(lmp: date) -> date:
    return lmp + timedelta(days=GESTATION_DAYS)


def lmp_from_edd(edd: date) -> date:
    return edd - timedelta(days=GESTATION_DAYS)


def gestational_week(lmp: date | None, on: date | None = None) -> int | None:
    if not lmp:
        return None
    days = ((on or date.today()) - lmp).days
    if days < 0:
        return None
    return min(days // 7, 42)


def gestational_days(lmp: date | None, on: date | None = None) -> int | None:
    if not lmp:
        return None
    return max(((on or date.today()) - lmp).days, 0)


def trimester(week: int | None) -> int | None:
    if week is None:
        return None
    if week < 14:
        return 1
    if week < 28:
        return 2
    return 3


TRIMESTER_LABELS = {1: ("First Trimester", "पहली तिमाही"), 2: ("Second Trimester", "दूसरी तिमाही"), 3: ("Third Trimester", "तीसरी तिमाही")}

# Government of India ANC schedule (MCP card / PMSMA): 4 contacts minimum.
ANC_SCHEDULE = [
    {"visit": 1, "from_week": 0, "to_week": 12, "label": "ANC 1 · registration", "checks": ["Registration & MCP card", "Blood group, Hb, urine", "HIV, syphilis, hepatitis B screening", "Start folic acid"]},
    {"visit": 2, "from_week": 14, "to_week": 26, "label": "ANC 2", "checks": ["BP, weight, Hb", "Start IFA & calcium", "TT/Td dose", "Glucose test (OGTT) at 24–28 weeks", "Anomaly ultrasound 18–22 weeks"]},
    {"visit": 3, "from_week": 28, "to_week": 34, "label": "ANC 3", "checks": ["BP, weight, Hb", "Fetal movement & growth", "Birth preparedness plan"]},
    {"visit": 4, "from_week": 36, "to_week": 40, "label": "ANC 4", "checks": ["BP, Hb, fetal position", "Delivery facility & transport plan (102)", "JSY/JSSK documents ready"]},
]


def next_anc(week: int | None, visits_completed: int = 0) -> dict | None:
    if week is None:
        return None
    for item in ANC_SCHEDULE:
        if item["visit"] > visits_completed and week <= item["to_week"]:
            return item
    return None


def anc_target_date(lmp: date | None, item: dict | None, today: date | None = None) -> date | None:
    """Earliest sensible date for the given ANC visit (not before today)."""
    if not lmp or not item:
        return None
    today = today or date.today()
    target = lmp + timedelta(weeks=item["from_week"])
    return max(target, today)
