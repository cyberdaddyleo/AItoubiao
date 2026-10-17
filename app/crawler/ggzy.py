"""全国公共资源交易平台（www.ggzy.gov.cn）采集器 —— 实验性。

2026-10-15 实测：
- 旧地址 deal.ggzy.gov.cn 已返回 502，交易列表迁到 https://www.ggzy.gov.cn/deal/dealList.html；
- 列表数据接口：POST /information/pubTradingInfo/getTradList（表单），
  参数 SOURCE_TYPE、DEAL_TIME、DEAL_CLASSIFY、DEAL_STAGE、DEAL_PROVINCE、TIMEBEGIN、TIMEEND、FINDTXT、PAGENUMBER 等；
- 返回 code：200 正常（data.records：url/title/publishTime/provinceText/transactionSourcesPlatformText/
  businessTypeText/informationTypeText/industryTypeText）；800「操作过于频繁」；829 需要图形验证码；804 需细化条件。
- 该站限频很严。系统遇到 800/829 立即停止并暂停该来源，绝不识别或绕过验证码。
- 详情页结构未能在限频下核实，使用通用正文提取。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, Iterator
from urllib.parse import urljoin

from ..config import now
from ..extract import classify_notice
from ..textutil import one_line, parse_datetime_cn
from .base import Fetcher, StopCrawl

API = "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList"
REFERER = "https://www.ggzy.gov.cn/deal/dealList.html"

CLASSIFY = {"01": "工程建设", "02": "政府采购", "03": "土地使用权", "04": "矿业权", "05": "国有产权", "90": "其他"}


def parse_records(data: dict) -> list[dict]:
    recs = ((data or {}).get("data") or {}).get("records") or []
    items = []
    for r in recs:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        url = urljoin("https://www.ggzy.gov.cn/", url)
        title = one_line(r.get("title") or "")
        label = r.get("informationTypeText") or ""
        items.append(
            {
                "url": url,
                "title": title,
                "publish_time": parse_datetime_cn(r.get("publishTime") or ""),
                "region": r.get("provinceText") or "",
                "purchaser": "",
                "type_label": label,
                "category": " / ".join(x for x in (r.get("businessTypeText"), r.get("industryTypeText")) if x),
                "platform": r.get("transactionSourcesPlatformText") or "",
                "notice_type": classify_notice(title, label),
            }
        )
    return items


def collect(cfg: dict, fetcher: Fetcher, is_known: Callable[[str], bool], log: Callable[[str], None]) -> Iterator[dict]:
    pages = int(cfg.get("max_pages") or 3)
    interval = max(15.0, float(cfg.get("interval") or 30))
    days = int(cfg.get("days") or 1)
    classify_list = cfg.get("classify") or ["02", "01"]
    keyword = (cfg.get("keyword") or "").strip()
    end = now().strftime("%Y-%m-%d")
    begin = (now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for cl in classify_list:
        for page in range(1, pages + 1):
            # DEAL_TIME 与 SOURCE_TYPE 取页面实测默认值（'02' / '1'）；指定时间段时才传日期
            form = {
                "SOURCE_TYPE": "1",
                "DEAL_TIME": str(cfg.get("deal_time") or "02"),
                "DEAL_CLASSIFY": cl,
                "PAGENUMBER": str(page),
            }
            if cfg.get("use_date_range"):
                form["TIMEBEGIN"] = begin
                form["TIMEEND"] = end
            if keyword:
                form["FINDTXT"] = keyword
            r = fetcher.post_form(
                API,
                form,
                headers={"Referer": REFERER, "X-Pass-Token": "", "Content-Type": "application/x-www-form-urlencoded"},
                interval=interval,
            )
            try:
                data = r.json()
            except ValueError:
                raise StopCrawl("全国公共资源交易平台返回的不是数据（可能被拦截），已停止")
            code = data.get("code")
            if code == 800:
                raise StopCrawl("全国公共资源交易平台提示「操作过于频繁」，已停止并暂停该来源")
            if code == 829:
                raise StopCrawl("全国公共资源交易平台要求输入验证码，系统不会自动识别验证码，已暂停该来源；可降低频率或改用人工导入")
            if code == 804:
                log(f"[{CLASSIFY.get(cl, cl)}] 平台要求细化查询条件，跳过")
                break
            if code != 200:
                log(f"[{CLASSIFY.get(cl, cl)}] 平台返回 {code}：{data.get('message')}")
                break
            items = parse_records(data)
            log(f"[{CLASSIFY.get(cl, cl)}] 第{page}页 {len(items)} 条")
            if not items:
                break
            known = 0
            for it in items:
                if is_known(it["url"]):
                    known += 1
                yield it
            if known == len(items):
                break
