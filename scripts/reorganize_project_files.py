"""把已上传的项目文件重新归位到"一个项目一个文件夹"的目录结构。

目标结构（本地与对象存储都适用）：
    <bucket>/projects/<项目ID>-<项目名>/<用途>/<uuid>_<文件名>

改动对象：已经被项目引用（project_file）的文件 —— 按项目名重建 object_key，
把对象搬过去（S3 copy+delete / 本地重命名），并更新 file_asset.object_key。

幂等：已经在目标目录下的文件会被跳过；--dry-run 只打印计划。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.crud.attempt import FileAssetRepository  # noqa: E402
from app.models.attempt import FileAsset  # noqa: E402
from app.models.project import ProjectFile, TrainingProject  # noqa: E402
from app.services import storage  # noqa: E402


def _target_key(asset_key: str, *, project_id: int, project_name: str, file_kind: str) -> str:
    """按项目的目录前缀重建 object_key，文件名部分保持不变。"""
    filename = asset_key.rsplit("/", 1)[-1]
    scope = storage.project_scope(project_id, project_name)
    return f"{scope}/{file_kind.lower()}/{filename}"


async def reorganize(*, dry_run: bool) -> dict[str, int]:
    stats = {"scanned": 0, "moved": 0, "skipped": 0}
    async with SessionLocal() as session:
        assets = FileAssetRepository(session)
        stmt = (
            select(ProjectFile, TrainingProject, FileAsset)
            .join(TrainingProject, TrainingProject.id == ProjectFile.project_id)  # type: ignore[arg-type]
            .join(FileAsset, FileAsset.id == ProjectFile.file_asset_id)  # type: ignore[arg-type]
            .order_by(ProjectFile.id)
        )
        for link, project, asset in (await session.exec(stmt)).all():
            stats["scanned"] += 1
            target = _target_key(
                asset.object_key,
                project_id=project.id,
                project_name=project.project_name,
                file_kind=link.file_kind,
            )
            if target == asset.object_key:
                stats["skipped"] += 1
                continue
            if dry_run:
                stats["moved"] += 1
                print(f"  待归位：{asset.object_key}\n         -> {target}")
                continue
            if not storage.exists(asset.bucket, asset.object_key):
                stats["skipped"] += 1
                print(f"  跳过（对象不存在）：{asset.object_key}")
                continue
            storage.move_object(asset.bucket, asset.object_key, target)
            await assets.update(asset, {"object_key": target})
            stats["moved"] += 1
            print(f"  已归位：{asset.original_name} -> {target}")
        if not dry_run:
            await session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="把项目文件归位到项目目录下")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改数据")
    args = parser.parse_args()

    print(f"存储后端：{storage.backend_name()}")
    stats = asyncio.run(reorganize(dry_run=args.dry_run))
    print("完成：扫描 {scanned}，归位 {moved}，已在目标位置 {skipped}".format(**stats))
    if args.dry_run:
        print("（dry-run：没有改动任何数据）")


if __name__ == "__main__":
    main()
