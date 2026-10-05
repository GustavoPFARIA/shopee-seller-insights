"""Fixed-window rate limiter stored in PostgreSQL.

Counters live in the database so every API replica shares them, and the window is
computed from the database clock so replicas never disagree. Keys are HMAC'd, so
client IP addresses are never stored in clear text.
"""

import random

from sqlalchemy import delete, func, literal
from sqlalchemy.dialects.postgresql import INTERVAL, insert

from app.db import get_engine
from app.models import RateLimitHit
from app.security import pseudonymize

CLEANUP_PROBABILITY = 0.01
RETENTION = "1 day"


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Count one hit for `key` and return whether it is within `limit`."""
    epoch = func.extract("epoch", func.now())
    window_start = func.to_timestamp(func.floor(epoch / window_seconds) * window_seconds)
    stmt = (
        insert(RateLimitHit)
        .values(key=pseudonymize(key), window_start=window_start, hits=1)
        .on_conflict_do_update(
            constraint="pk_rate_limit_hits",
            set_={"hits": RateLimitHit.hits + 1},
        )
        .returning(RateLimitHit.hits)
    )
    # Own transaction: the hit counts even if the request fails afterwards.
    with get_engine().begin() as conn:
        hits = conn.execute(stmt).scalar_one()
        if random.random() < CLEANUP_PROBABILITY:  # noqa: S311 (not security sensitive)
            conn.execute(
                delete(RateLimitHit).where(
                    RateLimitHit.window_start < func.now() - literal(RETENTION).cast(INTERVAL)
                )
            )
    return int(hits) <= limit


def reset() -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(RateLimitHit))
