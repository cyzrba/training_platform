"""新增 project_module_skill（关卡↔技能点），并把现有项目级技能平铺到关卡

Revision ID: c7e4a1b93f28
Revises: b5d7e2f9a1c3
Create Date: 2026-09-29

技能点亮的权威绑定从"项目级"改到"关卡级"：原来挂在项目上的技能，按
"该项目下每个关卡都挂一遍"平铺过来，保证改造后技能仍有归属、进度能算出来。
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "c7e4a1b93f28"
down_revision: str | None = "b5d7e2f9a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_module_skill",
        sa.Column("project_module_id", sa.Integer(), nullable=False),
        sa.Column("skill_node_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_module_id"],
            ["project_module.id"],
            name=op.f("fk_project_module_skill_project_module_id"),
        ),
        sa.ForeignKeyConstraint(
            ["skill_node_id"], ["skill_node.id"], name=op.f("fk_project_module_skill_skill_node_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_module_skill")),
        sa.UniqueConstraint(
            "project_module_id", "skill_node_id", name="uk_project_module_skill"
        ),
    )
    with op.batch_alter_table("project_module_skill", schema=None) as batch_op:
        batch_op.create_index("idx_project_module_skill_node", ["skill_node_id"], unique=False)

    # 老数据平铺：项目级技能复制到该项目下的每一个关卡
    op.execute(
        """
        INSERT INTO project_module_skill (project_module_id, skill_node_id, created_at)
        SELECT pm.id, ps.skill_node_id, CURRENT_TIMESTAMP
        FROM project_module pm
        JOIN project_skill ps ON ps.project_id = pm.project_id
        WHERE NOT EXISTS (
            SELECT 1 FROM project_module_skill pms
            WHERE pms.project_module_id = pm.id AND pms.skill_node_id = ps.skill_node_id
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("project_module_skill", schema=None) as batch_op:
        batch_op.drop_index("idx_project_module_skill_node")
    op.drop_table("project_module_skill")
