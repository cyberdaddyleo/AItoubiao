"""通用网页解析：任意公告详情页的正文/标题/附件提取，自定义列表页的条目提取。

用于：全国公共资源交易平台详情页、各省市交易中心、企业自建采购平台、手动导入的链接。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..extract import classify_notice, extract_fields, extract_winners
from ..textutil import html_to_text, one_line, parse_datetime_cn

NOTICE_WORDS = re.compile(r"公告|招标|采购|磋商|谈判|询价|中标|成交|结果|比选|遴选|更正|澄清|候选人|单一来源|意向")
ATTACH_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip|rar|7z|wps)(\?|$)", re.I)


def _link_density(node) -> float:
    text = node.get_text(" ", strip=True)
    if not text:
        return 1.0
    links = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
    return links / max(len(text), 1)


def main_node(soup: BeautifulSoup):
    """找正文所在节点：文字最多、链接密度低的块。"""
    for sel in (
        "div.vF_detail_content",
        "#content", ".content", ".article", "article", ".detail", "#detail",
        ".ewb-article", ".news_content", ".con", ".main-text", ".TRS_Editor",
    ):
        n = soup.select_one(sel)
        if n and len(n.get_text(strip=True)) > 200:
            return n
    best, best_score = None, 0.0
    for n in soup.find_all(["div", "td", "section", "article"]):
        txt = n.get_text(" ", strip=True)
        if len(txt) < 150:
            continue
        ld = _link_density(n)
        if ld > 0.4:
            continue
        # 子节点越少越可能是正文容器本身；用文字量 × (1 - 链接密度)
        score = len(txt) * (1 - ld)
        depth_penalty = len(list(n.parents)) * 0.5
        score += depth_penalty
        if score > best_score:
            best, best_score = n, score
    return best or soup.body or soup


def parse_detail(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        t.decompose()
    title = ""
    for sel in ("h1", ".title", "h2", "h3"):
        n = soup.select_one(sel)
        if n and 6 <= len(n.get_text(strip=True)) <= 150:
            title = one_line(n.get_text())
            break
    if not title and soup.title:
        title = one_line(soup.title.get_text()).split("_")[0].split("-")[0]
    node = main_node(soup)
    text = html_to_text(node) if node else ""
    pub = ""
    m = re.search(r"(?:发布时间|发布日期|公告时间|信息时间)[:：]?\s*([0-9]{4}[^\n]{4,20})", soup.get_text(" "))
    if m:
        pub = parse_datetime_cn(m.group(1))
    attachments = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ATTACH_RE.search(href) or "download" in href.lower():
            att = {"name": a.get_text(strip=True) or href.rsplit("/", 1)[-1], "url": urljoin(url, href)}
            if att["url"].startswith("http") and att not in attachments:
                attachments.append(att)
    fields = extract_fields(text)
    return {
        "title": title,
        "publish_time": pub,
        "table": {},
        "content_text": text,
        "attachments": attachments[:20],
        "winners": extract_winners(text, soup),
        "fields": fields,
    }


def parse_list(html: str, page_url: str, cfg: dict) -> list[dict]:
    """自定义列表页：
    - item_selector：列表项 CSS 选择器（可选）；
    - link_pattern：详情链接 href 需要匹配的正则（可选）；
    - 不填时自动识别：标题含「公告/招标/采购…」且长度 ≥ 8 的链接。
    """
    soup = BeautifulSoup(html, "lxml")
    item_sel = (cfg.get("item_selector") or "").strip()
    pat = (cfg.get("link_pattern") or "").strip()
    link_re = re.compile(pat) if pat else None
    nodes = soup.select(item_sel) if item_sel else [a for a in soup.find_all("a", href=True)]
    items = []
    seen = set()
    for n in nodes:
        a = n if n.name == "a" else n.find("a", href=True)
        if not a or not a.get("href"):
            continue
        href = a["href"].strip()
        if href.startswith(("javascript", "#", "mailto")):
            continue
        if link_re and not link_re.search(href):
            continue
        title = one_line(a.get("title") or a.get_text(" ", strip=True))
        if not item_sel and not link_re:
            if len(title) < 8 or not NOTICE_WORDS.search(title):
                continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        ctx = n.get_text(" ", strip=True) if n.name != "a" else (a.parent.get_text(" ", strip=True) if a.parent else "")
        pub = parse_datetime_cn(ctx.replace(title, ""))
        items.append(
            {
                "url": url,
                "title": title,
                "publish_time": pub,
                "region": cfg.get("region", ""),
                "purchaser": "",
                "type_label": "",
                "notice_type": classify_notice(title),
            }
        )
    return items
