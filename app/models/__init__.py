"""全部 ORM 表模型（44 张表）。

导入本包即完成 Base.metadata 注册，Alembic 与建表脚本都依赖这里的汇总导入。
"""

from app.models.account import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SystemConfig,
    SysUser,
    SysUserRole,
)
from app.models.attempt import (
    AttemptStage,
    AttemptStageFile,
    FileAsset,
    ProjectSubmission,
    StudentProject,
    StudentProjectPick,
    TrainingAttempt,
)
from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin
from app.models.certificate import StudentCertificate
from app.models.job_skill import (
    GrowthRule,
    Job,
    JobSkill,
    ProjectSkill,
    SkillNode,
    SkillNodeDependency,
    SkillTree,
    StudentJob,
    StudentSkill,
)
from app.models.knowledge import (
    AiQaCitation,
    AiQaMessage,
    AiQaSession,
    KnowledgeChunk,
    KnowledgeDoc,
)
from app.models.notification import Notification, OperationLog
from app.models.organization import ClassGroup, ClassInfo, ClassStudent, ClassStudentGroup
from app.models.project import (
    ProjectFile,
    ProjectModule,
    ProjectStageTemplate,
    TrainingProject,
)
from app.models.publish import (
    PublishTask,
    PublishTaskJob,
    PublishTaskProject,
    PublishTaskTarget,
)
from app.models.review import ReviewAiJob, ReviewRecord

__all__ = [
    "AiQaCitation",
    "AiQaMessage",
    "AiQaSession",
    "AttemptStage",
    "AttemptStageFile",
    "Base",
    "ClassGroup",
    "ClassInfo",
    "ClassStudent",
    "ClassStudentGroup",
    "CreatedAtMixin",
    "FileAsset",
    "GrowthRule",
    "Job",
    "JobSkill",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "Notification",
    "OperationLog",
    "ProjectModule",
    "ProjectFile",
    "ProjectSkill",
    "ProjectStageTemplate",
    "ProjectSubmission",
    "PublishTask",
    "PublishTaskJob",
    "PublishTaskProject",
    "PublishTaskTarget",
    "ReviewAiJob",
    "ReviewRecord",
    "SkillNode",
    "SkillNodeDependency",
    "SkillTree",
    "SoftDeleteMixin",
    "StudentCertificate",
    "StudentJob",
    "StudentProject",
    "StudentProjectPick",
    "StudentSkill",
    "SysPermission",
    "SysRole",
    "SysRolePermission",
    "SysUser",
    "SysUserRole",
    "SystemConfig",
    "TimestampMixin",
    "TrainingAttempt",
    "TrainingProject",
]
