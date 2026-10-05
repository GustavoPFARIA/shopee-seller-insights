"""Alerts endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.schemas import Alert
from app.services.alerts import compute_alerts
from app.timeutil import today_local

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
def get_alerts(
    user: CurrentUser,
    db: DbSession,
    stalled_days: Annotated[int | None, Query(ge=1, le=365)] = None,
    min_margin_pct: Annotated[float | None, Query(ge=-100, le=100)] = None,
) -> list[Alert]:
    """Alerts using the shop's saved thresholds; query parameters override them."""
    return compute_alerts(
        db,
        user.seller_id,
        today=today_local(),
        stalled_days=stalled_days,
        min_margin_pct=min_margin_pct,
    )
