"""Milvus 连通性与能力自检。

用法：
    uv run python scripts/check_milvus.py                      # 连 http://127.0.0.1:19530
    uv run python scripts/check_milvus.py --uri http://host:19530
    uv run python scripts/check_milvus.py --keep               # 保留测试集合，便于排查

为什么要实测而不是假设：RAG 方案有三条硬依赖取决于 Milvus 版本与部署形态——
① 稀疏向量（BGE-M3 的 lexical weights 直接落库）、② 标量字段过滤（doc_id 白名单 + status=READY）、
③ 集合别名原子切换（换 embedding 模型时建 _v2 再切别名）。三条任何一条不支持，方案就要改。

脚本自建自删临时集合（前缀 ``rag_check_``），不动业务数据。
"""

import argparse
import random
import sys

COLL_A = "rag_check_chunk_v1"
COLL_B = "rag_check_chunk_v2"
ALIAS = "rag_check_chunk"

from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker  # noqa: E402


def build_schema(dim: int):
    """按 RAG 方案的 chunk collection 结构建 schema。"""
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="doc_id", datatype=DataType.INT64)
    schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=20)
    schema.add_field(field_name="chunk_index", datatype=DataType.INT32)
    schema.add_field(field_name="status", datatype=DataType.VARCHAR, max_length=20)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="dense", datatype=DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    return schema


def build_indexes(client: MilvusClient):
    """稠密 HNSW/COSINE + 稀疏倒排/IP，参数取自方案 5.2。"""
    params = client.prepare_index_params()
    params.add_index(
        field_name="dense",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    params.add_index(
        field_name="sparse",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
        params={"drop_ratio_build": 0.2},
    )
    return params


def make_rows(dim: int, rng: random.Random, start_pk: int = 1) -> list[dict]:
    """造几行样本，覆盖两种 doc_type 与 READY / PENDING 两种状态。"""
    rows = []
    for i in range(6):
        rows.append(
            {
                "pk": start_pk + i,
                "doc_id": 1 + i % 3,
                "doc_type": "KNOWLEDGE" if i % 2 == 0 else "EVAL_CRITERIA",
                "chunk_index": i,
                "status": "PENDING" if i == 5 else "READY",
                "text": f"第 {i} 条切片样本，用于验证混合检索。",
                "dense": [rng.random() for _ in range(dim)],
                "sparse": {rng.randrange(100): rng.random() for _ in range(5)},
            }
        )
    return rows


def aliases_of(client: MilvusClient, collection_name: str) -> set[str]:
    """取集合的别名集合。

    注意 pymilvus 3.0 的 ``list_aliases`` 返回的是 dict（``{"aliases": [...], "collection_name": ...}``），
    2.x 时代返回的是 list，这里两种都兼容。
    """
    result = client.list_aliases(collection_name)
    names = result.get("aliases", []) if isinstance(result, dict) else result
    return set(names or [])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--uri", default="http://127.0.0.1:19530", help="Milvus 地址")
    parser.add_argument("--dim", type=int, default=1024, help="稠密向量维度，默认 1024（BGE-M3）")
    parser.add_argument("--keep", action="store_true", help="保留测试集合与别名，不清理")
    args = parser.parse_args()

    rng = random.Random(42)
    checks: list[tuple[str, bool, str]] = []
    client = MilvusClient(uri=args.uri)

    version = client.get_server_version()
    checks.append(("连接与版本", True, f"server={version}"))

    for name in (COLL_A, COLL_B):
        if client.has_collection(name):
            client.drop_collection(name)

    # ① 建集合：稠密 + 稀疏 + 标量字段
    schema = build_schema(args.dim)
    client.create_collection(collection_name=COLL_A, schema=schema, index_params=build_indexes(client))
    fields = {f["name"] for f in client.describe_collection(COLL_A)["fields"]}
    need = {"pk", "doc_id", "doc_type", "chunk_index", "status", "text", "dense", "sparse"}
    checks.append(
        (
            "集合建表（含 SPARSE_FLOAT_VECTOR）",
            need <= fields,
            f"缺 {need - fields}" if need - fields else "字段齐全",
        )
    )

    client.insert(COLL_A, make_rows(args.dim, rng))
    client.flush(COLL_A)
    if hasattr(client, "load_collection"):
        client.load_collection(COLL_A)

    # ② 混合检索 + 标量过滤
    query = {"dense": [rng.random() for _ in range(args.dim)], "sparse": {1: 0.8, 3: 0.5, 7: 0.3}}
    filt = 'doc_id in [1, 2] and status == "READY"'
    reqs = [
        AnnSearchRequest(
            data=[query["dense"]],
            anns_field="dense",
            param={"metric_type": "COSINE", "params": {"ef": 96}},
            limit=10,
            expr=filt,
        ),
        AnnSearchRequest(
            data=[query["sparse"]],
            anns_field="sparse",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=10,
            expr=filt,
        ),
    ]
    hits = client.hybrid_search(
        collection_name=COLL_A,
        reqs=reqs,
        ranker=RRFRanker(60),
        limit=5,
        output_fields=["doc_id", "chunk_index", "status", "doc_type", "text"],
    )
    got = hits[0] if hits else []
    filtered_ok = all(h["entity"]["doc_id"] in (1, 2) and h["entity"]["status"] == "READY" for h in got)
    checks.append(("dense+sparse 混合检索（RRFRanker）", len(got) > 0, f"命中 {len(got)} 条"))
    checks.append(("标量过滤生效（doc_id + status）", len(got) > 0 and filtered_ok, f"过滤表达式 {filt}"))

    # ③ 集合别名：建 _v2 后原子切别名
    client.create_alias(collection_name=COLL_A, alias=ALIAS)
    alias_ok = ALIAS in aliases_of(client, COLL_A)
    client.create_collection(
        collection_name=COLL_B, schema=build_schema(args.dim), index_params=build_indexes(client)
    )
    client.alter_alias(collection_name=COLL_B, alias=ALIAS)
    switched = ALIAS in aliases_of(client, COLL_B) and ALIAS not in aliases_of(client, COLL_A)
    checks.append(("集合别名创建", alias_ok, f"alias={ALIAS} -> {COLL_A}"))
    checks.append(("别名原子切换（换模型重建用）", switched, f"alias={ALIAS} -> {COLL_B}"))

    if not args.keep:
        client.drop_alias(ALIAS)
        for name in (COLL_A, COLL_B):
            client.drop_collection(name)

    print()
    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'[通过]' if ok else '[失败]'} {name.ljust(width)}  {detail}")
    failed = [name for name, ok, _ in checks if not ok]
    print()
    if failed:
        print(f"未通过的检查项：{', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"全部通过（{len(checks)} 项）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
