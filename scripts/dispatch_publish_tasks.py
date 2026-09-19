"""把到点的定时发布任务翻成"已发布"（学生端任务清单里出现、项目标成必修）。

项目本身只要 PUBLISHED 就对全体学生开放，所以这个脚本不再影响"能不能看到 / 能不能做"，
它决定的是"从什么时候开始算必修"（以及给目标学生发通知）。

用法：
    uv run python scripts/dispatch_publish_tasks.py              # 处理所有到点的 PENDING 任务
    uv run python scripts/dispatch_publish_tasks.py --dry-run    # 只看会发布哪些，不动数据
    uv run python scripts/dispatch_publish_tasks.py --task-id 12 # 只处理某一条（排查用）
    uv run python scripts/dispatch_publish_tasks.py --at "2026-09-20 08:00:00"

什么时候用：挂宿主 cron，每分钟跑一次（定时发布的最小粒度就是分钟）：

    * * * * * cd /path/to/training_platform && .venv/bin/python scripts/dispatch_publish_tasks.py \
        >> data/run/publish.log 2>&1

三条口径（见 docs/方案设计.md §4.3）：

1. **脚本没跑 = 任务不生效**：定时发布只有这一条生效路径，刻意不做"读的时候顺便判定"的
   双口径，否则通知与可见性会不一致；
2. **发布前复核项目状态**：已经被下架 / 改回草稿的项目从任务里剔除；项目被剔空的任务整体
   留在 PENDING 并写一条 SKIP 审计，等教师处理（不会发一个空任务出去）；
3. **通知与审计同事务**：发布成功才给学生写 TASK_PUBLISH 通知、给平台写 PUBLISH 审计。
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.services import publish as publish_service  # noqa: E402


async def dispatch(*, at: datetime | None, task_id: int | None, dry_run: bool) -> list[dict]:
    async with SessionLocal() as session:
        results = await publish_service.dispatch_due_tasks(session, at=at, task_id=task_id, dry_run=dry_run)
        if not dry_run:
            await session.commit()
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description="发布到点的定时任务")
    parser.add_argument("--task-id", type=int, default=None, help="只处理指定任务（排查用）")
    parser.add_argument("--at", type=str, default=None, help="把「现在」当作这个时间，如 2026-09-20 08:00:00")
    parser.add_argument("--dry-run", action="store_true", help="只列出会发布哪些任务，不动数据")
    args = parser.parse_args()

    at = datetime.fromisoformat(args.at) if args.at else None
    results = asyncio.run(dispatch(at=at, task_id=args.task_id, dry_run=args.dry_run))

    if not results:
        print("没有到点的定时任务。")
        return 0

    head = "将发布" if args.dry_run else "已处理"
    published = [item for item in results if item["published"]]
    skipped = [item for item in results if not item["published"]]
    print(f"{head} {len(published)} 条 / 跳过 {len(skipped)} 条：")
    for item in results:
        task = item["task"]
        mark = "发布" if item["published"] else "跳过"
        reason = f"（{item['reason']}）" if item["reason"] else ""
        print(f"  [{mark}] #{task.id} {task.title}{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
