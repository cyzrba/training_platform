"""文档解析：把上传的文件变成结构化文本块（纯 Python，不依赖模型）。

产出统一结构 :class:`ParsedDocument`，每块带 ``heading_path``（标题路径）与 ``page_no``，
给后续结构化切分用。支持格式与依赖：

| 格式 | 依赖 | 说明 |
| --- | --- | --- |
| .md / .markdown | 内置 | 按 ``#`` 标题层级建 heading_path |
| .txt | 内置 | 按空行分段 |
| .csv | 内置 | 首行当表头逐行展开 |
| .docx | python-docx | 标题样式与表格一并抽取 |
| .pdf | pypdf | 按页抽文本，带页码 |
| .xlsx | openpyxl | 每个工作表一块，行按 ``列: 值`` 展开 |

不支持的扩展名明确失败（抛 ``BusinessRuleError``），不静默跳过——评审标准传错格式时
老师要立刻看到原因，而不是发现"传了却没进知识库"。
"""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.exceptions import BusinessRuleError

#: 支持的扩展名
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".csv", ".docx", ".pdf", ".xlsx"})

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class ParsedBlock:
    """解析出的一段文本；若干块可以共享同一个 heading_path。"""

    text: str
    heading_path: str = ""
    page_no: int | None = None


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(block.text.strip() for block in self.blocks)

    @property
    def char_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)


def parse(content: bytes, *, filename: str) -> ParsedDocument:
    """按扩展名分派解析；没有扩展名时按内容嗅探，都认不出来才抛 ``BusinessRuleError``。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        sniffed = sniff_suffix(content)
        if sniffed is None:
            supported = " / ".join(sorted(SUPPORTED_SUFFIXES))
            raise BusinessRuleError(f"暂不支持解析 {suffix or '无扩展名'} 文件，支持的格式：{supported}")
        suffix = sniffed
    if suffix in {".md", ".markdown"}:
        return _parse_markdown(content)
    if suffix == ".txt":
        return _parse_text(content)
    if suffix == ".csv":
        return _parse_csv(content)
    if suffix == ".docx":
        return _parse_docx(content)
    if suffix == ".pdf":
        return _parse_pdf(content)
    return _parse_xlsx(content)


def sniff_suffix(content: bytes) -> str | None:
    """按文件头猜格式：客户端把中文文件名编码坏（丢掉扩展名）时兜底。

    - ``%PDF-`` → PDF；
    - ``PK\\x03\\x04``（zip 容器）→ 含 ``word/`` 判 docx、含 ``xl/`` 判 xlsx；
    - 能按文本解码的 → 当 markdown 处理（标题、段落解析规则一致）。
    """
    head = content[:4096]
    if content[:5] == b"%PDF-":
        return ".pdf"
    if content[:4] == b"PK\x03\x04":
        if b"word/" in head:
            return ".docx"
        if b"xl/" in head:
            return ".xlsx"
        return None
    for encoding in ("utf-8", "gb18030"):
        try:
            content.decode(encoding)
        except UnicodeDecodeError:
            continue
        return ".md"
    return None


def decode_text(content: bytes) -> str:
    """按常见编码解码（中文文档有 GBK 存量），失败时用替换字符兜底。"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ 纯文本类


def _parse_markdown(content: bytes) -> ParsedDocument:
    """按标题层级维护标题栈，正文挂到当前标题路径下。"""
    document = ParsedDocument()
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    current_path = ""

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if text:
            document.blocks.append(ParsedBlock(text=text, heading_path=current_path))

    for raw_line in decode_text(content).splitlines():
        heading = _HEADING_RE.match(raw_line.strip())
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            stack = [item for item in stack if item[0] < level]
            stack.append((level, title))
            current_path = " > ".join(item[1] for item in stack)
            continue
        buffer.append(raw_line)
    flush()
    return document


def _parse_text(content: bytes) -> ParsedDocument:
    """纯文本按空行分段；没有标题信息，heading_path 留空。"""
    document = ParsedDocument()
    for block in re.split(r"\n\s*\n", decode_text(content)):
        text = block.strip()
        if text:
            document.blocks.append(ParsedBlock(text=text))
    return document


def _parse_csv(content: bytes) -> ParsedDocument:
    """CSV 按表头展开成 ``列名: 值`` 的行文本，每 50 行一块。"""
    document = ParsedDocument()
    rows = list(csv.reader(io.StringIO(decode_text(content))))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return document

    header = [cell.strip() or f"列{i + 1}" for i, cell in enumerate(rows[0])]
    lines: list[str] = []
    for row in rows[1:]:
        pairs = [
            f"{header[i] if i < len(header) else f'列{i + 1}'}: {cell.strip()}" for i, cell in enumerate(row)
        ]
        if pairs:
            lines.append("；".join(pairs))

    for start in range(0, len(lines), 50):
        document.blocks.append(ParsedBlock(text="\n".join(lines[start : start + 50])))
    return document


# ------------------------------------------------------------------ Office 类


def _parse_docx(content: bytes) -> ParsedDocument:
    """Word：优先用标题样式（Heading 1/2/…）建标题路径，正文与表格都抽。"""
    from docx import Document

    document = ParsedDocument()
    try:
        docx = Document(io.BytesIO(content))
    except Exception as exc:
        raise BusinessRuleError(f"docx 解析失败：{exc}") from exc

    stack: list[tuple[int, str]] = []
    current_path = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if text:
            document.blocks.append(ParsedBlock(text=text, heading_path=current_path))

    for paragraph in docx.paragraphs:
        text = paragraph.text.strip()
        style = (paragraph.style.name or "") if paragraph.style is not None else ""
        level = _heading_level(style)
        if level and text:
            flush()
            stack = [item for item in stack if item[0] < level]
            stack.append((level, text))
            current_path = " > ".join(item[1] for item in stack)
            continue
        if text:
            buffer.append(text)

    for table in docx.tables:
        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in table.rows
            if any(cell.text.strip() for cell in row.cells)
        ]
        if rows:
            flush()
            document.blocks.append(ParsedBlock(text="\n".join(rows), heading_path=current_path))
    flush()
    return document


def _heading_level(style_name: str) -> int:
    """把 ``Heading 2`` / ``标题 2`` 这类样式名解析成层级数字，非标题返回 0。"""
    match = re.match(r"^(?:heading|标题)\s*(\d)$", style_name.strip(), re.I)
    return int(match.group(1)) if match else 0


def _parse_xlsx(content: bytes) -> ParsedDocument:
    """Excel：每个工作表一块，行按 ``表头: 值`` 展开，heading_path 用表名。"""
    from openpyxl import load_workbook

    document = ParsedDocument()
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise BusinessRuleError(f"xlsx 解析失败：{exc}") from exc

    try:
        for sheet in workbook.worksheets:
            rows = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            rows = [row for row in rows if any(row)]
            if not rows:
                continue
            header = [cell or f"列{i + 1}" for i, cell in enumerate(rows[0])]
            lines = [
                "；".join(
                    f"{header[i] if i < len(header) else f'列{i + 1}'}: {cell}"
                    for i, cell in enumerate(row)
                    if cell
                )
                for row in rows[1:]
            ]
            text = "\n".join(line for line in lines if line) or " | ".join(header)
            document.blocks.append(ParsedBlock(text=text, heading_path=sheet.title))
    finally:
        workbook.close()
    return document


def _parse_pdf(content: bytes) -> ParsedDocument:
    """PDF：按页抽文本，页码记在 page_no 上，供引用定位。"""
    from pypdf import PdfReader

    document = ParsedDocument()
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise BusinessRuleError(f"pdf 解析失败：{exc}") from exc

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # 单页坏掉不影响整篇
            continue
        if text:
            document.blocks.append(ParsedBlock(text=text, page_no=index))
    return document


__all__ = ["SUPPORTED_SUFFIXES", "ParsedBlock", "ParsedDocument", "decode_text", "parse", "sniff_suffix"]
