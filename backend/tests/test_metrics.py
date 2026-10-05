from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Product
from app.services.metrics import classify_abc, pct_change
from tests.factories import Line, build_csv

PERIOD = {"start": "2025-09-01", "end": "2025-09-30"}


def _upload(client: TestClient, headers: dict[str, str], lines: list[Line]) -> None:
    resp = client.post("/api/uploads", headers=headers, files={"file": ("o.csv", build_csv(lines))})
    assert resp.status_code == 201, resp.text


def _set_cost(db: Session, sku: str, cost: str) -> None:
    db.execute(update(Product).where(Product.sku == sku).values(unit_cost=Decimal(cost)))
    db.commit()


# --- pure functions -------------------------------------------------------


def test_classify_abc_thresholds() -> None:
    result = classify_abc(
        [(1, Decimal("70")), (2, Decimal("20")), (3, Decimal("6")), (4, Decimal("4"))]
    )
    assert [c for _, c, _, _ in result] == ["A", "B", "C", "C"]
    shares = [s for _, _, s, _ in result]
    assert shares == [70.0, 20.0, 6.0, 4.0]
    assert result[-1][3] == 100.0


def test_classify_abc_dominant_product_is_a() -> None:
    # A single product with 90% of revenue must still be class A.
    result = classify_abc([(1, Decimal("90")), (2, Decimal("10"))])
    assert [c for _, c, _, _ in result] == ["A", "C"]


def test_classify_abc_empty_and_zero() -> None:
    assert classify_abc([]) == []
    assert [c for _, c, _, _ in classify_abc([(1, Decimal("0"))])] == ["C"]


def test_pct_change() -> None:
    assert pct_change(Decimal("150"), Decimal("100")) == 50.0
    assert pct_change(Decimal("5"), Decimal("0")) is None


# --- API ------------------------------------------------------------------


def test_margin_math_per_product(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    # Order 1: 2 x A @100 + 2 x B @50 => gross 300. Commission 30, service 6,
    # seller shipping 9, voucher 15 -> allocated 2/3 to A and 1/3 to B.
    _upload(
        client,
        auth_headers,
        [
            Line(
                "O1", "SKU-A", "100.00", 2, commission="30", service="6", shipping="9", voucher="15"
            ),
            Line(
                "O1", "SKU-B", "50.00", 2, commission="30", service="6", shipping="9", voucher="15"
            ),
            Line("O2", "SKU-A", "100.00", 1, status="Cancelado", commission="12"),
        ],
    )
    _set_cost(db, "SKU-A", "40.00")
    body = client.get("/api/metrics/products", headers=auth_headers, params=PERIOD).json()
    by_sku = {p["sku"]: p for p in body}

    a = by_sku["SKU-A"]
    assert Decimal(a["revenue"]) == Decimal("200.00")
    assert a["units"] == 2  # cancelled order excluded
    assert Decimal(a["commission_fee"]) == Decimal("20.00")
    assert Decimal(a["service_fee"]) == Decimal("4.00")
    assert Decimal(a["shipping_fee"]) == Decimal("6.00")
    assert Decimal(a["voucher"]) == Decimal("10.00")
    assert Decimal(a["product_cost"]) == Decimal("80.00")
    # 200 - 20 - 4 - 6 - 10 - 80 = 80
    assert Decimal(a["margin"]) == Decimal("80.00")
    assert a["margin_pct"] == pytest.approx(40.0)

    b = by_sku["SKU-B"]
    assert Decimal(b["revenue"]) == Decimal("100.00")
    assert b["product_cost"] is None
    assert b["margin"] is None


def test_overview_and_period_comparison(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(
        client,
        auth_headers,
        [
            Line("P1", "SKU-A", "100.00", 1, date="2025-08-15 12:00"),
            Line("C1", "SKU-A", "100.00", 1, date="2025-09-05 12:00", commission="10"),
            Line("C2", "SKU-A", "50.00", 2, date="2025-09-20 12:00"),
            Line("C3", "SKU-A", "999.00", 1, date="2025-09-21 12:00", status="Não pago"),
        ],
    )
    _set_cost(db, "SKU-A", "10.00")
    body = client.get("/api/metrics/overview", headers=auth_headers, params=PERIOD).json()
    cur, prev = body["current"], body["previous"]
    assert Decimal(cur["revenue"]) == Decimal("200.00")
    assert cur["orders"] == 2
    assert cur["units"] == 3
    assert Decimal(cur["avg_ticket"]) == Decimal("100.00")
    assert Decimal(cur["net_margin"]) == Decimal("160.00")  # 200 - 10 - 3*10
    assert body["previous_start"] == "2025-08-02"
    assert body["previous_end"] == "2025-08-31"
    assert Decimal(prev["revenue"]) == Decimal("100.00")
    assert body["change"]["revenue_pct"] == pytest.approx(100.0)
    assert body["change"]["orders_pct"] == pytest.approx(100.0)
    assert body["change"]["avg_ticket_pct"] == pytest.approx(0.0)


def test_net_margin_is_null_when_cost_missing(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _upload(client, auth_headers, [Line("C1", "SKU-A", "10.00", date="2025-09-05 12:00")])
    body = client.get("/api/metrics/overview", headers=auth_headers, params=PERIOD).json()
    assert body["current"]["net_margin"] is None
    assert Decimal(body["previous"]["avg_ticket"]) == Decimal("0")


def test_local_day_boundaries(client: TestClient, auth_headers: dict[str, str]) -> None:
    # 2025-09-30 23:30 in Brasília is 2025-10-01 02:30 UTC but still belongs to Sept 30.
    _upload(client, auth_headers, [Line("L1", "SKU-A", "10.00", date="2025-09-30 23:30")])
    daily = client.get("/api/metrics/daily", headers=auth_headers, params=PERIOD).json()
    assert daily == [{"day": "2025-09-30", "revenue": "10.00", "orders": 1}]


def test_abc_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    _upload(
        client,
        auth_headers,
        [
            Line("A1", "SKU-A", "700.00"),
            Line("A2", "SKU-B", "200.00"),
            Line("A3", "SKU-C", "100.00"),
        ],
    )
    body = client.get("/api/metrics/abc", headers=auth_headers, params=PERIOD).json()
    assert [(i["sku"], i["abc_class"]) for i in body] == [
        ("SKU-A", "A"),
        ("SKU-B", "B"),
        ("SKU-C", "C"),
    ]


def test_invalid_period(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.get(
        "/api/metrics/overview",
        headers=auth_headers,
        params={"start": "2025-09-30", "end": "2025-09-01"},
    )
    assert resp.status_code == 422
    resp = client.get(
        "/api/metrics/overview",
        headers=auth_headers,
        params={"start": "2020-01-01", "end": "2025-09-01"},
    )
    assert resp.status_code == 422


def test_default_period_works(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/metrics/overview", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["current"]["orders"] == 0


def test_cross_seller_isolation_in_metrics(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    _upload(client, auth_headers, [Line("X1", "SKU-A", "500.00")])
    _upload(client, other_headers, [Line("Y1", "SKU-OTHER", "7.00")])
    for path in ("overview", "daily", "products", "abc"):
        mine = client.get(f"/api/metrics/{path}", headers=auth_headers, params=PERIOD).text
        theirs = client.get(f"/api/metrics/{path}", headers=other_headers, params=PERIOD).text
        assert "SKU-OTHER" not in mine
        assert "500" not in theirs and "SKU-A" not in theirs
    overview = client.get("/api/metrics/overview", headers=other_headers, params=PERIOD).json()
    assert Decimal(overview["current"]["revenue"]) == Decimal("7.00")


def test_metrics_require_auth(client: TestClient) -> None:
    for path in ("overview", "daily", "products", "abc", "products/export.csv"):
        assert client.get(f"/api/metrics/{path}").status_code == 401


def test_csv_export_neutralizes_formulas(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(client, auth_headers, [Line("E1", "SKU-A", "10.00")])
    # Simulate a malicious name that reached the DB through another path.
    db.execute(update(Product).values(name="=cmd|'/C calc'!A0"))
    db.commit()
    resp = client.get("/api/metrics/products/export.csv", headers=auth_headers, params=PERIOD)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.splitlines()
    assert lines[0].startswith("sku,name,units,revenue")
    assert "'=cmd" in lines[1]
    assert ",=cmd" not in resp.text


def test_product_query_is_scoped_even_with_foreign_product_id(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str], db: Session
) -> None:
    _upload(client, other_headers, [Line("Y1", "SKU-B", "7.00")])
    other_id = db.scalar(select(Product.id).where(Product.sku == "SKU-B"))
    resp = client.patch(f"/api/products/{other_id}", headers=auth_headers, json={"unit_cost": "1"})
    assert resp.status_code == 404
