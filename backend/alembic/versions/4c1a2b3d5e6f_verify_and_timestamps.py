"""add email verification columns + suppression timestamp

Revision ID: 4c1a2b3d5e6f
Revises: 3701fcbc4495
Create Date: 2026-07-23

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "4c1a2b3d5e6f"
down_revision: Union[str, Sequence[str], None] = "3701fcbc4495"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    op.add_column("suppressions", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("suppressions", "updated_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
