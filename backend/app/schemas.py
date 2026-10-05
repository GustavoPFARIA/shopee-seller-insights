"""Pydantic v2 request/response schemas for the public API."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    shop_name: str = Field(min_length=2, max_length=120)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105


Role = Literal["owner", "manager", "viewer"]


class MeResponse(BaseModel):
    email: str
    seller_id: int
    shop_name: str
    role: Role


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    unit_cost: Decimal | None
    stock_quantity: int | None
    low_stock_threshold: int


class ProductUpdate(BaseModel):
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock_quantity: int | None = Field(default=None, ge=0, le=10_000_000)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=10_000_000)


class UploadResult(BaseModel):
    upload_id: int
    row_count: int
    orders_created: int
    orders_updated: int
    orders_unchanged: int
    products_created: int


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    row_count: int
    orders_created: int
    orders_updated: int
    orders_unchanged: int
    created_at: datetime


class KpiValues(BaseModel):
    revenue: Decimal
    orders: int
    units: int
    avg_ticket: Decimal
    net_margin: Decimal | None


class KpiDelta(BaseModel):
    revenue_pct: float | None
    orders_pct: float | None
    avg_ticket_pct: float | None


class OverviewResponse(BaseModel):
    start: date
    end: date
    previous_start: date
    previous_end: date
    current: KpiValues
    previous: KpiValues
    change: KpiDelta


class DailyPoint(BaseModel):
    day: date
    revenue: Decimal
    orders: int


class ProductMetrics(BaseModel):
    product_id: int
    sku: str
    name: str
    units: int
    revenue: Decimal
    commission_fee: Decimal
    service_fee: Decimal
    shipping_fee: Decimal
    voucher: Decimal
    product_cost: Decimal | None
    margin: Decimal | None
    margin_pct: float | None


class AbcItem(BaseModel):
    product_id: int
    sku: str
    name: str
    revenue: Decimal
    share_pct: float
    cumulative_pct: float
    abc_class: Literal["A", "B", "C"]


class Alert(BaseModel):
    kind: Literal["low_stock", "stalled_product", "low_margin"]
    product_id: int
    sku: str
    name: str
    message: str


class AiSummaryResponse(BaseModel):
    enabled: bool
    summary: str | None
    # Exactly what was sent to the model, shown for transparency.
    facts: dict[str, Any] | None = None


class CatalogImportResultOut(BaseModel):
    rows: int
    updated: int
    created: int
    unchanged: int


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    created_at: datetime


class MemberUpdate(BaseModel):
    role: Role


class InvitationCreate(BaseModel):
    email: EmailStr
    role: Role


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    expires_at: datetime


class InvitationCreated(InvitationOut):
    # Shown once to the owner; only its hash is stored.
    token: str


class AcceptInvitation(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: Literal["manual", "scheduled"]
    status: Literal["queued", "running", "success", "error"]
    started_at: datetime
    finished_at: datetime | None
    orders_created: int
    orders_updated: int
    orders_skipped: int
    products_stock_updated: int
    error: str | None


class ShopeeStatus(BaseModel):
    enabled: bool
    connected: bool
    shop_id: int | None = None
    connected_at: datetime | None = None
    orders_synced_until: datetime | None = None
    sync_interval_minutes: int
    runs: list[SyncRunOut] = []


class AuthorizationUrl(BaseModel):
    authorization_url: str
