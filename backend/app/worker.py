"""Shopee synchronization worker.

Usage:  python -m app.worker [--once]

Runs in its own container (same image as the API). It executes manual syncs queued
by "Sync now" (checked every few seconds) and a scheduled sync of every connected
shop every SHOPEE_SYNC_INTERVAL_MINUTES. A failure in one shop never stops the
others, and a PostgreSQL advisory lock prevents two syncs of the same shop at once.
"""

import argparse
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.integrations import shopee_sync
from app.integrations.shopee_client import ShopeeClient
from app.logging_setup import configure_logging
from app.models import ShopeeConnection, SyncRun
from app.services import digest
from app.timeutil import today_local

log = logging.getLogger("app.worker")

QUEUE_POLL_SECONDS = 15
DIGEST_CHECK_SECONDS = 3600
# Touched on every loop iteration; the container healthcheck fails if it gets old,
# so a hung worker is reported unhealthy (see `python -m app.worker --healthcheck`).
HEARTBEAT_FILE = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/ssi-worker-heartbeat"))  # noqa: S108
HEARTBEAT_MAX_AGE_SECONDS = 120
REAUTH_ERROR = shopee_sync.REAUTH_ERROR


def _record_reauth_once(seller_id: int) -> None:
    """Record the problem once instead of adding an error row every cycle."""
    with get_sessionmaker()() as db:
        last = db.scalar(
            select(SyncRun)
            .where(SyncRun.seller_id == seller_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        if last is not None and last.error == REAUTH_ERROR:
            return
        db.add(
            SyncRun(
                seller_id=seller_id,
                trigger="scheduled",
                status="error",
                finished_at=datetime.now(UTC),
                error=REAUTH_ERROR,
            )
        )
        db.commit()


def _sync_one(client: ShopeeClient, seller_id: int, run_id: int | None = None) -> str:
    """Sync one shop; returns "synced", "failed", "busy" or "reauth"."""
    with get_sessionmaker()() as db:
        conn = db.scalar(select(ShopeeConnection).where(ShopeeConnection.seller_id == seller_id))
        run = db.get(SyncRun, run_id) if run_id is not None else None
        if conn is None:
            if run is not None:
                run.status, run.error, run.finished_at = "error", "not_connected", datetime.now(UTC)
                db.commit()
            return "failed"
        if conn.refresh_expires_at <= datetime.now(UTC):  # 30-day Shopee refresh token
            if run is not None:
                run.status, run.error, run.finished_at = "error", REAUTH_ERROR, datetime.now(UTC)
                db.commit()
            else:
                _record_reauth_once(seller_id)
            return "reauth"
        try:
            result = shopee_sync.sync_seller(
                db, client, seller_id, trigger="manual" if run else "scheduled", run=run
            )
        except shopee_sync.SyncBusyError:
            return "busy"  # a queued run stays queued and is retried on the next poll
        except Exception:  # never let one shop kill the worker
            log.exception("seller %s: unexpected sync error", seller_id)
            return "failed"
        return "synced" if result.status == "success" else "failed"


def process_queue(client: ShopeeClient | None = None) -> int:
    """Run every queued manual sync that is not blocked; returns how many ran."""
    client = client or shopee_sync.make_client()
    done = 0
    skipped: set[int] = set()
    while True:
        with get_sessionmaker()() as db:
            query = (
                select(SyncRun)
                .where(SyncRun.status == "queued", SyncRun.id.not_in(skipped or {0}))
                .order_by(SyncRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            run = db.scalar(query)
            if run is None:
                return done
            run_id, seller_id = run.id, run.seller_id
            db.commit()  # release the row lock; the advisory lock guards the sync itself
        outcome = _sync_one(client, seller_id, run_id)
        if outcome == "busy":
            skipped.add(run_id)
        else:
            done += 1


def run_digests() -> int:
    """Send the weekly e-mail digests that are due (no-op without SMTP)."""
    try:
        with get_sessionmaker()() as db:
            return digest.send_due_digests(db, today_local())
    except Exception:  # a digest problem must never stop the worker
        log.exception("weekly digest run failed")
        return 0


def run_cycle() -> dict[str, int]:
    """Scheduled sync of all connected shops; returns counters for logging/tests."""
    stats = {"synced": 0, "busy": 0, "failed": 0, "reauth": 0}
    client = shopee_sync.make_client()
    with get_sessionmaker()() as db:
        shopee_sync.mark_stale_runs(db)
        seller_ids = db.scalars(select(ShopeeConnection.seller_id)).all()
    for seller_id in seller_ids:
        stats[_sync_one(client, seller_id)] += 1
    return stats


def beat() -> None:
    HEARTBEAT_FILE.touch()


def is_healthy() -> bool:
    try:
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < HEARTBEAT_MAX_AGE_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopee sync worker")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument(
        "--healthcheck", action="store_true", help="exit 0 if the running worker is alive"
    )
    args = parser.parse_args()
    if args.healthcheck:
        raise SystemExit(0 if is_healthy() else 1)
    configure_logging()
    settings = get_settings()
    stop = threading.Event()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("signal %s received, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    interval = settings.shopee_sync_interval_minutes * 60
    next_cycle = 0.0
    if not settings.shopee_enabled:
        log.info("Shopee integration not configured; only e-mail digests will run")
    next_digest_check = 0.0
    while not stop.is_set():
        beat()
        if time.monotonic() >= next_digest_check:
            run_digests()
            next_digest_check = time.monotonic() + DIGEST_CHECK_SECONDS
        if settings.shopee_enabled:
            if (ran := process_queue()) > 0:
                log.info("queued syncs done: %s", ran)
            if time.monotonic() >= next_cycle:
                log.info("cycle done: %s", run_cycle())
                next_cycle = time.monotonic() + interval
        if args.once:
            return
        stop.wait(QUEUE_POLL_SECONDS)


if __name__ == "__main__":  # pragma: no cover
    main()
