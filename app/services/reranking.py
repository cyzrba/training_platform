"""BGE-Reranker-v2-m3 重排：cross-encoder 打分，模型同样进程内单例。"""

import inspect
from functools import lru_cache
from typing import Any

from app.core.exceptions import BusinessRuleError
from app.services.embedding import RAG_EXTRA_HINT, resolve_device
from app.services.settings_store import RerankerConfig


def reranker_available() -> bool:
    try:
        import FlagEmbedding  # noqa: F401
    except Exception:
        return False
    return True


@lru_cache(maxsize=4)
def _load(path: str, device: str, use_fp16: bool) -> Any:
    from FlagEmbedding import FlagReranker

    params = inspect.signature(FlagReranker.__init__).parameters
    kwargs: dict[str, Any] = {"use_fp16": use_fp16}
    if "devices" in params:
        kwargs["devices"] = device
    elif "device" in params:
        kwargs["device"] = device
    return FlagReranker(path, **kwargs)


def reset_model_cache() -> None:
    _load.cache_clear()


def score_pairs(config: RerankerConfig, query: str, passages: list[str]) -> list[float]:
    """给 ``(query, passage)`` 打分；分数是 cross-encoder 的 logits，越大越相关。"""
    if not passages:
        return []
    if not reranker_available():
        raise BusinessRuleError(RAG_EXTRA_HINT)
    device = resolve_device(config.device)
    model = _load(config.resolved_path, device, device.startswith("cuda"))
    scores = model.compute_score(
        [[query, passage] for passage in passages],
        batch_size=config.batch_size,
        max_length=config.max_length,
    )
    if isinstance(scores, (int, float)):
        return [float(scores)]
    return [float(item) for item in scores]


__all__ = ["reranker_available", "reset_model_cache", "score_pairs"]
