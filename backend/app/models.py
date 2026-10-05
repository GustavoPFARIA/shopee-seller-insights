"""SQLAlchemy ORM models.

Money is always stored as NUMERIC(12, 2) and handled as Decimal in Python.
All timestamps are timezone-aware and stored in UTC.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

Money = Numeric(12, 2)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Seller(Base):
    """A shop. Its alert thresholds and notification preferences live here (1:1)."""

    __tablename__ = "sellers"
    __table_args__ = (
        CheckConstraint("stalled_days BETWEEN 1 AND 365", name="ck_sellers_stalled_days"),
        CheckConstraint("min_margin_pct BETWEEN -100 AND 100", name="ck_sellers_min_margin"),
        CheckConstraint("max_return_rate_pct BETWEEN 0 AND 100", name="ck_sellers_max_return_rate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    shop_code: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Alert thresholds (editable in Settings).
    stalled_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    min_margin_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("15"), server_default="15"
    )
    max_return_rate_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("10"), server_default="10"
    )
    # Send the weekly summary to owners and managers by e-mail (needs SMTP).
    weekly_email: Mapped[bool] = mapped_column(default=False, server_default="false")
    last_digest_sent_on: Mapped[date | None] = mapped_column(Date)


ROLES = ("owner", "manager", "viewer")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'manager', 'viewer')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="owner", server_default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    seller: Mapped[Seller] = relationship()


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("seller_id", "sku", name="uq_products_seller_sku"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_products_unit_cost"),
        CheckConstraint("stock_quantity >= 0", name="ck_products_stock"),
        CheckConstraint("low_stock_threshold >= 0", name="ck_products_low_stock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    # NULL means "cost not informed yet": margin cannot be computed.
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    # NULL means "stock not tracked": no low-stock alert is raised.
    stock_quantity: Mapped[int | None] = mapped_column(Integer)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        CheckConstraint(
            "row_count >= 0 AND orders_created >= 0 AND orders_updated >= 0 "
            "AND orders_unchanged >= 0",
            name="ck_uploads_counts",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    orders_created: Mapped[int] = mapped_column(Integer)
    orders_updated: Mapped[int] = mapped_column(Integer)
    orders_unchanged: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # Idempotency key: the same Shopee order can only exist once per seller.
        UniqueConstraint("seller_id", "order_sn", name="uq_orders_seller_order_sn"),
        CheckConstraint("source IN ('file', 'shopee_api')", name="ck_orders_source"),
        Index("ix_orders_seller_ordered_at", "seller_id", "ordered_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), index=True
    )
    order_sn: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # HMAC-SHA256 of the buyer username; raw PII is never stored.
    buyer_hash: Mapped[str | None] = mapped_column(String(64))
    # "file" (manual upload) or "shopee_api" (Open Platform sync).
    source: Mapped[str] = mapped_column(String(16), default="file", server_default="file")
    # True once fees come from the final escrow statement (completed order).
    fees_final: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    """One product line of an order.

    Order-level fees from the Shopee export are allocated to items proportionally to
    their gross value at import time, so per-product margin is a plain sum.
    """

    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price"),
        CheckConstraint(
            "commission_fee >= 0 AND service_fee >= 0 AND seller_shipping_fee >= 0 "
            "AND seller_voucher >= 0",
            name="ck_order_items_fees",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    commission_fee: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    service_fee: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    seller_shipping_fee: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    seller_voucher: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class RateLimitHit(Base):
    """Shared fixed-window counter (key is an HMAC, never a raw IP)."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        PrimaryKeyConstraint("key", "window_start", name="pk_rate_limit_hits"),
        CheckConstraint("hits > 0", name="ck_rate_limit_hits_positive"),
        Index("ix_rate_limit_hits_window_start", "window_start"),
    )

    key: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer)


class RefreshToken(Base):
    """Opaque refresh token (stored only as SHA-256) with rotation families."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_refresh_tokens_expiry"),
        Index("ix_refresh_tokens_family", "family_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    """One-time invite to join a shop; the token is stored only as SHA-256."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'manager', 'viewer')", name="ck_invitations_role"),
        CheckConstraint("expires_at > created_at", name="ck_invitations_expiry"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShopeeConnection(Base):
    """A shop authorized through Shopee Open Platform OAuth (one per seller).

    Access and refresh tokens are encrypted at rest with Fernet (TOKEN_ENCRYPTION_KEY).
    """

    __tablename__ = "shopee_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(
        ForeignKey("sellers.id", ondelete="CASCADE"), unique=True
    )
    # A Shopee shop can be linked to only one seller account.
    shop_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # High-water mark of order update_time already synced.
    orders_synced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OAuthState(Base):
    """Single-use CSRF state for the Shopee OAuth redirect (stored as SHA-256)."""

    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """History of Shopee synchronizations (manual or by the worker)."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')", name="ck_sync_runs_status"
        ),
        CheckConstraint("trigger IN ('manual', 'scheduled', 'push')", name="ck_sync_runs_trigger"),
        Index("ix_sync_runs_seller_started", "seller_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orders_created: Mapped[int] = mapped_column(Integer, default=0)
    orders_updated: Mapped[int] = mapped_column(Integer, default=0)
    orders_skipped: Mapped[int] = mapped_column(Integer, default=0)
    products_stock_updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500))
