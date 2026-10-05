from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Order, OrderItem, Product
from tests.factories import Line, build_csv


def _upload(
    client: TestClient, headers: dict[str, str], content: bytes, name: str = "orders.csv"
) -> tuple[int, dict]:  # type: ignore[type-arg]
    resp = client.post("/api/uploads", headers=headers, files={"file": (name, content)})
    return resp.status_code, resp.json()


SAMPLE = [
    Line("ORD1", "SKU-A", "100.00", qty=2, commission="30.00", voucher="10.00"),
    Line("ORD1", "SKU-B", "50.00", qty=2, commission="30.00", voucher="10.00"),
    Line("ORD2", "SKU-A", "100.00", status="Cancelado"),
]


def test_upload_creates_orders_products_and_allocates_fees(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    code, body = _upload(client, auth_headers, build_csv(SAMPLE))
    assert code == 201, body
    assert body["orders_created"] == 2
    assert body["products_created"] == 2
    assert body["row_count"] == 3

    items = db.scalars(select(OrderItem).join(Order).where(Order.order_sn == "ORD1")).all()
    by_price = {i.unit_price: i for i in items}
    # gross 200 vs 100 -> commission 30 split 20/10, voucher 10 split 6.66/3.34
    assert by_price[Decimal("100.00")].commission_fee == Decimal("20.00")
    assert by_price[Decimal("50.00")].commission_fee == Decimal("10.00")
    assert sum(i.seller_voucher for i in items) == Decimal("10.00")


def test_reupload_is_idempotent(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    content = build_csv(SAMPLE)
    _upload(client, auth_headers, content)
    code, body = _upload(client, auth_headers, content)
    assert code == 201
    assert body["orders_created"] == 0
    assert body["orders_unchanged"] == 2
    assert body["products_created"] == 0
    assert db.scalar(select(func.count()).select_from(Order)) == 2
    assert db.scalar(select(func.count()).select_from(OrderItem)) == 3
    assert db.scalar(select(func.count()).select_from(Product)) == 2


def test_reupload_updates_status(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _upload(client, auth_headers, build_csv(SAMPLE))
    changed = [Line("ORD1", "SKU-A", "100.00", status="Cancelado")]
    _, body = _upload(client, auth_headers, build_csv(changed))
    assert body["orders_updated"] == 1
    assert db.scalar(select(Order.status).where(Order.order_sn == "ORD1")) == "cancelled"


def test_pii_is_not_stored(client: TestClient, auth_headers: dict[str, str], db: Session) -> None:
    _upload(client, auth_headers, build_csv(SAMPLE))
    dump = (
        db.execute(
            text(
                "SELECT row_to_json(o)::text FROM orders o "
                "UNION ALL SELECT row_to_json(u)::text FROM uploads u"
            )
        )
        .scalars()
        .all()
    )
    blob = " ".join(dump)
    for secret in ("Joao", "98888", "Rua Secreta", "buyer_x"):
        assert secret not in blob
    hashes = db.scalars(select(Order.buyer_hash)).all()
    assert all(h is not None and len(h) == 64 for h in hashes)


def test_same_order_sn_is_independent_per_seller(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    db: Session,
) -> None:
    content = build_csv(SAMPLE)
    assert _upload(client, auth_headers, content)[1]["orders_created"] == 2
    assert _upload(client, other_headers, content)[1]["orders_created"] == 2
    assert db.scalar(select(func.count()).select_from(Order)) == 4


def test_upload_requires_auth(client: TestClient) -> None:
    resp = client.post("/api/uploads", files={"file": ("o.csv", build_csv(SAMPLE))})
    assert resp.status_code == 401


def test_oversized_upload_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    big = b"a" * (get_settings().max_upload_mb * 1024 * 1024 + 10)
    code, _ = _upload(client, auth_headers, big)
    assert code == 413


def test_malicious_formula_upload_rejected(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    evil = [Line("ORD9", "SKU-Z", "10.00", name='=HYPERLINK("http://evil")')]
    code, body = _upload(client, auth_headers, build_csv(evil))
    assert code == 422
    assert body["errors"][0]["field"] == "product_name"
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_disguised_binary_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    code, _ = _upload(client, auth_headers, b"MZ\x90\x00\x03binary", "orders.xlsx")
    assert code == 422
    code, _ = _upload(client, auth_headers, b"#!/bin/sh\nrm -rf /", "orders.sh")
    assert code == 422


def test_invalid_rows_do_not_partially_import(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    lines = [Line("ORD1", "SKU-A", "10.00"), Line("ORD2", "SKU-B", "-3.00")]
    code, body = _upload(client, auth_headers, build_csv(lines))
    assert code == 422
    assert body["errors"] == [
        {"row": 3, "field": "unit_price", "message": body["errors"][0]["message"]}
    ]
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_list_uploads_is_scoped(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    _upload(client, auth_headers, build_csv(SAMPLE))
    mine = client.get("/api/uploads", headers=auth_headers).json()
    theirs = client.get("/api/uploads", headers=other_headers).json()
    assert len(mine) == 1
    assert theirs == []


def test_upload_rate_limited(client: TestClient, auth_headers: dict[str, str]) -> None:
    limit = get_settings().upload_rate_limit
    codes = [_upload(client, auth_headers, b"", "e.csv")[0] for _ in range(limit + 1)]
    assert codes[-1] == 429
    assert 429 not in codes[:limit]


def test_concurrent_upload_race_counts_existing_as_unchanged(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orders inserted by another upload between our SELECT and INSERT are not duplicated."""
    import sqlalchemy

    _upload(client, auth_headers, build_csv(SAMPLE))
    real_select = sqlalchemy.select

    def select_hiding_orders(*cols: object) -> object:
        stmt = real_select(*cols)  # type: ignore[call-overload]
        if cols and getattr(cols[0], "key", None) == "order_sn":
            return stmt.where(False)  # simulate "not there yet" when we looked
        return stmt

    monkeypatch.setattr("app.services.importer.select", select_hiding_orders)
    code, body = _upload(client, auth_headers, build_csv(SAMPLE))
    assert code == 201, body
    assert (body["orders_created"], body["orders_unchanged"]) == (0, 2)
    assert db.scalar(select(func.count()).select_from(Order)) == 2
    assert db.scalar(select(func.count()).select_from(OrderItem)) == 3
