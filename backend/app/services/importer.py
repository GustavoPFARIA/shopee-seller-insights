"""Parse, validate and persist Shopee Seller Centre order exports.

Pipeline: raw bytes -> safety checks -> pandas DataFrame (strings only) -> column
mapping (PII columns dropped) -> one Pydantic model per row -> idempotent upsert.
"""

import csv
import hashlib
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Annotated, Any, Literal

import pandas as pd
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.csv_safety import is_formula
from app.models import Order, OrderItem, Product, Upload
from app.security import pseudonymize

CENT = Decimal("0.01")
# Shopee Brazil exports local time (Brasília, UTC-3, no DST since 2019).
SHOPEE_BR_TZ = timezone(timedelta(hours=-3))
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_REPORTED_ERRORS = 20

# Canonical field -> accepted headers (Portuguese Seller Centre BR and English export).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "order_sn": ("ID do pedido", "Order ID"),
    "status": ("Status do pedido", "Order Status"),
    "ordered_at": ("Data de criação do pedido", "Order Creation Date"),
    "sku": ("Número de referência SKU", "SKU Reference No."),
    "product_name": ("Nome do Produto", "Product Name"),
    "unit_price": ("Preço acordado", "Deal Price"),
    "quantity": ("Quantidade", "Quantity"),
    "commission_fee": ("Taxa de comissão", "Commission Fee"),
    "service_fee": ("Taxa de serviço", "Service Fee"),
    "seller_shipping_fee": ("Taxa de envio paga pelo vendedor", "Seller Paid Shipping Fee"),
    "seller_voucher": ("Cupom do vendedor", "Seller Voucher"),
    "buyer_username": ("Nome de usuário (comprador)", "Username (Buyer)"),
}
REQUIRED_FIELDS = (
    "order_sn",
    "status",
    "ordered_at",
    "sku",
    "product_name",
    "unit_price",
    "quantity",
)

STATUS_ALIASES: dict[str, str] = {
    "concluído": "completed",
    "concluido": "completed",
    "completed": "completed",
    "enviado": "shipped",
    "shipped": "shipped",
    "a enviar": "to_ship",
    "to ship": "to_ship",
    "não pago": "unpaid",
    "nao pago": "unpaid",
    "unpaid": "unpaid",
    "cancelado": "cancelled",
    "cancelled": "cancelled",
    "devolução/reembolso": "returned",
    "return/refund": "returned",
}
OrderStatus = Literal["completed", "shipped", "to_ship", "unpaid", "cancelled", "returned"]
# Orders in these statuses do not generate revenue.
NON_REVENUE_STATUSES = ("cancelled", "unpaid", "returned")


class ImportValidationError(Exception):
    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []


def parse_decimal(value: object) -> Decimal:
    """Parse '1.234,56', 'R$ 12,90', '12.5' or numbers into a 2-decimal Decimal."""
    if isinstance(value, int | float | Decimal):
        text = str(value)
    else:
        text = str(value).replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
        if text == "":
            return Decimal("0.00")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid number: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid number: {value!r}")
    return result.quantize(CENT)


def _parse_status(value: object) -> str:
    status = STATUS_ALIASES.get(str(value).strip().lower())
    if status is None:
        raise ValueError(f"unknown order status: {value!r}")
    return status


BR_DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


def _parse_datetime(value: object) -> datetime:
    """Parse ISO ('2025-09-01 10:30') or Brazilian ('01/09/2025 10:30') dates to UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in BR_DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"invalid date: {value!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHOPEE_BR_TZ)
    return dt.astimezone(UTC)


def _no_formula(value: str) -> str:
    if is_formula(value):
        raise ValueError("value must not start with a formula character (= + - @)")
    return value


def _empty_to_none(value: object) -> object:
    return None if value is None or str(value).strip() == "" else str(value).strip()


Identifier = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
]
SafeText = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(_no_formula)]
Money = Annotated[Decimal, BeforeValidator(parse_decimal), Field(ge=0, le=Decimal("9999999"))]


class OrderRow(BaseModel):
    """One validated line of an order export (no personal data except buyer handle)."""

    order_sn: Identifier
    status: Annotated[OrderStatus, BeforeValidator(_parse_status)]
    ordered_at: Annotated[datetime, BeforeValidator(_parse_datetime)]
    sku: Annotated[Identifier, Field(max_length=100)]
    product_name: SafeText
    unit_price: Money
    quantity: int = Field(gt=0, le=100_000)
    commission_fee: Money = Decimal("0.00")
    service_fee: Money = Decimal("0.00")
    seller_shipping_fee: Money = Decimal("0.00")
    seller_voucher: Money = Decimal("0.00")
    buyer_username: Annotated[str | None, BeforeValidator(_empty_to_none)] = Field(
        default=None, max_length=255
    )


def _detect_delimiter(text: str) -> str:
    """Comma by default; also accept the ';' and tab exports common in Brazil."""
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def read_spreadsheet(content: bytes, filename: str, max_rows: int) -> pd.DataFrame:
    if not content:
        raise ImportValidationError("The file is empty")
    lower = filename.lower()
    if lower.endswith(".csv"):
        if b"\x00" in content:
            raise ImportValidationError("The CSV file must be UTF-8 text")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportValidationError("The CSV file must be UTF-8 text") from exc
        try:
            return pd.read_csv(
                io.StringIO(text),
                dtype=str,
                keep_default_na=False,
                sep=_detect_delimiter(text),
                nrows=max_rows + 1,
            )
        except (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error) as exc:
            raise ImportValidationError("Could not parse the CSV file") from exc
    if lower.endswith(".xlsx"):
        if not content.startswith(b"PK\x03\x04"):
            raise ImportValidationError("The file is not a valid XLSX workbook")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                total = sum(info.file_size for info in archive.infolist())
            if total > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ImportValidationError("The XLSX file is too large when decompressed")
            return pd.read_excel(
                io.BytesIO(content),
                dtype=str,
                keep_default_na=False,
                nrows=max_rows + 1,
                engine="openpyxl",
            )
        except ImportValidationError:
            raise
        except Exception as exc:  # openpyxl raises many unrelated exception types
            raise ImportValidationError("The file is not a valid XLSX workbook") from exc
    raise ImportValidationError("Only .csv and .xlsx files are supported")


def parse_file(content: bytes, filename: str, max_rows: int) -> list[OrderRow]:
    """Validate an export file and return clean rows, or raise ImportValidationError."""
    df = read_spreadsheet(content, filename, max_rows)
    if len(df) > max_rows:
        raise ImportValidationError(f"The file exceeds the row limit of {max_rows}")

    headers = {str(c).strip(): c for c in df.columns}
    rename: dict[Any, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in headers:
                rename[headers[alias]] = field
                break
    missing = [f for f in REQUIRED_FIELDS if f not in rename.values()]
    if missing:
        raise ImportValidationError(f"Missing required columns: {', '.join(missing)}")
    # Keep only mapped columns: recipient name, phone and address never leave this function.
    df = df[list(rename)].rename(columns=rename)

    rows: list[OrderRow] = []
    errors: list[dict[str, Any]] = []
    for position, record in enumerate(df.to_dict(orient="records")):
        line = position + 2  # header is line 1
        data = {k: v for k, v in record.items() if not (k != "buyer_username" and v == "")}
        try:
            rows.append(OrderRow.model_validate(data))
        except ValidationError as exc:
            for err in exc.errors():
                field = str(err["loc"][0]) if err["loc"] else "row"
                errors.append({"row": line, "field": field, "message": err["msg"]})
        if len(errors) >= MAX_REPORTED_ERRORS:
            break
    if errors:
        raise ImportValidationError("Some rows are invalid", errors[:MAX_REPORTED_ERRORS])
    if not rows:
        raise ImportValidationError("The file is empty")
    _check_duplicate_lines(rows)
    return rows


def _check_duplicate_lines(rows: list[OrderRow]) -> None:
    prices: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        key = (row.order_sn, row.sku)
        if key in prices and prices[key] != row.unit_price:
            raise ImportValidationError(
                f"Order {row.order_sn} lists SKU {row.sku} twice with different prices"
            )
        prices[key] = row.unit_price


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` proportionally to `weights`; the remainder goes to the last share."""
    if not weights:
        return []
    weight_sum = sum(weights, Decimal("0"))
    if weight_sum == 0:
        weights = [Decimal("1")] * len(weights)
        weight_sum = Decimal(len(weights))
    shares = [(total * w / weight_sum).quantize(CENT, rounding=ROUND_DOWN) for w in weights[:-1]]
    shares.append((total - sum(shares, Decimal("0"))).quantize(CENT))
    return shares


@dataclass
class ImportSummary:
    upload: Upload
    products_created: int


def import_orders(
    db: Session,
    *,
    seller_id: int,
    user_id: int,
    filename: str,
    content: bytes,
    rows: list[OrderRow],
) -> ImportSummary:
    """Persist validated rows idempotently in a single transaction."""
    by_order: dict[str, list[OrderRow]] = defaultdict(list)
    for row in rows:
        by_order[row.order_sn].append(row)

    products_created = _ensure_products(db, seller_id, rows)
    product_ids: dict[str, int] = {
        sku: pid
        for sku, pid in db.execute(
            select(Product.sku, Product.id).where(
                Product.seller_id == seller_id, Product.sku.in_({r.sku for r in rows})
            )
        )
    }

    upload = Upload(
        seller_id=seller_id,
        user_id=user_id,
        filename=filename[:255],
        file_sha256=hashlib.sha256(content).hexdigest(),
        row_count=len(rows),
        orders_created=0,
        orders_updated=0,
        orders_unchanged=0,
    )
    db.add(upload)
    db.flush()

    for order_sn, lines in by_order.items():
        head = lines[0]
        inserted_id = db.scalar(
            insert(Order)
            .values(
                seller_id=seller_id,
                upload_id=upload.id,
                order_sn=order_sn,
                status=head.status,
                ordered_at=head.ordered_at,
                buyer_hash=pseudonymize(head.buyer_username) if head.buyer_username else None,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_orders_seller_order_sn")
            .returning(Order.id)
        )
        if inserted_id is None:
            existing = db.scalars(
                select(Order).where(Order.seller_id == seller_id, Order.order_sn == order_sn)
            ).one()
            if existing.status != head.status:
                existing.status = head.status
                upload.orders_updated += 1
            else:
                upload.orders_unchanged += 1
            continue
        upload.orders_created += 1
        db.add_all(_build_items(inserted_id, lines, product_ids))

    db.commit()
    return ImportSummary(upload=upload, products_created=products_created)


def _ensure_products(db: Session, seller_id: int, rows: list[OrderRow]) -> int:
    names: dict[str, str] = {}
    for row in rows:
        names.setdefault(row.sku, row.product_name)
    result = db.execute(
        insert(Product)
        .values(
            [
                {
                    "seller_id": seller_id,
                    "sku": sku,
                    "name": name,
                    "unit_cost": None,
                    "stock_quantity": None,
                    "low_stock_threshold": 5,
                    "created_at": datetime.now(UTC),
                }
                for sku, name in names.items()
            ]
        )
        .on_conflict_do_nothing(constraint="uq_products_seller_sku")
        .returning(Product.id)
    )
    return len(result.all())


def _build_items(
    order_id: int, lines: list[OrderRow], product_ids: dict[str, int]
) -> list[OrderItem]:
    # Merge repeated SKU lines (same price, validated earlier).
    merged: dict[str, OrderRow] = {}
    for line in lines:
        if line.sku in merged:
            prev = merged[line.sku]
            merged[line.sku] = prev.model_copy(update={"quantity": prev.quantity + line.quantity})
        else:
            merged[line.sku] = line
    items = list(merged.values())
    head = lines[0]
    gross = [i.unit_price * i.quantity for i in items]
    # Fees are order-level in the export (repeated on every line): allocate by item value.
    fee_shares = {
        fee: allocate(getattr(head, fee), gross)
        for fee in ("commission_fee", "service_fee", "seller_shipping_fee", "seller_voucher")
    }
    return [
        OrderItem(
            order_id=order_id,
            product_id=product_ids[item.sku],
            quantity=item.quantity,
            unit_price=item.unit_price,
            commission_fee=fee_shares["commission_fee"][idx],
            service_fee=fee_shares["service_fee"][idx],
            seller_shipping_fee=fee_shares["seller_shipping_fee"][idx],
            seller_voucher=fee_shares["seller_voucher"][idx],
        )
        for idx, item in enumerate(items)
    ]
