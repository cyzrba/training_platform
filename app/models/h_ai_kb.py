"""H 域 · AI 问答知识库（5 张表）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import TZDateTime
from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class KnowledgeDoc(Base, TimestampMixin, SoftDeleteMixin):
    """RAG 知识库文档（课程资料等）。"""

    __tablename__ = "knowledge_doc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="知识文档标题")
    biz_type: Mapped[str | None] = mapped_column(String(30), comment="JOB/COURSE/SYSTEM")
    biz_id: Mapped[int | None] = mapped_column(BigInteger, comment="关联业务 ID")
    file_asset_id: Mapped[int | None] = mapped_column(ForeignKey("file_asset.id"), comment="文件 ID")
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="'UPLOAD'", comment="UPLOAD/TEXT"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="说明/描述")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="'PARSING'",
        comment="PARSING/READY/FAILED/DISABLED",
    )
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", comment="切片总数")
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), comment="上传人")

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="doc", cascade="all, delete-orphan", order_by="KnowledgeChunk.chunk_index"
    )


class KnowledgeChunk(Base, TimestampMixin):
    """知识文档切片，Milvus 向量与此表 vector_id 对应。"""

    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uk_knowledge_chunk"),
        Index("idx_knowledge_chunk_doc", "doc_id", "chunk_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("knowledge_doc.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="切片序号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片正文")
    content_hash: Mapped[str | None] = mapped_column(String(64), comment="内容哈希")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", comment="字符数")
    vector_id: Mapped[str | None] = mapped_column(String(64), comment="Milvus 主键")
    model_name: Mapped[str | None] = mapped_column(String(100), comment="模型名称")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="'READY'", comment="READY/FAILED/DISABLED"
    )

    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")


class AiQaSession(Base, TimestampMixin):
    """学生的 AI 问答会话。"""

    __tablename__ = "ai_qa_session"
    __table_args__ = (Index("idx_qa_session_student", "student_id", text("updated_at DESC")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), comment="会话标题")
    subject: Mapped[str | None] = mapped_column(String(100), comment="课程/知识范围")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="'ACTIVE'", comment="ACTIVE/CLOSED"
    )

    messages: Mapped[list["AiQaMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AiQaMessage.id"
    )


class AiQaMessage(Base):
    """会话中的问答消息（用户 / 助手）。"""

    __tablename__ = "ai_qa_message"
    __table_args__ = (Index("idx_qa_message_session", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("ai_qa_session.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment="USER/ASSISTANT")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    model_name: Mapped[str | None] = mapped_column(String(100), comment="模型名称")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, comment="输入 Token 数")
    completion_tokens: Mapped[int | None] = mapped_column(Integer, comment="输出 Token 数")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="'COMPLETED'", comment="COMPLETED/FAILED"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )

    session: Mapped[AiQaSession] = relationship(back_populates="messages")
    citations: Mapped[list["AiQaCitation"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class AiQaCitation(Base):
    """回答引用的知识来源，保证回答可信度与可追溯。"""

    __tablename__ = "ai_qa_citation"
    __table_args__ = (Index("idx_qa_citation_message", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("ai_qa_message.id"), nullable=False)
    doc_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_doc.id"), comment="知识文档 ID")
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunk.id"), comment="知识切片 ID")
    source_title: Mapped[str | None] = mapped_column(String(255), comment="引用来源标题快照")
    snippet: Mapped[str | None] = mapped_column(Text, comment="引用片段快照")
    relevance: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), comment="相关度 0~1")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )

    message: Mapped[AiQaMessage] = relationship(back_populates="citations")


__all__ = ["AiQaCitation", "AiQaMessage", "AiQaSession", "KnowledgeChunk", "KnowledgeDoc"]
