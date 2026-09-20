"""设置主模型（``system_config`` 里的 ``ai.llm``）：换模型 / 换 base_url / 换 api key。

配置放在表里而不是 ``.env``，所以改完**不用重启服务**（读缓存 60 秒；脚本会顺手清掉）。

``ai.llm`` 的平铺字段是**默认模型**（DeepSeek）；用 ``--model-key`` 改的是
``ai.llm.models.<key>`` 里那一家（学生端 AI 助教的选项 id：``kimi`` / ``mimo``…）。

用法：
    uv run python scripts/set_llm_config.py --show
    uv run python scripts/set_llm_config.py --model deepseek-v4-flash --base-url https://api.deepseek.com/v1
    LLM_API_KEY=sk-xxx uv run python scripts/set_llm_config.py --api-key-env LLM_API_KEY
    uv run python scripts/set_llm_config.py --model-key kimi --model kimi-latest \
        --base-url https://api.moonshot.cn/v1
    LLM_API_KEY=sk-xxx uv run python scripts/set_llm_config.py --model-key kimi --api-key-env LLM_API_KEY

**api key 不要写成命令行参数**：``--api-key`` 会进 shell 历史与进程列表。
推荐用 ``--api-key-env`` 从环境变量读，或 ``--api-key-stdin`` 从标准输入读。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.core.secrets import mask_secrets  # noqa: E402
from app.crud.account import SystemConfigRepository  # noqa: E402
from app.services import settings_store  # noqa: E402

#: 这一行的说明文案（表里也是它，便于直接看库的人明白结构）
_DESCRIPTION = "大模型配置：默认模型（DeepSeek）+ 可选模型 kimi / mimo 各自的地址与 key"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="设置 system_config 里的 ai.llm")
    parser.add_argument("--show", action="store_true", help="只打印当前配置（api key 打码）")
    parser.add_argument(
        "--model-key",
        dest="model_key",
        help="改 ai.llm.models.<key> 里的一家（kimi / mimo…）；不填则改默认模型那套平铺字段",
    )
    parser.add_argument("--model", help="模型名，如 deepseek-v4-flash")
    parser.add_argument("--base-url", dest="base_url", help="OpenAI 兼容端点，如 https://api.deepseek.com/v1")
    parser.add_argument("--provider", help="供应商标识，默认 openai-compatible")
    parser.add_argument("--temperature", type=float, help="采样温度")
    parser.add_argument("--timeout", type=int, help="请求超时（秒）")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="单次最大输出 token")
    parser.add_argument("--api-key-env", help="从该环境变量读 api key（推荐）")
    parser.add_argument("--api-key-stdin", action="store_true", help="从标准输入读 api key")
    parser.add_argument("--api-key", help="直接给 api key（会进 shell 历史，仅调试用）")
    parser.add_argument("--clear-api-key", action="store_true", help="清空 api key")
    return parser


def _collect(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key in ("model", "base_url", "provider", "temperature", "timeout", "max_tokens"):
        value = getattr(args, key)
        if value is not None:
            updates[key] = value
    if args.clear_api_key:
        updates["api_key"] = ""
    elif args.api_key_env:
        value = os.environ.get(args.api_key_env)
        if not value:
            raise SystemExit(f"环境变量 {args.api_key_env} 是空的")
        updates["api_key"] = value.strip()
    elif args.api_key_stdin:
        updates["api_key"] = sys.stdin.readline().strip()
    elif args.api_key:
        updates["api_key"] = args.api_key.strip()
    return updates


async def main() -> None:
    args = _parser().parse_args()
    async with SessionLocal() as session:
        repo = SystemConfigRepository(session)
        row = await repo.by_key(settings_store.LLM_KEY)
        current: dict[str, Any] = dict(row.config_value) if row and isinstance(row.config_value, dict) else {}
        merged = {**settings_store.DEFAULTS[settings_store.LLM_KEY], **current}
        # 默认骨架里带了 models（kimi / mimo…），与表里的同名项逐家合并，别被整体覆盖
        skeleton = settings_store.DEFAULTS[settings_store.LLM_KEY].get("models") or {}
        stored_models = merged.get("models") if isinstance(merged.get("models"), dict) else {}
        merged["models"] = {
            key: {**(skeleton.get(key) or {}), **(stored_models.get(key) or {})}
            for key in {*skeleton, *stored_models}
        }

        if args.show:
            print(f"{settings_store.LLM_KEY} = {mask_secrets(merged)}")
            print(f"api key {'已配置' if merged.get('api_key') else '未配置'}")
            for model_key, value in (merged.get("models") or {}).items():
                print(
                    f"  · {model_key}: model={value.get('model') or '<空>'} "
                    f"base_url={value.get('base_url') or '<空>'} "
                    f"api_key={'已配置' if value.get('api_key') else '未配置'}"
                )
            return

        updates = _collect(args)
        if not updates:
            raise SystemExit("没有要改的内容；加 --help 看用法，或加 --show 只看当前值")
        if args.model_key:
            model_key = args.model_key.strip()
            merged["models"] = {
                **merged["models"],
                model_key: {**(merged["models"].get(model_key) or {}), **updates},
            }
            target = f"{settings_store.LLM_KEY}.models.{model_key}"
        else:
            merged.update(updates)
            target = settings_store.LLM_KEY

        if row is None:
            await repo.create(
                {
                    "config_key": settings_store.LLM_KEY,
                    "config_value": merged,
                    "description": _DESCRIPTION,
                }
            )
        else:
            await repo.update(row, {"config_value": merged, "description": _DESCRIPTION})
        await session.commit()

    settings_store.invalidate(settings_store.LLM_KEY)
    changed = ", ".join(sorted(updates))
    print(f"已更新 {target}：{changed}")
    print(f"当前：{mask_secrets(merged)}")


if __name__ == "__main__":
    asyncio.run(main())
