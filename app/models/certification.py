"""证书规则、证书所需技能点、证书申请与发放流程。"""

from sqlmodel import Field, Index, SQLModel, UniqueConstraint

from app.models.base import Base, CreatedAtMixin, TimestampMixin


class CertificateRuleBase(SQLModel):
    job_id: int = Field(foreign_key="job.id", description="认证岗位（1:1）")
    cert_name: str = Field(max_length=100, description="证书名称")
    issue_org_name: str | None = Field(default=None, max_length=150, description="发证机构")
    description: str | None = Field(default=None, description="证书说明")
    status: str = Field(default="ENABLED", max_length=20, description="ENABLED / DISABLED")


class CertificateRule(Base, TimestampMixin, CertificateRuleBase, table=True):
    """职业对应的证书规则：每个岗位至多一张证书。"""

    __tablename__ = "certificate_rule"
    __table_args__ = (
        UniqueConstraint("job_id", name="uk_certificate_rule_job"),
        Index("idx_certificate_rule_status", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)


class CertificateSkillBase(SQLModel):
    certificate_rule_id: int = Field(foreign_key="certificate_rule.id", description="证书规则 ID")
    skill_node_id: int = Field(foreign_key="skill_node.id", description="所需技能点 ID")


class CertificateSkill(Base, CreatedAtMixin, CertificateSkillBase, table=True):
    """证书所需技能点：全部点亮才能点亮证书。"""

    __tablename__ = "certificate_skill"
    __table_args__ = (
        UniqueConstraint("certificate_rule_id", "skill_node_id", name="uk_certificate_skill"),
        Index("idx_certificate_skill_node", "skill_node_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


class CertificateApplicationBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    certificate_rule_id: int = Field(foreign_key="certificate_rule.id", description="证书规则 ID")
    status: str = Field(default="PENDING_INTERVIEW", max_length=30, description="申请状态")
    interview_note: str | None = Field(default=None, description="AI 面试结果（本期占位）")
    review_comment: str | None = Field(default=None, max_length=255, description="人工审核意见")
    reviewer_id: int | None = Field(default=None, foreign_key="sys_user.id", description="审核人 ID")
    student_certificate_id: int | None = Field(
        default=None, foreign_key="student_certificate.id", description="通过后生成的证书记录"
    )


class CertificateApplication(Base, TimestampMixin, CertificateApplicationBase, table=True):
    """证书申请：AI 面试 → 人工审核 → 发放。驳回后可重新提交申请。"""

    __tablename__ = "certificate_application"
    __table_args__ = (
        Index("idx_cert_application_student", "student_id", "status"),
        Index("idx_cert_application_rule", "certificate_rule_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# 申请状态（本期不接入枚举字典，前端用固定文案映射）
APPLICATION_STATUS_PENDING_INTERVIEW = "PENDING_INTERVIEW"  # 待 AI 面试
APPLICATION_STATUS_UNDER_REVIEW = "UNDER_REVIEW"  # 面试通过，待人工审核
APPLICATION_STATUS_APPROVED = "APPROVED"  # 审核通过，已发证
APPLICATION_STATUS_REJECTED = "REJECTED"  # 审核驳回，可重新申请


__all__ = [
    "APPLICATION_STATUS_APPROVED",
    "APPLICATION_STATUS_PENDING_INTERVIEW",
    "APPLICATION_STATUS_REJECTED",
    "APPLICATION_STATUS_UNDER_REVIEW",
    "CertificateApplication",
    "CertificateApplicationBase",
    "CertificateRule",
    "CertificateRuleBase",
    "CertificateSkill",
    "CertificateSkillBase",
]
