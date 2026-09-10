"""B 域 · 教学组织（4 张表）。"""

from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import TZDateTime
from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class ClassInfo(Base, TimestampMixin, SoftDeleteMixin):
    """班级主数据。"""

    __tablename__ = "class_info"
    __table_args__ = (Index("idx_class_info_teacher", "head_teacher_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="班级名称")
    grade_year: Mapped[int | None] = mapped_column(SmallInteger, comment="届/年级")
    head_teacher_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="负责教师")
    group_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), comment="列表展示冗余"
    )
    remark: Mapped[str | None] = mapped_column(String(255), comment="备注")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), comment="ACTIVE/ARCHIVED"
    )


class ClassGroup(Base, TimestampMixin):
    """班级内部的分组（第 1 组、第 2 组…）。"""

    __tablename__ = "class_group"
    __table_args__ = (
        UniqueConstraint("class_id", "group_no", name="uk_class_group_no"),
        Index("idx_class_group_class", "class_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_info.id"), nullable=False, comment="班级 ID")
    group_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="分组序号")
    group_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="分组名称")


class ClassStudent(Base, TimestampMixin):
    """学生在班关系，含转出历史。"""

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_info.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ENROLLED'"), comment="ENROLLED/LEFT"
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="入班时间"
    )
    left_at: Mapped[datetime | None] = mapped_column(TZDateTime, comment="离班/转出时间")


class ClassStudentGroup(Base, TimestampMixin):
    """学生当前所在分组（调整分组即更新本表）。"""

    __tablename__ = "class_student_group"
    __table_args__ = (Index("idx_class_student_group_group", "group_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_student_id: Mapped[int] = mapped_column(
        ForeignKey("class_student.id"), nullable=False, unique=True, comment="在班记录 ID"
    )
    group_id: Mapped[int] = mapped_column(ForeignKey("class_group.id"), nullable=False, comment="分组 ID")
    assigned_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="分配时间"
    )
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="分配人")


__all__ = ["ClassGroup", "ClassInfo", "ClassStudent", "ClassStudentGroup"]
