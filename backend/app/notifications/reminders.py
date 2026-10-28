"""Scheduled reminders: ANC due, medication, hydration. Idempotent per user per day (dedupe keys)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import NotificationType
from app.core.pregnancy import gestational_week
from app.database.models import AshaWorker, Mother, PregnancyProfile


def _wants(m: Mother, kind: str) -> bool:
    prefs = (m.user.preferences or {}).get("notifications", {}) if m.user else {}
    return prefs.get(kind, True)


def anc_reminders(db: Session, today: date | None = None) -> int:
    from app.notifications.service import notify

    today = today or date.today()
    n = 0
    rows = db.scalars(select(Mother).join(PregnancyProfile, PregnancyProfile.mother_id == Mother.id).where(
        PregnancyProfile.next_anc_date.between(today, today + timedelta(days=3)))).all()
    for m in rows:
        d = m.pregnancy.next_anc_date
        when = "today" if d == today else "tomorrow" if d == today + timedelta(days=1) else f"on {d:%a, %d %b}"
        if m.user_id and _wants(m, "anc"):
            if notify(db, m.user_id, NotificationType.ANC_DUE, "ANC check-up " + when,
                      "Bring your MCP card and reports. Free ANC with a doctor on the 9th of every month (PMSMA).",
                      data={"date": d.isoformat()}, sms=d == today + timedelta(days=1),
                      dedupe_key=f"anc:{m.id}:{d}:{today}"):
                n += 1
        if m.assigned_asha_id:
            asha = db.get(AshaWorker, m.assigned_asha_id)
            if asha and asha.user_id:
                notify(db, asha.user_id, NotificationType.ANC_DUE, f"ANC due {when}: {m.name or 'mother'}",
                       f"Week {gestational_week(m.pregnancy.lmp) or '-'}", data={"mother_id": str(m.id)},
                       dedupe_key=f"anc-asha:{m.id}:{d}:{today}")
    return n


def daily_wellness_reminders(db: Session, today: date | None = None, slot: str = "midday") -> int:
    """slot: morning (hydration) | midday (iron after lunch) | night (calcium)."""
    from app.notifications.service import notify

    today = today or date.today()
    n = 0
    for m in db.scalars(select(Mother).where(Mother.status == "pregnant", Mother.user_id.is_not(None))).all():
        week = gestational_week(m.pregnancy.lmp) if m.pregnancy else None
        hi = m.language == "hi"
        if slot == "morning" and _wants(m, "hydration"):
            ok = notify(db, m.user_id, NotificationType.HYDRATION, "पानी पीना न भूलें" if hi else "Hydration reminder",
                        "आज 8 गिलास पानी का लक्ष्य।" if hi else "Aim for 8 glasses of water today.",
                        dedupe_key=f"hyd:{m.id}:{today}")
        elif slot == "midday" and _wants(m, "medication") and week is not None:
            title = ("आयरन की गोली" if hi else "Iron tablet") if week >= 14 else ("फ़ोलिक एसिड" if hi else "Folic acid tablet")
            ok = notify(db, m.user_id, NotificationType.MEDICATION, title,
                        "खाने के बाद नींबू पानी से लें, चाय से नहीं।" if hi else "Take it after lunch with lemon water, not tea.",
                        dedupe_key=f"med-mid:{m.id}:{today}")
        elif slot == "night" and _wants(m, "medication") and week is not None and week >= 14:
            ok = notify(db, m.user_id, NotificationType.MEDICATION, "कैल्शियम की गोली" if hi else "Calcium tablets",
                        "रात को 2 गोली लें।" if hi else "Take 2 calcium tablets tonight.", dedupe_key=f"med-night:{m.id}:{today}")
        else:
            ok = None
        n += 1 if ok else 0
    return n


# --- ASHA alerts ------------------------------------------------------------------------------------------------

def asha_morning_summary(db: Session, today: date | None = None) -> int:
    """7:30 AM: each ASHA gets today's work list in one line (and an SMS when something is urgent)."""
    from app.asha.worklist import worklist
    from app.notifications.service import notify

    today = today or date.today()
    n = 0
    for asha in db.scalars(select(AshaWorker).where(AshaWorker.user_id.is_not(None), AshaWorker.available.is_(True))).all():
        wl = worklist(db, asha, today=today)
        todo = [i for i in wl["items"] if not i["done"]]
        if not todo:
            continue
        urgent = [i for i in todo if i["priority"] == 1]
        first = ", ".join((i["name"] or "").split(" ")[0] for i in (urgent or todo)[:3])
        title = f"Today: {len(todo)} mother{'s' if len(todo) != 1 else ''} to see" + (f", {len(urgent)} urgent" if urgent else "")
        body = (f"Urgent first: {first}. " if urgent else f"Start with {first}. ") + \
            f"About {wl['summary']['total_km']} km. Open the work list in Janamdatri."
        if notify(db, asha.user_id, NotificationType.SYSTEM, title, body, data={"screen": "ashaDash"},
                  priority="high" if urgent else "normal", sms=bool(urgent), dedupe_key=f"wl:{asha.id}:{today}"):
            n += 1
    return n


def visit_tomorrow_reminders(db: Session, today: date | None = None) -> int:
    """Evening: tell a mother her ASHA's follow-up visit is tomorrow (so she is at home)."""
    from app.database.models import Visit
    from app.notifications.service import notify

    today = today or date.today()
    tomorrow = today + timedelta(days=1)
    n = 0
    for v in db.scalars(select(Visit).where(Visit.next_visit_date == tomorrow)).all():
        m = db.get(Mother, v.mother_id)
        latest = db.scalar(select(Visit).where(Visit.mother_id == v.mother_id).order_by(Visit.visit_date.desc()))
        if not m or not m.user_id or latest.id != v.id:
            continue
        asha = db.get(AshaWorker, v.asha_id)
        name = asha.name.split(" ")[0] if asha else "Your ASHA"
        hi = m.language == "hi"
        if notify(db, m.user_id, NotificationType.ASHA_MESSAGE,
                  f"{name} दीदी कल आएँगी" if hi else f"{name} will visit you tomorrow",
                  "कृपया घर पर रहें और अपना MCP कार्ड पास रखें।" if hi else "Please be at home and keep your MCP card ready.",
                  sms=True, dedupe_key=f"visit-tmrw:{v.id}"):
            n += 1
    return n


def quiet_mother_alerts(db: Session, today: date | None = None, days: int = 7) -> int:
    """Weekly: alert the ASHA about pregnant mothers who have not used the app or replied for ``days`` days."""
    from app.database.models import CompanionPlan, ConversationMessage, Message, User, Visit
    from app.notifications.service import notify

    today = today or date.today()
    since = datetime.combine(today - timedelta(days=days), datetime.min.time()).astimezone()
    week = today.isocalendar()
    n = 0
    for m in db.scalars(select(Mother).where(Mother.status == "pregnant", Mother.user_id.is_not(None),
                                             Mother.assigned_asha_id.is_not(None))).all():
        user = db.get(User, m.user_id)
        recent = [user.last_login_at if user else None,
                  db.scalar(select(ConversationMessage.created_at).where(ConversationMessage.mother_id == m.id)
                            .order_by(ConversationMessage.created_at.desc())),
                  db.scalar(select(Message.created_at).where(Message.mother_id == m.id, Message.sender_role == "mother")
                            .order_by(Message.created_at.desc())),
                  db.scalar(select(Visit.visit_date).where(Visit.mother_id == m.id).order_by(Visit.visit_date.desc()))]
        plan = db.scalar(select(CompanionPlan.plan_date).where(CompanionPlan.mother_id == m.id).order_by(CompanionPlan.plan_date.desc()))
        active = any(t and (t if t.tzinfo else t.astimezone()) >= since for t in recent) or (plan and plan >= since.date())
        if active or (m.created_at and (m.created_at if m.created_at.tzinfo else m.created_at.astimezone()) >= since):
            continue
        asha = db.get(AshaWorker, m.assigned_asha_id)
        if asha and asha.user_id and notify(
                db, asha.user_id, NotificationType.WELLBEING, f"No news from {m.name or 'a mother'} for {days} days",
                "She has not opened the app, replied or been visited this week. A quick call or visit may help.",
                data={"mother_id": str(m.id)}, dedupe_key=f"quiet:{m.id}:{week[0]}-{week[1]}"):
            n += 1
    return n
