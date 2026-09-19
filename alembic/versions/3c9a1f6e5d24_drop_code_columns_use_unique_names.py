"""技能树 / 技能节点 / 标准化模块库去掉编码列，唯一约束落到名称上

Revision ID: 3c9a1f6e5d24
Revises: 8b2df4d8233d
Create Date: 2026-09-19 21:30:00.000000

这三张表的编码（tree_code / node_code / stage_key）与名称一一对应，业务上名字本身就是唯一标识：
老师建完技能树/关卡后还得再编一个 code，接口也要透出两套标识给前端，纯属重复维护。
去掉编码列，把唯一约束改到名称上（uk_skill_tree_name / uk_skill_node_name / uk_stage_template_name）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c9a1f6e5d24'
down_revision: str | None = '8b2df4d8233d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('skill_tree', schema=None) as batch_op:
        batch_op.drop_constraint('uk_skill_tree_code', type_='unique')
        batch_op.drop_column('tree_code')
        batch_op.create_unique_constraint('uk_skill_tree_name', ['tree_name'])

    with op.batch_alter_table('skill_node', schema=None) as batch_op:
        batch_op.drop_constraint('uk_skill_node_code', type_='unique')
        batch_op.drop_column('node_code')
        batch_op.create_unique_constraint('uk_skill_node_name', ['node_name'])

    with op.batch_alter_table('project_stage_template', schema=None) as batch_op:
        batch_op.drop_constraint('uk_stage_template_key', type_='unique')
        batch_op.drop_column('stage_key')
        batch_op.create_unique_constraint('uk_stage_template_name', ['stage_name'])


def downgrade() -> None:
    # 原来的编码已经删了、补不回来，回滚只能给每行发一个占位编码（LEGACY_<id>），
    # 保证列非空且唯一；真要恢复真实编码得靠业务侧重新填。
    targets = (
        ('project_stage_template', 'stage_key', 'uk_stage_template_name', 'uk_stage_template_key'),
        ('skill_node', 'node_code', 'uk_skill_node_name', 'uk_skill_node_code'),
        ('skill_tree', 'tree_code', 'uk_skill_tree_name', 'uk_skill_tree_code'),
    )
    for table, column, name_constraint, code_constraint in targets:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name_constraint, type_='unique')
            batch_op.add_column(sa.Column(column, sa.String(length=50), nullable=True))
        op.execute(f"UPDATE {table} SET {column} = 'LEGACY_' || id WHERE {column} IS NULL")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(column, existing_type=sa.String(length=50), nullable=False)
            batch_op.create_unique_constraint(code_constraint, [column])
