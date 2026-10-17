"""定时任务：按整点采集、每日早报、节点提醒、过期数据清理。"""
from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, notify
from .config import SNAPSHOT_DIR, TZ, now
from .crawler import runner

log = logging.getLogger("bid.scheduler")
_sched: BackgroundScheduler | None = None


def _safe(fn, name):
    def wrapped():
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("定时任务 %s 失败", name)

    return wrapped


def _crawl():
    if not db.get_setting("crawl_enabled", True):
        return
    runner.run_all("schedule")


def _cleanup():
    keep = int(db.get_setting("crawl_keep_days") or 180)
    before = (now() - timedelta(days=keep)).strftime("%Y-%m-%d")
    rows = db.q(
        "SELECT id FROM notices WHERE created_at < ? AND id NOT IN (SELECT notice_id FROM projects WHERE notice_id IS NOT NULL) "
        "AND id NOT IN (SELECT notice_id FROM matches WHERE status IN ('fav','project'))",
        (before,),
    )
    for r in rows:
        db.ex("DELETE FROM matches WHERE notice_id = ?", (r["id"],))
        db.ex("DELETE FROM notices WHERE id = ?", (r["id"],))
        try:
            (SNAPSHOT_DIR / f"{r['id']}.html").unlink(missing_ok=True)
        except OSError:
            pass
    db.ex("DELETE FROM notifications WHERE created_at < ? AND read = 1", (before,))


def _hm(value: str, default: str) -> tuple[int, int]:
    try:
        h, m = (value or default).split(":")
        return int(h), int(m)
    except ValueError:
        h, m = default.split(":")
        return int(h), int(m)


def configure() -> None:
    if _sched is None:
        return
    s = db.all_settings()
    hours = ",".join(x.strip() for x in str(s.get("crawl_hours") or "8").split(",") if x.strip().isdigit()) or "8"
    _sched.add_job(_safe(_crawl, "crawl"), CronTrigger(hour=hours, minute=5, timezone=TZ), id="crawl", replace_existing=True, max_instances=1, coalesce=True)
    h, m = _hm(s.get("digest_time"), "08:30")
    _sched.add_job(_safe(notify.daily_digest, "digest"), CronTrigger(hour=h, minute=m, timezone=TZ), id="digest", replace_existing=True)
    h, m = _hm(s.get("reminder_time"), "09:00")
    _sched.add_job(_safe(notify.daily_reminders, "reminders"), CronTrigger(hour=h, minute=m, timezone=TZ), id="reminders", replace_existing=True)
    _sched.add_job(_safe(_cleanup, "cleanup"), CronTrigger(hour=3, minute=30, timezone=TZ), id="cleanup", replace_existing=True)


def start() -> None:
    global _sched
    if _sched is not None:
        return
    _sched = BackgroundScheduler(timezone=TZ)
    _sched.start()
    configure()


def schedule_catch_up(delay_seconds: int = 45, stale_minutes: int = 60) -> None:
    """启动后补采：电脑睡眠或程序关闭期间错过了定时采集，启动后自动补一次。"""
    if _sched is None or not db.get_setting("crawl_enabled", True):
        return
    if not db.scalar("SELECT COUNT(*) FROM sources WHERE enabled = 1"):
        return
    last = db.scalar("SELECT MAX(started_at) FROM crawl_runs")
    threshold = (now() - timedelta(minutes=stale_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    if last and last > threshold:
        return
    from datetime import datetime

    run_at = datetime.now(TZ) + timedelta(seconds=delay_seconds)
    _sched.add_job(_safe(_crawl, "catch_up"), "date", run_date=run_at, id="catch_up", replace_existing=True)
    log.info("距上次采集已超过 %s 分钟，%s 秒后自动补采一次", stale_minutes, delay_seconds)


def shutdown() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None


def next_runs() -> dict:
    if _sched is None:
        return {}
    out = {}
    for j in _sched.get_jobs():
        out[j.id] = j.next_run_time.strftime("%Y-%m-%d %H:%M") if j.next_run_time else ""
    return out
