"""Generate realistic *fake* Shopee Seller Centre data and load it.

Usage:  python -m app.seed [--days 120] [--csv-out path.csv]

The generated CSV mimics the Brazilian Seller Centre order export (including fake
recipient name/phone/address columns) and is loaded through the same importer used
by the upload endpoint, so personal-data columns are dropped exactly as in production.
No real customer data is ever used.
"""

import argparse
import csv
import io
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Product, Seller, User
from app.security import hash_password
from app.services.importer import import_orders, parse_file

DEMO_EMAIL = "demo@shopee-insights.dev"
DEMO_PASSWORD = "DemoPassword123!"  # noqa: S105 (public demo account, documented in README)
SECOND_EMAIL = "other@shopee-insights.dev"

HEADER = [
    "ID do pedido",
    "Status do pedido",
    "Data de criação do pedido",
    "Número de referência SKU",
    "Nome do Produto",
    "Preço acordado",
    "Quantidade",
    "Taxa de comissão",
    "Taxa de serviço",
    "Taxa de envio paga pelo vendedor",
    "Cupom do vendedor",
    "Nome de usuário (comprador)",
    "Nome do destinatário",
    "Telefone",
    "Endereço de entrega",
]


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    price: Decimal
    cost: Decimal
    stock: int


CATALOG = [
    CatalogItem("CAPA-IP15-SIL", "Silicone Case iPhone 15", Decimal("39.90"), Decimal("9.50"), 180),
    CatalogItem(
        "PEL-VIDRO-3D", "3D Tempered Glass Screen Protector", Decimal("19.90"), Decimal("3.20"), 420
    ),
    CatalogItem("CABO-USBC-2M", "USB-C Braided Cable 2m", Decimal("29.90"), Decimal("8.90"), 260),
    CatalogItem("CARR-20W-PD", "20W PD Fast Wall Charger", Decimal("59.90"), Decimal("24.00"), 95),
    CatalogItem("FONE-BT-TWS", "TWS Bluetooth Earbuds", Decimal("89.90"), Decimal("41.00"), 60),
    CatalogItem(
        "SUP-CEL-CARRO", "Magnetic Car Phone Holder", Decimal("34.90"), Decimal("11.50"), 140
    ),
    CatalogItem(
        "RING-LED-26", "26cm LED Ring Light with Tripod", Decimal("119.90"), Decimal("58.00"), 25
    ),
    CatalogItem("PWR-10000", "10000mAh Power Bank", Decimal("99.90"), Decimal("52.00"), 40),
    CatalogItem(
        "SMW-PULS-SIL", "Silicone Smartwatch Strap", Decimal("24.90"), Decimal("4.10"), 300
    ),
    CatalogItem("MOUSE-SF-WL", "Silent Wireless Mouse", Decimal("49.90"), Decimal("19.90"), 70),
    CatalogItem("TECL-BT-MINI", "Mini Bluetooth Keyboard", Decimal("79.90"), Decimal("38.00"), 30),
    CatalogItem("HUB-USBC-5", "5-in-1 USB-C Hub", Decimal("129.90"), Decimal("71.00"), 18),
    CatalogItem(
        "CAPA-S23-ANT", "Shockproof Case Galaxy S23", Decimal("34.90"), Decimal("8.00"), 150
    ),
    CatalogItem("ORG-CABOS", "Cable Organizer Kit", Decimal("14.90"), Decimal("2.90"), 500),
    CatalogItem("LAMP-USB-LED", "USB LED Desk Lamp", Decimal("44.90"), Decimal("21.00"), 4),
    CatalogItem("TRIPE-FLEX", "Flexible Phone Tripod", Decimal("27.90"), Decimal("9.00"), 85),
    CatalogItem("PEN-64GB", "64GB USB Flash Drive", Decimal("39.90"), Decimal("23.00"), 3),
    CatalogItem("ADAPT-OTG", "USB-C OTG Adapter", Decimal("12.90"), Decimal("2.10"), 380),
    CatalogItem("CAIXA-SOM-MINI", "Mini Bluetooth Speaker", Decimal("69.90"), Decimal("55.00"), 22),
    CatalogItem("SUP-NOTE-ALU", "Aluminium Laptop Stand", Decimal("109.90"), Decimal("97.00"), 35),
    CatalogItem("WEBCAM-1080", "1080p USB Webcam", Decimal("149.90"), Decimal("88.00"), 12),
    CatalogItem("PAD-MOUSE-XL", "XL Desk Mouse Pad", Decimal("32.90"), Decimal("10.50"), 0),
]

CENT = Decimal("0.01")
COMMISSION_RATE = Decimal("0.14")
SERVICE_RATE = Decimal("0.06")
SERVICE_CAP = Decimal("100.00")


def _money(value: Decimal) -> str:
    """Format like the Brazilian export ('1234,56')."""
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP)).replace(".", ",")


def generate_csv(days: int, today: date, seed: int = 42) -> bytes:
    rng = random.Random(seed)  # noqa: S311 (fake data, not security sensitive)
    weights = [1 / (rank + 1) ** 1.1 for rank in range(len(CATALOG))]  # Zipf-like popularity
    stalled = {"SUP-NOTE-ALU", "WEBCAM-1080"}  # stop selling 45 days ago -> stalled alerts
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    seq = 0
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        weekend_boost = 1.35 if day.weekday() >= 5 else 1.0
        trend = 0.7 + 0.6 * (days - offset) / max(days, 1)  # shop is growing
        n_orders = max(0, int(rng.gauss(14 * weekend_boost * trend, 4)))
        for _ in range(n_orders):
            seq += 1
            order_sn = f"{day:%y%m%d}{seq:06d}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
            when = datetime(day.year, day.month, day.day, rng.randint(7, 23), rng.randint(0, 59))
            roll = rng.random()
            if offset <= 3 and roll < 0.35:
                status = rng.choice(["A enviar", "Enviado"])
            elif roll < 0.06:
                status = "Cancelado"
            elif roll < 0.08:
                status = "Devolução/Reembolso"
            elif roll < 0.10:
                status = "Não pago"
            else:
                status = "Concluído"
            n_items = 1 if rng.random() < 0.75 else rng.randint(2, 3)
            picks: dict[str, tuple[CatalogItem, int]] = {}
            while len(picks) < n_items:
                item = rng.choices(CATALOG, weights=weights)[0]
                if item.sku in stalled and offset < 45:
                    continue
                picks[item.sku] = (item, 1 if rng.random() < 0.8 else rng.randint(2, 4))
            gross = sum((it.price * q for it, q in picks.values()), Decimal("0"))
            commission = gross * COMMISSION_RATE
            service = min(gross * SERVICE_RATE, SERVICE_CAP)
            shipping = Decimal(rng.choice(["0", "0", "0", "4.90", "7.90"]))
            voucher = Decimal(rng.choice(["0"] * 6 + ["5", "10"]))
            buyer = f"user_{rng.randint(10000, 99999)}"
            for item, qty in picks.values():
                writer.writerow(
                    [
                        order_sn,
                        status,
                        when.strftime("%Y-%m-%d %H:%M"),
                        item.sku,
                        item.name,
                        _money(item.price),
                        qty,
                        _money(commission),
                        _money(service),
                        _money(shipping),
                        _money(voucher),
                        buyer,
                        f"Fake Recipient {rng.randint(1, 999)}",
                        f"(11) 9{rng.randint(1000, 9999)}-0000",
                        f"Rua Exemplo {rng.randint(1, 999)}, São Paulo - SP",
                    ]
                )
    return out.getvalue().encode()


def _get_or_create_user(db: Session, email: str, shop: str, code: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        seller = Seller(name=shop, shop_code=code)
        user = User(seller=seller, email=email, password_hash=hash_password(DEMO_PASSWORD))
        db.add(user)
        db.commit()
    return user


def seed(db: Session, days: int = 120, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    user = _get_or_create_user(db, DEMO_EMAIL, "Demo Gadgets Store", "demo-gadgets")
    content = generate_csv(days, today)
    rows = parse_file(content, "seed-orders.csv", max_rows=1_000_000)
    summary = import_orders(
        db,
        seller_id=user.seller_id,
        user_id=user.id,
        filename="seed-orders.csv",
        content=content,
        rows=rows,
    )
    # Cost and stock come from the catalogue (sellers inform these in the UI).
    for item in CATALOG:
        db.execute(
            update(Product)
            .where(Product.seller_id == user.seller_id, Product.sku == item.sku)
            .values(unit_cost=item.cost, stock_quantity=item.stock, low_stock_threshold=10)
        )
    db.commit()

    # A second, tiny shop proves tenant isolation in the demo.
    other = _get_or_create_user(db, SECOND_EMAIL, "Another Shop", "another-shop")
    other_csv = generate_csv(10, today, seed=7)
    other_rows = parse_file(other_csv, "other.csv", max_rows=100_000)
    import_orders(
        db,
        seller_id=other.seller_id,
        user_id=other.id,
        filename="other.csv",
        content=other_csv,
        rows=other_rows,
    )
    return {"orders_created": summary.upload.orders_created, "rows": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Load fake demo data")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--csv-out", type=Path, help="Only write the sample CSV to this path")
    args = parser.parse_args()
    if args.csv_out:
        args.csv_out.write_bytes(generate_csv(args.days, date.today()))
        print(f"Sample export written to {args.csv_out}")
        return
    with get_sessionmaker()() as db:
        result = seed(db, days=args.days)
    print(f"Seed complete: {result['orders_created']} new orders ({result['rows']} rows).")
    print(f"Demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}")


if __name__ == "__main__":  # pragma: no cover
    main()
