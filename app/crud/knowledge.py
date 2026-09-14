"""知识库仓储：知识文档与切片。"""

from collections.abc import Sequence

from sqlmodel import select

from app.crud.base import BaseRepository
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc


class KnowledgeDocRepository(BaseRepository[KnowledgeDoc]):
    """知识文档仓储（带软删）。"""

    model = KnowledgeDoc
    soft_delete = True

    async def by_file_asset(self, file_asset_id: int) -> KnowledgeDoc | None:
        """按来源文件取文档：同一份文件重复入库时用它做幂等判断。"""
        return await self.get_by(file_asset_id=file_asset_id)

    async def list_by_file_assets(
        self, file_asset_ids: Sequence[int], *, status: str | None = None
    ) -> list[KnowledgeDoc]:
        """按附件 ID 批量取文档（项目 → 附件 → 文件 → 知识文档 这条链）。"""
        ids = [int(item) for item in file_asset_ids]
        if not ids:
            return []
        stmt = self._statement().where(KnowledgeDoc.file_asset_id.in_(ids))  # type: ignore[attr-defined]
        if status:
            stmt = stmt.where(KnowledgeDoc.status == status)
        return list((await self.session.exec(stmt)).all())


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    """知识切片仓储。"""

    model = KnowledgeChunk

    async def list_of_doc(self, doc_id: int) -> list[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id).order_by(KnowledgeChunk.chunk_index)
        )
        return list((await self.session.exec(stmt)).all())

    async def by_ids(self, chunk_ids: Sequence[int]) -> list[KnowledgeChunk]:
        ids = [int(item) for item in chunk_ids]
        if not ids:
            return []
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids))  # type: ignore[attr-defined]
        return list((await self.session.exec(stmt)).all())

    async def doc_ids_with_chunks(self) -> list[int]:
        stmt = select(KnowledgeChunk.doc_id).distinct()
        return [int(item) for item in (await self.session.exec(stmt)).all()]


__all__ = ["KnowledgeChunkRepository", "KnowledgeDocRepository"]
