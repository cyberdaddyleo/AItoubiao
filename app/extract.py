"""公告字段的规则提取（不依赖 AI）。

从公告正文（以及中国政府采购网的「公告概要」表）里抽：项目编号、预算/最高限价、投标截止、开标时间、
获取文件截止、保证金、采购人、代理机构、地区、品目、联合体、是否专门面向中小企业、资格要求原文、
中标人与中标金额。抽不到的字段留空，由人工补充或 AI 补全。
"""
from __future__ import annotations

import re

from .textutil import find_all_dates, one_line, parse_amount_cn, parse_datetime_cn

# ---------------------------------------------------------------- 公告类型

TYPE_LABELS = {
    "tender": "招标/采购公告",
    "change": "更正/澄清",
    "award": "中标/成交结果",
    "fail": "废标/流标/终止",
    "intention": "采购意向",
    "other": "其他",
}

_TENDER_WORDS = ("公开招标", "邀请招标", "竞争性磋商", "竞争性谈判", "询价", "单一来源", "资格预审", "招标公告", "采购公告", "比选", "遴选", "谈判公告", "磋商公告", "询比")


def classify_notice(title: str, label: str = "") -> str:
    t = (title or "") + " " + (label or "")
    if "采购意向" in t or "意向公开" in t:
        return "intention"
    if re.search(r"废标|流标|终止公告|失败公告|暂停公告|结果（废标）|结果\(废标\)", t):
        return "fail"
    if re.search(r"更正|澄清|变更|延期|补充公告|答疑|暂停", t):
        return "change"
    if re.search(r"中标|成交|结果公告|候选人|结果公示|中选", t):
        return "award"
    if any(w in t for w in _TENDER_WORDS) or re.search(r"招标|采购.*公告|谈判|磋商|询价", t):
        return "tender"
    return "other"


# ---------------------------------------------------------------- 工具

def _after(label_pat: str, text: str, max_len: int = 120) -> str:
    """取「标签：内容」中的内容（到行尾）。"""
    m = re.search(r"(?:" + label_pat + r")\s*[:：]\s*([^\n]{1," + str(max_len) + r"})", text)
    return m.group(1).strip() if m else ""


def _clean_name(v: str) -> str:
    v = re.split(r"\s{2,}|地\s*址|联系|电\s*话|[，,；;]", v)[0]
    return v.strip(" 　:：。")


def _section(text: str, start_pat: str, end_pat: str, max_len: int = 3000) -> str:
    m = re.search(start_pat, text)
    if not m:
        return ""
    rest = text[m.end():]
    e = re.search(end_pat, rest)
    seg = rest[: e.start()] if e else rest[:max_len]
    return seg.strip()[:max_len]


# ---------------------------------------------------------------- 主函数

def extract_fields(text: str, table: dict | None = None) -> dict:
    """返回字段字典；table 是「公告概要」表格的 {标签: 值}。"""
    table = table or {}
    t = text or ""
    out: dict = {}

    # 项目编号
    pn = _after(r"项目编号|招标编号|采购编号|项目代码|采购项目编号|招标项目编号|标段编号", t, 80)
    if pn:
        pn = re.split(r"[\s　）)，,；;]", pn.strip("（(【["))[0]
        out["project_no"] = pn.strip("。")
    # 项目名称
    name = table.get("采购项目名称") or _after(r"项目名称|采购项目名称|招标项目名称", t, 150)
    if name:
        out["project_name"] = one_line(name).strip("。")

    # 预算/最高限价
    budget_src = (
        table.get("预算金额")
        or _after(r"预算金额|采购预算|项目预算|最高限价|招标控制价|最高投标限价|控制价|采购预算金额", t, 80)
    )
    b = parse_amount_cn(budget_src) if budget_src else None
    if b:
        out["budget"] = b

    # 截止时间（优先正文的精确时间）
    dl_src = _after(
        r"提交投标文件截止时间|投标截止时间|递交投标文件截止时间|响应文件提交截止时间|提交响应文件截止时间|"
        r"首次响应文件提交截止时间|投标文件递交截止时间|递交响应文件截止时间|报价截止时间|截止时间",
        t,
        80,
    )
    dl = parse_datetime_cn(dl_src)
    if not dl:
        m = re.search(r"于\s*(\d{4}年\d{1,2}月\d{1,2}日[^前]{0,20})前递交", t)
        if m:
            dl = parse_datetime_cn(m.group(1))
    if dl:
        out["deadline"] = dl

    ot = parse_datetime_cn(table.get("开标时间") or _after(r"开标时间|开启时间|开标日期", t, 80))
    if ot:
        out["open_time"] = ot
    if not out.get("deadline") and ot:
        out["deadline"] = ot

    # 获取招标文件截止
    get_src = table.get("获取招标文件时间") or _section(t, r"获取(?:招标|采购|磋商|谈判|询价)?文件", r"\n[一二三四五六七八九十]+、|\n地点", 300)
    dates = find_all_dates(get_src or "")
    if len(dates) >= 2:
        out["doc_get_end"] = dates[1]
    elif len(dates) == 1 and "至" not in (get_src or ""):
        out["doc_get_end"] = dates[0]

    # 保证金
    bond = _after(r"投标保证金|保证金金额|磋商保证金|响应保证金|保证金", t, 100)
    if bond:
        out["bond_text"] = bond[:100]

    # 采购人 / 代理机构
    purchaser = table.get("采购单位") or ""
    if not purchaser:
        sec = _section(t, r"采购人信息", r"\n\s*2[\.．、]|代理机构信息", 300)
        purchaser = _clean_name(_after(r"名\s*称", sec, 60)) if sec else ""
    if not purchaser:
        purchaser = _clean_name(_after(r"采购人|采购单位|招标人|建设单位", t, 60))
    if purchaser:
        out["purchaser"] = purchaser
    agency = table.get("代理机构名称") or ""
    if not agency:
        sec = _section(t, r"采购代理机构信息|代理机构信息", r"\n\s*3[\.．、]|项目联系方式", 300)
        agency = _clean_name(_after(r"名\s*称", sec, 60)) if sec else ""
    if not agency:
        agency = _clean_name(_after(r"采购代理机构|招标代理机构|代理机构", t, 60))
    if agency:
        out["agency"] = agency

    if table.get("行政区域"):
        out["region"] = table["行政区域"]
    if table.get("品目"):
        out["category"] = table["品目"]

    # 联合体
    m = re.search(r"\(\s*(不接受|接受)\s*\)\s*联合体|（\s*(不接受|接受)\s*）\s*联合体|(不接受|接受)联合体", t)
    if m:
        out["joint_venture"] = next(g for g in m.groups() if g)
    if re.search(r"专门面向中小企业|专门面向小微企业|预留.*中小企业", t):
        out["sme_only"] = 1

    # 资格要求原文
    q = _section(
        t,
        r"(?:申请人|投标人|供应商|响应人|竞买人)的?资格要求[:：]?|资格要求[:：]|投标人资格条件[:：]?",
        r"\n\s*[三四五六七八九十]+、|获取(?:招标|采购|磋商|谈判|询价)?文件",
        2500,
    )
    if q:
        out["qualification_text"] = q

    # 中标信息
    amt_src = table.get("总中标金额") or _after(r"总中标金额|中标金额|成交金额|中标（成交）金额|中标价", t, 80)
    if amt_src:
        a = parse_amount_cn(amt_src)
        if a:
            out["award_amount"] = a
    return out


def extract_winners(text: str, soup=None) -> list[dict]:
    """中标/成交供应商。优先用 ccgp 结构化表格单元格，其次用正文规则。"""
    winners: list[dict] = []
    if soup is not None:
        names = soup.select("td.code-winningSupplierName")
        prices = soup.select("td.code-summaryPrice")
        scores = soup.select("td.code-reviewTotalScore")
        for i, n in enumerate(names):
            w = {"name": n.get_text(strip=True)}
            if i < len(prices):
                w["amount_text"] = prices[i].get_text(strip=True)
                w["amount"] = parse_amount_cn(w["amount_text"])
            if i < len(scores):
                try:
                    w["score"] = float(scores[i].get_text(strip=True))
                except ValueError:
                    pass
            if w["name"]:
                winners.append(w)
    if winners:
        return winners
    for pat in (
        r"中标（成交）供应商名称[:：]\s*([^\n]{2,60})",
        r"中标供应商名称[:：]\s*([^\n]{2,60})",
        r"成交供应商名称[:：]\s*([^\n]{2,60})",
        r"中标人(?:名称)?[:：]\s*([^\n]{2,60})",
        r"中标单位[:：]\s*([^\n]{2,60})",
        r"成交人[:：]\s*([^\n]{2,60})",
        r"供应商名称[:：]\s*([^\n]{2,60})",
    ):
        for m in re.finditer(pat, text or ""):
            name = _clean_name(m.group(1))
            if name and len(name) >= 4 and not any(w["name"] == name for w in winners):
                winners.append({"name": name})
        if winners:
            break
    # ccgp 表格转文本后的形式：「1 | 公司名 | 地址 | 金额 | 得分」
    if not winners:
        for m in re.finditer(r"\n\s*\d+\s*\|\s*([^|\n]{4,60}(?:公司|集团|研究院|中心|厂|事务所|院|所|站))\s*\|", text or ""):
            winners.append({"name": m.group(1).strip()})
    return winners[:10]
