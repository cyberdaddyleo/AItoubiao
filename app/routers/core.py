"""投标主体、企业资料库、文件、订阅画像、平台账号/CA 台账。"""
from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import db, docparse, matching
from ..config import FILES_DIR, MAX_UPLOAD_MB, now, now_str
from ..security import current_user, require
from . import must, num_or_none, pick

router = APIRouter(prefix="/api", tags=["core"])

COMPANY_FIELDS = [
    "name", "short_name", "credit_code", "legal_person", "legal_person_title", "reg_capital", "founded_date", "address",
    "postcode", "phone", "fax", "email", "bank_name", "bank_account_note", "contact_person", "contact_phone",
    "business_scope", "intro", "is_sme", "annual_revenue_wan", "staff_count", "industries",
]
LIB_CATEGORIES = {
    "basic": "基础资料",
    "qualification": "资质证书",
    "performance": "业绩",
    "personnel": "人员",
    "solution": "方案素材",
    "history_bid": "历史标书",
}
LIB_FIELDS = ["name", "number", "issuer", "level", "valid_from", "valid_to", "tags", "content"]


# ---------------------------------------------------------------- 主体

@router.get("/companies")
def list_companies(user: dict = Depends(current_user)):
    rows = db.q("SELECT * FROM companies ORDER BY id")
    for r in rows:
        r["library_count"] = db.scalar("SELECT COUNT(*) FROM library_items WHERE company_id = ?", (r["id"],))
        r["subscription_count"] = db.scalar("SELECT COUNT(*) FROM subscriptions WHERE company_id = ?", (r["id"],))
    return rows


@router.get("/companies/{cid}")
def get_company(cid: int, user: dict = Depends(current_user)):
    return must(db.get("companies", cid), "主体")


@router.post("/companies")
def create_company(payload: dict = Body(...), user: dict = Depends(require("manager", "sales"))):
    data = pick(payload, COMPANY_FIELDS)
    if not data.get("name"):
        raise HTTPException(400, "请填写主体全称（与营业执照一致）")
    for k in ("annual_revenue_wan",):
        if k in data:
            data[k] = num_or_none(data[k])
    if "staff_count" in data:
        data["staff_count"] = int(data["staff_count"]) if str(data["staff_count"]).strip().isdigit() else None
    data["is_sme"] = 1 if data.get("is_sme") in (1, True, "1", "true") else 0
    data.update({"created_at": now_str(), "updated_at": now_str()})
    cid = db.insert("companies", data)
    db.audit(user, "新建主体", data["name"])
    return db.get("companies", cid)


@router.put("/companies/{cid}")
def update_company(cid: int, payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer"))):
    must(db.get("companies", cid), "主体")
    data = pick(payload, COMPANY_FIELDS)
    if "annual_revenue_wan" in data:
        data["annual_revenue_wan"] = num_or_none(data["annual_revenue_wan"])
    if "staff_count" in data:
        data["staff_count"] = int(data["staff_count"]) if str(data["staff_count"]).strip().isdigit() else None
    if "is_sme" in data:
        data["is_sme"] = 1 if data["is_sme"] in (1, True, "1", "true") else 0
    data["updated_at"] = now_str()
    db.update("companies", cid, data)
    db.audit(user, "修改主体", str(cid))
    return db.get("companies", cid)


@router.delete("/companies/{cid}")
def delete_company(cid: int, user: dict = Depends(require("admin"))):
    must(db.get("companies", cid), "主体")
    if db.scalar("SELECT COUNT(*) FROM projects WHERE company_id = ?", (cid,)):
        raise HTTPException(400, "该主体下还有投标项目，不能删除")
    for t in ("library_items", "subscriptions", "matches", "platform_accounts"):
        db.ex(f"DELETE FROM {t} WHERE company_id = ?", (cid,))
    db.ex("DELETE FROM companies WHERE id = ?", (cid,))
    db.audit(user, "删除主体", str(cid))
    return {"ok": True}


# ---------------------------------------------------------------- 文件

def save_upload(f: UploadFile, user_id: int | None, kind: str = "") -> dict:
    name = Path(f.filename or "file").name
    name = re.sub(r'[\\/:*?"<>|]', "_", name)[:120] or "file"
    sub = FILES_DIR / now().strftime("%Y%m")
    sub.mkdir(parents=True, exist_ok=True)
    dest = sub / (uuid.uuid4().hex[:10] + "_" + name)
    h = hashlib.sha256()
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = f.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"文件超过 {MAX_UPLOAD_MB}MB")
            h.update(chunk)
            out.write(chunk)
    fid = db.insert(
        "files",
        {
            "filename": name,
            "stored_path": str(dest),
            "mime": f.content_type or mimetypes.guess_type(name)[0] or "",
            "size": size,
            "sha256": h.hexdigest(),
            "kind": kind,
            "uploaded_by": user_id,
            "created_at": now_str(),
        },
    )
    return db.get("files", fid)


def file_public(f: dict) -> dict:
    return {"id": f["id"], "filename": f["filename"], "size": f["size"], "mime": f["mime"], "created_at": f["created_at"]}


@router.post("/files")
def upload_file(file: UploadFile = File(...), kind: str = Form(""), user: dict = Depends(current_user)):
    return file_public(save_upload(file, user["id"], kind))


@router.get("/files/{fid}")
def download_file(fid: int, inline: int = 0, user: dict = Depends(current_user)):
    f = must(db.get("files", fid), "文件")
    p = Path(f["stored_path"])
    if not p.exists():
        raise HTTPException(404, "文件已丢失")
    media = f["mime"] or mimetypes.guess_type(f["filename"])[0] or "application/octet-stream"
    if inline:
        return FileResponse(p, media_type=media, headers={"Content-Disposition": "inline"})
    return FileResponse(p, media_type=media, filename=f["filename"])


@router.post("/files/meta")
def files_meta(payload: dict = Body(...), user: dict = Depends(current_user)):
    ids = [int(i) for i in payload.get("ids") or [] if str(i).isdigit()][:200]
    if not ids:
        return []
    return [file_public(f) for f in db.q(f"SELECT * FROM files WHERE id IN ({','.join('?' for _ in ids)})", ids)]


# ---------------------------------------------------------------- 资料库

def _lib_out(r: dict) -> dict:
    r["data"] = db.jload(r.pop("data_json"), {}) or {}
    r["file_ids"] = db.jload(r["file_ids"], []) or []
    r["category_name"] = LIB_CATEGORIES.get(r["category"], r["category"])
    vt = r.get("valid_to") or ""
    today = now().strftime("%Y-%m-%d")
    soon = (now() + timedelta(days=int(db.get_setting("expire_warn_days") or 30))).strftime("%Y-%m-%d")
    r["expire_state"] = "expired" if vt and vt < today else ("soon" if vt and vt <= soon else "")
    if r["category"] in ("solution", "history_bid"):
        r["content_preview"] = (r.get("content") or "")[:200]
        r["content_chars"] = len(r.get("content") or "")
        r.pop("content", None)
    return r


@router.get("/library")
def list_library(company_id: int, category: str = "", q: str = "", user: dict = Depends(current_user)):
    sql = "SELECT * FROM library_items WHERE company_id = ?"
    params: list = [company_id]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if q:
        sql += " AND (name LIKE ? OR tags LIKE ? OR number LIKE ? OR content LIKE ?)"
        params += [f"%{q}%"] * 4
    sql += " ORDER BY category, id DESC"
    return [_lib_out(r) for r in db.q(sql, params)]


@router.get("/library/expiring")
def expiring(days: int = 60, user: dict = Depends(current_user)):
    limit = (now() + timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.q(
        "SELECT l.*, c.name AS company_name FROM library_items l JOIN companies c ON c.id = l.company_id "
        "WHERE l.valid_to != '' AND l.valid_to <= ? ORDER BY l.valid_to",
        (limit,),
    )
    return [_lib_out(r) for r in rows]


@router.get("/library/{iid}")
def get_library_item(iid: int, user: dict = Depends(current_user)):
    r = must(db.get("library_items", iid), "资料")
    r["data"] = db.jload(r.pop("data_json"), {}) or {}
    r["file_ids"] = db.jload(r["file_ids"], []) or []
    return r


def _lib_data(payload: dict) -> dict:
    data = pick(payload, LIB_FIELDS)
    if "data" in payload and isinstance(payload["data"], dict):
        data["data_json"] = db.jdump(payload["data"])
    if "file_ids" in payload:
        data["file_ids"] = db.jdump([int(i) for i in payload["file_ids"] or [] if str(i).isdigit()])
    for k in ("valid_from", "valid_to"):
        if data.get(k):
            m = re.match(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", data[k])
            if m:
                data[k] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            elif data[k] in ("长期", "永久"):
                data[k] = ""
    return data


@router.post("/library")
def create_library_item(payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer"))):
    cid = int(payload.get("company_id") or 0)
    must(db.get("companies", cid), "主体")
    cat = payload.get("category")
    if cat not in LIB_CATEGORIES:
        raise HTTPException(400, "资料类别不正确")
    data = _lib_data(payload)
    if not data.get("name"):
        raise HTTPException(400, "请填写名称")
    data.update({"company_id": cid, "category": cat, "created_by": user["id"], "created_at": now_str(), "updated_at": now_str()})
    data.setdefault("data_json", "{}")
    data.setdefault("file_ids", "[]")
    iid = db.insert("library_items", data)
    db.audit(user, "新增资料", f"{LIB_CATEGORIES[cat]}：{data['name']}")
    return _lib_out(db.get("library_items", iid))


@router.put("/library/{iid}")
def update_library_item(iid: int, payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer"))):
    must(db.get("library_items", iid), "资料")
    data = _lib_data(payload)
    data["updated_at"] = now_str()
    db.update("library_items", iid, data)
    return _lib_out(db.get("library_items", iid))


@router.delete("/library/{iid}")
def delete_library_item(iid: int, user: dict = Depends(require("manager", "sales", "writer"))):
    r = must(db.get("library_items", iid), "资料")
    db.ex("DELETE FROM library_items WHERE id = ?", (iid,))
    db.audit(user, "删除资料", r["name"])
    return {"ok": True}


@router.post("/library/import_file")
def import_library_file(payload: dict = Body(...), user: dict = Depends(require("manager", "sales", "writer"))):
    """把方案/历史标书文件（PDF/Word）转成文本存入资料库，供 AI 写标检索。"""
    cid = int(payload.get("company_id") or 0)
    must(db.get("companies", cid), "主体")
    f = must(db.get("files", int(payload.get("file_id") or 0)), "文件")
    cat = payload.get("category") or "solution"
    if cat not in ("solution", "history_bid"):
        raise HTTPException(400, "只有方案素材和历史标书可以导入文件内容")
    try:
        res = docparse.extract(f["stored_path"], f["filename"])
    except docparse.UnsupportedFile as e:
        raise HTTPException(400, str(e)) from e
    text = re.sub(r"【第\d+页】\n?", "", res["text"])
    iid = db.insert(
        "library_items",
        {
            "company_id": cid,
            "category": cat,
            "name": payload.get("name") or re.sub(r"\.\w+$", "", f["filename"]),
            "tags": payload.get("tags") or "",
            "content": text,
            "data_json": db.jdump({"result": payload.get("result", "")}),
            "file_ids": db.jdump([f["id"]]),
            "created_by": user["id"],
            "created_at": now_str(),
            "updated_at": now_str(),
        },
    )
    out = _lib_out(db.get("library_items", iid))
    out["warning"] = res.get("warning", "")
    return out


# ---------------------------------------------------------------- 订阅

SUB_FIELDS = ["name", "keywords_include", "keywords_exclude", "regions", "categories", "notice_types", "budget_min", "budget_max", "purchaser_keywords", "enabled"]


def _sub_data(payload: dict) -> dict:
    data = pick(payload, SUB_FIELDS)
    for k in ("budget_min", "budget_max"):
        if k in data:
            data[k] = num_or_none(data[k])
    if "enabled" in data:
        data["enabled"] = 1 if data["enabled"] in (1, True, "1", "true") else 0
    return data


@router.get("/subscriptions")
def list_subs(company_id: int = 0, user: dict = Depends(current_user)):
    if company_id:
        return db.q("SELECT * FROM subscriptions WHERE company_id = ? ORDER BY id", (company_id,))
    return db.q("SELECT s.*, c.name AS company_name FROM subscriptions s JOIN companies c ON c.id = s.company_id ORDER BY s.company_id, s.id")


@router.post("/subscriptions")
def create_sub(payload: dict = Body(...), user: dict = Depends(require("manager", "sales"))):
    cid = int(payload.get("company_id") or 0)
    must(db.get("companies", cid), "主体")
    data = _sub_data(payload)
    if not data.get("name"):
        data["name"] = "订阅" + now().strftime("%m%d%H%M")
    data.setdefault("notice_types", "tender")
    data.update({"company_id": cid, "created_at": now_str(), "updated_at": now_str()})
    sid = db.insert("subscriptions", data)
    return db.get("subscriptions", sid)


@router.put("/subscriptions/{sid}")
def update_sub(sid: int, payload: dict = Body(...), user: dict = Depends(require("manager", "sales"))):
    must(db.get("subscriptions", sid), "订阅")
    data = _sub_data(payload)
    data["updated_at"] = now_str()
    db.update("subscriptions", sid, data)
    return db.get("subscriptions", sid)


@router.delete("/subscriptions/{sid}")
def delete_sub(sid: int, user: dict = Depends(require("manager", "sales"))):
    db.ex("DELETE FROM subscriptions WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/subscriptions/{sid}/preview")
def preview_sub(sid: int, user: dict = Depends(current_user)):
    """用这条订阅去扫最近 30 天已入库的标讯，预览能匹配到多少（不写入）。"""
    sub = must(db.get("subscriptions", sid), "订阅")
    since = (now() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = db.q("SELECT * FROM notices WHERE created_at >= ? AND dup_of IS NULL ORDER BY publish_time DESC LIMIT 5000", (since,))
    hits = []
    for n in rows:
        ok, score, reasons, kw = matching.subscription_check(n, sub)
        if ok:
            hits.append({"id": n["id"], "title": n["title"], "publish_time": n["publish_time"], "score": score, "reasons": reasons})
    hits.sort(key=lambda x: -x["score"])
    return {"total_scanned": len(rows), "count": len(hits), "items": hits[:30]}


@router.post("/subscriptions/rematch")
def rematch(payload: dict = Body(default={}), user: dict = Depends(require("manager", "sales"))):
    """订阅改了之后，对最近 N 天的标讯重新匹配。"""
    days = int(payload.get("days") or 7)
    since = (now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.q("SELECT id FROM notices WHERE created_at >= ? AND dup_of IS NULL", (since,))
    companies = db.q("SELECT * FROM companies")
    n = 0
    for r in rows:
        n += len(matching.match_notice(r["id"], companies=companies))
    return {"scanned": len(rows), "matched": n}


# ---------------------------------------------------------------- 平台账号 / CA 台账（不存密码）

ACC_FIELDS = ["platform_name", "url", "account_name", "registered", "ca_type", "ca_holder", "ca_expire", "note"]


@router.get("/platform_accounts")
def list_accounts(company_id: int = 0, user: dict = Depends(current_user)):
    sql = "SELECT p.*, c.name AS company_name FROM platform_accounts p JOIN companies c ON c.id = p.company_id"
    if company_id:
        return db.q(sql + " WHERE p.company_id = ? ORDER BY p.id", (company_id,))
    return db.q(sql + " ORDER BY p.company_id, p.id")


@router.post("/platform_accounts")
def create_account(payload: dict = Body(...), user: dict = Depends(require("manager", "sales"))):
    if any(k in payload for k in ("password", "pwd", "pin")):
        raise HTTPException(400, "系统不保存任何平台密码或 CA 口令，请勿填写")
    cid = int(payload.get("company_id") or 0)
    must(db.get("companies", cid), "主体")
    data = pick(payload, ACC_FIELDS)
    if not data.get("platform_name"):
        raise HTTPException(400, "请填写平台名称")
    data.update({"company_id": cid, "created_at": now_str(), "updated_at": now_str()})
    aid = db.insert("platform_accounts", data)
    return db.get("platform_accounts", aid)


@router.put("/platform_accounts/{aid}")
def update_account(aid: int, payload: dict = Body(...), user: dict = Depends(require("manager", "sales"))):
    must(db.get("platform_accounts", aid), "平台账号")
    data = pick(payload, ACC_FIELDS)
    data["updated_at"] = now_str()
    db.update("platform_accounts", aid, data)
    return db.get("platform_accounts", aid)


@router.delete("/platform_accounts/{aid}")
def delete_account(aid: int, user: dict = Depends(require("manager", "sales"))):
    db.ex("DELETE FROM platform_accounts WHERE id = ?", (aid,))
    return {"ok": True}
