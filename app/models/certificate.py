"""证书台账：已发放的电子证书与台账。"""

from datetime import date

from sqlmodel import Field, Index, SQLModel, UniqueConstraint

from app.models.base import Base, TimestampMixin


class StudentCertificateBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    job_id: int = Field(foreign_key="job.id", description="认证岗位")
    certificate_no: str = Field(max_length=64, description="证书编号（唯一）")
    cert_name: str = Field(max_length=100, description="证书名称快照")
    issue_org_name: str | None = Field(default=None, max_length=150, description="认证机构快照")
    issue_date: date = Field(default_factory=date.today, description="发放日期")
    expire_date: date | None = Field(default=None, description="有效期至")
    status: str = Field(
        default="VALID",
        max_length=20,
        description="VALID 有效 / EXPIRED 过期 / REVOKED 作废 / RESSUED 已补发",
    )
    issued_by: int | None = Field(default=None, foreign_key="sys_user.id", description="发放人 ID")
    file_asset_id: int | None = Field(default=None, foreign_key="file_asset.id", description="电子证书文件")
    revoke_reason: str | None = Field(default=None, max_length=255, description="作废原因")
    replaced_by_id: int | None = Field(
        default=None, foreign_key="student_certificate.id", description="补发后新证书 ID"
    )


class StudentCertificate(Base, TimestampMixin, StudentCertificateBase, table=True):
    """已发放的电子证书与台账（作废、补发、有效期）。"""

    __tablename__ = "student_certificate"
    __table_args__ = (
        UniqueConstraint("certificate_no", name="uk_certificate_no"),
        Index("idx_student_cert_student", "student_id", "status"),
        Index("idx_student_cert_job", "job_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = ["StudentCertificate", "StudentCertificateBase"]
