from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Product
from app.services.alerts import compute_alerts
from tests.factories import Line, build_csv

TODAY = date(2025, 9, 30)


def _upload(client: TestClient, headers: dict[str, str], lines: list[Line]) -> None:
    resp = client.post("/api/uploads", headers=headers, files={"file": ("o.csv", build_csv(lines))})
    assert resp.status_code == 201, resp.text


def _set(db: Session, sku: str, **values: object) -> None:
    db.execute(update(Product).where(Product.sku == sku).values(**values))
    db.commit()


def _seller_id(client: TestClient, headers: dict[str, str]) -> int:
    return int(client.get("/api/auth/me", headers=headers).json()["seller_id"])


def test_alert_rules(client: TestClient, auth_headers: dict[str, str], db: Session) -> None:
    _upload(
        client,
        auth_headers,
        [
            # Recent seller with healthy margin and low stock.
            Line("R1", "LOW", "100.00", date="2025-09-28 10:00"),
            # Last sale 60 days ago -> stalled.
            Line("S1", "STALE", "50.00", date="2025-08-01 10:00"),
            # Recent but heavy fees -> low margin.
            Line("M1", "THIN", "100.00", date="2025-09-25 10:00", commission="20"),
            # Old sale but out of stock -> not stalled (nothing sitting on the shelf).
            Line("Z1", "EMPTY", "10.00", date="2025-06-01 10:00"),
            # Only a cancelled sale recently -> counts as no sale.
            Line("C1", "CANCEL", "10.00", date="2025-09-29 10:00", status="Cancelado"),
        ],
    )
    _set(db, "LOW", stock_quantity=2, low_stock_threshold=5, unit_cost=Decimal("10"))
    _set(db, "STALE", stock_quantity=40, unit_cost=Decimal("10"))
    _set(db, "THIN", stock_quantity=100, unit_cost=Decimal("75"))  # 100-20-75 = 5%
    _set(db, "EMPTY", stock_quantity=0, low_stock_threshold=0)

    alerts = compute_alerts(
        db,
        _seller_id(client, auth_headers),
        today=TODAY,
        stalled_days=30,
        min_margin_pct=15.0,
    )
    got = {(a.kind, a.sku) for a in alerts}
    assert got == {
        ("low_stock", "LOW"),
        ("stalled_product", "STALE"),
        ("stalled_product", "CANCEL"),
        ("low_margin", "THIN"),
        ("low_stock", "EMPTY"),
    }
    stale = next(a for a in alerts if a.sku == "STALE")
    assert "60 days" in stale.message
    never = next(a for a in alerts if a.sku == "CANCEL")
    assert "No sales" in never.message


def test_untracked_stock_has_no_low_stock_alert(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(client, auth_headers, [Line("R1", "SKU-A", "10.00", date="2025-09-28 10:00")])
    alerts = compute_alerts(
        db, _seller_id(client, auth_headers), today=TODAY, stalled_days=30, min_margin_pct=0
    )
    assert alerts == []


def test_alerts_endpoint_scoped_and_validated(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    db: Session,
) -> None:
    _upload(client, auth_headers, [Line("R1", "SKU-A", "10.00")])
    _set(db, "SKU-A", stock_quantity=0)
    mine = client.get("/api/alerts", headers=auth_headers).json()
    assert any(a["sku"] == "SKU-A" and a["kind"] == "low_stock" for a in mine)
    assert client.get("/api/alerts", headers=other_headers).json() == []
    bad = client.get("/api/alerts", headers=auth_headers, params={"stalled_days": 0})
    assert bad.status_code == 422
    assert client.get("/api/alerts").status_code == 401
