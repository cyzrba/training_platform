"""枚举字典：与《数据库表字段清单》附录 A 一一对应。

落库仍是 varchar code（见各模型 status 字段），枚举在此集中定义，
供 Pydantic 校验、seed 数据与 /api/v1/enums 字典接口共用。
"""

from dataclasses import dataclass
from enum import StrEnum


class UserType(StrEnum):
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"
    ADMIN = "ADMIN"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ClassStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ClassStudentStatus(StrEnum):
    ENROLLED = "ENROLLED"
    LEFT = "LEFT"


class EnabledStatus(StrEnum):
    """岗位 / 技能树 / 技能节点的启停状态。"""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class LearningLevel(StrEnum):
    """实训层级：项目层级、岗位推荐等级、成长规则层级共用。"""

    BASIC = "BASIC"
    ADVANCED = "ADVANCED"
    EXPANDED = "EXPANDED"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    OFF_SHELF = "OFF_SHELF"


class StudentProjectStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"


class AttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class SubmissionStatus(StrEnum):
    PENDING_AI = "PENDING_AI"
    AI_PASSED = "AI_PASSED"
    AI_FAILED = "AI_FAILED"
    PENDING_REVIEW = "PENDING_REVIEW"
    REVIEWING = "REVIEWING"
    REVIEWED = "REVIEWED"
    WITHDRAWN = "WITHDRAWN"


class Conclusion(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ReviewKind(StrEnum):
    AI = "AI"
    TEACHER = "TEACHER"


class ReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    FINAL = "FINAL"


class AiJobStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEED = "SUCCEED"
    FAILED = "FAILED"


class SkillState(StrEnum):
    LOCKED = "LOCKED"
    ACTIVATED = "ACTIVATED"
    MASTERED = "MASTERED"


class SkillProgressSource(StrEnum):
    PROJECT = "PROJECT"
    REVIEW = "REVIEW"
    MANUAL = "MANUAL"


class CertificateStatus(StrEnum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    RESSUED = "RESSUED"


class FileBizType(StrEnum):
    SUBMISSION = "SUBMISSION"
    AVATAR = "AVATAR"
    CERT_PDF = "CERT_PDF"
    IMPORT = "IMPORT"
    REPORT = "REPORT"


class FileStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"


class KnowledgeBizType(StrEnum):
    JOB = "JOB"
    COURSE = "COURSE"
    SYSTEM = "SYSTEM"


class KnowledgeSource(StrEnum):
    UPLOAD = "UPLOAD"
    TEXT = "TEXT"


class KnowledgeDocStatus(StrEnum):
    PARSING = "PARSING"
    READY = "READY"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class KnowledgeChunkStatus(StrEnum):
    READY = "READY"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class QaSessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class QaRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class QaMessageStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NotificationBizType(StrEnum):
    REVIEW_RESULT = "REVIEW_RESULT"
    STAGE_RESULT = "STAGE_RESULT"
    TASK_PUBLISH = "TASK_PUBLISH"
    CERT = "CERT"
    POINT = "POINT"


@dataclass(frozen=True)
class EnumItem:
    code: str
    label: str


@dataclass(frozen=True)
class EnumDict:
    key: str
    title: str
    items: tuple[EnumItem, ...]


def _enum_dict(key: str, title: str, enum_cls: type[StrEnum], labels: dict[str, str]) -> EnumDict:
    return EnumDict(
        key=key,
        title=title,
        items=tuple(
            EnumItem(code=member.value, label=labels.get(member.value, member.value)) for member in enum_cls
        ),
    )


ENUM_DICTS: tuple[EnumDict, ...] = (
    _enum_dict(
        "user_type",
        "用户类型",
        UserType,
        {"STUDENT": "学生", "TEACHER": "教师", "ADMIN": "管理员"},
    ),
    _enum_dict(
        "account_status",
        "账号状态",
        AccountStatus,
        {"ACTIVE": "正常", "DISABLED": "停用"},
    ),
    _enum_dict(
        "class_status",
        "班级状态",
        ClassStatus,
        {"ACTIVE": "在读", "ARCHIVED": "归档"},
    ),
    _enum_dict(
        "class_student_status",
        "在班状态",
        ClassStudentStatus,
        {"ENROLLED": "在班", "LEFT": "已离班"},
    ),
    _enum_dict(
        "enabled_status",
        "启停状态",
        EnabledStatus,
        {"ENABLED": "启用", "DISABLED": "停用"},
    ),
    _enum_dict(
        "learning_level",
        "实训层级",
        LearningLevel,
        {"BASIC": "基础", "ADVANCED": "进阶", "EXPANDED": "拓展"},
    ),
    _enum_dict(
        "project_status",
        "实训项目状态",
        ProjectStatus,
        {"DRAFT": "草稿", "PUBLISHED": "已发布", "OFF_SHELF": "已下架"},
    ),
    _enum_dict(
        "student_project_status",
        "学生实训记录状态",
        StudentProjectStatus,
        {"NOT_STARTED": "未开始", "IN_PROGRESS": "进行中", "SUBMITTED": "已提交", "COMPLETED": "已完成"},
    ),
    _enum_dict(
        "attempt_status",
        "闯关轮次状态",
        AttemptStatus,
        {"IN_PROGRESS": "进行中", "SUBMITTED": "已提交", "COMPLETED": "已完成", "ABANDONED": "已放弃"},
    ),
    _enum_dict(
        "submission_status",
        "提交评审状态",
        SubmissionStatus,
        {
            "PENDING_AI": "待 AI 评审",
            "AI_PASSED": "AI 通过",
            "AI_FAILED": "AI 未通过",
            "PENDING_REVIEW": "待复审",
            "REVIEWING": "复审中",
            "REVIEWED": "已复审",
            "WITHDRAWN": "已撤回",
        },
    ),
    _enum_dict(
        "conclusion",
        "评审结论",
        Conclusion,
        {"PASS": "通过", "FAIL": "不通过"},
    ),
    _enum_dict(
        "review_kind",
        "评审类型",
        ReviewKind,
        {"AI": "AI 自动评审", "TEACHER": "教师复审"},
    ),
    _enum_dict(
        "review_status",
        "评审记录状态",
        ReviewStatus,
        {"DRAFT": "草稿", "FINAL": "最终"},
    ),
    _enum_dict(
        "ai_job_status",
        "AI 评审任务状态",
        AiJobStatus,
        {"QUEUED": "排队中", "PROCESSING": "处理中", "SUCCEED": "成功", "FAILED": "失败"},
    ),
    _enum_dict(
        "skill_state",
        "技能状态",
        SkillState,
        {"LOCKED": "未解锁", "ACTIVATED": "已激活", "MASTERED": "已精通"},
    ),
    _enum_dict(
        "skill_progress_source",
        "技能进度来源",
        SkillProgressSource,
        {"PROJECT": "项目", "REVIEW": "评审", "MANUAL": "人工"},
    ),
    _enum_dict(
        "certificate_status",
        "证书状态",
        CertificateStatus,
        {"VALID": "有效", "EXPIRED": "已过期", "REVOKED": "已作废", "RESSUED": "已补发"},
    ),
    _enum_dict(
        "file_biz_type",
        "文件业务类型",
        FileBizType,
        {
            "SUBMISSION": "作答附件",
            "AVATAR": "头像",
            "CERT_PDF": "证书 PDF",
            "IMPORT": "导入名单",
            "REPORT": "实训报告",
        },
    ),
    _enum_dict(
        "file_status",
        "文件状态",
        FileStatus,
        {"ACTIVE": "可用", "INVALID": "已失效"},
    ),
    _enum_dict(
        "knowledge_biz_type",
        "知识文档关联类型",
        KnowledgeBizType,
        {"JOB": "岗位", "COURSE": "课程", "SYSTEM": "系统"},
    ),
    _enum_dict(
        "knowledge_source",
        "知识文档来源",
        KnowledgeSource,
        {"UPLOAD": "文件上传", "TEXT": "手工录入"},
    ),
    _enum_dict(
        "knowledge_doc_status",
        "知识文档状态",
        KnowledgeDocStatus,
        {"PARSING": "解析中", "READY": "可用", "FAILED": "失败", "DISABLED": "停用"},
    ),
    _enum_dict(
        "knowledge_chunk_status",
        "知识切片状态",
        KnowledgeChunkStatus,
        {"READY": "可用", "FAILED": "失败", "DISABLED": "停用"},
    ),
    _enum_dict(
        "qa_session_status",
        "问答会话状态",
        QaSessionStatus,
        {"ACTIVE": "进行中", "CLOSED": "已结束"},
    ),
    _enum_dict(
        "qa_role",
        "问答消息角色",
        QaRole,
        {"USER": "用户提问", "ASSISTANT": "AI 回答"},
    ),
    _enum_dict(
        "qa_message_status",
        "问答消息状态",
        QaMessageStatus,
        {"COMPLETED": "已完成", "FAILED": "失败"},
    ),
    _enum_dict(
        "notification_biz_type",
        "通知业务类型",
        NotificationBizType,
        {
            "REVIEW_RESULT": "复审结果",
            "STAGE_RESULT": "关卡结果",
            "TASK_PUBLISH": "任务发布",
            "CERT": "证书",
            "POINT": "积分",
        },
    ),
)

ENUM_INDEX: dict[str, EnumDict] = {item.key: item for item in ENUM_DICTS}
