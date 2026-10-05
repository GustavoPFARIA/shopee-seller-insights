"""Alembic environment.

Migrations run with MIGRATION_DATABASE_URL (schema owner role). The API itself
connects with a least-privilege role that cannot change the schema.
"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL (or DATABASE_URL) to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
