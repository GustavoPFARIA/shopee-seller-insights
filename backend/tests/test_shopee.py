import hashlib
import hmac
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import crypto, worker
from app.config import get_settings
from app.integrations import shopee_sync
from app.integrations.shopee_client import ShopeeApiError, ShopeeClient, sign
from app.models import Order, OrderItem, Product, ShopeeConnection, SyncRun, User
from tests import fake_shopee
from tests.fake_shopee import HOST, PARTNER_ID, PARTNER_KEY, SHOP_ID, FakeShopee, stock

DAY = 86_400


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeShopee]:
    shopee = FakeShopee(now=int(time.time()))
    settings = get_settings()
    monkeypatch.setattr(settings, "shopee_partner_id", PARTNER_ID)
    monkeypatch.setattr(settings, "shopee_partner_key", SecretStr(PARTNER_KEY))
    monkeypatch.setattr(settings, "shopee_api_host", HOST)
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))

    def client() -> ShopeeClient:
        return ShopeeClient(
            partner_id=PARTNER_ID,
            partner_key=PARTNER_KEY,
            host=HOST,
            http=httpx.Client(transport=shopee.transport()),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr(shopee_sync, "make_client", client)
    yield shopee


def _connect(client: TestClient, owner: dict[str, str], code: str = "good-code") -> str:
    """Run the OAuth round trip and return where the callback redirects the browser."""
    url = client.post("/api/shopee/connect", headers=owner).json()["authorization_url"]
    redirect = parse_qs(urlparse(url).query)["redirect"][0]
    state = parse_qs(urlparse(redirect).query)["state"][0]
    resp = client.get(
        "/api/shopee/callback",
        params={"state": state, "code": code, "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return str(resp.headers["location"])


def _sync(client: TestClient, headers: dict[str, str]) -> dict:  # type: ignore[type-arg]
    """Queue a manual sync, let the worker run it, return the finished run."""
    resp = client.post("/api/shopee/sync", headers=headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] in ("queued", "running")
    worker.process_queue()
    runs = client.get("/api/shopee/status", headers=headers).json()["runs"]
    assert runs[0]["id"] == resp.json()["id"]
    return dict(runs[0])


def _item(sku: str, price: float, qty: int = 1, name: str | None = None) -> dict:  # type: ignore[type-arg]
    return {
        "item_sku": sku,
        "model_sku": "",
        "item_name": name or f"Item {sku}",
        "model_discounted_price": price,
        "model_quantity_purchased": qty,
    }


# ---- signing & client -------------------------------------------------------


def test_sign_matches_shopee_spec() -> None:
    base = f"{PARTNER_ID}/api/v2/order/get_order_list1700000000tok{SHOP_ID}"
    expected = hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert (
        sign(PARTNER_KEY, PARTNER_ID, "/api/v2/order/get_order_list", 1700000000, "tok", SHOP_ID)
        == expected
    )


def test_authorization_url_is_signed() -> None:
    c = ShopeeClient(
        partner_id=PARTNER_ID, partner_key=PARTNER_KEY, host=HOST, clock=lambda: 1700000000
    )
    q = parse_qs(urlparse(c.authorization_url("https://app/cb")).query)
    assert q["sign"][0] == sign(PARTNER_KEY, PARTNER_ID, "/api/v2/shop/auth_partner", 1700000000)
    assert q["redirect"][0] == "https://app/cb"


def test_client_retries_then_gives_up(fake: FakeShopee) -> None:
    c = shopee_sync.make_client()
    fake.fail_next = [("/api/v2/auth/token/get", 503, "")] * 3
    with pytest.raises(ShopeeApiError, match="http_error"):
        c.get_token("good-code", SHOP_ID)
    fake.fail_next = [("/api/v2/auth/token/get", 429, "")]
    assert c.get_token("good-code", SHOP_ID).expire_in == 14400


def test_client_network_errors_and_bad_responses() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    c = ShopeeClient(
        partner_id=1,
        partner_key="k",
        host=HOST,
        http=httpx.Client(transport=httpx.MockTransport(boom)),
        sleep=lambda _s: None,
    )
    with pytest.raises(ShopeeApiError, match="network_error"):
        c.get_token("x", 1)
    bad = ShopeeClient(
        partner_id=1,
        partner_key="k",
        host=HOST,
        http=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
        ),
    )
    with pytest.raises(ShopeeApiError, match="bad_response"):
        bad.get_token("x", 1)
    partial = ShopeeClient(
        partner_id=1,
        partner_key="k",
        host=HOST,
        http=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": ""}))
        ),
    )
    with pytest.raises(ShopeeApiError, match="incomplete"):
        partial.get_token("x", 1)


# ---- OAuth --------------------------------------------------------------------


def test_connect_flow_stores_encrypted_tokens(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    assert _connect(client, auth_headers) == "/#shopee=connected"
    conn = db.scalar(select(ShopeeConnection))
    assert conn is not None and conn.shop_id == SHOP_ID
    raw_tokens = set(fake.access) | set(fake.refresh)
    assert conn.access_token_enc not in raw_tokens
    assert crypto.decrypt(conn.access_token_enc) in fake.access
    status = client.get("/api/shopee/status", headers=auth_headers).json()
    assert status["connected"] is True and status["shop_id"] == SHOP_ID


def test_callback_rejects_bad_or_reused_state(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee
) -> None:
    def cb(**params: str | int) -> str:
        r = client.get("/api/shopee/callback", params=params, follow_redirects=False)
        return str(r.headers["location"])

    assert (
        cb(state="forged" * 5, code="good-code", shop_id=SHOP_ID) == "/#shopee=error:invalid_state"
    )
    assert cb(code="good-code") == "/#shopee=error:missing_params"
    url = client.post("/api/shopee/connect", headers=auth_headers).json()["authorization_url"]
    state = parse_qs(urlparse(parse_qs(urlparse(url).query)["redirect"][0]).query)["state"][0]
    assert cb(state=state, code="wrong-code", shop_id=SHOP_ID) == "/#shopee=error:exchange_failed"
    # The state was consumed by the failed attempt: it cannot be replayed.
    assert cb(state=state, code="good-code", shop_id=SHOP_ID) == "/#shopee=error:invalid_state"


def test_expired_state(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    from app.models import OAuthState

    url = client.post("/api/shopee/connect", headers=auth_headers).json()["authorization_url"]
    state = parse_qs(urlparse(parse_qs(urlparse(url).query)["redirect"][0]).query)["state"][0]
    db.execute(update(OAuthState).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    r = client.get(
        "/api/shopee/callback",
        params={"state": state, "code": "good-code", "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/#shopee=error:invalid_state"


def test_shop_cannot_be_linked_to_two_sellers(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    fake: FakeShopee,
) -> None:
    assert _connect(client, auth_headers) == "/#shopee=connected"
    fake.codes["second-code"] = SHOP_ID
    loc = _connect(client, other_headers, "second-code")
    assert loc == "/#shopee=error:shop_already_connected"


def test_permissions_and_configuration(
    client: TestClient, auth_headers: dict[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.execute(update(User).values(role="manager"))
    db.commit()
    assert client.post("/api/shopee/connect", headers=auth_headers).status_code == 403
    assert client.delete("/api/shopee/connection", headers=auth_headers).status_code == 403
    db.execute(update(User).values(role="owner"))
    db.commit()
    # Not configured on this server (no partner credentials).
    assert client.post("/api/shopee/connect", headers=auth_headers).status_code == 503
    assert client.get("/api/shopee/status", headers=auth_headers).json()["enabled"] is False
    assert client.get("/api/shopee/status").status_code == 401


# ---- order sync ---------------------------------------------------------------


def test_sync_orders_with_exact_escrow_fees(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    now = fake.now
    fake.add_order(
        "API001",
        status="COMPLETED",
        create_time=now - 3 * DAY,
        items=[
            _item("SKU-A", 100.0, 2),
            {**_item("SKU-B", 50.0), "item_sku": "PARENT", "model_sku": "SKU-B"},
        ],
        escrow={
            "commission_fee": 30.0,
            "service_fee": 10.0,
            "seller_transaction_fee": 2.5,
            "voucher_from_seller": 5.0,
            "actual_shipping_fee": 20.0,
            "shopee_shipping_rebate": 8.0,
            "buyer_paid_shipping_fee": 7.0,
        },
    )
    fake.add_order(
        "API002", status="CANCELLED", create_time=now - 2 * DAY, items=[_item("SKU-A", 100.0)]
    )
    fake.add_order(
        "API003", status="READY_TO_SHIP", create_time=now - DAY, items=[_item("SKU-C", 9.9)]
    )

    run = _sync(client, auth_headers)
    assert run["status"] == "success"
    assert (run["orders_created"], run["orders_skipped"]) == (3, 0)
    # Personal data minimization: the address was never requested.
    detail_q = fake.last_query["/api/v2/order/get_order_detail"]
    assert "recipient_address" not in detail_q["response_optional_fields"]

    o1 = db.scalar(select(Order).where(Order.order_sn == "API001"))
    assert o1 is not None
    assert (o1.status, o1.source, o1.fees_final) == ("completed", "shopee_api", True)
    assert o1.buyer_hash and "buyer" not in o1.buyer_hash
    items = db.scalars(select(OrderItem).where(OrderItem.order_id == o1.id)).all()
    assert sum(i.commission_fee for i in items) == Decimal("30.00")
    assert sum(i.service_fee for i in items) == Decimal("12.50")  # service + transaction
    assert sum(i.seller_shipping_fee for i in items) == Decimal("5.00")  # 20 - 8 - 7
    assert sum(i.seller_voucher for i in items) == Decimal("5.00")
    skus = set(db.scalars(select(Product.sku)))
    assert {"SKU-A", "SKU-B", "SKU-C"} <= skus and "PARENT" not in skus
    # No escrow for a cancelled order; READY_TO_SHIP had none available yet.
    assert fake.calls["/api/v2/payment/get_escrow_detail"] == 2


def test_resync_is_idempotent_and_updates_status_and_fees(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    now = fake.now
    fake.add_order(
        "R1", status="READY_TO_SHIP", create_time=now - DAY, items=[_item("SKU-A", 40.0)]
    )
    _sync(client, auth_headers)

    fake.add_order(
        "R1",
        status="COMPLETED",
        create_time=now - DAY,
        update_time=now,
        items=[_item("SKU-A", 40.0)],
        escrow={"commission_fee": 6.0},
    )
    run = _sync(client, auth_headers)
    assert (run["orders_created"], run["orders_updated"]) == (0, 1)
    order = db.scalar(select(Order).where(Order.order_sn == "R1"))
    assert order is not None and order.status == "completed" and order.fees_final
    fee = db.scalar(select(OrderItem.commission_fee).where(OrderItem.order_id == order.id))
    assert fee == Decimal("6.00")
    assert len(db.scalars(select(Order)).all()) == 1

    escrow_calls = fake.calls["/api/v2/payment/get_escrow_detail"]
    fake.shops[SHOP_ID].update_times["R1"] = now  # appears again in the window
    _sync(client, auth_headers)
    assert fake.calls["/api/v2/payment/get_escrow_detail"] == escrow_calls  # final: not refetched


def test_backfill_is_split_into_15_day_windows(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee
) -> None:
    _connect(client, auth_headers)
    for i in range(130):  # > one page (100)
        fake.add_order(
            f"B{i:03d}",
            status="UNPAID",
            create_time=fake.now - 80 * DAY + i * 600,
            items=[_item("SKU-A", 1.0)],
        )
    run = _sync(client, auth_headers)
    assert run["orders_created"] == 130
    assert fake.calls["/api/v2/order/get_order_list"] >= 7  # 90 days / 15 + pagination


def test_invalid_orders_are_skipped_not_fatal(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee
) -> None:
    _connect(client, auth_headers)
    now = fake.now
    fake.add_order("OK1", status="UNPAID", create_time=now, items=[_item("SKU-A", 1.0)])
    fake.add_order("NOSKU", status="UNPAID", create_time=now, items=[_item("", 1.0)])
    fake.add_order(
        "EVIL", status="UNPAID", create_time=now, items=[_item("SKU-E", 1.0, name="=cmd()")]
    )
    fake.add_order("WEIRD", status="SOMETHING_NEW", create_time=now, items=[_item("SKU-A", 1.0)])
    fake.add_order("EMPTY", status="UNPAID", create_time=now, items=[])
    fake.add_order(
        "TWOPRICE",
        status="UNPAID",
        create_time=now,
        items=[_item("SKU-A", 1.0), _item("SKU-A", 2.0)],
    )
    run = _sync(client, auth_headers)
    assert (run["orders_created"], run["orders_skipped"]) == (1, 5)


def test_expired_token_is_refreshed_and_rotated(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    conn = db.scalar(select(ShopeeConnection))
    assert conn is not None
    old_refresh = crypto.decrypt(conn.refresh_token_enc)
    # Server-side expiry the app does not know about: the call fails, refresh, retry.
    fake.expire_all_access_tokens()
    assert _sync(client, auth_headers)["status"] == "success"
    assert fake.calls["/api/v2/auth/access_token/get"] == 1
    db.expire_all()
    conn = db.scalar(select(ShopeeConnection))
    assert conn is not None and crypto.decrypt(conn.refresh_token_enc) != old_refresh
    # Locally known expiry: refreshed proactively before calling.
    db.execute(update(ShopeeConnection).values(access_expires_at=datetime.now(UTC)))
    db.commit()
    _sync(client, auth_headers)
    assert fake.calls["/api/v2/auth/access_token/get"] == 2


def test_api_failure_is_recorded_without_secrets(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    fake.fail_next = [("/api/v2/order/get_order_list", 400, "error_shop_frozen")]
    run = _sync(client, auth_headers)
    assert run["status"] == "error"
    assert run["error"].startswith("error_shop_frozen")
    for token in list(fake.access) + list(fake.refresh):
        assert token not in run["error"]
    runs = client.get("/api/shopee/status", headers=auth_headers).json()["runs"]
    assert runs[0]["status"] == "error"


def test_sync_requires_connection_and_editor(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    assert client.post("/api/shopee/sync", headers=auth_headers).status_code == 404
    db.execute(update(User).values(role="viewer"))
    db.commit()
    assert client.post("/api/shopee/sync", headers=auth_headers).status_code == 403


def test_queue_is_idempotent_and_waits_for_a_busy_shop(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee
) -> None:
    _connect(client, auth_headers)
    seller_id = client.get("/api/auth/me", headers=auth_headers).json()["seller_id"]
    first = client.post("/api/shopee/sync", headers=auth_headers).json()
    again = client.post("/api/shopee/sync", headers=auth_headers).json()
    assert again["id"] == first["id"]  # a second click does not queue twice
    with shopee_sync.seller_lock(seller_id):  # e.g. the scheduled cycle is running
        assert worker.process_queue() == 0
    runs = client.get("/api/shopee/status", headers=auth_headers).json()["runs"]
    assert runs[0]["status"] == "queued"
    assert worker.process_queue() == 1
    runs = client.get("/api/shopee/status", headers=auth_headers).json()["runs"]
    assert runs[0]["status"] == "success"


# ---- stock sync ---------------------------------------------------------------


def test_stock_sync_by_sku(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    fake.add_order("S1", status="UNPAID", create_time=fake.now, items=[_item("SKU-A", 1.0)])
    shop = fake.shops[SHOP_ID]
    shop.items = [
        {"item_id": 1, "item_sku": "SKU-A", "item_name": "A", "has_model": False, **stock(7)},
        {"item_id": 2, "item_sku": "", "item_name": "Shirt", "has_model": True},
        {"item_id": 3, "item_sku": "", "item_name": "No sku", "has_model": False, **stock(3)},
        {"item_id": 4, "item_sku": "BAD SKU!", "item_name": "x", "has_model": False, **stock(1)},
    ] + [
        {
            "item_id": 100 + i,
            "item_sku": f"BULK-{i}",
            "item_name": f"Bulk {i}",
            "has_model": False,
            **stock(i),
        }
        for i in range(120)
    ]
    shop.models[2] = [
        {"model_sku": "SHIRT-M", **stock(4)},
        {"model_sku": "", **stock(9)},
    ]
    run = _sync(client, auth_headers)
    assert run["status"] == "success"
    by_sku = {p.sku: p for p in db.scalars(select(Product))}
    assert by_sku["SKU-A"].stock_quantity == 7
    assert by_sku["SHIRT-M"].stock_quantity == 4 and by_sku["SHIRT-M"].name == "Shirt"
    assert "BAD SKU!" not in by_sku
    assert by_sku["BULK-119"].stock_quantity == 119
    assert run["products_stock_updated"] == 1 + 1 + 120
    # Unchanged stock is not counted again.
    assert _sync(client, auth_headers)["products_stock_updated"] == 0


# ---- misc ---------------------------------------------------------------------


def test_disconnect_and_isolation(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    fake: FakeShopee,
    db: Session,
) -> None:
    _connect(client, auth_headers)
    assert client.get("/api/shopee/status", headers=other_headers).json()["connected"] is False
    assert client.post("/api/shopee/sync", headers=other_headers).status_code == 404
    assert client.delete("/api/shopee/connection", headers=other_headers).status_code == 404
    assert client.delete("/api/shopee/connection", headers=auth_headers).status_code == 204
    assert db.scalar(select(ShopeeConnection)) is None


def test_reconnecting_replaces_connection(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    fake.codes["code-2"] = 99_002
    fake.shops[99_002] = fake_shopee.FakeShop()
    url = client.post("/api/shopee/connect", headers=auth_headers).json()["authorization_url"]
    state = parse_qs(urlparse(parse_qs(urlparse(url).query)["redirect"][0]).query)["state"][0]
    client.get(
        "/api/shopee/callback",
        params={"state": state, "code": "code-2", "shop_id": 99_002},
        follow_redirects=False,
    )
    conns = db.scalars(select(ShopeeConnection)).all()
    assert [c.shop_id for c in conns] == [99_002]


def test_crypto_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "token_encryption_key", None)
    with pytest.raises(crypto.TokenCryptoError):
        crypto.encrypt("x")
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr("not-a-fernet-key"))
    with pytest.raises(crypto.TokenCryptoError):
        crypto.encrypt("x")
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    token = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    with pytest.raises(crypto.TokenCryptoError, match="key changed"):
        crypto.decrypt(token)


def test_stale_runs_are_closed(
    db: Session, client: TestClient, auth_headers: dict[str, str]
) -> None:
    seller_id = client.get("/api/auth/me", headers=auth_headers).json()["seller_id"]
    db.add(
        SyncRun(
            seller_id=seller_id,
            trigger="scheduled",
            status="running",
            started_at=datetime.now(UTC) - timedelta(hours=3),
        )
    )
    db.commit()
    shopee_sync.mark_stale_runs(db)
    assert db.scalar(select(SyncRun.status)) == "error"


def test_make_client_requires_configuration() -> None:
    with pytest.raises(shopee_sync.ShopeeNotConfiguredError):
        shopee_sync.make_client()


# ---- regression tests from code review ------------------------------------------


def test_oauth_link_from_another_browser_is_rejected(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    """An attacker's authorization link must not work in the victim's browser."""
    url = client.post("/api/shopee/connect", headers=auth_headers).json()["authorization_url"]
    state = parse_qs(urlparse(parse_qs(urlparse(url).query)["redirect"][0]).query)["state"][0]
    from app.main import create_app

    victim_browser = TestClient(create_app())  # no ssi_oauth_state cookie
    resp = victim_browser.get(
        "/api/shopee/callback",
        params={"state": state, "code": "good-code", "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/#shopee=error:invalid_state"
    assert db.scalar(select(ShopeeConnection)) is None
    assert fake.calls["/api/v2/auth/token/get"] == 0


def test_oauth_cookie_is_scoped_and_cleared(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee
) -> None:
    resp = client.post("/api/shopee/connect", headers=auth_headers)
    cookie = resp.headers["set-cookie"].lower()
    for flag in ("ssi_oauth_state=", "httponly", "path=/api/shopee/callback", "samesite=lax"):
        assert flag in cookie
    assert _connect(client, auth_headers) == "/#shopee=connected"


def test_failed_escrow_is_retried_on_later_syncs(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    fake.add_order(
        "E1",
        status="COMPLETED",
        create_time=fake.now - 2 * DAY,
        items=[_item("SKU-A", 50.0)],
        escrow={"commission_fee": 7.0},
    )
    fake.fail_next = [("/api/v2/payment/get_escrow_detail", 400, "error_busy")]
    _sync(client, auth_headers)
    order = db.scalar(select(Order).where(Order.order_sn == "E1"))
    assert order is not None and not order.fees_final
    # E1 is not updated again on Shopee's side: it falls behind the high-water mark,
    # but the pending-escrow re-check still picks it up.
    _sync(client, auth_headers)
    db.expire_all()
    order = db.scalar(select(Order).where(Order.order_sn == "E1"))
    assert order is not None and order.fees_final
    fee = db.scalar(select(OrderItem.commission_fee).where(OrderItem.order_id == order.id))
    assert fee == Decimal("7.00")


def test_brazil_net_fees_take_precedence() -> None:
    fees = shopee_sync._fees_from_escrow(
        {
            "commission_fee": 20.0,
            "net_commission_fee": 18.0,
            "service_fee": 5.0,
            "net_service_fee": 4.0,
            "credit_card_transaction_fee": 1.0,
        }
    )
    assert fees["commission_fee"] == Decimal("18.00")
    assert fees["service_fee"] == Decimal("5.00")  # 4 net service + 1 transaction


def test_unexpected_errors_end_the_run_cleanly(
    client: TestClient,
    auth_headers: dict[str, str],
    fake: FakeShopee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect(client, auth_headers)

    def broken(*_a: object, **_k: object) -> None:
        raise KeyError("item_id")

    monkeypatch.setattr(shopee_sync, "_sync_stock", broken)
    run = _sync(client, auth_headers)
    assert (run["status"], run["error"]) == ("error", "internal_error: KeyError")
    monkeypatch.setattr(
        shopee_sync, "_sync_stock", lambda *_a: (_ for _ in ()).throw(crypto.TokenCryptoError())
    )
    run = _sync(client, auth_headers)
    assert run["error"].startswith("token_decryption_failed")


def test_long_skus_are_supported(
    client: TestClient, auth_headers: dict[str, str], fake: FakeShopee, db: Session
) -> None:
    _connect(client, auth_headers)
    long_sku = "SKU-" + "X" * 90  # 94 chars: valid for products.sku (100)
    fake.add_order("L1", status="UNPAID", create_time=fake.now, items=[_item(long_sku, 3.0)])
    fake.shops[SHOP_ID].items = [
        {
            "item_id": 9,
            "item_sku": "NEW-" + "Y" * 90,
            "item_name": "Long",
            "has_model": False,
            **stock(2),
        }
    ]
    assert _sync(client, auth_headers)["orders_created"] == 1
    skus = set(db.scalars(select(Product.sku)))
    assert long_sku in skus and "NEW-" + "Y" * 90 in skus
