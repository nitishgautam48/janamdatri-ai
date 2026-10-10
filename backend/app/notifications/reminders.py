"""Scheduled reminders: ANC due, medication, hydration. Idempotent per user per day (dedupe keys)."""
from __future__ import annotations

from datetime import date, timedelta

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
