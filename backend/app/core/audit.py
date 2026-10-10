from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.database.models import AuditLog


def audit(db: Session, actor_id: uuid.UUID | None, action: str, entity: str, entity_id=None, **details) -> None:
    db.add(AuditLog(actor_user_id=actor_id, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id else None, details=details))
