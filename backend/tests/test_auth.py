from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from tests.conftest import register


def _login(client: TestClient, email: str, password: str) -> tuple[int, dict[str, str]]:
    resp = client.post("/api/auth/login", data={"username": email, "password": password})
    return resp.status_code, resp.json()


def test_register_login_and_me(client: TestClient) -> None:
    register(client, "Owner@Example.com", "My Shop")
    code, body = _login(client, "owner@example.com", "correct-horse-battery")
    assert code == 200
    token = body["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"
    assert me.json()["shop_name"] == "My Shop"


def test_password_is_hashed(client: TestClient, db: Session) -> None:
    register(client, "hash@example.com")
    stored = db.scalar(select(User.password_hash))
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert "correct-horse-battery" not in stored


def test_duplicate_email_rejected(client: TestClient) -> None:
    register(client, "dup@example.com")
    resp = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "another-password", "shop_name": "X shop"},
    )
    assert resp.status_code == 409


def test_weak_password_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": "short", "shop_name": "Shop"},
    )
    assert resp.status_code == 422


def test_wrong_password(client: TestClient) -> None:
    register(client, "user@example.com")
    assert _login(client, "user@example.com", "wrong-password")[0] == 401


def test_unknown_user(client: TestClient) -> None:
    assert _login(client, "nobody@example.com", "whatever-pass")[0] == 401


def test_missing_token(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_expired_token(client: TestClient) -> None:
    register(client, "exp@example.com")
    settings = get_settings()
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {"sub": "1", "iat": past, "exp": past + timedelta(minutes=1), "type": "access"},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_signed_with_other_key(client: TestClient) -> None:
    register(client, "forged@example.com")
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "1", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"},
        "attacker-key-" + "z" * 32,
        algorithm="HS256",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_alg_none_token_rejected(client: TestClient) -> None:
    register(client, "none@example.com")
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "1", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"},
        key=None,
        algorithm="none",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_for_deleted_user(client: TestClient) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "999", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"},
        get_settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_login_rate_limited(client: TestClient) -> None:
    limit = get_settings().login_rate_limit
    codes = [_login(client, "x@example.com", "bad-password")[0] for _ in range(limit + 1)]
    assert codes[:limit] == [401] * limit
    assert codes[-1] == 429


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_cors_only_allows_configured_origin(client: TestClient) -> None:
    allowed = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = client.options(
        "/api/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in denied.headers


def test_production_rejects_dev_secrets() -> None:
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="dev-only"):
        Settings(
            app_env="production",
            jwt_secret="dev-only-insecure-jwt-secret-change-me-please",
            pii_hash_secret="x" * 40,
        )
    ok = Settings(app_env="production", jwt_secret="y" * 40, pii_hash_secret="x" * 40)
    assert ok.app_env == "production"
