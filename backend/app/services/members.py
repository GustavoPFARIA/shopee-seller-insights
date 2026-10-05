"""Shop membership: invitations, role changes and removal."""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Invitation, Membership, Seller, User
from app.security import hash_password, verify_password
from app.services import sessions

INVITE_TTL = timedelta(hours=72)


class MembershipError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _lock_shop(db: Session, seller_id: int) -> None:
    """Serialize membership changes of one shop (prevents losing the last owner
    when two owners demote or remove each other at the same time)."""
    db.execute(select(Seller.id).where(Seller.id == seller_id).with_for_update())


def _owner_count(db: Session, seller_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.seller_id == seller_id, Membership.role == "owner")
        )
        or 0
    )


def create_invitation(
    db: Session, *, seller_id: int, invited_by: int, email: str, role: str
) -> tuple[Invitation, str]:
    email = email.lower()
    # Deliberately no check against existing accounts here: answering "already
    # registered" would let any owner probe which e-mails use the platform. A
    # conflict is reported to the invitee when they accept.
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


def accept_invitation(db: Session, token: str, password: str) -> tuple[User, int]:
    """Join the inviting shop.

    New e-mail: creates the account with `password`. Existing account: `password`
    must be that account's current password (the invite then adds a membership).
    """
    now = datetime.now(UTC)
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == _hash(token)).with_for_update()
    )
    if invitation is None or invitation.accepted_at is not None or invitation.expires_at <= now:
        raise MembershipError(400, "This invitation is invalid or has expired")
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user is None:
        user = User(
            seller_id=invitation.seller_id,
            email=invitation.email,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
    else:
        if not verify_password(password, user.password_hash):
            # Same message whatever is wrong, to not confirm which e-mails exist.
            raise MembershipError(401, "Incorrect password for this account")
        already = db.get(Membership, {"user_id": user.id, "seller_id": invitation.seller_id})
        if already is not None:
            raise MembershipError(409, "You are already a member of this shop")
    db.add(Membership(user_id=user.id, seller_id=invitation.seller_id, role=invitation.role))
    invitation.accepted_at = now
    db.commit()
    return user, invitation.seller_id


def change_role(db: Session, *, seller_id: int, user_id: int, role: str) -> Membership:
    _lock_shop(db, seller_id)
    member = _get_member(db, seller_id, user_id)
    if member.role == "owner" and role != "owner" and _owner_count(db, seller_id) <= 1:
        raise MembershipError(409, "A shop must keep at least one owner")
    member.role = role
    db.commit()
    return member


def remove_member(db: Session, *, seller_id: int, user_id: int) -> None:
    """Remove a user from one shop. A user left without any shop is deleted."""
    _lock_shop(db, seller_id)
    member = _get_member(db, seller_id, user_id)
    if member.role == "owner" and _owner_count(db, seller_id) <= 1:
        raise MembershipError(409, "A shop must keep at least one owner")
    user = member.user
    db.delete(member)
    db.flush()
    other = db.scalar(
        select(Membership.seller_id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.seller_id)
        .limit(1)
    )
    if other is None:
        sessions.revoke_all_for_user(db, user.id)
        db.delete(user)
    elif user.seller_id == seller_id:
        user.seller_id = other  # their default shop is gone: open another one
    db.commit()


def create_shop(db: Session, *, user_id: int, name: str) -> Seller:
    """A new shop owned by the given user."""
    seller = Seller(name=name, shop_code=new_shop_code(name))
    db.add(seller)
    db.flush()
    db.add(Membership(user_id=user_id, seller_id=seller.id, role="owner"))
    db.commit()
    return seller


def new_shop_code(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "shop"
    return f"{slug}-{secrets.token_hex(4)}"


def _get_member(db: Session, seller_id: int, user_id: int) -> Membership:
    member = db.get(Membership, {"user_id": user_id, "seller_id": seller_id})
    if member is None:
        raise MembershipError(404, "Member not found")
    return member
