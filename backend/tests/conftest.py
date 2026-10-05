"""Test fixtures. Tests run against a real PostgreSQL database.

Set TEST_DATABASE_URL to point at a disposable database; the schema is created
with the real Alembic migrations.
"""

import os
from collections.abc import Iterator

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/shopee_insights_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MIGRATION_DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-" + "x" * 32)
os.environ.setdefault("PII_HASH_SECRET", "test-pii-secret-" + "y" * 32)
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> Iterator[None]:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE order_items, orders, uploads, products, users, sellers, "
                "rate_limit_hits "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def register(client: TestClient, email: str, shop: str = "Test Shop") -> dict[str, str]:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery", "shop_name": shop},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    return register(client, "seller-a@example.com", "Shop A")


@pytest.fixture
def other_headers(client: TestClient) -> dict[str, str]:
    return register(client, "seller-b@example.com", "Shop B")
