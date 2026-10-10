from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser
from app.core.exceptions import NotFound
from app.database.base import utcnow
from app.database.models import Notification
from app.notifications.schemas import NotificationList, NotificationOut

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationList)
def list_notifications(db: DB, user: CurrentUser, unread_only: bool = False,
                       limit: int = Query(50, le=200)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(Notification)
                       .where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    return NotificationList(unread=unread or 0, items=items)


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, db: DB, user: CurrentUser):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise NotFound("Notification not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return n


@router.post("/read-all")
def mark_all_read(db: DB, user: CurrentUser):
    res = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                     .values(read_at=utcnow()))
    db.commit()
    return {"updated": res.rowcount}
