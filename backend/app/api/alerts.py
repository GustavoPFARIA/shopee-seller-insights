"""Alerts endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.metrics import today_local
from app.deps import CurrentUser, DbSession
from app.schemas import Alert
from app.services.alerts import compute_alerts

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
def get_alerts(
    user: CurrentUser,
    db: DbSession,
    stalled_days: Annotated[int, Query(ge=1, le=365)] = 30,
    min_margin_pct: Annotated[float, Query(ge=-100, le=100)] = 15.0,
) -> list[Alert]:
    return compute_alerts(
        db,
        user.seller_id,
        today=today_local(),
        stalled_days=stalled_days,
        min_margin_pct=min_margin_pct,
    )
