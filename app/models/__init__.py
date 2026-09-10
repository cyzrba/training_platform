"""全部 ORM 模型（38 张表）。

导入本包即完成 Base.metadata 注册，Alembic 与建表脚本都依赖这里的汇总导入。
"""

from app.models.a_account import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SystemConfig,
    SysUser,
    SysUserRole,
)
from app.models.b_org import ClassGroup, ClassInfo, ClassStudent, ClassStudentGroup
from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.c_job_skill import (
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
from app.models.d_project import ProjectModule, ProjectStageTemplate, TrainingProject
from app.models.e_attempt_review import (
    AttemptStage,
    AttemptStageFile,
    FileAsset,
    ProjectSubmission,
    ReviewAiJob,
    ReviewRecord,
    StudentProject,
    TrainingAttempt,
)
from app.models.f_certificate import StudentCertificate
from app.models.h_ai_kb import (
    AiQaCitation,
    AiQaMessage,
    AiQaSession,
    KnowledgeChunk,
    KnowledgeDoc,
)
from app.models.i_notify_audit import Notification, OperationLog

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
    "FileAsset",
    "GrowthRule",
    "Job",
    "JobSkill",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "Notification",
    "OperationLog",
    "ProjectModule",
    "ProjectSkill",
    "ProjectStageTemplate",
    "ProjectSubmission",
    "ReviewAiJob",
    "ReviewRecord",
    "SkillNode",
    "SkillNodeDependency",
    "SkillTree",
    "SoftDeleteMixin",
    "StudentCertificate",
    "StudentJob",
    "StudentProject",
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
