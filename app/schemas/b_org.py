"""B 域 Schema · 教学组织。"""

from datetime import datetime

from pydantic import Field

from app.models.enums import ClassStatus, ClassStudentStatus
from app.schemas.base import ORMModel, SoftDeleteRead, TimestampRead

# ------------------------------------------------------------- class_info


class ClassInfoBase(ORMModel):
    class_name: str = Field(min_length=1, max_length=100, description="班级名称")
    grade_year: int | None = Field(None, ge=1900, le=2999, description="届/年级")
    head_teacher_id: int | None = Field(None, description="负责教师")
    group_count: int = Field(1, ge=0, le=99, description="分组数量（列表展示冗余）")
    remark: str | None = Field(None, max_length=255, description="备注")
    status: ClassStatus = Field(ClassStatus.ACTIVE, description="班级状态")


class ClassInfoCreate(ClassInfoBase):
    pass


class ClassInfoUpdate(ORMModel):
    class_name: str | None = Field(None, min_length=1, max_length=100)
    grade_year: int | None = Field(None, ge=1900, le=2999)
    head_teacher_id: int | None = None
    group_count: int | None = Field(None, ge=0, le=99)
    remark: str | None = Field(None, max_length=255)
    status: ClassStatus | None = None


class ClassInfoRead(TimestampRead, SoftDeleteRead, ClassInfoBase):
    id: int


# ------------------------------------------------------------ class_group


class ClassGroupBase(ORMModel):
    class_id: int = Field(description="班级 ID")
    group_no: int = Field(ge=1, le=99, description="分组序号")
    group_name: str = Field(min_length=1, max_length=50, description="分组名称")


class ClassGroupCreate(ClassGroupBase):
    pass


class ClassGroupUpdate(ORMModel):
    class_id: int | None = None
    group_no: int | None = Field(None, ge=1, le=99)
    group_name: str | None = Field(None, min_length=1, max_length=50)


class ClassGroupRead(TimestampRead, ClassGroupBase):
    id: int


# ---------------------------------------------------------- class_student


class ClassStudentBase(ORMModel):
    class_id: int = Field(description="班级 ID")
    student_id: int = Field(description="学生 ID")
    status: ClassStudentStatus = Field(ClassStudentStatus.ENROLLED, description="在班状态")
    enrolled_at: datetime | None = Field(None, description="入班时间，默认当前时间")
    left_at: datetime | None = Field(None, description="离班/转出时间")


class ClassStudentCreate(ClassStudentBase):
    pass


class ClassStudentUpdate(ORMModel):
    class_id: int | None = None
    student_id: int | None = None
    status: ClassStudentStatus | None = None
    enrolled_at: datetime | None = None
    left_at: datetime | None = None


class ClassStudentRead(TimestampRead, ClassStudentBase):
    id: int


# ---------------------------------------------------- class_student_group


class ClassStudentGroupBase(ORMModel):
    class_student_id: int = Field(description="在班记录 ID")
    group_id: int = Field(description="分组 ID")
    assigned_at: datetime | None = Field(None, description="分配时间，默认当前时间")
    assigned_by: int | None = Field(None, description="分配人")


class ClassStudentGroupCreate(ClassStudentGroupBase):
    pass


class ClassStudentGroupUpdate(ORMModel):
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
