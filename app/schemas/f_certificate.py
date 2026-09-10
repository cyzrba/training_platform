"""F 域 Schema · 证书台账。"""

from datetime import date

from pydantic import Field

from app.models.enums import CertificateStatus
from app.schemas.base import ORMModel, TimestampRead


class StudentCertificateBase(ORMModel):
    student_id: int = Field(description="学生 ID")
    job_id: int = Field(description="认证岗位")
    certificate_no: str = Field(min_length=1, max_length=64, description="证书编号")
    cert_name: str = Field(min_length=1, max_length=100, description="证书名称快照")
    issue_org_name: str | None = Field(None, max_length=150, description="认证机构快照")
    issue_date: date | None = Field(None, description="发放日期，默认当天")
    expire_date: date | None = Field(None, description="有效期至")
    status: CertificateStatus = Field(CertificateStatus.VALID, description="证书状态")
    issued_by: int | None = Field(None, description="发放人")
    file_asset_id: int | None = Field(None, description="电子证书文件")
    revoke_reason: str | None = Field(None, max_length=255, description="作废原因")
    replaced_by_id: int | None = Field(None, description="补发后新证书 ID")


class StudentCertificateCreate(StudentCertificateBase):
    pass


class StudentCertificateUpdate(ORMModel):
    certificate_no: str | None = Field(None, min_length=1, max_length=64)
    cert_name: str | None = Field(None, min_length=1, max_length=100)
    issue_org_name: str | None = Field(None, max_length=150)
    issue_date: date | None = None
    expire_date: date | None = None
    status: CertificateStatus | None = None
    issued_by: int | None = None
    file_asset_id: int | None = None
    revoke_reason: str | None = Field(None, max_length=255)
    replaced_by_id: int | None = None


class StudentCertificateRead(TimestampRead, StudentCertificateBase):
    id: int


__all__ = [
    "StudentCertificateCreate",
    "StudentCertificateRead",
    "StudentCertificateUpdate",
]
