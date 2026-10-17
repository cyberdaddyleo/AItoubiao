"""标书编写。

原则（写进代码，不靠自觉）：
1. 格式文件（投标函、授权书、声明函等）用固定模板 + 资料库字段填充，不经过 AI；缺的字段写【待补充：…】；
2. 资格证明、业绩、人员只「引用」资料库里的真实条目，AI 不生成这类内容；
3. 技术/服务方案可以由 AI 起草：只能用项目事实、招标文件原文、企业方案素材；
   任何数字（工期、人数、金额、参数）必须有出处，没有出处写【待补充】；禁止写其他项目名称和其他公司名称；
4. 所有 AI 生成的章节标记「AI 起草·待审核」，审核通过后才算定稿。

章节正文使用简化 Markdown：# 标题、| 表格 |、- 列表、**加粗**、![说明](file:ID) 图片、[[分页]]。
"""
from __future__ import annotations

import re

from . import db, llm
from .config import now_str, today_str
from .textutil import BM25, chunk_text, fmt_amount, number_to_cn_upper, tokenize

PH = "【待补充：{}】"


def ph(name: str) -> str:
    return PH.format(name)


# ---------------------------------------------------------------- 上下文

def project_context(project_id: int) -> dict:
    p = db.get("projects", project_id)
    if not p:
        raise ValueError("项目不存在")
    c = db.get("companies", p["company_id"]) or {}
    td = db.q1("SELECT * FROM tender_docs WHERE project_id = ? AND analysis_json != '' ORDER BY id DESC LIMIT 1", (project_id,))
    analysis = db.jload(td["analysis_json"], {}) if td else {}
    notice = db.get("notices", p["notice_id"]) if p.get("notice_id") else None
    return {"project": p, "company": c, "analysis": analysis or {}, "tender_doc": td, "notice": notice}


def tender_text(ctx: dict) -> str:
    td = ctx.get("tender_doc")
    if td and td.get("text_path"):
        try:
            with open(td["text_path"], encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    n = ctx.get("notice")
    return (n or {}).get("content_text") or ""


# ---------------------------------------------------------------- 目录

KIND_RULES = [
    (r"投标函|响应函|磋商响应函|报价函", "form", "bid_letter"),
    (r"开标一览表|报价一览表|报价表|报价明细|分项报价|投标报价|价格", "price", ""),
    (r"法定代表人.*(身份证明|资格证明)", "form", "legal_rep"),
    (r"授权委托书|授权书", "form", "authorization"),
    (r"无重大违法|违法记录", "form", "no_violation"),
    (r"中小企业声明", "form", "sme"),
    (r"资格证明|资质证明|资格审查|资格文件|证明文件", "qualification", ""),
    (r"业绩", "performance", ""),
    (r"人员|项目班子|团队|拟派|项目组", "personnel", ""),
    (r"偏离", "deviation", ""),
    (r"承诺", "form", "commitment"),
    (r"技术|方案|服务|实施|施工组织|组织设计|售后|培训|质量|进度|应急|设计", "tech", ""),
]


def _kind_for(title: str) -> tuple[str, str]:
    for pat, kind, key in KIND_RULES:
        if re.search(pat, title):
            return kind, key
    return "text", ""


DEFAULT_TECH = [
    ("项目理解与需求分析", "结合招标需求说明对项目背景、目标、范围和重点难点的理解"),
    ("总体实施（服务/供货）方案", "围绕采购需求逐项给出实施思路、方法和步骤"),
    ("进度计划及保障措施", "满足招标文件规定的工期/服务期要求"),
    ("质量保证措施", "质量目标、质量管理体系与控制措施"),
    ("项目组织机构与人员配置", "组织架构、岗位职责，人员只引用人员库"),
    ("售后服务与培训方案", "响应时间、服务内容、培训计划"),
    ("应急预案与风险控制", "可能风险及应对措施"),
]


def default_outline(ctx: dict) -> list[dict]:
    analysis = ctx["analysis"] or {}
    notice = ctx.get("notice") or {}
    sme = bool(notice.get("sme_only")) or "中小企业" in (tender_text(ctx)[:20000] if ctx.get("tender_doc") else "")
    toc = ((analysis.get("format") or {}).get("toc")) or []
    sections: list[dict] = []
    if len(toc) >= 3:
        for t in toc:
            kind, key = _kind_for(t)
            sections.append({"title": t, "level": 1, "kind": kind, "form_key": key})
        kinds = {s["kind"] for s in sections}
        if "qualification" not in kinds:
            sections.append({"title": "资格证明文件", "level": 1, "kind": "qualification", "form_key": ""})
        if "tech" not in kinds:
            sections.append({"title": "技术（服务）方案", "level": 1, "kind": "tech", "form_key": ""})
    else:
        sections = [
            {"title": "投标函", "level": 1, "kind": "form", "form_key": "bid_letter"},
            {"title": "开标一览表（报价一览表）", "level": 1, "kind": "price", "form_key": ""},
            {"title": "法定代表人身份证明", "level": 1, "kind": "form", "form_key": "legal_rep"},
            {"title": "法定代表人授权委托书", "level": 1, "kind": "form", "form_key": "authorization"},
            {"title": "资格证明文件", "level": 1, "kind": "qualification", "form_key": ""},
            {"title": "参加政府采购活动前三年内无重大违法记录声明", "level": 1, "kind": "form", "form_key": "no_violation"},
        ]
        if sme:
            sections.append({"title": "中小企业声明函", "level": 1, "kind": "form", "form_key": "sme"})
        sections += [
            {"title": "类似项目业绩", "level": 1, "kind": "performance", "form_key": ""},
            {"title": "拟派项目人员", "level": 1, "kind": "personnel", "form_key": ""},
            {"title": "技术（服务）方案", "level": 1, "kind": "tech", "form_key": ""},
            {"title": "技术偏离表", "level": 1, "kind": "deviation", "form_key": ""},
            {"title": "其他承诺", "level": 1, "kind": "form", "form_key": "commitment"},
        ]
    # 技术方案下按「评分点 → 响应」展开
    tech_items = [s for s in (analysis.get("scoring") or []) if s.get("category") == "技术"]
    out: list[dict] = []
    tech_done = False
    for s in sections:
        out.append(s)
        if s["kind"] == "tech" and not tech_done:
            tech_done = True
            s["kind"] = "text"
            s["requirement"] = "技术部分总述（可留空）"
            if tech_items:
                for it in tech_items[:30]:
                    w = it.get("weight")
                    out.append(
                        {
                            "title": it.get("item", "评分项") + (f"（{w:g}分）" if isinstance(w, (int, float)) else ""),
                            "level": 2,
                            "kind": "tech",
                            "form_key": "",
                            "requirement": (it.get("criteria") or "")[:1500],
                        }
                    )
            else:
                for title, req in DEFAULT_TECH:
                    out.append({"title": title, "level": 2, "kind": "tech", "form_key": "", "requirement": req})
    return out


def create_bid_doc(project_id: int, user_id: int | None = None) -> int:
    ctx = project_context(project_id)
    existing = db.q1("SELECT id FROM bid_docs WHERE project_id = ?", (project_id,))
    if existing:
        return existing["id"]
    t = now_str()
    doc_id = db.insert(
        "bid_docs",
        {"project_id": project_id, "title": ctx["project"]["name"] + " 投标文件", "status": "draft", "price_json": db.jdump({"items": []}), "created_at": t, "updated_at": t},
    )
    for i, s in enumerate(default_outline(ctx)):
        sid = db.insert(
            "bid_sections",
            {
                "doc_id": doc_id,
                "order_no": float(i + 1),
                "level": s.get("level", 1),
                "title": s["title"],
                "kind": s["kind"],
                "form_key": s.get("form_key", ""),
                "requirement": s.get("requirement", ""),
                "content": "",
                "updated_by": user_id,
                "updated_at": t,
            },
        )
        # 能确定性生成的章节直接填好
        if s["kind"] in ("form", "price", "qualification", "performance", "personnel", "deviation"):
            try:
                generate_section(sid, user_id=user_id, use_ai=False)
            except Exception:  # noqa: BLE001
                pass
    return doc_id


# ---------------------------------------------------------------- 格式文件模板

def _v(value, name: str) -> str:
    v = (str(value).strip() if value not in (None, "") else "")
    return v or ph(name)


def form_content(key: str, ctx: dict, doc: dict) -> str:
    p, c, a = ctx["project"], ctx["company"], ctx["analysis"]
    ki = (a.get("key_info") or {}) if a else {}
    price = db.jload(doc.get("price_json"), {}) or {}
    total = price.get("total")
    company = _v(c.get("name"), "投标人名称")
    pname = _v(p.get("name"), "项目名称")
    pno = _v(p.get("project_no"), "项目编号")
    purchaser = _v(p.get("purchaser"), "采购人名称")
    agency = p.get("agency") or ""
    validity = ki.get("validity") or ph("投标有效期，见招标文件")
    legal = _v(c.get("legal_person"), "法定代表人姓名")
    auth = ph("授权代表姓名、职务")
    if c.get("contact_person"):
        auth = c["contact_person"] + "（" + ph("职务") + "）"
    date_line = "日期：" + ph("年 月 日（按递交日期填写）")
    seal = "\n\n投标人名称（盖单位公章）：" + company + "\n\n法定代表人或授权代表（签字或盖章）：________________\n\n" + date_line

    if key == "bid_letter":
        amt_upper = number_to_cn_upper(total) if total else ph("投标总价大写")
        amt = f"{total:,.2f}" if total else ph("投标总价小写")
        to_line = "致：" + purchaser + ("（采购人）、" + agency + "（采购代理机构）" if agency else "（采购人）")
        return (
            to_line + "\n\n"
            "根据贵方「" + pname + "」（项目编号：" + pno + "）的招标公告及招标文件，签字代表" + auth
            + "经正式授权并代表投标人" + company + "（地址：" + _v(c.get("address"), "投标人地址") + "）提交投标文件。据此函，签字代表宣布同意如下：\n\n"
            "1. 我方投标总报价为：人民币（大写）" + amt_upper + "（￥" + amt + "元）。\n"
            "2. 我方已详细审查全部招标文件，包括修改文件（如有）以及全部参考资料和有关附件，完全理解并接受招标文件的各项规定和要求。\n"
            "3. 我方同意在招标文件规定的投标截止之日起" + validity + "内遵守本投标文件中的承诺，在此期限届满之前均具有约束力。\n"
            "4. 我方承诺本投标文件中提供的全部资料真实、准确、完整，如有虚假，愿意承担由此产生的一切法律责任。\n"
            "5. 如在投标有效期内撤回投标或中标后拒绝签订合同，我方愿按招标文件和法律规定承担相应责任。\n"
            "6. 与本投标有关的一切正式往来通讯请寄：地址 " + _v(c.get("address"), "地址") + "，邮编 " + _v(c.get("postcode"), "邮编")
            + "，电话 " + _v(c.get("phone"), "电话") + "，电子邮箱 " + _v(c.get("email"), "电子邮箱") + "。"
            + seal
        )
    if key == "legal_rep":
        return (
            "投标人名称：" + company + "\n\n"
            "统一社会信用代码：" + _v(c.get("credit_code"), "统一社会信用代码") + "\n\n"
            "地址：" + _v(c.get("address"), "注册地址") + "\n\n"
            "成立时间：" + _v(c.get("founded_date"), "成立日期") + "\n\n"
            "姓名：" + legal + "　　性别：" + ph("性别") + "　　职务：" + _v(c.get("legal_person_title"), "职务") + "\n\n"
            "系 " + company + " 的法定代表人。\n\n"
            "特此证明。\n\n"
            "附：法定代表人身份证正反面复印件\n\n"
            "投标人名称（盖单位公章）：" + company + "\n\n" + date_line
        )
    if key == "authorization":
        return (
            "致：" + purchaser + "\n\n"
            "本人 " + legal + " 系 " + company + " 的法定代表人，现委托 " + auth + " 为我方代理人。"
            "代理人根据授权，以我方名义签署、澄清、说明、补正、递交、撤回、修改「" + pname + "」（项目编号：" + pno + "）投标文件，"
            "签订合同和处理有关事宜，其法律后果由我方承担。\n\n"
            "委托期限：自本授权书签署之日起至投标有效期满。\n\n代理人无转委托权。\n\n"
            "附：法定代表人身份证复印件、委托代理人身份证复印件\n\n"
            "投标人名称（盖单位公章）：" + company + "\n\n"
            "法定代表人（签字或盖章）：________________\n\n委托代理人（签字）：________________\n\n" + date_line
        )
    if key == "no_violation":
        return (
            "致：" + purchaser + "\n\n"
            "我方 " + company + " 郑重声明：参加本次政府采购活动前三年内，在经营活动中没有因违法经营受到刑事处罚或者"
            "责令停产停业、吊销许可证或者执照、较大数额罚款等行政处罚的重大违法记录。\n\n"
            "我方对上述声明的真实性负责。如有虚假，将依法承担相应责任。" + seal
        )
    if key == "sme":
        size = ph("企业规模：中型/小型/微型")
        return (
            "> 提示：中小企业声明函必须如实填写。只有满足条件时才能提交，虚假声明属于提供虚假材料谋取中标，将承担法律责任。\n\n"
            "本公司（联合体）郑重声明，根据《政府采购促进中小企业发展管理办法》（财库〔2020〕46 号）的规定，"
            "本公司（联合体）参加 " + purchaser + " 的「" + pname + "」采购活动，提供的货物/服务全部由符合政策要求的中小企业制造/承接。相关企业的具体情况如下：\n\n"
            "| 序号 | 标的名称 | 制造商/承接企业 | 所属行业 | 从业人员 | 营业收入 | 资产总额 | 企业规模 |\n"
            "| 1 | " + ph("标的名称") + " | " + company + " | " + ph("行业") + " | "
            + (str(c.get("staff_count")) + "人" if c.get("staff_count") else ph("人数")) + " | "
            + (f"{c['annual_revenue_wan']:g}万元" if c.get("annual_revenue_wan") else ph("营业收入")) + " | " + ph("资产总额") + " | " + size + " |\n\n"
            "以上企业，不属于大企业的分支机构，不存在控股股东为大企业的情形，也不存在与大企业的负责人为同一人的情形。\n\n"
            "本企业对上述声明内容的真实性负责。如有虚假，将依法承担相应责任。\n\n"
            "企业名称（盖章）：" + company + "\n\n" + date_line
        )
    if key == "commitment":
        return (
            "致：" + purchaser + "\n\n"
            "我方就「" + pname + "」（项目编号：" + pno + "）郑重承诺：\n\n"
            "1. 我方提交的投标文件及所有证明材料真实、合法、有效；\n"
            "2. 我方未与其他投标人相互串通投标，未与采购人、采购代理机构恶意串通，未向采购人、采购代理机构或评标委员会成员行贿或提供其他不正当利益；\n"
            "3. 若我方中标，将严格按照招标文件要求和投标承诺签订并履行合同；\n"
            "4. " + ph("招标文件要求的其他承诺事项") + "\n" + seal
        )
    return ph("本章节内容")


# ---------------------------------------------------------------- 引用资料库的章节

def _library(company_id: int, category: str, ids: list[int] | None = None) -> list[dict]:
    if ids:
        rows = db.q(
            f"SELECT * FROM library_items WHERE company_id = ? AND category = ? AND id IN ({','.join('?' for _ in ids)})",
            [company_id, category] + list(ids),
        )
        order = {i: n for n, i in enumerate(ids)}
        rows.sort(key=lambda r: order.get(r["id"], 999))
        return rows
    return db.q("SELECT * FROM library_items WHERE company_id = ? AND category = ? ORDER BY valid_to DESC, id DESC", (company_id, category))


def _images_for(item: dict) -> list[str]:
    out = []
    for fid in db.jload(item.get("file_ids"), []) or []:
        f = db.get("files", fid)
        if not f:
            continue
        if re.search(r"\.(png|jpe?g|bmp|gif)$", f["filename"], re.I):
            out.append(f"![{item['name']}](file:{fid})")
        else:
            out.append(f"（附件：{f['filename']}，打印/上传时随附原件扫描件）")
    return out


def qualification_content(ctx: dict, refs: list[int] | None) -> tuple[str, list[int]]:
    cid = ctx["company"]["id"]
    basics = _library(cid, "basic", None)
    quals = _library(cid, "qualification", refs or None)
    lines = ["以下材料均引用自企业资料库的原件扫描件，请核对与原件一致并逐页加盖公章。", ""]
    lines.append("# 一、基本证明材料")
    if basics:
        lines.append("| 序号 | 材料名称 | 编号 | 有效期至 |")
        for i, b in enumerate(basics, 1):
            lines.append(f"| {i} | {b['name']} | {b['number'] or '—'} | {b['valid_to'] or '长期'} |")
    else:
        lines.append(ph("营业执照、财务状况报告、纳税和社保证明、信用记录查询截图等（资料库「基础资料」为空）"))
    lines.append("")
    lines.append("# 二、资质证书")
    if quals:
        lines.append("| 序号 | 证书名称 | 等级 | 证书编号 | 发证机关 | 有效期至 |")
        for i, q in enumerate(quals, 1):
            lines.append(f"| {i} | {q['name']} | {q['level'] or '—'} | {q['number'] or '—'} | {q['issuer'] or '—'} | {q['valid_to'] or '长期'} |")
    else:
        lines.append(ph("资质证书（资料库「资质证书」为空）"))
    lines.append("")
    lines.append("# 三、证书扫描件")
    imgs = []
    for it in basics + quals:
        imgs += _images_for(it)
    lines += imgs or ["（资料库中尚未上传证书扫描件图片，导出后请手动插入）"]
    return "\n".join(lines), [q["id"] for q in quals]


def performance_content(ctx: dict, refs: list[int] | None) -> tuple[str, list[int]]:
    cid = ctx["company"]["id"]
    rows = _library(cid, "performance", refs or None)
    if not refs and rows:
        # 自动挑选：与项目名称/品目最相关、近三年的业绩
        query = (ctx["project"].get("name") or "") + " " + (ctx["project"].get("category") or "")
        docs = [r["name"] + " " + (r["tags"] or "") + " " + (r["content"] or "") for r in rows]
        bm = BM25(docs)
        ranked = [i for i, _ in bm.top(query, k=8, min_score=0)] or list(range(len(rows)))
        rows = [rows[i] for i in ranked][:8]
    lines = ["以下业绩均引用自企业业绩库，附合同关键页、中标通知书或验收证明扫描件。", ""]
    if not rows:
        return "\n".join(lines + [ph("类似项目业绩（业绩库为空，请先在资料库录入真实业绩）")]), []
    lines.append("| 序号 | 项目名称 | 采购人/客户 | 合同金额（万元） | 签订日期 | 项目内容 |")
    for i, r in enumerate(rows, 1):
        d = db.jload(r["data_json"], {}) or {}
        amt = d.get("amount_wan")
        lines.append(
            f"| {i} | {r['name']} | {d.get('client') or '—'} | {amt if amt not in (None, '') else '—'} | {d.get('sign_date') or '—'} | {(r['content'] or d.get('scope') or '')[:60] or '—'} |"
        )
    lines.append("")
    for r in rows:
        lines += _images_for(r)
    return "\n".join(lines), [r["id"] for r in rows]


def personnel_content(ctx: dict, refs: list[int] | None) -> tuple[str, list[int]]:
    cid = ctx["company"]["id"]
    rows = _library(cid, "personnel", refs or None)[:15]
    lines = ["以下人员均引用自企业人员库，附身份证、职称/执业证书及近期社保缴纳证明。", ""]
    if not rows:
        return "\n".join(lines + [ph("拟派项目人员（人员库为空）")]), []
    lines.append("| 序号 | 姓名 | 拟任岗位 | 职称/执业证书 | 证书编号 | 备注 |")
    for i, r in enumerate(rows, 1):
        d = db.jload(r["data_json"], {}) or {}
        note = d.get("occupied_by") and ("在建项目：" + d["occupied_by"]) or ""
        lines.append(f"| {i} | {r['name']} | {d.get('position') or ph('岗位')} | {d.get('certs') or d.get('title') or '—'} | {r['number'] or '—'} | {note or '—'} |")
    lines.append("")
    for r in rows:
        lines += _images_for(r)
    return "\n".join(lines), [r["id"] for r in rows]


def price_content(doc: dict, ctx: dict) -> str:
    price = db.jload(doc.get("price_json"), {}) or {}
    items = price.get("items") or []
    p = ctx["project"]
    lines = [
        "项目名称：" + _v(p.get("name"), "项目名称") + "　　项目编号：" + _v(p.get("project_no"), "项目编号"),
        "",
        "| 序号 | 名称 | 品牌/规格型号 | 数量 | 单位 | 单价（元） | 小计（元） |",
    ]
    total = 0.0
    for i, it in enumerate(items, 1):
        qty = float(it.get("qty") or 0)
        up = float(it.get("unit_price") or 0)
        sub = round(qty * up, 2)
        total += sub
        lines.append(f"| {i} | {it.get('name') or '—'} | {it.get('spec') or '—'} | {qty:g} | {it.get('unit') or '—'} | {up:,.2f} | {sub:,.2f} |")
    if not items:
        lines.append("| 1 | " + ph("报价明细，在「报价」里填写") + " |  |  |  |  |  |")
    total = round(total, 2)
    lines.append("")
    if items:
        lines.append(f"投标总价（小写）：￥{total:,.2f} 元")
        lines.append("投标总价（大写）：人民币" + number_to_cn_upper(total))
    else:
        lines.append("投标总价（小写）：" + ph("金额"))
        lines.append("投标总价（大写）：" + ph("大写金额"))
    for k, label in (("delivery", "交货期/服务期"), ("warranty", "质保期"), ("tax_note", "报价说明")):
        if price.get(k):
            lines.append(f"{label}：{price[k]}")
    lines.append("\n投标人名称（盖单位公章）：" + _v(ctx["company"].get("name"), "投标人名称") + "\n\n法定代表人或授权代表（签字）：________________")
    return "\n".join(lines)


def deviation_content(ctx: dict) -> str:
    a = ctx["analysis"] or {}
    rows = [s["text"] for s in (a.get("starred") or [])][:30]
    if not rows:
        rows = [r["text"] for r in (a.get("reject_clauses") or []) if r.get("starred")][:30]
    lines = ["说明：逐条响应招标文件的技术要求，★号条款必须完全满足，不允许负偏离。", "", "| 序号 | 招标文件要求 | 投标响应 | 偏离情况 | 说明 |"]
    if rows:
        for i, r in enumerate(rows, 1):
            lines.append(f"| {i} | {r[:200]} | {ph('响应内容')} | {ph('无偏离/正偏离/负偏离')} |  |")
    else:
        lines.append("| 1 | " + ph("按招标文件技术要求逐条填写") + " |  |  |  |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 技术章节

WRITER_SYSTEM = """你是资深投标文件（技术/服务方案）编写专家，负责撰写投标文件中的一个章节。

硬性规则（必须严格遵守）：
1. 只能基于提供的材料撰写：【项目事实】【招标文件相关原文】【企业方案素材】。企业方案素材可借鉴结构和通用表述；
2. 所有项目特定数字（工期、人数、数量、金额、参数、响应时间等）必须来自【项目事实】或【招标文件相关原文】；找不到依据的，一律写【待补充：说明】，禁止编造；
3. 严禁编造或写入：资质证书、获奖、业绩案例、人员姓名与证书、检测报告、合作客户。这些内容在其他章节引用资料库，本章不写；
4. 严禁出现其他项目名称、其他采购人名称或其他公司名称（素材里出现的历史项目信息一律不要照搬）；
5. 紧扣【本章评分要求】逐条响应，每一个得分点都要有对应的小节或段落，让评委一眼找到；
6. 输出简化 Markdown：用 # / ## 表示本章内的小标题，表格用 | 分隔，列表用 - ；不要输出本章标题本身，不要任何解释或开场白。"""


def _retrieve_solutions(company_id: int, query: str, k: int = 5) -> list[dict]:
    rows = db.q(
        "SELECT id, name, content, category FROM library_items WHERE company_id = ? AND category IN ('solution','history_bid') AND content != ''",
        (company_id,),
    )
    chunks = []
    for r in rows:
        for ch in chunk_text(r["content"], 700):
            chunks.append({"item_id": r["id"], "title": r["name"], "text": ch})
    if not chunks:
        return []
    bm = BM25([c["title"] + "\n" + c["text"] for c in chunks])
    return [chunks[i] | {"score": round(s, 2)} for i, s in bm.top(query, k=k, min_score=0.5)]


def _tender_excerpts(text: str, query: str, k: int = 4) -> list[str]:
    if not text:
        return []
    chunks = chunk_text(text, 900)
    if not chunks:
        return []
    bm = BM25(chunks)
    return [chunks[i] for i, _ in bm.top(query, k=k, min_score=0.3)]


def _facts(ctx: dict) -> str:
    p, n = ctx["project"], ctx.get("notice") or {}
    a = ctx["analysis"] or {}
    ki = a.get("key_info") or {}
    lines = [
        "项目名称：" + (p.get("name") or ""),
        "项目编号：" + (p.get("project_no") or "未知"),
        "采购人：" + (p.get("purchaser") or "未知"),
        "地区：" + (p.get("region") or "未知"),
        "品目：" + (p.get("category") or n.get("category") or "未知"),
        "预算/最高限价：" + (fmt_amount(p.get("budget")) or "未知"),
        "投标截止：" + (p.get("deadline") or "未知"),
    ]
    if ki.get("validity"):
        lines.append("投标有效期：" + ki["validity"])
    if a.get("summary"):
        lines.append("项目概要：" + a["summary"])
    return "\n".join(lines)


def generate_tech(section: dict, ctx: dict, *, use_ai: bool = True, extra: str = "") -> tuple[str, list[dict], bool]:
    query = section["title"] + " " + (section.get("requirement") or "")
    sols = _retrieve_solutions(ctx["company"]["id"], query)
    excerpts = _tender_excerpts(tender_text(ctx), query)
    refs = [{"type": "solution", "item_id": s["item_id"], "title": s["title"], "score": s["score"]} for s in sols]
    if use_ai and llm.enabled():
        prompt = (
            "【本章标题】" + section["title"] + "\n"
            "【本章评分要求/编写要求】\n" + (section.get("requirement") or "（无，按标题撰写）") + "\n\n"
            "【项目事实】\n" + _facts(ctx) + "\n\n"
            "【招标文件相关原文】\n" + ("\n---\n".join(excerpts) if excerpts else "（未上传招标文件或无相关内容）") + "\n\n"
            "【企业方案素材（只借鉴写法，不照搬其中的项目数据）】\n"
            + ("\n---\n".join(f"◆ {s['title']}\n{s['text']}" for s in sols) if sols else "（资料库没有相关素材）")
            + "\n\n"
            + ("【本次特别要求】" + extra + "\n\n" if extra else "")
            + "【篇幅】1200~2500 字，结构清晰，多用小标题和表格，便于评委查找得分点。"
        )
        text = llm.chat(prompt, system=WRITER_SYSTEM, feature="write_tech", max_tokens=4096, temperature=0.5)
        return sanitize_ai_text(text, ctx), refs, True
    # 无 AI：搭骨架 + 引用素材
    lines = []
    if section.get("requirement"):
        lines += ["# 评分要求", section["requirement"], ""]
    lines += ["# 响应方案", ph("结合本项目实际情况，逐条响应上述评分要求"), ""]
    if sols:
        lines.append("# 可参考的企业方案素材（请改写后使用，删除不适用内容）")
        for s in sols[:3]:
            demoted = re.sub(r"^(#{1,2})\s", lambda m: "#" * (len(m.group(1)) + 2) + " ", s["text"], flags=re.M)
            lines += ["## 素材：" + s["title"], demoted, ""]
    if excerpts:
        lines.append("# 招标文件相关原文（供对照，定稿前删除本节）")
        for e in excerpts[:2]:
            lines += ["> " + e.replace("\n", " ")[:600], ""]
    return "\n".join(lines).strip(), refs, False


def sanitize_ai_text(text: str, ctx: dict) -> str:
    """AI 输出兜底清洗：去掉代码围栏和开场白；把出现的其他公司名替换为占位。"""
    t = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", (text or "").strip())
    t = re.sub(r"^(好的|以下是|下面是)[^\n]{0,40}\n", "", t)
    own = ctx["company"].get("name") or ""
    others = [r["name"] for r in db.q("SELECT name FROM companies WHERE id != ?", (ctx["company"]["id"],))]
    for o in others:
        if o and o != own and o in t:
            t = t.replace(o, ph("核实：此处出现了其他主体名称"))
    return t


REWRITE_PRESETS = {
    "polish": "在不改变事实和数字的前提下润色，使语言更专业、规范、有条理。",
    "expand": "在不编造任何新事实和数字的前提下扩写，补充实施步骤、保障措施和管理要点，缺依据的数字写【待补充】。",
    "shorten": "精简为原来一半左右的篇幅，保留全部得分点和关键数字。",
    "formal": "改写为正式的投标文件书面语，去掉口语化表达。",
    "score": "对照本章评分要求，检查是否每个得分点都有对应内容，缺失的补上小节（缺依据写【待补充】），并让得分点标题醒目。",
}


def rewrite_section(section_id: int, instruction: str, user_id: int | None = None) -> str:
    s = db.get("bid_sections", section_id)
    doc = db.get("bid_docs", s["doc_id"])
    ctx = project_context(doc["project_id"])
    instr = REWRITE_PRESETS.get(instruction, instruction)
    prompt = (
        "【改写要求】" + instr + "\n\n"
        "【本章标题】" + s["title"] + "\n【本章评分要求】" + (s.get("requirement") or "无") + "\n\n"
        "【项目事实】\n" + _facts(ctx) + "\n\n【原文】\n" + (s["content"] or "")
    )
    text = llm.chat(prompt, system=WRITER_SYSTEM, feature="rewrite", max_tokens=4096, temperature=0.4)
    text = sanitize_ai_text(text, ctx)
    save_section(section_id, text, user_id=user_id, note="AI改写：" + instr[:20], ai=True)
    return text


# ---------------------------------------------------------------- 生成与保存

def save_section(section_id: int, content: str, *, user_id: int | None = None, note: str = "", ai: bool | None = None, refs: list | None = None) -> None:
    s = db.get("bid_sections", section_id)
    if s and (s["content"] or "").strip() and s["content"] != content:
        db.insert("section_versions", {"section_id": section_id, "content": s["content"], "note": note or "修改前版本", "user_id": user_id, "created_at": now_str()})
    upd = {"content": content, "updated_by": user_id, "updated_at": now_str()}
    if ai is not None:
        upd["ai_generated"] = 1 if ai else 0
        upd["reviewed"] = 0
    if refs is not None:
        upd["refs_json"] = db.jdump(refs)
    db.update("bid_sections", section_id, upd)
    db.update("bid_docs", s["doc_id"], {"updated_at": now_str()})


def generate_section(section_id: int, *, user_id: int | None = None, use_ai: bool = True, extra: str = "") -> dict:
    s = db.get("bid_sections", section_id)
    doc = db.get("bid_docs", s["doc_id"])
    ctx = project_context(doc["project_id"])
    refs_in = [r.get("item_id") for r in (db.jload(s["refs_json"], []) or []) if r.get("item_id")]
    kind = s["kind"]
    ai_used = False
    refs: list = []
    if kind == "form":
        content = form_content(s["form_key"], ctx, doc)
    elif kind == "price":
        content = price_content(doc, ctx)
    elif kind == "qualification":
        content, ids = qualification_content(ctx, refs_in or None)
        refs = [{"type": "library", "item_id": i} for i in ids]
    elif kind == "performance":
        content, ids = performance_content(ctx, refs_in or None)
        refs = [{"type": "library", "item_id": i} for i in ids]
    elif kind == "personnel":
        content, ids = personnel_content(ctx, refs_in or None)
        refs = [{"type": "library", "item_id": i} for i in ids]
    elif kind == "deviation":
        content = deviation_content(ctx)
    elif kind == "tech":
        content, refs, ai_used = generate_tech(s, ctx, use_ai=use_ai, extra=extra)
    else:
        content = s["content"] or ""
    save_section(section_id, content, user_id=user_id, note="重新生成", ai=ai_used, refs=refs)
    return db.get("bid_sections", section_id)


def update_price(doc_id: int, price: dict, user_id: int | None = None) -> dict:
    items = []
    total = 0.0
    for it in price.get("items") or []:
        qty = float(it.get("qty") or 0)
        up = float(it.get("unit_price") or 0)
        sub = round(qty * up, 2)
        total += sub
        items.append({**it, "qty": qty, "unit_price": up, "total": sub})
    data = {**price, "items": items, "total": round(total, 2), "updated_at": now_str()}
    db.update("bid_docs", doc_id, {"price_json": db.jdump(data), "updated_at": now_str()})
    doc = db.get("bid_docs", doc_id)
    ctx = project_context(doc["project_id"])
    for s in db.q("SELECT * FROM bid_sections WHERE doc_id = ? AND (kind = 'price' OR form_key = 'bid_letter')", (doc_id,)):
        generate_section(s["id"], user_id=user_id, use_ai=False)
    db.update("projects", ctx["project"]["id"], {"bid_amount": data["total"] or None, "updated_at": now_str()})
    return data


def doc_full(doc_id: int) -> dict:
    doc = db.get("bid_docs", doc_id)
    if not doc:
        raise ValueError("标书不存在")
    secs = db.q("SELECT * FROM bid_sections WHERE doc_id = ? ORDER BY order_no", (doc_id,))
    for s in secs:
        s["refs"] = db.jload(s.pop("refs_json"), [])
        s["placeholders"] = len(re.findall(r"【待补充", s["content"] or ""))
        s["chars"] = len(re.sub(r"\s", "", s["content"] or ""))
    doc["price"] = db.jload(doc.pop("price_json"), {})
    doc["sections"] = secs
    return doc


def date_today_cn() -> str:
    t = today_str()
    return f"{t[:4]}年{int(t[5:7])}月{int(t[8:10])}日"


def tokens_overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
