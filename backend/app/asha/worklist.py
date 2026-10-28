"""Smart daily work list for an ASHA: who needs her today, why, and in which order to go.

Each mother gets today's tasks from her records (rules only, explainable):
  1 urgent   – SOS open; Immediate Care / Medical Review not yet followed up; danger sign told to the app (3 days)
  2 today    – scheduled follow-up due; newborn home visit day (HBNC 3, 7, 14, 21, 28, 42); EDD within 7 days
  3 soon     – ANC due or overdue; new report with a flagged value; unread message from her
  4 routine  – iron tablet missed for 3 days in her daily plan; birth preparedness from week 36; not seen for 30 days
The list is ordered by priority, and inside each priority by the shortest walk from the previous stop
(nearest-neighbour from her current position), with straight-line distance turned into an estimated road time.
Marking a mother done (or recording a visit today) moves her to the bottom.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asha.priority import BAND_RANK, followed_up
from app.asha.priority import score as priority_score
from app.asha.service import mother_row, mothers_for_asha_query
from app.core.enums import CareLevel, EmergencyStatus
from app.core.pregnancy import gestational_week
from app.database.models import (
    AshaWorker,
    AssessmentResult,
    CompanionPlan,
    ConversationMessage,
    EmergencyEvent,
    Message,
    Mother,
    ReportFinding,
    Visit,
)
from app.gis.service import haversine_km, mother_location, travel_estimate

HBNC_DAYS = (3, 7, 14, 21, 28, 42)
WALK_KMPH = 4.5


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _task(kind: str, priority: int, title: str, title_hi: str) -> dict:
    return {"kind": kind, "priority": priority, "title": title, "title_hi": title_hi}


def tasks_for(db: Session, m: Mother, today: date) -> list[dict]:
    out: list[dict] = []
    preg = m.pregnancy
    week = gestational_week(preg.lmp, today) if preg and preg.lmp and not (preg.delivery_date) else None
    visits = db.scalars(select(Visit).where(Visit.mother_id == m.id).order_by(Visit.visit_date.desc()).limit(5)).all()
    last_visit = visits[0] if visits else None
    last_visit_day = _aware(last_visit.visit_date).date() if last_visit else None

    sos = db.scalar(select(EmergencyEvent).where(EmergencyEvent.mother_id == m.id, EmergencyEvent.status.in_(
        [EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED])))
    if sos:
        out.append(_task("sos", 1, "SOS is open: call her now", "SOS चालू है: अभी फ़ोन करें"))

    res = db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == m.id).order_by(AssessmentResult.created_at.desc()))
    if res and res.level >= CareLevel.MEDICAL_REVIEW and not followed_up(res, last_visit):
        lvl = CareLevel(res.level)
        out.append(_task("care_level", 1, f"{lvl.label}: check on her and the referral",
                         "तुरंत देखभाल: उनसे मिलें और रेफ़रल देखें" if lvl == CareLevel.IMMEDIATE_CARE else "डॉक्टर जाँच: मिलें और रेफ़रल देखें"))
    elif res and res.level == CareLevel.NEEDS_ATTENTION and not followed_up(res, last_visit):
        out.append(_task("care_level", 3, "Needs attention: follow up on her health check", "ध्यान दें: स्वास्थ्य जाँच का फ़ॉलो-अप"))

    if m.user_id:
        since = datetime.now().astimezone() - timedelta(days=3)
        flagged = db.scalars(select(ConversationMessage).where(
            ConversationMessage.mother_id == m.id, ConversationMessage.role == "user",
            ConversationMessage.created_at >= since)).all()
        signs = sorted({f for c in flagged for f in (c.safety_flags or [])})
        if signs and not (last_visit and _aware(last_visit.visit_date) >= since):
            from app.ai.safety import DANGER_SIGNS

            en = ", ".join(DANGER_SIGNS[c][0].lower() if c in DANGER_SIGNS else "thoughts of self-harm" if c == "self_harm"
                           else c.replace("_", " ") for c in signs[:3])
            hi = ", ".join(DANGER_SIGNS[c][1] if c in DANGER_SIGNS else "खुद को नुकसान के विचार" if c == "self_harm"
                           else c.replace("_", " ") for c in signs[:3])
            out.append(_task("danger_sign", 1, f"Told the app about: {en}", f"ऐप में बताया: {hi}"))

    if last_visit and last_visit.next_visit_date and last_visit.next_visit_date <= today:
        late = (today - last_visit.next_visit_date).days
        out.append(_task("follow_up", 2, "Follow-up visit due" + (f" ({late} days late)" if late else " today"),
                         "फ़ॉलो-अप विज़िट" + (f" ({late} दिन देर)" if late else " आज")))

    if preg and preg.delivery_date:
        day = (today - preg.delivery_date).days
        due = [d for d in HBNC_DAYS if d <= day < d + 2]
        if due and not (last_visit_day and last_visit_day >= preg.delivery_date + timedelta(days=due[0])):
            out.append(_task("hbnc", 2, f"Newborn home visit, day {due[0]} after birth", f"नवजात गृह भेंट, जन्म के बाद दिन {due[0]}"))
    elif preg and preg.edd and 0 <= (preg.edd - today).days <= 7:
        out.append(_task("edd", 2, f"Due date in {(preg.edd - today).days} days: check delivery plan and transport",
                         f"प्रसव {(preg.edd - today).days} दिन में: प्रसव योजना और वाहन तय करें"))

    if preg and not preg.delivery_date:
        if preg.next_anc_date and preg.next_anc_date <= today + timedelta(days=3):
            late = (today - preg.next_anc_date).days
            out.append(_task("anc", 3, f"ANC check-up overdue by {late} days" if late > 0 else "ANC check-up due",
                             f"ANC जाँच {late} दिन से बाकी" if late > 0 else "ANC जाँच होनी है"))
        elif week is not None and week > 12 and (preg.anc_visits_completed or 0) == 0:
            out.append(_task("anc", 3, f"No ANC check-up yet (week {week})", f"अभी तक कोई ANC जाँच नहीं (हफ़्ता {week})"))

    week_ago = datetime.now().astimezone() - timedelta(days=7)
    flagged = db.scalars(select(ReportFinding).where(ReportFinding.mother_id == m.id, ReportFinding.valid.is_(True),
                                                     ReportFinding.flag.in_(["low", "high", "critical"]),
                                                     ReportFinding.created_at >= week_ago)).all()
    if flagged and not (last_visit and _aware(last_visit.visit_date) >= _aware(max(f.created_at for f in flagged))):
        f = flagged[0]
        out.append(_task("report", 3, f"New report: {f.label} {f.flag}", f"नई रिपोर्ट: {f.label} {f.flag}"))

    if m.assigned_asha_id:
        unread = db.scalars(select(Message).where(Message.mother_id == m.id, Message.sender_role == "mother",
                                                  Message.read_at.is_(None)).order_by(Message.created_at.desc())).first()
        if unread:
            text = (unread.body or unread.transcript or unread.message_type or "")[:50]
            out.append(_task("message", 3, f"Unread message: “{text}”", f"नया संदेश: “{text}”"))

    plans = db.scalars(select(CompanionPlan).where(CompanionPlan.mother_id == m.id,
                                                   CompanionPlan.plan_date >= today - timedelta(days=3),
                                                   CompanionPlan.plan_date < today)).all()
    if len(plans) >= 3 and all(not next((t["done"] for t in p.tasks if t["id"] == "goal-iron"), True) for p in plans):
        out.append(_task("ifa", 4, "Iron tablet not ticked for 3 days: ask why", "3 दिन से आयरन की गोली नहीं ली: कारण पूछें"))

    if week is not None and week >= 36 and not (last_visit_day and last_visit_day >= today - timedelta(days=7)):
        out.append(_task("birth_prep", 4, f"Week {week}: birth preparedness, hospital bag, 102 number",
                         f"हफ़्ता {week}: प्रसव की तैयारी, अस्पताल का थैला, 102 नंबर"))
    if not out and (not last_visit_day or last_visit_day <= today - timedelta(days=30)):
        days = (today - last_visit_day).days if last_visit_day else None
        out.append(_task("not_seen", 4, f"Not visited for {days} days" if days else "Not visited yet",
                         f"{days} दिन से भेंट नहीं" if days else "अभी तक भेंट नहीं"))
    return sorted(out, key=lambda t: t["priority"])


def prioritise(db: Session, m: Mother, today: date, tasks: list[dict] | None = None) -> dict:
    """Priority engine score for one mother (see app.asha.priority)."""
    if tasks is None:
        tasks = tasks_for(db, m, today)
    res = db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == m.id).order_by(AssessmentResult.created_at.desc()))
    last = db.scalar(select(Visit).where(Visit.mother_id == m.id).order_by(Visit.visit_date.desc()))
    return priority_score(db, m, today, tasks, res, last)


def _done_today(asha: AshaWorker, today: date) -> set[str]:
    prefs = (asha.user.preferences or {}) if asha.user else {}
    wl = prefs.get("worklist") or {}
    return set(wl.get("done", [])) if wl.get("date") == today.isoformat() else set()


def mark_done(db: Session, asha: AshaWorker, mother_id: str, done: bool, today: date) -> None:
    if not asha.user:
        return
    prefs = dict(asha.user.preferences or {})
    ids = _done_today(asha, today)
    (ids.add if done else ids.discard)(mother_id)
    prefs["worklist"] = {"date": today.isoformat(), "done": sorted(ids)}
    asha.user.preferences = prefs
    db.commit()


def worklist(db: Session, asha: AshaWorker, lat: float | None = None, lon: float | None = None,
             today: date | None = None) -> dict:
    today = today or date.today()
    start = (lat, lon) if lat is not None and lon is not None else (
        (asha.latitude, asha.longitude) if asha.latitude is not None else None)
    done_ids = _done_today(asha, today)
    items = []
    for m in db.scalars(mothers_for_asha_query(asha)).all():
        tasks = tasks_for(db, m, today)
        pr = prioritise(db, m, today, tasks)
        if not tasks and pr["score"] < 30:
            continue
        if not tasks:  # high score from her records alone (e.g. low Hb, high BP): ask the ASHA to check on her
            top = pr["reasons"][0]
            tasks = [_task("check", 3, f"Check on her: {top['text']}", f"हाल पूछें: {top['text_hi']}")]
        visited = db.scalar(select(Visit.id).where(Visit.mother_id == m.id, Visit.visit_date >= datetime.combine(
            today, datetime.min.time()).astimezone()))
        loc = mother_location(db, m)
        row = mother_row(db, m, asha)
        items.append({**row, "tasks": tasks, "priority": BAND_RANK[pr["band"]], "priority_score": pr["score"],
                      "priority_band": pr["band"], "priority_label": pr["band_label"], "priority_label_hi": pr["band_label_hi"],
                      "priority_reasons": pr["reasons"], "emergency": pr["emergency"],
                      "done": str(m.id) in done_ids or visited is not None,
                      "latitude": loc.latitude if loc else None, "longitude": loc.longitude if loc else None})

    # Order: by priority band; Critical by score (emergencies first), other bands by the nearest next stop.
    ordered, here = [], start
    for prio in sorted({i["priority"] for i in items}):
        group = [i for i in items if i["priority"] == prio and not i["done"]]
        if prio == 1:
            group.sort(key=lambda i: -i["priority_score"])
        while group:
            nxt = group[0] if prio == 1 else min(group, key=lambda i: haversine_km(here[0], here[1], i["latitude"], i["longitude"])
                                                 if here and i["latitude"] is not None else 1e9)
            group.remove(nxt)
            if here and nxt["latitude"] is not None:
                km, minutes = travel_estimate(haversine_km(here[0], here[1], nxt["latitude"], nxt["longitude"]))
                nxt["leg_km"], nxt["leg_minutes"] = km, minutes
                nxt["leg_walk_minutes"] = round(km / WALK_KMPH * 60)
                here = (nxt["latitude"], nxt["longitude"])
            ordered.append(nxt)
    ordered += [i for i in items if i["done"]]
    for n, i in enumerate(ordered, 1):
        i["order"] = n
    todo = [i for i in ordered if not i["done"]]
    total_km = round(sum(i.get("leg_km", 0) for i in todo), 1)
    return {
        "date": today, "start": {"latitude": start[0], "longitude": start[1]} if start else None,
        "summary": {"stops": len(todo), "done": len(ordered) - len(todo), "urgent": sum(1 for i in todo if i["priority"] == 1),
                    "bands": {b: sum(1 for i in todo if i["priority_band"] == b) for b in BAND_RANK},
                    "total_km": total_km, "drive_minutes": sum(i.get("leg_minutes", 0) for i in todo),
                    "walk_minutes": sum(i.get("leg_walk_minutes", 0) for i in todo)},
        "items": ordered,
    }
