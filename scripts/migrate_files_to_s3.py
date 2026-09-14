"""把本地磁盘上的存量文件迁到 S3 兼容对象存储（MinIO / OSS / S3）。

用法：
    # 先在 .env 里配好 STORAGE_BACKEND=s3 与 S3_* 参数
    uv run python scripts/migrate_files_to_s3.py          # 迁移
    uv run python scripts/migrate_files_to_s3.py --dry-run # 只看会迁什么

做的事：遍历 ``file_asset`` 里 bucket=local 的记录 → 按原 object_key 上传到对象存储 →
把台账的 bucket 改成目标桶。**本地文件保留**（确认没问题后再手工清理 data/uploads）。
脚本幂等：已经是对象存储桶的记录会被跳过。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.crud.attempt import FileAssetRepository  # noqa: E402
from app.schemas.base import PageParams  # noqa: E402
from app.services import storage  # noqa: E402


async def migrate(*, dry_run: bool) -> dict[str, int]:
    if storage.backend_name() != storage.S3_BACKEND:
        raise SystemExit("当前 STORAGE_BACKEND 不是 s3，先在 .env 里配置 STORAGE_BACKEND=s3 与 S3_* 参数")
    bucket = storage.ensure_bucket()

    stats = {"scanned": 0, "migrated": 0, "skipped": 0, "missing": 0}
    async with SessionLocal() as session:
        assets = FileAssetRepository(session)
        page_no = 1
        while True:
            page = await assets.list_page(PageParams(page=page_no, page_size=200))
            for asset in page.items:
                stats["scanned"] += 1
                if storage.is_object_storage(asset.bucket):
                    stats["skipped"] += 1
                    continue
                local_path = settings.resolved_upload_dir / asset.bucket / asset.object_key
                if not local_path.is_file():
                    stats["missing"] += 1
                    print(f"  跳过（本地文件不存在）：{asset.bucket}/{asset.object_key}")
                    continue
                if dry_run:
                    stats["migrated"] += 1
                    print(f"  待迁移：{asset.original_name} -> s3://{bucket}/{asset.object_key}")
                    continue
                storage.upload_local_file(local_path, bucket=bucket, object_key=asset.object_key)
                await assets.update(asset, {"bucket": bucket})
                stats["migrated"] += 1
                print(f"  已迁移：{asset.original_name} -> s3://{bucket}/{asset.object_key}")
            if page_no >= page.pages:
                break
            page_no += 1
        if not dry_run:
            await session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="把本地文件迁到 S3 兼容对象存储")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不真正迁移")
    args = parser.parse_args()

    print(f"目标对象存储：{settings.s3_endpoint} / 桶 {settings.s3_bucket}")
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    print("完成：扫描 {scanned}，迁移 {migrated}，已是对象存储 {skipped}，本地缺失 {missing}".format(**stats))
    if args.dry_run:
        print("（dry-run：没有改动任何数据）")


if __name__ == "__main__":
    main()
