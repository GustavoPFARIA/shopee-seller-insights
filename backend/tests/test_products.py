from fastapi.testclient import TestClient

from tests.factories import Line, build_csv


def _seed(client: TestClient, headers: dict[str, str]) -> int:
    client.post(
        "/api/uploads",
        headers=headers,
        files={"file": ("o.csv", build_csv([Line("O1", "SKU-A", "10.00")]))},
    )
    products = client.get("/api/products", headers=headers).json()
    assert len(products) == 1
    assert products[0]["unit_cost"] is None
    assert products[0]["stock_quantity"] is None
    return int(products[0]["id"])


def test_update_cost_and_stock(client: TestClient, auth_headers: dict[str, str]) -> None:
    pid = _seed(client, auth_headers)
    resp = client.patch(
        f"/api/products/{pid}",
        headers=auth_headers,
        json={"unit_cost": "4.50", "stock_quantity": 12, "low_stock_threshold": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unit_cost"] == "4.50"
    assert body["stock_quantity"] == 12
    # Partial update keeps other fields.
    resp = client.patch(f"/api/products/{pid}", headers=auth_headers, json={"stock_quantity": 1})
    assert resp.json()["unit_cost"] == "4.50"


def test_negative_values_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    pid = _seed(client, auth_headers)
    for payload in ({"unit_cost": "-1"}, {"stock_quantity": -5}, {"unit_cost": "1.234"}):
        resp = client.patch(f"/api/products/{pid}", headers=auth_headers, json=payload)
        assert resp.status_code == 422


def test_products_are_scoped(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    _seed(client, auth_headers)
    assert client.get("/api/products", headers=other_headers).json() == []
    assert client.patch("/api/products/999", headers=auth_headers, json={}).status_code == 404
