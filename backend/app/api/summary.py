"""Optional AI weekly summary endpoint."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.metrics import today_local
from app.config import get_settings
from app.deps import DbSession, EditorUser, rate_limit
from app.schemas import AiSummaryResponse
from app.services import ai_summary

router = APIRouter(prefix="/api/summary", tags=["ai"])

summary_limit = rate_limit(
    "ai-summary",
    lambda: get_settings().upload_rate_limit,
    lambda: get_settings().upload_rate_window_seconds,
)


@router.get("/weekly", response_model=AiSummaryResponse, dependencies=[Depends(summary_limit)])
def weekly_summary(user: EditorUser, db: DbSession) -> AiSummaryResponse:
    if not ai_summary.ai_enabled():
        return AiSummaryResponse(enabled=False, summary=None, facts=None)
    facts: dict[str, Any] = ai_summary.weekly_facts(db, user.seller_id, today_local())
    try:
        text = ai_summary.generate_summary(facts)
    except ai_summary.SummaryUnavailableError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return AiSummaryResponse(enabled=True, summary=text, facts=facts)
