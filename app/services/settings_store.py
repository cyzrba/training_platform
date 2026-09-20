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
from app.core.exceptions import BusinessRuleError
from app.crud.account import SystemConfigRepository

#: 进程内缓存时长（秒）
CACHE_TTL_SECONDS = 60

EMBEDDING_KEY = "ai.embedding"
RERANKER_KEY = "ai.reranker"
LLM_KEY = "ai.llm"
#: 默认模型选项 id（与前端 Student/src/config/models.ts 对齐）；它在 ai.llm 里用平铺字段配置
DEFAULT_LLM_MODEL = "deepseek"
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
        # 以上平铺字段 = **默认模型**（DeepSeek）的一套配置；
        # 下面按「模型选项 id」再挂几家，学生端 AI 助教选到谁就连谁。
        # 每项只需写要覆盖的字段（通常就是 base_url / model / api_key），
        # 其余字段（temperature / timeout / max_tokens…）沿用默认那套。
        # id 必须与前端 Student/src/config/models.ts 的选项 id 一致。
        "models": {
            "kimi": {
                "label": "Kimi",
                "provider": "openai-compatible",
                "base_url": "https://api.moonshot.cn/v1",  # 月之暗面（Moonshot）OpenAI 兼容地址
                "model": "kimi-latest",  # 按账号可用模型改，如 kimi-k2-0905-preview
                "api_key": "",
            },
            "mimo": {
                "label": "MiMo",
                "provider": "openai-compatible",
                # MiMo 的 OpenAI 兼容地址与模型名请在拿到账号后填这两项
                "base_url": "",
                "model": "",
                "api_key": "",
            },
        },
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
    """一次调用要用的大模型配置（OpenAI 兼容接口）：换模型 / 换 key 只改配置，不动代码。

    ``key`` 是学生端选项 id（见 ``Student/src/config/models.ts``，如 ``deepseek`` / ``kimi``），
    ``label`` 是给人看的名字；默认模型那一套也用 ``deepseek`` 这个 key。
    """

    provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    timeout: int
    max_tokens: int
    key: str = DEFAULT_LLM_MODEL
    label: str = ""

    @property
    def configured(self) -> bool:
        """没填 api key 时调用方应先给出可读的提示，而不是等 SDK 抛鉴权错误。"""
        return bool(self.api_key.strip())

    @property
    def has_endpoint(self) -> bool:
        """接口地址与模型名都填了才谈得上调用（新增一家模型时先配这两项）。"""
        return bool(self.base_url.strip()) and bool(self.model.strip())

    @property
    def display_name(self) -> str:
        """报错文案里用的名字：优先 label，其次 key。"""
        return self.label.strip() or self.key


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


def _llm_models(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把默认骨架与表里的 ``models`` 逐家合并：表里没写到的家仍然保留骨架（便于看到该配什么）。"""
    skeleton = DEFAULTS[LLM_KEY].get("models") or {}
    merged: dict[str, dict[str, Any]] = {key: dict(value) for key, value in skeleton.items()}
    stored = raw.get("models")
    if isinstance(stored, dict):
        for key, value in stored.items():
            if isinstance(value, dict):
                merged[str(key)] = {**merged.get(str(key), {}), **value}
    return merged


async def get_llm_config(session: AsyncSession, model: str | None = None) -> LLMConfig:
    """取某个模型选项要用的配置。

    ``ai.llm`` 的平铺字段是**默认模型**（``DEFAULT_LLM_MODEL``，当前是 DeepSeek）那一套；
    ``ai.llm.models.<key>`` 里按学生端选项 id 再挂别家（kimi / mimo…）。

    **不继承的字段**：``base_url`` / ``model`` / ``api_key`` —— 这几项每家必须自己写，
    避免"Kimi 没填 key 却悄悄用 DeepSeek 的 key"这种错；``provider`` / ``temperature`` /
    ``timeout`` / ``max_tokens`` 沿用默认那套。

    ``model`` 给不认识的值时报 422（而不是悄悄回落默认模型），错误信息里列出可选值。
    """
    raw = await _raw(session, LLM_KEY)
    key = (model or "").strip() or DEFAULT_LLM_MODEL
    if key == DEFAULT_LLM_MODEL:
        return LLMConfig(
            provider=str(raw.get("provider", "openai-compatible")),
            base_url=str(raw.get("base_url", "https://api.deepseek.com/v1")),
            model=str(raw.get("model", "deepseek-v4-flash")),
            api_key=str(raw.get("api_key") or ""),
            temperature=float(raw.get("temperature", 0.2)),
            timeout=int(raw.get("timeout", 60)),
            max_tokens=int(raw.get("max_tokens", 4096)),
            key=key,
            label=str(raw.get("label") or "DeepSeek"),
        )

    models = _llm_models(raw)
    extra = models.get(key)
    if extra is None:
        options = "、".join(sorted({DEFAULT_LLM_MODEL, *models}))
        raise BusinessRuleError(f"没有这个模型选项「{key}」，现在可选：{options}")
    return LLMConfig(
        provider=str(extra.get("provider") or raw.get("provider", "openai-compatible")),
        base_url=str(extra.get("base_url") or ""),
        model=str(extra.get("model") or ""),
        api_key=str(extra.get("api_key") or ""),
        temperature=float(extra.get("temperature", raw.get("temperature", 0.2))),
        timeout=int(extra.get("timeout", raw.get("timeout", 60))),
        max_tokens=int(extra.get("max_tokens", raw.get("max_tokens", 4096))),
        key=key,
        label=str(extra.get("label") or key),
    )


async def list_llm_models(session: AsyncSession) -> list[dict[str, Any]]:
    """列出可选模型（默认模型 + ``ai.llm.models`` 里的各家），带"配没配齐"的状态。

    给接口/前端做"哪些模型现在能用"的展示用；api_key 一律不返回，只返回是否已配置。
    """
    raw = await _raw(session, LLM_KEY)
    keys = [DEFAULT_LLM_MODEL, *_llm_models(raw).keys()]
    items: list[dict[str, Any]] = []
    for key in dict.fromkeys(keys):
        config = await get_llm_config(session, key)
        items.append(
            {
                "key": key,
                "label": config.display_name,
                "model": config.model,
                "base_url": config.base_url,
                "has_endpoint": config.has_endpoint,
                "configured": config.configured,
            }
        )
    return items


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
    "DEFAULT_LLM_MODEL",
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
    "list_llm_models",
]
