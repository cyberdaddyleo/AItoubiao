"""招标文件解析：资格要求、评分标准、废标/否决条款、★实质性条款、文件组成与格式、关键时间，
以及「投标准备清单」（每一项要什么材料、资料库里有没有）。

两条路径：
- 规则解析：不依赖 AI，基于章节/关键词/表格规则，速度快、结果偏粗；
- AI 解析：参考 BidCraft 的做法，按「资格、评分、废标、格式、时间」分专项抽取，
  每个专项只喂相关的关键词窗口，结果更全、更准，并给出可向招标人澄清的问题。
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from . import db, llm, matching
from .extract import extract_fields
from .textutil import parse_amount_cn, parse_datetime_cn

PAGE_RE = re.compile(r"【第(\d+)页】")
HEADING_RE = re.compile(r"^(?:#+\s*)?(?:第[一二三四五六七八九十百\d]+[章节部分篇]|[一二三四五六七八九十]+、)")


def _lines_with_page(text: str) -> list[tuple[int, str]]:
    page = 0
    out = []
    for raw in (text or "").split("\n"):
        m = PAGE_RE.match(raw.strip())
        if m:
            page = int(m.group(1))
            continue
        line = raw.strip()
        if line:
            out.append((page, line))
    return out


def _src(page: int) -> str:
    return f"第{page}页" if page else ""


_CN_NUM = "一二三四五六七八九十百"


def _heading_level(line: str) -> int | None:
    """标题层级：# 号数 / 第X章(1) / 第X节(2) / 一、(3) / （一）(4)；不是标题返回 None。"""
    m = re.match(r"^(#+)\s", line)
    if m:
        return len(m.group(1))
    if re.match(rf"^第[{_CN_NUM}\d]+[章部篇]|^第[{_CN_NUM}\d]+部分", line):
        return 1
    if re.match(rf"^第[{_CN_NUM}\d]+节", line):
        return 2
    if re.match(rf"^[{_CN_NUM}]+、", line):
        return 3
    if re.match(rf"^[（(][{_CN_NUM}]+[)）]", line):
        return 4
    return None


def _section_lines(lines: list[tuple[int, str]], start_pat: str, max_lines: int = 80) -> list[tuple[int, str]]:
    """找到标题匹配 start_pat 的章节，取到下一个同级或更高级标题为止（取所有匹配处中最长的一段）。"""
    best: list[tuple[int, str]] = []
    rx = re.compile(start_pat)
    for i, (_, line) in enumerate(lines):
        if not rx.search(line) or len(line) > 60:
            continue
        start_level = _heading_level(line) or 3
        seg = []
        for j in range(i + 1, min(len(lines), i + 1 + max_lines)):
            pg, ln = lines[j]
            lv = _heading_level(ln)
            if lv is not None and lv <= start_level:
                break
            seg.append((pg, ln))
        # 跳过目录页里的命中（目录行通常带「……12」页码）
        if seg and sum(1 for _, ln in seg[:8] if re.search(r"[.…·]{3,}\s*\d+$", ln)) >= 3:
            continue
        if len(seg) > len(best):
            best = seg
    return best


# ---------------------------------------------------------------- 规则解析

def rule_analyze(text: str, tables: list | None = None) -> dict:
    lines = _lines_with_page(text)
    out: dict = {"by": "rule", "warnings": []}

    # 资格要求
    q_lines = _section_lines(lines, r"(?:投标人|供应商|申请人|响应人)?资格(?:要求|条件|审查标准)|合格(?:投标人|供应商)")
    quals = []
    for pg, ln in q_lines:
        if len(ln) < 6:
            continue
        if re.match(r"^[\(（]?\d+[\)）\.．、]|^[①②③④⑤⑥⑦⑧⑨⑩]|^[（(][一二三四五六七八九十]+[）)]", ln) or matching.REQ_HINT.search(ln):
            quals.append({"text": ln[:300], "type": _qual_type(ln), "source": _src(pg)})
    out["qualifications"] = quals[:40]

    # 评分标准：表格优先
    scoring = []
    for tbl in tables or []:
        if not tbl:
            continue
        header = " ".join(tbl[0])
        if not re.search(r"分值|评分|得分|权重|评审因素|评分标准", header):
            continue
        hi = {h: i for i, h in enumerate(tbl[0])}
        wcol = next((i for h, i in hi.items() if re.search(r"分值|权重|满分|分数", h)), None)
        icol = next((i for h, i in hi.items() if re.search(r"评审因素|评分项|评审项|评分因素|项目|内容", h)), 0)
        ccol = next((i for h, i in hi.items() if re.search(r"标准|细则|评分办法|评审标准|说明", h)), None)
        for row in tbl[1:]:
            if not row or len(row) <= icol:
                continue
            item = row[icol]
            w = None
            if wcol is not None and wcol < len(row):
                m = re.search(r"(\d+(?:\.\d+)?)", row[wcol])
                w = float(m.group(1)) if m else None
            crit = row[ccol] if ccol is not None and ccol < len(row) else " ".join(row)
            if not item or item in ("合计", "总分", "小计"):
                continue
            scoring.append({"item": item[:60], "weight": w, "criteria": crit[:500], "category": _score_cat(item + crit), "source": "评分表"})
    if not scoring:
        for pg, ln in lines:
            m = re.search(r"^(?:[\(（]?\d+[\)）\.．、]\s*)?([一-龥A-Za-z、及与和（）()]{2,30}?)[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]", ln)
            if m and not re.search(r"总分|满分|合计", m.group(1)):
                scoring.append({"item": m.group(1), "weight": float(m.group(2)), "criteria": ln[:500], "category": _score_cat(ln), "source": _src(pg)})
    # 去重
    seen = set()
    uniq = []
    for s in scoring:
        k = s["item"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    out["scoring"] = uniq[:60]

    # 废标/否决
    rejects = []
    for pg, ln in lines:
        if re.search(r"否决|无效投标|投标无效|作无效处理|按无效|废标|不予受理|拒绝|重大偏差|不得进入|视为无效|无效响应|响应无效", ln) and len(ln) >= 8:
            rejects.append({"text": ln[:300], "starred": "★" in ln, "tip": "", "source": _src(pg)})
    out["reject_clauses"] = rejects[:80]
    out["starred"] = [{"text": ln[:300], "source": _src(pg)} for pg, ln in lines if "★" in ln][:80]

    # 投标文件组成 / 格式
    toc = []
    t_lines = _section_lines(lines, r"投标文件(?:的)?(?:组成|格式|构成|内容)|响应文件(?:的)?(?:组成|格式|构成)|投标文件应包括", 120)
    for pg, ln in t_lines:
        if re.search(r"[.…·]{3,}\s*\d+$", ln):
            ln = re.sub(r"[.…·]{3,}\s*\d+$", "", ln)
        m = re.match(r"^(?:[（(]?[一二三四五六七八九十\d]+[）)\.．、]|第[一二三四五六七八九十]+[部分章])\s*(.{2,40})$", ln)
        if m:
            title = m.group(1).strip("：:；;。")
            if title and title not in toc and not re.search(r"截止时间|开标时间|开标地点|递交地点|时间[:：]|地点[:：]", title):
                toc.append(title)
    forms = []
    for pg, ln in lines:
        for name in ("投标函", "法定代表人身份证明", "授权委托书", "开标一览表", "报价一览表", "分项报价表", "技术偏离表",
                     "商务偏离表", "资格证明文件", "中小企业声明函", "承诺函", "响应函", "报价表", "偏离表", "服务方案", "技术方案"):
            if name in ln and not any(name in f or f in name for f in forms):
                forms.append(name)
    out["format"] = {"toc": toc[:40], "required_forms": forms, "rules": _format_rules(lines)}

    # 关键时间与保证金
    f = extract_fields(text)
    key = {
        "deadline": f.get("deadline", ""),
        "open_time": f.get("open_time", ""),
        "bond": f.get("bond_text", ""),
        "bond_amount": parse_amount_cn(f.get("bond_text", "")) if f.get("bond_text") else None,
    }
    for pg, ln in lines:
        if not key.get("clarify_deadline") and re.search(r"(澄清|疑问|质疑|答疑).{0,12}(截止|之前|前)", ln):
            v = parse_datetime_cn(ln)
            if v:
                key["clarify_deadline"] = v
        if not key.get("validity") and "有效期" in ln and "投标" in ln:
            m = re.search(r"(\d+)\s*(日历天|天|日)", ln)
            if m:
                key["validity"] = m.group(1) + m.group(2)
        if not key.get("bond_deadline") and "保证金" in ln and re.search(r"截止|前.{0,4}(缴纳|到账|递交)", ln):
            v = parse_datetime_cn(ln)
            if v:
                key["bond_deadline"] = v
    out["key_info"] = key
    out["clarify_questions"] = []
    out["summary"] = ""
    if not quals:
        out["warnings"].append("没有识别到资格要求章节，请人工确认或使用 AI 解析")
    if not out["scoring"]:
        out["warnings"].append("没有识别到评分标准，请人工确认或使用 AI 解析")
    return out


def _format_rules(lines: list[tuple[int, str]]) -> list[str]:
    rules = []
    for pg, ln in lines:
        if re.search(r"正本|副本|份数|装订|胶装|密封|签字|盖章|骑缝|页码|电子投标文件|加密|CA|暗标|字体|字号", ln) and 8 <= len(ln) <= 200:
            if ln not in rules:
                rules.append(ln)
    return rules[:30]


def _qual_type(t: str) -> str:
    if re.search(r"业绩|案例|合同", t):
        return "业绩"
    if re.search(r"项目经理|建造师|工程师|人员|负责人|社保|职称", t):
        return "人员"
    if re.search(r"财务|审计|注册资本|营业收入|资产", t):
        return "财务"
    if re.search(r"信用|失信|违法|处罚|纳税|社会保障", t):
        return "信誉"
    if re.search(r"资质|证书|许可|认证|等级|备案", t):
        return "资质"
    return "其他"


def _score_cat(t: str) -> str:
    if re.search(r"价格|报价|投标价|评标价", t):
        return "价格"
    if re.search(r"业绩|资质|信誉|证书|财务|认证|荣誉|获奖|企业实力|诚信|社保|人员资格", t):
        return "商务"
    return "技术"


# ---------------------------------------------------------------- AI 解析

def _windows(text: str, keywords: str, head_chars: int = 15000, n_windows: int = 14, window: int = 2400, max_input: int = 45000) -> str:
    """文档开头 + 关键词上下文窗口（在全文均匀采样，避开只命中目录页）。"""
    head = text[:head_chars]
    ms = list(re.finditer(keywords, text))
    if len(ms) > n_windows:
        step = len(ms) / n_windows
        ms = [ms[min(int(i * step), len(ms) - 1)] for i in range(n_windows)]
    segs = []
    for m in ms:
        s = max(0, m.start() - window // 3)
        seg = text[s: s + window]
        if seg.strip() and not any(seg[:150] == x[:150] for x in segs):
            segs.append(seg)
    return (head + "\n\n……（以下为相关片段）……\n\n" + "\n---\n".join(segs))[:max_input]


AI_SYSTEM = (
    "你是资深招投标专家，负责从招标文件中抽取信息。要求：忠于原文，保留关键原话和数字；"
    "不编造，原文没有的写空；每条尽量给出出处（页码或条款号，文本里的「【第N页】」标记即页码）。"
)

TASKS = {
    "qualifications": (
        r"资格|资质|业绩|证书|许可证|项目经理|建造师|注册资本|财务|信誉|社保",
        "抽取投标人资格要求（资质、业绩、人员、财务、信誉、其他），每条一项。输出："
        '{"qualifications":[{"text":"要求原文要点","type":"资质|业绩|人员|财务|信誉|其他","mandatory":true,"source":"出处"}]}',
    ),
    "scoring": (
        r"评分|评审|分值|得分|权重|评标办法|综合评分",
        "抽取评分标准（综合评分法的每个评分项）。表格逐行拆开。category 取 价格/商务/技术。输出："
        '{"scoring":[{"item":"评分项名称","weight":分值数字或null,"criteria":"评分细则要点","category":"技术","source":"出处"}]}',
    ),
    "reject_clauses": (
        r"否决|无效|废标|不予受理|拒绝|重大偏差|实质性|★",
        "抽取会导致投标无效/被否决/废标的条款，以及★号实质性要求。每条给出规避建议。输出："
        '{"reject_clauses":[{"text":"条款要点","starred":true,"tip":"怎么避免","source":"出处"}]}',
    ),
    "format": (
        r"投标文件格式|投标文件的组成|投标文件构成|响应文件|目录|正本|副本|装订|签字|盖章|密封|电子投标|加密",
        "抽取投标文件的组成目录（按顺序）、必须使用的格式文件、编制/签章/装订/份数/电子投标加密等硬性规则。输出："
        '{"format":{"toc":["第一部分 投标函", "..."],"required_forms":["投标函","..."],"rules":["规则要点"]}}',
    ),
    "key_info": (
        r"截止|开标|保证金|有效期|澄清|答疑|递交|上传|平台|CA|获取",
        "抽取关键时间和投递信息。时间用 YYYY-MM-DD HH:MM。输出："
        '{"key_info":{"deadline":"","open_time":"","clarify_deadline":"","bond":"保证金金额与形式原文","bond_amount":数字(元)或null,'
        '"bond_deadline":"","validity":"投标有效期","submit_method":"电子/纸质/两者","platform":"投递平台名称或网址"},'
        '"clarify_questions":["招标文件里表述不清、互相矛盾、可以向招标人提出澄清的问题"],"summary":"三句话说清这个项目要什么、怎么评、最大风险"}',
    ),
}


def ai_analyze(text: str) -> dict:
    def run(key: str) -> tuple[str, dict]:
        kw, ask = TASKS[key]
        prompt = "【任务】" + ask + "\n\n【招标文件内容】\n" + _windows(text, kw)
        try:
            return key, llm.chat_json(prompt, system=AI_SYSTEM, feature="tender_" + key, max_tokens=4000)
        except Exception as e:  # noqa: BLE001
            return key, {"_error": str(e)}

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = dict(ex.map(run, list(TASKS)))
    out: dict = {"by": "ai", "warnings": []}
    for key, data in results.items():
        if "_error" in data:
            out["warnings"].append(f"AI 解析「{key}」失败：{data['_error'][:150]}")
            continue
        if key == "key_info":
            out["key_info"] = data.get("key_info") or {}
            out["clarify_questions"] = data.get("clarify_questions") or []
            out["summary"] = data.get("summary") or ""
        elif key == "format":
            out["format"] = data.get("format") or {}
        else:
            out[key] = data.get(key) or []
    return out


def analyze(text: str, tables: list | None = None, *, use_ai: bool = True) -> dict:
    rule = rule_analyze(text, tables)
    if not (use_ai and llm.enabled()):
        return rule
    ai = ai_analyze(text)
    merged = dict(rule)
    merged["by"] = "ai"
    for k in ("qualifications", "scoring", "reject_clauses", "clarify_questions", "summary"):
        if ai.get(k):
            merged[k] = ai[k]
    if ai.get("format"):
        f = dict(rule.get("format") or {})
        for k, v in ai["format"].items():
            if v:
                f[k] = v
        merged["format"] = f
    if ai.get("key_info"):
        ki = dict(rule.get("key_info") or {})
        for k, v in ai["key_info"].items():
            if v not in (None, "", []):
                ki[k] = v
        merged["key_info"] = ki
    merged["warnings"] = ai.get("warnings", []) + ([w for w in rule.get("warnings", [])] if len(ai.get("warnings", [])) >= 3 else [])
    return merged


# ---------------------------------------------------------------- 投标准备清单

STANDARD_PROOFS = [
    ("营业执照", r"营业执照"),
    ("财务状况报告（审计报告或银行资信证明）", r"审计|财务报告|资信证明"),
    ("依法缴纳税收证明", r"纳税|完税|税收"),
    ("依法缴纳社会保障资金证明", r"社保|社会保险|社会保障"),
    ("参加政府采购活动前三年内无重大违法记录声明", r"无重大违法|违法记录声明"),
    ("信用记录查询截图（信用中国、中国政府采购网）", r"信用中国|信用记录|失信"),
]


def build_checklist(analysis: dict, company_id: int, deadline: str = "") -> list[dict]:
    lib = matching.library_summary(company_id)
    basics = db.q(
        "SELECT id, name, valid_to, file_ids FROM library_items WHERE company_id = ? AND category IN ('basic','qualification')",
        (company_id,),
    )
    items: list[dict] = []
    # 1. 格式文件（在标书编写里自动生成）
    forms = (analysis.get("format") or {}).get("required_forms") or ["投标函", "法定代表人身份证明", "授权委托书", "开标一览表"]
    for fm in forms:
        if re.search(r"资格|证明文件", fm):
            note = "在标书「资格证明文件」章节自动引用资料库原件扫描件"
        elif "偏离" in fm:
            note = "标书里已按★条款生成偏离表框架，需逐条填写响应"
        elif re.search(r"方案", fm):
            note = "在「标书编写」里按评分点起草"
        else:
            note = "在「标书编写」里按资料库自动填写，需签字盖章"
        items.append({"group": "格式文件", "requirement": fm, "status": "系统生成", "items": [], "note": note})
    # 2. 通用证明材料
    for name, pat in STANDARD_PROOFS:
        hits = [b for b in basics if re.search(pat, b["name"])]
        status, note = ("已具备", "") if hits else ("缺失", "请在资料库「基础资料」里上传")
        for h in hits:
            if h["valid_to"] and deadline and h["valid_to"][:10] < deadline[:10]:
                status, note = "已过期", f"「{h['name']}」有效期至 {h['valid_to']}"
        if "声明" in name and not hits:
            status, note = "系统生成", "按格式在标书里生成声明函，需盖章"
        items.append({"group": "通用证明", "requirement": name, "status": status, "items": [{"id": h["id"], "name": h["name"], "valid_to": h["valid_to"]} for h in hits], "note": note})
    # 3. 资格要求逐条
    for q in analysis.get("qualifications") or []:
        text = q.get("text", "")
        if matching.GENERIC_CLAUSE.search(text) and not re.search(r"资质|证书|许可|认证|等级", text):
            continue
        terms = [t for t in matching.TERM_RE.findall(text) if len(t) >= 4]
        if not terms:
            if q.get("type") == "业绩":
                st = "需人工确认" if lib["performance"] else "缺失"
                items.append({"group": "资格要求", "requirement": text, "status": st, "items": [{"id": p["id"], "name": p["name"], "valid_to": ""} for p in lib["performance"][:5]], "note": "从业绩库挑选符合要求的业绩"})
            elif q.get("type") == "人员":
                items.append({"group": "资格要求", "requirement": text, "status": "需人工确认", "items": [{"id": p["id"], "name": p["name"] + "（" + (p.get("certs") or p.get("position") or "") + "）", "valid_to": p.get("cert_valid_to", "")} for p in lib["personnel"][:5]], "note": "从人员库挑选，注意在建项目占用"})
            else:
                items.append({"group": "资格要求", "requirement": text, "status": "需人工确认", "items": [], "note": ""})
            continue
        for term in terms[:3]:
            status, hit = matching._find_cover(term, lib, deadline)
            st = {"ok": "已具备", "expired": "已过期", "level": "等级不足", "none": "缺失"}[status]
            items.append(
                {
                    "group": "资格要求",
                    "requirement": term + ("（" + text[:60] + "）" if len(text) > len(term) + 4 else ""),
                    "status": st,
                    "items": [{"id": hit["id"], "name": hit["name"], "valid_to": hit.get("valid_to") or hit.get("cert_valid_to", "")}] if hit else [],
                    "note": "",
                }
            )
    return items
