"""Shopee Open Platform connection and synchronization endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
def connect(owner: OwnerUser, db: DbSession) -> AuthorizationUrl:
    url = shopee_sync.start_authorization(
        db, _client(), seller_id=owner.seller_id, user_id=owner.id
    )
    return AuthorizationUrl(authorization_url=url)


@router.get("/callback", include_in_schema=False, dependencies=[Depends(callback_limit)])
def callback(
    db: DbSession,
    state: Annotated[str | None, Query(max_length=200)] = None,
    code: Annotated[str | None, Query(max_length=500)] = None,
    shop_id: Annotated[int | None, Query(gt=0)] = None,
) -> RedirectResponse:
    """Shopee redirects the browser here after the owner authorizes the shop.

    There is no bearer token on this request: the single-use `state` identifies the
    shop owner who started the flow. Errors redirect with a fixed code only.
    """
    client = _client()
    outcome = "connected"
    if not state or not code or shop_id is None:
        outcome = "error:missing_params"
    else:
        try:
            shopee_sync.complete_authorization(db, client, state=state, code=code, shop_id=shop_id)
        except shopee_sync.OAuthError as exc:
            reason = str(exc)
            outcome = f"error:{reason if reason in CALLBACK_ERRORS else 'exchange_failed'}"
        except ShopeeApiError:
            outcome = "error:exchange_failed"
    return RedirectResponse(f"/#shopee={outcome}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sync", response_model=SyncRunOut, dependencies=[Depends(sync_limit)])
def sync_now(user: EditorUser, db: DbSession) -> SyncRun:
    client = _client()
    try:
        return shopee_sync.sync_seller(db, client, user.seller_id, trigger="manual")
    except shopee_sync.OAuthError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Shopee shop connected") from None
    except shopee_sync.SyncBusyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A synchronization is already running"
        ) from None


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(owner: OwnerUser, db: DbSession) -> Response:
    if not shopee_sync.disconnect(db, owner.seller_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Shopee shop connected")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
