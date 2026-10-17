"""SQLite 数据层：建表、迁移与少量通用读写工具。

单机部署用 SQLite（WAL 模式）足够支撑一家企业或一个集团日常使用；
表结构保持简单，后续如需换 PostgreSQL，只需替换本文件的连接与少量 SQL 方言。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DB_PATH, now_str

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'sales',
    phone TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 投标主体（一家企业或集团下的多家子公司），资料按主体隔离
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    short_name TEXT DEFAULT '',
    credit_code TEXT DEFAULT '',
    legal_person TEXT DEFAULT '',
    legal_person_title TEXT DEFAULT '法定代表人',
    reg_capital TEXT DEFAULT '',
    founded_date TEXT DEFAULT '',
    address TEXT DEFAULT '',
    postcode TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    fax TEXT DEFAULT '',
    email TEXT DEFAULT '',
    bank_name TEXT DEFAULT '',
    bank_account_note TEXT DEFAULT '',
    contact_person TEXT DEFAULT '',
    contact_phone TEXT DEFAULT '',
    business_scope TEXT DEFAULT '',
    intro TEXT DEFAULT '',
    is_sme INTEGER DEFAULT 1,
    annual_revenue_wan REAL,
    staff_count INTEGER,
    industries TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 企业资料库：基础资料/资质证书/业绩/人员/方案素材/历史标书
CREATE TABLE IF NOT EXISTS library_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    number TEXT DEFAULT '',
    issuer TEXT DEFAULT '',
    level TEXT DEFAULT '',
    valid_from TEXT DEFAULT '',
    valid_to TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    content TEXT DEFAULT '',
    data_json TEXT DEFAULT '{}',
    file_ids TEXT DEFAULT '[]',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_company ON library_items(company_id, category);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    mime TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    sha256 TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    uploaded_by INTEGER,
    created_at TEXT NOT NULL
);

-- 采集来源
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT DEFAULT '',
    last_run_at TEXT,
    last_status TEXT DEFAULT '',
    last_message TEXT DEFAULT '',
    paused_until TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    list_count INTEGER DEFAULT 0,
    new_count INTEGER DEFAULT 0,
    detail_count INTEGER DEFAULT 0,
    request_count INTEGER DEFAULT 0,
    message TEXT DEFAULT ''
);

-- 标讯
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    source_name TEXT DEFAULT '',
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    notice_type TEXT DEFAULT 'other',
    type_label TEXT DEFAULT '',
    publish_time TEXT DEFAULT '',
    region TEXT DEFAULT '',
    purchaser TEXT DEFAULT '',
    agency TEXT DEFAULT '',
    category TEXT DEFAULT '',
    project_no TEXT DEFAULT '',
    project_name TEXT DEFAULT '',
    budget REAL,
    deadline TEXT DEFAULT '',
    open_time TEXT DEFAULT '',
    doc_get_end TEXT DEFAULT '',
    bond_text TEXT DEFAULT '',
    joint_venture TEXT DEFAULT '',
    sme_only INTEGER DEFAULT 0,
    qualification_text TEXT DEFAULT '',
    content_text TEXT DEFAULT '',
    detail_fetched INTEGER DEFAULT 0,
    attachments_json TEXT DEFAULT '[]',
    winners_json TEXT DEFAULT '[]',
    award_amount REAL,
    ai_summary_json TEXT DEFAULT '',
    simhash TEXT DEFAULT '',
    dup_of INTEGER,
    snapshot_sha256 TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notices_publish ON notices(publish_time);
CREATE INDEX IF NOT EXISTS idx_notices_type ON notices(notice_type);
CREATE INDEX IF NOT EXISTS idx_notices_purchaser ON notices(purchaser);
CREATE INDEX IF NOT EXISTS idx_notices_projectno ON notices(project_no);

-- 订阅画像（每个主体可以有多条）
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    keywords_include TEXT DEFAULT '',
    keywords_exclude TEXT DEFAULT '',
    regions TEXT DEFAULT '',
    categories TEXT DEFAULT '',
    notice_types TEXT DEFAULT 'tender',
    budget_min REAL,
    budget_max REAL,
    purchaser_keywords TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 匹配结果（标讯 × 主体）
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    subscription_id INTEGER,
    score REAL DEFAULT 0,
    level TEXT DEFAULT '待评估',
    level_by TEXT DEFAULT 'rule',
    reasons_json TEXT DEFAULT '[]',
    missing_json TEXT DEFAULT '[]',
    keyword_hits TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    assigned_to INTEGER,
    notified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(notice_id, company_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_company ON matches(company_id, status);

-- 投标项目（立项后）
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    notice_id INTEGER,
    name TEXT NOT NULL,
    project_no TEXT DEFAULT '',
    purchaser TEXT DEFAULT '',
    agency TEXT DEFAULT '',
    region TEXT DEFAULT '',
    category TEXT DEFAULT '',
    budget REAL,
    status TEXT NOT NULL DEFAULT 'preparing',
    owner_id INTEGER,
    signup_deadline TEXT DEFAULT '',
    clarify_deadline TEXT DEFAULT '',
    deadline TEXT DEFAULT '',
    open_time TEXT DEFAULT '',
    bond_amount REAL,
    bond_deadline TEXT DEFAULT '',
    bond_status TEXT DEFAULT 'none',
    bond_paid_at TEXT DEFAULT '',
    bond_refunded_at TEXT DEFAULT '',
    platform_name TEXT DEFAULT '',
    platform_account_id INTEGER,
    ca_holder TEXT DEFAULT '',
    submit_method TEXT DEFAULT 'electronic',
    submitted_at TEXT DEFAULT '',
    receipt_file_ids TEXT DEFAULT '[]',
    bid_amount REAL,
    result TEXT DEFAULT '',
    result_amount REAL,
    winner TEXT DEFAULT '',
    our_score REAL,
    result_note TEXT DEFAULT '',
    result_notice_id INTEGER,
    note TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    comment TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- 招标文件及解析结果
CREATE TABLE IF NOT EXISTS tender_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    file_id INTEGER,
    filename TEXT DEFAULT '',
    text_chars INTEGER DEFAULT 0,
    pages INTEGER DEFAULT 0,
    text_path TEXT DEFAULT '',
    warning TEXT DEFAULT '',
    analysis_json TEXT DEFAULT '',
    analysis_by TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 标书（一个项目一份，多版本通过 section_versions 留痕）
CREATE TABLE IF NOT EXISTS bid_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    price_json TEXT DEFAULT '{}',
    settings_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bid_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    order_no REAL NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    form_key TEXT DEFAULT '',
    requirement TEXT DEFAULT '',
    content TEXT DEFAULT '',
    refs_json TEXT DEFAULT '[]',
    ai_generated INTEGER DEFAULT 0,
    reviewed INTEGER DEFAULT 0,
    updated_by INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sections_doc ON bid_sections(doc_id, order_no);

CREATE TABLE IF NOT EXISTS section_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL,
    content TEXT,
    note TEXT DEFAULT '',
    user_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    summary TEXT DEFAULT '',
    result_json TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

-- 平台账号与 CA 台账（不保存任何密码）
CREATE TABLE IF NOT EXISTS platform_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    platform_name TEXT NOT NULL,
    url TEXT DEFAULT '',
    account_name TEXT DEFAULT '',
    registered INTEGER DEFAULT 1,
    ca_type TEXT DEFAULT '',
    ca_holder TEXT DEFAULT '',
    ca_expire TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    kind TEXT DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    link TEXT DEFAULT '',
    dedup_key TEXT UNIQUE,
    read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    progress INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    result_json TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 1,
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT DEFAULT '',
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_local = threading.local()
_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    """每个线程复用一个连接（FastAPI 同步接口运行在线程池里）。"""
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


@contextmanager
def tx():
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def init_db() -> None:
    with _init_lock:
        c = conn()
        c.executescript(SCHEMA)
        c.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        c.commit()


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def q(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def q1(sql: str, params: Iterable[Any] = ()) -> dict | None:
    return _row(conn().execute(sql, tuple(params)).fetchone())


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    r = conn().execute(sql, tuple(params)).fetchone()
    return r[0] if r is not None else None


def ex(sql: str, params: Iterable[Any] = ()) -> int:
    c = conn()
    cur = c.execute(sql, tuple(params))
    c.commit()
    return cur.lastrowid or cur.rowcount


def insert(table: str, data: dict, *, or_ignore: bool = False) -> int:
    data = dict(data)
    cols = list(data.keys())
    verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
    sql = f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    c = conn()
    cur = c.execute(sql, [data[k] for k in cols])
    c.commit()
    return cur.lastrowid if cur.rowcount else 0


def update(table: str, row_id: int, data: dict) -> None:
    if not data:
        return
    cols = list(data.keys())
    sql = f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?"
    ex(sql, [data[k] for k in cols] + [row_id])


def get(table: str, row_id: int) -> dict | None:
    return q1(f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def jload(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS: dict[str, Any] = {
    # AI 模型
    "llm_provider": "none",          # none / openai(兼容接口) / anthropic
    "llm_preset": "",
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "llm_temperature": 0.3,
    "llm_timeout": 120,
    "llm_max_tokens": 4096,
    "ai_auto_check_top_n": 10,       # 每次采集后自动用 AI 复核前 N 条匹配的资格
    # 采集
    "crawl_enabled": True,
    "crawl_hours": "8,9,10,11,12,13,14,15,16,17,18,19,20,22",   # 每天哪些整点运行采集（地方公开招标栏目更新很快）
    "crawl_respect_robots": True,
    "crawl_user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AIBidAssistant/1.0"
    ),
    "crawl_detail_policy": "matched",   # matched / all / none
    "crawl_lookback_days": 3,
    "crawl_keep_days": 180,
    # 推送
    "digest_time": "08:30",
    "reminder_time": "09:00",
    "notify_wecom_webhook": "",
    "notify_dingtalk_webhook": "",
    "notify_dingtalk_secret": "",
    "notify_feishu_webhook": "",
    "notify_feishu_secret": "",
    "digest_top_n": 15,
    "remind_days": "7,3,1",
    "expire_warn_days": 30,
    # 系统
    "system_name": "AI智能投标系统",
}

SECRET_SETTING_KEYS = {
    "llm_api_key",
    "notify_wecom_webhook",
    "notify_dingtalk_webhook",
    "notify_dingtalk_secret",
    "notify_feishu_webhook",
    "notify_feishu_secret",
}


def get_setting(key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM settings WHERE key = ?", (key,))
    if r is None:
        return DEFAULT_SETTINGS.get(key, default)
    return jload(r["value"], r["value"])


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        out[r["key"]] = jload(r["value"], r["value"])
    return out


def set_setting(key: str, value: Any) -> None:
    ex(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, jdump(value)),
    )


def audit(user: dict | None, action: str, target: str = "", detail: str = "") -> None:
    insert(
        "audit_logs",
        {
            "user_id": user["id"] if user else None,
            "username": user["username"] if user else "system",
            "action": action,
            "target": target,
            "detail": detail[:2000],
            "created_at": now_str(),
        },
    )
