"""Performance benchmark on a large synthetic dataset.

Usage (against a DISPOSABLE database, it truncates all tables):

    TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/shopee_insights_test \\
        python -m benchmarks.benchmark [--orders 200000] [--budget-ms 500]

What it does:
1. Loads one large shop (default 200k orders, ~300k items, 500 products) plus
   20 smaller "noise" shops, with plain bulk inserts (not the importer, for speed).
2. Calls every read endpoint in-process (FastAPI TestClient, real PostgreSQL) and
   reports p50/p95 latency for a 30-day and a 365-day period.
3. Times a maximum-size upload (50,000 rows) through the real importer.
4. Prints the PostgreSQL plan of the core metrics query to show index usage.

It fails (exit 1) when a p95 latency exceeds --budget-ms.
"""

import argparse
import os
import random
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/shopee_insights_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MIGRATION_DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "bench-jwt-secret-" + "x" * 32)
os.environ.setdefault("PII_HASH_SECRET", "bench-pii-secret-" + "y" * 32)
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ["LOGIN_RATE_LIMIT"] = "1000000"
os.environ["UPLOAD_RATE_LIMIT"] = "1000000"

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import insert, select, text  # noqa: E402

from alembic import command  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Order, OrderItem, Product, Seller, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.metrics import _revenue_items  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date.today()
STATUSES = (
    ["completed"] * 85 + ["shipped"] * 5 + ["cancelled"] * 6 + ["returned"] * 2 + ["unpaid"] * 2
)
BATCH = 20_000


def migrate_and_truncate() -> None:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    command.upgrade(cfg, "head")
    with get_engine().begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'"
            )
        ).scalars()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


def load_shop(seller_name: str, n_orders: int, n_products: int, rng: random.Random) -> int:
    """Bulk-insert one shop; returns its seller id."""
    with get_engine().begin() as conn:
        seller_id = conn.execute(
            insert(Seller)
            .values(
                name=seller_name,
                shop_code=seller_name.lower().replace(" ", "-"),
                created_at=datetime.now(UTC),
            )
            .returning(Seller.id)
        ).scalar_one()
        conn.execute(
            insert(Product),
            [
                {
                    "seller_id": seller_id,
                    "sku": f"SKU-{i:05d}",
                    "name": f"Product {i}",
                    "unit_cost": Decimal(rng.randint(100, 5000)) / 100,
                    "stock_quantity": rng.randint(0, 300),
                    "low_stock_threshold": 10,
                    "created_at": datetime.now(UTC),
                }
                for i in range(n_products)
            ],
        )
        product_ids = (
            conn.execute(select(Product.id).where(Product.seller_id == seller_id)).scalars().all()
        )
    weights = [1 / (r + 1) ** 1.1 for r in range(len(product_ids))]
    start = datetime.now(UTC) - timedelta(days=400)
    for offset in range(0, n_orders, BATCH):
        size = min(BATCH, n_orders - offset)
        with get_engine().begin() as conn:
            order_ids = (
                conn.execute(
                    insert(Order).returning(Order.id),
                    [
                        {
                            "seller_id": seller_id,
                            "order_sn": f"{seller_id}-{offset + i:08d}",
                            "status": rng.choice(STATUSES),
                            "ordered_at": start + timedelta(seconds=rng.randint(0, 400 * 86400)),
                            "buyer_hash": None,
                            "source": "file",
                            "fees_final": False,
                            "created_at": datetime.now(UTC),
                        }
                        for i in range(size)
                    ],
                )
                .scalars()
                .all()
            )
            items = []
            for oid in order_ids:
                for pid in set(
                    rng.choices(product_ids, weights=weights, k=1 if rng.random() < 0.7 else 2)
                ):
                    price = Decimal(rng.randint(990, 19990)) / 100
                    items.append(
                        {
                            "order_id": oid,
                            "product_id": pid,
                            "quantity": rng.randint(1, 3),
                            "unit_price": price,
                            "commission_fee": (price * Decimal("0.14")).quantize(Decimal("0.01")),
                            "service_fee": (price * Decimal("0.06")).quantize(Decimal("0.01")),
                            "seller_shipping_fee": Decimal("0"),
                            "seller_voucher": Decimal("0"),
                        }
                    )
            conn.execute(insert(OrderItem), items)
    return seller_id


def timed(client: TestClient, headers: dict[str, str], url: str, runs: int) -> list[float]:
    client.get(url, headers=headers)  # warm-up (plans, caches)
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        resp = client.get(url, headers=headers)
        out.append((time.perf_counter() - t0) * 1000)
        assert resp.status_code == 200, (url, resp.status_code, resp.text[:200])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopee Seller Insights benchmark")
    parser.add_argument("--orders", type=int, default=200_000)
    parser.add_argument("--products", type=int, default=500)
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--budget-ms", type=float, default=500.0)
    args = parser.parse_args()
    rng = random.Random(42)

    print(f"Loading {args.orders:,} orders for the main shop + 20 noise shops...")
    t0 = time.perf_counter()
    migrate_and_truncate()
    seller_id = load_shop("Big Shop", args.orders, args.products, rng)
    for n in range(20):
        load_shop(f"Noise Shop {n}", args.orders // 20, 50, rng)
    with get_engine().begin() as conn:
        conn.execute(
            insert(User).values(
                seller_id=seller_id,
                email="bench@example.com",
                password_hash=hash_password("bench-password-1"),
                role="owner",
                created_at=datetime.now(UTC),
            )
        )
        conn.execute(text("ANALYZE"))
        counts = conn.execute(
            text("SELECT (SELECT count(*) FROM orders), (SELECT count(*) FROM order_items)")
        ).one()
    print(f"Loaded {counts[0]:,} orders / {counts[1]:,} items in {time.perf_counter() - t0:.1f}s\n")

    client = TestClient(create_app())
    token = client.post(
        "/api/auth/login", data={"username": "bench@example.com", "password": "bench-password-1"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    worst = 0.0
    print(f"{'endpoint':<52}{'p50 ms':>9}{'p95 ms':>9}")
    for days in (30, 365):
        period = f"start={TODAY - timedelta(days=days - 1)}&end={TODAY}"
        for path in ("overview", "daily", "products", "abc"):
            samples = timed(client, headers, f"/api/metrics/{path}?{period}", args.runs)
            p50, p95 = statistics.median(samples), statistics.quantiles(samples, n=20)[18]
            worst = max(worst, p95)
            print(f"{f'/api/metrics/{path} ({days} days)':<52}{p50:>9.1f}{p95:>9.1f}")
    for path in (
        "/api/alerts",
        "/api/products",
        f"/api/metrics/products/export.csv?start={TODAY - timedelta(days=364)}&end={TODAY}",
    ):
        samples = timed(client, headers, path, args.runs)
        p50, p95 = statistics.median(samples), statistics.quantiles(samples, n=20)[18]
        worst = max(worst, p95)
        print(
            f"{path.split('?')[0] + (' (365 days)' if '?' in path else ''):<52}{p50:>9.1f}{p95:>9.1f}"
        )

    # Largest allowed upload through the real importer (validation + idempotent upsert).
    lines = [
        "ID do pedido,Status do pedido,Data de criação do pedido,Número de referência SKU,"
        "Nome do Produto,Preço acordado,Quantidade,Taxa de comissão"
    ]
    for i in range(50_000):
        lines.append(
            f"UP{i:07d},Concluído,{TODAY - timedelta(days=i % 90)} 12:00,SKU-{i % 400:05d},"
            f'Product {i % 400},"19,90",1,"2,79"'
        )
    payload = ("\n".join(lines) + "\n").encode()
    t0 = time.perf_counter()
    resp = client.post("/api/uploads", headers=headers, files={"file": ("big.csv", payload)})
    upload_s = time.perf_counter() - t0
    print(
        f"\nUpload of 50,000 rows ({len(payload) / 1e6:.1f} MB): {upload_s:.1f}s -> {resp.status_code}"
    )
    t0 = time.perf_counter()
    again = client.post("/api/uploads", headers=headers, files={"file": ("big.csv", payload)})
    print(
        f"Re-upload of the same file (idempotent): {time.perf_counter() - t0:.1f}s -> "
        f"{again.json().get('orders_created')} new orders"
    )

    stmt = _revenue_items(seller_id, TODAY - timedelta(days=29), TODAY).with_only_columns(
        OrderItem.id
    )
    compiled = stmt.compile(get_engine(), compile_kwargs={"literal_binds": True})
    with get_engine().connect() as conn:
        plan = (
            conn.execute(text(f"EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) {compiled}"))
            .scalars()
            .all()
        )
    print("\nPlan of the core 30-day metrics query:")
    print("\n".join("  " + line for line in plan))

    print(f"\nWorst p95: {worst:.1f} ms (budget {args.budget_ms:.0f} ms)")
    if worst > args.budget_ms or resp.status_code != 201:
        print("FAIL: performance budget exceeded")
        sys.exit(1)
    print("OK: within budget")


if __name__ == "__main__":
    main()
