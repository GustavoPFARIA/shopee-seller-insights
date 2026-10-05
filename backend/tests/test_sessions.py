from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import RefreshToken
from tests.conftest import register

CSRF = {"X-Requested-With": "ssi"}


def _login(client: TestClient, email: str = "s@example.com") -> str:
    resp = client.post(
        "/api/auth/login", data={"username": email, "password": "correct-horse-battery"}
    )
    assert resp.status_code == 200
    return str(resp.json()["access_token"])


def test_login_sets_hardened_refresh_cookie(client: TestClient) -> None:
    register(client, "s@example.com")
    resp = client.post(
        "/api/auth/login",
        data={"username": "s@example.com", "password": "correct-horse-battery"},
    )
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("ssi_refresh=")
    for flag in ("HttpOnly", "Path=/api/auth", "SameSite=strict", "Max-Age=604800"):
        assert flag.lower() in cookie.lower()
    assert "ssi_refresh" not in resp.text  # the refresh token is never in the body


def test_refresh_token_stored_only_as_hash(client: TestClient, db: Session) -> None:
    register(client, "s@example.com")
    raw = client.cookies.get("ssi_refresh")
    assert raw
    hashes = db.scalars(select(RefreshToken.token_hash)).all()
    assert raw not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_refresh_rotates_and_returns_new_access_token(client: TestClient) -> None:
    register(client, "s@example.com")
    first = client.cookies.get("ssi_refresh")
    resp = client.post("/api/auth/refresh", headers=CSRF)
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert client.cookies.get("ssi_refresh") != first
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_reuse_of_rotated_token_revokes_family(client: TestClient) -> None:
    register(client, "s@example.com")
    stolen = client.cookies.get("ssi_refresh")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 200
    legit = client.cookies.get("ssi_refresh")

    client.cookies.set("ssi_refresh", stolen or "", path="/api/auth")
    resp = client.post("/api/auth/refresh", headers=CSRF)
    assert resp.status_code == 401
    assert "ssi_refresh=" in resp.headers["set-cookie"]  # cookie cleared

    client.cookies.set("ssi_refresh", legit or "", path="/api/auth")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401


def test_refresh_requires_csrf_header(client: TestClient) -> None:
    register(client, "s@example.com")
    assert client.post("/api/auth/refresh").status_code == 403
    assert client.post("/api/auth/refresh", headers={"X-Requested-With": "x"}).status_code == 403


def test_refresh_without_cookie_or_unknown(client: TestClient) -> None:
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401
    client.cookies.set("ssi_refresh", "forged", path="/api/auth")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401


def test_expired_refresh_token(client: TestClient, db: Session) -> None:
    register(client, "s@example.com")
    past = datetime.now(UTC) - timedelta(days=30)
    db.execute(update(RefreshToken).values(created_at=past - timedelta(days=1), expires_at=past))
    db.commit()
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401


def test_logout_revokes_session(client: TestClient, db: Session) -> None:
    register(client, "s@example.com")
    token = client.cookies.get("ssi_refresh")
    resp = client.post("/api/auth/logout", headers=CSRF)
    assert resp.status_code == 204
    assert all(r is not None for r in db.scalars(select(RefreshToken.revoked_at)).all())
    client.cookies.set("ssi_refresh", token or "", path="/api/auth")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401


def test_logout_without_cookie_is_harmless(client: TestClient) -> None:
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.post("/api/auth/logout").status_code == 403


def test_access_token_lifetime_is_short(client: TestClient) -> None:
    import jwt

    register(client, "s@example.com")
    payload = jwt.decode(_login(client), options={"verify_signature": False})
    assert payload["exp"] - payload["iat"] == 15 * 60
