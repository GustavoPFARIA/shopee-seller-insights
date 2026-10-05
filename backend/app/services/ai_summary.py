"""Optional weekly summary written by Claude, grounded only on computed aggregates.

Privacy: the model receives a small JSON of metrics (totals, top products, alert
counts). Orders, order ids, buyer hashes and any personal data are never sent.
"""

import json
from datetime import date, timedelta
from typing import Any, Protocol

import anthropic
from anthropic.types import TextBlock
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.alerts import compute_alerts
from app.services.metrics import abc_curve, overview, product_metrics

SYSTEM_PROMPT = (
    "You are a concise e-commerce analyst helping a small Shopee seller. "
    "Write a plain-language weekly summary in English (max 180 words) with: "
    "1) how sales went versus the previous week, 2) best products, 3) the most "
    "important risks from the alerts, 4) two or three practical next steps. "
    "Use ONLY the numbers in the JSON provided by the user. Never invent figures; "
    "if a value is null or missing, say it is not available. Currency is BRL (R$)."
)


class SummaryUnavailableError(Exception):
    pass


class _Messages(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    @property
    def messages(self) -> _Messages: ...


def ai_enabled() -> bool:
    key = get_settings().anthropic_api_key
    return key is not None and key.get_secret_value() != ""


def _client() -> "anthropic.Anthropic | _Client":
    key = get_settings().anthropic_api_key
    if key is None:
        raise SummaryUnavailableError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=key.get_secret_value(), timeout=30.0, max_retries=2)


def weekly_facts(db: Session, seller_id: int, today: date) -> dict[str, Any]:
    """Aggregate-only snapshot of the last 7 days (the only data sent to the LLM)."""
    start = today - timedelta(days=6)
    ov = overview(db, seller_id, start, today)
    top = product_metrics(db, seller_id, start, today)[:5]
    abc = abc_curve(db, seller_id, start, today)
    alerts = compute_alerts(db, seller_id, today=today)  # the shop's own thresholds
    return {
        "period": {"start": str(start), "end": str(today)},
        "this_week": ov.current.model_dump(mode="json"),
        "previous_week": ov.previous.model_dump(mode="json"),
        "change_pct": ov.change.model_dump(mode="json"),
        "top_products": [
            {
                "name": p.name,
                "units": p.units,
                "revenue": str(p.revenue),
                "margin_pct": p.margin_pct,
            }
            for p in top
        ],
        "abc_counts": {c: sum(1 for i in abc if i.abc_class == c) for c in ("A", "B", "C")},
        "alerts": {
            kind: [a.name for a in alerts if a.kind == kind][:5]
            for kind in ("low_stock", "stalled_product", "low_margin", "high_returns")
        },
    }


def generate_summary(
    facts: dict[str, Any], client: "anthropic.Anthropic | _Client | None" = None
) -> str:
    client = client or _client()
    try:
        response = client.messages.create(
            model=get_settings().anthropic_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Weekly metrics JSON:\n" + json.dumps(facts, ensure_ascii=False),
                }
            ],
        )
    except anthropic.APIError as exc:
        raise SummaryUnavailableError("The AI provider is unavailable") from exc
    text = "".join(block.text for block in response.content if isinstance(block, TextBlock)).strip()
    if not text:
        raise SummaryUnavailableError("The AI provider returned an empty answer")
    return text
