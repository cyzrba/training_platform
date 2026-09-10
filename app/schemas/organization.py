"""教学组织 Schema。"""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.organization import (
    ClassGroupBase,
    ClassInfoBase,
    ClassStudentBase,
    ClassStudentGroupBase,
)
from app.schemas.base import SoftDeleteRead, TimestampRead

# ------------------------------------------------------------------------- 班级


class ClassInfoCreate(ClassInfoBase):
    pass


class ClassInfoUpdate(SQLModel):
    class_name: str | None = Field(default=None, max_length=100)
    grade_year: int | None = None
    head_teacher_id: int | None = None
    group_count: int | None = Field(default=None, ge=0, le=99)
    remark: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=20)


class ClassInfoRead(TimestampRead, SoftDeleteRead, ClassInfoBase):
    id: int


# ------------------------------------------------------------------------- 分组


class ClassGroupCreate(ClassGroupBase):
    pass


class ClassGroupUpdate(SQLModel):
    class_id: int | None = None
    group_no: int | None = Field(default=None, ge=1, le=99)
    group_name: str | None = Field(default=None, max_length=50)


class ClassGroupRead(TimestampRead, ClassGroupBase):
    id: int


# --------------------------------------------------------------------- 在班学生


class ClassStudentCreate(ClassStudentBase):
    pass


class ClassStudentUpdate(SQLModel):
    class_id: int | None = None
    student_id: int | None = None
    status: str | None = Field(default=None, max_length=20)
    enrolled_at: datetime | None = None
    left_at: datetime | None = None


class ClassStudentRead(TimestampRead, ClassStudentBase):
    id: int


# ----------------------------------------------------------------- 学生所在分组


class ClassStudentGroupCreate(ClassStudentGroupBase):
    pass


class ClassStudentGroupUpdate(SQLModel):
    class_student_id: int | None = None
    group_id: int | None = None
    assigned_at: datetime | None = None
    assigned_by: int | None = None


class ClassStudentGroupRead(TimestampRead, ClassStudentGroupBase):
    id: int


__all__ = [
    "ClassGroupCreate",
    "ClassGroupRead",
    "ClassGroupUpdate",
    "ClassInfoCreate",
    "ClassInfoRead",
    "ClassInfoUpdate",
    "ClassStudentCreate",
    "ClassStudentGroupCreate",
    "ClassStudentGroupRead",
    "ClassStudentGroupUpdate",
    "ClassStudentRead",
    "ClassStudentUpdate",
]
