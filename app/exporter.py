"""导出 Word：投标文件 / 投标准备清单。

- 封面、目录（Word 打开后右键「更新域」即可生成页码）、页眉项目名称、页脚页码；
- 正文字体宋体小四、标题黑体，1.5 倍行距；表格带边框；资料库里的证书图片自动插入；
- 文件属性（作者、单位、标题）写入本投标人自己的信息，不残留模板或他人的属性。
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from . import db
from .config import EXPORT_DIR, now
from .writer import doc_full, project_context

BODY_FONT = "宋体"
HEAD_FONT = "黑体"


def _set_font(run, name: str, size: float | None = None, bold: bool | None = None, color: RGBColor | None = None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rfonts.set(qn(attr), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _style_doc(d: Document):
    st = d.styles["Normal"]
    st.font.name = BODY_FONT
    st.font.size = Pt(12)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    pf = st.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(0)
    for lvl, size in ((1, 16), (2, 14), (3, 13), (4, 12)):
        hs = d.styles[f"Heading {lvl}"]
        hs.font.name = HEAD_FONT
        hs.font.size = Pt(size)
        hs.font.bold = True
        hs.font.color.rgb = RGBColor(0, 0, 0)
        rpr = hs.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.append(rf)
        for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
            rf.set(qn(attr), HEAD_FONT)
        hs.paragraph_format.space_before = Pt(12)
        hs.paragraph_format.space_after = Pt(6)
    sec = d.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = sec.bottom_margin = Cm(2.5)


def _field(paragraph, instr: str, placeholder: str = ""):
    run = paragraph.add_run()
    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    i = OxmlElement("w:instrText")
    i.set(qn("xml:space"), "preserve")
    i.text = instr
    s = OxmlElement("w:fldChar")
    s.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t")
    t.text = placeholder
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    run._r.append(b)
    run._r.append(i)
    run._r.append(s)
    run._r.append(t)
    run._r.append(e)
    return run


def _header_footer(section, header_text: str, page_numbers: bool = True):
    section.header.is_linked_to_previous = False
    section.footer.is_linked_to_previous = False
    hp = section.header.paragraphs[0]
    hp.text = ""
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if header_text:
        r = hp.add_run(header_text)
        _set_font(r, BODY_FONT, 9)
    fp = section.footer.paragraphs[0]
    fp.text = ""
    if not page_numbers:
        return
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = fp.add_run("第 ")
    _set_font(r1, BODY_FONT, 9)
    _field(fp, "PAGE", "1")
    r2 = fp.add_run(" 页")
    _set_font(r2, BODY_FONT, 9)


def _add_inline(paragraph, text: str, size: float | None = None):
    """支持 **加粗** 与【待补充】高亮。"""
    parts = re.split(r"(\*\*[^*]+\*\*|【待补充[^】]*】)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            _set_font(run, BODY_FONT, size, bold=True)
        elif part.startswith("【待补充"):
            run = paragraph.add_run(part)
            _set_font(run, BODY_FONT, size, bold=True, color=RGBColor(0xC0, 0x00, 0x00))
            run.font.highlight_color = 7  # 黄色高亮
        else:
            run = paragraph.add_run(part)
            _set_font(run, BODY_FONT, size)


def _add_table(d: Document, rows: list[list[str]]):
    if not rows:
        return
    ncol = max(len(r) for r in rows)
    t = d.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # 列宽按内容长度分配（总宽 16cm），避免金额、编号被折行
    weights = []
    for j in range(ncol):
        longest = max((len(r[j]) if j < len(r) else 0) for r in rows)
        weights.append(min(max(longest, 2), 28))
    total = sum(weights) or 1
    widths = [Cm(max(1.1, 15.4 * w / total)) for w in weights]
    for j, col in enumerate(t.columns):
        col.width = widths[j]
    for i, r in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.width = widths[j]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.2
            _add_inline(p, r[j] if j < len(r) else "", 10.5)
            if i == 0:
                for run in p.runs:
                    run.font.bold = True
    d.add_paragraph()


def _add_image(d: Document, file_id: int, caption: str):
    f = db.get("files", file_id)
    if not f or not Path(f["stored_path"]).exists():
        p = d.add_paragraph()
        _add_inline(p, f"【待补充：图片「{caption}」文件缺失】")
        return
    try:
        d.add_picture(f["stored_path"], width=Cm(14))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cp = d.add_paragraph()
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cp.add_run(caption)
        _set_font(r, BODY_FONT, 10)
    except Exception:  # noqa: BLE001 - 不支持的图片格式
        p = d.add_paragraph()
        _add_inline(p, f"【待补充：图片「{caption}」无法插入，请手动放入】")


def render_markdown_lite(d: Document, text: str, base_level: int):
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if not s:
            i += 1
            continue
        if s == "[[分页]]":
            d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip().strip("|")
                cells = [c.strip() for c in row.split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            _add_table(d, rows)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = min(4, base_level + len(m.group(1)))
            d.add_heading(m.group(2).strip(), level=lvl)
            i += 1
            continue
        m = re.match(r"^!\[([^\]]*)\]\(file:(\d+)\)$", s)
        if m:
            _add_image(d, int(m.group(2)), m.group(1))
            i += 1
            continue
        if s.startswith(("- ", "* ", "• ")):
            p = d.add_paragraph(style="List Bullet")
            _add_inline(p, s[2:].strip())
            i += 1
            continue
        if s.startswith("> "):
            p = d.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            _add_inline(p, s[2:])
            for r in p.runs:
                r.font.italic = True
            i += 1
            continue
        p = d.add_paragraph()
        if not re.match(r"^(\d+[\.．、]|[（(]\d+[)）]|致：|投标人|法定代表人|日期|委托|代理人|企业名称|项目名称|投标总价)", s):
            p.paragraph_format.first_line_indent = Pt(24)
        _add_inline(p, s)
        i += 1


def export_bid_doc(doc_id: int, *, include_unfinished: bool = True) -> Path:
    doc = doc_full(doc_id)
    ctx = project_context(doc["project_id"])
    p, comp = ctx["project"], ctx["company"]
    d = Document()
    _style_doc(d)
    cp = d.core_properties
    cp.author = comp.get("name") or ""
    cp.last_modified_by = comp.get("name") or ""
    cp.title = (p.get("name") or "") + " 投标文件"
    cp.subject = p.get("project_no") or ""
    cp.comments = ""
    cp.keywords = ""
    cp.category = ""

    # 封面
    for _ in range(5):
        d.add_paragraph()
    t = d.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(t.add_run(p.get("name") or "项目名称"), HEAD_FONT, 22, bold=True)
    if p.get("project_no"):
        t2 = d.add_paragraph()
        t2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_font(t2.add_run("项目编号：" + p["project_no"]), BODY_FONT, 14)
    d.add_paragraph()
    t3 = d.add_paragraph()
    t3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(t3.add_run("投 标 文 件"), HEAD_FONT, 36, bold=True)
    for _ in range(6):
        d.add_paragraph()
    for label, val in (("投标人", (comp.get("name") or "") + "（盖章）"), ("法定代表人或授权代表", "（签字）"), ("日期", "      年    月    日")):
        pp = d.add_paragraph()
        pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_font(pp.add_run(f"{label}：{val}"), BODY_FONT, 14)
    _header_footer(d.sections[0], "", page_numbers=False)

    # 目录
    sec = d.add_section(WD_SECTION.NEW_PAGE)
    _header_footer(sec, (p.get("name") or "") + " · 投标文件")
    h = d.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(h.add_run("目  录"), HEAD_FONT, 16, bold=True)
    tp = d.add_paragraph()
    _field(tp, 'TOC \\o "1-2" \\h \\z \\u', "（在 Word 中右键此处选择「更新域」生成目录和页码）")

    # 正文
    first = True
    for s in doc["sections"]:
        if s["level"] == 1:
            if not first:
                d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            else:
                d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            first = False
        d.add_heading(s["title"], level=min(3, s["level"]))
        render_markdown_lite(d, s["content"] or "", s["level"])

    safe = re.sub(r'[\\/:*?"<>|]', "_", (p.get("name") or "投标文件"))[:60]
    out = EXPORT_DIR / f"{safe}_投标文件_{now().strftime('%Y%m%d%H%M%S')}.docx"
    d.save(str(out))
    return out


def export_checklist(project_id: int, checklist: list[dict], key_info: dict | None = None) -> Path:
    ctx = project_context(project_id)
    p, comp = ctx["project"], ctx["company"]
    d = Document()
    _style_doc(d)
    d.core_properties.author = comp.get("name") or ""
    d.core_properties.title = (p.get("name") or "") + " 投标准备清单"
    d.add_heading((p.get("name") or "") + " — 投标准备与投递清单", level=1)
    info = [
        ["投标主体", comp.get("name") or ""],
        ["项目编号", p.get("project_no") or ""],
        ["采购人", p.get("purchaser") or ""],
        ["投标截止", p.get("deadline") or (key_info or {}).get("deadline", "")],
        ["开标时间", p.get("open_time") or (key_info or {}).get("open_time", "")],
        ["保证金", (key_info or {}).get("bond") or (f"{p['bond_amount']:,.2f} 元" if p.get("bond_amount") else "")],
        ["投递平台", p.get("platform_name") or (key_info or {}).get("platform", "")],
        ["CA 证书持有人", p.get("ca_holder") or ""],
    ]
    _add_table(d, [["项目", "内容"]] + info)
    d.add_heading("一、材料清单", level=2)
    rows = [["□", "类别", "要求", "状态", "对应资料", "备注"]]
    for it in checklist:
        rows.append(
            [
                "□",
                it.get("group", ""),
                it.get("requirement", "")[:120],
                it.get("status", ""),
                "、".join(x["name"] for x in it.get("items") or [])[:120],
                it.get("note", ""),
            ]
        )
    _add_table(d, rows)
    d.add_heading("二、投递前确认", level=2)
    for line in (
        "标书已通过系统「合规体检」，必须修改项为 0",
        "报价表合计、投标函金额、大写金额三者一致，且不超过最高限价",
        "所有需签字盖章的位置已签字盖章（电子投标已用 CA 电子签章）",
        "投标文件已按平台要求生成加密文件并成功上传，已截图保存回执",
        "投标保证金已按要求缴纳（如需），并保存缴纳凭证",
        "纸质投标：正副本份数、密封、标记符合要求",
    ):
        pp = d.add_paragraph()
        _add_inline(pp, "□ " + line)
    safe = re.sub(r'[\\/:*?"<>|]', "_", (p.get("name") or "项目"))[:60]
    out = EXPORT_DIR / f"{safe}_投标清单_{now().strftime('%Y%m%d%H%M%S')}.docx"
    d.save(str(out))
    return out
