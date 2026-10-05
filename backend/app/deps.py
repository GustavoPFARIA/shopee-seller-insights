"""Shared FastAPI dependencies: DB session, current user, rate limits."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.ratelimit import limiter
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
        if not limiter.allow(f"{scope}:{client}", limit(), window()):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, try again later",
            )

    return dependency
