"""下载 RAG 所需的向量模型与重排模型权重到本地 ``models/`` 目录。

用法：
    uv run python scripts/download_rag_models.py                 # 两个模型都下
    uv run python scripts/download_rag_models.py --model bge-m3  # 只下向量模型
    uv run python scripts/download_rag_models.py --dry-run       # 只看会下哪些文件

为什么落到本地目录而不是默认 HF 缓存：``system_config`` 里的
``ai.embedding.model_path`` / ``ai.reranker.model_path`` 支持填本地绝对路径，
离线部署时不必再依赖 HuggingFace 网络。

国内直连 ``huggingface.co`` 不通，脚本默认把 ``HF_ENDPOINT`` 指向 ``hf-mirror.com``；
要走官方源就显式设 ``HF_ENDPOINT=https://huggingface.co``。

只下推理必需文件（排除 onnx 导出与 README 配图），两个模型合计约 4.5GB。
脚本幂等：已下载且校验一致的文件会跳过。
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 模型目录名 -> (HuggingFace 仓库, 不下载的文件模式)
MODELS: dict[str, tuple[str, list[str]]] = {
    "bge-m3": ("BAAI/bge-m3", ["onnx/*", "imgs/*", "*.jpg", "*.webp", "*.DS_Store"]),
    "bge-reranker-v2-m3": ("BAAI/bge-reranker-v2-m3", ["assets/*"]),
}

#: 权重文件名，用来判断一个模型目录是不是真的可用
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def download_model(name: str, target_root: Path, *, dry_run: bool) -> Path:
    """下载单个模型，返回落盘目录。"""
    from huggingface_hub import snapshot_download

    repo_id, ignore = MODELS[name]
    local_dir = target_root / name
    local_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{name}] {repo_id} -> {local_dir}", flush=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        ignore_patterns=ignore,
        max_workers=4,
        dry_run=dry_run,
    )
    return local_dir


def report(path: Path) -> None:
    """打印目录大小并检查权重文件是否到位。"""
    files = [p for p in path.rglob("*") if p.is_file() and ".cache" not in p.parts]
    total = sum(p.stat().st_size for p in files)
    weights = [p.name for p in files if p.name in WEIGHT_FILES]
    print(f"[{path.name}] {len(files)} 个文件, {total / 1024**3:.2f} GB")
    for w in weights:
        size = next(p.stat().st_size for p in files if p.name == w)
        print(f"          权重 {w} {size / 1024**3:.2f} GB")
    if not weights:
        print("          [失败] 没有任何权重文件（model.safetensors / pytorch_model.bin）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model",
        action="append",
        choices=sorted(MODELS),
        help="只下载指定模型，可重复；缺省下载全部",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "models",
        help="模型落盘根目录，默认 <项目根>/models",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出会下载的文件，不实际下载")
    args = parser.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    print(f"HF_ENDPOINT={os.environ['HF_ENDPOINT']}", flush=True)

    names = args.model or sorted(MODELS)
    failed: list[str] = []
    for name in names:
        path = download_model(name, args.out_dir, dry_run=args.dry_run)
        if args.dry_run:
            print(f"[{name}] dry-run 结束，未下载文件")
        else:
            report(path)
            if not any((path / w).is_file() for w in WEIGHT_FILES):
                failed.append(name)

    if failed:
        print(f"以下模型缺少权重文件：{', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
