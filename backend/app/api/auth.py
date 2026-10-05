"""Registration, login and current-user endpoints."""

import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, rate_limit
from app.models import Seller, User
from app.schemas import MeResponse, RegisterRequest, TokenResponse
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

login_limit = rate_limit(
    "login",
    lambda: get_settings().login_rate_limit,
    lambda: get_settings().login_rate_window_seconds,
)


def _shop_code(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "shop"
    return f"{slug}-{secrets.token_hex(4)}"


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_limit)],
)
def register(body: RegisterRequest, db: DbSession) -> TokenResponse:
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-mail already registered")
    seller = Seller(name=body.shop_name, shop_code=_shop_code(body.shop_name))
    user = User(seller=seller, email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_limit)])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if not verify_password(form.password, user.password_hash if user else None) or user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect e-mail or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(email=user.email, seller_id=user.seller_id, shop_name=user.seller.name)
