"""任务下发：发布任务主表、目标班级/分组、岗位范围、包含的项目。

口径（见 docs/方案设计.md §1.1）：
- 学生可见 = ``training_project.status = 'PUBLISHED'``：项目发布后所有学生都能看到、都能做；
- 任务 = **必修**：一条已生效（``status = 'PUBLISHED'``）的任务覆盖到某个学生后，
  这些项目对该学生就是"老师点名要求完成"的必修项（学生端 ``is_required``）；
- 岗位范围（``publish_task_job``）**只用于筛项目**（``training_project.job_id``），
  不筛学生；"发给谁"只由班级 / 分组决定。无记录 = 全部岗位。
"""

from datetime import datetime

from sqlmodel import CheckConstraint, Field, Index, SQLModel, UniqueConstraint, text

from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin

# ------------------------------------------------------------------- 发布任务


class PublishTaskBase(SQLModel):
    title: str = Field(max_length=150, description="任务名称")
    description: str | None = Field(default=None, description="任务说明（学生端展示）")
    project_level: str | None = Field(
        default=None,
        max_length=20,
        description="项目层级 BASIC 基础 / ADVANCED 进阶 / EXPANDED 拓展；为空表示不限层级",
    )
    publish_mode: str = Field(
        default="IMMEDIATE", max_length=20, description="IMMEDIATE 即时发布 / SCHEDULED 定时发布"
    )
    scheduled_at: datetime | None = Field(default=None, description="定时发布时间（SCHEDULED 必填）")
    deadline_at: datetime | None = Field(default=None, description="截止时间；为空表示不限")
    status: str = Field(
        default="PENDING",
        max_length=20,
        description="PENDING 待发布（含已排期）/ PUBLISHED 已发布 / CANCELLED 已撤回",
    )
    creator_id: int | None = Field(default=None, foreign_key="sys_user.id", description="发布教师 ID")
    published_at: datetime | None = Field(default=None, description="实际生效时间")
    cancelled_at: datetime | None = Field(default=None, description="撤回时间")
    remark: str | None = Field(default=None, max_length=255, description="备注（仅教师可见）")


class PublishTask(Base, TimestampMixin, SoftDeleteMixin, PublishTaskBase, table=True):
    """发布任务：把"已编辑完成的项目"下发给目标班级/分组，学生才看得到。

    任务进入 PUBLISHED 后目标与项目锁定（要改就撤回重建），保证已发任务的确定性。
    """

    __tablename__ = "publish_task"
    __table_args__ = (
        CheckConstraint(
            "(publish_mode = 'SCHEDULED' AND scheduled_at IS NOT NULL) OR publish_mode <> 'SCHEDULED'",
            name="chk_publish_task_schedule",
        ),
        Index("idx_publish_task_status", "status", "scheduled_at"),
        Index("idx_publish_task_creator", "creator_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------- 目标班级/分组


class PublishTaskTargetBase(SQLModel):
    task_id: int = Field(foreign_key="publish_task.id", description="任务 ID")
    target_type: str = Field(max_length=20, description="CLASS 全班 / GROUP 指定分组")
    class_id: int = Field(foreign_key="class_info.id", description="目标班级 ID")
    group_id: int | None = Field(
        default=None, foreign_key="class_group.id", description="目标分组 ID；target_type=CLASS 时为空"
    )


class PublishTaskTarget(Base, CreatedAtMixin, PublishTaskTargetBase, table=True):
    """任务目标：一条 = 一个"全班"目标或一个"指定分组"目标。

    唯一性用两条**部分唯一索引**表达（``group_id`` 为空 / 非空各一条）：
    普通 ``UNIQUE(task_id, class_id, group_id)`` 在 SQLite 与 PostgreSQL 下会因为
    NULL 互不相等而形同虚设。
    """

    __tablename__ = "publish_task_target"
    __table_args__ = (
        CheckConstraint(
            "(target_type = 'CLASS' AND group_id IS NULL) OR (target_type = 'GROUP' AND group_id IS NOT NULL)",
            name="chk_publish_task_target_group",
        ),
        Index(
            "uk_publish_task_target_class",
            "task_id",
            "class_id",
            unique=True,
            sqlite_where=text("group_id IS NULL"),
        ),
        Index(
            "uk_publish_task_target_group",
            "task_id",
            "group_id",
            unique=True,
            sqlite_where=text("group_id IS NOT NULL"),
        ),
        Index("idx_publish_task_target_class", "class_id", "target_type"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------- 岗位范围


class PublishTaskJobBase(SQLModel):
    task_id: int = Field(foreign_key="publish_task.id", description="任务 ID")
    job_id: int = Field(foreign_key="job.id", description="岗位 ID")


class PublishTaskJob(Base, CreatedAtMixin, PublishTaskJobBase, table=True):
    """任务岗位范围：只用于筛选项目（``training_project.job_id``）；没有记录 = 全部岗位。"""

    __tablename__ = "publish_task_job"
    __table_args__ = (UniqueConstraint("task_id", "job_id", name="uk_publish_task_job"),)

    id: int | None = Field(default=None, primary_key=True)


# --------------------------------------------------------------- 任务项目快照


class PublishTaskProjectBase(SQLModel):
    task_id: int = Field(foreign_key="publish_task.id", description="任务 ID")
    project_id: int = Field(
        foreign_key="training_project.id", description="项目 ID；发布时必须是 PUBLISHED 状态"
    )
    sort_no: int = Field(default=0, description="学生端展示顺序，值越小越靠前")


class PublishTaskProject(Base, CreatedAtMixin, PublishTaskProjectBase, table=True):
    """任务包含的项目快照：创建任务时定稿，之后新增的同层级项目不会自动混进来。"""

    __tablename__ = "publish_task_project"
    __table_args__ = (
        UniqueConstraint("task_id", "project_id", name="uk_publish_task_project"),
        Index("idx_publish_task_project_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "PublishTask",
    "PublishTaskBase",
    "PublishTaskJob",
    "PublishTaskJobBase",
    "PublishTaskProject",
    "PublishTaskProjectBase",
    "PublishTaskTarget",
    "PublishTaskTargetBase",
]
