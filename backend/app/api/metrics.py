"""Metrics endpoints. Every query is scoped to the authenticated seller."""

import csv
import io
from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.schemas import AbcItem, DailyPoint, OverviewResponse, ProductMetrics
from app.services import metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

MAX_PERIOD_DAYS = 366
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _today() -> date:
    try:
        return datetime.now(ZoneInfo(get_settings().report_timezone)).date()
    except ZoneInfoNotFoundError:  # slim images may lack tzdata; UTC is close enough
        return datetime.now().astimezone().date()


class Period:
    def __init__(
        self,
        start: Annotated[date | None, Query()] = None,
        end: Annotated[date | None, Query()] = None,
    ) -> None:
        self.end = end or _today()
        self.start = start or self.end - timedelta(days=29)
        if self.start > self.end:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start must be <= end")
        if (self.end - self.start).days + 1 > MAX_PERIOD_DAYS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Period cannot exceed {MAX_PERIOD_DAYS} days",
            )


PeriodDep = Annotated[Period, Depends()]


@router.get("/overview", response_model=OverviewResponse)
def get_overview(user: CurrentUser, db: DbSession, period: PeriodDep) -> OverviewResponse:
    return metrics.overview(db, user.seller_id, period.start, period.end)


@router.get("/daily", response_model=list[DailyPoint])
def get_daily(user: CurrentUser, db: DbSession, period: PeriodDep) -> list[DailyPoint]:
    return metrics.daily(db, user.seller_id, period.start, period.end)


@router.get("/products", response_model=list[ProductMetrics])
def get_products(user: CurrentUser, db: DbSession, period: PeriodDep) -> list[ProductMetrics]:
    return metrics.product_metrics(db, user.seller_id, period.start, period.end)


@router.get("/abc", response_model=list[AbcItem])
def get_abc(user: CurrentUser, db: DbSession, period: PeriodDep) -> list[AbcItem]:
    return metrics.abc_curve(db, user.seller_id, period.start, period.end)


def _safe_cell(value: object) -> str:
    """Neutralize spreadsheet formula injection (OWASP CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


@router.get("/products/export.csv", response_class=Response)
def export_products(user: CurrentUser, db: DbSession, period: PeriodDep) -> Response:
    rows = metrics.product_metrics(db, user.seller_id, period.start, period.end)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["sku", "name", "units", "revenue", "fees", "product_cost", "margin", "margin_pct"]
    )
    for r in rows:
        fees = r.commission_fee + r.service_fee + r.shipping_fee + r.voucher
        writer.writerow(
            [
                _safe_cell(r.sku),
                _safe_cell(r.name),
                r.units,
                r.revenue,
                fees,
                r.product_cost if r.product_cost is not None else "",
                r.margin if r.margin is not None else "",
                r.margin_pct if r.margin_pct is not None else "",
            ]
        )
    filename = f"product-metrics-{period.start}-{period.end}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
