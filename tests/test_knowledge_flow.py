"""评分标准入库与召回测试。

分两层：入库与切片只依赖 MinIO（存储层），任何环境都能跑；向量化与召回还要
Milvus + rag 依赖 + 本地模型权重，缺任何一样就跳过——这样 CI 里没 GPU 也不会红，
本地开发机（MinIO + Milvus + models/）会真跑一遍。
"""

import socket

import httpx
import pytest

from app.core.config import BASE_DIR
from app.services import settings_store, vector_store
from app.services.settings_store import MilvusConfig

# 测试用独立集合，不和开发集合 knowledge_chunk_v1 混在一起
TEST_COLLECTION_ALIAS = "knowledge_chunk_check"

CRITERIA_MD = """# 实训报告评分标准

## 一、需求分析（20 分）

需求描述完整、边界清晰得 20 分；缺少关键约束每处扣 5 分。

## 二、方案设计（30 分）

系统架构合理、技术选型有依据得 30 分；架构描述含糊扣 10 分。

## 三、数据处理（20 分）

数据集划分比例、清洗步骤说明清楚得 20 分；未说明划分比例扣 10 分。
"""


def _milvus_reachable(uri: str) -> bool:
    host_port = uri.split("//")[-1].split("/")[0].split(":")
    host = host_port[0]
    port = int(host_port[1]) if len(host_port) > 1 else 19530
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _rag_ready() -> bool:
    return (
        vector_store.milvus_available()
        and (BASE_DIR / "models" / "bge-m3").is_dir()
        and (BASE_DIR / "models" / "bge-reranker-v2-m3").is_dir()
        and _milvus_reachable("http://127.0.0.1:19530")
    )


def _test_milvus_config() -> MilvusConfig:
    return MilvusConfig(
        provider="milvus",
        uri="http://127.0.0.1:19530",
        collection_alias=TEST_COLLECTION_ALIAS,
        collection_version="v1",
        metric="COSINE",
        index="HNSW",
        consistency="Bounded",
    )


async def _use_test_collection(monkeypatch: pytest.MonkeyPatch) -> MilvusConfig:
    """把 Milvus 配置指向测试集合（不污染开发集合）。"""
    config = _test_milvus_config()

    async def _fake_config(_session) -> MilvusConfig:
        return config

    monkeypatch.setattr(settings_store, "get_milvus_config", _fake_config)
    client_sdk = vector_store.get_client(config.uri)
    vector_store.drop_collection(client_sdk, config)
    return config


async def _create_project(client: httpx.AsyncClient, name: str = "评分标准实训") -> dict:
    return (await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})).json()


async def _upload_criteria(
    client: httpx.AsyncClient,
    project_id: int,
    *,
    filename: str = "评分标准.md",
    content: bytes = CRITERIA_MD.encode(),
) -> dict:
    return (
        await client.post(
            f"/api/projects/{project_id}/files/upload",
            files={"file": (filename, content, "text/markdown")},
            data={"file_kind": "SCORING_CRITERIA", "title": "实训报告评分标准"},
        )
    ).json()


# ------------------------------------------------------------- 入库与切片


@pytest.mark.asyncio
async def test_upload_criteria_creates_doc_and_chunks(client: httpx.AsyncClient) -> None:
    """上传评分标准 → 整份作为一块。

    评分标准是"整份对照着用"的文档，按标题拆成十几片反而会在超出上下文预算后
    让后面的关卡拿不到条款，所以走 ``whole_document`` 策略（见 splitting.py）。
    """
    project = await _create_project(client)
    uploaded = await _upload_criteria(client, project["id"])

    assert uploaded["file_kind"] == "SCORING_CRITERIA"
    assert uploaded["knowledge_status"] == "READY"
    assert uploaded["chunk_count"] == 1
    assert uploaded["knowledge_error"] is None
    doc_id = uploaded["knowledge_doc_id"]

    detail = (await client.get(f"/api/knowledge/docs/{doc_id}")).json()
    assert detail["doc_type"] == "EVAL_CRITERIA"
    assert detail["total_chunks"] == uploaded["chunk_count"]
    assert detail["chunk_strategy"] == "whole_document"
    assert detail["original_name"] == "评分标准.md"

    chunks = (await client.get(f"/api/knowledge/docs/{doc_id}/chunks", params={"page_size": 50})).json()
    assert chunks["total"] == uploaded["chunk_count"]
    assert all(item["status"] == "PENDING" for item in chunks["items"])
    assert all(item["char_count"] == len(item["content"]) for item in chunks["items"])
    # 分节标题要写回正文：模型靠它把条款和关卡对应起来
    content = chunks["items"][0]["content"]
    assert "一、需求分析（20 分）" in content
    assert "二、方案设计（30 分）" in content
    assert "三、数据处理（20 分）" in content


@pytest.mark.asyncio
async def test_docs_list_can_filter_by_project(client: httpx.AsyncClient) -> None:
    """项目维度过滤：批改时就是按 project_id 找评分标准。"""
    project = await _create_project(client, "评分标准实训A")
    other = await _create_project(client, "评分标准实训B")
    uploaded = await _upload_criteria(client, project["id"])

    mine = (
        await client.get(
            "/api/knowledge/docs",
            params={"project_id": project["id"], "doc_type": "EVAL_CRITERIA"},
        )
    ).json()
    assert [item["id"] for item in mine["items"]] == [uploaded["knowledge_doc_id"]]

    theirs = (await client.get("/api/knowledge/docs", params={"project_id": other["id"]})).json()
    assert theirs["items"] == []


@pytest.mark.asyncio
async def test_unsupported_format_fails_but_upload_succeeds(client: httpx.AsyncClient) -> None:
    """不支持的文件格式：文件照常上传，知识文档标 FAILED 并给出原因。"""
    project = await _create_project(client, "格式不支持的实训")
    response = await client.post(
        f"/api/projects/{project['id']}/files/upload",
        files={"file": ("准则.zip", b"PK\x03\x04", "application/zip")},
        data={"file_kind": "SCORING_CRITERIA"},
    )

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["knowledge_status"] == "FAILED"
    assert uploaded["chunk_count"] == 0
    assert ".zip" in uploaded["knowledge_error"]

    disabled = await client.delete(f"/api/knowledge/docs/{uploaded['knowledge_doc_id']}")
    assert disabled.json()["success"] is True
    chunks = (await client.get(f"/api/knowledge/docs/{uploaded['knowledge_doc_id']}/chunks")).json()
    assert chunks["code"] == 422  # 停用后详情/切片都读不到了


@pytest.mark.asyncio
async def test_reparse_rebuilds_chunks(client: httpx.AsyncClient) -> None:
    """重切片幂等：切片编号重排、总数不变。"""
    project = await _create_project(client, "重切片实训")
    uploaded = await _upload_criteria(client, project["id"])
    doc_id = uploaded["knowledge_doc_id"]

    reparsed = (await client.post(f"/api/knowledge/docs/{doc_id}/reparse")).json()
    assert reparsed["status"] == "READY"
    assert reparsed["chunk_count"] == uploaded["chunk_count"]

    chunks = (await client.get(f"/api/knowledge/docs/{doc_id}/chunks", params={"page_size": 50})).json()
    assert [item["chunk_index"] for item in chunks["items"]] == list(range(uploaded["chunk_count"]))


# ------------------------------------------------- 向量化与召回（需 Milvus）


@pytest.mark.skipif(not _rag_ready(), reason="缺少 Milvus / rag 依赖 / 本地模型权重，跳过召回测试")
@pytest.mark.asyncio
async def test_embed_then_recall_finds_right_chunk(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入库 → 向量化 → 召回：问"数据集划分比例"，命中讲划分比例的那一片。"""
    config = await _use_test_collection(monkeypatch)
    client_sdk = vector_store.get_client(config.uri)

    try:
        project = await _create_project(client, "召回测试实训")
        uploaded = await _upload_criteria(client, project["id"])
        doc_id = uploaded["knowledge_doc_id"]

        embedded = (await client.post(f"/api/knowledge/docs/{doc_id}/embed")).json()
        assert embedded["chunk_count"] == uploaded["chunk_count"]
        assert embedded["collection"] == config.collection_name

        chunks = (await client.get(f"/api/knowledge/docs/{doc_id}/chunks", params={"page_size": 50})).json()
        assert all(item["status"] == "READY" for item in chunks["items"])
        assert all(item["model_name"] == "BAAI/bge-m3" for item in chunks["items"])
        assert all(item["vector_id"] for item in chunks["items"])

        recalled = (
            await client.post(
                f"/api/knowledge/docs/{doc_id}/recall",
                json={"query": "数据集划分比例没写清楚扣多少分", "top_k": 3},
            )
        ).json()
        assert recalled["candidate_count"] > 0
        assert recalled["hits"], "召回结果不应为空"
        assert "数据集划分比例" in recalled["hits"][0]["content"]
        assert recalled["hits"][0]["rerank_score"] is not None

        # 不重排也要能返回：按 RRF 融合分数排序
        plain = (
            await client.post(
                f"/api/knowledge/docs/{doc_id}/recall",
                json={"query": "技术选型有依据得多少分", "top_k": 2, "use_rerank": False},
            )
        ).json()
        assert plain["reranked"] is False
        assert plain["hits"] and plain["hits"][0]["rerank_score"] is None
        assert "方案设计" in plain["hits"][0]["content"]

        # 跨文档召回：按项目检索，批改链路用的就是这个入口
        project_hits = (
            await client.post(
                "/api/knowledge/recall",
                params={"project_id": project["id"], "doc_type": "EVAL_CRITERIA"},
                json={"query": "需求分析缺关键约束怎么扣分", "top_k": 2},
            )
        ).json()
        assert project_hits["doc_ids"] == [doc_id]
        assert project_hits["hits"] and "需求分析" in project_hits["hits"][0]["content"]

        health = (await client.get("/api/health/milvus")).json()
        assert health["ok"] is True
        assert health["chunks"]["READY"] == uploaded["chunk_count"]
    finally:
        vector_store.drop_collection(client_sdk, config)


@pytest.mark.skipif(not _rag_ready(), reason="缺少 Milvus / rag 依赖 / 本地模型权重，跳过召回测试")
@pytest.mark.asyncio
async def test_recall_without_embedding_returns_empty(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没向量化就召回：返回空结果（且不报错），提示先做 embedding。"""
    await _use_test_collection(monkeypatch)
    project = await _create_project(client, "未向量化实训")
    uploaded = await _upload_criteria(client, project["id"])

    recalled = (
        await client.post(
            f"/api/knowledge/docs/{uploaded['knowledge_doc_id']}/recall",
            json={"query": "数据集划分比例"},
        )
    ).json()

    assert recalled["candidate_count"] == 0
    assert recalled["hits"] == []


@pytest.mark.skipif(not _rag_ready(), reason="缺少 Milvus / rag 依赖 / 本地模型权重，跳过召回测试")
@pytest.mark.asyncio
async def test_disable_purges_vectors_so_recall_returns_nothing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停用后 Milvus 里的向量也要清掉：不带 doc_id 的召回不能再命中已停用内容。"""
    config = await _use_test_collection(monkeypatch)
    client_sdk = vector_store.get_client(config.uri)

    try:
        project = await _create_project(client, "停用清理实训")
        uploaded = await _upload_criteria(client, project["id"])
        doc_id = uploaded["knowledge_doc_id"]
        await client.post(f"/api/knowledge/docs/{doc_id}/embed")

        before = client_sdk.query(
            collection_name=config.collection_name,
            filter=f"doc_id == {doc_id}",
            output_fields=["pk"],
        )
        assert len(before) == uploaded["chunk_count"]

        disabled = (await client.delete(f"/api/knowledge/docs/{doc_id}")).json()
        assert disabled["success"] is True
        assert "Milvus" in disabled["message"]

        after = client_sdk.query(
            collection_name=config.collection_name,
            filter=f"doc_id == {doc_id}",
            output_fields=["pk"],
        )
        assert after == []

        # 不带 doc_id / project_id 的召回（最容易漏的那条路径）也拿不到已停用内容
        recalled = (
            await client.post(
                "/api/knowledge/recall",
                params={"doc_type": "EVAL_CRITERIA"},
                json={"query": "数据集划分比例没写清楚扣多少分", "top_k": 3},
            )
        ).json()
        assert recalled["hits"] == []
    finally:
        vector_store.drop_collection(client_sdk, config)
