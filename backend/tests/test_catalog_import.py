import io
from decimal import Decimal

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product
from app.services.catalog_import import parse_catalog
from app.services.importer import ImportValidationError
from tests.factories import Line, build_csv


def _seed_products(client: TestClient, headers: dict[str, str]) -> None:
    lines = [Line("O1", "SKU-A", "10.00"), Line("O2", "SKU-B", "20.00")]
    resp = client.post("/api/uploads", headers=headers, files={"file": ("o.csv", build_csv(lines))})
    assert resp.status_code == 201


def _import(
    client: TestClient, headers: dict[str, str], content: bytes, name: str = "products.csv"
) -> tuple[int, dict]:  # type: ignore[type-arg]
    resp = client.post("/api/products/import", headers=headers, files={"file": (name, content)})
    return resp.status_code, resp.json()


def test_template_roundtrip(client: TestClient, auth_headers: dict[str, str], db: Session) -> None:
    _seed_products(client, auth_headers)
    template = client.get("/api/products/template.csv", headers=auth_headers)
    assert template.status_code == 200
    assert template.text.splitlines()[0] == "sku,name,unit_cost,stock_quantity,low_stock_threshold"
    filled = template.text.replace("SKU-A,Product SKU-A,,,5", "SKU-A,Product SKU-A,4.50,30,8")
    code, body = _import(client, auth_headers, filled.encode())
    assert code == 200, body
    assert body == {"rows": 2, "updated": 1, "created": 0, "unchanged": 1}
    a = db.scalar(select(Product).where(Product.sku == "SKU-A"))
    assert a is not None
    assert (a.unit_cost, a.stock_quantity, a.low_stock_threshold) == (Decimal("4.50"), 30, 8)


def test_blank_cells_keep_values_and_portuguese_headers(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _seed_products(client, auth_headers)
    _import(client, auth_headers, b"sku,unit_cost,stock_quantity\nSKU-A,10,5\n")
    code, body = _import(client, auth_headers, b'SKU,Custo,Estoque\nSKU-A,"12,90",\n')
    assert code == 200, body
    a = db.scalar(select(Product).where(Product.sku == "SKU-A"))
    assert a is not None
    assert a.unit_cost == Decimal("12.90")
    assert a.stock_quantity == 5  # blank cell: unchanged


def test_xlsx_and_create_with_name(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    df = pd.DataFrame({"SKU": ["NEW-1"], "Name": ["Brand new"], "Stock": ["7"]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    code, body = _import(client, auth_headers, buf.getvalue(), "catalog.xlsx")
    assert code == 200, body
    assert body["created"] == 1
    assert db.scalar(select(Product.stock_quantity).where(Product.sku == "NEW-1")) == 7


def test_unknown_sku_without_name_rejects_whole_file(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _seed_products(client, auth_headers)
    code, body = _import(client, auth_headers, b"sku,unit_cost\nSKU-A,1\nGHOST,2\n")
    assert code == 422
    assert "GHOST" in body["errors"][0]["message"]
    assert db.scalar(select(Product.unit_cost).where(Product.sku == "SKU-A")) is None


def test_cannot_touch_other_sellers_products(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    db: Session,
) -> None:
    _seed_products(client, other_headers)
    code, _ = _import(client, auth_headers, b"sku,unit_cost\nSKU-A,99\n")
    assert code == 422  # unknown for this seller
    assert db.scalar(select(Product.unit_cost).where(Product.sku == "SKU-A")) is None
    assert client.get("/api/products/template.csv", headers=auth_headers).text.count("\n") == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"sku,unit_cost\nSKU-A,-1\n", "unit_cost"),
        (b"sku,stock_quantity\nSKU-A,1.5\n", "stock_quantity"),
        (b"sku,stock_quantity\nSKU-A,99999999999\n", "stock_quantity"),
        (b"sku,unit_cost\nSKU-A,1\nSKU-A,2\n", "sku"),
        (b"sku,name\nNEW,=HYPERLINK(1)\n", "name"),
    ],
)
def test_invalid_rows(content: bytes, message: str) -> None:
    with pytest.raises(ImportValidationError) as exc:
        parse_catalog(content, "p.csv", max_rows=10)
    assert exc.value.errors[0]["field"] == message


def test_structural_errors() -> None:
    with pytest.raises(ImportValidationError, match="Missing required column"):
        parse_catalog(b"unit_cost\n1\n", "p.csv", 10)
    with pytest.raises(ImportValidationError, match="Nothing to update"):
        parse_catalog(b"sku\nA\n", "p.csv", 10)
    with pytest.raises(ImportValidationError, match="row limit"):
        parse_catalog(b"sku,unit_cost\nA,1\nB,2\n", "p.csv", 1)
    with pytest.raises(ImportValidationError, match="empty"):
        parse_catalog(b"sku,unit_cost\n", "p.csv", 10)


def test_template_neutralizes_formulas(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _seed_products(client, auth_headers)
    db.execute(update(Product).values(name="@SUM(A1)"))
    db.commit()
    text = client.get("/api/products/template.csv", headers=auth_headers).text
    assert ",'@SUM(A1)," in text


def test_import_requires_auth_and_size_limit(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.post("/api/products/import", files={"file": ("p.csv", b"x")}).status_code == 401
    assert client.get("/api/products/template.csv").status_code == 401
    big = b"a" * (get_settings().max_upload_mb * 1024 * 1024 + 1)
    assert _import(client, auth_headers, big)[0] == 413


def test_long_sku_roundtrip(client: TestClient, auth_headers: dict[str, str], db: Session) -> None:
    long_sku = "SKU-" + "Z" * 90
    resp = client.post(
        "/api/uploads",
        headers=auth_headers,
        files={"file": ("o.csv", build_csv([Line("O1", long_sku, "10.00")]))},
    )
    assert resp.status_code == 201, resp.text
    template = client.get("/api/products/template.csv", headers=auth_headers).text
    code, body = _import(client, auth_headers, template.replace(",,,5", ",1.00,3,5").encode())
    assert code == 200, body
    assert db.scalar(select(Product.stock_quantity).where(Product.sku == long_sku)) == 3
