"""模型自检：验证 BGE-M3（稠密 + 稀疏）与 bge-reranker-v2-m3 能加载并推理。

用法：
    uv run python scripts/check_models.py                   # 有 GPU 就用 GPU + fp16
    uv run python scripts/check_models.py --device cpu      # 无卡环境
    uv run python scripts/check_models.py --skip-reranker   # 只测向量模型

权重默认从 ``<项目根>/models`` 读（先跑 scripts/download_rag_models.py）。
脚本会打印推理耗时与显存峰值，用来核对方案里的显存风险项：
BGE-M3 fp16 约 2.2GB + Reranker 约 2.3GB，同卡共存建议 ≥16GB；8GB 卡只够小批量。
"""

import argparse
import inspect
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_TEXTS = [
    "工业缺陷检测实训第一阶段要求完成数据采集与标注。",
    "报告模板里的实验步骤必须包含数据集划分比例。",
    "Milvus 的稀疏向量字段是 SPARSE_FLOAT_VECTOR，用倒排索引检索。",
]
QUERY = "实训报告需要写数据集划分比例吗？"


def device_kwargs(cls, device: str) -> dict:
    """FlagEmbedding 不同版本对设备参数命名不一致，按签名决定是否传。"""
    params = inspect.signature(cls.__init__).parameters
    return {"devices": device} if "devices" in params else {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models-dir", type=Path, default=REPO_ROOT / "models", help="模型根目录")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda:0", "cpu"], help="推理设备")
    parser.add_argument("--skip-reranker", action="store_true", help="不测重排模型")
    parser.add_argument("--no-fp16", action="store_true", help="GPU 上也用 fp32（排查精度问题时用）")
    args = parser.parse_args()

    import torch
    from FlagEmbedding import BGEM3FlagModel, FlagReranker

    device = ("cuda:0" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    # CPU 上 fp16 没有收益且部分算子不支持，只有走 GPU 时才默认开
    use_fp16 = device != "cpu" and not args.no_fp16

    embed_dir = args.models_dir / "bge-m3"
    rerank_dir = args.models_dir / "bge-reranker-v2-m3"
    if not embed_dir.is_dir():
        print(f"缺少向量模型目录 {embed_dir}，先跑 scripts/download_rag_models.py", file=sys.stderr)
        return 1

    print(f"设备={device} fp16={use_fp16}")
    if device.startswith("cuda"):
        name = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"GPU={name} 显存={total:.1f}GB")

    # ① BGE-M3：稠密 + 稀疏一次前向
    t0 = time.perf_counter()
    embed_model = BGEM3FlagModel(
        str(embed_dir), use_fp16=use_fp16, normalize_embeddings=True, **device_kwargs(BGEM3FlagModel, device)
    )
    load_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = embed_model.encode(
        SAMPLE_TEXTS, batch_size=8, max_length=512, return_dense=True, return_sparse=True
    )
    encode_s = time.perf_counter() - t0
    dense, sparse = out["dense_vecs"], out["lexical_weights"]
    norm0 = float((dense[0] ** 2).sum() ** 0.5)
    print(f"[向量模型] 加载 {load_s:.1f}s，编码 {len(SAMPLE_TEXTS)} 条 {encode_s:.2f}s")
    print(f"          dense={dense.shape} dtype={dense.dtype} 首条 L2 范数={norm0:.4f}")
    print(f"          sparse 前 3 条 token 数={[len(s) for s in sparse[:3]]}")
    if dense.shape[1] != 1024:
        print(f"[失败] 稠密维度应为 1024，实际 {dense.shape[1]}", file=sys.stderr)
        return 1
    if not all(len(s) > 0 for s in sparse):
        print("[失败] 稀疏权重为空", file=sys.stderr)
        return 1

    # ② 重排模型
    if args.skip_reranker:
        print("[重排模型] 已跳过")
    else:
        if not rerank_dir.is_dir():
            print(f"[失败] 缺少重排模型目录 {rerank_dir}", file=sys.stderr)
            return 1
        t0 = time.perf_counter()
        reranker = FlagReranker(str(rerank_dir), use_fp16=use_fp16, **device_kwargs(FlagReranker, device))
        load2_s = time.perf_counter() - t0
        pairs = [[QUERY, text] for text in SAMPLE_TEXTS]
        t0 = time.perf_counter()
        scores = reranker.compute_score(pairs, batch_size=8, max_length=1024)
        score_s = time.perf_counter() - t0
        print(f"[重排模型] 加载 {load2_s:.1f}s，打分 {len(pairs)} 对 {score_s:.2f}s")
        print(f"          分数={[round(float(s), 4) for s in scores]}")
        best = max(range(len(scores)), key=lambda i: float(scores[i]))
        print(f"          最相关片段=第 {best} 条：{SAMPLE_TEXTS[best][:24]}…")

    if device.startswith("cuda"):
        used = torch.cuda.max_memory_allocated() / 1024**3
        reserved = torch.cuda.max_memory_reserved() / 1024**3
        print(f"显存峰值 allocated={used:.2f}GB reserved={reserved:.2f}GB")
        if not args.skip_reranker and used > 6.5:
            print("提示：8GB 卡上两模型共存已接近上限，生产建议按方案把推理拆到独立进程/服务。")

    print("模型自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
