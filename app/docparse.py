"""招标文件 / 资料文件的文本提取：PDF、Word(.docx)、TXT/MD、HTML。

- PDF 按页提取，正文里插入「【第N页】」标记，便于定位条款出处；
- Word 按文档顺序提取段落和表格，表格转成「| 单元格 | 单元格 |」行，保留标题层级；
- 每页平均文字过少时提示「疑似扫描件」（扫描件需要先 OCR，V1 未内置 OCR）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .textutil import html_to_text, normalize_text


class UnsupportedFile(ValueError):
    pass


def extract(path: str | Path, filename: str = "") -> dict:
    p = Path(path)
    name = (filename or p.name).lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "pdf":
        return _pdf(p)
    if ext == "docx":
        return _docx(p)
    if ext in ("txt", "md", "csv"):
        raw = p.read_bytes()
        for enc in ("utf-8", "gb18030"):
            try:
                return {"text": normalize_text(raw.decode(enc)), "pages": 0, "tables": [], "warning": ""}
            except UnicodeDecodeError:
                continue
        return {"text": normalize_text(raw.decode("utf-8", "ignore")), "pages": 0, "tables": [], "warning": ""}
    if ext in ("html", "htm"):
        return {"text": html_to_text(p.read_text(encoding="utf-8", errors="ignore")), "pages": 0, "tables": [], "warning": ""}
    if ext == "doc":
        raise UnsupportedFile("暂不支持旧版 .doc，请用 Word/WPS 另存为 .docx 后上传")
    raise UnsupportedFile(f"暂不支持 .{ext} 文件，请上传 PDF、Word(.docx) 或文本文件")


def _pdf(p: Path) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(p))
    parts = []
    total_chars = 0
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 个别页面解析失败不影响整体
            t = ""
        t = normalize_text(t)
        total_chars += len(t)
        parts.append(f"【第{i}页】\n{t}")
    n = len(reader.pages)
    warning = ""
    if n and total_chars / n < 40:
        warning = "疑似扫描件（每页可识别文字很少），解析结果会不完整，建议先用 OCR 转成可复制文字的 PDF 再上传"
    return {"text": "\n\n".join(parts), "pages": n, "tables": [], "warning": warning}


def _docx(p: Path) -> dict:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(p))
    lines: list[str] = []
    tables: list[list[list[str]]] = []
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, d)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name if para.style is not None else "") or ""
            m = re.match(r"(?:Heading|标题)\s*(\d)", style)
            if m:
                lines.append("#" * int(m.group(1)) + " " + text)
            else:
                lines.append(text)
        elif tag == "tbl":
            tbl = Table(child, d)
            rows = []
            for row in tbl.rows:
                cells = []
                prev = None
                for c in row.cells:
                    # 合并单元格会重复出现，去重
                    if prev is not None and c._tc is prev:
                        continue
                    prev = c._tc
                    cells.append(re.sub(r"\s+", " ", c.text).strip())
                rows.append(cells)
            if rows:
                tables.append(rows)
                lines.append("")
                for r in rows:
                    lines.append("| " + " | ".join(r) + " |")
                lines.append("")
    text = normalize_text("\n".join(lines))
    return {"text": text, "pages": 0, "tables": tables, "warning": ""}
