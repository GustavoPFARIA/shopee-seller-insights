"""Shopee Open Platform: shop authorization and order/stock synchronization.

Used by the API (connect, callback, "Sync now") and by the scheduled worker.
"""

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import TokenCryptoError, decrypt, encrypt
from app.db import get_engine
from app.integrations.shopee_client import ShopeeApiError, ShopeeAuthError, ShopeeClient
from app.models import OAuthState, Order, Product, ShopeeConnection, SyncRun
from app.services.importer import (
    STATUS_ALIASES,
    OrderRow,
    SafeText,
    Sku,
    upsert_api_orders,
)

log = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=10)
REFRESH_TOKEN_TTL = timedelta(days=30)
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
SYNC_OVERLAP = timedelta(minutes=10)
LOCK_NAMESPACE = 7_300_000_000
# Escrow (exact fees) only exists once the buyer has paid.
ESCROW_STATUSES = {"to_ship", "shipped", "completed"}
PENDING_ESCROW_LIMIT = 200


class ShopeeNotConfiguredError(Exception):
    pass


class OAuthError(Exception):
    pass


class SyncBusyError(Exception):
    pass


def make_client() -> ShopeeClient:
    settings = get_settings()
    if not settings.shopee_enabled:
        raise ShopeeNotConfiguredError
    assert settings.shopee_partner_id is not None  # noqa: S101 (narrowed by shopee_enabled)
    assert settings.shopee_partner_key is not None  # noqa: S101
    return ShopeeClient(
        partner_id=settings.shopee_partner_id,
        partner_key=settings.shopee_partner_key.get_secret_value(),
        host=settings.shopee_api_host,
        auth_host=settings.shopee_auth_host,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---- OAuth -------------------------------------------------------------------


def start_authorization(
    db: Session, client: ShopeeClient, *, seller_id: int, user_id: int
) -> tuple[str, str]:
    """Return (Shopee URL the owner must visit, state). The state is single-use and
    bound to the owner; the API also binds it to their browser with a cookie."""
    state = secrets.token_urlsafe(32)
    db.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC)))
    db.add(
        OAuthState(
            state_hash=_hash(state),
            seller_id=seller_id,
            user_id=user_id,
            expires_at=datetime.now(UTC) + STATE_TTL,
        )
    )
    db.commit()
    redirect = f"{get_settings().shopee_redirect_url}?{urlencode({'state': state})}"
    return client.authorization_url(redirect), state


def complete_authorization(
    db: Session, client: ShopeeClient, *, state: str, code: str, shop_id: int
) -> ShopeeConnection:
    record = db.scalar(
        select(OAuthState).where(OAuthState.state_hash == _hash(state)).with_for_update()
    )
    if record is None or record.expires_at <= datetime.now(UTC):
        raise OAuthError("invalid_state")
    seller_id = record.seller_id
    db.delete(record)  # single use, even if the exchange below fails
    db.commit()

    other = db.scalar(
        select(ShopeeConnection).where(
            ShopeeConnection.shop_id == shop_id, ShopeeConnection.seller_id != seller_id
        )
    )
    if other is not None:
        raise OAuthError("shop_already_connected")

    tokens = client.get_token(code, shop_id)
    now = datetime.now(UTC)
    conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.seller_id == seller_id))
    if conn is None or conn.shop_id != shop_id:
        if conn is not None:
            db.delete(conn)
            db.flush()
        conn = ShopeeConnection(seller_id=seller_id, shop_id=shop_id)
        db.add(conn)
    conn.access_token_enc = encrypt(tokens.access_token)
    conn.refresh_token_enc = encrypt(tokens.refresh_token)
    conn.access_expires_at = now + timedelta(seconds=tokens.expire_in)
    conn.refresh_expires_at = now + REFRESH_TOKEN_TTL
    db.commit()
    return conn


def disconnect(db: Session, seller_id: int) -> bool:
    result = db.execute(delete(ShopeeConnection).where(ShopeeConnection.seller_id == seller_id))
    db.commit()
    return bool(result.rowcount)  # type: ignore[attr-defined]


# ---- tokens ------------------------------------------------------------------


class ReauthorizationRequiredError(Exception):
    """Shopee rejected the refresh token (revoked or expired): an owner must reconnect."""


REAUTH_ERROR = "reauthorization_required"


def _refresh(db: Session, client: ShopeeClient, conn: ShopeeConnection) -> str:
    try:
        tokens = client.refresh_token(decrypt(conn.refresh_token_enc), conn.shop_id)
    except ShopeeAuthError:
        # Mark the connection as expired so the scheduler stops retrying every cycle.
        db.rollback()
        conn.refresh_expires_at = datetime.now(UTC)
        db.commit()
        raise ReauthorizationRequiredError from None
    now = datetime.now(UTC)
    conn.access_token_enc = encrypt(tokens.access_token)
    conn.refresh_token_enc = encrypt(tokens.refresh_token)  # Shopee rotates it too
    conn.access_expires_at = now + timedelta(seconds=tokens.expire_in)
    conn.refresh_expires_at = now + REFRESH_TOKEN_TTL
    db.commit()
    return tokens.access_token


def _access_token(db: Session, client: ShopeeClient, conn: ShopeeConnection) -> str:
    if conn.access_expires_at - TOKEN_REFRESH_MARGIN <= datetime.now(UTC):
        return _refresh(db, client, conn)
    return decrypt(conn.access_token_enc)


def _call[T](
    db: Session, client: ShopeeClient, conn: ShopeeConnection, fn: Callable[[str], T]
) -> T:
    """Call the API with a valid token; on a token error refresh once and retry."""
    try:
        return fn(_access_token(db, client, conn))
    except ShopeeAuthError:
        return fn(_refresh(db, client, conn))


# ---- sync --------------------------------------------------------------------


@contextmanager
def seller_lock(seller_id: int) -> Iterator[None]:
    """Cluster-wide lock (PostgreSQL advisory lock) so a shop never syncs twice at once."""
    with get_engine().connect() as lock_conn:
        got = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_NAMESPACE + seller_id}
        ).scalar_one()
        if not got:
            raise SyncBusyError
        try:
            yield
        finally:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_NAMESPACE + seller_id}
            )
            lock_conn.commit()


def sync_seller(
    db: Session,
    client: ShopeeClient,
    seller_id: int,
    *,
    trigger: str = "manual",
    run: SyncRun | None = None,
) -> SyncRun:
    """Synchronize one shop. `run` is a queued run to execute (else a new one is created).

    Any failure ends the run as "error" with a safe message: the API error code, or
    only the exception class for unexpected errors (never tokens or payloads).
    """
    conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.seller_id == seller_id))
    if conn is None:
        raise OAuthError("not_connected")
    with seller_lock(seller_id):
        if run is None:
            run = SyncRun(seller_id=seller_id, trigger=trigger)
            db.add(run)
        run.status = "running"
        run.started_at = datetime.now(UTC)
        db.commit()
        try:
            _sync_orders(db, client, conn, run)
            run.products_stock_updated = _sync_stock(db, client, conn)
            run.status = "success"
        except ReauthorizationRequiredError:
            db.rollback()
            run.status = "error"
            run.error = REAUTH_ERROR
            log.warning("seller %s: Shopee refused the refresh token", seller_id)
        except ShopeeApiError as exc:
            db.rollback()
            run.status = "error"
            run.error = str(exc)[:500]
            log.warning("Shopee sync failed for seller %s: %s", seller_id, exc.error)
        except TokenCryptoError:
            db.rollback()
            run.status = "error"
            run.error = "token_decryption_failed: reconnect the shop"
            log.error("seller %s: stored Shopee tokens cannot be decrypted", seller_id)
        except Exception as exc:
            db.rollback()
            run.status = "error"
            run.error = f"internal_error: {type(exc).__name__}"
            log.exception("seller %s: unexpected sync error", seller_id)
        run.finished_at = datetime.now(UTC)
        db.commit()
        return run


def _money(value: Any) -> Decimal:
    # The API returns floats; go through str() to avoid binary float artifacts.
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _first(income: dict[str, Any], *keys: str) -> Decimal:
    """First present field: newer/regional names first, older names as fallback."""
    for key in keys:
        if income.get(key) is not None:
            return _money(income[key])
    return Decimal("0.00")


def _fees_from_escrow(income: dict[str, Any]) -> dict[str, Decimal]:
    """Map v2.payment.get_escrow_detail `order_income` to our fee columns.

    Brazil returns net_commission_fee / net_service_fee (net of Shopee rebates); the
    payment fee is seller_transaction_fee (credit_card_transaction_fee on old shops).
    """
    shipping = (
        _money(income.get("actual_shipping_fee"))
        - _money(income.get("shopee_shipping_rebate"))
        - _money(income.get("buyer_paid_shipping_fee"))
    )
    return {
        "commission_fee": _first(income, "net_commission_fee", "commission_fee"),
        # Transaction (payment) fee is folded into the service fee column.
        "service_fee": _first(income, "net_service_fee", "service_fee")
        + _first(income, "seller_transaction_fee", "credit_card_transaction_fee"),
        "seller_shipping_fee": max(shipping, Decimal("0.00")),
        "seller_voucher": _money(income.get("voucher_from_seller")),
    }


def _sync_orders(db: Session, client: ShopeeClient, conn: ShopeeConnection, run: SyncRun) -> None:
    now = datetime.now(UTC)
    since = (
        conn.orders_synced_until - SYNC_OVERLAP
        if conn.orders_synced_until
        else now - timedelta(days=get_settings().shopee_backfill_days)
    )
    sns = list(
        dict.fromkeys(
            _call(
                db,
                client,
                conn,
                lambda t: list(
                    client.iter_order_sns(
                        t, conn.shop_id, int(since.timestamp()), int(now.timestamp())
                    )
                ),
            )
        )
    )
    # Completed API orders without final escrow (e.g. the escrow call failed last time)
    # would never be listed again once the high-water mark moves past them.
    pending = db.scalars(
        select(Order.order_sn)
        .where(
            Order.seller_id == conn.seller_id,
            Order.source == "shopee_api",
            Order.status == "completed",
            Order.fees_final.is_(False),
        )
        .order_by(Order.ordered_at.desc())
        .limit(PENDING_ESCROW_LIMIT)
    ).all()
    sns = list(dict.fromkeys([*sns, *pending]))
    if sns:
        details = _call(db, client, conn, lambda t: client.get_order_details(t, conn.shop_id, sns))
        final_already = set(
            db.scalars(
                select(Order.order_sn).where(
                    Order.seller_id == conn.seller_id,
                    Order.order_sn.in_(sns),
                    Order.fees_final.is_(True),
                )
            )
        )
        orders: dict[str, list[OrderRow]] = {}
        refresh_fees: set[str] = set()
        fees_final: set[str] = set()
        for detail in details:
            order_sn = str(detail.get("order_sn", ""))
            status = STATUS_ALIASES.get(str(detail.get("order_status", "")).lower())
            fees = dict.fromkeys(
                ("commission_fee", "service_fee", "seller_shipping_fee", "seller_voucher"),
                Decimal("0.00"),
            )
            if status in ESCROW_STATUSES and order_sn not in final_already:
                try:
                    income = _call(
                        db,
                        client,
                        conn,
                        partial(client.get_escrow_detail, shop_id=conn.shop_id, order_sn=order_sn),
                    )
                    fees = _fees_from_escrow(income)
                    refresh_fees.add(order_sn)
                    if status == "completed":
                        fees_final.add(order_sn)
                except ShopeeAuthError:
                    raise
                except ShopeeApiError:
                    pass  # escrow not available yet: keep zero fees, retry next sync
            lines = _order_lines(detail, status, fees)
            if lines is None:
                run.orders_skipped += 1
                continue
            orders[order_sn] = lines
        summary = upsert_api_orders(
            db,
            seller_id=conn.seller_id,
            orders=orders,
            refresh_fees=refresh_fees,
            fees_final=fees_final,
        )
        run.orders_created = summary.created
        run.orders_updated = summary.updated
    conn.orders_synced_until = now
    db.commit()


def _order_lines(
    detail: dict[str, Any], status: str | None, fees: dict[str, Decimal]
) -> list[OrderRow] | None:
    """Map one API order to validated rows; None if it cannot be represented."""
    if status is None:
        return None
    rows: list[OrderRow] = []
    try:
        for item in detail.get("item_list", []):
            rows.append(
                OrderRow.model_validate(
                    {
                        "order_sn": detail["order_sn"],
                        "status": status,
                        "ordered_at": datetime.fromtimestamp(int(detail["create_time"]), UTC),
                        "sku": item.get("model_sku") or item.get("item_sku") or "",
                        "product_name": item.get("item_name", ""),
                        "unit_price": str(item.get("model_discounted_price", 0)),
                        "quantity": int(item.get("model_quantity_purchased", 0)),
                        "buyer_username": detail.get("buyer_username"),
                        **{k: str(v) for k, v in fees.items()},
                    }
                )
            )
    except (ValidationError, KeyError, TypeError, ValueError):
        return None
    if not rows:
        return None
    # The same SKU twice with different prices cannot be stored as one line.
    prices: dict[str, Decimal] = {}
    for row in rows:
        if prices.setdefault(row.sku, row.unit_price) != row.unit_price:
            return None
    return rows


def _sync_stock(db: Session, client: ShopeeClient, conn: ShopeeConnection) -> int:
    """Update stock (and create missing products) by SKU from the Shopee catalogue."""
    item_ids = _call(db, client, conn, lambda t: list(client.iter_item_ids(t, conn.shop_id)))
    if not item_ids:
        return 0
    items = _call(db, client, conn, lambda t: client.get_item_base_info(t, conn.shop_id, item_ids))
    stock: dict[str, tuple[str, int]] = {}
    for item in items:
        name = str(item.get("item_name", ""))[:255]
        if item.get("has_model"):
            models = _call(
                db,
                client,
                conn,
                partial(client.get_model_list, shop_id=conn.shop_id, item_id=int(item["item_id"])),
            )
            for model in models:
                sku = str(model.get("model_sku") or "")
                if sku:
                    stock[sku] = (name, _available(model))
        elif item.get("item_sku"):
            stock[str(item["item_sku"])] = (name, _available(item))

    updated = 0
    existing = {
        p.sku: p
        for p in db.scalars(
            select(Product).where(Product.seller_id == conn.seller_id, Product.sku.in_(list(stock)))
        )
    }
    for sku, (name, quantity) in stock.items():
        product = existing.get(sku)
        if product is None:
            try:
                validated = _NewProduct(sku=sku, name=name or sku)
            except ValidationError:
                continue  # SKU/name fails the same rules as file imports: skip
            db.add(
                Product(
                    seller_id=conn.seller_id,
                    sku=validated.sku,
                    name=validated.name,
                    stock_quantity=quantity,
                    low_stock_threshold=5,
                )
            )
            updated += 1
        elif product.stock_quantity != quantity:
            product.stock_quantity = quantity
            updated += 1
    db.commit()
    return updated


class _NewProduct(BaseModel):
    sku: Sku
    name: SafeText


def _available(entry: dict[str, Any]) -> int:
    summary = (entry.get("stock_info_v2") or {}).get("summary_info") or {}
    return max(int(summary.get("total_available_stock") or 0), 0)


def mark_stale_runs(db: Session) -> None:
    """Runs left 'running' by a crashed process are closed as errors."""
    db.execute(
        update(SyncRun)
        .where(
            SyncRun.status == "running", SyncRun.started_at < datetime.now(UTC) - timedelta(hours=2)
        )
        .values(status="error", error="interrupted", finished_at=datetime.now(UTC))
    )
    db.commit()


def request_sync(db: Session, seller_id: int, trigger: str = "manual") -> SyncRun:
    """Queue a sync for the worker. Requests are coalesced into a run that is still
    queued; a run already *running* may have started before the change that triggered
    this request, so a new one is queued after it."""
    if (
        db.scalar(select(ShopeeConnection.id).where(ShopeeConnection.seller_id == seller_id))
        is None
    ):
        raise OAuthError("not_connected")
    pending = db.scalar(
        select(SyncRun)
        .where(SyncRun.seller_id == seller_id, SyncRun.status == "queued")
        .order_by(SyncRun.id.desc())
        .limit(1)
    )
    if pending is not None:
        return pending
    run = SyncRun(seller_id=seller_id, trigger=trigger, status="queued")
    db.add(run)
    db.commit()
    return run


PUSH_ORDER_CODES = {3, 4}  # order status update, tracking number update
PUSH_DEAUTHORIZED = 2


def verify_push_signature(raw_body: bytes, authorization: str | None) -> bool:
    """Shopee push: Authorization = hex HMAC-SHA256(push key, push URL + "|" + raw body)."""
    settings = get_settings()
    key = settings.shopee_push_key or settings.shopee_partner_key
    if not authorization or key is None:
        return False
    base = settings.shopee_push_url.encode() + b"|" + raw_body
    expected = hmac.new(key.get_secret_value().encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, authorization.strip().lower())


def handle_push(db: Session, payload: dict[str, object]) -> str:
    """Act on a verified push. The payload is only a hint: data is re-read via the API."""
    try:
        code = int(str(payload.get("code")))
        shop_id = int(str(payload.get("shop_id")))
    except ValueError:
        return "ignored"
    conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.shop_id == shop_id))
    if conn is None:
        return "unknown_shop"
    if code in PUSH_ORDER_CODES:
        request_sync(db, conn.seller_id, trigger="push")
        return "sync_queued"
    if code == PUSH_DEAUTHORIZED:
        disconnect(db, conn.seller_id)
        return "disconnected"
    return "ignored"
