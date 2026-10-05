"""Actionable alerts: low stock, stalled products and thin margins."""

from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, Product
from app.schemas import Alert
from app.services.importer import NON_REVENUE_STATUSES
from app.services.metrics import local_day, product_metrics

MARGIN_WINDOW_DAYS = 30


def compute_alerts(
    db: Session,
    seller_id: int,
    *,
    today: date,
    stalled_days: int,
    min_margin_pct: float,
) -> list[Alert]:
    alerts: list[Alert] = []

    low_stock = db.scalars(
        select(Product)
        .where(
            Product.seller_id == seller_id,
            Product.stock_quantity.is_not(None),
            Product.stock_quantity <= Product.low_stock_threshold,
        )
        .order_by(Product.stock_quantity, Product.sku)
    )
    for p in low_stock:
        alerts.append(
            Alert(
                kind="low_stock",
                product_id=p.id,
                sku=p.sku,
                name=p.name,
                message=f"Only {p.stock_quantity} units left (threshold {p.low_stock_threshold}).",
            )
        )

    last_sale = (
        select(OrderItem.product_id, func.max(local_day(Order.ordered_at)).label("last_day"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.seller_id == seller_id, Order.status.not_in(NON_REVENUE_STATUSES))
        .group_by(OrderItem.product_id)
        .subquery()
    )
    cutoff = today - timedelta(days=stalled_days)
    stalled = db.execute(
        select(Product, last_sale.c.last_day)
        .outerjoin(last_sale, last_sale.c.product_id == Product.id)
        .where(
            Product.seller_id == seller_id,
            or_(Product.stock_quantity.is_(None), Product.stock_quantity > 0),
            or_(last_sale.c.last_day.is_(None), last_sale.c.last_day < cutoff),
        )
        .order_by(last_sale.c.last_day.asc().nulls_first(), Product.sku)
    )
    for p, last_day in stalled:
        message = (
            f"No sales in {(today - last_day).days} days."
            if last_day is not None
            else "No sales recorded yet."
        )
        alerts.append(
            Alert(kind="stalled_product", product_id=p.id, sku=p.sku, name=p.name, message=message)
        )

    window_start = today - timedelta(days=MARGIN_WINDOW_DAYS - 1)
    for m in product_metrics(db, seller_id, window_start, today):
        if m.margin_pct is not None and m.margin_pct < min_margin_pct:
            alerts.append(
                Alert(
                    kind="low_margin",
                    product_id=m.product_id,
                    sku=m.sku,
                    name=m.name,
                    message=(
                        f"Margin {m.margin_pct:.1f}% in the last {MARGIN_WINDOW_DAYS} days "
                        f"(minimum {min_margin_pct:.1f}%)."
                    ),
                )
            )
    return alerts
