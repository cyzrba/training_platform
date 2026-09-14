"""运行时可改配置的读取层。

模型、向量库、检索参数存在 ``system_config`` 表里（``.env`` 只放引导性配置），业务代码
只调这里带类型的取值函数，不直接碰 ``config_value`` 字典——键名改了只需要改这一处。

读一次缓存 60 秒：改完配置调 :func:`invalidate` 立刻生效（``/api/system-configs`` 的写接口
负责调）。配置全部来自表，所以没种子的库也能跑：字段缺失时回落到 :data:`DEFAULTS`。
"""

import time
from dataclasses import dataclass
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import BASE_DIR
from app.crud.account import SystemConfigRepository

#: 进程内缓存时长（秒）
CACHE_TTL_SECONDS = 60

EMBEDDING_KEY = "ai.embedding"
RERANKER_KEY = "ai.reranker"
LLM_KEY = "ai.llm"
MILVUS_KEY = "rag.vector_store"
RETRIEVAL_KEY = "rag.retrieval"
QA_KEY = "ai.qa"

#: 配置键 -> 默认值。种子数据（app/db/seed.py）与这里保持一致，表里没有也能跑
DEFAULTS: dict[str, dict[str, Any]] = {
    EMBEDDING_KEY: {
        "provider": "flagembedding",
        "model": "BAAI/bge-m3",
        "model_path": "",  # 留空则优先用 <项目根>/models/<模型名>，没有才去网上拉
        "device": "auto",  # auto / cuda / cpu
        "batch_size": 32,
        "max_length": 8192,
        "normalize": True,
        "dim": 1024,
        "version": "bge-m3-v1",  # 换模型必须换版本，Milvus 集合物理名带版本
    },
    RERANKER_KEY: {
        "provider": "flagembedding",
        "model": "BAAI/bge-reranker-v2-m3",
        "model_path": "",
        "device": "auto",
        "batch_size": 16,
        "max_length": 1024,
    },
    LLM_KEY: {
        "provider": "openai-compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "api_key": "",  # 只存在这里；接口读取一律掩码（app/core/secrets.py）
        "temperature": 0.2,
        "timeout": 60,
        "max_tokens": 4096,
    },
    MILVUS_KEY: {
        "provider": "milvus",
        "uri": "http://127.0.0.1:19530",
        "collection_alias": "knowledge_chunk",
        "collection_version": "v1",
        "metric": "COSINE",
        "index": "HNSW",
        "consistency": "Bounded",
    },
    RETRIEVAL_KEY: {
        "retrieve_top_k": 50,
        "rerank_top_k": 30,
        "context_top_n": 6,
        "score_threshold": 0.3,
        "rrf_k": 60,
        # 评分标准喂给大模型的总字符预算。评分场景要的是"每个维度都有依据"，
        # 按条数截断会静默丢掉排在后面的维度，所以这里用字符预算（可随模型上下文调整）。
        "criteria_max_chars": 60000,
        # 长标准按关卡分维度召回时，每个维度保底取几片
        "criteria_top_k_per_dimension": 4,
    },
    QA_KEY: {
        # 留空则用 app/services/qa.py 里的内置提示词；这里只在需要覆盖时填
        "system_prompt": "",
        # 上下文窗口：最近 3 轮（1 轮 = 1 问 + 1 答），当前这一问不计入
        "history_rounds": 3,
        # 字符双保险：单轮贴了长文时按"轮"丢弃，不是按条截
        "history_max_chars": 6000,
        # 历史保留天数：查询层过滤 + scripts/prune_qa_history.py 定时清理
        "history_retention_days": 7,
        "max_question_chars": 2000,
        "temperature": 0.3,
        "timeout": 60,
        # 一期 none（不检索，引用恒为空）/ 二期 knowledge（接 Milvus）
        "context_provider": "none",
        "context_top_n": 6,
        "score_threshold": 0.3,
        "rate_limit_per_minute": 10,
    },
}

#: 配置键 -> (过期时间, 原始配置字典)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    model_path: str
    device: str
    batch_size: int
    max_length: int
    normalize: bool
    dim: int
    version: str

    @property
    def resolved_path(self) -> str:
        """优先用项目内 ``models/<模型名>``，其次用配置里的路径，最后回落到 HuggingFace 仓库名。"""
        if self.model_path:
            return self.model_path
        local = BASE_DIR / "models" / self.model.split("/")[-1]
        return str(local) if local.is_dir() else self.model


@dataclass(frozen=True)
class RerankerConfig:
    provider: str
    model: str
    model_path: str
    device: str
    batch_size: int
    max_length: int

    @property
    def resolved_path(self) -> str:
        if self.model_path:
            return self.model_path
        local = BASE_DIR / "models" / self.model.split("/")[-1]
        return str(local) if local.is_dir() else self.model


@dataclass(frozen=True)
class MilvusConfig:
    provider: str
    uri: str
    collection_alias: str
    collection_version: str
    metric: str
    index: str
    consistency: str

    @property
    def collection_name(self) -> str:
        """物理集合名带版本，对外只用别名，换模型时建 ``_v2`` 追平后再切别名。"""
        return f"{self.collection_alias}_{self.collection_version}"


@dataclass(frozen=True)
class LLMConfig:
    """主模型（OpenAI 兼容接口）配置：换模型 / 换 key 只改配置，不动代码。"""

    provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    timeout: int
    max_tokens: int

    @property
    def configured(self) -> bool:
        """没填 api key 时调用方应先给出可读的提示，而不是等 SDK 抛鉴权错误。"""
        return bool(self.api_key.strip())


@dataclass(frozen=True)
class RetrievalConfig:
    retrieve_top_k: int
    rerank_top_k: int
    context_top_n: int
    score_threshold: float
    rrf_k: int
    criteria_max_chars: int
    criteria_top_k_per_dimension: int


@dataclass(frozen=True)
class QaConfig:
    """AI 问答的运行参数（上下文窗口、保留期、频控）。模型与 key 仍复用 ai.llm。"""

    system_prompt: str
    history_rounds: int
    history_max_chars: int
    history_retention_days: int
    max_question_chars: int
    temperature: float
    timeout: int
    context_provider: str
    context_top_n: int
    score_threshold: float
    rate_limit_per_minute: int


def invalidate(key: str | None = None) -> None:
    """清缓存：不传 key 清全部。改配置、改检索参数后必须调。"""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


async def _raw(session: AsyncSession, key: str) -> dict[str, Any]:
    """读原始配置字典：表里有就用表里的覆盖默认值，读一次缓存 60 秒。"""
    cached = _cache.get(key)
    now_monotonic = time.monotonic()
    if cached is not None and cached[0] > now_monotonic:
        return cached[1]

    merged = dict(DEFAULTS.get(key, {}))
    row = await SystemConfigRepository(session).by_key(key)
    if row is not None and isinstance(row.config_value, dict):
        merged.update(row.config_value)
    _cache[key] = (now_monotonic + CACHE_TTL_SECONDS, merged)
    return merged


async def get_embedding_config(session: AsyncSession) -> EmbeddingConfig:
    raw = await _raw(session, EMBEDDING_KEY)
    return EmbeddingConfig(
        provider=str(raw.get("provider", "flagembedding")),
        model=str(raw.get("model", "BAAI/bge-m3")),
        model_path=str(raw.get("model_path") or ""),
        device=str(raw.get("device", "auto")),
        batch_size=int(raw.get("batch_size", 32)),
        max_length=int(raw.get("max_length", 8192)),
        normalize=bool(raw.get("normalize", True)),
        dim=int(raw.get("dim", 1024)),
        version=str(raw.get("version", "bge-m3-v1")),
    )


async def get_reranker_config(session: AsyncSession) -> RerankerConfig:
    raw = await _raw(session, RERANKER_KEY)
    return RerankerConfig(
        provider=str(raw.get("provider", "flagembedding")),
        model=str(raw.get("model", "BAAI/bge-reranker-v2-m3")),
        model_path=str(raw.get("model_path") or ""),
        device=str(raw.get("device", "auto")),
        batch_size=int(raw.get("batch_size", 16)),
        max_length=int(raw.get("max_length", 1024)),
    )


async def get_milvus_config(session: AsyncSession) -> MilvusConfig:
    raw = await _raw(session, MILVUS_KEY)
    return MilvusConfig(
        provider=str(raw.get("provider", "milvus")),
        uri=str(raw.get("uri", "http://127.0.0.1:19530")),
        collection_alias=str(raw.get("collection_alias", "knowledge_chunk")),
        collection_version=str(raw.get("collection_version", "v1")),
        metric=str(raw.get("metric", "COSINE")),
        index=str(raw.get("index", "HNSW")),
        consistency=str(raw.get("consistency", "Bounded")),
    )


async def get_llm_config(session: AsyncSession) -> LLMConfig:
    raw = await _raw(session, LLM_KEY)
    return LLMConfig(
        provider=str(raw.get("provider", "openai-compatible")),
        base_url=str(raw.get("base_url", "https://api.deepseek.com/v1")),
        model=str(raw.get("model", "deepseek-v4-flash")),
        api_key=str(raw.get("api_key") or ""),
        temperature=float(raw.get("temperature", 0.2)),
        timeout=int(raw.get("timeout", 60)),
        max_tokens=int(raw.get("max_tokens", 4096)),
    )


async def get_retrieval_config(session: AsyncSession) -> RetrievalConfig:
    raw = await _raw(session, RETRIEVAL_KEY)
    return RetrievalConfig(
        retrieve_top_k=int(raw.get("retrieve_top_k", 50)),
        rerank_top_k=int(raw.get("rerank_top_k", 30)),
        context_top_n=int(raw.get("context_top_n", 6)),
        score_threshold=float(raw.get("score_threshold", 0.3)),
        rrf_k=int(raw.get("rrf_k", 60)),
        criteria_max_chars=int(raw.get("criteria_max_chars", 60000)),
        criteria_top_k_per_dimension=int(raw.get("criteria_top_k_per_dimension", 4)),
    )


async def get_qa_config(session: AsyncSession) -> QaConfig:
    raw = await _raw(session, QA_KEY)
    return QaConfig(
        system_prompt=str(raw.get("system_prompt") or ""),
        history_rounds=max(1, int(raw.get("history_rounds", 3))),
        history_max_chars=max(1, int(raw.get("history_max_chars", 6000))),
        history_retention_days=max(1, int(raw.get("history_retention_days", 7))),
        max_question_chars=max(1, int(raw.get("max_question_chars", 2000))),
        temperature=float(raw.get("temperature", 0.3)),
        timeout=int(raw.get("timeout", 60)),
        context_provider=str(raw.get("context_provider", "none")),
        context_top_n=int(raw.get("context_top_n", 6)),
        score_threshold=float(raw.get("score_threshold", 0.3)),
        rate_limit_per_minute=max(0, int(raw.get("rate_limit_per_minute", 10))),
    )


__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULTS",
    "LLM_KEY",
    "EmbeddingConfig",
    "LLMConfig",
    "MilvusConfig",
    "QA_KEY",
    "QaConfig",
    "RerankerConfig",
    "RetrievalConfig",
    "get_embedding_config",
    "get_llm_config",
    "get_milvus_config",
    "get_qa_config",
    "get_reranker_config",
    "get_retrieval_config",
    "invalidate",
]
