"""枚举字典：与《数据库表字段清单》附录 A 一一对应。

落库仍是 varchar code（见各模型 status 字段），枚举在此集中定义，
供 Pydantic 校验、seed 数据与 /api/enums 字典接口共用。

每个枚举成员后面的中文注释即该 code 的业务含义，与 ENUM_DICTS 里的文案保持一致。
"""

from dataclasses import dataclass
from enum import StrEnum


class UserType(StrEnum):
    """用户类型：决定账号在平台里的身份与可见菜单。"""

    STUDENT = "STUDENT"  # 学生
    TEACHER = "TEACHER"  # 教师
    ADMIN = "ADMIN"  # 管理员


class AccountStatus(StrEnum):
    """账号状态。"""

    ACTIVE = "ACTIVE"  # 正常，可登录
    DISABLED = "DISABLED"  # 停用，禁止登录


class ClassStatus(StrEnum):
    """班级状态。"""

    ACTIVE = "ACTIVE"  # 在读
    ARCHIVED = "ARCHIVED"  # 归档，历史班级只读


class ClassStudentStatus(StrEnum):
    """学生在班状态。"""

    ENROLLED = "ENROLLED"  # 在班，任务发布按此状态取人
    LEFT = "LEFT"  # 已离班 / 已转出


class EnabledStatus(StrEnum):
    """岗位 / 技能树 / 技能节点的启停状态。"""

    ENABLED = "ENABLED"  # 启用
    DISABLED = "DISABLED"  # 停用


class LearningLevel(StrEnum):
    """实训层级：项目层级、岗位推荐等级、成长规则层级共用。"""

    BASIC = "BASIC"  # 基础
    ADVANCED = "ADVANCED"  # 进阶
    EXPANDED = "EXPANDED"  # 拓展


class ProjectStatus(StrEnum):
    """实训项目状态。"""

    DRAFT = "DRAFT"  # 草稿，未发布
    PUBLISHED = "PUBLISHED"  # 已发布，学生可见可做
    OFF_SHELF = "OFF_SHELF"  # 已下架


class StudentProjectStatus(StrEnum):
    """学生实训记录状态（学生 × 项目的闯关进度）。"""

    NOT_STARTED = "NOT_STARTED"  # 未开始
    IN_PROGRESS = "IN_PROGRESS"  # 进行中
    SUBMITTED = "SUBMITTED"  # 已提交，待评审
    COMPLETED = "COMPLETED"  # 已完成


class AttemptStatus(StrEnum):
    """闯关轮次状态（重新挑战生成新一轮）。"""

    IN_PROGRESS = "IN_PROGRESS"  # 进行中
    SUBMITTED = "SUBMITTED"  # 已提交
    COMPLETED = "COMPLETED"  # 已完成
    ABANDONED = "ABANDONED"  # 已放弃


class SubmissionStatus(StrEnum):
    """整单提交的评审流转状态。"""

    PENDING_AI = "PENDING_AI"  # 待 AI 评审
    AI_PASSED = "AI_PASSED"  # AI 判定通过
    AI_FAILED = "AI_FAILED"  # AI 判定未通过，学生可提异议
    PENDING_REVIEW = "PENDING_REVIEW"  # 待教师复审
    REVIEWING = "REVIEWING"  # 复审中
    REVIEWED = "REVIEWED"  # 已复审
    WITHDRAWN = "WITHDRAWN"  # 学生已撤回


class Conclusion(StrEnum):
    """项目是否通过（由评审分数与配置的及格线判定，不由评审人指定）。"""

    PASS = "PASS"  # 通过
    FAIL = "FAIL"  # 不通过


class ReviewKind(StrEnum):
    """评审类型。"""

    AI = "AI"  # AI 自动评审
    TEACHER = "TEACHER"  # 教师复审


class ReviewStatus(StrEnum):
    """评审记录状态。"""

    DRAFT = "DRAFT"  # 草稿，AI 中间结果或教师未定稿
    FINAL = "FINAL"  # 最终结论


class AiJobStatus(StrEnum):
    """AI 评审异步任务状态（用于重试与对账）。"""

    QUEUED = "QUEUED"  # 排队中
    PROCESSING = "PROCESSING"  # 处理中
    SUCCEED = "SUCCEED"  # 成功
    FAILED = "FAILED"  # 失败，可重试


class SkillProgressSource(StrEnum):
    """技能进度的来源。"""

    PROJECT = "PROJECT"  # 由完成项目推进
    REVIEW = "REVIEW"  # 由评审结果推进
    MANUAL = "MANUAL"  # 人工调整


class CertificateStatus(StrEnum):
    """证书状态。"""

    VALID = "VALID"  # 有效
    EXPIRED = "EXPIRED"  # 已过期
    REVOKED = "REVOKED"  # 已作废
    RESSUED = "RESSUED"  # 已补发（拼写沿用字段清单，不要改）


class FileBizType(StrEnum):
    """文件业务类型（file_asset.biz_type）。"""

    SUBMISSION = "SUBMISSION"  # 作答附件
    AVATAR = "AVATAR"  # 头像
    CERT_PDF = "CERT_PDF"  # 证书 PDF
    IMPORT = "IMPORT"  # 导入名单
    REPORT = "REPORT"  # 实训报告
    # 以下四个与 ProjectFileKind 对齐：项目附件上传时 file_asset.biz_type 直接取 file_kind
    REPORT_TEMPLATE = "REPORT_TEMPLATE"  # 报告模板
    DATASET = "DATASET"  # 数据文件
    GUIDE = "GUIDE"  # 说明文档
    SCORING_CRITERIA = "SCORING_CRITERIA"  # 评分标准


class FileStatus(StrEnum):
    """文件状态。"""

    ACTIVE = "ACTIVE"  # 可用
    INVALID = "INVALID"  # 已失效


class ProjectFileKind(StrEnum):
    """项目附件的用途。"""

    REPORT_TEMPLATE = "REPORT_TEMPLATE"  # 报告模板
    DATASET = "DATASET"  # 数据文件
    GUIDE = "GUIDE"  # 说明文档 / 指导书
    SCORING_CRITERIA = "SCORING_CRITERIA"  # 评分标准（上传后进知识库，供批改检索）
    OTHER = "OTHER"  # 其它


class KnowledgeDocType(StrEnum):
    """知识文档用途：决定检索链路与可见范围。"""

    KNOWLEDGE = "KNOWLEDGE"  # 知识问答走这条（学生可问）
    EVAL_CRITERIA = "EVAL_CRITERIA"  # 评分标准，只供批改链路内部调用


class KnowledgeSource(StrEnum):
    """知识文档来源。"""

    UPLOAD = "UPLOAD"  # 文件上传
    TEXT = "TEXT"  # 手工录入


class KnowledgeDocStatus(StrEnum):
    """知识文档状态（解析流水线）。"""

    PARSING = "PARSING"  # 解析中
    READY = "READY"  # 可用，切片已入库
    FAILED = "FAILED"  # 解析失败
    DISABLED = "DISABLED"  # 停用


class KnowledgeChunkStatus(StrEnum):
    """知识切片状态。"""

    PENDING = "PENDING"  # 已切片待入库
    READY = "READY"  # 可用，向量已写入 Milvus
    FAILED = "FAILED"  # 失败
    DISABLED = "DISABLED"  # 停用


class QaSessionStatus(StrEnum):
    """AI 问答会话状态。"""

    ACTIVE = "ACTIVE"  # 进行中
    CLOSED = "CLOSED"  # 已结束


class QaRole(StrEnum):
    """问答消息角色。"""

    USER = "USER"  # 用户提问
    ASSISTANT = "ASSISTANT"  # AI 回答


class QaMessageStatus(StrEnum):
    """问答消息状态。"""

    COMPLETED = "COMPLETED"  # 已完成
    FAILED = "FAILED"  # 失败


class NotificationBizType(StrEnum):
    """站内通知的业务类型。"""

    REVIEW_RESULT = "REVIEW_RESULT"  # 复审结果
    STAGE_RESULT = "STAGE_RESULT"  # 关卡结果
    TASK_PUBLISH = "TASK_PUBLISH"  # 任务发布
    CERT = "CERT"  # 证书
    POINT = "POINT"  # 积分


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
            "REPORT_TEMPLATE": "报告模板",
            "DATASET": "数据文件",
            "GUIDE": "说明文档",
            "SCORING_CRITERIA": "评分标准",
        },
    ),
    _enum_dict(
        "file_status",
        "文件状态",
        FileStatus,
        {"ACTIVE": "可用", "INVALID": "已失效"},
    ),
    _enum_dict(
        "project_file_kind",
        "项目附件用途",
        ProjectFileKind,
        {
            "REPORT_TEMPLATE": "报告模板",
            "DATASET": "数据文件",
            "GUIDE": "说明文档",
            "SCORING_CRITERIA": "评分标准",
            "OTHER": "其它",
        },
    ),
    _enum_dict(
        "knowledge_doc_type",
        "知识文档用途",
        KnowledgeDocType,
        {"KNOWLEDGE": "知识问答", "EVAL_CRITERIA": "评分标准"},
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
        {"PENDING": "待入库", "READY": "可用", "FAILED": "失败", "DISABLED": "停用"},
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
