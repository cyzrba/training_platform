"""知识库文档加 doc_type / 切片加结构化字段（评分标准入库的前置改动）

Revision ID: 996df7ebcc4a
Revises: 2fa52fe757e0
Create Date: 2026-09-14 13:19:03.401695

三处改动：

1. ``knowledge_doc`` 删掉 ``biz_type`` / ``biz_id``：一个字段扛"用途 + 归属"两个维度，
   评分标准一进来就错位；归属改由 ``file_asset → project_file`` 表达。同时加 ``doc_type``
   区分 KNOWLEDGE 与 EVAL_CRITERIA，并记录解析错误与切分策略版本。
2. ``knowledge_chunk`` 加 ``heading_path`` / ``page_no`` / ``token_count``，并把 ``status``
   默认值从 READY 改成 PENDING（先切片，向量写进 Milvus 之后才算 READY）。
3. 新增索引 ``idx_knowledge_doc_file_asset``，支撑"项目 → 附件 → 文件 → 知识文档"这条链。
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '996df7ebcc4a'
down_revision: str | None = '2fa52fe757e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_doc", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "doc_type",
                sqlmodel.sql.sqltypes.AutoString(length=20),
                nullable=False,
                server_default="KNOWLEDGE",
            )
        )
        batch_op.add_column(sa.Column("parse_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("chunk_strategy", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=True)
        )
        batch_op.drop_column("biz_type")
        batch_op.drop_column("biz_id")
        batch_op.create_index("idx_knowledge_doc_file_asset", ["file_asset_id"], unique=False)

    with op.batch_alter_table("knowledge_chunk", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("heading_path", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True)
        )
        batch_op.add_column(sa.Column("page_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("token_count", sa.Integer(), nullable=True))
        batch_op.alter_column(
            "status",
            existing_type=sqlmodel.sql.sqltypes.AutoString(length=20),
            server_default="PENDING",
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_chunk", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sqlmodel.sql.sqltypes.AutoString(length=20),
            server_default="READY",
            existing_nullable=False,
        )
        batch_op.drop_column("token_count")
        batch_op.drop_column("page_no")
        batch_op.drop_column("heading_path")

    with op.batch_alter_table("knowledge_doc", schema=None) as batch_op:
        batch_op.drop_index("idx_knowledge_doc_file_asset")
        batch_op.add_column(sa.Column("biz_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("biz_type", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=True)
        )
        batch_op.drop_column("chunk_strategy")
        batch_op.drop_column("parse_error")
        batch_op.drop_column("doc_type")
