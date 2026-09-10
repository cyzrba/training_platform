"""AI 问答知识库 Schema。"""

from sqlmodel import Field, SQLModel

from app.models.knowledge import (
    AiQaCitationBase,
    AiQaMessageBase,
    AiQaSessionBase,
    KnowledgeChunkBase,
    KnowledgeDocBase,
)
from app.schemas.base import CreatedAtRead, SoftDeleteRead, TimestampRead

# ------------------------------------------------------------------ 知识文档


class KnowledgeDocCreate(KnowledgeDocBase):
    pass


class KnowledgeDocUpdate(SQLModel):
    title: str | None = Field(default=None, max_length=255)
    biz_type: str | None = Field(default=None, max_length=30)
    biz_id: int | None = None
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    total_chunks: int | None = Field(default=None, ge=0)


class KnowledgeDocRead(TimestampRead, SoftDeleteRead, KnowledgeDocBase):
    id: int


# ------------------------------------------------------------------ 知识切片


class KnowledgeChunkCreate(KnowledgeChunkBase):
    pass


class KnowledgeChunkUpdate(SQLModel):
    content: str | None = None
    content_hash: str | None = Field(default=None, max_length=64)
    char_count: int | None = Field(default=None, ge=0)
    vector_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=20)


class KnowledgeChunkRead(TimestampRead, KnowledgeChunkBase):
    id: int


# ------------------------------------------------------------------ 问答会话


class AiQaSessionCreate(AiQaSessionBase):
    pass


class AiQaSessionUpdate(SQLModel):
    title: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=20)


class AiQaSessionRead(TimestampRead, AiQaSessionBase):
    id: int


# ------------------------------------------------------------------ 问答消息


class AiQaMessageCreate(AiQaMessageBase):
    pass


class AiQaMessageRead(CreatedAtRead, AiQaMessageBase):
    id: int


# ------------------------------------------------------------------ 引用来源


class AiQaCitationCreate(AiQaCitationBase):
    pass


class AiQaCitationRead(CreatedAtRead, AiQaCitationBase):
    id: int


class AiQaSessionDetail(AiQaSessionRead):
    """会话详情：附带消息（含各自引用）。"""

    messages: list[AiQaMessageRead] = Field(default_factory=list)


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
