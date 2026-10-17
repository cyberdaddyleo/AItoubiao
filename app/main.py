"""程序入口：python -m app.main 或 uvicorn app.main:app"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, scheduler
from .config import DATA_DIR, DESKTOP, HOST, PORT, SCHEDULER_ENABLED, STATIC_DIR
from .crawler import runner
from .routers import auth, core, notices, projects, system
from .security import ensure_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bid")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    pw = ensure_admin()
    if pw:
        log.info("=" * 60)
        log.info("首次启动，已创建管理员账号 admin")
        log.info("初始密码见 %s", DATA_DIR / "初始管理员密码.txt")
        log.info("=" * 60)
    runner.ensure_default_sources()
    if DESKTOP:
        write_desktop_token()
    if SCHEDULER_ENABLED:
        scheduler.start()
        scheduler.schedule_catch_up()
    yield
    scheduler.shutdown()


def write_desktop_token() -> None:
    """桌面模式：每次启动生成新的本机登录令牌，只有本机用户可读。"""
    import os
    import secrets

    path = DATA_DIR / "desktop.token"
    path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


app = FastAPI(title="AI智能投标系统", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)

for r in (auth.router, core.router, notices.router, projects.router, system.router):
    app.include_router(r)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("未处理的错误：%s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"服务器内部错误：{type(exc).__name__}: {exc}"}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "desktop": DESKTOP}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def run():
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, workers=1)


if __name__ == "__main__":
    run()
