"""Sales metrics computed in PostgreSQL, always scoped to a single seller."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import ColumnElement, Date, DateTime, Select, cast, func, literal, select
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


def _local_midnight(day: date) -> ColumnElement[datetime]:
    """UTC instant of 00:00 on `day` in the reporting time zone (computed by PostgreSQL)."""
    return func.timezone(get_settings().report_timezone, cast(literal(day), DateTime))


def _items(
    seller_id: int, start: date, end: date, status_filter: ColumnElement[bool]
) -> Select[OrderItem]:
    """Items of one seller's orders placed in [start, end] (local days) matching a status filter.

    The period is a half-open UTC range [local midnight of start, local midnight of
    end + 1). Comparing the raw column (instead of converting every row to a local
    date) lets PostgreSQL use the (seller_id, ordered_at) index.
    """
    return (
        select(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .where(
            Order.seller_id == seller_id,
            Product.seller_id == seller_id,  # defense in depth
            status_filter,
            Order.ordered_at >= _local_midnight(start),
            Order.ordered_at < _local_midnight(end + timedelta(days=1)),
        )
    )


def _revenue_items(seller_id: int, start: date, end: date) -> Select[OrderItem]:
    """Items that generate revenue (not cancelled, unpaid or returned)."""
    return _items(seller_id, start, end, Order.status.not_in(NON_REVENUE_STATUSES))


GROSS = OrderItem.quantity * OrderItem.unit_price
FEES = (
    OrderItem.commission_fee
    + OrderItem.service_fee
    + OrderItem.seller_shipping_fee
    + OrderItem.seller_voucher
)


def kpis(db: Session, seller_id: int, start: date, end: date) -> KpiValues:
    """Headline numbers in a single aggregate query.

    Net margin = revenue - all fees - unit cost x units over every sold item. It is
    None when any sold product has no cost informed (a partial sum would mislead).
    """
    row = db.execute(
        _revenue_items(seller_id, start, end).with_only_columns(
            func.coalesce(func.sum(GROSS), 0),
            func.count(func.distinct(OrderItem.order_id)),
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(GROSS - FEES - OrderItem.quantity * Product.unit_cost), 0),
            func.coalesce(func.bool_or(Product.unit_cost.is_(None)), False),
        )
    ).one()
    revenue = Decimal(row[0]).quantize(CENT)
    orders = int(row[1])
    return KpiValues(
        revenue=revenue,
        orders=orders,
        units=int(row[2]),
        avg_ticket=(revenue / orders).quantize(CENT) if orders else ZERO,
        net_margin=None if row[4] else Decimal(row[3]).quantize(CENT),
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
    lost = _lost_units(db, seller_id, start, end)
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
        returned, cancelled = lost.pop(pid, (0, 0))
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
                returned_units=returned,
                cancelled_units=cancelled,
                return_rate_pct=_return_rate(int(units), returned),
            )
        )
    # Products whose every unit in the period was returned or cancelled.
    if lost:
        names = {
            p.id: p
            for p in db.scalars(
                select(Product).where(Product.seller_id == seller_id, Product.id.in_(lost))
            )
        }
        for pid, (returned, cancelled) in lost.items():
            result.append(
                ProductMetrics(
                    product_id=pid,
                    sku=names[pid].sku,
                    name=names[pid].name,
                    units=0,
                    revenue=ZERO,
                    commission_fee=ZERO,
                    service_fee=ZERO,
                    shipping_fee=ZERO,
                    voucher=ZERO,
                    product_cost=None,
                    margin=None,
                    margin_pct=None,
                    returned_units=returned,
                    cancelled_units=cancelled,
                    return_rate_pct=_return_rate(0, returned),
                )
            )
    return result


def _return_rate(sold_units: int, returned_units: int) -> float | None:
    """Returned units / (kept + returned units), in %. None when nothing was shipped."""
    shipped = sold_units + returned_units
    return round(returned_units / shipped * 100, 2) if shipped else None


def _lost_units(db: Session, seller_id: int, start: date, end: date) -> dict[int, tuple[int, int]]:
    """(returned units, cancelled units) per product for orders placed in the period."""
    stmt = (
        _items(seller_id, start, end, Order.status.in_(("returned", "cancelled")))
        .with_only_columns(OrderItem.product_id, Order.status, func.sum(OrderItem.quantity))
        .group_by(OrderItem.product_id, Order.status)
    )
    lost: dict[int, tuple[int, int]] = {}
    for pid, status, units in db.execute(stmt):
        returned, cancelled = lost.get(pid, (0, 0))
        if status == "returned":
            returned += int(units)
        else:
            cancelled += int(units)
        lost[pid] = (returned, cancelled)
    return lost


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
