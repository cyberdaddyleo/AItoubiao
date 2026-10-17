"""采集调度：跑来源 → 入库去重 → 按需抓详情 → 匹配 → 结果关联 → 推送。"""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from .. import db, llm, matching, notify
from ..config import FILES_DIR, SNAPSHOT_DIR, now, now_str
from ..extract import classify_notice, extract_fields, extract_winners
from ..textutil import hamming, simhash64, split_keywords, tokenize
from . import ccgp, generic, ggzy
from .base import Fetcher, RobotsDisallowed, StopCrawl

_run_lock = threading.Lock()

SOURCE_TYPES = {
    "ccgp_list": "中国政府采购网·栏目列表",
    "ccgp_search": "中国政府采购网·关键词搜索（低频）",
    "ggzy": "全国公共资源交易平台（实验性）",
    "custom_list": "自定义列表页（省市交易中心/企业采购平台）",
}


def ensure_default_sources() -> None:
    if db.scalar("SELECT COUNT(*) FROM sources") > 0:
        return
    t = now_str()
    db.insert(
        "sources",
        {
            "name": "中国政府采购网",
            "type": "ccgp_list",
            "config_json": db.jdump({"channels": ccgp.DEFAULT_CHANNELS, "max_pages": 25, "lookback_days": 3, "interval": 4}),
            "enabled": 1,
            "description": "按栏目增量采集中央/地方公告（招标、磋商、更正、中标、成交）。2026-10 实测可用。",
            "created_at": t,
        },
    )
    db.insert(
        "sources",
        {
            "name": "中国政府采购网·关键词搜索",
            "type": "ccgp_search",
            "config_json": db.jdump({"days": 2, "pages": 1, "interval": 40, "max_keywords": 6, "bid_type": "1"}),
            "enabled": 0,
            "description": "用订阅关键词在全站搜索，补充栏目采集遗漏。该站搜索限频很严，默认关闭；开启后每个关键词间隔 40 秒。",
            "created_at": t,
        },
    )
    db.insert(
        "sources",
        {
            "name": "全国公共资源交易平台",
            "type": "ggzy",
            "config_json": db.jdump({"classify": ["02", "01"], "max_pages": 2, "interval": 30}),
            "enabled": 0,
            "description": "实验性：接口已核实，但该站限频严格且可能要求验证码；遇到验证码会自动暂停，不会绕过。",
            "created_at": t,
        },
    )


# ---------------------------------------------------------------- 入库

def _known_urls(days: int = 30) -> set[str]:
    since = (now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return {r["url"] for r in db.q("SELECT url FROM notices WHERE created_at >= ?", (since,))}


def find_duplicate(title: str, purchaser: str, exclude_id: int | None = None) -> int | None:
    """同一项目在不同网站/栏目转载：标题 SimHash 接近且采购人一致（或采购人未知）。"""
    h = simhash64(title)
    if not h:
        return None
    since = (now() - timedelta(days=20)).strftime("%Y-%m-%d")
    rows = db.q(
        "SELECT id, title, simhash, purchaser, notice_type FROM notices WHERE created_at >= ? AND dup_of IS NULL AND simhash != '' "
        + ("AND purchaser = ? " if purchaser else "")
        + "ORDER BY id DESC LIMIT 3000",
        (since, purchaser) if purchaser else (since,),
    )
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        if r["title"] == title or hamming(int(r["simhash"]), h) <= 3:
            return r["id"]
    return None


def store_item(item: dict, source: dict | None) -> int | None:
    """新公告入库，已存在返回 None。"""
    if db.q1("SELECT id FROM notices WHERE url = ?", (item["url"],)):
        return None
    t = now_str()
    title = item.get("title") or "(无标题)"
    purchaser = item.get("purchaser") or ""
    dup = find_duplicate(title, purchaser)
    nid = db.insert(
        "notices",
        {
            "source_id": source["id"] if source else None,
            "source_name": (source["name"] if source else item.get("source_name", "人工导入")) + (("·" + item["platform"]) if item.get("platform") else ""),
            "url": item["url"],
            "title": title,
            "notice_type": item.get("notice_type") or classify_notice(title, item.get("type_label", "")),
            "type_label": item.get("type_label", ""),
            "publish_time": item.get("publish_time", ""),
            "region": item.get("region", ""),
            "purchaser": purchaser,
            "agency": item.get("agency", ""),
            "category": item.get("category", ""),
            "content_text": item.get("snippet", ""),
            "simhash": str(simhash64(title)),
            "dup_of": dup,
            "created_at": t,
            "updated_at": t,
        },
        or_ignore=True,
    )
    return nid or None


def apply_detail(notice_id: int, detail: dict, raw_html: str = "") -> None:
    n = db.get("notices", notice_id)
    f = detail.get("fields") or {}
    upd = {
        "content_text": detail.get("content_text") or n["content_text"],
        "attachments_json": db.jdump(detail.get("attachments") or []),
        "winners_json": db.jdump(detail.get("winners") or []),
        "detail_fetched": 1,
        "updated_at": now_str(),
    }
    if detail.get("title") and (not n["title"] or n["title"].endswith("...")):
        upd["title"] = detail["title"]
    if detail.get("publish_time") and not n["publish_time"]:
        upd["publish_time"] = detail["publish_time"]
    for k in ("project_no", "project_name", "budget", "deadline", "open_time", "doc_get_end", "bond_text",
              "joint_venture", "sme_only", "qualification_text", "award_amount", "category", "purchaser", "agency", "region"):
        if f.get(k) not in (None, "") and (k not in ("purchaser", "region", "category") or not n.get(k)):
            upd[k] = f[k]
    if raw_html:
        sha = hashlib.sha256(raw_html.encode("utf-8", "ignore")).hexdigest()
        upd["snapshot_sha256"] = sha
        try:
            (SNAPSHOT_DIR / f"{notice_id}.html").write_text(raw_html, encoding="utf-8")
        except OSError:
            pass
    db.update("notices", notice_id, upd)


def fetch_detail(notice_id: int, fetcher: Fetcher | None = None) -> dict:
    n = db.get("notices", notice_id)
    if not n or n["url"].startswith("manual://"):
        return n
    own = fetcher is None
    fetcher = fetcher or Fetcher(interval=4)
    try:
        html = fetcher.get(n["url"])
        host = urlparse(n["url"]).netloc
        detail = ccgp.parse_detail(html, n["url"]) if "ccgp.gov.cn" in host else generic.parse_detail(html, n["url"])
        apply_detail(notice_id, detail, html)
    finally:
        if own:
            fetcher.close()
    return db.get("notices", notice_id)


def _related_to_project(item: dict) -> bool:
    """更正/结果公告：如果和在投项目同一个采购人，抓详情用于自动关联。"""
    if item.get("notice_type") not in ("change", "award", "fail"):
        return False
    pur = item.get("purchaser") or ""
    rows = db.q("SELECT name, purchaser, project_no FROM projects WHERE status NOT IN ('abandoned')")
    title = item.get("title") or ""
    for p in rows:
        if p["project_no"] and p["project_no"] in title:
            return True
        if pur and p["purchaser"] and (pur in p["purchaser"] or p["purchaser"] in pur):
            ta, tb = set(tokenize(title)), set(tokenize(p["name"]))
            if ta and tb and len(ta & tb) / len(tb) >= 0.5:
                return True
    return False


def link_results(notice_id: int) -> list[int]:
    """把中标/更正公告关联到投标项目，并给负责人发通知。"""
    n = db.get("notices", notice_id)
    if not n or n["notice_type"] not in ("award", "fail", "change"):
        return []
    linked = []
    projects = db.q("SELECT * FROM projects WHERE status NOT IN ('abandoned')")
    nt = set(tokenize(n["title"]))
    for p in projects:
        hit = False
        if p["project_no"] and (p["project_no"] == n.get("project_no") or p["project_no"] in n["title"] or p["project_no"] in (n.get("content_text") or "")[:3000]):
            hit = True
        elif p["purchaser"] and n["purchaser"] and (p["purchaser"] in n["purchaser"] or n["purchaser"] in p["purchaser"]):
            pt = set(tokenize(p["name"]))
            if pt and len(nt & pt) / len(pt) >= 0.6:
                hit = True
        if not hit:
            continue
        linked.append(p["id"])
        comp = db.get("companies", p["company_id"]) or {}
        if n["notice_type"] == "award":
            winners = db.jload(n["winners_json"], []) or []
            names = "、".join(w.get("name", "") for w in winners) or "（未识别出中标人，请打开公告查看）"
            we_won = any(comp.get("name") and comp["name"] in w.get("name", "") for w in winners)
            if not p["result"]:
                upd = {"result_notice_id": notice_id, "winner": names[:200], "updated_at": now_str()}
                if winners and winners[0].get("amount"):
                    upd["result_amount"] = winners[0]["amount"]
                elif n.get("award_amount"):
                    upd["result_amount"] = n["award_amount"]
                db.update("projects", p["id"], upd)
            notify.add(
                ("🎉 疑似中标：" if we_won else "中标结果已发布：") + p["name"],
                f"中标人：{names}。请到项目里确认结果。",
                kind="result",
                link=f"#/projects/{p['id']}",
                user_id=p.get("owner_id"),
                dedup_key=f"result:{p['id']}:{notice_id}",
            )
        else:
            notify.add(
                ("更正公告：" if n["notice_type"] == "change" else "废标/终止公告：") + p["name"],
                n["title"],
                kind="change",
                link=f"#/projects/{p['id']}",
                user_id=p.get("owner_id"),
                dedup_key=f"change:{p['id']}:{notice_id}",
            )
        db.insert("project_events", {"project_id": p["id"], "action": "关联公告", "comment": n["title"][:200], "created_at": now_str()})
    return linked


# ---------------------------------------------------------------- 跑一个来源

def run_source(source_id: int, *, trigger: str = "manual", log_cb=None) -> dict:
    src = db.get("sources", source_id)
    if not src:
        raise ValueError("来源不存在")
    if trigger == "schedule":
        if not src["enabled"]:
            return {"skipped": "未启用"}
        if src.get("paused_until") and src["paused_until"] > now_str():
            return {"skipped": f"暂停至 {src['paused_until']}"}
    cfg = db.jload(src["config_json"], {}) or {}
    run_id = db.insert("crawl_runs", {"source_id": source_id, "started_at": now_str(), "status": "running"})
    logs: list[str] = []

    def log(msg: str) -> None:
        logs.append(msg)
        if log_cb:
            log_cb(msg)

    stats = {"list": 0, "new": 0, "detail": 0, "matched": 0, "status": "ok"}
    settings = db.all_settings()
    policy = settings.get("crawl_detail_policy") or "matched"
    max_details = int(cfg.get("max_details") or 300)
    subs = db.q("SELECT * FROM subscriptions WHERE enabled = 1")
    known = _known_urls()
    fetcher = Fetcher(interval=float(cfg.get("interval") or 4))
    try:
        if src["type"] == "ccgp_list":
            it = ccgp.collect_list(cfg, fetcher, lambda u: u in known, log)
        elif src["type"] == "ccgp_search":
            kws: list[str] = []
            for s in subs:
                for k in split_keywords(s["keywords_include"]):
                    if k not in kws:
                        kws.append(k)
            if not kws:
                log("没有设置订阅关键词，跳过搜索")
                it = iter(())
            else:
                it = ccgp.collect_search(cfg, fetcher, kws, log)
        elif src["type"] == "ggzy":
            it = ggzy.collect(cfg, fetcher, lambda u: u in known, log)
        elif src["type"] == "custom_list":
            it = _collect_custom(cfg, fetcher, log)
        else:
            raise ValueError("未知来源类型：" + src["type"])

        new_ids: list[tuple[int, dict]] = []
        for item in it:
            stats["list"] += 1
            if item["url"] in known:
                continue
            nid = store_item(item, src)
            known.add(item["url"])
            if not nid:
                continue
            stats["new"] += 1
            new_ids.append((nid, item))

        # 抓详情（按策略）
        for nid, item in new_ids:
            if stats["detail"] >= max_details:
                log(f"达到单次详情上限 {max_details}，其余留待下次")
                break
            want = policy == "all" or (policy == "matched" and (matching.prefilter_title(item, subs) or _related_to_project(item)))
            if not want:
                continue
            try:
                fetch_detail(nid, fetcher)
                stats["detail"] += 1
            except (StopCrawl, RobotsDisallowed):
                raise
            except Exception as e:  # noqa: BLE001
                log(f"详情抓取失败：{item['title'][:30]} {type(e).__name__} {e}")

        # 匹配 & 结果关联
        companies = db.q("SELECT * FROM companies")
        for nid, item in new_ids:
            res = matching.match_notice(nid, companies=companies)
            stats["matched"] += len(res)
            link_results(nid)
    except StopCrawl as e:
        stats["status"] = "blocked"
        log(str(e))
        pause_hours = int(cfg.get("pause_hours") or 6)
        until = (now() + timedelta(hours=pause_hours)).strftime("%Y-%m-%d %H:%M:%S")
        db.update("sources", source_id, {"paused_until": until})
        notify.add(f"采集来源「{src['name']}」被限频/要求验证，已暂停到 {until[:16]}", str(e), kind="warning", link="#/sources", dedup_key=f"blocked:{source_id}:{until[:13]}")
    except RobotsDisallowed as e:
        stats["status"] = "robots"
        log(str(e))
    except Exception as e:  # noqa: BLE001
        stats["status"] = "error"
        log(f"采集出错：{type(e).__name__} {e}")
    finally:
        fetcher.close()
    msg = f"列表 {stats['list']} 条，新增 {stats['new']} 条，抓详情 {stats['detail']} 条，匹配 {stats['matched']} 条"
    db.update(
        "crawl_runs",
        run_id,
        {
            "finished_at": now_str(),
            "status": stats["status"],
            "list_count": stats["list"],
            "new_count": stats["new"],
            "detail_count": stats["detail"],
            "request_count": fetcher.request_count,
            "message": (msg + "\n" + "\n".join(logs[-40:]))[:6000],
        },
    )
    db.update("sources", source_id, {"last_run_at": now_str(), "last_status": stats["status"], "last_message": msg if stats["status"] == "ok" else (logs[-1] if logs else msg)})
    stats["message"] = msg
    stats["logs"] = logs[-40:]
    return stats


def _collect_custom(cfg: dict, fetcher: Fetcher, log):
    url_tpl = cfg.get("list_url") or ""
    if not url_tpl:
        raise ValueError("自定义来源缺少列表页地址")
    pages = int(cfg.get("pages") or 1)
    for p in range(1, pages + 1):
        url = url_tpl.replace("{page}", str(p)) if "{page}" in url_tpl else url_tpl
        html = fetcher.get(url, interval=float(cfg.get("interval") or 5))
        items = generic.parse_list(html, url, cfg)
        log(f"[{url}] 解析到 {len(items)} 条")
        for it in items:
            yield it
        if "{page}" not in url_tpl:
            break


def test_custom_source(cfg: dict) -> dict:
    with Fetcher(interval=3) as f:
        url = (cfg.get("list_url") or "").replace("{page}", "1")
        html = f.get(url)
        items = generic.parse_list(html, url, cfg)
        detail = None
        if items:
            dhtml = f.get(items[0]["url"])
            d = generic.parse_detail(dhtml, items[0]["url"])
            detail = {
                "title": d["title"],
                "publish_time": d["publish_time"],
                "fields": d["fields"],
                "text_preview": d["content_text"][:800],
                "attachments": d["attachments"][:5],
            }
    return {"count": len(items), "items": items[:15], "first_detail": detail}


# ---------------------------------------------------------------- 全量运行

def run_all(trigger: str = "schedule") -> list[dict]:
    if not _run_lock.acquire(blocking=False):
        return [{"skipped": "上一轮采集还在进行"}]
    try:
        out = []
        for s in db.q("SELECT id, name FROM sources WHERE enabled = 1 ORDER BY id"):
            r = run_source(s["id"], trigger=trigger)
            r["source"] = s["name"]
            out.append(r)
        auto_ai_check()
        notify.push_new_matches()
        return out
    finally:
        _run_lock.release()


def is_running() -> bool:
    return _run_lock.locked()


def auto_ai_check() -> int:
    if not llm.enabled():
        return 0
    n = int(db.get_setting("ai_auto_check_top_n") or 0)
    if n <= 0:
        return 0
    rows = db.q(
        "SELECT m.id FROM matches m JOIN notices n ON n.id = m.notice_id "
        "WHERE m.level_by = 'rule' AND m.status IN ('new','fav') AND n.notice_type = 'tender' AND n.detail_fetched = 1 "
        "AND m.created_at >= ? ORDER BY m.score DESC LIMIT ?",
        ((now() - timedelta(days=1)).strftime("%Y-%m-%d"), n),
    )
    done = 0
    for r in rows:
        try:
            matching.ai_recheck_match(r["id"])
            done += 1
        except Exception:  # noqa: BLE001
            break
    return done


# ---------------------------------------------------------------- 手动导入

def import_url(url: str) -> int:
    if not url.startswith(("http://", "https://")):
        raise ValueError("请输入以 http:// 或 https:// 开头的公告链接")
    existing = db.q1("SELECT id FROM notices WHERE url = ?", (url,))
    if existing:
        return existing["id"]
    with Fetcher(interval=2) as f:
        html = f.get(url)
    host = urlparse(url).netloc
    detail = ccgp.parse_detail(html, url) if "ccgp.gov.cn" in host else generic.parse_detail(html, url)
    title = detail["title"] or url
    nid = store_item(
        {
            "url": url,
            "title": title,
            "publish_time": detail.get("publish_time", ""),
            "region": detail["fields"].get("region", ""),
            "purchaser": detail["fields"].get("purchaser", ""),
            "notice_type": classify_notice(title),
            "source_name": "人工导入",
        },
        None,
    )
    if nid:
        apply_detail(nid, detail, html)
        matching.match_notice(nid)
        link_results(nid)
        return nid
    return db.q1("SELECT id FROM notices WHERE url = ?", (url,))["id"]


def import_text(title: str, text: str, url: str = "") -> int:
    title = (title or "").strip() or text.strip().split("\n")[0][:80]
    url = url.strip() or "manual://" + uuid.uuid4().hex
    nid = store_item({"url": url, "title": title, "notice_type": classify_notice(title), "source_name": "人工导入"}, None)
    if not nid:
        nid = db.q1("SELECT id FROM notices WHERE url = ?", (url,))["id"]
    f = extract_fields(text)
    apply_detail(nid, {"content_text": text, "fields": f, "attachments": [], "winners": extract_winners(text)})
    matching.match_notice(nid)
    link_results(nid)
    return nid


# ---------------------------------------------------------------- 附件下载到项目

def download_attachments(notice_id: int, user_id: int | None = None) -> list[dict]:
    n = db.get("notices", notice_id)
    atts = db.jload(n["attachments_json"], []) or []
    out = []
    if not atts:
        return out
    with Fetcher(interval=4) as f:
        for a in atts[:8]:
            name = a.get("name") or "附件"
            safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')[:80] or "附件"
            dest = FILES_DIR / now().strftime("%Y%m") / (uuid.uuid4().hex[:8] + "_" + safe)
            try:
                info = f.download(a["url"], dest)
            except Exception as e:  # noqa: BLE001
                out.append({"name": name, "error": f"{type(e).__name__}: {e}"})
                continue
            fid = db.insert(
                "files",
                {
                    "filename": safe,
                    "stored_path": str(dest),
                    "mime": info["content_type"],
                    "size": info["size"],
                    "sha256": info["sha256"],
                    "kind": "notice_attachment",
                    "uploaded_by": user_id,
                    "created_at": now_str(),
                },
            )
            out.append({"name": safe, "file_id": fid, "size": info["size"]})
    return out
