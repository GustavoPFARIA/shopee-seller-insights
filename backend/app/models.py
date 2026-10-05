"""SQLAlchemy ORM models.

Money is always stored as NUMERIC(12, 2) and handled as Decimal in Python.
All timestamps are timezone-aware and stored in UTC.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

Money = Numeric(12, 2)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Seller(Base):
    __tablename__ = "sellers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    shop_code: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
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
