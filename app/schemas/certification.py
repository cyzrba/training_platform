"""证书规则、证书所需技能点、证书申请与发放的读写结构。"""

from datetime import datetime
from typing import Literal

from sqlmodel import Field, SQLModel

from app.models.certification import (
    CertificateApplicationBase,
    CertificateRuleBase,
)
from app.schemas.base import TimestampRead


class CertificateRuleCreate(SQLModel):
    job_id: int = Field(description="认证岗位 ID")
    cert_name: str = Field(max_length=100, description="证书名称")
    issue_org_name: str | None = Field(default=None, max_length=150, description="发证机构")
    description: str | None = Field(default=None, description="证书说明")
    status: str = Field(default="ENABLED", max_length=20, description="ENABLED / DISABLED")
    skill_node_ids: list[int] = Field(default_factory=list, description="所需技能点 ID 列表")


class CertificateRuleUpdate(SQLModel):
    cert_name: str | None = Field(default=None, max_length=100)
    issue_org_name: str | None = Field(default=None, max_length=150)
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    skill_node_ids: list[int] | None = Field(default=None, description="覆盖式设置所需技能点")


class CertificateSkillRead(SQLModel):
    skill_node_id: int = Field(description="技能点 ID")
    node_name: str = Field(description="技能点名称")
    description: str | None = Field(default=None, description="技能点说明")
    tree_name: str | None = Field(default=None, description="所属技能树名称")


class CertificateRuleRead(TimestampRead, CertificateRuleBase):
    id: int
    job_name: str | None = Field(default=None, description="岗位名称")
    skill_count: int = Field(default=0, description="所需技能点数量")
    skill_node_ids: list[int] = Field(default_factory=list, description="所需技能点 ID 列表")


class CertificateRuleDetail(CertificateRuleRead):
    skills: list[CertificateSkillRead] = Field(default_factory=list, description="所需技能点明细")


class CertificateApplicationCreate(SQLModel):
    certificate_rule_id: int = Field(description="要申请的证书规则 ID")


class CertificateApplicationReviewIn(SQLModel):
    status: Literal["APPROVED", "REJECTED"] = Field(description="通过 / 驳回")
    review_comment: str | None = Field(default=None, max_length=255, description="审核意见")


class CertificateApplicationRead(TimestampRead, CertificateApplicationBase):
    id: int
    student_name: str | None = Field(default=None, description="学生姓名")
    student_no: str | None = Field(default=None, description="学号")
    job_name: str | None = Field(default=None, description="岗位名称")
    cert_name: str | None = Field(default=None, description="证书名称")


class CertificateSkillProgressItem(SQLModel):
    skill_node_id: int = Field(description="技能点 ID")
    node_name: str = Field(description="技能点名称")
    description: str | None = Field(default=None, description="技能点说明")
    progress: float = Field(default=0, description="技能点进度 0~100")
    lit: bool = Field(default=False, description="是否已点亮（进度 100%）")


class CertificateOverviewItem(SQLModel):
    certificate_rule_id: int = Field(description="证书规则 ID")
    job_id: int = Field(description="岗位 ID")
    job_name: str | None = Field(default=None, description="岗位名称")
    cert_name: str = Field(description="证书名称")
    issue_org_name: str | None = Field(default=None, description="发证机构")
    description: str | None = Field(default=None, description="证书说明")
    skill_total: int = Field(default=0, description="所需技能点总数")
    skill_done: int = Field(default=0, description="已点亮技能点个数")
    lit: bool = Field(default=False, description="证书是否已点亮")
    skills: list[CertificateSkillProgressItem] = Field(default_factory=list, description="所需技能点进度")
    application: CertificateApplicationRead | None = Field(default=None, description="最近一次申请")


class CertificationOverview(SQLModel):
    student_id: int = Field(description="学生 ID")
    items: list[CertificateOverviewItem] = Field(default_factory=list, description="各选定职业的证书概览")


class IssuedCertificateRead(SQLModel):
    id: int
    job_id: int
    job_name: str | None = Field(default=None, description="岗位名称")
    student_name: str | None = Field(default=None, description="学生姓名")
    student_no: str | None = Field(default=None, description="学号")
    cert_name: str = Field(description="证书名称")
    issue_org_name: str | None = Field(default=None, description="发证机构")
    certificate_no: str = Field(description="证书编号")
    issue_date: datetime | None = Field(default=None, description="发放日期")
    status: str = Field(description="证书状态")


__all__ = [
    "CertificateApplicationCreate",
    "CertificateApplicationRead",
    "CertificateApplicationReviewIn",
    "CertificateOverviewItem",
    "CertificateRuleCreate",
    "CertificateRuleDetail",
    "CertificateRuleRead",
    "CertificateRuleUpdate",
    "CertificateSkillProgressItem",
    "CertificateSkillRead",
    "CertificationOverview",
    "IssuedCertificateRead",
]
