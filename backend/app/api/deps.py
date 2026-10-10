"""Shared FastAPI dependencies: authentication, role guards and record access checks."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.core.exceptions import Forbidden, NotFound, Unauthorized
from app.core.security import decode_token
from app.database.models import AshaWorker, Mother, User
from app.database.session import get_db

bearer = HTTPBearer(auto_error=False)
DB = Annotated[Session, Depends(get_db)]


@dataclass
class Principal:
    """The caller. Guests have no database row (``user`` is None)."""

    role: Role
    user: User | None
    token: dict

    @property
    def id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def is_guest(self) -> bool:
        return self.role == Role.GUEST


def _principal_from_token(db: Session, token: str) -> Principal:
    payload = decode_token(token)
    role = Role(payload["role"])
    if role == Role.GUEST:
        return Principal(role=role, user=None, token=payload)
    user = db.get(User, uuid.UUID(payload["sub"]))
    if not user or not user.is_active:
        raise Unauthorized("Account not found or disabled", code="inactive_user")
    return Principal(role=Role(user.role), user=user, token=payload)


def get_principal(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> Principal:
    if not creds:
        raise Unauthorized("Login required", code="not_authenticated")
    return _principal_from_token(db, creds.credentials)


def get_optional_principal(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> Principal | None:
    if not creds:
        return None
    try:
        return _principal_from_token(db, creds.credentials)
    except Unauthorized:
        return None


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


def require_roles(*roles: Role):
    def _dep(p: CurrentPrincipal) -> Principal:
        if p.role not in roles:
            raise Forbidden("You do not have access to this feature", code="role_forbidden")
        return p

    return _dep


def require_user(p: CurrentPrincipal) -> User:
    if p.user is None:
        raise Forbidden("Create a free account to use this feature", code="guest_forbidden")
    return p.user


CurrentUser = Annotated[User, Depends(require_user)]
AdminUser = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


def get_current_mother(db: DB, p: CurrentPrincipal) -> Mother:
    if p.role != Role.MOTHER or p.user is None:
        raise Forbidden("This feature is for mothers", code="role_forbidden")
    mother = db.scalar(select(Mother).where(Mother.user_id == p.user.id))
    if not mother:
        raise NotFound("Mother profile not found", code="mother_not_found")
    return mother


def get_current_asha(db: DB, p: CurrentPrincipal) -> AshaWorker:
    if p.role != Role.ASHA or p.user is None:
        raise Forbidden("This feature is for ASHA workers", code="role_forbidden")
    asha = db.scalar(select(AshaWorker).where(AshaWorker.user_id == p.user.id))
    if not asha:
        raise NotFound("ASHA profile not found", code="asha_not_found")
    if not asha.verified:
        raise Forbidden("ASHA verification pending", code="asha_unverified")
    return asha


CurrentMother = Annotated[Mother, Depends(get_current_mother)]
CurrentAsha = Annotated[AshaWorker, Depends(get_current_asha)]


def asha_can_access(asha: AshaWorker, mother: Mother) -> bool:
    return asha.id in (mother.assigned_asha_id, mother.backup_asha_id, mother.registered_by_asha_id)


def resolve_mother(db: Session, p: Principal, mother_id: uuid.UUID | None) -> Mother:
    """The mother a request acts on.

    Mothers always act on themselves. ASHA workers must pass ``mother_id`` for a mother
    assigned to them (primary or backup). Admins may act on any mother.
    """
    if p.role == Role.MOTHER:
        mother = get_current_mother(db, p)
        if mother_id and mother_id != mother.id:
            raise Forbidden("You can only access your own records")
        return mother
    if p.role in (Role.ASHA, Role.ADMIN):
        if not mother_id:
            raise NotFound("mother_id is required", code="mother_id_required")
        mother = db.get(Mother, mother_id)
        if not mother:
            raise NotFound("Mother not found", code="mother_not_found")
        if p.role == Role.ASHA and not asha_can_access(get_current_asha(db, p), mother):
            raise Forbidden("This mother is not assigned to you")
        return mother
    raise Forbidden("Create a free account to use this feature", code="guest_forbidden")


def mothers_for_asha_query(asha: AshaWorker):
    return select(Mother).where(
        or_(Mother.assigned_asha_id == asha.id, Mother.backup_asha_id == asha.id)
    )
