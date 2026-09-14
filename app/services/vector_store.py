"""Milvus 写入与混合检索（pymilvus 原生）。

不用 LangChain 的 Milvus 集成做混合检索：dense + sparse + RRFRanker + 标量过滤
用原生 API 更可控。集合物理名带版本（``knowledge_chunk_v1``），对外只用别名
``knowledge_chunk``——换 embedding 模型时建 ``_v2``，追平数据后原子切别名，不中断服务。

字段设计见方案 5.2：主键直接用 ``knowledge_chunk.id``（天然幂等），
``doc_id`` / ``doc_type`` / ``status`` 冗余一份，检索阶段做标量过滤（检索后再筛会掉召回）。
"""

from functools import lru_cache
from typing import Any

from app.core.exceptions import BusinessRuleError
from app.services.embedding import RAG_EXTRA_HINT
from app.services.settings_store import MilvusConfig, RetrievalConfig

TEXT_MAX_LENGTH = 65535


def milvus_available() -> bool:
    try:
        import pymilvus  # noqa: F401
    except Exception:
        return False
    return True


@lru_cache(maxsize=4)
def get_client(uri: str) -> Any:
    """按地址缓存客户端（一个进程一个连接就够）。"""
    if not milvus_available():
        raise BusinessRuleError(RAG_EXTRA_HINT)
    from pymilvus import MilvusClient

    return MilvusClient(uri=uri)


def reset_client_cache() -> None:
    get_client.cache_clear()


def _schema(client: Any, dim: int) -> Any:
    from pymilvus import DataType

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="doc_id", datatype=DataType.INT64)
    schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=20)
    schema.add_field(field_name="chunk_index", datatype=DataType.INT32)
    schema.add_field(field_name="status", datatype=DataType.VARCHAR, max_length=20)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH)
    schema.add_field(field_name="dense", datatype=DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    return schema


def _index_params(client: Any, config: MilvusConfig) -> Any:
    params = client.prepare_index_params()
    params.add_index(
        field_name="dense",
        index_type=config.index,
        metric_type=config.metric,
        params={"M": 16, "efConstruction": 200},
    )
    params.add_index(
        field_name="sparse",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
        params={"drop_ratio_build": 0.2},
    )
    return params


def ensure_collection(client: Any, config: MilvusConfig, dim: int) -> str:
    """确保物理集合存在并挂上别名，返回物理集合名。"""
    name = config.collection_name
    if not client.has_collection(name):
        client.create_collection(
            collection_name=name,
            schema=_schema(client, dim),
            index_params=_index_params(client, config),
            consistency_level=config.consistency,
        )
    aliases = client.list_aliases()
    existing = set(aliases.get("aliases", []) if isinstance(aliases, dict) else aliases or [])
    if config.collection_alias not in existing:
        client.create_alias(collection_name=name, alias=config.collection_alias)
    return name


def upsert_chunks(client: Any, config: MilvusConfig, rows: list[dict[str, Any]], *, dim: int) -> int:
    """按主键 upsert，重复入库幂等。"""
    if not rows:
        return 0
    ensure_collection(client, config, dim)
    client.upsert(collection_name=config.collection_name, data=rows)
    # 刚写进去的数据 flush 之后才保证能被检索到，否则"入库完立刻召回"会返回空
    client.flush(config.collection_name)
    return len(rows)


def delete_by_doc(client: Any, config: MilvusConfig, doc_id: int) -> None:
    """删掉某文档的全部向量（重切片前先清旧数据）。"""
    if not client.has_collection(config.collection_name):
        return
    try:
        client.delete(collection_name=config.collection_name, filter=f"doc_id == {int(doc_id)}")
        # 与写入同理：删除也要 flush，否则紧接着的查询还会看到被删的行
        client.flush(config.collection_name)
    except Exception:  # 没数据 / 集合为空都不该让主流程失败
        return


def drop_collection(client: Any, config: MilvusConfig, *, keep_alias: bool = False) -> None:
    """删掉物理集合。

    Milvus 不允许删除"还挂着别名"的集合，所以默认先把别名摘掉再删；重建或清理测试集合
    都要走这里，否则会拿到一个很难看懂的 1100 报错。
    """
    if not client.has_collection(config.collection_name):
        return
    if not keep_alias:
        try:
            aliases = client.list_aliases(config.collection_name)
            names = aliases.get("aliases", []) if isinstance(aliases, dict) else aliases
            for name in names or []:
                client.drop_alias(name)
        except Exception:  # 没别名 / 查询失败都不该挡住删除
            pass
    client.drop_collection(config.collection_name)


def build_filter(*, doc_ids: list[int] | None = None, doc_type: str | None = None) -> str:
    """组装标量过滤表达式：可见范围与 doc_type 必须在检索阶段过滤。

    注意 ``doc_ids=None`` 与 ``doc_ids=[]`` 语义不同：``None`` = 不限制（仅内部调用），
    空列表 = 一份可见文档都没有。空列表由 :func:`hybrid_search` 提前拦掉，
    绝不能退化成"不过滤"——那会让可见范围内没有文档的调用方搜到全库内容
    （学生问答链路一旦这样就是评分标准泄露）。
    """
    clauses = ['status == "READY"']
    if doc_ids:
        joined = ", ".join(str(int(item)) for item in doc_ids)
        clauses.append(f"doc_id in [{joined}]")
    if doc_type:
        clauses.append(f'doc_type == "{doc_type}"')
    return " and ".join(clauses)


def hybrid_search(
    client: Any,
    config: MilvusConfig,
    retrieval: RetrievalConfig,
    *,
    dense: list[float],
    sparse: dict[int, float],
    doc_ids: list[int] | None = None,
    doc_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """dense + sparse 双路召回，RRF 融合；返回 ``[{chunk_id, doc_id, chunk_index, doc_type, score}]``。"""
    from pymilvus import AnnSearchRequest, RRFRanker

    if not client.has_collection(config.collection_name):
        return []
    if doc_ids is not None and not doc_ids:
        # 显式限定了可见范围但范围为空 → 没有可检索内容，直接返回空
        return []
    expr = build_filter(doc_ids=doc_ids, doc_type=doc_type)
    top_k = limit or retrieval.retrieve_top_k
    requests = [
        AnnSearchRequest(
            data=[dense],
            anns_field="dense",
            param={"metric_type": config.metric, "params": {"ef": 96}},
            limit=top_k,
            expr=expr,
        ),
        AnnSearchRequest(
            data=[sparse],
            anns_field="sparse",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=top_k,
            expr=expr,
        ),
    ]
    results = client.hybrid_search(
        collection_name=config.collection_name,
        reqs=requests,
        ranker=RRFRanker(retrieval.rrf_k),
        limit=top_k,
        output_fields=["doc_id", "doc_type", "chunk_index", "text"],
    )
    hits: list[dict[str, Any]] = []
    for hit in results[0] if results else []:
        entity = _entity_of(hit)
        chunk_id = _primary_key(hit, entity)
        if chunk_id is None:
            continue
        hits.append(
            {
                "chunk_id": chunk_id,
                "doc_id": int(entity.get("doc_id") or 0),
                "doc_type": entity.get("doc_type"),
                "chunk_index": int(entity.get("chunk_index") or 0),
                "text": entity.get("text") or "",
                "score": float(_value_of(hit, "distance") or 0.0),
            }
        )
    return hits


def _value_of(hit: Any, key: str) -> Any:
    """命中对象在不同版本里可能是 dict 也可能是对象，取值统一走这里。"""
    if hasattr(hit, "get"):
        return hit.get(key)
    return getattr(hit, key, None)


def _primary_key(hit: Any, entity: dict[str, Any]) -> int | None:
    """主键在 pymilvus 2.x 叫 ``id``、3.x 叫 ``pk``，两处都取一遍。"""
    for source in (hit, entity):
        for key in ("pk", "id"):
            value = _value_of(source, key)
            if value is not None:
                return int(value)
    return None


def _entity_of(hit: Any) -> dict[str, Any]:
    """pymilvus 不同版本命中结构略有差异，统一取 entity 字典。"""
    entity = _value_of(hit, "entity")
    if entity is not None and hasattr(entity, "keys"):
        return {str(key): value for key, value in dict(entity).items()}
    if hasattr(hit, "keys"):
        return {str(key): value for key, value in dict(hit).items() if key != "entity"}
    return {}


def describe(client: Any, config: MilvusConfig) -> dict[str, Any]:
    """集合概况（健康检查与召回测试用）。"""
    exists = client.has_collection(config.collection_name)
    info: dict[str, Any] = {
        "uri": config.uri,
        "collection": config.collection_name,
        "alias": config.collection_alias,
        "exists": exists,
    }
    if exists:
        # stats 的 row_count 会把"已删除但还没压缩（compact）"的行也算进去，
        # 删除/重嵌之后会明显虚高，所以另取一次服务端 count(*) 作为真实行数。
        try:
            stats = client.get_collection_stats(config.collection_name)
            info["stats_row_count"] = int(stats.get("row_count", 0))
        except Exception:
            info["stats_row_count"] = None
        try:
            counted = client.query(
                collection_name=config.collection_name, filter="", output_fields=["count(*)"]
            )
            info["row_count"] = int(counted[0]["count(*)"]) if counted else 0
        except Exception:
            info["row_count"] = info.get("stats_row_count")
        try:
            aliases = client.list_aliases(config.collection_name)
            names = aliases.get("aliases", []) if isinstance(aliases, dict) else aliases
            info["aliases"] = list(names or [])
        except Exception:
            info["aliases"] = []
    return info


__all__ = [
    "TEXT_MAX_LENGTH",
    "build_filter",
    "delete_by_doc",
    "describe",
    "drop_collection",
    "ensure_collection",
    "get_client",
    "hybrid_search",
    "milvus_available",
    "reset_client_cache",
    "upsert_chunks",
]
