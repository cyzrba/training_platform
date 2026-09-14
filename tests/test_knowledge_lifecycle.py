"""知识文档的生命周期：删除要连带清切片、重新切片后要还能被检索到。

这两条都是"看起来成功了、其实数据不对"的坑：

1. 删除文档只软删文档行，切片留在库里 → 孤儿数据带着完整正文，白占空间还可能被误召回；
2. 重新切片换了切片主键，旧向量指向已删除的切片 → 文档静默搜不到，接口却返回成功。
"""

import socket

import httpx
import pytest
from sqlmodel import func, select

from app.core.config import BASE_DIR
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services import settings_store, vector_store

CRITERIA_MD = """# 实训报告评分标准

## 一、需求分析（20 分）

需求描述完整、边界清晰得 20 分；缺少关键约束每处扣 5 分。

## 二、方案设计（30 分）

系统架构合理、技术选型有依据得 30 分；架构描述含糊扣 10 分。
"""

TEST_COLLECTION_ALIAS = "knowledge_chunk_lifecycle"


def _milvus_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 19530), timeout=0.5):
            return True
    except OSError:
        return False


def _rag_ready() -> bool:
    return (
        vector_store.milvus_available() and (BASE_DIR / "models" / "bge-m3").is_dir() and _milvus_reachable()
    )


async def _use_test_collection(monkeypatch: pytest.MonkeyPatch) -> settings_store.MilvusConfig:
    config = settings_store.MilvusConfig(
        provider="milvus",
        uri="http://127.0.0.1:19530",
        collection_alias=TEST_COLLECTION_ALIAS,
        collection_version="v1",
        metric="COSINE",
        index="HNSW",
        consistency="Bounded",
    )

    async def _fake(_session) -> settings_store.MilvusConfig:
        return config

    monkeypatch.setattr(settings_store, "get_milvus_config", _fake)
    vector_store.drop_collection(vector_store.get_client(config.uri), config)
    return config


async def _upload_criteria(client: httpx.AsyncClient, project_id: int) -> dict:
    return (
        await client.post(
            f"/api/projects/{project_id}/files/upload",
            files={"file": ("评分标准.md", CRITERIA_MD.encode(), "text/markdown")},
            data={"file_kind": "SCORING_CRITERIA", "title": "实训报告评分标准"},
        )
    ).json()


async def _new_project(client: httpx.AsyncClient, name: str) -> dict:
    return (await client.post("/api/projects", json={"project_name": name, "project_level": "BASIC"})).json()


# ------------------------------------------------------------------ 删除级联


@pytest.mark.asyncio
async def test_delete_document_also_removes_chunks(client: httpx.AsyncClient, db_session) -> None:
    """删文档要连带删切片：不留孤儿行（切片正文可占很大空间）。"""
    project = await _new_project(client, "删除级联实训")
    uploaded = await _upload_criteria(client, project["id"])
    doc_id = uploaded["knowledge_doc_id"]
    assert uploaded["chunk_count"] > 0

    before = (
        await db_session.exec(
            select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
        )
    ).one()
    assert before == uploaded["chunk_count"]

    removed = (await client.delete(f"/api/knowledge/docs/{doc_id}")).json()
    assert removed["success"] is True
    assert f"连带删除 {before} 条切片" in removed["message"]

    after = (
        await db_session.exec(
            select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
        )
    ).one()
    assert after == 0, "切片应随文档一起删除，不能留孤儿行"

    # 文档本身是软删（保留台账做审计），但已从常规查询里消失
    doc = (await db_session.exec(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))).one()
    assert doc.deleted_at is not None
    assert doc.status == "DISABLED"
    assert doc.total_chunks == 0
    assert (await client.get(f"/api/knowledge/docs/{doc_id}")).json()["code"] == 422


# ------------------------------------------------------ 重新切片（需 Milvus）


@pytest.mark.skipif(not _rag_ready(), reason="缺少 Milvus / rag 依赖 / 本地模型权重，跳过")
@pytest.mark.asyncio
async def test_reparse_reembeds_so_document_stays_searchable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重切之后必须重新向量化：否则新切片的 chunk_id 换了、旧向量还指向已删除的行，
    文档会静默搜不到，而接口却返回成功。"""
    config = await _use_test_collection(monkeypatch)
    client_sdk = vector_store.get_client(config.uri)

    try:
        project = await _new_project(client, "重切重嵌实训")
        uploaded = await _upload_criteria(client, project["id"])
        doc_id = uploaded["knowledge_doc_id"]
        await client.post(f"/api/knowledge/docs/{doc_id}/embed")

        # 问法能命中"数据集划分"那一段（本例标准里写的是"缺少关键约束"）
        before = (
            await client.post(
                "/api/knowledge/recall",
                params={"project_id": project["id"], "doc_type": "EVAL_CRITERIA"},
                json={"query": "缺少关键约束怎么扣分", "top_k": 2},
            )
        ).json()
        assert before["hits"], "重切前应该能召回"

        reparsed = (await client.post(f"/api/knowledge/docs/{doc_id}/reparse")).json()
        assert reparsed["status"] == "READY"
        assert reparsed["chunk_count"] == uploaded["chunk_count"]
        assert reparsed["warnings"] == []

        chunks = (await client.get(f"/api/knowledge/docs/{doc_id}/chunks", params={"page_size": 50})).json()
        assert all(item["status"] == "READY" for item in chunks["items"]), "重切后应已重新向量化"
        assert all(item["vector_id"] for item in chunks["items"])

        # 关键断言：重切之后仍然搜得到，且 Milvus 里只有新切片的向量
        after = (
            await client.post(
                "/api/knowledge/recall",
                params={"project_id": project["id"], "doc_type": "EVAL_CRITERIA"},
                json={"query": "缺少关键约束怎么扣分", "top_k": 2},
            )
        ).json()
        assert after["hits"], "重切后文档不能失去可检索性"
        assert not [w for w in after["warnings"] if "找不到" in w]

        rows = client_sdk.query(
            collection_name=config.collection_name,
            filter=f"doc_id == {doc_id}",
            output_fields=["pk"],
        )
        assert len(rows) == uploaded["chunk_count"], "旧向量应已清理，只剩新切片的向量"
    finally:
        vector_store.drop_collection(client_sdk, config)


@pytest.mark.skipif(not _rag_ready(), reason="缺少 Milvus / rag 依赖 / 本地模型权重，跳过")
@pytest.mark.asyncio
async def test_reparse_purges_vectors_when_embedding_unavailable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """向量链路不可用时：清掉旧向量并回提示，而不是留下指向已删除切片的脏命中。"""
    config = await _use_test_collection(monkeypatch)
    client_sdk = vector_store.get_client(config.uri)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模拟向量化失败")

    try:
        project = await _new_project(client, "重切失败降级实训")
        uploaded = await _upload_criteria(client, project["id"])
        doc_id = uploaded["knowledge_doc_id"]
        await client.post(f"/api/knowledge/docs/{doc_id}/embed")

        monkeypatch.setattr("app.services.knowledge_ingest.embed_document", _boom)
        reparsed = (await client.post(f"/api/knowledge/docs/{doc_id}/reparse")).json()

        assert reparsed["status"] == "READY"  # 真源（切片）是好的
        assert reparsed["warnings"], "重新向量化失败必须回提示，不能装作成功"

        rows = client_sdk.query(
            collection_name=config.collection_name,
            filter=f"doc_id == {doc_id}",
            output_fields=["pk"],
        )
        assert rows == [], "旧向量必须清掉，否则会命中已不存在的切片"
    finally:
        vector_store.drop_collection(client_sdk, config)
