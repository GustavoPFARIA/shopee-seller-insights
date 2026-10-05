"""Shopee Open Platform connection and synchronization endpoints."""

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, EditorUser, OwnerUser, rate_limit
from app.integrations import shopee_sync
from app.integrations.shopee_client import ShopeeApiError, ShopeeClient
from app.models import ShopeeConnection, SyncRun
from app.schemas import AuthorizationUrl, ShopeeStatus, SyncRunOut

router = APIRouter(prefix="/api/shopee", tags=["shopee"])

callback_limit = rate_limit("shopee-callback", lambda: 20, lambda: 3600)
sync_limit = rate_limit(
    "shopee-sync",
    lambda: get_settings().upload_rate_limit,
    lambda: get_settings().upload_rate_window_seconds,
)
OAUTH_COOKIE = "ssi_oauth_state"
CALLBACK_ERRORS = {"invalid_state", "shop_already_connected", "exchange_failed", "missing_params"}


def _client() -> ShopeeClient:
    try:
        return shopee_sync.make_client()
    except shopee_sync.ShopeeNotConfiguredError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Shopee integration is not configured on this server",
        ) from None


@router.get("/status", response_model=ShopeeStatus)
def get_status(user: CurrentUser, db: DbSession) -> ShopeeStatus:
    settings = get_settings()
    conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.seller_id == user.seller_id))
    runs = db.scalars(
        select(SyncRun)
        .where(SyncRun.seller_id == user.seller_id)
        .order_by(SyncRun.started_at.desc())
        .limit(10)
    )
    return ShopeeStatus(
        enabled=settings.shopee_enabled,
        connected=conn is not None,
        shop_id=conn.shop_id if conn else None,
        connected_at=conn.created_at if conn else None,
        orders_synced_until=conn.orders_synced_until if conn else None,
        sync_interval_minutes=settings.shopee_sync_interval_minutes,
        runs=[SyncRunOut.model_validate(r) for r in runs],
    )


@router.post("/connect", response_model=AuthorizationUrl)
def connect(owner: OwnerUser, db: DbSession, response: Response) -> AuthorizationUrl:
    url, state = shopee_sync.start_authorization(
        db, _client(), seller_id=owner.seller_id, user_id=owner.id
    )
    # Bind the flow to this browser: the callback must come back with this cookie.
    # Otherwise an attacker could send their own authorization link to a victim
    # seller and get the victim's shop linked to the attacker's account.
    response.set_cookie(
        OAUTH_COOKIE,
        state,
        max_age=int(shopee_sync.STATE_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",  # sent on the top-level redirect back from Shopee
        path="/api/shopee/callback",
    )
    return AuthorizationUrl(authorization_url=url)


@router.get("/callback", include_in_schema=False, dependencies=[Depends(callback_limit)])
def callback(
    db: DbSession,
    state: Annotated[str | None, Query(max_length=200)] = None,
    code: Annotated[str | None, Query(max_length=500)] = None,
    shop_id: Annotated[int | None, Query(gt=0)] = None,
    ssi_oauth_state: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    """Shopee redirects the browser here after the owner authorizes the shop.

    There is no bearer token on this request: the single-use `state` identifies the
    shop owner who started the flow. Errors redirect with a fixed code only.
    """
    client = _client()
    outcome = "connected"
    if not state or not code or shop_id is None:
        outcome = "error:missing_params"
    elif not ssi_oauth_state or not secrets.compare_digest(ssi_oauth_state, state):
        outcome = "error:invalid_state"  # started in another browser (or cookie expired)
    else:
        try:
            shopee_sync.complete_authorization(db, client, state=state, code=code, shop_id=shop_id)
        except shopee_sync.OAuthError as exc:
            reason = str(exc)
            outcome = f"error:{reason if reason in CALLBACK_ERRORS else 'exchange_failed'}"
        except ShopeeApiError:
            outcome = "error:exchange_failed"
    redirect = RedirectResponse(f"/#shopee={outcome}", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(OAUTH_COOKIE, path="/api/shopee/callback")
    return redirect


@router.post(
    "/sync",
    response_model=SyncRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(sync_limit)],
)
def sync_now(user: EditorUser, db: DbSession) -> SyncRun:
    """Queue a sync; the worker runs it in the background (poll /status for progress).

    A first sync can mean thousands of API calls, far longer than an HTTP request
    should take, so it never runs inside the request.
    """
    _client()  # 503 if the integration is not configured
    try:
        return shopee_sync.request_sync(db, user.seller_id)
    except shopee_sync.OAuthError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Shopee shop connected") from None


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(owner: OwnerUser, db: DbSession) -> Response:
    if not shopee_sync.disconnect(db, owner.seller_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Shopee shop connected")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


MAX_PUSH_BYTES = 64 * 1024


@router.post("/push", include_in_schema=False)
async def push(request: Request, db: DbSession) -> dict[str, int]:
    """Shopee push notifications (webhook). Only signed requests are accepted.

    The body is never trusted for data: a valid order event just queues a sync of
    that shop, which reads everything through the signed API.
    """
    raw = await request.body()
    if len(raw) > MAX_PUSH_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    if not shopee_sync.verify_push_signature(raw, request.headers.get("authorization")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None
    if isinstance(payload, dict):
        shopee_sync.handle_push(db, payload)
    # Always acknowledge a verified push, otherwise Shopee keeps retrying it.
    return {"code": 0}
