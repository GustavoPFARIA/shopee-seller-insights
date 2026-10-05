"""Shop membership: invitations, role changes and removal."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Invitation, User
from app.security import hash_password
from app.services import sessions

INVITE_TTL = timedelta(hours=72)


class MembershipError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _owner_count(db: Session, seller_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.seller_id == seller_id, User.role == "owner")
        )
        or 0
    )


def create_invitation(
    db: Session, *, seller_id: int, invited_by: int, email: str, role: str
) -> tuple[Invitation, str]:
    email = email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise MembershipError(409, "This e-mail already has an account")
    # A new invite replaces any pending one for the same e-mail in this shop.
    db.execute(
        delete(Invitation).where(
            Invitation.seller_id == seller_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
        )
    )
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    invitation = Invitation(
        seller_id=seller_id,
        email=email,
        role=role,
        token_hash=_hash(token),
        invited_by=invited_by,
        created_at=now,
        expires_at=now + INVITE_TTL,
    )
    db.add(invitation)
    db.commit()
    return invitation, token


def accept_invitation(db: Session, token: str, password: str) -> User:
    now = datetime.now(UTC)
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == _hash(token)).with_for_update()
    )
    if invitation is None or invitation.accepted_at is not None or invitation.expires_at <= now:
        raise MembershipError(400, "This invitation is invalid or has expired")
    if db.scalar(select(User.id).where(User.email == invitation.email)) is not None:
        raise MembershipError(409, "This e-mail already has an account")
    user = User(
        seller_id=invitation.seller_id,
        email=invitation.email,
        password_hash=hash_password(password),
        role=invitation.role,
    )
    invitation.accepted_at = now
    db.add(user)
    db.commit()
    return user


def change_role(db: Session, *, seller_id: int, user_id: int, role: str) -> User:
    member = _get_member(db, seller_id, user_id)
    if member.role == "owner" and role != "owner" and _owner_count(db, seller_id) <= 1:
        raise MembershipError(409, "A shop must keep at least one owner")
    member.role = role
    db.commit()
    return member


def remove_member(db: Session, *, seller_id: int, user_id: int) -> None:
    member = _get_member(db, seller_id, user_id)
    if member.role == "owner" and _owner_count(db, seller_id) <= 1:
        raise MembershipError(409, "A shop must keep at least one owner")
    sessions.revoke_all_for_user(db, member.id)
    db.delete(member)
    db.commit()


def _get_member(db: Session, seller_id: int, user_id: int) -> User:
    member = db.scalar(select(User).where(User.id == user_id, User.seller_id == seller_id))
    if member is None:
        raise MembershipError(404, "Member not found")
    return member
