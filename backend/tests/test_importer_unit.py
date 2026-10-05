"""Unit tests for parsing, validation and fee allocation (no database)."""

import io
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from app.services.importer import (
    ImportValidationError,
    OrderRow,
    allocate,
    parse_decimal,
    parse_file,
)

HEADER = (
    "ID do pedido,Status do pedido,Data de criação do pedido,Número de referência SKU,"
    "Nome do Produto,Preço acordado,Quantidade,Taxa de comissão,Taxa de serviço,"
    "Taxa de envio paga pelo vendedor,Cupom do vendedor,Nome de usuário (comprador),"
    "Nome do destinatário,Telefone,Endereço de entrega\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode()


ROW = (
    '2509010001AB,Concluído,2025-09-01 10:30,SKU-1,Capa de celular,"29,90",2,"8,37","1,20",'
    '"0,00","2,00",buyer_one,Maria Silva,11999990000,"Rua A, 1"'
)


def test_parse_decimal_formats() -> None:
    assert parse_decimal("1.234,56") == Decimal("1234.56")
    assert parse_decimal("R$ 12,90") == Decimal("12.90")
    assert parse_decimal("12.5") == Decimal("12.50")
    assert parse_decimal("") == Decimal("0")
    assert parse_decimal(3) == Decimal("3.00")
    with pytest.raises(ValueError):
        parse_decimal("abc")


def test_allocate_sums_exactly() -> None:
    parts = allocate(Decimal("10.00"), [Decimal("1"), Decimal("1"), Decimal("1")])
    assert sum(parts) == Decimal("10.00")
    assert parts == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]


def test_allocate_proportional_and_zero_weights() -> None:
    assert allocate(Decimal("9.00"), [Decimal("100"), Decimal("200")]) == [
        Decimal("3.00"),
        Decimal("6.00"),
    ]
    assert allocate(Decimal("1.00"), [Decimal("0"), Decimal("0")]) == [
        Decimal("0.50"),
        Decimal("0.50"),
    ]
    assert allocate(Decimal("0"), [Decimal("5")]) == [Decimal("0.00")]


def test_parse_portuguese_export_drops_pii() -> None:
    rows = parse_file(_csv(ROW), "orders.csv", max_rows=100)
    assert len(rows) == 1
    row = rows[0]
    assert row.order_sn == "2509010001AB"
    assert row.status == "completed"
    assert row.unit_price == Decimal("29.90")
    assert row.quantity == 2
    assert row.commission_fee == Decimal("8.37")
    assert row.seller_voucher == Decimal("2.00")
    # Brasília time (UTC-3) converted to UTC.
    assert row.ordered_at == datetime(2025, 9, 1, 13, 30, tzinfo=UTC)
    dumped = row.model_dump()
    assert "Maria" not in str(dumped)
    assert "11999990000" not in str(dumped)
    assert row.buyer_username == "buyer_one"


def test_parse_english_xlsx() -> None:
    df = pd.DataFrame(
        {
            "Order ID": ["A1"],
            "Order Status": ["Cancelled"],
            "Order Creation Date": ["2025-09-02 08:00"],
            "SKU Reference No.": ["SKU-9"],
            "Product Name": ["Mouse"],
            "Deal Price": ["50.00"],
            "Quantity": ["1"],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    rows = parse_file(buf.getvalue(), "orders.xlsx", max_rows=10)
    assert rows[0].status == "cancelled"
    assert rows[0].commission_fee == Decimal("0")
    assert rows[0].buyer_username is None


def test_missing_required_column() -> None:
    with pytest.raises(ImportValidationError, match="Missing required columns"):
        parse_file(b"Order ID,Quantity\nA,1\n", "x.csv", max_rows=10)


def test_row_errors_are_reported_with_line_numbers() -> None:
    bad = ROW.replace(",2,", ",-1,").replace('"29,90"', '"-5,00"')
    with pytest.raises(ImportValidationError) as exc:
        parse_file(_csv(ROW, bad), "x.csv", max_rows=10)
    errors = exc.value.errors
    assert {e["row"] for e in errors} == {3}
    assert {e["field"] for e in errors} >= {"quantity", "unit_price"}


@pytest.mark.parametrize("payload", ["=HYPERLINK(1)", "+cmd|' /C calc'!A0", "@SUM(1)", "-2+3"])
def test_formula_injection_rejected(payload: str) -> None:
    bad = ROW.replace("Capa de celular", payload.replace(",", " "))
    with pytest.raises(ImportValidationError) as exc:
        parse_file(_csv(bad), "x.csv", max_rows=10)
    assert exc.value.errors[0]["field"] == "product_name"


def test_too_many_rows() -> None:
    with pytest.raises(ImportValidationError, match="row limit"):
        parse_file(_csv(ROW, ROW.replace("0001AB", "0002AB")), "x.csv", max_rows=1)


def test_unsupported_extension_and_bad_content() -> None:
    with pytest.raises(ImportValidationError, match=r"Only \.csv and \.xlsx"):
        parse_file(b"x", "orders.exe", max_rows=10)
    with pytest.raises(ImportValidationError, match="not a valid XLSX"):
        parse_file(b"not a zip file", "orders.xlsx", max_rows=10)
    with pytest.raises(ImportValidationError, match="UTF-8"):
        parse_file(b"\xff\xfe\x00bad", "orders.csv", max_rows=10)
    with pytest.raises(ImportValidationError, match="empty"):
        parse_file(b"", "orders.csv", max_rows=10)


def test_zip_bomb_xlsx_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.importer as importer

    df = pd.DataFrame({"Order ID": ["A"] * 50})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    monkeypatch.setattr(importer, "MAX_XLSX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(ImportValidationError, match="too large when decompressed"):
        parse_file(buf.getvalue(), "orders.xlsx", max_rows=100)


def test_conflicting_duplicate_line_rejected() -> None:
    dup = ROW.replace('"29,90"', '"30,00"')
    with pytest.raises(ImportValidationError, match="different prices"):
        parse_file(_csv(ROW, dup), "x.csv", max_rows=10)


def test_order_row_rejects_bad_identifiers() -> None:
    base = {
        "order_sn": "ABC",
        "status": "completed",
        "ordered_at": datetime(2025, 1, 1, tzinfo=UTC),
        "sku": "SKU 1; DROP",
        "product_name": "x",
        "unit_price": "1",
        "quantity": 1,
    }
    with pytest.raises(ValueError):
        OrderRow.model_validate(base)


def test_brazilian_day_first_dates() -> None:
    row = ROW.replace("2025-09-01 10:30", "02/09/2025 10:30")
    assert parse_file(_csv(row), "x.csv", max_rows=10)[0].ordered_at.month == 9
    bad = ROW.replace("2025-09-01 10:30", "31/31/2025")
    with pytest.raises(ImportValidationError):
        parse_file(_csv(bad), "x.csv", max_rows=10)


def test_semicolon_delimited_export() -> None:
    semi = _csv(ROW).decode().replace('"29,90"', "29,90").replace('"8,37"', "8,37")
    semi = semi.replace('"1,20"', "1,20").replace('"0,00"', "0,00").replace('"2,00"', "2,00")
    semi = semi.replace('"Rua A, 1"', "Rua A 1").replace(",", ";").replace("29;90", "29,90")
    for a, b in (("8;37", "8,37"), ("1;20", "1,20"), ("0;00", "0,00"), ("2;00", "2,00")):
        semi = semi.replace(a, b)
    rows = parse_file(semi.encode(), "x.csv", max_rows=10)
    assert rows[0].unit_price == Decimal("29.90")


@pytest.mark.parametrize(
    "status", ["completed", "shipped", "to_ship", "unpaid", "cancelled", "returned"]
)
def test_canonical_statuses_are_accepted(status: str) -> None:
    row = OrderRow.model_validate(
        {
            "order_sn": "A1",
            "status": status,
            "ordered_at": "2025-09-01 10:00",
            "sku": "SKU-1",
            "product_name": "x",
            "unit_price": "1",
            "quantity": 1,
        }
    )
    assert row.status == status
