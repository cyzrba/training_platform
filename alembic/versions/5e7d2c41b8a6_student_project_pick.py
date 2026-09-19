"""新增「我的实训」清单表 student_project_pick

Revision ID: 5e7d2c41b8a6
Revises: 3c9a1f6e5d24
Create Date: 2026-09-19 22:30:00.000000

学生从项目库里自己挑的项目放这张表；老师发任务点名（必修）不写这张表，展示时实时并集。
关系表，不做软删。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5e7d2c41b8a6'
down_revision: str | None = '3c9a1f6e5d24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "student_project_pick",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sort_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["sys_user.id"], name=op.f("fk_student_project_pick_student_id")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["training_project.id"], name=op.f("fk_student_project_pick_project_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_project_pick")),
        sa.UniqueConstraint("student_id", "project_id", name="uk_student_project_pick"),
    )
    op.create_index(
        "idx_student_project_pick_project", "student_project_pick", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_student_project_pick_project", table_name="student_project_pick")
    op.drop_table("student_project_pick")
