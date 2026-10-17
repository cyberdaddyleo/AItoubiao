"""运行配置：路径、时区、环境变量。

所有业务配置（AI 模型、推送、采集频率等）存放在数据库 settings 表里，
可在「系统设置」页面修改；这里只放启动前就必须确定的东西。
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BID_DATA_DIR", str(BASE_DIR / "data"))).resolve()
FILES_DIR = DATA_DIR / "files"
EXPORT_DIR = DATA_DIR / "exports"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "bidding.db"
STATIC_DIR = BASE_DIR / "static"

TZ = ZoneInfo(os.environ.get("BID_TZ", "Asia/Shanghai"))

# 桌面模式（Mac 应用 / Windows 双击启动）：只监听本机，启动器用一次性令牌自动登录
DESKTOP = os.environ.get("BID_DESKTOP", "0") == "1"

HOST = os.environ.get("BID_HOST", "127.0.0.1" if DESKTOP else "0.0.0.0")
PORT = int(os.environ.get("BID_PORT", "8600"))

# 设为 0 可关闭内置定时任务（例如多进程部署时只让一个进程跑定时任务）
SCHEDULER_ENABLED = os.environ.get("BID_SCHEDULER", "1") != "0"

MAX_UPLOAD_MB = int(os.environ.get("BID_MAX_UPLOAD_MB", "100"))

for _d in (DATA_DIR, FILES_DIR, EXPORT_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_secret() -> str:
    path = DATA_DIR / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _load_secret()


def now() -> datetime:
    """当前本地时间（不带时区，便于与数据库中的字符串比较）。"""
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def now_str() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return now().strftime("%Y-%m-%d")
