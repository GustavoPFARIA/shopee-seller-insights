"""Registration, login, token refresh, logout and current-user endpoints."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, rate_limit
from app.models import Membership, Seller, User
from app.schemas import AcceptInvitation, MeResponse, RegisterRequest, ShopRef, TokenResponse
from app.security import create_access_token, hash_password, verify_password
from app.services import members, sessions

router = APIRouter(prefix="/api/auth", tags=["auth"])

login_limit = rate_limit(
    "login",
    lambda: get_settings().login_rate_limit,
    lambda: get_settings().login_rate_window_seconds,
)


REFRESH_COOKIE = "ssi_refresh"
COOKIE_PATH = "/api/auth"
CSRF_HEADER_VALUE = "ssi"


def _set_refresh_cookie(response: Response, value: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        value,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _require_csrf_header(
    x_requested_with: Annotated[str | None, Header()] = None,
) -> None:
    """Cookie-authenticated endpoints need a header cross-site forms cannot send."""
    if x_requested_with != CSRF_HEADER_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header")


def _start_session(db: DbSession, response: Response, user_id: int) -> TokenResponse:
    _set_refresh_cookie(response, sessions.issue(db, user_id).value)
    return TokenResponse(access_token=create_access_token(user_id))


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_limit)],
)
def register(body: RegisterRequest, db: DbSession, response: Response) -> TokenResponse:
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-mail already registered")
    seller = Seller(name=body.shop_name, shop_code=members.new_shop_code(body.shop_name))
    user = User(seller=seller, email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, seller_id=seller.id, role="owner"))
    db.commit()
    return _start_session(db, response, user.id)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_limit)])
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession, response: Response
) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if not verify_password(form.password, user.password_hash if user else None) or user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect e-mail or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _start_session(db, response, user.id)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(_require_csrf_header)],
)
def refresh(
    db: DbSession,
    response: Response,
    ssi_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse | JSONResponse:
    try:
        if not ssi_refresh:
            raise sessions.InvalidRefreshTokenError
        issued = sessions.rotate(db, ssi_refresh)
    except sessions.InvalidRefreshTokenError:
        denied = JSONResponse(
            {"detail": "Session expired, please sign in again"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        _clear_refresh_cookie(denied)
        return denied
    _set_refresh_cookie(response, issued.value)
    return TokenResponse(access_token=create_access_token(issued.user_id))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_csrf_header)],
)
def logout(
    db: DbSession,
    response: Response,
    ssi_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    if ssi_refresh:
        sessions.revoke_by_value(db, ssi_refresh)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser, db: DbSession) -> MeResponse:
    shops = db.execute(
        select(Seller.id, Seller.name, Membership.role)
        .join(Membership, Membership.seller_id == Seller.id)
        .where(Membership.user_id == user.id)
        .order_by(Seller.name, Seller.id)
    ).all()
    return MeResponse(
        email=user.email,
        seller_id=user.seller_id,
        shop_name=user.seller.name,
        role=user.role,
        shops=[ShopRef(id=i, name=n, role=r) for i, n, r in shops],
    )


@router.post(
    "/accept-invite",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_limit)],
)
def accept_invite(body: AcceptInvitation, db: DbSession, response: Response) -> TokenResponse:
    try:
        user, shop_id = members.accept_invitation(db, body.token, body.password)
    except members.MembershipError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    token = _start_session(db, response, user.id)
    token.shop_id = shop_id  # the client switches to the shop that invited them
    return token
