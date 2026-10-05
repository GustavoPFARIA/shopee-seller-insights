import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app import ratelimit
from app.models import RateLimitHit


def test_allows_up_to_limit_then_blocks() -> None:
    results = [ratelimit.allow("login:203.0.113.7", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_keys_are_independent() -> None:
    assert ratelimit.allow("login:a", 1, 60)
    assert not ratelimit.allow("login:a", 1, 60)
    assert ratelimit.allow("login:b", 1, 60)
    assert ratelimit.allow("upload:a", 1, 60)


def test_raw_ip_is_never_stored(db: Session) -> None:
    ratelimit.allow("login:198.51.100.23", 5, 60)
    keys = db.scalars(select(RateLimitHit.key)).all()
    assert len(keys) == 1
    assert "198.51.100.23" not in keys[0]
    assert len(keys[0]) == 64


def test_new_window_resets_counter(db: Session) -> None:
    assert ratelimit.allow("k", 1, 60)
    assert not ratelimit.allow("k", 1, 60)
    # Simulate the window having passed.
    db.execute(update(RateLimitHit).values(window_start=text("now() - interval '2 minutes'")))
    db.commit()
    assert ratelimit.allow("k", 1, 60)


def test_cleanup_removes_old_windows(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    ratelimit.allow("old", 5, 60)
    db.execute(update(RateLimitHit).values(window_start=text("now() - interval '3 days'")))
    db.commit()
    monkeypatch.setattr(ratelimit, "CLEANUP_PROBABILITY", 1.0)
    ratelimit.allow("new", 5, 60)
    assert db.scalar(select(RateLimitHit.hits)) == 1
    assert len(db.scalars(select(RateLimitHit.key)).all()) == 1


def test_reset() -> None:
    ratelimit.allow("x", 1, 60)
    ratelimit.reset()
    assert ratelimit.allow("x", 1, 60)
