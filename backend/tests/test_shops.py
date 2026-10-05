"""Several shops per account: switching, isolation, creation and removal."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Membership, RefreshToken, User
from tests.factories import Line, build_csv

PERIOD = {"start": "2025-09-01", "end": "2025-09-30"}


def _create_shop(client: TestClient, headers: dict[str, str], name: str) -> int:
    resp = client.post("/api/shops", headers=headers, json={"name": name})
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "owner"
    return int(resp.json()["id"])


def _in(headers: dict[str, str], shop_id: int) -> dict[str, str]:
    return {**headers, "X-Shop-Id": str(shop_id)}


def test_create_and_switch_shop(client: TestClient, auth_headers: dict[str, str]) -> None:
    first = client.get("/api/auth/me", headers=auth_headers).json()
    second_id = _create_shop(client, auth_headers, "Second Store")
    me = client.get("/api/auth/me", headers=_in(auth_headers, second_id)).json()
    assert (me["seller_id"], me["shop_name"], me["role"]) == (second_id, "Second Store", "owner")
    assert {s["name"] for s in me["shops"]} == {"Shop A", "Second Store"}
    # Without the header the default (first) shop is used.
    assert (
        client.get("/api/auth/me", headers=auth_headers).json()["seller_id"] == first["seller_id"]
    )


def test_data_is_separated_between_my_shops(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    second_id = _create_shop(client, auth_headers, "Second Store")
    client.post(
        "/api/uploads",
        headers=_in(auth_headers, second_id),
        files={"file": ("o.csv", build_csv([Line("S2", "SKU-SECOND", "10.00")]))},
    )
    in_second = client.get("/api/products", headers=_in(auth_headers, second_id)).json()
    in_first = client.get("/api/products", headers=auth_headers).json()
    assert [p["sku"] for p in in_second] == ["SKU-SECOND"]
    assert in_first == []


def test_shop_header_for_a_shop_i_am_not_in_is_forbidden(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    other_shop = client.get("/api/auth/me", headers=other_headers).json()["seller_id"]
    for path in ("/api/auth/me", "/api/products", "/api/metrics/overview", "/api/settings"):
        assert client.get(path, headers=_in(auth_headers, other_shop)).status_code == 403
    assert client.get("/api/products", headers=_in(auth_headers, 999_999)).status_code == 403
    bad = client.get("/api/products", headers={**auth_headers, "X-Shop-Id": "abc"})
    assert bad.status_code == 422


def test_role_is_per_shop(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    """Owner in one shop, viewer in another: writes are allowed only where owner."""
    invite = client.post(
        "/api/members/invitations",
        headers=other_headers,
        json={"email": "seller-a@example.com", "role": "viewer"},
    ).json()
    joined = client.post(
        "/api/auth/accept-invite",
        json={"token": invite["token"], "password": "correct-horse-battery"},
    )
    assert joined.status_code == 201
    shop_b = joined.json()["shop_id"]
    assert (
        client.patch(
            "/api/settings", headers=_in(auth_headers, shop_b), json={"stalled_days": 5}
        ).status_code
        == 403
    )
    assert (
        client.patch("/api/settings", headers=auth_headers, json={"stalled_days": 5}).status_code
        == 200
    )


def test_removal_from_one_shop_keeps_the_others(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str], db: Session
) -> None:
    invite = client.post(
        "/api/members/invitations",
        headers=other_headers,
        json={"email": "seller-a@example.com", "role": "manager"},
    ).json()
    shop_b = client.post(
        "/api/auth/accept-invite",
        json={"token": invite["token"], "password": "correct-horse-battery"},
    ).json()["shop_id"]
    a_id = db.scalar(select(User.id).where(User.email == "seller-a@example.com"))
    assert client.delete(f"/api/members/{a_id}", headers=other_headers).status_code == 204
    assert client.get("/api/products", headers=_in(auth_headers, shop_b)).status_code == 403
    assert client.get("/api/products", headers=auth_headers).status_code == 200  # own shop
    assert db.get(User, a_id) is not None


def test_removal_from_default_shop_switches_default(
    client: TestClient, auth_headers: dict[str, str], other_headers: dict[str, str], db: Session
) -> None:
    """B joins A's shop; A removes B there... then B's default moves if it was that shop."""
    invite = client.post(
        "/api/members/invitations",
        headers=auth_headers,
        json={"email": "seller-b@example.com", "role": "owner"},
    ).json()
    client.post(
        "/api/auth/accept-invite",
        json={"token": invite["token"], "password": "correct-horse-battery"},
    )
    b = db.scalar(select(User).where(User.email == "seller-b@example.com"))
    assert b is not None
    shop_a = client.get("/api/auth/me", headers=auth_headers).json()["seller_id"]
    b.seller_id = shop_a  # pretend B made shop A their default
    db.commit()
    assert client.delete(f"/api/members/{b.id}", headers=auth_headers).status_code == 204
    db.expire_all()
    me = client.get("/api/auth/me", headers=other_headers).json()
    assert me["shop_name"] == "Shop B"
    assert db.scalar(select(User.seller_id).where(User.id == b.id)) != shop_a


def test_removal_from_last_shop_deletes_account(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    invite = client.post(
        "/api/members/invitations",
        headers=auth_headers,
        json={"email": "only@example.com", "role": "viewer"},
    ).json()
    client.post(
        "/api/auth/accept-invite",
        json={"token": invite["token"], "password": "member-password-123"},
    )
    uid = db.scalar(select(User.id).where(User.email == "only@example.com"))
    assert client.delete(f"/api/members/{uid}", headers=auth_headers).status_code == 204
    assert db.get(User, uid) is None
    assert db.scalar(select(RefreshToken.id).where(RefreshToken.user_id == uid)) is None


def test_default_shop_fallback_when_membership_missing(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    second_id = _create_shop(client, auth_headers, "Second Store")
    a = db.scalar(select(User).where(User.email == "seller-a@example.com"))
    assert a is not None
    db.delete(db.get(Membership, {"user_id": a.id, "seller_id": a.seller_id}))
    db.commit()
    assert client.get("/api/auth/me", headers=auth_headers).json()["seller_id"] == second_id


def test_account_without_any_shop_is_rejected(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    db.query(Membership).delete()
    db.commit()
    assert client.get("/api/auth/me", headers=auth_headers).status_code == 401


def test_create_shop_validation_and_auth(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.post("/api/shops", headers=auth_headers, json={"name": "x"}).status_code == 422
    assert client.post("/api/shops", json={"name": "Valid"}).status_code == 401
