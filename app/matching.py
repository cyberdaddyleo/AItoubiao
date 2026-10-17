"""标讯匹配：订阅画像过滤 → 打分 → 资格比对（规则初判 + AI 复核）→ 推荐理由。

结论分四档：能投 / 差一项 / 不能投 / 待评估。
规则初判只看得懂明确写出的证书、资质等级、注册资本等硬条件；看不懂的条款标「需人工确认」，
配置 AI 后会对得分靠前的标讯自动做一次资格复核。
"""
from __future__ import annotations

import re

from . import db, llm
from .config import now_str
from .textutil import parse_amount_cn, split_keywords, to_dt, tokenize

LEVELS = ("能投", "差一项", "不能投", "待评估")
LEVEL_ORDER = {"特": 0, "甲": 1, "一": 1, "壹": 1, "乙": 2, "二": 2, "贰": 2, "丙": 3, "三": 3, "叁": 3, "丁": 4, "四": 4}

GENERIC_CLAUSE = re.compile(
    r"第二十二条|政府采购政策|特定资格要求[:：]?\s*(无|不设|/)|失信被执行人|重大税收违法|政府采购严重违法|信用中国|"
    r"具有独立承担民事责任|良好的商业信誉|健全的财务会计制度|依法缴纳税收|社会保障资金|必需的设备和专业技术能力|"
    r"参加政府采购活动前三年内|法律、行政法规规定的其他条件|单位负责人为同一人|存在直接控股|中小企业"
)
REQ_HINT = re.compile(r"资质|证书|许可证|许可|认证|等级|注册|执业|建造师|工程师|业绩|注册资本|ISO|CMMI|ITSS|CCRC|安全生产|备案|级")
TERM_RE = re.compile(
    r"([一-龥A-Za-z0-9（）()·]{2,30}?(?:资质|许可证|证书|认证证书|认证|备案证明|等级证书))"
)
LEVEL_RE = re.compile(r"([特甲乙丙丁一二三四壹贰叁])级")


# ---------------------------------------------------------------- 资料库摘要

def library_summary(company_id: int) -> dict:
    comp = db.get("companies", company_id) or {}
    items = db.q(
        "SELECT id, category, name, number, issuer, level, valid_to, tags, data_json FROM library_items "
        "WHERE company_id = ? AND category IN ('qualification','personnel','performance','basic')",
        (company_id,),
    )
    quals, people, perfs, basics = [], [], [], []
    for it in items:
        data = db.jload(it["data_json"], {}) or {}
        if it["category"] == "qualification":
            quals.append({"id": it["id"], "name": it["name"], "level": it["level"], "number": it["number"], "valid_to": it["valid_to"]})
        elif it["category"] == "personnel":
            people.append(
                {
                    "id": it["id"],
                    "name": it["name"],
                    "position": data.get("position", ""),
                    "certs": data.get("certs", ""),
                    "title": data.get("title", ""),
                    "cert_valid_to": it["valid_to"],
                }
            )
        elif it["category"] == "performance":
            perfs.append(
                {
                    "id": it["id"],
                    "name": it["name"],
                    "client": data.get("client", ""),
                    "amount_wan": data.get("amount_wan"),
                    "sign_date": data.get("sign_date", ""),
                    "industry": data.get("industry", "") or it["tags"],
                }
            )
        else:
            basics.append({"id": it["id"], "name": it["name"], "valid_to": it["valid_to"]})
    return {
        "company": {
            "name": comp.get("name", ""),
            "reg_capital": comp.get("reg_capital", ""),
            "is_sme": bool(comp.get("is_sme")),
            "annual_revenue_wan": comp.get("annual_revenue_wan"),
            "staff_count": comp.get("staff_count"),
            "industries": comp.get("industries", ""),
            "business_scope": (comp.get("business_scope") or "")[:300],
        },
        "qualifications": quals,
        "personnel": people,
        "performance": perfs,
        "basics": basics,
    }


# ---------------------------------------------------------------- 资格规则初判

def _term_tokens(term: str) -> set[str]:
    t = re.sub(r"[（）()·\s]", "", term)
    toks = set(tokenize(t, keep_stop=True))
    toks -= {"证书", "资质", "认证", "许可证", "等级", "具有", "具备", "有效", "的", "级"}
    return toks


def _level_ok(required: str, have: str) -> bool:
    r = LEVEL_RE.search(required or "")
    if not r:
        return True
    h = LEVEL_RE.search(have or "")
    if not h:
        return False
    return LEVEL_ORDER.get(h.group(1), 9) <= LEVEL_ORDER.get(r.group(1), 9)


def _find_cover(term: str, lib: dict, deadline: str) -> tuple[str, dict | None]:
    """在资料库里找能覆盖该要求的证书/人员证书。返回 (状态, 命中项)。状态：ok / expired / level / none"""
    want = _term_tokens(term)
    if not want:
        return "none", None
    best, best_ratio = None, 0.0
    candidates = [("q", q) for q in lib["qualifications"]] + [("p", p) for p in lib["personnel"]]
    for kind, c in candidates:
        name = c["name"] if kind == "q" else (c.get("certs") or "") + " " + (c.get("title") or "")
        have = _term_tokens(name + " " + (c.get("level") or ""))
        if not have:
            continue
        inter = len(want & have)
        ratio = inter / len(want)
        if term.replace("证书", "") in name or name.replace("证书", "") in term:
            ratio = max(ratio, 0.9)
        if ratio > best_ratio:
            best, best_ratio = (kind, c), ratio
    if not best or best_ratio < 0.5:
        return "none", None
    kind, c = best
    level_src = (c.get("level") or "") + (c.get("name") or "") if kind == "q" else (c.get("certs") or "")
    if not _level_ok(term, level_src):
        return "level", c
    vt = c.get("valid_to") if kind == "q" else c.get("cert_valid_to")
    if vt and deadline and vt[:10] < deadline[:10]:
        return "expired", c
    return "ok", c


def rule_qualification(notice: dict, lib: dict) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    missing: list[str] = []
    comp = lib["company"]
    text = notice.get("qualification_text") or ""
    deadline = notice.get("deadline") or ""

    if notice.get("sme_only") and not comp.get("is_sme"):
        return "不能投", ["本项目专门面向中小企业，本主体未标记为中小企业"], ["中小企业身份"]

    if not text:
        if notice.get("detail_fetched"):
            return "待评估", ["公告正文里没有找到资格要求段落，需查看招标文件"], []
        return "待评估", ["尚未抓取公告详情，资格要求待确认"], []

    clauses = [c.strip() for c in re.split(r"\n|；|;|(?<=。)|(?=\d+[\.．、])", text) if c and len(c.strip()) > 3]
    specific = [c for c in clauses if REQ_HINT.search(c) and not GENERIC_CLAUSE.search(c)]
    if re.search(r"特定资格要求[:：]?\s*(无|不设|/)", text) and not specific:
        return "能投", ["无特定资格要求，满足《政府采购法》第二十二条通用条件即可"], []
    if not specific:
        return "能投", ["只有通用资格条件，未发现资质证书类硬性要求"], []

    unknown: list[str] = []
    for clause in specific[:12]:
        terms = [t for t in TERM_RE.findall(clause) if len(t) >= 4]
        cap = re.search(r"注册资本[^\d]{0,10}(\d+(?:\.\d+)?)\s*(万|亿)?", clause)
        if cap:
            need = float(cap.group(1)) * (10000 if cap.group(2) == "亿" else 1)
            have = parse_amount_cn(comp.get("reg_capital") or "", default_unit="万元")
            have_wan = (have / 10000) if have else None
            if have_wan is None:
                unknown.append("注册资本（资料库未填写）")
            elif have_wan + 1e-6 < need:
                missing.append(f"注册资本≥{need:g}万元（现有 {have_wan:g} 万元）")
            else:
                reasons.append(f"注册资本满足≥{need:g}万元")
        if "业绩" in clause and not terms:
            if lib["performance"]:
                reasons.append(f"资料库有 {len(lib['performance'])} 条业绩，需人工确认是否满足：{clause[:40]}")
            else:
                missing.append("业绩要求：" + clause[:40])
            continue
        if not terms and not cap:
            unknown.append(clause[:60])
            continue
        for term in terms[:4]:
            status, hit = _find_cover(term, lib, deadline)
            label = term.strip("具有具备持有提供有效的")
            if status == "ok":
                reasons.append(f"具备「{hit['name']}」，满足「{label}」")
            elif status == "expired":
                missing.append(f"{label}（资料库中「{hit['name']}」在截止日前已过期）")
            elif status == "level":
                missing.append(f"{label}（现有「{hit['name']}{hit.get('level') or ''}」等级不够）")
            else:
                missing.append(label)

    missing = list(dict.fromkeys(missing))
    if unknown:
        reasons.append("需人工确认：" + "；".join(unknown[:3]))
    if not missing:
        return ("能投" if not unknown else "待评估"), reasons, []
    if len(missing) == 1:
        return "差一项", reasons, missing
    return "不能投", reasons, missing


AI_SYSTEM = (
    "你是招投标资格审查专家。根据招标公告的资格要求和投标企业资料库，判断这家企业能否投标。"
    "只能依据给出的资料判断，资料里没有的证书、业绩、人员一律视为没有，不要假设。"
)


def ai_qualification(notice: dict, lib: dict) -> dict:
    req = notice.get("qualification_text") or ""
    content = (notice.get("content_text") or "")[:6000]
    prompt = (
        "【招标公告标题】" + notice.get("title", "") + "\n"
        "【投标截止】" + (notice.get("deadline") or "未知") + "\n"
        "【资格要求原文】\n" + (req or "（未单独提取，见公告正文）") + "\n\n"
        "【公告正文节选】\n" + content + "\n\n"
        "【投标企业资料库】\n" + db.jdump(lib) + "\n\n"
        "请输出 JSON：{\"level\": \"能投|差一项|不能投|信息不足\", "
        "\"met\": [\"已满足的要求及对应资料\"], \"missing\": [\"缺少或不满足的要求\"], "
        "\"uncertain\": [\"需要看招标文件或人工确认的点\"], \"summary\": \"一句话结论\"}。"
        "判断标准：所有硬性资格要求都满足→能投；只缺一项→差一项；缺两项及以上或有无法弥补的硬伤→不能投；"
        "公告信息不足以判断→信息不足。证书有效期早于投标截止日视为不满足。"
    )
    data = llm.chat_json(prompt, system=AI_SYSTEM, feature="qualification", max_tokens=1500)
    level = data.get("level") or "待评估"
    if level == "信息不足":
        level = "待评估"
    if level not in LEVELS:
        level = "待评估"
    return {
        "level": level,
        "reasons": [str(x) for x in (data.get("met") or [])][:8]
        + (["需确认：" + "；".join(str(x) for x in data.get("uncertain") or [])] if data.get("uncertain") else [])
        + ([str(data.get("summary"))] if data.get("summary") else []),
        "missing": [str(x) for x in (data.get("missing") or [])][:8],
    }


# ---------------------------------------------------------------- 订阅过滤与打分

def _contains_any(text: str, words: list[str]) -> list[str]:
    tl = (text or "").lower()
    return [w for w in words if w.lower() in tl]


def subscription_check(notice: dict, sub: dict, *, title_only: bool = False) -> tuple[bool, float, list[str], list[str]]:
    """返回 (是否通过, 分数, 理由, 命中关键词)。title_only=True 用于列表阶段预筛（还没有正文）。"""
    title = notice.get("title") or ""
    body = "" if title_only else (notice.get("content_text") or "")
    types = split_keywords(sub.get("notice_types") or "tender")
    if types and notice.get("notice_type") not in types:
        return False, 0, [], []
    exc = split_keywords(sub.get("keywords_exclude"))
    if exc and _contains_any(title, exc):
        return False, 0, [], []
    inc = split_keywords(sub.get("keywords_include"))
    title_hits = _contains_any(title, inc)
    body_hits = [w for w in _contains_any(body, inc) if w not in title_hits] if inc else []
    if inc and not title_hits and not body_hits:
        return False, 0, [], []
    regions = split_keywords(sub.get("regions"))
    region_text = (notice.get("region") or "") + " " + (notice.get("purchaser") or "") + " " + title
    region_hit = _contains_any(region_text, regions) if regions else []
    if regions and not region_hit:
        if not title_only or notice.get("region"):
            return False, 0, [], []
    cats = split_keywords(sub.get("categories"))
    cat_hit = _contains_any((notice.get("category") or "") + " " + title, cats) if cats else []
    if cats and not cat_hit and not title_only and notice.get("category"):
        return False, 0, [], []
    pk = split_keywords(sub.get("purchaser_keywords"))
    pk_hit = _contains_any(notice.get("purchaser") or "", pk) if pk else []
    if pk and not pk_hit and not title_only:
        return False, 0, [], []
    budget = notice.get("budget")
    bmin, bmax = sub.get("budget_min"), sub.get("budget_max")
    if budget is not None and not title_only:
        if bmin is not None and budget < float(bmin) * 1e4:
            return False, 0, [], []
        if bmax is not None and budget > float(bmax) * 1e4:
            return False, 0, [], []

    score = 20.0
    reasons = []
    if title_hits:
        score += 30 + min(20, 10 * (len(title_hits) - 1))
        reasons.append("标题命中关键词：" + "、".join(title_hits))
    if body_hits:
        score += min(15, 5 * len(body_hits))
        reasons.append("正文命中关键词：" + "、".join(body_hits[:5]))
    if not inc:
        score += 10
    if region_hit:
        score += 10
        reasons.append("地区符合：" + "、".join(region_hit))
    if cat_hit:
        score += 10
        reasons.append("品目符合：" + "、".join(cat_hit))
    if pk_hit:
        score += 5
        reasons.append("采购人符合：" + "、".join(pk_hit))
    if budget is not None and (bmin is not None or bmax is not None):
        score += 5
        reasons.append("预算在设定区间内")
    return True, score, reasons, title_hits + body_hits


def deadline_passed(notice: dict) -> bool:
    dl = to_dt(notice.get("deadline"))
    if not dl:
        return False
    from .config import now

    return dl < now()


def _ignored_titles(company_id: int) -> list[set[str]]:
    rows = db.q(
        "SELECT n.title FROM matches m JOIN notices n ON n.id = m.notice_id "
        "WHERE m.company_id = ? AND m.status = 'ignored' ORDER BY m.updated_at DESC LIMIT 200",
        (company_id,),
    )
    return [set(tokenize(r["title"])) for r in rows]


def match_notice(notice_id: int, *, companies: list[dict] | None = None, use_ai: bool = False) -> list[dict]:
    notice = db.get("notices", notice_id)
    if not notice or notice.get("dup_of"):
        return []
    if notice["notice_type"] not in ("tender", "change", "intention", "award", "fail", "other"):
        return []
    companies = companies if companies is not None else db.q("SELECT * FROM companies")
    results = []
    for comp in companies:
        subs = db.q("SELECT * FROM subscriptions WHERE company_id = ? AND enabled = 1", (comp["id"],))
        best = None
        for sub in subs:
            ok, score, reasons, hits = subscription_check(notice, sub)
            if ok and (best is None or score > best[1]):
                best = (sub, score, reasons, hits)
        if not best:
            continue
        sub, score, reasons, hits = best
        lib = library_summary(comp["id"])
        level, q_reasons, missing = rule_qualification(notice, lib)
        level_by = "rule"
        if use_ai and llm.enabled() and notice["notice_type"] == "tender":
            try:
                ai = ai_qualification(notice, lib)
                level, q_reasons, missing, level_by = ai["level"], ai["reasons"], ai["missing"], "ai"
            except Exception as e:  # noqa: BLE001
                q_reasons = q_reasons + [f"AI 复核失败：{e}"]
        if notice["notice_type"] != "tender":
            level = "待评估"
        if deadline_passed(notice) and notice["notice_type"] == "tender":
            score -= 50
            reasons.append("投标已截止")
        score += {"能投": 15, "差一项": 0, "不能投": -40, "待评估": 5}[level]
        tset = set(tokenize(notice["title"]))
        for ig in _ignored_titles(comp["id"]):
            if tset and len(tset & ig) / max(len(tset | ig), 1) >= 0.5:
                score -= 15
                reasons.append("与你忽略过的标讯相似")
                break
        score = max(0.0, min(100.0, score))
        existing = db.q1("SELECT * FROM matches WHERE notice_id = ? AND company_id = ?", (notice_id, comp["id"]))
        row = {
            "subscription_id": sub["id"],
            "score": round(score, 1),
            "level": level,
            "level_by": level_by,
            "reasons_json": db.jdump(reasons + q_reasons),
            "missing_json": db.jdump(missing),
            "keyword_hits": "、".join(hits),
            "updated_at": now_str(),
        }
        if existing:
            if existing["level_by"] == "ai" and level_by == "rule":
                row.pop("level")
                row.pop("level_by")
                row.pop("missing_json")
                row["reasons_json"] = db.jdump(reasons + db.jload(existing["reasons_json"], [])[-6:])
            db.update("matches", existing["id"], row)
            mid = existing["id"]
        else:
            row.update({"notice_id": notice_id, "company_id": comp["id"], "status": "new", "created_at": now_str()})
            mid = db.insert("matches", row)
        results.append({"match_id": mid, "company_id": comp["id"], "score": score, "level": row.get("level", existing and existing["level"])})
    return results


def ai_recheck_match(match_id: int) -> dict:
    m = db.get("matches", match_id)
    if not m:
        raise ValueError("匹配记录不存在")
    notice = db.get("notices", m["notice_id"])
    lib = library_summary(m["company_id"])
    ai = ai_qualification(notice, lib)
    old_reasons = [r for r in db.jload(m["reasons_json"], []) if r.startswith(("标题命中", "正文命中", "地区", "品目", "采购人", "预算"))]
    score = float(m["score"])
    delta = {"能投": 15, "差一项": 0, "不能投": -40, "待评估": 5}
    score = max(0, min(100, score - delta.get(m["level"], 0) + delta.get(ai["level"], 0)))
    db.update(
        "matches",
        match_id,
        {
            "level": ai["level"],
            "level_by": "ai",
            "reasons_json": db.jdump(old_reasons + ai["reasons"]),
            "missing_json": db.jdump(ai["missing"]),
            "score": round(score, 1),
            "updated_at": now_str(),
        },
    )
    return db.get("matches", match_id)


def prefilter_title(item: dict, subs: list[dict]) -> bool:
    """列表阶段：标题是否可能命中某个订阅（决定要不要抓详情）。"""
    for sub in subs:
        ok, _, _, _ = subscription_check(item, sub, title_only=True)
        if ok:
            return True
    return False
