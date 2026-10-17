"""站内通知 + 企业微信/钉钉/飞书群机器人推送 + 每日早报 + 节点提醒。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from datetime import timedelta

import httpx

from . import db
from .config import now, now_str
from .textutil import fmt_amount, to_dt


def add(title: str, body: str = "", *, kind: str = "info", link: str = "", user_id: int | None = None, dedup_key: str | None = None) -> None:
    db.insert(
        "notifications",
        {
            "user_id": user_id,
            "kind": kind,
            "title": title[:200],
            "body": body[:2000],
            "link": link,
            "dedup_key": dedup_key,
            "read": 0,
            "created_at": now_str(),
        },
        or_ignore=True,
    )


# ---------------------------------------------------------------- 群机器人

def _dingtalk_url(url: str, secret: str) -> str:
    if not secret:
        return url
    ts = str(round(time.time() * 1000))
    sign = base64.b64encode(hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()).decode()
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def _feishu_sign(secret: str) -> dict:
    ts = str(int(time.time()))
    sign = base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()).decode()
    return {"timestamp": ts, "sign": sign}


def push(title: str, markdown: str) -> list[str]:
    """推送到已配置的群机器人，返回各渠道结果。"""
    s = db.all_settings()
    results = []
    with httpx.Client(timeout=15) as c:
        if s.get("notify_wecom_webhook"):
            try:
                r = c.post(s["notify_wecom_webhook"], json={"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{markdown}"[:4000]}})
                results.append("企业微信：" + _result(r))
            except httpx.HTTPError as e:
                results.append(f"企业微信：失败 {e}")
        if s.get("notify_dingtalk_webhook"):
            try:
                url = _dingtalk_url(s["notify_dingtalk_webhook"], s.get("notify_dingtalk_secret") or "")
                r = c.post(url, json={"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n{markdown}"[:5000]}})
                results.append("钉钉：" + _result(r))
            except httpx.HTTPError as e:
                results.append(f"钉钉：失败 {e}")
        if s.get("notify_feishu_webhook"):
            try:
                body = {"msg_type": "text", "content": {"text": f"{title}\n{markdown}"[:4000]}}
                if s.get("notify_feishu_secret"):
                    body.update(_feishu_sign(s["notify_feishu_secret"]))
                r = c.post(s["notify_feishu_webhook"], json=body)
                results.append("飞书：" + _result(r))
            except httpx.HTTPError as e:
                results.append(f"飞书：失败 {e}")
    return results


def _result(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    code = data.get("errcode", data.get("code", data.get("StatusCode", 0)))
    if r.status_code == 200 and code in (0, None):
        return "成功"
    return f"失败 {data.get('errmsg') or data.get('msg') or data}"


def has_channels() -> bool:
    s = db.all_settings()
    return any(s.get(k) for k in ("notify_wecom_webhook", "notify_dingtalk_webhook", "notify_feishu_webhook"))


# ---------------------------------------------------------------- 新标讯推送

def push_new_matches(min_score: float = 60) -> int:
    rows = db.q(
        "SELECT m.id, m.score, m.level, c.short_name, c.name AS cname, n.title, n.deadline, n.budget, n.region "
        "FROM matches m JOIN notices n ON n.id = m.notice_id JOIN companies c ON c.id = m.company_id "
        "WHERE m.notified = 0 AND m.status = 'new' AND m.score >= ? AND n.notice_type = 'tender' "
        "ORDER BY m.score DESC LIMIT 30",
        (min_score,),
    )
    if not rows:
        return 0
    lines = []
    for r in rows[:15]:
        who = r["short_name"] or r["cname"]
        dl = (r["deadline"] or "未知")[:16]
        lines.append(f"- 【{r['level']}·{int(r['score'])}分】{r['title'][:40]}｜{who}｜{r['region'] or ''}｜预算{fmt_amount(r['budget']) or '未知'}｜截止{dl}")
    if len(rows) > 15:
        lines.append(f"……另有 {len(rows) - 15} 条，请登录系统查看")
    md = "\n".join(lines)
    add(f"新增 {len(rows)} 条匹配标讯", md, kind="match", link="#/notices", dedup_key=f"newmatch:{now_str()[:16]}")
    if has_channels():
        push(f"新增 {len(rows)} 条匹配标讯", md)
    db.ex(f"UPDATE matches SET notified = 1 WHERE id IN ({','.join(str(r['id']) for r in rows)})")
    return len(rows)


# ---------------------------------------------------------------- 每日早报 & 节点提醒

def daily_digest() -> str:
    s = db.all_settings()
    top_n = int(s.get("digest_top_n") or 15)
    since = (now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    new_notices = db.scalar("SELECT COUNT(*) FROM notices WHERE created_at >= ?", (since,))
    rows = db.q(
        "SELECT m.score, m.level, c.short_name, c.name AS cname, n.title, n.deadline, n.budget "
        "FROM matches m JOIN notices n ON n.id = m.notice_id JOIN companies c ON c.id = m.company_id "
        "WHERE m.created_at >= ? AND m.status IN ('new','fav') AND n.notice_type = 'tender' AND m.level != '不能投' "
        "ORDER BY m.score DESC LIMIT ?",
        (since, top_n),
    )
    lines = [f"过去 24 小时新采集公告 {new_notices} 条，匹配到 {len(rows)} 条值得关注："]
    for r in rows:
        lines.append(f"- 【{r['level']}】{r['title'][:42]}（{r['short_name'] or r['cname']}，截止 {(r['deadline'] or '未知')[:10]}）")
    upcoming = upcoming_deadlines(days=3)
    if upcoming:
        lines.append("")
        lines.append("近 3 天的投标节点：")
        for u in upcoming[:10]:
            lines.append(f"- {u['when'][:16]} {u['what']}：{u['project']}")
    md = "\n".join(lines)
    add("每日标讯早报", md, kind="digest", link="#/dashboard", dedup_key="digest:" + now_str()[:10])
    if has_channels():
        push("每日标讯早报 " + now_str()[:10], md)
    return md


def upcoming_deadlines(days: int = 7) -> list[dict]:
    end = (now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    start = now().strftime("%Y-%m-%d %H:%M")
    out = []
    fields = [
        ("signup_deadline", "报名/获取文件截止"),
        ("clarify_deadline", "答疑/澄清截止"),
        ("bond_deadline", "保证金缴纳截止"),
        ("deadline", "投标截止"),
        ("open_time", "开标"),
    ]
    projects = db.q(
        "SELECT id, name, company_id, status, signup_deadline, clarify_deadline, bond_deadline, deadline, open_time "
        "FROM projects WHERE status IN ('evaluating','preparing','reviewing','submitted')"
    )
    for p in projects:
        for f, label in fields:
            v = p.get(f) or ""
            if v and start <= v[:16] <= end:
                if f in ("signup_deadline", "clarify_deadline", "bond_deadline", "deadline") and p["status"] == "submitted" and f != "deadline":
                    continue
                out.append({"project_id": p["id"], "project": p["name"], "what": label, "when": v})
    out.sort(key=lambda x: x["when"])
    return out


def daily_reminders() -> int:
    s = db.all_settings()
    remind_days = [int(x) for x in str(s.get("remind_days") or "7,3,1").split(",") if x.strip().isdigit()]
    warn_days = int(s.get("expire_warn_days") or 30)
    count = 0
    today = now()
    # 项目节点
    for u in upcoming_deadlines(days=max(remind_days or [7])):
        dt = to_dt(u["when"])
        if not dt:
            continue
        left = (dt.date() - today.date()).days
        if left in remind_days or left == 0:
            key = f"remind:{u['project_id']}:{u['what']}:{left}"
            add(f"{u['what']}还有 {left} 天：{u['project']}", f"时间：{u['when']}", kind="deadline", link=f"#/projects/{u['project_id']}", dedup_key=key)
            count += 1
    # 资质证书到期
    limit = (today + timedelta(days=warn_days)).strftime("%Y-%m-%d")
    rows = db.q(
        "SELECT l.id, l.name, l.valid_to, l.category, c.name AS cname FROM library_items l JOIN companies c ON c.id = l.company_id "
        "WHERE l.valid_to != '' AND l.valid_to <= ? ORDER BY l.valid_to",
        (limit,),
    )
    for r in rows:
        state = "已过期" if r["valid_to"] < today.strftime("%Y-%m-%d") else "即将到期"
        key = f"expire:{r['id']}:{r['valid_to']}:{state}"
        add(f"{state}：{r['cname']}「{r['name']}」", f"有效期至 {r['valid_to']}，请及时续期，避免投标时资质失效", kind="expire", link="#/library", dedup_key=key)
        count += 1
    # CA 证书到期
    rows = db.q(
        "SELECT p.id, p.platform_name, p.ca_expire, p.ca_holder, c.name AS cname FROM platform_accounts p JOIN companies c ON c.id = p.company_id "
        "WHERE p.ca_expire != '' AND p.ca_expire <= ?",
        (limit,),
    )
    for r in rows:
        key = f"ca:{r['id']}:{r['ca_expire']}"
        add(f"CA 证书即将到期：{r['platform_name']}", f"{r['cname']}，持有人 {r['ca_holder'] or '未填'}，到期 {r['ca_expire']}", kind="expire", link="#/accounts", dedup_key=key)
        count += 1
    # 保证金待退还
    rows = db.q(
        "SELECT id, name, bond_amount, open_time FROM projects WHERE bond_status = 'paid' AND result IN ('won','lost') "
        "AND open_time != '' AND open_time <= ?",
        ((today - timedelta(days=15)).strftime("%Y-%m-%d"),),
    )
    for r in rows:
        key = f"bond:{r['id']}:{today.strftime('%Y-%W')}"
        add(f"保证金待退还：{r['name']}", f"金额 {fmt_amount(r['bond_amount'])}，开标已超过 15 天，请跟进退还", kind="bond", link=f"#/projects/{r['id']}", dedup_key=key)
        count += 1
    if count and has_channels():
        rows = db.q("SELECT title FROM notifications WHERE created_at >= ? AND kind IN ('deadline','expire','bond')", (today.strftime("%Y-%m-%d"),))
        if rows:
            push("今日提醒", "\n".join("- " + r["title"] for r in rows[:20]))
    return count
