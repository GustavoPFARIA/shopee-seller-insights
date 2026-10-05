"""Sales metrics computed in PostgreSQL, always scoped to a single seller."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import ColumnElement, Date, Select, cast, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.config import get_settings
from app.models import Order, OrderItem, Product
from app.schemas import AbcItem, DailyPoint, KpiDelta, KpiValues, OverviewResponse, ProductMetrics
from app.services.importer import NON_REVENUE_STATUSES

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
ABC_A_LIMIT = 80.0
ABC_B_LIMIT = 95.0
AbcClass = Literal["A", "B", "C"]


def local_day(
    column: ColumnElement[datetime] | InstrumentedAttribute[datetime],
) -> ColumnElement[date]:
    """Calendar day of a UTC timestamp in the seller's reporting time zone."""
    return cast(func.timezone(get_settings().report_timezone, column), Date)


def _revenue_items(seller_id: int, start: date, end: date) -> Select[OrderItem]:
    """Base filter shared by every metric: seller scope, revenue statuses, period."""
    day = local_day(Order.ordered_at)
    return (
        select(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .where(
            Order.seller_id == seller_id,
            Product.seller_id == seller_id,  # defense in depth
            Order.status.not_in(NON_REVENUE_STATUSES),
            day >= start,
            day <= end,
        )
    )


GROSS = OrderItem.quantity * OrderItem.unit_price
FEES = (
    OrderItem.commission_fee
    + OrderItem.service_fee
    + OrderItem.seller_shipping_fee
    + OrderItem.seller_voucher
)


def kpis(db: Session, seller_id: int, start: date, end: date) -> KpiValues:
    base = _revenue_items(seller_id, start, end).subquery()
    row = db.execute(
        select(
            func.coalesce(func.sum(base.c.quantity * base.c.unit_price), 0),
            func.count(func.distinct(base.c.order_id)),
            func.coalesce(func.sum(base.c.quantity), 0),
        )
    ).one()
    revenue = Decimal(row[0]).quantize(CENT)
    orders = int(row[1])
    products = product_metrics(db, seller_id, start, end)
    margins = [p.margin for p in products]
    net_margin = (
        sum((m for m in margins if m is not None), ZERO)
        if all(m is not None for m in margins)
        else None
    )
    return KpiValues(
        revenue=revenue,
        orders=orders,
        units=int(row[2]),
        avg_ticket=(revenue / orders).quantize(CENT) if orders else ZERO,
        net_margin=net_margin,
    )


def pct_change(current: Decimal | int, previous: Decimal | int) -> float | None:
    if previous == 0:
        return None
    return round(float((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100), 2)


def overview(db: Session, seller_id: int, start: date, end: date) -> OverviewResponse:
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    current = kpis(db, seller_id, start, end)
    previous = kpis(db, seller_id, prev_start, prev_end)
    return OverviewResponse(
        start=start,
        end=end,
        previous_start=prev_start,
        previous_end=prev_end,
        current=current,
        previous=previous,
        change=KpiDelta(
            revenue_pct=pct_change(current.revenue, previous.revenue),
            orders_pct=pct_change(current.orders, previous.orders),
            avg_ticket_pct=pct_change(current.avg_ticket, previous.avg_ticket),
        ),
    )


def daily(db: Session, seller_id: int, start: date, end: date) -> list[DailyPoint]:
    day = local_day(Order.ordered_at).label("day")
    stmt = (
        _revenue_items(seller_id, start, end)
        .with_only_columns(day, func.sum(GROSS), func.count(func.distinct(Order.id)))
        .group_by(day)
        .order_by(day)
    )
    return [
        DailyPoint(day=d, revenue=Decimal(rev).quantize(CENT), orders=int(n))
        for d, rev, n in db.execute(stmt)
    ]


def product_metrics(db: Session, seller_id: int, start: date, end: date) -> list[ProductMetrics]:
    stmt = (
        _revenue_items(seller_id, start, end)
        .with_only_columns(
            Product.id,
            Product.sku,
            Product.name,
            Product.unit_cost,
            func.sum(OrderItem.quantity),
            func.sum(GROSS),
            func.sum(OrderItem.commission_fee),
            func.sum(OrderItem.service_fee),
            func.sum(OrderItem.seller_shipping_fee),
            func.sum(OrderItem.seller_voucher),
        )
        .group_by(Product.id)
        .order_by(func.sum(GROSS).desc(), Product.sku)
    )
    result: list[ProductMetrics] = []
    for (
        pid,
        sku,
        name,
        unit_cost,
        units,
        gross,
        commission,
        service,
        shipping,
        voucher,
    ) in db.execute(stmt):
        revenue = Decimal(gross).quantize(CENT)
        fees = commission + service + shipping + voucher
        cost = (unit_cost * units).quantize(CENT) if unit_cost is not None else None
        margin = (revenue - fees - cost).quantize(CENT) if cost is not None else None
        result.append(
            ProductMetrics(
                product_id=pid,
                sku=sku,
                name=name,
                units=int(units),
                revenue=revenue,
                commission_fee=commission,
                service_fee=service,
                shipping_fee=shipping,
                voucher=voucher,
                product_cost=cost,
                margin=margin,
                margin_pct=(
                    round(float(margin / revenue * 100), 2)
                    if margin is not None and revenue > 0
                    else None
                ),
            )
        )
    return result


def classify_abc(
    revenues: list[tuple[int, Decimal]],
) -> list[tuple[int, AbcClass, float, float]]:
    """Return (id, class, share %, cumulative %) sorted by revenue desc.

    Classic Pareto rule on the cumulative share *including* the product:
    A up to 80%, B up to 95%, C above. The top seller is always A, even when it
    alone exceeds 80% of revenue. Products without revenue are C.
    """
    ordered = sorted(revenues, key=lambda r: r[1], reverse=True)
    total = sum((r for _, r in ordered), Decimal("0"))
    out: list[tuple[int, AbcClass, float, float]] = []
    cumulative = 0.0
    for index, (pid, revenue) in enumerate(ordered):
        share = float(revenue / total * 100) if total > 0 else 0.0
        cumulative += share
        cls: AbcClass
        if revenue <= 0:
            cls = "C"
        elif index == 0 or cumulative <= ABC_A_LIMIT:
            cls = "A"
        elif cumulative <= ABC_B_LIMIT:
            cls = "B"
        else:
            cls = "C"
        out.append((pid, cls, round(share, 2), round(cumulative, 2)))
    return out


def abc_curve(db: Session, seller_id: int, start: date, end: date) -> list[AbcItem]:
    products = {p.product_id: p for p in product_metrics(db, seller_id, start, end)}
    return [
        AbcItem(
            product_id=pid,
            sku=products[pid].sku,
            name=products[pid].name,
            revenue=products[pid].revenue,
            share_pct=share,
            cumulative_pct=cumulative,
            abc_class=cls,
        )
        for pid, cls, share, cumulative in classify_abc(
            [(p.product_id, p.revenue) for p in products.values()]
        )
    ]
