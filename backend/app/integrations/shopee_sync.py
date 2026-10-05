"""Shopee Open Platform: shop authorization and order/stock synchronization.

Used by the API (connect, callback, "Sync now") and by the scheduled worker.
"""

import hashlib
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
from app.crypto import decrypt, encrypt
from app.db import get_engine
from app.integrations.shopee_client import ShopeeApiError, ShopeeAuthError, ShopeeClient
from app.models import OAuthState, Order, Product, ShopeeConnection, SyncRun
from app.services.importer import (
    STATUS_ALIASES,
    Identifier,
    OrderRow,
    SafeText,
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
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---- OAuth -------------------------------------------------------------------


def start_authorization(db: Session, client: ShopeeClient, *, seller_id: int, user_id: int) -> str:
    """Return the Shopee URL the owner must visit; binds a single-use state to them."""
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
    return client.authorization_url(redirect)


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


def _refresh(db: Session, client: ShopeeClient, conn: ShopeeConnection) -> str:
    tokens = client.refresh_token(decrypt(conn.refresh_token_enc), conn.shop_id)
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
    db: Session, client: ShopeeClient, seller_id: int, *, trigger: str = "manual"
) -> SyncRun:
    conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.seller_id == seller_id))
    if conn is None:
        raise OAuthError("not_connected")
    with seller_lock(seller_id):
        run = SyncRun(seller_id=seller_id, trigger=trigger, status="running")
        db.add(run)
        db.commit()
        try:
            _sync_orders(db, client, conn, run)
            run.products_stock_updated = _sync_stock(db, client, conn)
            run.status = "success"
        except ShopeeApiError as exc:
            db.rollback()
            run.status = "error"
            # Only the API error code/message: never tokens or payloads.
            run.error = str(exc)[:500]
            log.warning("Shopee sync failed for seller %s: %s", seller_id, exc.error)
        run.finished_at = datetime.now(UTC)
        db.commit()
        return run


def _money(value: Any) -> Decimal:
    # The API returns floats; go through str() to avoid binary float artifacts.
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _fees_from_escrow(income: dict[str, Any]) -> dict[str, Decimal]:
    shipping = (
        _money(income.get("actual_shipping_fee"))
        - _money(income.get("shopee_shipping_rebate"))
        - _money(income.get("buyer_paid_shipping_fee"))
    )
    return {
        "commission_fee": _money(income.get("commission_fee")),
        # Transaction (payment) fee is folded into the service fee column.
        "service_fee": _money(income.get("service_fee")) + _money(income.get("transaction_fee")),
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
    sku: Identifier
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
