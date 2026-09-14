"""BGE-M3 向量化：稠密 + 稀疏一次前向拿到。

- 模型实例**进程内单例**（``lru_cache``）：每次请求重新加载权重会慢到不可用；
- dense 与 sparse 一次前向同时产出，不要分两次调用；
- 稀疏权重是 ``{token_id: weight}``，写 Milvus 前由 vector_store 转成对应字段格式；
- FlagEmbedding 在 ``rag`` 可选依赖组里（会连带装 torch），没装时给出明确提示而不是
  抛一个看不懂的 ImportError。
"""

import inspect
from functools import lru_cache
from typing import Any

from app.core.exceptions import BusinessRuleError
from app.services.settings_store import EmbeddingConfig

#: 缺依赖时的统一提示
RAG_EXTRA_HINT = "缺少 RAG 依赖（FlagEmbedding / pymilvus），先执行：uv sync --extra rag"


def rag_available() -> bool:
    """RAG 依赖是否已安装（没装不影响其它功能，只是知识库不可用）。"""
    try:
        import FlagEmbedding  # noqa: F401
    except Exception:
        return False
    return True


def resolve_device(device: str) -> str:
    """``auto`` 时按有没有可用的 CUDA 决定；没有 torch 就退到 CPU。"""
    if device and device != "auto":
        return device
    try:
        import torch
    except Exception:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _build_model(model_path: str, device: str, *, use_fp16: bool, normalize: bool) -> Any:
    """按已装版本的实际参数名传设备（不同版本叫 devices / device）。"""
    from FlagEmbedding import BGEM3FlagModel

    params = inspect.signature(BGEM3FlagModel.__init__).parameters
    kwargs: dict[str, Any] = {"use_fp16": use_fp16, "normalize_embeddings": normalize}
    if "devices" in params:
        kwargs["devices"] = device
    elif "device" in params:
        kwargs["device"] = device
    return BGEM3FlagModel(model_path, **kwargs)


@lru_cache(maxsize=4)
def _load_model(model_path: str, device: str, use_fp16: bool, normalize: bool) -> Any:
    """加载并缓存模型（按 路径 + 设备 + 精度 作键，配置一变自动换实例）。"""
    return _build_model(model_path, device, use_fp16=use_fp16, normalize=normalize)


def get_model(config: EmbeddingConfig) -> Any:
    if not rag_available():
        raise BusinessRuleError(RAG_EXTRA_HINT)
    device = resolve_device(config.device)
    return _load_model(config.resolved_path, device, device.startswith("cuda"), config.normalize)


def reset_model_cache() -> None:
    """换了 embedding 模型或精度后清缓存（配置变更后调用）。"""
    _load_model.cache_clear()


def encode_texts(
    config: EmbeddingConfig,
    texts: list[str],
    *,
    batch_size: int | None = None,
    max_length: int | None = None,
) -> tuple[list[list[float]], list[dict[int, float]]]:
    """批量编码，返回 ``(dense 向量, 稀疏权重)``。"""
    if not texts:
        return [], []
    model = get_model(config)
    output = model.encode(
        list(texts),
        batch_size=batch_size or config.batch_size,
        max_length=max_length or config.max_length,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,  # colbert 多向量先不开，存储开销大
    )
    dense = [[float(value) for value in row] for row in output["dense_vecs"]]
    sparse = [
        {int(token): float(weight) for token, weight in weights.items()}
        for weights in output["lexical_weights"]
    ]
    return dense, sparse


def encode_query(config: EmbeddingConfig, text: str) -> tuple[list[float], dict[int, float]]:
    dense, sparse = encode_texts(config, [text], batch_size=1)
    return dense[0], sparse[0]


def count_tokens(config: EmbeddingConfig, texts: list[str]) -> list[int] | None:
    """用真实 tokenizer 数 token（用于回填 ``knowledge_chunk.token_count``）；失败返回 None。"""
    if not texts:
        return []
    try:
        model = get_model(config)
        encoded = model.tokenizer(list(texts))
        return [len(ids) for ids in encoded["input_ids"]]
    except Exception:  # 分词失败不该让入库失败，token 数只是统计信息
        return None


__all__ = [
    "RAG_EXTRA_HINT",
    "count_tokens",
    "encode_query",
    "encode_texts",
    "get_model",
    "rag_available",
    "reset_model_cache",
    "resolve_device",
]
