"""教学组织：班级、分组、在班学生、学生分组。"""

from datetime import datetime

from sqlmodel import Field, Index, SQLModel, UniqueConstraint, text

from app.core.time import now
from app.models.base import Base, SoftDeleteMixin, TimestampMixin

# ------------------------------------------------------------------------- 班级


class ClassInfoBase(SQLModel):
    class_name: str = Field(max_length=100, description="班级名称")
    grade_year: int | None = Field(default=None, description="届/年级")
    head_teacher_id: int | None = Field(default=None, foreign_key="sys_user.id", description="负责教师")
    group_count: int = Field(default=1, description="分组数量（列表展示冗余）")
    remark: str | None = Field(default=None, max_length=255, description="备注")
    status: str = Field(default="ACTIVE", max_length=20, description="ACTIVE 在读 / ARCHIVED 归档")


class ClassInfo(Base, TimestampMixin, SoftDeleteMixin, ClassInfoBase, table=True):
    """班级主数据：班级名称、负责教师、分组数量。"""

    __tablename__ = "class_info"
    __table_args__ = (Index("idx_class_info_teacher", "head_teacher_id"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------------- 分组


class ClassGroupBase(SQLModel):
    class_id: int = Field(foreign_key="class_info.id", description="班级 ID")
    group_no: int = Field(description="分组序号")
    group_name: str = Field(max_length=50, description="分组名称")


class ClassGroup(Base, TimestampMixin, ClassGroupBase, table=True):
    """班级内部的分组（第 1 组、第 2 组…），教师可调整。"""

    __tablename__ = "class_group"
    __table_args__ = (
        UniqueConstraint("class_id", "group_no", name="uk_class_group_no"),
        Index("idx_class_group_class", "class_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------------- 在班学生


class ClassStudentBase(SQLModel):
    class_id: int = Field(foreign_key="class_info.id", description="班级 ID")
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    status: str = Field(default="ENROLLED", max_length=20, description="ENROLLED 在班 / LEFT 已离班")
    enrolled_at: datetime = Field(default_factory=now, description="入班时间")
    left_at: datetime | None = Field(default=None, description="离班/转出时间（为空表示仍在班）")


class ClassStudent(Base, TimestampMixin, ClassStudentBase, table=True):
    """学生在班关系，含转出历史；任务发布按 ENROLLED 状态取人。"""

    __tablename__ = "class_student"
    __table_args__ = (
        Index(
            "uk_class_student_active",
            "class_id",
            "student_id",
            unique=True,
            sqlite_where=text("status = 'ENROLLED'"),
        ),
        Index("idx_class_student_student", "student_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ----------------------------------------------------------------- 学生所在分组


class ClassStudentGroupBase(SQLModel):
    class_student_id: int = Field(foreign_key="class_student.id", unique=True, description="在班记录 ID")
    group_id: int = Field(foreign_key="class_group.id", description="分组 ID")
    assigned_at: datetime = Field(default_factory=now, description="分配时间")
    assigned_by: int | None = Field(default=None, foreign_key="sys_user.id", description="分配人 ID")


class ClassStudentGroup(Base, TimestampMixin, ClassStudentGroupBase, table=True):
    """学生当前所在分组（调整分组即更新本表）。"""

    __tablename__ = "class_student_group"
    __table_args__ = (Index("idx_class_student_group_group", "group_id"),)

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "ClassGroup",
    "ClassGroupBase",
    "ClassInfo",
    "ClassInfoBase",
    "ClassStudent",
    "ClassStudentBase",
    "ClassStudentGroup",
    "ClassStudentGroupBase",
]
