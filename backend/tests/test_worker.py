import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import worker
from app.config import get_settings
from app.integrations import shopee_sync
from app.integrations.shopee_client import ShopeeClient
from app.models import Order, ShopeeConnection, SyncRun
from devtools.fake_shopee import HOST, PARTNER_ID, PARTNER_KEY, SHOP_ID, FakeShopee


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeShopee:
    shopee = FakeShopee(now=int(time.time()))
    settings = get_settings()
    monkeypatch.setattr(settings, "shopee_partner_id", PARTNER_ID)
    monkeypatch.setattr(settings, "shopee_partner_key", SecretStr(PARTNER_KEY))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(
        shopee_sync,
        "make_client",
        lambda: ShopeeClient(
            partner_id=PARTNER_ID,
            partner_key=PARTNER_KEY,
            host=HOST,
            http=httpx.Client(transport=shopee.transport()),
            sleep=lambda _s: None,
        ),
    )
    return shopee


def _connect(client: TestClient, headers: dict[str, str]) -> None:
    url = client.post("/api/shopee/connect", headers=headers).json()["authorization_url"]
    state = parse_qs(urlparse(parse_qs(urlparse(url).query)["redirect"][0]).query)["state"][0]
    client.get(
        "/api/shopee/callback",
        params={"state": state, "code": "good-code", "shop_id": SHOP_ID},
        follow_redirects=False,
    )


def test_cycle_syncs_connected_shops(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    fake.add_order(
        "W1",
        status="UNPAID",
        create_time=fake.now,
        items=[
            {
                "item_sku": "SKU-W",
                "model_sku": "",
                "item_name": "W",
                "model_discounted_price": 5.0,
                "model_quantity_purchased": 1,
            }
        ],
    )
    assert worker.run_cycle() == {"synced": 1, "busy": 0, "failed": 0, "reauth": 0}
    assert db.scalar(select(Order.order_sn)) == "W1"
    assert db.scalar(select(SyncRun.trigger)) == "scheduled"


def test_cycle_survives_failures(
    client: TestClient,
    auth_headers: dict[str, str],
    fake: FakeShopee,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect(client, auth_headers)
    seller_id = db.scalar(select(ShopeeConnection.seller_id))
    assert seller_id is not None

    fake.fail_next = [("/api/v2/order/get_order_list", 400, "error_server")]
    assert worker.run_cycle()["failed"] == 1

    with shopee_sync.seller_lock(seller_id):
        assert worker.run_cycle()["busy"] == 1

    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(shopee_sync, "sync_seller", explode)
    assert worker.run_cycle()["failed"] == 1

    def api_error(*_a: object, **_k: object) -> None:
        raise shopee_sync.OAuthError("not_connected")

    monkeypatch.setattr(shopee_sync, "sync_seller", api_error)
    assert worker.run_cycle()["failed"] == 1


def test_expired_refresh_token_requires_reauthorization(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    db.execute(
        update(ShopeeConnection).values(refresh_expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    db.commit()
    assert worker.run_cycle()["reauth"] == 1
    assert db.scalar(select(SyncRun.error)) == "reauthorization_required"
    assert fake.calls["/api/v2/order/get_order_list"] == 0


def test_main_once(
    monkeypatch: pytest.MonkeyPatch, fake: FakeShopee, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    with caplog.at_level("INFO"):
        worker.main()
    assert "cycle done" in caplog.text


def test_main_idle_when_not_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    with caplog.at_level("INFO"):
        worker.main()
    assert "not configured" in caplog.text


def test_signal_stops_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import signal

    handlers: dict[int, object] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    monkeypatch.setattr("sys.argv", ["worker"])

    calls = {"n": 0}

    def fake_wait(self: object, timeout: float) -> bool:
        calls["n"] += 1
        handlers[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]
        return True

    monkeypatch.setattr("threading.Event.wait", fake_wait)
    worker.main()
    assert calls["n"] == 1


def test_logs_never_contain_shopee_tokens(
    client: TestClient,
    auth_headers: dict[str, str],
    fake: FakeShopee,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    _connect(client, auth_headers)
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    # Simulate a fresh process where basicConfig would enable INFO for everything.
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    with caplog.at_level(logging.DEBUG):
        worker.main()
    assert "cycle done" in caplog.text
    assert fake.access, "a token was issued"
    for token in list(fake.access) + list(fake.refresh):
        assert token not in caplog.text
    assert "access_token=" not in caplog.text


def test_reauthorization_is_recorded_once(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    db.execute(
        update(ShopeeConnection).values(refresh_expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    db.commit()
    for _ in range(3):
        worker.run_cycle()
    errors = db.scalars(select(SyncRun.error)).all()
    assert errors == ["reauthorization_required"]


def test_queued_run_for_shop_needing_reauth_is_closed(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    client.post("/api/shopee/sync", headers=auth_headers)
    db.execute(
        update(ShopeeConnection).values(refresh_expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    db.commit()
    assert worker.process_queue() == 1
    assert db.scalar(select(SyncRun.error)) == "reauthorization_required"


def test_queued_run_for_disconnected_shop_is_closed(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    client.post("/api/shopee/sync", headers=auth_headers)
    client.delete("/api/shopee/connection", headers=auth_headers)
    assert worker.process_queue() == 1
    assert db.scalar(select(SyncRun.status)) == "error"


def test_heartbeat_healthcheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeShopee
) -> None:
    import os

    heartbeat = tmp_path / "beat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat)
    assert worker.is_healthy() is False  # never started
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    worker.main()
    assert worker.is_healthy() is True
    old = time.time() - worker.HEARTBEAT_MAX_AGE_SECONDS - 5
    os.utime(heartbeat, (old, old))
    assert worker.is_healthy() is False  # hung worker
    monkeypatch.setattr("sys.argv", ["worker", "--healthcheck"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1
