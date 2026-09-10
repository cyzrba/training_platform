"""AI 问答知识库：知识文档、切片、会话、消息、引用。"""

from decimal import Decimal

from sqlmodel import (
    BigInteger,
    Field,
    Index,
    Numeric,
    SQLModel,
    Text,
    UniqueConstraint,
    text,
)

from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin

# ------------------------------------------------------------------ 知识文档


class KnowledgeDocBase(SQLModel):
    title: str = Field(max_length=255, description="知识文档标题")
    biz_type: str | None = Field(
        default=None, max_length=30, description="关联对象类型 JOB 岗位 / COURSE 课程 / SYSTEM 系统"
    )
    biz_id: int | None = Field(default=None, sa_type=BigInteger, description="关联业务 ID")
    file_asset_id: int | None = Field(default=None, foreign_key="file_asset.id", description="文件 ID")
    source: str = Field(
        default="UPLOAD",
        max_length=30,
        description="来源 UPLOAD 文件上传 / TEXT 手工录入",
        sa_column_kwargs={"server_default": text("'UPLOAD'")},
    )
    description: str | None = Field(default=None, sa_type=Text, description="说明/描述")
    status: str = Field(
        default="PARSING",
        max_length=20,
        description="PARSING 解析中 / READY 可用 / FAILED 失败 / DISABLED 停用",
        sa_column_kwargs={"server_default": text("'PARSING'")},
    )
    total_chunks: int = Field(
        default=0, description="切片总数", sa_column_kwargs={"server_default": text("0")}
    )
    uploaded_by: int | None = Field(default=None, foreign_key="sys_user.id", description="上传人 ID")


class KnowledgeDoc(Base, TimestampMixin, SoftDeleteMixin, KnowledgeDocBase, table=True):
    """RAG 知识库文档（课程资料等）。"""

    __tablename__ = "knowledge_doc"

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 知识切片


class KnowledgeChunkBase(SQLModel):
    doc_id: int = Field(foreign_key="knowledge_doc.id", description="知识文档 ID")
    chunk_index: int = Field(description="切片序号")
    content: str = Field(sa_type=Text, description="切片正文")
    content_hash: str | None = Field(default=None, max_length=64, description="内容哈希")
    char_count: int = Field(default=0, description="字符数", sa_column_kwargs={"server_default": text("0")})
    vector_id: str | None = Field(default=None, max_length=64, description="Milvus 主键")
    model_name: str | None = Field(default=None, max_length=100, description="模型名称")
    status: str = Field(
        default="READY",
        max_length=20,
        description="READY / FAILED / DISABLED",
        sa_column_kwargs={"server_default": text("'READY'")},
    )


class KnowledgeChunk(Base, TimestampMixin, KnowledgeChunkBase, table=True):
    """知识文档切片，向量库 Milvus 中的向量与此表 vector_id 对应。"""

    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uk_knowledge_chunk"),
        Index("idx_knowledge_chunk_doc", "doc_id", "chunk_index"),
    )

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 问答会话


class AiQaSessionBase(SQLModel):
    student_id: int = Field(foreign_key="sys_user.id", description="学生 ID")
    title: str | None = Field(default=None, max_length=255, description="会话标题")
    subject: str | None = Field(default=None, max_length=100, description="课程/知识范围")
    status: str = Field(
        default="ACTIVE",
        max_length=20,
        description="ACTIVE 进行中 / CLOSED 已结束",
        sa_column_kwargs={"server_default": text("'ACTIVE'")},
    )


class AiQaSession(Base, TimestampMixin, AiQaSessionBase, table=True):
    """学生的 AI 问答会话。"""

    __tablename__ = "ai_qa_session"
    __table_args__ = (Index("idx_qa_session_student", "student_id", text("updated_at DESC")),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 问答消息


class AiQaMessageBase(SQLModel):
    session_id: int = Field(foreign_key="ai_qa_session.id", description="会话 ID")
    role: str = Field(max_length=20, description="消息角色 USER 用户提问 / ASSISTANT AI 回答")
    content: str = Field(sa_type=Text, description="消息内容")
    model_name: str | None = Field(default=None, max_length=100, description="模型名称")
    prompt_tokens: int | None = Field(default=None, description="输入 Token 数")
    completion_tokens: int | None = Field(default=None, description="输出 Token 数")
    status: str = Field(
        default="COMPLETED",
        max_length=20,
        description="COMPLETED / FAILED",
        sa_column_kwargs={"server_default": text("'COMPLETED'")},
    )


class AiQaMessage(Base, CreatedAtMixin, AiQaMessageBase, table=True):
    """会话中的问答消息（用户 / 助手）。"""

    __tablename__ = "ai_qa_message"
    __table_args__ = (Index("idx_qa_message_session", "session_id", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 引用来源


class AiQaCitationBase(SQLModel):
    message_id: int = Field(foreign_key="ai_qa_message.id", description="消息 ID")
    doc_id: int | None = Field(default=None, foreign_key="knowledge_doc.id", description="知识文档 ID")
    chunk_id: int | None = Field(default=None, foreign_key="knowledge_chunk.id", description="知识切片 ID")
    source_title: str | None = Field(default=None, max_length=255, description="引用来源标题快照")
    snippet: str | None = Field(default=None, sa_type=Text, description="引用片段快照")
    relevance: Decimal | None = Field(default=None, sa_type=Numeric(6, 4), description="相关度 0~1")


class AiQaCitation(Base, CreatedAtMixin, AiQaCitationBase, table=True):
    """回答引用的知识来源，保证回答可信度与可追溯。"""

    __tablename__ = "ai_qa_citation"
    __table_args__ = (Index("idx_qa_citation_message", "message_id"),)

    id: int | None = Field(default=None, primary_key=True)


__all__ = [
    "AiQaCitation",
    "AiQaCitationBase",
    "AiQaMessage",
    "AiQaMessageBase",
    "AiQaSession",
    "AiQaSessionBase",
    "KnowledgeChunk",
    "KnowledgeChunkBase",
    "KnowledgeDoc",
    "KnowledgeDocBase",
]
