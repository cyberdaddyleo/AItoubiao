"""登录、会话与角色权限。

角色：
- admin    管理员：全部权限，含系统设置、用户、采集来源
- manager  负责人：全部业务权限，可审核、看数据
- sales    商务：找标、立项、维护订阅、管理投标项目
- writer   标书编写：解析招标文件、编写标书
- reviewer 审核：审核标书、合规体检
- finance  财务：保证金管理、看数据
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from . import db
from .config import DATA_DIR, now, now_str

ROLES = {
    "admin": "管理员",
    "manager": "负责人",
    "sales": "商务",
    "writer": "标书编写",
    "reviewer": "审核",
    "finance": "财务",
}

SESSION_COOKIE = "bid_session"
SESSION_DAYS = 7
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    if password.isdigit() or password.isalpha():
        raise HTTPException(400, "密码需同时包含字母和数字")


def ensure_admin() -> str | None:
    """首次启动时创建管理员账号。

    密码优先取环境变量 BID_ADMIN_PASSWORD；否则随机生成并写入 data/初始管理员密码.txt。
    返回生成的密码（仅首次），用于启动日志提示。
    """
    if db.scalar("SELECT COUNT(*) FROM users") > 0:
        return None
    password = os.environ.get("BID_ADMIN_PASSWORD") or (secrets.token_urlsafe(9) + "a1")
    db.insert(
        "users",
        {
            "username": "admin",
            "password_hash": hash_password(password),
            "display_name": "系统管理员",
            "role": "admin",
            "active": 1,
            "created_at": now_str(),
        },
    )
    if not os.environ.get("BID_ADMIN_PASSWORD"):
        (DATA_DIR / "初始管理员密码.txt").write_text(
            "管理员账号：admin\n初始密码：" + password + "\n\n请登录后在右上角「修改密码」里改掉，并删除本文件。\n",
            encoding="utf-8",
        )
    return password


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    db.insert("sessions", {"token": token, "user_id": user_id, "expires_at": expires, "created_at": now_str()})
    db.ex("DELETE FROM sessions WHERE expires_at < ?", (now_str(),))
    return token


def drop_session(token: str) -> None:
    db.ex("DELETE FROM sessions WHERE token = ?", (token,))


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def current_user(request: Request) -> dict:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(401, "请先登录")
    row = db.q1(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ? AND u.active = 1",
        (token, now_str()),
    )
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    row.pop("password_hash", None)
    return row


def require(*roles: str):
    """依赖：要求当前用户属于指定角色之一（admin 永远放行）。"""

    def _dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] == "admin" or not roles or user["role"] in roles:
            return user
        names = "、".join(ROLES.get(r, r) for r in roles)
        raise HTTPException(403, f"该操作需要以下角色之一：{names}")

    return _dep


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "username": u["username"],
        "display_name": u.get("display_name") or u["username"],
        "role": u["role"],
        "role_name": ROLES.get(u["role"], u["role"]),
        "phone": u.get("phone", ""),
        "active": u.get("active", 1),
    }
