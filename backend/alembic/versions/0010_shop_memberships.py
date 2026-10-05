"""memberships: a user can belong to several shops with a role in each

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'manager', 'viewer')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["seller_id"], ["sellers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "seller_id", name="pk_memberships"),
    )
    op.create_index("ix_memberships_seller_id", "memberships", ["seller_id"])
    # Every existing user keeps exactly the access they had.
    op.execute(
        "INSERT INTO memberships (user_id, seller_id, role, created_at) "
        "SELECT id, seller_id, role, created_at FROM users"
    )
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), server_default="owner", nullable=False),
    )
    op.execute(
        "UPDATE users SET role = m.role FROM memberships m "
        "WHERE m.user_id = users.id AND m.seller_id = users.seller_id"
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('owner', 'manager', 'viewer')")
    op.drop_index("ix_memberships_seller_id", table_name="memberships")
    op.drop_table("memberships")
