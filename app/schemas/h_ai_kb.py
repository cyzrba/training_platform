"""H 域 Schema · AI 问答知识库。"""

from decimal import Decimal

from pydantic import Field

from app.models.enums import (
    KnowledgeBizType,
    KnowledgeChunkStatus,
    KnowledgeDocStatus,
    KnowledgeSource,
    QaMessageStatus,
    QaRole,
    QaSessionStatus,
)
from app.schemas.base import CreatedAtRead, ORMModel, SoftDeleteRead, TimestampRead

# ----------------------------------------------------------- knowledge_doc


class KnowledgeDocBase(ORMModel):
    title: str = Field(min_length=1, max_length=255, description="知识文档标题")
    biz_type: KnowledgeBizType | None = Field(None, description="关联对象类型")
    biz_id: int | None = Field(None, description="关联业务 ID")
    file_asset_id: int | None = Field(None, description="文件 ID")
    source: KnowledgeSource = Field(KnowledgeSource.UPLOAD, description="来源")
    description: str | None = Field(None, description="说明")
    status: KnowledgeDocStatus = Field(KnowledgeDocStatus.PARSING, description="文档状态")
    total_chunks: int = Field(0, ge=0, description="切片总数")
    uploaded_by: int | None = Field(None, description="上传人")


class KnowledgeDocCreate(KnowledgeDocBase):
    pass


class KnowledgeDocUpdate(ORMModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    biz_type: KnowledgeBizType | None = None
    biz_id: int | None = None
    description: str | None = None
    status: KnowledgeDocStatus | None = None
    total_chunks: int | None = Field(None, ge=0)


class KnowledgeDocRead(TimestampRead, SoftDeleteRead, KnowledgeDocBase):
    id: int


# --------------------------------------------------------- knowledge_chunk


class KnowledgeChunkBase(ORMModel):
    doc_id: int = Field(description="知识文档 ID")
    chunk_index: int = Field(ge=0, description="切片序号")
    content: str = Field(min_length=1, description="切片正文")
    content_hash: str | None = Field(None, max_length=64, description="内容哈希")
    char_count: int = Field(0, ge=0, description="字符数")
    vector_id: str | None = Field(None, max_length=64, description="Milvus 主键")
    model_name: str | None = Field(None, max_length=100, description="模型名称")
    status: KnowledgeChunkStatus = Field(KnowledgeChunkStatus.READY, description="切片状态")


class KnowledgeChunkCreate(KnowledgeChunkBase):
    pass


class KnowledgeChunkUpdate(ORMModel):
    content: str | None = Field(None, min_length=1)
    content_hash: str | None = Field(None, max_length=64)
    char_count: int | None = Field(None, ge=0)
    vector_id: str | None = Field(None, max_length=64)
    model_name: str | None = Field(None, max_length=100)
    status: KnowledgeChunkStatus | None = None


class KnowledgeChunkRead(TimestampRead, KnowledgeChunkBase):
    id: int


# ----------------------------------------------------------- ai_qa_session


class AiQaSessionBase(ORMModel):
    student_id: int = Field(description="学生 ID")
    title: str | None = Field(None, max_length=255, description="会话标题")
    subject: str | None = Field(None, max_length=100, description="课程/知识范围")
    status: QaSessionStatus = Field(QaSessionStatus.ACTIVE, description="会话状态")


class AiQaSessionCreate(AiQaSessionBase):
    pass


class AiQaSessionUpdate(ORMModel):
    title: str | None = Field(None, max_length=255)
    subject: str | None = Field(None, max_length=100)
    status: QaSessionStatus | None = None


class AiQaSessionRead(TimestampRead, AiQaSessionBase):
    id: int


# ----------------------------------------------------------- ai_qa_message


class AiQaMessageBase(ORMModel):
    session_id: int = Field(description="会话 ID")
    role: QaRole = Field(description="消息角色")
    content: str = Field(min_length=1, description="消息内容")
    model_name: str | None = Field(None, max_length=100, description="模型名称")
    prompt_tokens: int | None = Field(None, ge=0, description="输入 Token 数")
    completion_tokens: int | None = Field(None, ge=0, description="输出 Token 数")
    status: QaMessageStatus = Field(QaMessageStatus.COMPLETED, description="消息状态")


class AiQaMessageCreate(AiQaMessageBase):
    pass


class AiQaMessageRead(CreatedAtRead, AiQaMessageBase):
    id: int


# ---------------------------------------------------------- ai_qa_citation


class AiQaCitationBase(ORMModel):
    message_id: int = Field(description="消息 ID")
    doc_id: int | None = Field(None, description="知识文档 ID")
    chunk_id: int | None = Field(None, description="知识切片 ID")
    source_title: str | None = Field(None, max_length=255, description="引用来源标题快照")
    snippet: str | None = Field(None, description="引用片段快照")
    relevance: Decimal | None = Field(
        None, ge=0, le=1, max_digits=6, decimal_places=4, description="相关度 0~1"
    )


class AiQaCitationCreate(AiQaCitationBase):
    pass


class AiQaCitationRead(CreatedAtRead, AiQaCitationBase):
    id: int


class AiQaSessionDetail(AiQaSessionRead):
    """会话详情：附带消息与引用。"""

    messages: list[dict] = Field(default_factory=list)


__all__ = [
    "AiQaCitationCreate",
    "AiQaCitationRead",
    "AiQaMessageCreate",
    "AiQaMessageRead",
    "AiQaSessionCreate",
    "AiQaSessionDetail",
    "AiQaSessionRead",
    "AiQaSessionUpdate",
    "KnowledgeChunkCreate",
    "KnowledgeChunkRead",
    "KnowledgeChunkUpdate",
    "KnowledgeDocCreate",
    "KnowledgeDocRead",
    "KnowledgeDocUpdate",
]
