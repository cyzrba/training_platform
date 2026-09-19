"""教师工作台 Schema：任教班级总览（班级 / 分组 / 任务完成进度）。"""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.schemas.organization import GroupMemberRead


class TaskStudentProgressOut(SQLModel):
    """任务里单个学生的完成进度。"""

    student_id: int = Field(description="学生账号 ID")
    user_no: str = Field(description="学号")
    real_name: str = Field(description="姓名")
    class_student_id: int = Field(description="在班记录 ID（重新分组时用它）")
    group_id: int | None = Field(default=None, description="所在分组 ID；未分组为空")
    group_name: str | None = Field(default=None, description="所在分组名称")
    completed_count: int = Field(description="已完成的项目数")
    total_count: int = Field(description="任务分给该学生的项目数")
    completion_rate: float = Field(description="该学生在这个任务里的完成率 0~100")
    status: str = Field(description="COMPLETED 全部完成 / IN_PROGRESS 进行中 / NOT_STARTED 未开始")


class TaskProgressOut(SQLModel):
    """一条任务在本班的完成进度。"""

    task_id: int
    title: str
    description: str | None = None
    status: str = Field(description="PENDING 待发布 / PUBLISHED 已发布 / CANCELLED 已撤回")
    effective: bool = Field(description="任务是否已生效（= status 为 PUBLISHED，学生此刻能看到）")
    publish_mode: str = Field(description="IMMEDIATE 即时 / SCHEDULED 定时")
    published_at: datetime | None = None
    scheduled_at: datetime | None = None
    deadline_at: datetime | None = None
    project_count: int = Field(description="任务包含的项目数")
    project_names: list[str] = Field(default_factory=list, description="项目名称")
    student_count: int = Field(description="该任务在本班覆盖的学生数")
    total_count: int = Field(description="应完成的对数 = 覆盖学生数 × 项目数")
    completed_count: int = Field(description="已完成的对数")
    in_progress_count: int = Field(description="已开始但未完成的对数")
    not_started_count: int = Field(description="还没开始的对数")
    completion_rate: float = Field(description="完成率 = 已完成对数 ÷ 应完成对数 × 100")
    students: list[TaskStudentProgressOut] = Field(default_factory=list, description="逐个学生的进度")


class ClassGroupOut(SQLModel):
    """班级里的一个分组及其成员。"""

    group_id: int
    group_no: int
    group_name: str
    member_count: int
    members: list[GroupMemberRead] = Field(default_factory=list)


class TeacherClassOut(SQLModel):
    """教师任教的一个班级：花名册 + 分组 + 任务进度。"""

    class_id: int
    class_name: str
    grade_year: int | None = None
    status: str = Field(description="ACTIVE 在读 / ARCHIVED 归档")
    remark: str | None = None
    student_count: int = Field(description="在班学生总数")
    group_count: int = Field(description="分组数")
    ungrouped_count: int = Field(description="还没分组的学生数")
    groups: list[ClassGroupOut] = Field(default_factory=list)
    ungrouped_students: list[GroupMemberRead] = Field(
        default_factory=list, description="未分组学生（教师可以直接把他们拖进某个组）"
    )
    task_count: int = Field(description="目标命中该班级的任务数（含待发布 / 已撤回）")
    average_completion_rate: float = Field(description="该班已发布任务的完成率平均值")
    tasks: list[TaskProgressOut] = Field(default_factory=list)


class TeacherClassOverviewOut(SQLModel):
    """教师工作台总览。"""

    teacher_id: int
    teacher_name: str
    class_count: int
    student_count: int = Field(description="所有任教班级的在班学生总数（跨班不去重）")
    task_count: int = Field(description="去重后的任务数（同一任务发给多个班只算一次）")
    average_completion_rate: float = Field(description="各班级平均完成率的平均值（没有任务时为 0）")
    generated_at: datetime
    classes: list[TeacherClassOut] = Field(default_factory=list)


__all__ = [
    "ClassGroupOut",
    "TaskProgressOut",
    "TaskStudentProgressOut",
    "TeacherClassOut",
    "TeacherClassOverviewOut",
]
