"""中国政府采购网（www.ccgp.gov.cn）采集器。

2026-10-15 实测：
- 栏目列表页：http://www.ccgp.gov.cn/cggg/{zygg|dfgg}/{栏目}/，第 n 页为 index_{n-1}.htm（n≥2），
  每页 20 条，最多 25 页；列表项 <ul class="c_list_bid"><li><a title=完整标题> 发布时间/地域/采购人。
  汇总页 /cggg/dfgg/ 还带 <em rel="bxlx">公告类型</em>。
- 详情页：「公告概要」表 div.table（品目、采购单位、行政区域、开标时间、预算金额、代理机构、附件），
  正文 div.vF_detail_content；附件 a.bizDownload[id] → //download.ccgp.gov.cn/oss/download?uuid={id}。
- 搜索：http://search.ccgp.gov.cn/bxsearch?...（结果 ul.vT-srch-result-list-bid）。
  限频非常严格，短时间第二次请求就会返回「您的访问过于频繁」，因此只做低频补充。
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Callable, Iterator
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from ..config import now
from ..extract import classify_notice, extract_fields, extract_winners
from ..textutil import html_to_text, one_line, parse_datetime_cn
from .base import Fetcher, StopCrawl

BASE = "http://www.ccgp.gov.cn/cggg/"

# 栏目：verified=2026-10-15 实际打开核实过
CHANNELS: dict[str, dict] = {
    "zygg/gkzb": {"label": "中央·公开招标", "verified": True},
    "dfgg/gkzb": {"label": "地方·公开招标", "verified": True},
    "zygg/jzxcs": {"label": "中央·竞争性磋商", "verified": False},
    "dfgg/jzxcs": {"label": "地方·竞争性磋商", "verified": True},
    "zygg/jzxtp": {"label": "中央·竞争性谈判", "verified": False},
    "dfgg/jzxtp": {"label": "地方·竞争性谈判", "verified": False},
    "zygg/xjgg": {"label": "中央·询价", "verified": False},
    "dfgg/xjgg": {"label": "地方·询价", "verified": False},
    "dfgg/dylyly": {"label": "地方·单一来源", "verified": False},
    "zygg/gzgg": {"label": "中央·更正公告", "verified": True},
    "dfgg/gzgg": {"label": "地方·更正公告", "verified": True},
    "zygg/zbgg": {"label": "中央·中标公告", "verified": True},
    "dfgg/zbgg": {"label": "地方·中标公告", "verified": True},
    "dfgg/cjgg": {"label": "地方·成交公告", "verified": True},
    "dfgg/qtgg": {"label": "地方·其他公告", "verified": True},
}

DEFAULT_CHANNELS = ["zygg/gkzb", "dfgg/gkzb", "dfgg/jzxcs", "zygg/gzgg", "dfgg/gzgg", "zygg/zbgg", "dfgg/zbgg", "dfgg/cjgg"]

_CH_LABEL = {
    "gkzb": "公开招标",
    "jzxcs": "竞争性磋商",
    "jzxtp": "竞争性谈判",
    "xjgg": "询价公告",
    "dylyly": "单一来源",
    "gzgg": "更正公告",
    "zbgg": "中标公告",
    "cjgg": "成交公告",
    "qtgg": "其他公告",
    "fblbgg": "废标公告",
}


def list_url(channel: str, page: int) -> str:
    base = BASE + channel.strip("/") + "/"
    return base if page <= 1 else base + f"index_{page - 1}.htm"


# ---------------------------------------------------------------- 解析

def parse_list(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.select("ul.c_list_bid li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not re.search(r"t\d{8}_\d+\.htm", href):
            continue
        url = urljoin(page_url, href)
        title = (a.get("title") or a.get_text(strip=True)).strip()
        text = li.get_text(" ", strip=True)
        ems = li.find_all("em")
        label = ""
        bx = li.find("em", attrs={"rel": "bxlx"})
        if bx:
            label = bx.get_text(strip=True)
        else:
            m = re.search(r"/cggg/(?:zygg|dfgg)/([a-z]+)/", url)
            if m:
                label = _CH_LABEL.get(m.group(1), "")
        pub = ""
        region = ""
        purchaser = ""
        m = re.search(r"发布时间[:：]\s*([\d\-: ]{10,19})", text)
        if m:
            pub = parse_datetime_cn(m.group(1))
        m = re.search(r"地域[:：]\s*(\S+)", text)
        if m:
            region = m.group(1)
        m = re.search(r"采购人[:：]\s*(.+)$", text)
        if m:
            purchaser = m.group(1).strip()
        if not purchaser and ems:
            purchaser = ems[-1].get_text(strip=True)
        items.append(
            {
                "url": url,
                "title": one_line(title),
                "publish_time": pub,
                "region": region,
                "purchaser": purchaser,
                "type_label": label,
                "notice_type": classify_notice(title, label),
            }
        )
    return items


def parse_page_count(html: str) -> int:
    m = re.search(r"Pager\(\{\s*size\s*:\s*(\d+)", html)
    return int(m.group(1)) if m else 1


def parse_detail(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    h = soup.select_one(".vF_detail_header h2") or soup.find("h2")
    if h:
        title = one_line(h.get_text())
    if not title and soup.title:
        title = one_line(soup.title.get_text()).split("_")[0]
    pub = ""
    pt = soup.find(id="pubTime")
    if pt:
        pub = parse_datetime_cn(pt.get_text())

    table: dict[str, str] = {}
    attachments: list[dict] = []
    tdiv = soup.select_one("div.table")
    if tdiv:
        for tr in tdiv.find_all("tr"):
            tds = tr.find_all("td")
            i = 0
            while i < len(tds) - 1:
                if "title" in (tds[i].get("class") or []):
                    k = tds[i].get_text(strip=True)
                    v = tds[i + 1].get_text(" ", strip=True)
                    if k and k not in table:
                        table[k] = v
                    i += 2
                else:
                    i += 1
    for a in soup.select("a.bizDownload"):
        fid = (a.get("id") or "").strip()
        name = a.get_text(strip=True) or a.get("title") or "附件"
        if fid and fid != "null":
            att = {"name": name, "url": "http://download.ccgp.gov.cn/oss/download?uuid=" + fid}
            if att not in attachments:
                attachments.append(att)
    content_node = soup.select_one("div.vF_detail_content") or soup.select_one("div.vF_detail_main") or soup.body
    for a in content_node.find_all("a", href=True) if content_node else []:
        href = a["href"]
        if re.search(r"\.(pdf|docx?|xlsx?|zip|rar|7z)(\?|$)", href, re.I) or "/oss/download" in href:
            att = {"name": a.get_text(strip=True) or href.rsplit("/", 1)[-1], "url": urljoin(url or "http://www.ccgp.gov.cn/", href)}
            if att not in attachments:
                attachments.append(att)
    content_text = html_to_text(str(content_node)) if content_node else ""
    table_text = "\n".join(f"{k}：{v}" for k, v in table.items())
    full_text = (table_text + "\n\n" + content_text).strip()
    fields = extract_fields(full_text, table)
    winners = extract_winners(content_text, soup)
    return {
        "title": title,
        "publish_time": pub,
        "table": table,
        "content_text": full_text,
        "attachments": attachments,
        "winners": winners,
        "fields": fields,
    }


def parse_search(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "lxml")
    total = 0
    m = re.search(r"共找到\s*(?:<[^>]+>)?\s*(\d+)", html)
    if m:
        total = int(m.group(1))
    items = []
    for li in soup.select("ul.vT-srch-result-list-bid li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = one_line(a.get_text(" ", strip=True)).replace(" ", "")
        span = li.find("span")
        meta = span.get_text(" ", strip=True) if span else ""
        snippet = li.find("p").get_text(strip=True) if li.find("p") else ""
        pub = parse_datetime_cn(meta[:25])
        purchaser = ""
        agency = ""
        mm = re.search(r"采购人[:：]\s*([^|]+)", meta)
        if mm:
            purchaser = mm.group(1).strip()
        mm = re.search(r"代理机构[:：]\s*([^|]+)", meta)
        if mm:
            agency = mm.group(1).strip()
        strongs = [s.get_text(" ", strip=True) for s in li.find_all("strong")]
        label = strongs[0] if strongs else ""
        category = strongs[1] if len(strongs) > 1 else ""
        region = ""
        parts = [p.strip() for p in meta.split("|")]
        for i, p in enumerate(parts):
            if label and label in p and i + 1 < len(parts):
                region = parts[i + 1].split()[0] if parts[i + 1] else ""
                break
        items.append(
            {
                "url": a["href"].strip(),
                "title": title,
                "publish_time": pub,
                "purchaser": purchaser,
                "agency": agency,
                "type_label": label,
                "category": category,
                "region": region,
                "snippet": snippet,
                "notice_type": classify_notice(title, label),
            }
        )
    return items, total


def search_url(keyword: str, start_date: str, end_date: str, page: int = 1, bid_type: str = "1") -> str:
    """start_date / end_date: YYYY-MM-DD。bid_type=1 实测为「公开招标」类。"""
    st = start_date.replace("-", ":")
    et = end_date.replace("-", ":")
    return (
        "http://search.ccgp.gov.cn/bxsearch?searchtype=1&page_index="
        + str(page)
        + "&bidSort=0&pinMu=0&bidType="
        + quote(str(bid_type))
        + "&dbselect=bidx&kw="
        + quote(keyword)
        + "&start_time="
        + quote(st)
        + "&end_time="
        + quote(et)
        + "&timeType=2&pppStatus=0"   # 2 = 近一周（实测参数组合），结果按时间倒序
    )


# ---------------------------------------------------------------- 采集流程

def collect_list(
    cfg: dict,
    fetcher: Fetcher,
    is_known: Callable[[str], bool],
    log: Callable[[str], None],
) -> Iterator[dict]:
    """按栏目翻页采集；整页都是已采集过的公告或早于回看天数时停止翻页。"""
    channels = cfg.get("channels") or DEFAULT_CHANNELS
    max_pages = int(cfg.get("max_pages") or 25)
    lookback = int(cfg.get("lookback_days") or 3)
    interval = float(cfg.get("interval") or 4)
    oldest = (now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
    for ch in channels:
        pages_total = max_pages
        for page in range(1, max_pages + 1):
            if page > pages_total:
                break
            url = list_url(ch, page)
            try:
                html = fetcher.get(url, interval=interval)
            except StopCrawl:
                raise
            except Exception as e:  # noqa: BLE001
                log(f"[{ch}] 第{page}页抓取失败：{type(e).__name__} {e}")
                break
            if page == 1:
                pages_total = min(max_pages, parse_page_count(html))
            items = parse_list(html, url)
            if not items:
                log(f"[{ch}] 第{page}页没有解析到公告，停止该栏目")
                break
            known = 0
            too_old = 0
            for it in items:
                it["channel"] = ch
                if is_known(it["url"]):
                    known += 1
                if it["publish_time"] and it["publish_time"][:10] < oldest:
                    too_old += 1
                yield it
            if known == len(items):
                log(f"[{ch}] 第{page}页全部已采集，增量停止")
                break
            if too_old == len(items):
                log(f"[{ch}] 第{page}页均早于 {oldest}，停止")
                break


def collect_search(
    cfg: dict,
    fetcher: Fetcher,
    keywords: list[str],
    log: Callable[[str], None],
) -> Iterator[dict]:
    """关键词搜索（低频补充）。每个关键词只取第 1~N 页。"""
    days = int(cfg.get("days") or 2)
    pages = int(cfg.get("pages") or 1)
    interval = max(20.0, float(cfg.get("interval") or 30))
    bid_type = str(cfg.get("bid_type") or "1")
    end = now().strftime("%Y-%m-%d")
    start = (now() - timedelta(days=days)).strftime("%Y-%m-%d")
    limit = int(cfg.get("max_keywords") or 10)
    for kw in keywords[:limit]:
        for page in range(1, pages + 1):
            url = search_url(kw, start, end, page, bid_type)
            html = fetcher.get(url, interval=interval)
            items, total = parse_search(html)
            log(f"[搜索「{kw}」] 第{page}页 {len(items)} 条（共 {total} 条）")
            for it in items:
                it["search_keyword"] = kw
                yield it
            if len(items) < 20:
                break
