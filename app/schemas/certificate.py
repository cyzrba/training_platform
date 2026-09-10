"""证书台账 Schema。"""

from datetime import date

from sqlmodel import Field, SQLModel

from app.models.certificate import StudentCertificateBase
from app.schemas.base import TimestampRead


class StudentCertificateCreate(StudentCertificateBase):
    pass


class StudentCertificateUpdate(SQLModel):
    certificate_no: str | None = Field(default=None, max_length=64)
    cert_name: str | None = Field(default=None, max_length=100)
    issue_org_name: str | None = Field(default=None, max_length=150)
    issue_date: date | None = None
    expire_date: date | None = None
    status: str | None = Field(default=None, max_length=20)
    issued_by: int | None = None
    file_asset_id: int | None = None
    revoke_reason: str | None = Field(default=None, max_length=255)
    replaced_by_id: int | None = None


class StudentCertificateRead(TimestampRead, StudentCertificateBase):
    id: int


__all__ = [
    "StudentCertificateCreate",
    "StudentCertificateRead",
    "StudentCertificateUpdate",
]
