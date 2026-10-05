"""Shared FastAPI dependencies: DB session, current user, rate limits."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ratelimit
from app.db import get_db
from app.models import Membership, Seller, User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

DbSession = Annotated[Session, Depends(get_db)]

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Actor:
    """The signed-in user acting in one shop, with their role in that shop.

    Every endpoint scopes its queries by `seller_id` (the active shop) and checks
    `role`; both come from a membership row read on each request, never from the
    client or the token, so access changes apply immediately.
    """

    id: int
    email: str
    seller_id: int
    role: str
    seller: Seller


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
    x_shop_id: Annotated[int | None, Header(gt=0)] = None,
) -> Actor:
    user_id = decode_access_token(token)
    if user_id is None:
        raise _credentials_error
    user = db.get(User, user_id)
    if user is None:
        raise _credentials_error
    shop_id = x_shop_id or user.seller_id
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.seller_id == shop_id)
    )
    if membership is None and x_shop_id is None:
        # Default shop no longer accessible: fall back to any shop the user belongs to.
        membership = db.scalar(
            select(Membership).where(Membership.user_id == user.id).order_by(Membership.seller_id)
        )
    if membership is None:
        if x_shop_id is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not a member of this shop")
        raise _credentials_error
    return Actor(
        id=user.id,
        email=user.email,
        seller_id=membership.seller_id,
        role=membership.role,
        seller=membership.seller,
    )


CurrentUser = Annotated[Actor, Depends(get_current_user)]


def rate_limit(
    scope: str, limit: Callable[[], int], window: Callable[[], int]
) -> Callable[[Request], None]:
    """Dependency factory limiting requests per client IP for a given scope."""

    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not ratelimit.allow(f"{scope}:{client}", limit(), window()):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, try again later",
            )

    return dependency


def require_role(*roles: str) -> Callable[[Actor], Actor]:
    """Dependency factory: the current user must have one of `roles` in the active shop."""

    def dependency(user: CurrentUser) -> Actor:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not allow this action",
            )
        return user

    return dependency


# Owners and managers can change data; viewers are read-only.
EditorUser = Annotated[Actor, Depends(require_role("owner", "manager"))]
OwnerUser = Annotated[Actor, Depends(require_role("owner"))]
