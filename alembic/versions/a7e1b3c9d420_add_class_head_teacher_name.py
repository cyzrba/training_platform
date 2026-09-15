"""class_info 增加负责教师姓名快照

Revision ID: a7e1b3c9d420
Revises: 996df7ebcc4a
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7e1b3c9d420"
down_revision: str | None = "996df7ebcc4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("class_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("head_teacher_name", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("class_info", schema=None) as batch_op:
        batch_op.drop_column("head_teacher_name")
