"""Mother ↔ ASHA secure messaging. Phone numbers are never exposed; calls go through a relay."""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import Principal, asha_can_access, get_current_asha, get_current_mother
from app.core.cache import set_json
from app.core.config import settings
from app.core.enums import MessageType, NotificationType, Role
from app.core.exceptions import Forbidden, NotFound, ValidationFailed
from app.database.base import utcnow
from app.database.models import AshaWorker, Message, Mother, Referral, Report
from app.notifications.service import notify

QUICK_ACTIONS = {
    "need_anc_help": ("Need ANC Help", "ANC में मदद चाहिए"),
    "request_home_visit": ("Request Home Visit", "घर आने का अनुरोध"),
    "upload_report": ("Upload Report", "रिपोर्ट भेजें"),
    "nutrition_question": ("Nutrition Question", "खान-पान का सवाल"),
    "emergency_help": ("Emergency Help", "आपातकालीन मदद"),
}


def conversation(db: Session, p: Principal, mother_id: uuid.UUID | None) -> tuple[Mother, AshaWorker]:
    if p.role == Role.MOTHER:
        mother = get_current_mother(db, p)
        asha_id = mother.assigned_asha_id or mother.backup_asha_id
        if not asha_id:
            raise NotFound("No ASHA worker assigned yet. Complete your passport location to get matched.",
                           code="asha_not_assigned")
        return mother, db.get(AshaWorker, asha_id)
    if p.role == Role.ASHA:
        asha = get_current_asha(db, p)
        if not mother_id:
            raise ValidationFailed("mother_id is required", code="mother_id_required")
        mother = db.get(Mother, mother_id)
        if not mother or not asha_can_access(asha, mother):
            raise Forbidden("This mother is not assigned to you")
        return mother, asha
    raise Forbidden("Messaging is between a mother and her ASHA worker")


def _recipient_user_id(p: Principal, mother: Mother, asha: AshaWorker):
    return asha.user_id if p.role == Role.MOTHER else mother.user_id


def send(db: Session, p: Principal, mother: Mother, asha: AshaWorker, *, message_type: MessageType,
         body: str | None = None, **fields) -> Message:
    quick = fields.get("quick_action")
    if quick and quick not in QUICK_ACTIONS:
        raise ValidationFailed("Unknown quick action", details={"allowed": list(QUICK_ACTIONS)})
    if quick and not body:
        body = QUICK_ACTIONS[quick][0]
    if fields.get("report_id"):
        r = db.get(Report, fields["report_id"])
        if not r or r.mother_id != mother.id:
            raise NotFound("Report not found")
    if fields.get("referral_id"):
        ref = db.get(Referral, fields["referral_id"])
        if not ref or ref.mother_id != mother.id:
            raise NotFound("Referral not found")
    m = Message(mother_id=mother.id, asha_id=asha.id, sender_user_id=p.id, sender_role=p.role,
                message_type=message_type.value, body=body, **fields)
    db.add(m)
    if p.role == Role.ASHA:
        asha.last_active_at = utcnow()
    recipient = _recipient_user_id(p, mother, asha)
    sender_name = (mother.name or "Mother") if p.role == Role.MOTHER else asha.name
    preview = body if message_type == MessageType.TEXT and body else {
        MessageType.VOICE: "Voice note", MessageType.IMAGE: "Photo", MessageType.REPORT: "Shared a report",
        MessageType.LOCATION: "Shared location", MessageType.REFERRAL: "Referral summary"}.get(message_type, body or "")
    urgent = quick == "emergency_help"
    notify(db, recipient, NotificationType.EMERGENCY if urgent else NotificationType.ASHA_MESSAGE,
           f"{'URGENT · ' if urgent else ''}Message from {sender_name}", (preview or "")[:140],
           data={"mother_id": str(mother.id), "kind": "message"}, priority="urgent" if urgent else "normal",
           sms=urgent)
    return m


def message_dict(m: Message, viewer_role: str) -> dict:
    d = {
        "id": m.id, "type": m.message_type, "from": "me" if m.sender_role == viewer_role else m.sender_role,
        "sender_role": m.sender_role, "body": m.body, "quick_action": m.quick_action, "created_at": m.created_at,
        "read": m.read_at is not None,
    }
    if m.attachment_path:
        d["attachment_url"] = f"/api/v1/messages/{m.id}/attachment"
        d["attachment_content_type"] = m.attachment_content_type
    if m.message_type == MessageType.VOICE:
        d.update(duration_seconds=m.duration_seconds, transcript=m.transcript)
    if m.latitude is not None:
        d["location"] = {"latitude": m.latitude, "longitude": m.longitude,
                         "maps_url": f"https://www.google.com/maps?q={m.latitude},{m.longitude}"}
    if m.report_id:
        d["report_id"] = m.report_id
    if m.referral_id:
        d["referral_id"] = m.referral_id
    return d


def history(db: Session, p: Principal, mother: Mother, asha: AshaWorker, limit: int, before=None) -> list[Message]:
    q = select(Message).where(Message.mother_id == mother.id, Message.asha_id == asha.id)
    if before:
        q = q.where(Message.created_at < before)
    rows = list(reversed(db.scalars(q.order_by(Message.created_at.desc()).limit(limit)).all()))
    db.execute(update(Message).where(Message.mother_id == mother.id, Message.asha_id == asha.id,
                                     Message.sender_role != p.role, Message.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return rows


def unread_count(db: Session, mother_id, asha_id, viewer_role: str) -> int:
    return db.scalar(select(func.count()).select_from(Message).where(
        Message.mother_id == mother_id, Message.asha_id == asha_id, Message.sender_role != viewer_role,
        Message.read_at.is_(None))) or 0


def presence(asha: AshaWorker) -> str:
    if not asha.last_active_at:
        return "offline"
    last = asha.last_active_at if asha.last_active_at.tzinfo else asha.last_active_at.replace(tzinfo=utcnow().tzinfo)
    mins = int((utcnow() - last).total_seconds() // 60)
    if mins < 2:
        return "active now"
    if mins < 60:
        return f"active {mins} min ago"
    return f"active {mins // 60} h ago" if mins < 1440 else "offline"


def start_relay_call(db: Session, p: Principal, mother: Mother, asha: AshaWorker) -> dict:
    """Number-masked call: both sides dial the relay number and enter a one-time code; the telephony
    provider bridges them. Neither party ever sees the other's number."""
    callee_phone = asha.phone if p.role == Role.MOTHER else (mother.user.phone if mother.user else None)
    if not callee_phone:
        raise ValidationFailed("This person has no reachable phone yet")
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    ttl = 15 * 60
    set_json(f"relay:{code}", {"caller_user_id": str(p.id), "callee": callee_phone, "mother_id": str(mother.id),
                               "asha_id": str(asha.id)}, ex=ttl)
    notify(db, _recipient_user_id(p, mother, asha), NotificationType.ASHA_MESSAGE, "Incoming secure call",
           f"Call {settings.relay_number} and enter code {code} to connect.",
           data={"relay_number": settings.relay_number, "code": code}, priority="high")
    db.commit()
    return {"relay_number": settings.relay_number, "code": code, "expires_at": utcnow() + timedelta(seconds=ttl),
            "note": "Dial the number and enter the code. Your phone number stays private."}
