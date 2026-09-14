"""AI 问答知识库：知识文档、切片、会话、消息、引用。"""

from decimal import Decimal

from sqlmodel import (
    Field,
    Index,
    SQLModel,
    UniqueConstraint,
    text,
)

from app.models.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin

# ------------------------------------------------------------------ 知识文档


class KnowledgeDocBase(SQLModel):
    title: str = Field(max_length=255, description="知识文档标题")
    doc_type: str = Field(
        default="KNOWLEDGE",
        max_length=20,
        description="KNOWLEDGE 知识问答 / EVAL_CRITERIA 评分标准（评分标准只走后端内部调用）",
    )
    file_asset_id: int | None = Field(default=None, foreign_key="file_asset.id", description="文件 ID")
    source: str = Field(default="UPLOAD", max_length=30, description="来源 UPLOAD 文件上传 / TEXT 手工录入")
    description: str | None = Field(default=None, description="说明/描述")
    status: str = Field(
        default="PARSING",
        max_length=20,
        description="PARSING 解析中 / READY 可用 / FAILED 失败 / DISABLED 停用",
    )
    parse_error: str | None = Field(default=None, description="解析或切片失败原因")
    chunk_strategy: str | None = Field(default=None, max_length=50, description="切分策略版本，换策略时识别旧数据")
    total_chunks: int = Field(default=0, description="切片总数")
    uploaded_by: int | None = Field(default=None, foreign_key="sys_user.id", description="上传人 ID")


class KnowledgeDoc(Base, TimestampMixin, SoftDeleteMixin, KnowledgeDocBase, table=True):
    """RAG 知识库文档（岗位资料、项目评分标准等）。

    归属不在这张表上重复存：评分标准通过 ``file_asset → project_file`` 找到所属项目，
    岗位资料将来加 ``job_file`` 即可（纯增量）。
    """

    __tablename__ = "knowledge_doc"
    __table_args__ = (Index("idx_knowledge_doc_file_asset", "file_asset_id"),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 知识切片


class KnowledgeChunkBase(SQLModel):
    doc_id: int = Field(foreign_key="knowledge_doc.id", description="知识文档 ID")
    chunk_index: int = Field(description="切片序号")
    content: str = Field(description="切片正文")
    content_hash: str | None = Field(default=None, max_length=64, description="内容哈希")
    char_count: int = Field(default=0, description="字符数")
    heading_path: str | None = Field(default=None, max_length=512, description="块所属标题路径，引用展示用")
    page_no: int | None = Field(default=None, description="来源页码（PDF 等）")
    token_count: int | None = Field(default=None, description="实际 token 数，接模型后回填")
    vector_id: str | None = Field(default=None, max_length=64, description="Milvus 主键")
    model_name: str | None = Field(default=None, max_length=100, description="模型名称")
    status: str = Field(
        default="PENDING",
        max_length=20,
        description="PENDING 已切片待入库 / READY 已写入 Milvus / FAILED / DISABLED",
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
    status: str = Field(default="ACTIVE", max_length=20, description="ACTIVE 进行中 / CLOSED 已结束")


class AiQaSession(Base, TimestampMixin, AiQaSessionBase, table=True):
    """学生的 AI 问答会话。"""

    __tablename__ = "ai_qa_session"
    __table_args__ = (Index("idx_qa_session_student", "student_id", text("updated_at DESC")),)

    id: int | None = Field(default=None, primary_key=True)


# ------------------------------------------------------------------ 问答消息


class AiQaMessageBase(SQLModel):
    session_id: int = Field(foreign_key="ai_qa_session.id", description="会话 ID")
    role: str = Field(max_length=20, description="消息角色 USER 用户提问 / ASSISTANT AI 回答")
    content: str = Field(description="消息内容")
    model_name: str | None = Field(default=None, max_length=100, description="模型名称")
    prompt_tokens: int | None = Field(default=None, description="输入 Token 数")
    completion_tokens: int | None = Field(default=None, description="输出 Token 数")
    status: str = Field(default="COMPLETED", max_length=20, description="COMPLETED / FAILED")


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
    snippet: str | None = Field(default=None, description="引用片段快照")
    relevance: Decimal | None = Field(default=None, description="相关度 0~1")


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
