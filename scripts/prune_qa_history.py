"""清理超出保留期的 AI 问答会话（历史只保留最近 N 天）。

用法：
    uv run python scripts/prune_qa_history.py                    # 用 ai.qa.history_retention_days
    uv run python scripts/prune_qa_history.py --days 7           # 临时指定保留天数
    uv run python scripts/prune_qa_history.py --dry-run          # 只看会删哪些，不动数据

什么时候用：挂宿主 cron / docker 定时任务，每天凌晨跑一次即可。

两条口径：

1. **按会话粒度**：保留期按会话的 ``updated_at`` 算，一周内有活动的会话整体保留；
2. **删除顺序**：引用 → 消息 → 会话。反了会留下悬空引用（外键约束会直接报错）。

查询层已经带了保留期过滤（学生看不到过期会话），这个脚本只负责把空间收回来 ——
所以它没跑或跑失败都不影响学生使用，只是数据还在库里。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.services import qa  # noqa: E402


async def prune(*, days: int | None, dry_run: bool) -> dict:
    """清理逻辑在 :func:`app.services.qa.prune_history`，这里只负责开会话与提交。"""
    async with SessionLocal() as session:
        result = await qa.prune_history(session, days=days, dry_run=dry_run)
        if not dry_run:
            await session.commit()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="清理超出保留期的 AI 问答会话")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="保留天数，默认取 ai.qa.history_retention_days",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计会删掉的会话数，不实际删除")
    args = parser.parse_args()

    result = asyncio.run(prune(days=args.days, dry_run=args.dry_run))
    head = "将清理" if result["dry_run"] else "已清理"
    print(
        f"{head}保留 {result['days']} 天以外的问答会话："
        f"{result['sessions']} 个会话 / {result['messages']} 条消息 / {result['citations']} 条引用"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
