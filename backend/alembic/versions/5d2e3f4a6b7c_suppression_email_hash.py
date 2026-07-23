"""add suppressions.email_hash (privacy purge keeps do-not-contact record)

Revision ID: 5d2e3f4a6b7c
Revises: 4c1a2b3d5e6f
Create Date: 2026-07-23

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "5d2e3f4a6b7c"
down_revision: Union[str, Sequence[str], None] = "4c1a2b3d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # Guarded so a partially-applied run (SQLite does DDL non-transactionally,
    # so a mid-migration failure leaves earlier ADD COLUMNs in place) can be
    # re-run without a "duplicate column" error.
    if "email_hash" not in _columns("suppressions"):
        op.add_column("suppressions", sa.Column("email_hash", sa.String(length=64), nullable=True))
    if "ix_suppressions_email_hash" not in _indexes("suppressions"):
        op.create_index("ix_suppressions_email_hash", "suppressions", ["email_hash"])

    if "created_at" not in _columns("email_logs"):
        # Added NULLABLE with no server_default: SQLite rejects ADD COLUMN with a
        # NOT NULL + non-constant default (CURRENT_TIMESTAMP). Historical rows are
        # backfilled below; new rows always get a value from the ORM default.
        op.add_column("email_logs", sa.Column("created_at", sa.DateTime(), nullable=True))
        op.execute("UPDATE email_logs SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    if "ix_email_logs_created_at" not in _indexes("email_logs"):
        op.create_index("ix_email_logs_created_at", "email_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_logs_created_at", table_name="email_logs")
    op.drop_column("email_logs", "created_at")
    op.drop_index("ix_suppressions_email_hash", table_name="suppressions")
    op.drop_column("suppressions", "email_hash")
