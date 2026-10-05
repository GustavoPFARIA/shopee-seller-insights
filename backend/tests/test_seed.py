from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import seed as seed_module
from app.models import Membership, Order, Product, Seller, User
from app.services.alerts import compute_alerts
from app.services.importer import parse_file
from app.services.metrics import abc_curve

TODAY = date(2025, 9, 30)


def test_generated_csv_is_valid_and_deterministic() -> None:
    a = seed_module.generate_csv(30, TODAY)
    assert a == seed_module.generate_csv(30, TODAY)
    rows = parse_file(a, "seed.csv", max_rows=100_000)
    assert len(rows) > 100
    assert {r.status for r in rows} >= {"completed", "cancelled"}


def test_seed_is_idempotent_and_realistic(db: Session) -> None:
    first = seed_module.seed(db, days=60, today=TODAY)
    assert first["orders_created"] > 300
    second = seed_module.seed(db, days=60, today=TODAY)
    assert second["orders_created"] == 0
    assert db.scalar(select(func.count()).select_from(Seller)) == 2
    roles = {
        email: role
        for email, role in db.execute(
            select(User.email, Membership.role)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.seller_id == demo_seller_id_for(db))
        )
    }
    assert roles[seed_module.VIEWER_EMAIL] == "viewer"
    assert roles[seed_module.DEMO_EMAIL] == "owner"

    demo_seller = db.scalar(select(Seller.id).where(Seller.shop_code == "demo-gadgets"))
    assert demo_seller is not None
    assert db.scalar(
        select(func.count()).select_from(Product).where(Product.seller_id == demo_seller)
    ) == len(seed_module.CATALOG)
    classes = {i.abc_class for i in abc_curve(db, demo_seller, date(2025, 9, 1), TODAY)}
    assert classes == {"A", "B", "C"}
    kinds = {
        a.kind
        for a in compute_alerts(db, demo_seller, today=TODAY, stalled_days=30, min_margin_pct=15)
    }
    assert kinds == {"low_stock", "stalled_product", "low_margin"}
    assert (db.scalar(select(func.count()).select_from(Order)) or 0) > 300


def test_main_writes_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "sample.csv"
    monkeypatch.setattr("sys.argv", ["seed", "--days", "5", "--csv-out", str(out)])
    seed_module.main()
    assert out.read_bytes().startswith(b"ID do pedido")


def test_main_seeds_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["seed", "--days", "3"])
    seed_module.main()
    out = capsys.readouterr().out
    assert "Demo accounts" in out
    assert seed_module.DEMO_PASSWORD not in out


def demo_seller_id_for(db: Session) -> int:
    seller_id = db.scalar(select(Seller.id).where(Seller.shop_code == "demo-gadgets"))
    assert seller_id is not None
    return seller_id
