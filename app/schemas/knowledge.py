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
    doc_type: str | None = Field(default=None, max_length=20)
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    total_chunks: int | None = Field(default=None, ge=0)


class KnowledgeDocRead(TimestampRead, SoftDeleteRead, KnowledgeDocBase):
    id: int


class KnowledgeDocListItem(KnowledgeDocRead):
    """知识文档列表项：附上来源文件名，列表里不用再查一次文件台账。"""

    original_name: str | None = Field(default=None, description="源文件名")


# ------------------------------------------------------------------ 知识切片


class KnowledgeChunkCreate(KnowledgeChunkBase):
    pass


class KnowledgeChunkUpdate(SQLModel):
    content: str | None = None
    content_hash: str | None = Field(default=None, max_length=64)
    char_count: int | None = Field(default=None, ge=0)
    heading_path: str | None = Field(default=None, max_length=512)
    page_no: int | None = None
    token_count: int | None = None
    vector_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=20)


class KnowledgeChunkRead(TimestampRead, KnowledgeChunkBase):
    id: int


# ------------------------------------------------------------------ 入库与召回


class KnowledgeIngestOut(SQLModel):
    """解析入库结果。"""

    knowledge_doc_id: int = Field(description="知识文档 ID")
    doc_type: str = Field(description="KNOWLEDGE / EVAL_CRITERIA")
    status: str = Field(description="PARSING / READY / FAILED / DISABLED")
    chunk_count: int = Field(default=0, description="切片数")
    chunk_strategy: str | None = Field(default=None, description="切分策略版本")
    parse_error: str | None = Field(default=None, description="失败原因，成功时为 null")


class KnowledgeEmbedOut(SQLModel):
    """向量化结果。"""

    knowledge_doc_id: int = Field(description="知识文档 ID")
    chunk_count: int = Field(description="写入 Milvus 的切片数")
    collection: str = Field(description="Milvus 物理集合名（带版本）")
    model: str = Field(description="embedding 模型")
    truncated: int = Field(default=0, description="因超长被截断的切片数")


class KnowledgeRecallIn(SQLModel):
    """召回测试入参（教师用来看"这句问法能不能检索到对的片段"）。"""

    query: str = Field(min_length=1, max_length=2000, description="查询文本")
    top_k: int | None = Field(
        default=None, ge=1, le=50, description="返回条数，默认用检索配置的 context_top_n"
    )
    use_rerank: bool = Field(default=True, description="是否用重排模型精排")


class KnowledgeRecallHit(SQLModel):
    """一条召回结果。"""

    chunk_id: int
    doc_id: int
    chunk_index: int
    heading_path: str | None = Field(default=None, description="块所属标题路径")
    content: str = Field(description="切片正文（取自 SQLite 真源）")
    score: float = Field(description="混合检索分数（RRF 融合）")
    rerank_score: float | None = Field(default=None, description="重排分数，未重排为 null")


class KnowledgeRecallOut(SQLModel):
    """召回测试结果。"""

    query: str
    doc_ids: list[int] = Field(default_factory=list, description="本次检索限定的文档范围")
    candidate_count: int = Field(default=0, description="混合检索召回条数（重排前）")
    reranked: bool = Field(default=False, description="是否做了重排")
    warnings: list[str] = Field(default_factory=list, description="降级说明，如未装重排依赖")
    hits: list[KnowledgeRecallHit] = Field(default_factory=list)


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
    "KnowledgeDocListItem",
    "KnowledgeDocRead",
    "KnowledgeDocUpdate",
    "KnowledgeEmbedOut",
    "KnowledgeIngestOut",
    "KnowledgeRecallHit",
    "KnowledgeRecallIn",
    "KnowledgeRecallOut",
]
