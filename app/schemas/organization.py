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


class ClassInfoDetail(ClassInfoRead):
    """班级详情：带在班学生数（列表展示冗余 group_count 之外再补一个实时值）。"""

    student_count: int = Field(default=0, description="在班（ENROLLED）学生数")


# ------------------------------------------------------------------------- 分组


class ClassGroupCreate(ClassGroupBase):
    pass


class ClassGroupUpdate(SQLModel):
    class_id: int | None = None
    group_no: int | None = Field(default=None, ge=1, le=99)
    group_name: str | None = Field(default=None, max_length=50)


class ClassGroupRead(TimestampRead, ClassGroupBase):
    id: int


class ClassGroupCreateIn(SQLModel):
    """新建分组：group_no 留空则自动取当前最大序号 +1。"""

    group_name: str = Field(max_length=50, description="分组名称")
    group_no: int | None = Field(default=None, ge=1, le=99, description="分组序号，留空自动分配")


class ClassGroupDetail(ClassGroupRead):
    """分组详情：带组内人数。"""

    student_count: int = Field(default=0, description="组内学生数")


class GroupMemberRead(SQLModel):
    """分组里的一个学生（精简结构，供分组视图直接展示）。"""

    class_student_id: int = Field(description="在班记录 ID（移出分组时用它）")
    student_id: int = Field(description="学生账号 ID")
    user_no: str = Field(description="学号")
    real_name: str = Field(description="姓名")
    major_name: str | None = Field(default=None, description="专业")


class ClassGroupWithStudents(ClassGroupDetail):
    """分组 + 组内学生；不带 with_students 时 students 为空数组。"""

    students: list[GroupMemberRead] = Field(
        default_factory=list, description="组内在班学生（with_students=true 时返回）"
    )


class GroupMemberBatchIn(SQLModel):
    """批量把在班学生加入某个分组。"""

    class_student_ids: list[int] = Field(min_length=1, description="在班记录 ID 列表")


class GroupMemberBatchResult(SQLModel):
    """批量分组结果。"""

    group_id: int = Field(description="分组 ID")
    added: int = Field(description="加入（或从其它组调整过来）的人数")
    member_total: int = Field(description="操作后组内人数")


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


class ClassStudentDetail(ClassStudentRead):
    """在班学生：拼接学生账号信息与所在分组，供班级花名册使用。"""

    user_no: str = Field(description="学号/工号")
    real_name: str = Field(description="姓名")
    major_name: str | None = Field(default=None, description="专业")
    class_name: str | None = Field(default=None, description="班级名称")
    account_status: str = Field(description="账号状态 ACTIVE / DISABLED")
    group_id: int | None = Field(default=None, description="所在分组 ID")
    group_name: str | None = Field(default=None, description="所在分组名称")


class ClassStudentAddIn(SQLModel):
    """把学生加入班级：账号存在则复用，不存在则按默认密码新建（教师端单个加人）。"""

    user_no: str = Field(max_length=50, description="学号；已存在则直接复用该学生账号")
    real_name: str | None = Field(default=None, max_length=50, description="姓名；账号不存在时必填")
    major_name: str | None = Field(default=None, max_length=100, description="专业")
    phone: str | None = Field(default=None, max_length=32, description="手机号")
    email: str | None = Field(default=None, max_length=128, description="邮箱")
    group_no: int | None = Field(default=None, ge=1, le=99, description="可选：同时分到第几组")


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


class ClassStudentGroupSetIn(SQLModel):
    """分配 / 调整学生分组。"""

    group_id: int = Field(description="目标分组 ID")
    assigned_by: int | None = Field(default=None, description="分配人（教师）ID")


class ClassPasswordResetIn(SQLModel):
    """按班级批量重置学生密码。"""

    new_password: str | None = Field(
        default=None, min_length=6, max_length=64, description="新密码；留空 = 重置为默认密码"
    )


# ------------------------------------------------------------- Excel 名单导入


class StudentImportError(SQLModel):
    """导入校验失败的单行明细。"""

    row: int = Field(description="Excel 中的行号（含表头，从 1 开始）")
    user_no: str | None = Field(default=None, description="该行的学号（如果有）")
    reason: str = Field(description="失败原因")


class StudentImportResult(SQLModel):
    """导入结果（整批校验通过才会有这个结构；有任何问题直接 422 返回明细）。"""

    dry_run: bool = Field(description="是否仅预校验未落库")
    class_id: int = Field(description="目标班级 ID")
    total: int = Field(description="Excel 中的有效数据行数")
    created_users: int = Field(default=0, description="新建的学生账号数")
    reused_users: int = Field(default=0, description="复用已有账号数")
    created_class_students: int = Field(default=0, description="新建的在班记录数")
    assigned_groups: int = Field(default=0, description="按名单里的分组序号成功分入分组的人数")
    ignored_group_hints: int = Field(
        default=0,
        description="名单里带了分组序号、但该分组还没建的提示行数（这些学生按未分组导入）",
    )


__all__ = [
    "ClassGroupCreateIn",
    "ClassGroupDetail",
    "ClassGroupCreate",
    "ClassGroupRead",
    "ClassGroupUpdate",
    "ClassInfoDetail",
    "ClassInfoCreate",
    "ClassInfoRead",
    "ClassInfoUpdate",
    "ClassPasswordResetIn",
    "ClassStudentAddIn",
    "ClassStudentCreate",
    "ClassStudentDetail",
    "ClassStudentGroupCreate",
    "ClassStudentGroupRead",
    "ClassStudentGroupSetIn",
    "ClassStudentGroupUpdate",
    "ClassStudentRead",
    "ClassStudentUpdate",
    "GroupMemberBatchIn",
    "GroupMemberBatchResult",
    "GroupMemberRead",
    "ClassGroupWithStudents",
    "StudentImportError",
    "StudentImportResult",
]
