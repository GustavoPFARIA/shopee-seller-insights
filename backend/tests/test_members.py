from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Invitation, RefreshToken, User
from app.services.members import INVITE_TTL
from tests.factories import Line, build_csv


def _invite(client: TestClient, owner: dict[str, str], email: str, role: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    resp = client.post(
        "/api/members/invitations", headers=owner, json={"email": email, "role": role}
    )
    return resp.status_code, resp.json()


def _join(client: TestClient, owner: dict[str, str], email: str, role: str) -> dict[str, str]:
    code, body = _invite(client, owner, email, role)
    assert code == 201, body
    resp = client.post(
        "/api/auth/accept-invite",
        json={"token": body["token"], "password": "member-password-123"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_invited_member_joins_same_shop(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    viewer = _join(client, auth_headers, "Viewer@Example.com", "viewer")
    me = client.get("/api/auth/me", headers=viewer).json()
    owner_me = client.get("/api/auth/me", headers=auth_headers).json()
    assert me["seller_id"] == owner_me["seller_id"]
    assert me["role"] == "viewer"
    assert me["email"] == "viewer@example.com"
    members = client.get("/api/members", headers=viewer).json()
    assert {m["role"] for m in members} == {"owner", "viewer"}
    # Token is single use and stored hashed.
    stored = db.scalar(select(Invitation.token_hash))
    assert stored is not None and len(stored) == 64


def test_invitation_token_is_single_use(client: TestClient, auth_headers: dict[str, str]) -> None:
    _, body = _invite(client, auth_headers, "m@example.com", "manager")
    payload = {"token": body["token"], "password": "member-password-123"}
    assert client.post("/api/auth/accept-invite", json=payload).status_code == 201
    assert client.post("/api/auth/accept-invite", json=payload).status_code == 400


def test_expired_and_forged_invitations(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    _, body = _invite(client, auth_headers, "late@example.com", "viewer")
    past = datetime.now(UTC) - INVITE_TTL - timedelta(hours=1)
    db.execute(update(Invitation).values(created_at=past - timedelta(hours=1), expires_at=past))
    db.commit()
    late = {"token": body["token"], "password": "member-password-123"}
    assert client.post("/api/auth/accept-invite", json=late).status_code == 400
    forged = {"token": "x" * 43, "password": "member-password-123"}
    assert client.post("/api/auth/accept-invite", json=forged).status_code == 400


def test_viewer_is_read_only(client: TestClient, auth_headers: dict[str, str]) -> None:
    viewer = _join(client, auth_headers, "v@example.com", "viewer")
    upload = client.post(
        "/api/uploads",
        headers=viewer,
        files={"file": ("o.csv", build_csv([Line("O1", "SKU-A", "1.00")]))},
    )
    assert upload.status_code == 403
    assert client.patch("/api/products/1", headers=viewer, json={}).status_code == 403
    imp = client.post(
        "/api/products/import", headers=viewer, files={"file": ("p.csv", b"sku,stock\nA,1\n")}
    )
    assert imp.status_code == 403
    assert client.get("/api/summary/weekly", headers=viewer).status_code == 403
    for path in ("/api/metrics/overview", "/api/alerts", "/api/products", "/api/uploads"):
        assert client.get(path, headers=viewer).status_code == 200


def test_manager_can_edit_but_not_manage_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    manager = _join(client, auth_headers, "m@example.com", "manager")
    upload = client.post(
        "/api/uploads",
        headers=manager,
        files={"file": ("o.csv", build_csv([Line("O1", "SKU-A", "1.00")]))},
    )
    assert upload.status_code == 201
    assert _invite(client, manager, "x@example.com", "owner")[0] == 403
    assert client.get("/api/members/invitations", headers=manager).status_code == 403
    assert (
        client.patch("/api/members/1", headers=manager, json={"role": "viewer"}).status_code == 403
    )


def test_role_change_applies_immediately(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    manager = _join(client, auth_headers, "m@example.com", "manager")
    member_id = db.scalar(select(User.id).where(User.email == "m@example.com"))
    resp = client.patch(f"/api/members/{member_id}", headers=auth_headers, json={"role": "viewer"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "viewer"
    # Same (still valid) access token, new permissions.
    assert client.get("/api/summary/weekly", headers=manager).status_code == 403


def test_last_owner_is_protected(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    owner_id = db.scalar(select(User.id).where(User.email == "seller-a@example.com"))
    demote = client.patch(f"/api/members/{owner_id}", headers=auth_headers, json={"role": "viewer"})
    assert demote.status_code == 409
    assert client.delete(f"/api/members/{owner_id}", headers=auth_headers).status_code == 409
    _join(client, auth_headers, "co@example.com", "owner")
    resp = client.patch(f"/api/members/{owner_id}", headers=auth_headers, json={"role": "manager"})
    assert resp.status_code == 200


def test_removing_member_revokes_access(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    member = _join(client, auth_headers, "gone@example.com", "manager")
    member_id = db.scalar(select(User.id).where(User.email == "gone@example.com"))
    assert client.delete(f"/api/members/{member_id}", headers=auth_headers).status_code == 204
    assert client.get("/api/auth/me", headers=member).status_code == 401
    assert db.scalar(select(RefreshToken.id).where(RefreshToken.user_id == member_id)) is None


def test_team_endpoints_are_isolated_between_shops(
    client: TestClient,
    auth_headers: dict[str, str],
    other_headers: dict[str, str],
    db: Session,
) -> None:
    _, body = _invite(client, auth_headers, "mine@example.com", "viewer")
    other_owner = db.scalar(select(User.id).where(User.email == "seller-b@example.com"))
    assert client.delete(f"/api/members/{other_owner}", headers=auth_headers).status_code == 404
    r = client.patch(f"/api/members/{other_owner}", headers=auth_headers, json={"role": "viewer"})
    assert r.status_code == 404
    deleted = client.delete(f"/api/members/invitations/{body['id']}", headers=other_headers)
    assert deleted.status_code == 404
    assert client.get("/api/members/invitations", headers=other_headers).json() == []
    assert "seller-b" not in client.get("/api/members", headers=auth_headers).text


def test_invitation_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    _invite(client, auth_headers, "a@example.com", "viewer")
    _, second = _invite(client, auth_headers, "a@example.com", "manager")  # replaces first
    pending = client.get("/api/members/invitations", headers=auth_headers).json()
    assert [(p["email"], p["role"]) for p in pending] == [("a@example.com", "manager")]
    assert "token" not in pending[0]
    assert (
        client.delete(f"/api/members/invitations/{second['id']}", headers=auth_headers).status_code
        == 204
    )
    assert client.get("/api/members/invitations", headers=auth_headers).json() == []
    assert _invite(client, auth_headers, "seller-a@example.com", "viewer")[0] == 409


def test_accept_rejects_email_registered_meanwhile(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _, body = _invite(client, auth_headers, "race@example.com", "viewer")
    client.post(
        "/api/auth/register",
        json={"email": "race@example.com", "password": "another-password", "shop_name": "Mine"},
    )
    resp = client.post(
        "/api/auth/accept-invite", json={"token": body["token"], "password": "member-password-123"}
    )
    assert resp.status_code == 409
