"""系统设置、AI 模型、推送测试、数据看板、通知、后台任务、审计日志。"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, jobs, llm, notify, scheduler
from ..config import now, now_str
from ..security import current_user, require

router = APIRouter(prefix="/api", tags=["system"])


def _mask(v: str) -> str:
    if not v:
        return ""
    return v[:4] + "…" + v[-4:] if len(v) > 10 else "已设置"


@router.get("/settings")
def get_settings(user: dict = Depends(require("admin"))):
    s = db.all_settings()
    for k in db.SECRET_SETTING_KEYS:
        s[k] = _mask(s.get(k) or "")
    return {"settings": s, "presets": llm.PRESETS, "llm_enabled": llm.enabled(), "next_runs": scheduler.next_runs()}


@router.put("/settings")
def put_settings(payload: dict = Body(...), user: dict = Depends(require("admin"))):
    changed = []
    for k, v in payload.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        if k in db.SECRET_SETTING_KEYS:
            if v is None or (isinstance(v, str) and ("…" in v or v == "已设置")):
                continue  # 没改动的密钥保持原样
        default = db.DEFAULT_SETTINGS[k]
        if isinstance(default, bool):
            v = v in (True, 1, "1", "true", "on")
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 需要是整数")
        elif isinstance(default, float):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 需要是数字")
        db.set_setting(k, v)
        changed.append(k)
    scheduler.configure()
    db.audit(user, "修改系统设置", "、".join(changed)[:500])
    return get_settings(user)


@router.post("/settings/llm_test")
def llm_test(payload: dict = Body(default={}), user: dict = Depends(require("admin"))):
    cfg = llm.config()
    for k in ("provider", "base_url", "model"):
        if payload.get(k):
            cfg[k] = payload[k].rstrip("/") if k == "base_url" else payload[k]
    if payload.get("api_key") and "…" not in payload["api_key"]:
        cfg["api_key"] = payload["api_key"]
    try:
        return llm.test_connection(cfg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"连接失败：{e}") from e


@router.post("/settings/notify_test")
def notify_test(user: dict = Depends(require("admin"))):
    if not notify.has_channels():
        raise HTTPException(400, "还没有配置任何群机器人地址")
    return {"results": notify.push("AI智能投标系统 · 推送测试", "如果你在群里看到这条消息，说明推送配置成功。")}


@router.post("/desktop/quit")
def desktop_quit(user: dict = Depends(require("admin"))):
    """桌面模式：从网页里退出程序（停止后台服务）。"""
    import os
    import threading

    from ..config import DESKTOP

    if not DESKTOP:
        raise HTTPException(404, "只有本机版可以从网页退出程序")
    db.audit(user, "退出程序")
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True}


@router.post("/digest/run")
def run_digest(user: dict = Depends(require("admin", "manager"))):
    return {"markdown": notify.daily_digest(), "reminders": notify.daily_reminders()}


# ---------------------------------------------------------------- 看板

@router.get("/stats/dashboard")
def dashboard(company_id: int = 0, user: dict = Depends(current_user)):
    today = now().strftime("%Y-%m-%d")
    since7 = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
    cf = " AND m.company_id = ?" if company_id else ""
    cp = [company_id] if company_id else []
    pf = " AND company_id = ?" if company_id else ""
    data = {
        "notices_today": db.scalar("SELECT COUNT(*) FROM notices WHERE created_at >= ?", (today,)),
        "notices_7d": db.scalar("SELECT COUNT(*) FROM notices WHERE created_at >= ?", (since7,)),
        "matches_today": db.scalar(f"SELECT COUNT(*) FROM matches m WHERE m.created_at >= ?{cf}", [today] + cp),
        "matches_new": db.scalar(f"SELECT COUNT(*) FROM matches m JOIN notices n ON n.id = m.notice_id WHERE m.status = 'new' AND n.notice_type = 'tender' AND (n.deadline = '' OR n.deadline >= ?){cf}", [now_str()[:16]] + cp),
        "projects_active": db.scalar(f"SELECT COUNT(*) FROM projects WHERE status IN ('evaluating','preparing','reviewing','ready'){pf}", cp),
        "projects_submitted": db.scalar(f"SELECT COUNT(*) FROM projects WHERE status IN ('submitted','opened'){pf}", cp),
        "bond_outstanding": db.scalar(f"SELECT COALESCE(SUM(bond_amount),0) FROM projects WHERE bond_status = 'paid'{pf}", cp) or 0,
        "llm_enabled": llm.enabled(),
    }
    data["top_matches"] = db.q(
        "SELECT m.id AS match_id, m.score, m.level, m.company_id, c.short_name, c.name AS company_name, n.id, n.title, n.deadline, n.budget, n.region "
        "FROM matches m JOIN notices n ON n.id = m.notice_id JOIN companies c ON c.id = m.company_id "
        f"WHERE m.status IN ('new','fav') AND n.notice_type = 'tender' AND m.level != '不能投' AND (n.deadline = '' OR n.deadline >= ?){cf} "
        "ORDER BY m.created_at >= ? DESC, m.score DESC LIMIT 10",
        [now_str()[:16]] + cp + [since7],
    )
    data["upcoming"] = notify.upcoming_deadlines(days=10)[:15]
    if company_id:
        data["upcoming"] = [u for u in data["upcoming"] if db.scalar("SELECT company_id FROM projects WHERE id = ?", (u["project_id"],)) == company_id]
    data["expiring"] = db.q(
        "SELECT l.id, l.name, l.valid_to, c.name AS company_name FROM library_items l JOIN companies c ON c.id = l.company_id "
        "WHERE l.valid_to != '' AND l.valid_to <= ?" + (" AND l.company_id = ?" if company_id else "") + " ORDER BY l.valid_to LIMIT 10",
        [(now() + timedelta(days=60)).strftime("%Y-%m-%d")] + cp,
    )
    data["sources"] = db.q("SELECT id, name, enabled, last_run_at, last_status, last_message, paused_until FROM sources ORDER BY id")
    data["setup"] = {
        "companies": db.scalar("SELECT COUNT(*) FROM companies"),
        "subscriptions": db.scalar("SELECT COUNT(*) FROM subscriptions WHERE enabled = 1"),
        "library": db.scalar("SELECT COUNT(*) FROM library_items"),
        "notify": notify.has_channels(),
    }
    return data


@router.get("/stats/overview")
def overview(months: int = 6, company_id: int = 0, user: dict = Depends(current_user)):
    since = (now() - timedelta(days=30 * months)).strftime("%Y-%m-01")
    pf = " AND company_id = ?" if company_id else ""
    cp = [company_id] if company_id else []
    by_status = db.q(f"SELECT status, COUNT(*) AS n FROM projects WHERE created_at >= ?{pf} GROUP BY status", [since] + cp)
    monthly = db.q(
        f"SELECT substr(created_at,1,7) AS month, COUNT(*) AS projects, "
        f"SUM(CASE WHEN status IN ('submitted','opened','won','lost') THEN 1 ELSE 0 END) AS submitted, "
        f"SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS won, "
        f"SUM(CASE WHEN status = 'won' THEN COALESCE(result_amount, bid_amount, 0) ELSE 0 END) AS won_amount "
        f"FROM projects WHERE created_at >= ?{pf} GROUP BY month ORDER BY month",
        [since] + cp,
    )
    found = db.q("SELECT substr(created_at,1,7) AS month, COUNT(*) AS n FROM notices WHERE created_at >= ? GROUP BY month", (since,))
    fm = {r["month"]: r["n"] for r in found}
    matched = db.q("SELECT substr(created_at,1,7) AS month, COUNT(*) AS n FROM matches WHERE created_at >= ?" + (" AND company_id = ?" if company_id else "") + " GROUP BY month", [since] + cp)
    mm = {r["month"]: r["n"] for r in matched}
    months_set = sorted(set(fm) | set(mm) | {r["month"] for r in monthly})
    mrow = {r["month"]: r for r in monthly}
    series = [
        {
            "month": m,
            "notices": fm.get(m, 0),
            "matches": mm.get(m, 0),
            "projects": (mrow.get(m) or {}).get("projects", 0),
            "submitted": (mrow.get(m) or {}).get("submitted", 0),
            "won": (mrow.get(m) or {}).get("won", 0),
            "won_amount": (mrow.get(m) or {}).get("won_amount", 0),
        }
        for m in months_set
    ]
    decided = db.q1(f"SELECT SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) AS won, SUM(CASE WHEN status IN ('won','lost') THEN 1 ELSE 0 END) AS decided FROM projects WHERE created_at >= ?{pf}", [since] + cp)
    by_region = db.q(
        f"SELECT COALESCE(NULLIF(region,''),'未知') AS region, COUNT(*) AS n, SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) AS won FROM projects WHERE created_at >= ?{pf} GROUP BY 1 ORDER BY n DESC LIMIT 10",
        [since] + cp,
    )
    competitors = db.q(
        f"SELECT winner, COUNT(*) AS n FROM projects WHERE winner != '' AND status = 'lost' AND created_at >= ?{pf} GROUP BY winner ORDER BY n DESC LIMIT 10",
        [since] + cp,
    )
    ai = db.q1("SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS pt, COALESCE(SUM(completion_tokens),0) AS ct FROM ai_usage WHERE created_at >= ?", ((now() - timedelta(days=30)).strftime("%Y-%m-%d"),))
    return {
        "by_status": by_status,
        "series": series,
        "win_rate": (decided["won"] or 0) / decided["decided"] if decided and decided["decided"] else None,
        "won": decided["won"] if decided else 0,
        "decided": decided["decided"] if decided else 0,
        "by_region": by_region,
        "competitors": competitors,
        "ai_usage_30d": ai,
    }


# ---------------------------------------------------------------- 通知 / 任务 / 审计

@router.get("/notifications")
def notifications(unread: int = 0, user: dict = Depends(current_user)):
    sql = "SELECT * FROM notifications WHERE (user_id IS NULL OR user_id = ?)"
    if unread:
        sql += " AND read = 0"
    sql += " ORDER BY id DESC LIMIT 60"
    rows = db.q(sql, (user["id"],))
    count = db.scalar("SELECT COUNT(*) FROM notifications WHERE (user_id IS NULL OR user_id = ?) AND read = 0", (user["id"],))
    return {"items": rows, "unread": count}


@router.post("/notifications/read")
def mark_read(payload: dict = Body(default={}), user: dict = Depends(current_user)):
    ids = payload.get("ids")
    if ids:
        ids = [int(i) for i in ids if str(i).isdigit()]
        db.ex(f"UPDATE notifications SET read = 1 WHERE id IN ({','.join('?' for _ in ids)})", ids)
    else:
        db.ex("UPDATE notifications SET read = 1 WHERE user_id IS NULL OR user_id = ?", (user["id"],))
    return {"ok": True}


@router.get("/jobs/{jid}")
def job(jid: int, user: dict = Depends(current_user)):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


@router.get("/audit")
def audit_logs(limit: int = 200, user: dict = Depends(require("admin", "manager"))):
    return db.q("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
