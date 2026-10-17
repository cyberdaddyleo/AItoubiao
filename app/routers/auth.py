from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from .. import db
from ..config import now_str
from ..security import (
    ROLES,
    SESSION_COOKIE,
    SESSION_DAYS,
    create_session,
    current_user,
    drop_session,
    hash_password,
    public_user,
    require,
    validate_password_strength,
    verify_password,
)
from . import must, pick

router = APIRouter(prefix="/api", tags=["auth"])

_fail: dict[str, list[float]] = {}


@router.post("/auth/login")
def login(response: Response, request: Request, payload: dict = Body(...)):
    import time

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    ip = request.client.host if request.client else "?"
    key = f"{ip}:{username}"
    recent = [t for t in _fail.get(key, []) if time.time() - t < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "失败次数过多，请 10 分钟后再试")
    u = db.q1("SELECT * FROM users WHERE username = ? AND active = 1", (username,))
    if not u or not verify_password(password, u["password_hash"]):
        recent.append(time.time())
        _fail[key] = recent
        raise HTTPException(400, "用户名或密码错误")
    _fail.pop(key, None)
    token = create_session(u["id"])
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")
    db.audit(u, "登录", ip)
    return {"user": public_user(u), "token": token}


@router.get("/auth/desktop")
def desktop_login(request: Request, token: str = ""):
    """桌面模式自动登录：启动器带着本机令牌打开这个地址，直接以管理员身份进入系统。

    只在 BID_DESKTOP=1 时启用，只接受来自本机（127.0.0.1）的请求；令牌每次启动重新生成。
    """
    import hmac as _hmac

    from fastapi.responses import RedirectResponse

    from ..config import DATA_DIR, DESKTOP

    if not DESKTOP:
        raise HTTPException(404, "Not Found")
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "只允许本机访问")
    path = DATA_DIR / "desktop.token"
    expected = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not expected or not _hmac.compare_digest(expected, token or ""):
        return RedirectResponse("/", status_code=302)
    u = db.q1("SELECT * FROM users WHERE role = 'admin' AND active = 1 ORDER BY id LIMIT 1")
    if not u:
        raise HTTPException(500, "没有可用的管理员账号")
    session = create_session(u["id"])
    resp = RedirectResponse("/#/dashboard", status_code=302)
    resp.set_cookie(SESSION_COOKIE, session, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")
    return resp


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        drop_session(token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    from .. import llm
    from ..config import DATA_DIR, DESKTOP

    return {
        "user": public_user(user),
        "roles": ROLES,
        "system_name": db.get_setting("system_name"),
        "llm_enabled": llm.enabled(),
        "desktop": DESKTOP,
        "data_dir": str(DATA_DIR) if DESKTOP else "",
    }


@router.post("/auth/password")
def change_password(payload: dict = Body(...), user: dict = Depends(current_user)):
    u = db.get("users", user["id"])
    if not verify_password(payload.get("old_password") or "", u["password_hash"]):
        raise HTTPException(400, "原密码不正确")
    new = payload.get("new_password") or ""
    validate_password_strength(new)
    db.update("users", user["id"], {"password_hash": hash_password(new)})
    db.audit(user, "修改密码")
    return {"ok": True}


@router.get("/users")
def list_users(user: dict = Depends(current_user)):
    return [public_user(u) for u in db.q("SELECT * FROM users ORDER BY id")]


@router.post("/users")
def create_user(payload: dict = Body(...), user: dict = Depends(require("admin"))):
    data = pick(payload, ["username", "display_name", "role", "phone"])
    if not data.get("username"):
        raise HTTPException(400, "请填写用户名")
    if data.get("role") not in ROLES:
        raise HTTPException(400, "角色不正确")
    if db.q1("SELECT id FROM users WHERE username = ?", (data["username"],)):
        raise HTTPException(400, "用户名已存在")
    pw = payload.get("password") or ""
    validate_password_strength(pw)
    data.update({"password_hash": hash_password(pw), "active": 1, "created_at": now_str()})
    uid = db.insert("users", data)
    db.audit(user, "新建用户", data["username"])
    return public_user(db.get("users", uid))


@router.put("/users/{uid}")
def update_user(uid: int, payload: dict = Body(...), user: dict = Depends(require("admin"))):
    must(db.get("users", uid), "用户")
    data = pick(payload, ["display_name", "role", "phone", "active"])
    if "role" in data and data["role"] not in ROLES:
        raise HTTPException(400, "角色不正确")
    if uid == user["id"] and (data.get("active") == 0 or (data.get("role") and data["role"] != "admin")):
        raise HTTPException(400, "不能停用或降级自己的管理员账号")
    if payload.get("password"):
        validate_password_strength(payload["password"])
        data["password_hash"] = hash_password(payload["password"])
    db.update("users", uid, data)
    if data.get("active") == 0:
        db.ex("DELETE FROM sessions WHERE user_id = ?", (uid,))
    db.audit(user, "修改用户", str(uid))
    return public_user(db.get("users", uid))
