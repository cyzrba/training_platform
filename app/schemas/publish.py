"""任务下发 Schema：发布任务、目标班级/分组、岗位范围、项目快照。"""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.publish import (
    PublishTaskBase,
    PublishTaskJobBase,
    PublishTaskProjectBase,
    PublishTaskTargetBase,
)
from app.schemas.base import CreatedAtRead, SoftDeleteRead, TimestampRead

# ------------------------------------------------------------------ 写入结构


class PublishTargetIn(SQLModel):
    """一个目标：`CLASS`（全班，不传 group_id）或 `GROUP`（指定分组）。"""

    class_id: int = Field(description="目标班级 ID")
    target_type: str = Field(default="CLASS", max_length=20, description="CLASS 全班 / GROUP 指定分组")
    group_id: int | None = Field(default=None, description="目标分组 ID；target_type=GROUP 时必填")


class PublishTaskCreate(SQLModel):
    """创建并下发任务：即时（IMMEDIATE）创建即生效，定时（SCHEDULED）到 scheduled_at 生效。"""

    title: str = Field(max_length=150, description="任务名称")
    description: str | None = Field(default=None, description="任务说明（学生端展示）")
    project_level: str | None = Field(
        default=None, max_length=20, description="BASIC / ADVANCED / EXPANDED；为空不限"
    )
    job_ids: list[int] = Field(default_factory=list, description="岗位范围，只用于筛项目；留空 = 全部岗位")
    targets: list[PublishTargetIn] = Field(min_length=1, description="目标班级 / 分组，至少一个")
    project_ids: list[int] = Field(
        default_factory=list, description="手工指定项目；留空 = 按岗位 × 层级自动生成"
    )
    publish_mode: str = Field(
        default="IMMEDIATE", max_length=20, description="IMMEDIATE 即时 / SCHEDULED 定时"
    )
    scheduled_at: datetime | None = Field(default=None, description="定时发布时间；SCHEDULED 必填")
    deadline_at: datetime | None = Field(default=None, description="截止时间；为空不限")
    creator_id: int | None = Field(default=None, description="发布教师 ID")
    remark: str | None = Field(default=None, max_length=255, description="备注（仅教师可见）")


class PublishTaskUpdate(SQLModel):
    """修改任务：未发布可全量改；已发布只允许改说明 / 截止时间 / 备注（服务层拦截）。"""

    title: str | None = Field(default=None, max_length=150)
    description: str | None = None
    project_level: str | None = Field(default=None, max_length=20)
    job_ids: list[int] | None = None
    targets: list[PublishTargetIn] | None = None
    project_ids: list[int] | None = None
    publish_mode: str | None = Field(default=None, max_length=20)
    scheduled_at: datetime | None = None
    deadline_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class PublishTargetsIn(SQLModel):
    """覆盖学生数预览入参。"""

    targets: list[PublishTargetIn] = Field(min_length=1, description="目标班级 / 分组")


# ------------------------------------------------------------------ 读取结构


class PublishTargetRead(CreatedAtRead, PublishTaskTargetBase):
    id: int
    class_name: str | None = Field(default=None, description="班级名称")
    group_name: str | None = Field(default=None, description="分组名称；全班时为空")


class PublishTaskJobRead(CreatedAtRead, PublishTaskJobBase):
    id: int
    job_name: str | None = Field(default=None, description="岗位名称")


class PublishTaskProjectRead(CreatedAtRead, PublishTaskProjectBase):
    id: int
    project_name: str | None = Field(default=None, description="项目名称")
    project_level: str | None = Field(default=None, description="项目层级")
    status: str | None = Field(default=None, description="项目状态（DRAFT / PUBLISHED / OFF_SHELF）")


class PublishTaskRead(TimestampRead, SoftDeleteRead, PublishTaskBase):
    """任务列表项。"""

    id: int
    project_count: int = Field(default=0, description="任务包含的项目数")
    target_count: int = Field(default=0, description="目标班级 / 分组条数")
    student_count: int = Field(default=0, description="覆盖学生数（去重）")
    expired: bool = Field(default=False, description="是否已过截止时间（派生，不改状态）")


class PublishTaskDetail(PublishTaskRead):
    """任务详情：目标、岗位范围、项目快照。"""

    targets: list[PublishTargetRead] = Field(default_factory=list)
    jobs: list[PublishTaskJobRead] = Field(default_factory=list)
    projects: list[PublishTaskProjectRead] = Field(default_factory=list)
    can_edit: bool = Field(default=True, description="是否还能改目标 / 项目（已发布即锁定）")


class PublishableProjectRead(SQLModel):
    """可选项目（按「岗位 + 层级」筛出来的 PUBLISHED 项目）。"""

    id: int
    project_name: str
    project_level: str
    difficulty: int
    job_id: int | None = None
    job_name: str | None = None
    status: str


class TargetPreviewOut(SQLModel):
    """目标覆盖情况预览。"""

    student_count: int = Field(default=0, description="覆盖学生数（去重）")
    class_count: int = Field(default=0, description="涉及班级数")
    group_count: int = Field(default=0, description="涉及分组数")
    missing_group_ids: list[int] = Field(default_factory=list, description="不存在或不属于该班级的分组 ID")


class StudentTaskRead(SQLModel):
    """学生端"我的任务"。"""

    id: int
    title: str
    description: str | None = None
    project_level: str | None = None
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    expired: bool = False
    projects: list[PublishTaskProjectRead] = Field(default_factory=list)


__all__ = [
    "PublishTargetIn",
    "PublishTargetRead",
    "PublishTargetsIn",
    "PublishTaskCreate",
    "PublishTaskDetail",
    "PublishTaskJobRead",
    "PublishTaskProjectRead",
    "PublishTaskRead",
    "PublishTaskUpdate",
    "PublishableProjectRead",
    "StudentTaskRead",
    "TargetPreviewOut",
]
