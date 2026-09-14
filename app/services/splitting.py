"""结构化切分：把解析出的文本块切成检索用切片（纯 Python，不依赖模型）。

为什么不用 LangChain 的 Splitter：我们需要"结构感知 + 标题路径"——按 ``heading_path``
分组、每块把标题路径前置（检索时标题常常就是命中的关键词）、超长段按句切、硬上限保护。

切分顺序：

1. 按 ``(heading_path, page_no)`` 分组，形成语义段（页码也参与分组，跨页不合并，引用才准）；
2. 段内按句子聚合到目标长度；
3. 超长单句按硬上限强制切；
4. 相邻块保留重叠，避免答案被切在缝里；
5. 每块正文前置 heading_path，``content_hash = sha256(归一化正文)`` 供去重与重切比对。

硬上限默认 20000 字符：Milvus 的 ``VarChar`` 上限是 65535 **字节**，中文 UTF-8 一字 3 字节，
约 2.1 万汉字，在切片阶段卡住比写向量库时才失败好。
"""

import hashlib
import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.parsing import ParsedDocument

#: 切分策略版本：换分组方式或长度口径时要改，用来识别旧数据
CHUNK_STRATEGY = "v1_structural"

#: 整份不切的策略名（评分标准这类"整份使用"的文档）
WHOLE_STRATEGY = "whole_document"

_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;\n]*")


@dataclass(frozen=True)
class Chunk:
    """一个待入库的切片。"""

    content: str
    heading_path: str
    page_no: int | None
    char_count: int
    content_hash: str


def strategy_name(chunk_size_chars: int) -> str:
    """策略版本带上块长，便于以后按版本识别旧数据。"""
    return f"{CHUNK_STRATEGY}_{chunk_size_chars}"


def split_whole_document(document: ParsedDocument) -> list[Chunk]:
    """整份正文作为**一个**切片，不按标题拆。

    适用场景：评分标准。它天然是"整份对照着用"的文档——评分时本来就要把标准全文
    交给模型，拆成一片片反而带来两个麻烦：一是按标题拆会拆出几十片、超出上下文预算后
    后面的维度拿不到条款；二是 RAG 只召回"措辞像"的章节，覆盖不全。
    整份一块既省事又准确（几百字的文档远在向量模型 8192 token 窗口之内）。

    注意要把**标题写回正文**：解析阶段把 Markdown 的 ``##`` 行收进了 ``heading_path``，
    正文里是没有的。不还原的话，模型拿到的标准就没有"一、需求分析（10 分）"这类分节名，
    没法把条款和关卡对应起来。标题路径变化时补一行同级标题，正文顺序保持不变。
    """
    parts: list[str] = []
    current_path: str | None = None
    for block in document.blocks:
        path = block.heading_path or ""
        if path != current_path:
            if path:
                parts.append(f"## {path.split(' > ')[-1]}")
            current_path = path
        parts.append(block.text)
    text = "\n\n".join(parts).strip()
    if not text:
        return []
    return [
        Chunk(
            content=text,
            heading_path="",
            page_no=document.blocks[0].page_no,
            char_count=len(text),
            content_hash=_content_hash(text),
        )
    ]


def split_document(
    document: ParsedDocument,
    *,
    whole: bool = False,
    chunk_size_chars: int | None = None,
    overlap_chars: int | None = None,
    max_chunk_chars: int | None = None,
) -> list[Chunk]:
    """把解析结果切成切片；空文档返回空列表（由调用方判定为失败）。

    ``whole=True`` 时整份作为一块（见 :func:`split_whole_document`）。
    """
    if whole:
        return split_whole_document(document)
    chunk_size = chunk_size_chars or settings.knowledge_chunk_size_chars
    overlap = overlap_chars if overlap_chars is not None else settings.knowledge_chunk_overlap_chars
    max_chars = max_chunk_chars or settings.knowledge_max_chunk_chars
    if chunk_size <= 0 or max_chars <= 0:
        raise ValueError("切片长度必须为正数")
    overlap = max(0, min(overlap, chunk_size // 2))

    chunks: list[Chunk] = []
    for heading_path, page_no, text in _group_blocks(document):
        for body in _pack(_sentences(text), chunk_size, overlap, max_chars):
            content = f"{heading_path}\n{body}" if heading_path else body
            content = content.strip()
            if not content:
                continue
            chunks.append(
                Chunk(
                    content=content,
                    heading_path=heading_path,
                    page_no=page_no,
                    char_count=len(content),
                    content_hash=_content_hash(content),
                )
            )
    return chunks


def _group_blocks(document: ParsedDocument) -> list[tuple[str, int | None, str]]:
    """把相邻且标题路径/页码相同的块并成一段，返回 ``(heading_path, page_no, text)``。"""
    groups: list[tuple[str, int | None, list[str]]] = []
    for block in document.blocks:
        text = block.text.strip()
        if not text:
            continue
        if groups and groups[-1][0] == block.heading_path and groups[-1][1] == block.page_no:
            groups[-1][2].append(text)
            continue
        groups.append((block.heading_path, block.page_no, [text]))
    return [(heading, page, "\n".join(parts)) for heading, page, parts in groups]


def _sentences(text: str) -> list[str]:
    """按中英文句末标点与换行切句，标点跟在前一句尾部。"""
    return [piece.strip() for piece in _SENTENCE_RE.findall(text) if piece.strip()]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """单句超长时的兜底：按硬上限等分。"""
    return [text[start : start + max_chars] for start in range(0, len(text), max_chars)]


def _pack(sentences: list[str], chunk_size: int, overlap: int, max_chars: int) -> list[str]:
    """段内按目标长度聚合，块间保留重叠，并保证每块不超过硬上限。"""
    chunks: list[str] = []
    buffer = ""
    for sentence in sentences:
        pieces = _hard_split(sentence, max_chars) if len(sentence) > max_chars else [sentence]
        for piece in pieces:
            if buffer and len(buffer) + len(piece) + 1 > chunk_size:
                chunks.append(buffer)
                tail = buffer[-overlap:].strip() if overlap else ""
                buffer = f"{tail}\n{piece}" if tail else piece
            else:
                buffer = f"{buffer}\n{piece}" if buffer else piece
            # 重叠 + 新句可能超硬上限，按上限切开（尾块继续往下拼）
            while len(buffer) > max_chars:
                chunks.append(buffer[:max_chars])
                buffer = buffer[max_chars:]
    if buffer.strip():
        chunks.append(buffer)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _content_hash(content: str) -> str:
    """归一化空白后取 sha256，避免同一内容因换行差异被当成两块。"""
    normalized = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "CHUNK_STRATEGY",
    "WHOLE_STRATEGY",
    "Chunk",
    "split_document",
    "split_whole_document",
    "strategy_name",
]
