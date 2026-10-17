"""后台任务：耗时操作（采集、整本标书 AI 生成、招标文件 AI 解析）放到线程里跑，前端轮询进度。"""
from __future__ import annotations

import threading
import traceback
from typing import Callable

from . import db
from .config import now_str


def start(kind: str, fn: Callable, *args, user_id: int | None = None, total: int = 0, message: str = "") -> int:
    jid = db.insert(
        "jobs",
        {"kind": kind, "status": "running", "progress": 0, "total": total, "message": message, "created_by": user_id, "created_at": now_str(), "updated_at": now_str()},
    )

    def runner():
        try:
            result = fn(jid, *args)
            db.update("jobs", jid, {"status": "done", "result_json": db.jdump(result) if result is not None else "", "updated_at": now_str()})
        except Exception as e:  # noqa: BLE001
            db.update(
                "jobs",
                jid,
                {"status": "error", "message": f"{type(e).__name__}: {e}"[:1000], "result_json": db.jdump({"trace": traceback.format_exc()[-2000:]}), "updated_at": now_str()},
            )

    threading.Thread(target=runner, name=f"job-{kind}-{jid}", daemon=True).start()
    return jid


def progress(job_id: int, done: int, total: int | None = None, message: str = "") -> None:
    upd = {"progress": done, "updated_at": now_str()}
    if total is not None:
        upd["total"] = total
    if message:
        upd["message"] = message[:1000]
    db.update("jobs", job_id, upd)


def get(job_id: int) -> dict | None:
    j = db.get("jobs", job_id)
    if j:
        j["result"] = db.jload(j.pop("result_json"), None)
    return j
