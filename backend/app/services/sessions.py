"""Refresh-token sessions with rotation and reuse detection.

A refresh token is a random 256-bit value given to the browser in an HttpOnly
cookie; only its SHA-256 is stored. Each use rotates it. Presenting a token that
was already rotated means it leaked, so the whole family is revoked.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RefreshToken


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class IssuedToken:
    value: str
    user_id: int


class InvalidRefreshTokenError(Exception):
    pass


def issue(db: Session, user_id: int, family_id: str | None = None) -> IssuedToken:
    value = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id or secrets.token_hex(16),
            token_hash=_hash(value),
            created_at=now,
            expires_at=now + timedelta(days=get_settings().refresh_token_days),
        )
    )
    db.commit()
    return IssuedToken(value=value, user_id=user_id)


def rotate(db: Session, value: str) -> IssuedToken:
    now = datetime.now(UTC)
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(value)).with_for_update()
    )
    if record is None:
        raise InvalidRefreshTokenError
    if record.revoked_at is not None:
        # Reuse of a rotated token: assume theft and kill the whole session family.
        revoke_family(db, record.family_id)
        raise InvalidRefreshTokenError
    if record.expires_at <= now:
        raise InvalidRefreshTokenError
    record.revoked_at = now
    db.flush()
    return issue(db, record.user_id, record.family_id)


def revoke_family(db: Session, family_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def revoke_by_value(db: Session, value: str) -> None:
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(value)))
    if record is not None:
        revoke_family(db, record.family_id)


def revoke_all_for_user(db: Session, user_id: int) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()
