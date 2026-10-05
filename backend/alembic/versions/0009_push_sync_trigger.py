"""allow 'push' as a sync trigger (Shopee webhook)

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sync_runs_trigger", "sync_runs", type_="check")
    op.create_check_constraint(
        "ck_sync_runs_trigger", "sync_runs", "trigger IN ('manual', 'scheduled', 'push')"
    )


def downgrade() -> None:
    op.execute("UPDATE sync_runs SET trigger = 'manual' WHERE trigger = 'push'")
    op.drop_constraint("ck_sync_runs_trigger", "sync_runs", type_="check")
    op.create_check_constraint(
        "ck_sync_runs_trigger", "sync_runs", "trigger IN ('manual', 'scheduled')"
    )
