import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import TextBlock
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import ai_summary
from tests.factories import Line, build_csv


class FakeMessages:
    def __init__(self, text: str = "Sales grew.", error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.text = text
        self.error = error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[TextBlock(type="text", text=self.text)])


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def _enable_ai(monkeypatch: pytest.MonkeyPatch, fake: FakeMessages) -> None:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", SecretStr("sk-test"))
    monkeypatch.setattr(ai_summary, "_client", lambda: FakeClient(fake))


def test_disabled_without_api_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = client.get("/api/summary/weekly", headers=auth_headers).json()
    assert body == {"enabled": False, "summary": None, "facts": None}


def test_summary_sends_only_aggregates(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = datetime.now().date()
    lines = [
        Line(
            "SECRETORDER1",
            "SKU-A",
            "80.00",
            date=f"{today} 09:00",
            buyer="secret_buyer",
            name="Phone case",
        )
    ]
    client.post("/api/uploads", headers=auth_headers, files={"file": ("o.csv", build_csv(lines))})
    fake = FakeMessages("Revenue was R$ 80.00 this week.")
    _enable_ai(monkeypatch, fake)

    body = client.get("/api/summary/weekly", headers=auth_headers).json()
    assert body["enabled"] is True
    assert body["summary"] == "Revenue was R$ 80.00 this week."

    call = fake.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    sent = json.dumps(call)
    for forbidden in ("SECRETORDER1", "secret_buyer", "Joao", "98888", "Rua Secreta", "SKU-A"):
        assert forbidden not in sent
    assert "Phone case" in sent  # catalogue product name is fine


def test_provider_error_returns_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    _enable_ai(monkeypatch, FakeMessages(error=anthropic.APIConnectionError(request=request)))
    resp = client.get("/api/summary/weekly", headers=auth_headers)
    assert resp.status_code == 502


def test_empty_answer_is_an_error() -> None:
    with pytest.raises(ai_summary.SummaryUnavailableError):
        ai_summary.generate_summary({}, client=FakeClient(FakeMessages(text="  ")))


def test_weekly_facts_shape(client: TestClient, auth_headers: dict[str, str], db: Session) -> None:
    seller_id = client.get("/api/auth/me", headers=auth_headers).json()["seller_id"]
    facts = ai_summary.weekly_facts(db, seller_id, date(2025, 9, 30))
    assert set(facts) == {
        "period",
        "this_week",
        "previous_week",
        "change_pct",
        "top_products",
        "abc_counts",
        "alerts",
    }
    assert facts["period"] == {"start": "2025-09-24", "end": "2025-09-30"}


def test_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/api/summary/weekly").status_code == 401
