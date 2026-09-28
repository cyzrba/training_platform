"""删除岗位热度字段 heat

Revision ID: b5d7e2f9a1c3
Revises: a91c3f8b2d4e
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b5d7e2f9a1c3"
down_revision: str | None = "a91c3f8b2d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.drop_column("heat")


def downgrade() -> None:
    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.add_column(sa.Column("heat", sa.Integer(), nullable=False, server_default="0"))
