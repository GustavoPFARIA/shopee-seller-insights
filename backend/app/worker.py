"""Scheduled Shopee synchronization worker.

Usage:  python -m app.worker [--once]

Runs in its own container (same image as the API). Each cycle syncs every connected
shop; a failure in one shop never stops the others. Two workers (or a worker and a
manual "Sync now") cannot sync the same shop at once thanks to the advisory lock.
"""

import argparse
import logging
import signal
import threading
from datetime import UTC, datetime
from types import FrameType

from sqlalchemy import select

from app.config import get_settings
from app.crypto import TokenCryptoError
from app.db import get_sessionmaker
from app.integrations import shopee_sync
from app.integrations.shopee_client import ShopeeApiError
from app.logging_setup import configure_logging
from app.models import ShopeeConnection, SyncRun

log = logging.getLogger("app.worker")


def run_cycle() -> dict[str, int]:
    """Sync all connected shops once; return counters for logging/tests."""
    stats = {"synced": 0, "busy": 0, "failed": 0, "reauth": 0}
    client = shopee_sync.make_client()
    with get_sessionmaker()() as db:
        shopee_sync.mark_stale_runs(db)
        connections = db.execute(
            select(ShopeeConnection.seller_id, ShopeeConnection.refresh_expires_at)
        ).all()
    for seller_id, refresh_expires_at in connections:
        with get_sessionmaker()() as db:
            if refresh_expires_at <= datetime.now(UTC):
                # Shopee refresh tokens last 30 days: the owner must authorize again.
                db.add(
                    SyncRun(
                        seller_id=seller_id,
                        trigger="scheduled",
                        status="error",
                        finished_at=datetime.now(UTC),
                        error="reauthorization_required",
                    )
                )
                db.commit()
                stats["reauth"] += 1
                continue
            try:
                run = shopee_sync.sync_seller(db, client, seller_id, trigger="scheduled")
            except shopee_sync.SyncBusyError:
                stats["busy"] += 1
                continue
            except (ShopeeApiError, TokenCryptoError, shopee_sync.OAuthError) as exc:
                log.warning("seller %s: sync failed (%s)", seller_id, type(exc).__name__)
                stats["failed"] += 1
                continue
            except Exception:  # never let one shop kill the worker
                log.exception("seller %s: unexpected sync error", seller_id)
                stats["failed"] += 1
                continue
            stats["synced" if run.status == "success" else "failed"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopee sync worker")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    configure_logging()
    settings = get_settings()
    stop = threading.Event()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("signal %s received, stopping after the current cycle", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    interval = settings.shopee_sync_interval_minutes * 60
    while not stop.is_set():
        if settings.shopee_enabled:
            stats = run_cycle()
            log.info("cycle done: %s", stats)
        else:
            log.info("Shopee integration not configured; worker idle")
        if args.once:
            return
        stop.wait(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
