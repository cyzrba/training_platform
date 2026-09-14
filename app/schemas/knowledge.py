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
    warnings: list[str] = Field(default_factory=list, description="非致命问题（如向量未重建）")


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


# ------------------------------------------------------------------ 提问与用量


class QaAskIn(SQLModel):
    """提问入参。``student_id`` 一期显式传（SSO 落地后改由登录态覆盖，只改一处依赖）。"""

    student_id: int = Field(description="学生 ID（sys_user.id）")
    question: str = Field(min_length=1, description="本轮问题")
    stream: bool = Field(default=True, description="true 走 SSE 流式；false 一次性返回 JSON")


class QaUsageOut(SQLModel):
    """一次回答的 token 用量。一期只统计不限制。"""

    prompt_tokens: int | None = Field(default=None, description="输入 token（含 system 与历史）")
    completion_tokens: int | None = Field(default=None, description="输出 token")
    estimated: bool = Field(default=False, description="true 表示端点没给 usage，按字符估算")


class QaMessagePage(SQLModel):
    """会话历史消息的一屏。用 ``next_before_id`` 游标往前翻，不用 offset。"""

    items: list[AiQaMessageRead] = Field(default_factory=list, description="按时间正序的消息")
    total: int = Field(default=0, description="该会话的消息总数")
    next_before_id: int | None = Field(
        default=None, description="还有更早的消息时给出下一页游标（当 before_id 再请求一次）"
    )


class QaAskOut(SQLModel):
    """非流式提问结果；字段名与 SSE 的 ``done`` 事件保持同名同义。"""

    message: AiQaMessageRead
    usage: QaUsageOut
    citations: list[AiQaCitationRead] = Field(
        default_factory=list, description="回答引用；一期恒为空，二期接知识库后填"
    )
    warnings: list[str] = Field(default_factory=list, description="降级说明，如未接入知识库检索")
    elapsed_ms: int = Field(default=0, description="从提问到收尾的耗时（毫秒）")


class QaUsageSummary(SQLModel):
    """一个时间范围内的用量汇总。"""

    scope: str = Field(description="today 当日 / recent_Nd 保留期内")
    question_count: int = Field(default=0, description="提问次数（USER 消息数）")
    prompt_tokens: int = Field(default=0, description="输入 token 合计")
    completion_tokens: int = Field(default=0, description="输出 token 合计")
    total_tokens: int = Field(default=0, description="输入 + 输出")


class QaUsageTotal(SQLModel):
    """token 用量统计（只统计，不做限制）。"""

    student_id: int
    retention_days: int = Field(description="历史保留天数，统计口径与它一致")
    today: QaUsageSummary
    recent: QaUsageSummary


__all__ = [
    "AiQaCitationCreate",
    "AiQaCitationRead",
    "AiQaMessageCreate",
    "AiQaMessageRead",
    "AiQaSessionCreate",
    "AiQaSessionDetail",
    "AiQaSessionRead",
    "AiQaSessionUpdate",
    "QaAskIn",
    "QaAskOut",
    "QaMessagePage",
    "QaUsageOut",
    "QaUsageSummary",
    "QaUsageTotal",
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
