"""queued sync runs (manual syncs run by the worker)

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sync_runs_status", "sync_runs", type_="check")
    op.create_check_constraint(
        "ck_sync_runs_status", "sync_runs", "status IN ('queued', 'running', 'success', 'error')"
    )


def downgrade() -> None:
    op.execute("UPDATE sync_runs SET status = 'error', error = 'cancelled' WHERE status = 'queued'")
    op.drop_constraint("ck_sync_runs_status", "sync_runs", type_="check")
    op.create_check_constraint(
        "ck_sync_runs_status", "sync_runs", "status IN ('running', 'success', 'error')"
    )
