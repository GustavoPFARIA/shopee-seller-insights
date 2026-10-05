"""make product stock optional (NULL = not tracked)

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("products", "stock_quantity", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE products SET stock_quantity = 0 WHERE stock_quantity IS NULL")
    op.alter_column("products", "stock_quantity", existing_type=sa.Integer(), nullable=False)
