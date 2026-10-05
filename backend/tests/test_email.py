import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import mailer, worker
from app.config import Settings, get_settings
from app.models import Membership, Seller, User
from app.services import ai_summary, digest
from tests.factories import Line, build_csv


class FakeSMTP:
    """Records what the code under test does with an SMTP connection."""

    sent: ClassVar[list[EmailMessage]] = []
    events: ClassVar[list[str]] = []
    fail: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: float = 0, **_kw: Any) -> None:
        FakeSMTP.events.append(f"connect {host}:{port}")

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        FakeSMTP.events.append("quit")

    def starttls(self, context: Any = None) -> None:
        FakeSMTP.events.append("starttls")

    def login(self, user: str, password: str) -> None:
        FakeSMTP.events.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if FakeSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({})
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.events, FakeSMTP.fail = [], [], False
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("smtp-secret"))
    monkeypatch.setattr(settings, "app_base_url", "https://insights.example.test")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


# ---- mailer ---------------------------------------------------------------------


def test_send_email_uses_starttls_and_login(smtp: type[FakeSMTP]) -> None:
    mailer.send_email(["a@example.com"], "Hello", "Body")
    assert smtp.events == ["connect smtp.example.test:587", "starttls", "login mailer", "quit"]
    assert smtp.sent[0]["To"] == "a@example.com"
    assert smtp.sent[0].get_content().strip() == "Body"


def test_header_injection_is_neutralized(smtp: type[FakeSMTP]) -> None:
    mailer.send_email(["a@example.com"], "Hi\r\nBcc: victim@example.com", "Body")
    message = smtp.sent[0]
    assert message["Bcc"] is None
    assert "\n" not in message["Subject"]


def test_ssl_mode_and_no_recipients(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_security", "ssl")
    mailer.send_email(["a@example.com"], "S", "B")
    assert "starttls" not in smtp.events
    smtp.events.clear()
    mailer.send_email([], "S", "B")
    assert smtp.events == []


def test_delivery_failure_and_disabled(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    smtp.fail = True
    with pytest.raises(mailer.MailError):
        mailer.send_email(["private@example.com"], "S", "B")
    assert "private@example.com" not in caplog.text  # recipients are personal data
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    with pytest.raises(mailer.MailError, match="not configured"):
        mailer.send_email(["a@example.com"], "S", "B")


def test_production_refuses_plaintext_smtp() -> None:
    with pytest.raises(ValueError, match="SMTP_SECURITY"):
        Settings(
            app_env="production",
            jwt_secret="y" * 40,
            pii_hash_secret="x" * 40,
            smtp_host="smtp.example.test",
            smtp_security="none",
            cookie_secure=True,
        )


# ---- invitations ----------------------------------------------------------------


def test_invitation_is_emailed(
    client: TestClient, auth_headers: dict[str, str], smtp: type[FakeSMTP]
) -> None:
    resp = client.post(
        "/api/members/invitations",
        headers=auth_headers,
        json={"email": "new@example.com", "role": "viewer"},
    )
    body = resp.json()
    assert body["emailed"] is True
    message = smtp.sent[0]
    assert message["To"] == "new@example.com"
    assert f"https://insights.example.test/#invite={body['token']}" in message.get_content()


def test_invitation_still_works_when_email_fails(
    client: TestClient, auth_headers: dict[str, str], smtp: type[FakeSMTP]
) -> None:
    smtp.fail = True
    resp = client.post(
        "/api/members/invitations",
        headers=auth_headers,
        json={"email": "new@example.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    assert resp.json()["emailed"] is False and resp.json()["token"]


def test_invitation_without_smtp_is_not_emailed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/members/invitations",
        headers=auth_headers,
        json={"email": "new@example.com", "role": "viewer"},
    )
    assert resp.json()["emailed"] is False


# ---- weekly digest -------------------------------------------------------------


def _seed_week(client: TestClient, headers: dict[str, str]) -> None:
    today = date.today()
    lines = [
        Line("D1", "SKU-A", "50.00", qty=2, date=f"{today - timedelta(days=1)} 10:00", name="Mug"),
        Line("D2", "SKU-A", "50.00", qty=1, date=f"{today - timedelta(days=9)} 10:00", name="Mug"),
    ]
    resp = client.post("/api/uploads", headers=headers, files={"file": ("o.csv", build_csv(lines))})
    assert resp.status_code == 201


def test_render_digest_contents(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _seed_week(client, auth_headers)
    seller = db.scalar(select(Seller))
    assert seller is not None
    subject, body = digest.build_digest(db, seller, date.today())
    assert "weekly summary" in subject
    assert "Revenue:        R$ 100.00 (up 100.0% vs previous week)" in body
    assert "Mug: 2 units" in body
    assert "AI summary" not in body  # AI disabled in tests
    assert "buyer" not in body.lower()


def test_digest_includes_ai_text_when_available(
    client: TestClient, auth_headers: dict[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = db.scalar(select(Seller))
    assert seller is not None
    monkeypatch.setattr(ai_summary, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_summary, "generate_summary", lambda facts: "Great week.")
    assert "AI summary:\nGreat week." in digest.build_digest(db, seller, date.today())[1]

    def broken(facts: object) -> str:
        raise ai_summary.SummaryUnavailableError

    monkeypatch.setattr(ai_summary, "generate_summary", broken)
    assert "AI summary" not in digest.build_digest(db, seller, date.today())[1]


def test_due_digests_are_sent_weekly_to_owners_and_managers(
    client: TestClient, auth_headers: dict[str, str], db: Session, smtp: type[FakeSMTP]
) -> None:
    _seed_week(client, auth_headers)
    client.patch("/api/settings", headers=auth_headers, json={"weekly_email": True})
    seller_id = db.scalar(select(Seller.id))
    assert seller_id is not None
    for email, role in (("viewer@example.com", "viewer"), ("manager@example.com", "manager")):
        member = User(seller_id=seller_id, email=email, password_hash="x")
        db.add(member)
        db.flush()
        db.add(Membership(user_id=member.id, seller_id=seller_id, role=role))
    db.commit()
    today = date.today()
    assert digest.send_due_digests(db, today) == 1
    assert smtp.sent[0]["To"] == "manager@example.com, seller-a@example.com"
    assert digest.send_due_digests(db, today + timedelta(days=3)) == 0  # not due yet
    assert digest.send_due_digests(db, today + timedelta(days=7)) == 1


def test_digest_skips_opted_out_failed_and_empty_shops(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    db: Session,
    smtp: type[FakeSMTP],
) -> None:
    assert digest.send_due_digests(db, date.today()) == 0  # nobody opted in
    client.patch("/api/settings", headers=auth_headers, json={"weekly_email": True})
    smtp.fail = True
    assert digest.send_due_digests(db, date.today()) == 0
    assert db.scalar(select(Seller.last_digest_sent_on).where(Seller.name == "Shop A")) is None
    smtp.fail = False
    db.execute(
        update(Membership)
        .where(
            Membership.user_id
            == select(User.id).where(User.email == "seller-a@example.com").scalar_subquery()
        )
        .values(role="viewer")
    )
    db.commit()
    assert digest.send_due_digests(db, date.today()) == 0  # no owner/manager to send to


def test_digest_disabled_without_smtp(db: Session) -> None:
    assert digest.send_due_digests(db, date.today()) == 0


def test_digest_preview_endpoint(
    client: TestClient, auth_headers: dict[str, str], smtp: type[FakeSMTP]
) -> None:
    assert client.post("/api/settings/digest/preview", headers=auth_headers).status_code == 204
    assert smtp.sent[0]["To"] == "seller-a@example.com"
    smtp.fail = True
    assert client.post("/api/settings/digest/preview", headers=auth_headers).status_code == 502


def test_digest_preview_without_smtp(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.post("/api/settings/digest/preview", headers=auth_headers).status_code == 503


def test_worker_runs_digests_and_survives_errors(
    client: TestClient,
    auth_headers: dict[str, str],
    smtp: type[FakeSMTP],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.patch("/api/settings", headers=auth_headers, json={"weekly_email": True})
    assert worker.run_digests() == 1

    def boom(*_a: object) -> int:
        raise RuntimeError("bug")

    monkeypatch.setattr(digest, "send_due_digests", boom)
    assert worker.run_digests() == 0
