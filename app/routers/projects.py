"""投标项目：立项、招标文件解析、准备清单、标书编写、合规体检、审核、投递、结果。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import compliance, db, docparse, exporter, jobs, llm, notify, tender_analysis, writer
from ..config import FILES_DIR, now_str
from ..crawler import runner
from ..crawler.base import RobotsDisallowed, StopCrawl
from ..security import current_user, require
from ..textutil import parse_amount_cn
from . import must, num_or_none, pick

router = APIRouter(prefix="/api", tags=["projects"])

STATUSES = {
    "evaluating": "待评估",
    "preparing": "准备中",
    "reviewing": "审核中",
    "ready": "待投递",
    "submitted": "已投递",
    "opened": "已开标",
    "won": "已中标",
    "lost": "未中标",
    "abandoned": "已放弃",
}
PROJECT_FIELDS = [
    "name", "project_no", "purchaser", "agency", "region", "category", "budget", "owner_id", "signup_deadline", "clarify_deadline",
    "deadline", "open_time", "bond_amount", "bond_deadline", "bond_status", "bond_paid_at", "bond_refunded_at", "platform_name",
    "platform_account_id", "ca_holder", "submit_method", "note",
]


def _event(pid: int, user: dict | None, action: str, comment: str = "") -> None:
    db.insert("project_events", {"project_id": pid, "user_id": user["id"] if user else None, "action": action, "comment": comment[:500], "created_at": now_str()})


def _project_out(p: dict) -> dict:
    p["status_name"] = STATUSES.get(p["status"], p["status"])
    p["receipt_file_ids"] = db.jload(p.get("receipt_file_ids"), []) or []
    c = db.get("companies", p["company_id"]) or {}
    p["company_name"] = c.get("short_name") or c.get("name") or ""
    if p.get("owner_id"):
        o = db.get("users", p["owner_id"])
        p["owner_name"] = (o or {}).get("display_name") or (o or {}).get("username", "")
    return p


@router.get("/projects")
def list_projects(status: str = "", company_id: int = 0, q: str = "", user: dict = Depends(current_user)):
    sql = "SELECT * FROM projects WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status IN (" + ",".join("?" for _ in status.split(",")) + ")"
        params += status.split(",")
    if company_id:
        sql += " AND company_id = ?"
        params.append(company_id)
    if q:
        sql += " AND (name LIKE ? OR project_no LIKE ? OR purchaser LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += " ORDER BY CASE WHEN deadline = '' THEN 1 ELSE 0 END, deadline ASC, id DESC"
    rows = [_project_out(r) for r in db.q(sql, params)]
    for r in rows:
        d = db.q1("SELECT id, status FROM bid_docs WHERE project_id = ?", (r["id"],))
        r["doc_status"] = d["status"] if d else ""
        r["doc_id"] = d["id"] if d else None
    return {"items": rows, "statuses": STATUSES}


@router.post("/projects")
def create_project(payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer"))):
    cid = int(payload.get("company_id") or 0)
    must(db.get("companies", cid), "投标主体")
    data = pick(payload, PROJECT_FIELDS)
    nid = payload.get("notice_id")
    n = None
    if nid:
        n = must(db.get("notices", int(nid)), "标讯")
        exist = db.q1("SELECT id FROM projects WHERE notice_id = ? AND company_id = ?", (n["id"], cid))
        if exist:
            return _project_out(db.get("projects", exist["id"]))
        if not data.get("name"):
            data["name"] = n.get("project_name") or re.sub(r"(的)?(公开招标|竞争性磋商|竞争性谈判|询价|单一来源|招标|采购)?公告.*$", "", n["title"]) or n["title"]
        for k_src, k_dst in (("project_no", "project_no"), ("purchaser", "purchaser"), ("agency", "agency"), ("region", "region"),
                             ("category", "category"), ("budget", "budget"), ("deadline", "deadline"), ("open_time", "open_time"), ("doc_get_end", "signup_deadline")):
            if n.get(k_src) not in (None, "") and not data.get(k_dst):
                data[k_dst] = n[k_src]
        if n.get("bond_text") and not data.get("bond_amount"):
            data["bond_amount"] = parse_amount_cn(n["bond_text"])
    if not data.get("name"):
        raise HTTPException(400, "请填写项目名称")
    for k in ("budget", "bond_amount"):
        if k in data:
            data[k] = num_or_none(data[k])
    data.update(
        {
            "company_id": cid,
            "notice_id": n["id"] if n else None,
            "status": payload.get("status") or "preparing",
            "owner_id": data.get("owner_id") or user["id"],
            "created_by": user["id"],
            "created_at": now_str(),
            "updated_at": now_str(),
        }
    )
    pid = db.insert("projects", data)
    _event(pid, user, "立项", data["name"])
    if n:
        db.ex("UPDATE matches SET status = 'project', updated_at = ? WHERE notice_id = ? AND company_id = ?", (now_str(), n["id"], cid))
        others = db.q("SELECT p.id, c.name FROM projects p JOIN companies c ON c.id = p.company_id WHERE p.notice_id = ? AND p.company_id != ?", (n["id"], cid))
        if others:
            notify.add(
                "串标风险提示：同一项目有多个主体立项",
                "、".join(o["name"] for o in others) + " 也在投这个项目。存在控股/管理关系或负责人为同一人的单位不得参加同一标段投标，请负责人确认。",
                kind="warning",
                link=f"#/projects/{pid}",
                dedup_key=f"multi:{n['id']}:{cid}",
            )
    db.audit(user, "立项", data["name"])
    return _project_out(db.get("projects", pid))


@router.get("/projects/{pid}")
def get_project(pid: int, user: dict = Depends(current_user)):
    p = _project_out(must(db.get("projects", pid), "项目"))
    p["events"] = db.q(
        "SELECT e.*, u.display_name AS user_name FROM project_events e LEFT JOIN users u ON u.id = e.user_id WHERE e.project_id = ? ORDER BY e.id DESC LIMIT 100",
        (pid,),
    )
    p["notice"] = None
    if p.get("notice_id"):
        n = db.get("notices", p["notice_id"])
        if n:
            p["notice"] = {"id": n["id"], "title": n["title"], "url": n["url"], "attachments": db.jload(n["attachments_json"], [])}
    if p.get("result_notice_id"):
        rn = db.get("notices", p["result_notice_id"])
        p["result_notice"] = {"id": rn["id"], "title": rn["title"], "url": rn["url"], "winners": db.jload(rn["winners_json"], [])} if rn else None
    d = db.q1("SELECT id, status, updated_at FROM bid_docs WHERE project_id = ?", (pid,))
    p["doc"] = d
    p["same_notice_projects"] = (
        db.q(
            "SELECT p.id, p.name, c.name AS company_name FROM projects p JOIN companies c ON c.id = p.company_id WHERE p.notice_id = ? AND p.id != ? AND p.company_id != ?",
            (p["notice_id"], pid, p["company_id"]),
        )
        if p.get("notice_id")
        else []
    )
    return p


@router.put("/projects/{pid}")
def update_project(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    p = must(db.get("projects", pid), "项目")
    data = pick(payload, PROJECT_FIELDS)
    bond_keys = {"bond_status", "bond_paid_at", "bond_refunded_at"}
    if bond_keys & set(data) and user["role"] not in ("admin", "manager", "finance", "sales"):
        raise HTTPException(403, "保证金状态由财务/商务/负责人维护")
    for k in ("budget", "bond_amount"):
        if k in data:
            data[k] = num_or_none(data[k])
    data["updated_at"] = now_str()
    db.update("projects", pid, data)
    changed = [k for k in data if k != "updated_at" and str(data[k]) != str(p.get(k))]
    if changed:
        _event(pid, user, "修改项目信息", "、".join(changed)[:200])
    return get_project(pid, user)


@router.post("/projects/{pid}/status")
def set_status(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("projects", pid), "项目")
    st = payload.get("status")
    if st not in STATUSES:
        raise HTTPException(400, "状态不正确")
    db.update("projects", pid, {"status": st, "updated_at": now_str()})
    _event(pid, user, "状态变更为「" + STATUSES[st] + "」", payload.get("comment") or "")
    return get_project(pid, user)


@router.delete("/projects/{pid}")
def delete_project(pid: int, user: dict = Depends(require("admin", "manager"))):
    p = must(db.get("projects", pid), "项目")
    for d in db.q("SELECT id FROM bid_docs WHERE project_id = ?", (pid,)):
        for s in db.q("SELECT id FROM bid_sections WHERE doc_id = ?", (d["id"],)):
            db.ex("DELETE FROM section_versions WHERE section_id = ?", (s["id"],))
        db.ex("DELETE FROM bid_sections WHERE doc_id = ?", (d["id"],))
        db.ex("DELETE FROM checks WHERE doc_id = ?", (d["id"],))
    db.ex("DELETE FROM bid_docs WHERE project_id = ?", (pid,))
    db.ex("DELETE FROM tender_docs WHERE project_id = ?", (pid,))
    db.ex("DELETE FROM project_events WHERE project_id = ?", (pid,))
    db.ex("DELETE FROM projects WHERE id = ?", (pid,))
    if p.get("notice_id"):
        db.ex("UPDATE matches SET status = 'fav' WHERE notice_id = ? AND company_id = ?", (p["notice_id"], p["company_id"]))
    db.audit(user, "删除项目", p["name"])
    return {"ok": True}


@router.post("/projects/{pid}/review")
def review(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    p = must(db.get("projects", pid), "项目")
    action = payload.get("action")
    comment = payload.get("comment") or ""
    d = db.q1("SELECT * FROM bid_docs WHERE project_id = ?", (pid,))
    if action == "submit":
        if not d:
            raise HTTPException(400, "还没有标书，不能提交审核")
        last = db.q1("SELECT result_json FROM checks WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (d["id"],))
        if not last:
            raise HTTPException(400, "请先运行一次「合规体检」再提交审核")
        db.update("bid_docs", d["id"], {"status": "reviewing", "updated_at": now_str()})
        db.update("projects", pid, {"status": "reviewing", "updated_at": now_str()})
        _event(pid, user, "提交审核", comment)
        for u in db.q("SELECT id FROM users WHERE role IN ('reviewer','manager') AND active = 1"):
            notify.add("待审核标书：" + p["name"], comment, kind="review", link=f"#/projects/{pid}", user_id=u["id"])
    elif action in ("approve", "reject"):
        if user["role"] not in ("admin", "manager", "reviewer"):
            raise HTTPException(403, "只有审核人或负责人可以审核")
        if not d:
            raise HTTPException(400, "没有标书")
        if action == "approve":
            last = db.q1("SELECT result_json FROM checks WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (d["id"],))
            res = db.jload(last["result_json"], {}) if last else {}
            if res.get("summary", {}).get("error"):
                if not payload.get("force"):
                    raise HTTPException(400, f"最近一次合规体检还有 {res['summary']['error']} 项必须修改，修改后重新体检再审核通过")
            db.update("bid_docs", d["id"], {"status": "approved", "updated_at": now_str()})
            db.ex("UPDATE bid_sections SET reviewed = 1 WHERE doc_id = ?", (d["id"],))
            db.update("projects", pid, {"status": "ready", "updated_at": now_str()})
            _event(pid, user, "审核通过", comment)
        else:
            db.update("bid_docs", d["id"], {"status": "draft", "updated_at": now_str()})
            db.update("projects", pid, {"status": "preparing", "updated_at": now_str()})
            _event(pid, user, "审核退回", comment)
        if p.get("owner_id"):
            notify.add(("标书审核通过：" if action == "approve" else "标书被退回：") + p["name"], comment, kind="review", link=f"#/projects/{pid}", user_id=p["owner_id"])
    else:
        raise HTTPException(400, "操作不正确")
    return get_project(pid, user)


@router.post("/projects/{pid}/submission")
def record_submission(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    """登记投递：系统不代替投递，投标文件须由经办人持 CA 在官方平台上传；这里记录投递信息和回执。"""
    must(db.get("projects", pid), "项目")
    data = pick(payload, ["submitted_at", "platform_name", "platform_account_id", "ca_holder", "submit_method"])
    data["receipt_file_ids"] = db.jdump([int(i) for i in payload.get("receipt_file_ids") or [] if str(i).isdigit()])
    data["submitted_at"] = data.get("submitted_at") or now_str()[:16]
    data["status"] = "submitted"
    data["updated_at"] = now_str()
    db.update("projects", pid, data)
    _event(pid, user, "登记已投递", f"{data.get('platform_name', '')} {data.get('submit_method', '')}")
    return get_project(pid, user)


@router.post("/projects/{pid}/result")
def record_result(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("projects", pid), "项目")
    res = payload.get("result")
    if res not in ("won", "lost", "failed", "abandoned"):
        raise HTTPException(400, "结果不正确")
    data = pick(payload, ["winner", "result_note"])
    data["result"] = res
    data["result_amount"] = num_or_none(payload.get("result_amount"))
    data["our_score"] = num_or_none(payload.get("our_score"))
    data["status"] = {"won": "won", "lost": "lost", "failed": "lost", "abandoned": "abandoned"}[res]
    data["updated_at"] = now_str()
    db.update("projects", pid, data)
    _event(pid, user, "登记结果：" + {"won": "中标", "lost": "未中标", "failed": "流标/废标", "abandoned": "放弃"}[res], data.get("result_note") or "")
    # 未中标复盘：把最终标书存入历史标书库，标记结果
    if payload.get("archive", True):
        d = db.q1("SELECT id FROM bid_docs WHERE project_id = ?", (pid,))
        if d:
            p = db.get("projects", pid)
            full = "\n\n".join("# " + s["title"] + "\n" + (s["content"] or "") for s in db.q("SELECT * FROM bid_sections WHERE doc_id = ? AND kind = 'tech' ORDER BY order_no", (d["id"],)))
            if full.strip():
                db.insert(
                    "library_items",
                    {
                        "company_id": p["company_id"],
                        "category": "history_bid",
                        "name": p["name"] + ("（中标）" if res == "won" else "（未中标）"),
                        "tags": p.get("category") or "",
                        "content": full,
                        "data_json": db.jdump({"result": res, "project_id": pid, "score": data.get("our_score")}),
                        "file_ids": "[]",
                        "created_by": user["id"],
                        "created_at": now_str(),
                        "updated_at": now_str(),
                    },
                )
    return get_project(pid, user)


@router.post("/projects/{pid}/download_attachments")
def project_download_attachments(pid: int, user: dict = Depends(current_user)):
    p = must(db.get("projects", pid), "项目")
    if not p.get("notice_id"):
        raise HTTPException(400, "该项目没有关联公告")

    def task(jid, project_id, notice_id):
        jobs.progress(jid, 0, message="正在下载公告附件…")
        res = runner.download_attachments(notice_id, user["id"])
        parsed = []
        for r in res:
            if r.get("file_id") and re.search(r"\.(pdf|docx)$", r["name"], re.I):
                try:
                    td = _add_tender_doc(project_id, r["file_id"], use_ai=False)
                    parsed.append(td["id"])
                except Exception as e:  # noqa: BLE001
                    r["error"] = f"解析失败：{e}"
        return {"files": res, "tender_doc_ids": parsed}

    jid = jobs.start("attachments", task, pid, p["notice_id"], user_id=user["id"])
    return {"job_id": jid}


# ---------------------------------------------------------------- 招标文件

def _add_tender_doc(pid: int, file_id: int, use_ai: bool) -> dict:
    f = must(db.get("files", file_id), "文件")
    res = docparse.extract(f["stored_path"], f["filename"])
    tdir = FILES_DIR / "text"
    tdir.mkdir(parents=True, exist_ok=True)
    t = now_str()
    tid = db.insert(
        "tender_docs",
        {"project_id": pid, "file_id": file_id, "filename": f["filename"], "text_chars": len(res["text"]), "pages": res["pages"], "warning": res["warning"], "created_at": t, "updated_at": t},
    )
    tpath = tdir / f"tender_{tid}.txt"
    tpath.write_text(res["text"], encoding="utf-8")
    import json

    (tdir / f"tender_{tid}.tables.json").write_text(json.dumps(res.get("tables") or [], ensure_ascii=False), encoding="utf-8")
    analysis = tender_analysis.rule_analyze(res["text"], res.get("tables"))
    db.update("tender_docs", tid, {"text_path": str(tpath), "analysis_json": db.jdump(analysis), "analysis_by": "rule"})
    _apply_key_info(pid, analysis)
    return _td_out(db.get("tender_docs", tid))


def _apply_key_info(pid: int, analysis: dict) -> None:
    p = db.get("projects", pid)
    ki = analysis.get("key_info") or {}
    upd = {}
    for src, dst in (("deadline", "deadline"), ("open_time", "open_time"), ("clarify_deadline", "clarify_deadline"), ("bond_deadline", "bond_deadline")):
        if ki.get(src) and not p.get(dst):
            upd[dst] = ki[src]
    if ki.get("bond_amount") and not p.get("bond_amount"):
        try:
            upd["bond_amount"] = float(ki["bond_amount"])
        except (TypeError, ValueError):
            pass
    if ki.get("platform") and not p.get("platform_name"):
        upd["platform_name"] = str(ki["platform"])[:100]
    if upd:
        upd["updated_at"] = now_str()
        db.update("projects", pid, upd)


def _td_out(td: dict) -> dict:
    td["analysis"] = db.jload(td.pop("analysis_json"), {}) or {}
    td.pop("text_path", None)
    return td


@router.get("/projects/{pid}/tender_docs")
def list_tender_docs(pid: int, user: dict = Depends(current_user)):
    return [_td_out(r) for r in db.q("SELECT * FROM tender_docs WHERE project_id = ? ORDER BY id DESC", (pid,))]


@router.post("/projects/{pid}/tender_docs")
def add_tender_doc(pid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("projects", pid), "项目")
    try:
        td = _add_tender_doc(pid, int(payload.get("file_id") or 0), use_ai=False)
    except docparse.UnsupportedFile as e:
        raise HTTPException(400, str(e)) from e
    _event(pid, user, "上传招标文件", td["filename"])
    return td


@router.post("/tender_docs/{tid}/analyze")
def analyze_tender_doc(tid: int, payload: dict = Body(default={}), user: dict = Depends(current_user)):
    td = must(db.get("tender_docs", tid), "招标文件")
    text = Path(td["text_path"]).read_text(encoding="utf-8") if td.get("text_path") and Path(td["text_path"]).exists() else ""
    tables_path = Path(td["text_path"].replace(".txt", ".tables.json")) if td.get("text_path") else None
    tables = db.jload(tables_path.read_text(encoding="utf-8"), []) if tables_path and tables_path.exists() else []
    use_ai = bool(payload.get("ai"))
    if use_ai and not llm.enabled():
        raise HTTPException(400, "尚未配置 AI 模型，可以先用规则解析结果")
    if not use_ai:
        analysis = tender_analysis.rule_analyze(text, tables)
        db.update("tender_docs", tid, {"analysis_json": db.jdump(analysis), "analysis_by": "rule", "updated_at": now_str()})
        _apply_key_info(td["project_id"], analysis)
        return _td_out(db.get("tender_docs", tid))

    def task(jid, tender_id):
        jobs.progress(jid, 0, 5, "AI 正在分专项解析（资格、评分、废标、格式、时间）…")
        analysis = tender_analysis.analyze(text, tables, use_ai=True)
        db.update("tender_docs", tender_id, {"analysis_json": db.jdump(analysis), "analysis_by": analysis.get("by", "ai"), "updated_at": now_str()})
        _apply_key_info(td["project_id"], analysis)
        return {"tender_doc_id": tender_id, "warnings": analysis.get("warnings", [])}

    jid = jobs.start("tender_ai", task, tid, user_id=user["id"], total=5)
    _event(td["project_id"], user, "AI 解析招标文件", td["filename"])
    return {"job_id": jid}


@router.put("/tender_docs/{tid}/analysis")
def save_analysis(tid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    """人工修订解析结果（增删评分项、改资格要求）。"""
    must(db.get("tender_docs", tid), "招标文件")
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise HTTPException(400, "解析结果格式不正确")
    analysis["edited_by"] = user.get("display_name") or user["username"]
    db.update("tender_docs", tid, {"analysis_json": db.jdump(analysis), "updated_at": now_str()})
    return _td_out(db.get("tender_docs", tid))


@router.delete("/tender_docs/{tid}")
def delete_tender_doc(tid: int, user: dict = Depends(current_user)):
    db.ex("DELETE FROM tender_docs WHERE id = ?", (tid,))
    return {"ok": True}


@router.get("/projects/{pid}/checklist")
def checklist(pid: int, user: dict = Depends(current_user)):
    p = must(db.get("projects", pid), "项目")
    td = db.q1("SELECT analysis_json FROM tender_docs WHERE project_id = ? ORDER BY id DESC LIMIT 1", (pid,))
    analysis = db.jload(td["analysis_json"], {}) if td else {}
    if not analysis and p.get("notice_id"):
        n = db.get("notices", p["notice_id"])
        analysis = {"qualifications": [{"text": ln, "type": tender_analysis._qual_type(ln)} for ln in (n.get("qualification_text") or "").split("\n") if len(ln) > 5]}
    return {"items": tender_analysis.build_checklist(analysis or {}, p["company_id"], p.get("deadline") or ""), "key_info": (analysis or {}).get("key_info") or {}}


@router.get("/projects/{pid}/checklist/export")
def export_checklist(pid: int, user: dict = Depends(current_user)):
    data = checklist(pid, user)
    path = exporter.export_checklist(pid, data["items"], data["key_info"])
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ---------------------------------------------------------------- 标书

@router.get("/projects/{pid}/bid_doc")
def get_bid_doc(pid: int, user: dict = Depends(current_user)):
    d = db.q1("SELECT id FROM bid_docs WHERE project_id = ?", (pid,))
    return writer.doc_full(d["id"]) if d else None


@router.post("/projects/{pid}/bid_doc")
def create_bid_doc(pid: int, user: dict = Depends(require("manager", "sales", "writer"))):
    must(db.get("projects", pid), "项目")
    did = writer.create_bid_doc(pid, user["id"])
    _event(pid, user, "创建标书", "按招标文件目录生成框架")
    return writer.doc_full(did)


@router.post("/projects/{pid}/bid_doc/rebuild")
def rebuild_bid_doc(pid: int, user: dict = Depends(require("manager", "writer"))):
    """按最新的招标文件解析结果重建目录（会清空现有内容，旧内容保留在版本记录里）。"""
    d = db.q1("SELECT id FROM bid_docs WHERE project_id = ?", (pid,))
    if d:
        for s in db.q("SELECT * FROM bid_sections WHERE doc_id = ?", (d["id"],)):
            if (s["content"] or "").strip():
                db.insert("section_versions", {"section_id": s["id"], "content": s["content"], "note": "重建目录前备份：" + s["title"], "user_id": user["id"], "created_at": now_str()})
        db.ex("DELETE FROM bid_sections WHERE doc_id = ?", (d["id"],))
        db.ex("DELETE FROM bid_docs WHERE id = ?", (d["id"],))
    did = writer.create_bid_doc(pid, user["id"])
    _event(pid, user, "重建标书目录")
    return writer.doc_full(did)


@router.put("/bid_docs/{did}")
def update_doc(did: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("bid_docs", did), "标书")
    data = pick(payload, ["title"])
    data["updated_at"] = now_str()
    db.update("bid_docs", did, data)
    return writer.doc_full(did)


@router.put("/bid_docs/{did}/price")
def update_price(did: int, payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer", "finance"))):
    must(db.get("bid_docs", did), "标书")
    return writer.update_price(did, payload, user["id"])


@router.post("/bid_docs/{did}/sections")
def add_section(did: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("bid_docs", did), "标书")
    after = payload.get("after_id")
    order = 0.0
    if after:
        a = db.get("bid_sections", int(after))
        nxt = db.q1("SELECT order_no FROM bid_sections WHERE doc_id = ? AND order_no > ? ORDER BY order_no LIMIT 1", (did, a["order_no"]))
        order = (a["order_no"] + nxt["order_no"]) / 2 if nxt else a["order_no"] + 1
    else:
        order = (db.scalar("SELECT MAX(order_no) FROM bid_sections WHERE doc_id = ?", (did,)) or 0) + 1
    kind = payload.get("kind") or "text"
    if kind not in ("text", "tech", "form", "price", "qualification", "performance", "personnel", "deviation"):
        raise HTTPException(400, "章节类型不正确")
    sid = db.insert(
        "bid_sections",
        {
            "doc_id": did,
            "order_no": order,
            "level": int(payload.get("level") or 1),
            "title": payload.get("title") or "新章节",
            "kind": kind,
            "form_key": payload.get("form_key") or "",
            "requirement": payload.get("requirement") or "",
            "content": payload.get("content") or "",
            "updated_by": user["id"],
            "updated_at": now_str(),
        },
    )
    return db.get("bid_sections", sid)


@router.put("/sections/{sid}")
def update_section(sid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    s = must(db.get("bid_sections", sid), "章节")
    doc = db.get("bid_docs", s["doc_id"])
    if doc["status"] == "approved" and "content" in payload and not payload.get("force"):
        raise HTTPException(400, "标书已审核通过，修改内容需先退回（或勾选强制修改）")
    data = pick(payload, ["title", "level", "requirement", "kind", "form_key"])
    if "reviewed" in payload:
        data["reviewed"] = 1 if payload["reviewed"] else 0
    if "refs" in payload:
        data["refs_json"] = db.jdump([{"type": "library", "item_id": int(i)} for i in payload["refs"] or [] if str(i).isdigit()])
    if data:
        data["updated_at"] = now_str()
        db.update("bid_sections", sid, data)
    if "content" in payload:
        writer.save_section(sid, payload["content"] or "", user_id=user["id"], note=payload.get("note") or "手动编辑")
    if "refs" in payload and s["kind"] in ("qualification", "performance", "personnel"):
        writer.generate_section(sid, user_id=user["id"], use_ai=False)
    out = db.get("bid_sections", sid)
    out["refs"] = db.jload(out.pop("refs_json"), [])
    return out


@router.delete("/sections/{sid}")
def delete_section(sid: int, user: dict = Depends(current_user)):
    s = must(db.get("bid_sections", sid), "章节")
    if (s["content"] or "").strip():
        db.insert("section_versions", {"section_id": sid, "content": s["content"], "note": "删除前备份：" + s["title"], "user_id": user["id"], "created_at": now_str()})
    db.ex("DELETE FROM bid_sections WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/sections/{sid}/move")
def move_section(sid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    s = must(db.get("bid_sections", sid), "章节")
    up = payload.get("direction") == "up"
    other = db.q1(
        "SELECT * FROM bid_sections WHERE doc_id = ? AND order_no " + ("<" if up else ">") + " ? ORDER BY order_no " + ("DESC" if up else "ASC") + " LIMIT 1",
        (s["doc_id"], s["order_no"]),
    )
    if other:
        db.update("bid_sections", sid, {"order_no": other["order_no"]})
        db.update("bid_sections", other["id"], {"order_no": s["order_no"]})
    return {"ok": True}


@router.post("/sections/{sid}/generate")
def generate(sid: int, payload: dict = Body(default={}), user: dict = Depends(current_user)):
    s = must(db.get("bid_sections", sid), "章节")
    use_ai = bool(payload.get("ai", True)) and s["kind"] == "tech"
    if use_ai and not llm.enabled():
        use_ai = False
    if not use_ai:
        out = writer.generate_section(sid, user_id=user["id"], use_ai=False)
        out["refs"] = db.jload(out.pop("refs_json"), [])
        return out

    def task(jid, section_id):
        jobs.progress(jid, 0, 1, "AI 正在起草「" + s["title"] + "」…")
        writer.generate_section(section_id, user_id=user["id"], use_ai=True, extra=payload.get("extra") or "")
        return {"section_id": section_id}

    return {"job_id": jobs.start("write_section", task, sid, user_id=user["id"], total=1)}


@router.post("/sections/{sid}/rewrite")
def rewrite(sid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    must(db.get("bid_sections", sid), "章节")
    if not llm.enabled():
        raise HTTPException(400, "尚未配置 AI 模型")
    instr = payload.get("instruction") or "polish"

    def task(jid, section_id):
        jobs.progress(jid, 0, 1, "AI 改写中…")
        writer.rewrite_section(section_id, instr, user["id"])
        return {"section_id": section_id}

    return {"job_id": jobs.start("rewrite", task, sid, user_id=user["id"], total=1)}


@router.get("/sections/{sid}/versions")
def versions(sid: int, user: dict = Depends(current_user)):
    return db.q(
        "SELECT v.id, v.note, v.created_at, u.display_name AS user_name, length(v.content) AS chars, substr(v.content, 1, 300) AS preview "
        "FROM section_versions v LEFT JOIN users u ON u.id = v.user_id WHERE v.section_id = ? ORDER BY v.id DESC LIMIT 50",
        (sid,),
    )


@router.post("/sections/{sid}/restore")
def restore(sid: int, payload: dict = Body(...), user: dict = Depends(current_user)):
    v = must(db.get("section_versions", int(payload.get("version_id") or 0)), "版本")
    if v["section_id"] != sid:
        raise HTTPException(400, "版本不属于该章节")
    writer.save_section(sid, v["content"] or "", user_id=user["id"], note="恢复到 " + v["created_at"])
    return {"ok": True}


@router.post("/bid_docs/{did}/generate_all")
def generate_all(did: int, payload: dict = Body(default={}), user: dict = Depends(current_user)):
    must(db.get("bid_docs", did), "标书")
    overwrite = bool(payload.get("overwrite"))
    use_ai = llm.enabled() and payload.get("ai", True)
    secs = db.q("SELECT * FROM bid_sections WHERE doc_id = ? ORDER BY order_no", (did,))
    targets = [s for s in secs if s["kind"] != "text" and (overwrite or not (s["content"] or "").strip() or (s["kind"] == "tech" and not s["ai_generated"] and "【待补充：结合本项目" in (s["content"] or "")))]

    def task(jid, doc_id):
        done, errors = 0, []
        for s in targets:
            jobs.progress(jid, done, len(targets), "正在生成「" + s["title"] + "」…")
            try:
                writer.generate_section(s["id"], user_id=user["id"], use_ai=bool(use_ai))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{s['title']}：{e}")
            done += 1
        jobs.progress(jid, done, len(targets), "完成")
        return {"generated": done - len(errors), "errors": errors}

    return {"job_id": jobs.start("generate_all", task, did, user_id=user["id"], total=len(targets)), "count": len(targets)}


@router.post("/bid_docs/{did}/check")
def check(did: int, payload: dict = Body(default={}), user: dict = Depends(current_user)):
    must(db.get("bid_docs", did), "标书")
    if payload.get("ai") and llm.enabled():
        def task(jid, doc_id):
            jobs.progress(jid, 0, 1, "正在体检（含 AI 逐条核对★条款）…")
            return compliance.run_check(doc_id, use_ai=True, user_id=user["id"])

        return {"job_id": jobs.start("check", task, did, user_id=user["id"], total=1)}
    return compliance.run_check(did, use_ai=False, user_id=user["id"])


@router.get("/bid_docs/{did}/checks")
def check_history(did: int, user: dict = Depends(current_user)):
    rows = db.q("SELECT id, summary, created_at, result_json FROM checks WHERE doc_id = ? ORDER BY id DESC LIMIT 10", (did,))
    for r in rows:
        r["result"] = db.jload(r.pop("result_json"), {})
    return rows


@router.get("/bid_docs/{did}/export")
def export(did: int, user: dict = Depends(current_user)):
    d = must(db.get("bid_docs", did), "标书")
    path = exporter.export_bid_doc(did)
    _event(d["project_id"], user, "导出 Word 投标文件", path.name)
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
