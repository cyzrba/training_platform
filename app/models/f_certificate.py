"""F 域 · 证书台账（1 张表）。"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StudentCertificate(Base, TimestampMixin):
    """已发放的电子证书与台账（作废、补发、有效期）。"""

    __tablename__ = "student_certificate"
    __table_args__ = (
        UniqueConstraint("certificate_no", name="uk_certificate_no"),
        Index("idx_student_cert_student", "student_id", "status"),
        Index("idx_student_cert_job", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, comment="认证岗位")
    certificate_no: Mapped[str] = mapped_column(String(64), nullable=False, comment="证书编号")
    cert_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="证书名称快照")
    issue_org_name: Mapped[str | None] = mapped_column(String(150), comment="认证机构快照")
    issue_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE"), comment="发放日期"
    )
    expire_date: Mapped[date | None] = mapped_column(Date, comment="有效期至")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'VALID'"),
        comment="VALID/EXPIRED/REVOKED/RESSUED",
    )
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="发放人")
    file_asset_id: Mapped[int | None] = mapped_column(ForeignKey("file_asset.id"), comment="电子证书文件")
    revoke_reason: Mapped[str | None] = mapped_column(String(255), comment="作废原因")
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_certificate.id"), comment="补发后新证书 ID"
    )


__all__ = ["StudentCertificate"]
