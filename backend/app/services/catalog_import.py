"""Bulk update of product cost and stock from a spreadsheet the seller fills in."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.services.importer import (
    MAX_REPORTED_ERRORS,
    Identifier,
    ImportValidationError,
    SafeText,
    parse_decimal,
    read_spreadsheet,
)

MAX_MONEY = Decimal("9999999")
MAX_QTY = 10_000_000

# Canonical column -> accepted headers (compared case-insensitively).
CATALOG_COLUMNS: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "número de referência sku", "sku reference no."),
    "name": ("name", "product name", "nome do produto", "nome"),
    "unit_cost": ("unit_cost", "unit cost", "cost", "custo", "custo unitário"),
    "stock_quantity": ("stock_quantity", "stock", "estoque"),
    "low_stock_threshold": ("low_stock_threshold", "low stock threshold", "estoque mínimo"),
}
TEMPLATE_HEADER = ["sku", "name", "unit_cost", "stock_quantity", "low_stock_threshold"]


def _blank(value: object) -> object:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _cost(value: object) -> Decimal | None:
    raw = _blank(value)
    if raw is None:
        return None
    amount = parse_decimal(raw)
    if amount < 0 or amount > MAX_MONEY:
        raise ValueError("cost must be between 0 and 9,999,999")
    return amount


def _quantity(value: object) -> int | None:
    raw = _blank(value)
    if raw is None:
        return None
    text = str(raw)
    if not text.isdigit():
        raise ValueError("must be a whole number >= 0")
    number = int(text)
    if number > MAX_QTY:
        raise ValueError("is too large")
    return number


class CatalogRow(BaseModel):
    """Empty cells mean "keep the current value"."""

    sku: Identifier
    name: Annotated[SafeText | None, BeforeValidator(_blank)] = None
    unit_cost: Annotated[Decimal | None, BeforeValidator(_cost)] = None
    stock_quantity: Annotated[int | None, BeforeValidator(_quantity)] = None
    low_stock_threshold: Annotated[int | None, BeforeValidator(_quantity)] = None


@dataclass
class CatalogImportResult:
    rows: int
    updated: int = 0
    created: int = 0
    unchanged: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def parse_catalog(content: bytes, filename: str, max_rows: int) -> list[CatalogRow]:
    df = read_spreadsheet(content, filename, max_rows)
    if len(df) > max_rows:
        raise ImportValidationError(f"The file exceeds the row limit of {max_rows}")
    headers = {str(c).strip().lower(): c for c in df.columns}
    rename: dict[Any, str] = {}
    for canonical, aliases in CATALOG_COLUMNS.items():
        for alias in aliases:
            if alias in headers:
                rename[headers[alias]] = canonical
                break
    if "sku" not in rename.values():
        raise ImportValidationError("Missing required column: sku")
    if len(rename) == 1:
        raise ImportValidationError(
            "Nothing to update: add at least one of unit_cost, stock_quantity, "
            "low_stock_threshold or name"
        )
    df = df[list(rename)].rename(columns=rename)

    rows: list[CatalogRow] = []
    errors: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for position, record in enumerate(df.to_dict(orient="records")):
        line = position + 2
        try:
            row = CatalogRow.model_validate(record)
        except ValidationError as exc:
            for err in exc.errors():
                loc = str(err["loc"][0]) if err["loc"] else "row"
                errors.append({"row": line, "field": loc, "message": err["msg"]})
        else:
            if row.sku in seen:
                errors.append(
                    {"row": line, "field": "sku", "message": f"duplicate of row {seen[row.sku]}"}
                )
            seen[row.sku] = line
            rows.append(row)
        if len(errors) >= MAX_REPORTED_ERRORS:
            break
    if errors:
        raise ImportValidationError("Some rows are invalid", errors[:MAX_REPORTED_ERRORS])
    if not rows:
        raise ImportValidationError("The file is empty")
    return rows


def apply_catalog(db: Session, seller_id: int, rows: list[CatalogRow]) -> CatalogImportResult:
    """Apply all rows in one transaction, or none if any SKU is unknown and unnamed."""
    existing = {
        p.sku: p
        for p in db.scalars(
            select(Product).where(
                Product.seller_id == seller_id, Product.sku.in_([r.sku for r in rows])
            )
        )
    }
    unknown = [
        {"row": None, "field": "sku", "message": f"unknown SKU {r.sku} (add a name to create it)"}
        for r in rows
        if r.sku not in existing and r.name is None
    ]
    if unknown:
        raise ImportValidationError("Unknown SKUs", unknown[:MAX_REPORTED_ERRORS])

    result = CatalogImportResult(rows=len(rows))
    for row in rows:
        changes = row.model_dump(exclude={"sku"}, exclude_none=True)
        product = existing.get(row.sku)
        if product is None:
            db.add(
                Product(
                    seller_id=seller_id,
                    sku=row.sku,
                    name=changes.pop("name"),
                    unit_cost=changes.get("unit_cost"),
                    stock_quantity=changes.get("stock_quantity"),
                    low_stock_threshold=changes.get("low_stock_threshold", 5),
                )
            )
            result.created += 1
            continue
        dirty = False
        for attr, value in changes.items():
            if getattr(product, attr) != value:
                setattr(product, attr, value)
                dirty = True
        if dirty:
            result.updated += 1
        else:
            result.unchanged += 1
    db.commit()
    return result
