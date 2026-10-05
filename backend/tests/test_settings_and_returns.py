from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Product, User
from app.services.alerts import compute_alerts
from tests.factories import Line, build_csv

PERIOD = {"start": "2025-09-01", "end": "2025-09-30"}


def _upload(client: TestClient, headers: dict[str, str], lines: list[Line]) -> None:
    resp = client.post("/api/uploads", headers=headers, files={"file": ("o.csv", build_csv(lines))})
    assert resp.status_code == 201, resp.text


def _seller_id(client: TestClient, headers: dict[str, str]) -> int:
    return int(client.get("/api/auth/me", headers=headers).json()["seller_id"])


# ---- settings -------------------------------------------------------------------


def test_default_settings(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = client.get("/api/settings", headers=auth_headers).json()
    assert body == {
        "name": "Shop A",
        "stalled_days": 30,
        "min_margin_pct": "15.00",
        "max_return_rate_pct": "10.00",
        "weekly_email": False,
    }


def test_update_settings(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.patch(
        "/api/settings",
        headers=auth_headers,
        json={
            "stalled_days": 45,
            "min_margin_pct": "20.5",
            "weekly_email": True,
            "name": "New name",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["stalled_days"], body["min_margin_pct"], body["weekly_email"]) == (
        45,
        "20.50",
        True,
    )
    assert client.get("/api/auth/me", headers=auth_headers).json()["shop_name"] == "New name"


def test_invalid_settings_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    for payload in (
        {"stalled_days": 0},
        {"stalled_days": 400},
        {"min_margin_pct": "101"},
        {"max_return_rate_pct": "-1"},
        {"name": "x"},
    ):
        assert client.patch("/api/settings", headers=auth_headers, json=payload).status_code == 422


def test_viewer_cannot_change_settings(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    db.execute(update(User).values(role="viewer"))
    db.commit()
    assert client.get("/api/settings", headers=auth_headers).status_code == 200
    resp = client.patch("/api/settings", headers=auth_headers, json={"stalled_days": 5})
    assert resp.status_code == 403


def test_settings_are_per_shop(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    client.patch("/api/settings", headers=auth_headers, json={"stalled_days": 7})
    assert client.get("/api/settings", headers=other_headers).json()["stalled_days"] == 30
    assert client.get("/api/settings").status_code == 401


def test_alerts_follow_saved_thresholds(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(client, auth_headers, [Line("O1", "SKU-A", "100.00", date="2025-08-15 10:00")])
    db.execute(update(Product).values(stock_quantity=10, low_stock_threshold=0))
    db.commit()
    seller_id = _seller_id(client, auth_headers)
    today = date(2025, 9, 30)
    assert [a.kind for a in compute_alerts(db, seller_id, today=today)] == ["stalled_product"]
    client.patch("/api/settings", headers=auth_headers, json={"stalled_days": 60})
    db.expire_all()
    assert compute_alerts(db, seller_id, today=today) == []


# ---- returns and cancellations ------------------------------------------------


def test_return_and_cancellation_counts(client: TestClient, auth_headers: dict[str, str]) -> None:
    _upload(
        client,
        auth_headers,
        [
            Line("S1", "SKU-A", "10.00", qty=8),
            Line("R1", "SKU-A", "10.00", qty=2, status="Devolução/Reembolso"),
            Line("C1", "SKU-A", "10.00", qty=5, status="Cancelado"),
            Line("R2", "SKU-B", "30.00", qty=1, status="Devolução/Reembolso"),
        ],
    )
    body = client.get("/api/metrics/products", headers=auth_headers, params=PERIOD).json()
    by_sku = {p["sku"]: p for p in body}
    a = by_sku["SKU-A"]
    assert (a["units"], a["returned_units"], a["cancelled_units"]) == (8, 2, 5)
    assert a["return_rate_pct"] == 20.0  # 2 returned / (8 kept + 2 returned)
    assert Decimal(a["revenue"]) == Decimal("80.00")
    b = by_sku["SKU-B"]  # only returned: still listed so the problem is visible
    assert (b["units"], b["returned_units"], b["return_rate_pct"]) == (0, 1, 100.0)
    assert Decimal(b["revenue"]) == Decimal("0.00") and b["margin"] is None
    abc = client.get("/api/metrics/abc", headers=auth_headers, params=PERIOD).json()
    assert {i["sku"]: i["abc_class"] for i in abc}["SKU-B"] == "C"


def test_high_return_rate_alert(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(
        client,
        auth_headers,
        [
            Line("S1", "SKU-A", "10.00", qty=6, date="2025-09-20 10:00"),
            Line(
                "R1", "SKU-A", "10.00", qty=4, date="2025-09-21 10:00", status="Devolução/Reembolso"
            ),
            # One return only: below the noise floor, no alert.
            Line("S2", "SKU-B", "10.00", qty=1, date="2025-09-20 10:00"),
            Line(
                "R2", "SKU-B", "10.00", qty=1, date="2025-09-21 10:00", status="Devolução/Reembolso"
            ),
        ],
    )
    alerts = compute_alerts(db, _seller_id(client, auth_headers), today=date(2025, 9, 30))
    returns = [a for a in alerts if a.kind == "high_returns"]
    assert [a.sku for a in returns] == ["SKU-A"]
    assert "40.0%" in returns[0].message
    client.patch("/api/settings", headers=auth_headers, json={"max_return_rate_pct": "50"})
    db.expire_all()
    alerts = compute_alerts(db, _seller_id(client, auth_headers), today=date(2025, 9, 30))
    assert not [a for a in alerts if a.kind == "high_returns"]


def test_returns_are_isolated_between_shops(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    _upload(client, other_headers, [Line("R1", "SKU-X", "10.00", status="Devolução/Reembolso")])
    body = client.get("/api/metrics/products", headers=auth_headers, params=PERIOD).json()
    assert body == []


def test_unknown_seller_has_no_alerts(db: Session) -> None:
    assert compute_alerts(db, 999_999, today=date(2025, 9, 30)) == []
