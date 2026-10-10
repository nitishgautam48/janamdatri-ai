"""Create in-app notifications (and optionally SMS) for users."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import NotificationType
from app.database.models import Notification, User
from app.notifications.sms import send_sms


def notify(
    db: Session,
    user_id: uuid.UUID | None,
    type_: NotificationType | str,
    title: str,
    body: str,
    *,
    data: dict | None = None,
    priority: str = "normal",
    sms: bool = False,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Queue a notification. With ``dedupe_key`` the same reminder is never created twice."""
    if user_id is None:
        return None
    if dedupe_key and db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)):
        return None
    channels = ["in_app"]
    if sms:
        user = db.get(User, user_id)
        if user and user.phone:
            send_sms(user.phone, f"{title}: {body}")
            channels.append("sms")
    n = Notification(user_id=user_id, type=str(type_), title=title, body=body, data=data or {},
                     priority=priority, channels=channels, dedupe_key=dedupe_key)
    if dedupe_key:
        try:
            with db.begin_nested():
                db.add(n)
        except IntegrityError:
            return None
    else:
        db.add(n)
    return n
