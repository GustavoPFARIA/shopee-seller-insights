"""Shared FastAPI dependencies: DB session, current user, rate limits."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import ratelimit
from app.db import get_db
from app.models import User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

DbSession = Annotated[Session, Depends(get_db)]

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    user_id = decode_access_token(token)
    if user_id is None:
        raise _credentials_error
    user = db.get(User, user_id)
    if user is None:
        raise _credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


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


def require_role(*roles: str) -> Callable[[User], User]:
    """Dependency factory: the current user must have one of `roles` in their shop."""

    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not allow this action",
            )
        return user

    return dependency


# Owners and managers can change data; viewers are read-only.
EditorUser = Annotated[User, Depends(require_role("owner", "manager"))]
OwnerUser = Annotated[User, Depends(require_role("owner"))]
