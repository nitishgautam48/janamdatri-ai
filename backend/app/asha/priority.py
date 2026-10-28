"""Priority engine: how urgently each mother needs her ASHA, as a 0–100 score with the reasons behind it.

Rules only (no AI), so every point can be explained to the ASHA and audited:
  • Emergency triggers set the score directly: open SOS 100, a danger sign or self-harm thought told to the app
    in the last 3 days 95, an Immediate Care result not yet followed up 90.
  • Otherwise points add up from clinical findings (severe anaemia, high BP, high sugar), her pregnancy stage
    (past the due date, due within a week, newborn visit days), risk history (age, previous C-section or
    complications, conditions, twins) and follow-up gaps (late follow-up, ANC overdue, missed tablets,
    unread message, new flagged report, not seen for a month). Without a trigger the score stops at 89.
Bands: Critical ≥ 90 · High 60–89 · Medium 30–59 · Routine < 30. Thresholds follow WHO / MoHFW guidance
(Hb < 7 g/dL severe anaemia; BP ≥ 160/110 severe and ≥ 140/90 hypertension; post-dates from 40 weeks).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.safety import DANGER_SIGNS
from app.core.enums import CareLevel
from app.core.pregnancy import gestational_week
from app.database.models import ConversationMessage, Mother, ReportFinding, Visit

BANDS = [(90, "critical", "Critical", "गंभीर"), (60, "high", "High", "ज़्यादा"), (30, "medium", "Medium", "मध्यम"),
         (0, "routine", "Routine", "सामान्य")]
BAND_RANK = {"critical": 1, "high": 2, "medium": 3, "routine": 4}
TASK_POINTS = {"follow_up": 15, "hbnc": 30, "edd": 20, "anc": 12, "report": 10, "message": 10, "ifa": 8,
               "birth_prep": 6, "not_seen": 6}


def band_for(score: int) -> tuple[str, str, str]:
    for floor, key, en, hi in BANDS:
        if score >= floor:
            return key, en, hi
    return "routine", "Routine", "सामान्य"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def followed_up(result, visit) -> bool:
    """A health-check result counts as followed up by a visit after it, or by the visit that produced it."""
    if result is None or visit is None:
        return False
    return visit.assessment_id == result.assessment_id or _aware(visit.visit_date) >= _aware(result.created_at)


def _latest_values(db: Session, m: Mother, since: datetime) -> dict:
    """Most recent Hb and BP from her visits and reports in the last 60 days, and whether sugar was flagged high."""
    out: dict = {}
    for v in db.scalars(select(Visit).where(Visit.mother_id == m.id, Visit.visit_date >= since).order_by(Visit.visit_date.desc())):
        if "hb" not in out and v.hemoglobin is not None:
            out["hb"] = (v.hemoglobin, _aware(v.visit_date))
        if "bp" not in out and v.bp_systolic:
            out["bp"] = (v.bp_systolic, v.bp_diastolic or 0, _aware(v.visit_date))
    for f in db.scalars(select(ReportFinding).where(ReportFinding.mother_id == m.id, ReportFinding.valid.is_(True),
                                                    ReportFinding.created_at >= since).order_by(ReportFinding.created_at.desc())):
        when = _aware(f.created_at)
        if f.code == "hemoglobin" and f.value_num is not None and ("hb" not in out or when > out["hb"][1]):
            out["hb"] = (f.value_num, when)
        elif f.code == "bp" and f.value_num and ("bp" not in out or when > out["bp"][2]):
            out["bp"] = (int(f.value_num), int(f.value_num2 or 0), when)
        elif f.code.startswith("blood_sugar") and f.flag in ("high", "critical"):
            out["sugar_high"] = True
    return out


def score(db: Session, m: Mother, today: date, tasks: list[dict], result=None, last_visit: Visit | None = None) -> dict:
    """Priority for one mother. ``tasks`` are her work-list tasks for today (worklist.tasks_for)."""
    reasons: list[dict] = []
    trigger = 0

    def add(points: int, kind: str, en: str, hi: str) -> None:
        reasons.append({"points": points, "kind": kind, "text": en, "text_hi": hi})

    kinds = {t["kind"] for t in tasks}
    if "sos" in kinds:
        trigger = 100
        add(100, "sos", "SOS is open", "SOS चालू है")

    now = datetime.now().astimezone()
    since3 = now - timedelta(days=3)
    if m.user_id and not (last_visit and _aware(last_visit.visit_date) >= since3):
        flags = {f for c in db.scalars(select(ConversationMessage).where(
            ConversationMessage.mother_id == m.id, ConversationMessage.role == "user",
            ConversationMessage.created_at >= since3)) for f in (c.safety_flags or [])}
        immediate = sorted(f for f in flags if f in DANGER_SIGNS and DANGER_SIGNS[f][2] == "immediate")
        same_day = sorted(f for f in flags if f in DANGER_SIGNS and DANGER_SIGNS[f][2] != "immediate")
        if "self_harm" in flags:
            trigger = max(trigger, 95)
            add(95, "self_harm", "Spoke of harming herself", "खुद को नुकसान की बात कही")
        if immediate:
            trigger = max(trigger, 95)
            add(95, "danger_sign", "Danger sign: " + ", ".join(DANGER_SIGNS[f][0].lower() for f in immediate[:3]),
                "खतरे का संकेत: " + ", ".join(DANGER_SIGNS[f][1] for f in immediate[:3]))
        if same_day:
            add(35, "danger_sign", "Needs a check today: " + ", ".join(DANGER_SIGNS[f][0].lower() for f in same_day[:3]),
                "आज जाँच चाहिए: " + ", ".join(DANGER_SIGNS[f][1] for f in same_day[:3]))

    if result is not None and not followed_up(result, last_visit):
        lvl = CareLevel(result.level)
        if lvl == CareLevel.IMMEDIATE_CARE:
            trigger = max(trigger, 90)
            add(90, "care_level", "Immediate Care result, not yet followed up", "तुरंत देखभाल का नतीजा, फ़ॉलो-अप बाकी")
        elif lvl == CareLevel.MEDICAL_REVIEW:
            add(45, "care_level", "Medical Review result, not yet followed up", "डॉक्टर जाँच का नतीजा, फ़ॉलो-अप बाकी")
        elif lvl == CareLevel.NEEDS_ATTENTION:
            add(20, "care_level", "Needs Attention result, not yet followed up", "ध्यान दें का नतीजा, फ़ॉलो-अप बाकी")

    vals = _latest_values(db, m, now - timedelta(days=60))
    if "hb" in vals:
        hb = vals["hb"][0]
        if hb < 7:
            add(35, "hb", f"Severe anaemia (Hb {hb:g})", f"गंभीर खून की कमी (Hb {hb:g})")
        elif hb < 9:
            add(22, "hb", f"Moderate anaemia (Hb {hb:g})", f"खून की कमी (Hb {hb:g})")
        elif hb < 11:
            add(10, "hb", f"Mild anaemia (Hb {hb:g})", f"हल्की खून की कमी (Hb {hb:g})")
    if "bp" in vals:
        s, d = vals["bp"][0], vals["bp"][1]
        if s >= 160 or d >= 110:
            add(40, "bp", f"Severe high BP ({s}/{d})", f"बहुत ज़्यादा BP ({s}/{d})")
        elif s >= 140 or d >= 90:
            add(25, "bp", f"High BP ({s}/{d})", f"ज़्यादा BP ({s}/{d})")
    if vals.get("sugar_high"):
        add(12, "sugar", "High blood sugar in a report", "रिपोर्ट में शुगर ज़्यादा")

    preg = m.pregnancy
    delivered = bool(preg and preg.delivery_date)
    week = gestational_week(preg.lmp, today) if preg and preg.lmp and not delivered else None
    if week is not None and week >= 40:
        add(20, "post_dates", f"Past the due date (week {week})", f"प्रसव की तारीख निकल गई (हफ़्ता {week})")
    if delivered and 0 <= (today - preg.delivery_date).days <= 7:
        add(15, "newborn", "First week after birth", "जन्म के बाद पहला हफ़्ता")
    if m.age is not None and (m.age < 18 or m.age > 35):
        add(10, "age", f"Age {m.age}", f"उम्र {m.age}")
    if preg and preg.multiple_pregnancy:
        add(12, "twins", "Twins / multiple pregnancy", "जुड़वाँ गर्भ")
    hist = m.medical_history
    if hist:
        if hist.previous_c_section:
            add(8, "history", "Previous C-section", "पहले ऑपरेशन से प्रसव")
        if hist.past_complications:
            add(10, "history", "Complications in a past pregnancy", "पिछली गर्भावस्था में परेशानी")
        conds = {c.lower() for c in hist.conditions or []}
        pts = min(20, 10 * len(conds & {"hypertension", "diabetes"}) + 5 * len(conds & {"anemia", "anaemia", "thyroid"}))
        if pts:
            add(pts, "conditions", "Known conditions: " + ", ".join(sorted(conds)), "पहले की बीमारियाँ: " + ", ".join(sorted(conds)))

    for t in tasks:
        k = t["kind"]
        if k in TASK_POINTS:
            pts = TASK_POINTS[k]
            if k == "follow_up" and "late" in t["title"]:
                pts = 25
            if k == "anc" and "overdue" in t["title"]:
                pts = 20
            add(pts, k, t["title"], t["title_hi"])

    reasons.sort(key=lambda r: -r["points"])
    total = trigger or min(89, sum(r["points"] for r in reasons))
    key, en, hi = band_for(total)
    return {"score": total, "band": key, "band_label": en, "band_label_hi": hi, "emergency": trigger > 0,
            "reasons": reasons[:6]}
