"""新增证书规则 / 证书所需技能点 / 证书申请三张表

Revision ID: a91c3f8b2d4e
Revises: 5e7d2c41b8a6
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "a91c3f8b2d4e"
down_revision: str | None = "5e7d2c41b8a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_rule",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("cert_name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("issue_org_name", sqlmodel.sql.sqltypes.AutoString(length=150), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name=op.f("fk_certificate_rule_job_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_rule")),
        sa.UniqueConstraint("job_id", name="uk_certificate_rule_job"),
    )
    with op.batch_alter_table("certificate_rule", schema=None) as batch_op:
        batch_op.create_index("idx_certificate_rule_status", ["status"], unique=False)

    op.create_table(
        "certificate_skill",
        sa.Column("certificate_rule_id", sa.Integer(), nullable=False),
        sa.Column("skill_node_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["certificate_rule_id"], ["certificate_rule.id"], name=op.f("fk_certificate_skill_certificate_rule_id")
        ),
        sa.ForeignKeyConstraint(
            ["skill_node_id"], ["skill_node.id"], name=op.f("fk_certificate_skill_skill_node_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_skill")),
        sa.UniqueConstraint("certificate_rule_id", "skill_node_id", name="uk_certificate_skill"),
    )
    with op.batch_alter_table("certificate_skill", schema=None) as batch_op:
        batch_op.create_index("idx_certificate_skill_node", ["skill_node_id"], unique=False)

    op.create_table(
        "certificate_application",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("certificate_rule_id", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column("interview_note", sa.Text(), nullable=True),
        sa.Column("review_comment", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("student_certificate_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["certificate_rule_id"], ["certificate_rule.id"], name=op.f("fk_certificate_application_certificate_rule_id")
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["sys_user.id"], name=op.f("fk_certificate_application_reviewer_id")
        ),
        sa.ForeignKeyConstraint(
            ["student_certificate_id"],
            ["student_certificate.id"],
            name=op.f("fk_certificate_application_student_certificate_id"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["sys_user.id"], name=op.f("fk_certificate_application_student_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_application")),
    )
    with op.batch_alter_table("certificate_application", schema=None) as batch_op:
        batch_op.create_index("idx_cert_application_student", ["student_id", "status"], unique=False)
        batch_op.create_index("idx_cert_application_rule", ["certificate_rule_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("certificate_application", schema=None) as batch_op:
        batch_op.drop_index("idx_cert_application_student")
        batch_op.drop_index("idx_cert_application_rule")
    op.drop_table("certificate_application")

    with op.batch_alter_table("certificate_skill", schema=None) as batch_op:
        batch_op.drop_index("idx_certificate_skill_node")
    op.drop_table("certificate_skill")

    with op.batch_alter_table("certificate_rule", schema=None) as batch_op:
        batch_op.drop_index("idx_certificate_rule_status")
    op.drop_table("certificate_rule")
