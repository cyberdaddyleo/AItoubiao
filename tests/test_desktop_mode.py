"""桌面模式：真实启动服务进程，验证本机令牌自动登录、错误令牌不登录、从网页退出程序。"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_desktop_auto_login_and_quit():
    data = tempfile.mkdtemp(prefix="bid_desktop_")
    port = _free_port()
    env = dict(os.environ, BID_DESKTOP="1", BID_PORT=str(port), BID_DATA_DIR=data, BID_SCHEDULER="0")
    env.pop("BID_ADMIN_PASSWORD", None)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/api/health", timeout=1).json().get("desktop"):
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        token = (Path(data) / "desktop.token").read_text(encoding="utf-8").strip()
        assert len(token) > 20
        # 首次启动生成了随机管理员密码文件
        assert (Path(data) / "初始管理员密码.txt").exists()

        with httpx.Client(base_url=base, follow_redirects=False) as c:
            r = c.get("/api/auth/desktop", params={"token": "wrong"})
            assert r.status_code == 302 and "bid_session" not in r.headers.get("set-cookie", "")
            r = c.get("/api/auth/desktop", params={"token": token})
            assert r.status_code == 302 and r.headers["location"] == "/#/dashboard"
            assert "bid_session" in r.headers.get("set-cookie", "")
            me = c.get("/api/auth/me").json()
            assert me["desktop"] is True and me["user"]["role"] == "admin"
            assert me["data_dir"] == str(Path(data).resolve())
            r = c.post("/api/desktop/quit")
            assert r.status_code == 200
        proc.wait(timeout=10)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
