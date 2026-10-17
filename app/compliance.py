"""合规体检（防废标）。输出：必须修改(error) / 警告(warning) / 提示(info)。

检查项：
1. 未填写的【待补充】、空章节；
2. AI 起草但未审核的章节；
3. 项目名称/编号前后不一致，残留其他项目名称或编号（复用旧标书的常见废标原因）；
4. 出现其他投标主体的名称；
5. 报价：投标函金额 = 报价表合计，大写 = 小写，不超过预算/最高限价；
6. 引用的资质证书在投标截止日前是否过期 / 即将过期；
7. 招标文件要求的格式文件是否都有；
8. ★ 实质性条款是否有响应、技术评分点是否都有内容；
9. 与系统内其他主体同一项目标书的雷同度；同一项目多个主体参与的串标风险提示；
10. 需要签字盖章的位置清单。
"""
from __future__ import annotations

import re

from . import db, llm
from .config import now_str
from .textutil import cn_upper_to_number, containment, shingles, to_dt, tokenize
from .writer import doc_full, project_context

PH_RE = re.compile(r"【待补充[^】]*】")


def _item(level: str, category: str, title: str, detail: str = "", section_id: int | None = None) -> dict:
    return {"level": level, "category": category, "title": title, "detail": detail, "section_id": section_id}


def run_check(doc_id: int, *, use_ai: bool = False, user_id: int | None = None) -> dict:
    doc = doc_full(doc_id)
    ctx = project_context(doc["project_id"])
    p, comp, a = ctx["project"], ctx["company"], ctx["analysis"] or {}
    secs = doc["sections"]
    full = "\n".join((s["title"] or "") + "\n" + (s["content"] or "") for s in secs)
    items: list[dict] = []

    # 1. 待补充 & 空章节
    for s in secs:
        n = len(PH_RE.findall(s["content"] or ""))
        if n:
            sample = "、".join(dict.fromkeys(m.strip("【】").replace("待补充：", "") for m in PH_RE.findall(s["content"])))[:120]
            items.append(_item("error", "未完成内容", f"「{s['title']}」还有 {n} 处【待补充】", sample, s["id"]))
        if not (s["content"] or "").strip() and s["kind"] != "text":
            items.append(_item("error", "未完成内容", f"「{s['title']}」内容为空", "", s["id"]))
        if s["ai_generated"] and not s["reviewed"]:
            items.append(_item("warning", "AI 起草待审核", f"「{s['title']}」为 AI 起草，尚未人工审核", "请逐段核对事实与数字后点「标记已审核」", s["id"]))

    # 3. 项目名称/编号一致性 & 残留
    pno = (p.get("project_no") or "").strip()
    for s in secs:
        for m in re.finditer(r"项目编号[：:（(\s]*([A-Za-z0-9\-_/#\[\]（）()\.]{4,40})", s["content"] or ""):
            found = m.group(1).strip("（）()")
            if pno and found != pno and not found.startswith("【"):
                items.append(_item("error", "前后不一致", f"「{s['title']}」中的项目编号「{found}」与本项目编号「{pno}」不一致", "", s["id"]))
    others = db.q("SELECT id, name, project_no FROM projects WHERE id != ?", (p["id"],))
    for o in others:
        for s in secs:
            c = s["content"] or ""
            if o["project_no"] and len(o["project_no"]) >= 5 and o["project_no"] != pno and o["project_no"] in c:
                items.append(_item("error", "残留旧项目信息", f"「{s['title']}」出现了其他项目的编号「{o['project_no']}」", "疑似复用旧标书未修改干净", s["id"]))
            if o["name"] and len(o["name"]) >= 8 and o["name"] != p["name"] and o["name"] in c:
                items.append(_item("error", "残留旧项目信息", f"「{s['title']}」出现了其他项目名称「{o['name'][:30]}」", "疑似复用旧标书未修改干净", s["id"]))
    if p.get("purchaser"):
        other_purchasers = {r["purchaser"] for r in db.q("SELECT DISTINCT purchaser FROM projects WHERE id != ? AND purchaser != ''", (p["id"],))}
        for op in other_purchasers:
            if len(op) >= 4 and op not in p["purchaser"] and p["purchaser"] not in op:
                for s in secs:
                    if op in (s["content"] or ""):
                        items.append(_item("error", "残留旧项目信息", f"「{s['title']}」出现了其他采购人「{op}」", "", s["id"]))

    # 4. 其他主体名称
    for oc in db.q("SELECT name FROM companies WHERE id != ?", (comp["id"],)):
        if oc["name"] and oc["name"] in full:
            hit = next((s for s in secs if oc["name"] in (s["content"] or "")), None)
            items.append(_item("error", "主体混用", f"标书里出现了其他投标主体「{oc['name']}」", "一份标书只能是本投标人的信息", hit and hit["id"]))

    # 5. 报价
    price = doc.get("price") or {}
    total = price.get("total") or 0
    letter = next((s for s in secs if s["form_key"] == "bid_letter"), None)
    if total:
        if p.get("budget") and total > float(p["budget"]) + 0.01:
            items.append(_item("error", "报价", f"投标总价 {total:,.2f} 元超过预算/最高限价 {float(p['budget']):,.2f} 元", "超过最高限价的投标通常会被否决", None))
        if letter:
            m = re.search(r"[￥¥]\s*([\d,]+(?:\.\d+)?)\s*元", letter["content"] or "")
            if m:
                v = float(m.group(1).replace(",", ""))
                if abs(v - total) > 0.01:
                    items.append(_item("error", "报价", f"投标函金额 {v:,.2f} 元与报价表合计 {total:,.2f} 元不一致", "", letter["id"]))
    for s in secs:
        for m in re.finditer(r"大写[）)]?[：:]?\s*(?:人民币)?\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆角分整正]{3,})", s["content"] or ""):
            up = cn_upper_to_number(m.group(1))
            tail = (s["content"] or "")[m.end(): m.end() + 40]
            lm = re.search(r"[￥¥]\s*([\d,]+(?:\.\d+)?)", tail) or re.search(r"[￥¥]\s*([\d,]+(?:\.\d+)?)", (s["content"] or "")[max(0, m.start() - 60): m.start()])
            if up is not None and lm:
                low = float(lm.group(1).replace(",", ""))
                if abs(up - low) > 0.01:
                    items.append(_item("error", "报价", f"「{s['title']}」大写金额（{up:,.2f}）与小写金额（{low:,.2f}）不一致", m.group(1), s["id"]))
    if not total and any(s["kind"] == "price" for s in secs):
        items.append(_item("error", "报价", "报价表还没有填写", "到「报价」里录入报价明细", None))

    # 6. 资质有效期
    deadline = p.get("deadline") or ""
    dl = to_dt(deadline)
    ref_ids = set()
    for s in secs:
        for r in s.get("refs") or []:
            if r.get("item_id") and r.get("type") == "library":
                ref_ids.add(r["item_id"])
    lib_rows = db.q("SELECT id, name, valid_to, category FROM library_items WHERE company_id = ? AND valid_to != ''", (comp["id"],))
    for r in lib_rows:
        if r["id"] not in ref_ids and r["category"] != "basic":
            continue
        vt = to_dt(r["valid_to"])
        if not vt or not dl:
            continue
        if vt < dl:
            items.append(_item("error", "资质有效期", f"「{r['name']}」有效期至 {r['valid_to']}，早于投标截止 {deadline[:10]}", "过期证书会导致资格审查不通过", None))
        elif (vt - dl).days <= 30:
            items.append(_item("warning", "资质有效期", f"「{r['name']}」将在 {r['valid_to']} 到期，距投标截止不足 30 天", "注意合同履约期内是否需要有效", None))

    # 7. 格式文件
    for form in (a.get("format") or {}).get("required_forms") or []:
        key = re.sub(r"[（(].*?[)）]", "", form)[:6]
        if key and key not in full:
            items.append(_item("error", "缺少格式文件", f"招标文件要求的「{form}」在标书中没有找到", "请添加对应章节", None))

    # 8. ★ 条款与评分点
    for st in (a.get("starred") or [])[:40] + [r for r in (a.get("reject_clauses") or []) if r.get("starred")][:20]:
        toks = [t for t in tokenize(st.get("text", "")) if len(t) >= 2 and not t.isdigit()]
        toks = sorted(set(toks), key=len, reverse=True)[:6]
        if len(toks) < 3:
            continue
        hit = sum(1 for t in toks if t in full)
        if hit / len(toks) < 0.5:
            items.append(_item("warning", "★实质性条款", "★条款可能没有响应：" + st["text"][:80], "请在技术偏离表或方案中逐条响应", None))
    for s in secs:
        if s["kind"] == "tech" and 0 < len(re.sub(r"\s", "", s["content"] or "")) < 200:
            items.append(_item("warning", "评分点覆盖", f"评分点「{s['title']}」内容过少（不足 200 字）", "评委按评分点打分，内容过少容易丢分", s["id"]))

    # 9. 雷同与串标风险
    if p.get("notice_id") or pno:
        same = db.q(
            "SELECT pr.id, pr.company_id, c.name AS cname FROM projects pr JOIN companies c ON c.id = pr.company_id "
            "WHERE pr.id != ? AND pr.company_id != ? AND pr.status != 'abandoned' AND ((pr.notice_id IS NOT NULL AND pr.notice_id = ?) OR (pr.project_no != '' AND pr.project_no = ?))",
            (p["id"], comp["id"], p.get("notice_id"), pno or "__none__"),
        )
        if same:
            names = "、".join(r["cname"] for r in same)
            items.append(
                _item(
                    "error",
                    "串标风险",
                    f"本系统中还有其他主体（{names}）参与同一项目",
                    "单位负责人为同一人或存在控股、管理关系的不同单位，不得参加同一标段投标（《招标投标法实施条例》第三十四条、《政府采购法实施条例》第十八条）。请确认各主体之间不存在上述关系，并由负责人决定是否只保留一家。",
                    None,
                )
            )
            my = shingles(full)
            for r in same:
                od = db.q1("SELECT id FROM bid_docs WHERE project_id = ?", (r["id"],))
                if not od:
                    continue
                other_full = "\n".join(x["content"] or "" for x in db.q("SELECT content FROM bid_sections WHERE doc_id = ?", (od["id"],)))
                sim = containment(my, shingles(other_full))
                if sim >= 0.25:
                    items.append(_item("error", "雷同风险", f"与「{r['cname']}」同一项目的标书内容重合度 {sim:.0%}", "评标时文件雷同会被认定串通投标，请各自独立编写", None))

    if dl and dl < to_dt(now_str()):
        items.append(_item("warning", "时间", f"投标截止时间 {deadline} 已过", "", None))

    # 10. 签字盖章清单
    sign = [s["title"] for s in secs if s["kind"] in ("form", "price") or "盖章" in (s["content"] or "")]
    if sign:
        items.append(_item("info", "签字盖章", "以下章节需要签字/盖章：" + "、".join(sign), "电子投标需用 CA 电子签章；纸质投标注意骑缝章和逐页盖章要求", None))
    rules = (a.get("format") or {}).get("rules") or []
    if rules:
        items.append(_item("info", "编制要求", "招标文件的编制/装订/加密要求共 " + str(len(rules)) + " 条", "；".join(rules[:6])[:600], None))

    # AI 语义核对（可选）：★条款与评分点逐条比对
    if use_ai and llm.enabled():
        try:
            items += _ai_check(secs, a)
        except Exception as e:  # noqa: BLE001
            items.append(_item("info", "AI 核对", "AI 语义核对失败：" + str(e)[:120], "", None))

    order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda x: order[x["level"]])
    summary = {lv: sum(1 for i in items if i["level"] == lv) for lv in ("error", "warning", "info")}
    result = {"summary": summary, "items": items, "checked_at": now_str(), "passed": summary["error"] == 0}
    db.insert(
        "checks",
        {
            "doc_id": doc_id,
            "summary": f"必须修改 {summary['error']}，警告 {summary['warning']}，提示 {summary['info']}",
            "result_json": db.jdump(result),
            "created_by": user_id,
            "created_at": now_str(),
        },
    )
    return result


def _ai_check(secs: list[dict], analysis: dict) -> list[dict]:
    reqs = [s.get("text") for s in (analysis.get("starred") or [])][:25]
    reqs += [r.get("text") for r in (analysis.get("reject_clauses") or [])][:25]
    if not reqs:
        return []
    body = "\n\n".join("## " + s["title"] + "\n" + (s["content"] or "")[:3000] for s in secs)[:40000]
    prompt = (
        "下面是招标文件中的★实质性要求和否决/废标条款，以及我方投标文件正文。请逐条判断投标文件是否已满足或已响应，"
        "只列出「未响应」或「可能不满足」的条款。输出 JSON：{\"issues\":[{\"clause\":\"条款\",\"problem\":\"问题\",\"suggest\":\"修改建议\"}]}\n\n"
        "【条款】\n" + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(reqs)) + "\n\n【投标文件】\n" + body
    )
    data = llm.chat_json(prompt, system="你是投标文件审查专家，判断要严格、具体。", feature="compliance", max_tokens=3000)
    out = []
    for it in (data.get("issues") or [])[:30]:
        out.append(_item("warning", "AI 核对", "可能未满足：" + str(it.get("clause", ""))[:80], (str(it.get("problem", "")) + "；建议：" + str(it.get("suggest", "")))[:400], None))
    return out
