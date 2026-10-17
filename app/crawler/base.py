"""HTTP 抓取器：限速、robots.txt、封禁识别、附件下载。"""
from __future__ import annotations

import hashlib
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .. import db

BLOCK_MARKERS = (
    "访问过于频繁",
    "操作过于频繁",
    "频繁访问",
    "请输入验证码",
    "人机验证",
    "安全验证",
    "访问受限",
    "拒绝访问",
)


class StopCrawl(Exception):
    """应当立即停止本来源采集（被限频/要求验证码/403）。"""


class RobotsDisallowed(Exception):
    pass


class _DomainLimiter:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str, interval: float) -> None:
        with self._lock:
            last = self._last.get(domain, 0.0)
            now = time.monotonic()
            sleep_for = last + interval - now
            self._last[domain] = max(now, last + interval)
        if sleep_for > 0:
            time.sleep(sleep_for)


_limiter = _DomainLimiter()
_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
_robots_lock = threading.Lock()


def looks_blocked(text: str) -> str:
    head = (text or "")[:6000]
    for m in BLOCK_MARKERS:
        if m in head:
            return m
    return ""


class Fetcher:
    def __init__(self, interval: float = 4.0, timeout: float = 25.0):
        s = db.all_settings()
        self.ua = s.get("crawl_user_agent") or db.DEFAULT_SETTINGS["crawl_user_agent"]
        self.respect_robots = bool(s.get("crawl_respect_robots", True))
        self.interval = max(1.0, float(interval))
        self.timeout = timeout
        self.request_count = 0
        self.client = httpx.Client(
            headers={
                "User-Agent": self.ua,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -------------------------------------------------------------- robots

    def _robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlparse(url)
        key = f"{p.scheme}://{p.netloc}"
        with _robots_lock:
            cached = _robots_cache.get(key)
            if cached and time.time() - cached[0] < 3600:
                return cached[1]
        rp: urllib.robotparser.RobotFileParser | None = None
        try:
            r = self.client.get(key + "/robots.txt", timeout=10)
            body = r.text if r.status_code == 200 else ""
            # 有的站点 robots.txt 返回的是 HTML 错误页，视为没有 robots 规则
            if body and "<html" not in body[:500].lower():
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
        except httpx.HTTPError:
            rp = None
        with _robots_lock:
            _robots_cache[key] = (time.time(), rp)
        return rp

    def check_robots(self, url: str) -> None:
        if not self.respect_robots:
            return
        rp = self._robots(url)
        if rp is not None and not rp.can_fetch(self.ua, url):
            raise RobotsDisallowed(f"robots.txt 不允许采集：{url}")

    # -------------------------------------------------------------- 请求

    def _guard(self, r: httpx.Response, url: str) -> None:
        if r.status_code in (403, 429):
            raise StopCrawl(f"站点返回 {r.status_code}（可能被限频），已停止本次采集：{url}")
        if r.status_code >= 500:
            raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)

    def get(self, url: str, *, interval: float | None = None, check_block: bool = True) -> str:
        self.check_robots(url)
        _limiter.wait(urlparse(url).netloc, interval or self.interval)
        self.request_count += 1
        r = self.client.get(url)
        self._guard(r, url)
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.charset_encoding or "utf-8"
            if "charset=gb" in r.text[:2000].lower():
                r.encoding = "gb18030"
        text = r.text
        if check_block:
            marker = looks_blocked(text)
            if marker and len(text) < 20000:
                raise StopCrawl(f"站点提示「{marker}」，已停止本次采集（系统不会绕过验证/限频）：{url}")
        return text

    def post_form(self, url: str, data: dict, *, headers: dict | None = None, interval: float | None = None) -> httpx.Response:
        self.check_robots(url)
        _limiter.wait(urlparse(url).netloc, interval or self.interval)
        self.request_count += 1
        r = self.client.post(url, data=data, headers=headers or {})
        self._guard(r, url)
        return r

    def download(self, url: str, dest: Path, *, max_mb: int = 60, interval: float | None = None) -> dict:
        self.check_robots(url)
        _limiter.wait(urlparse(url).netloc, interval or self.interval)
        self.request_count += 1
        h = hashlib.sha256()
        size = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", url) as r:
            self._guard(r, url)
            ctype = r.headers.get("content-type", "")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        raise ValueError(f"附件超过 {max_mb}MB，已跳过")
                    h.update(chunk)
                    f.write(chunk)
            disp = r.headers.get("content-disposition", "")
        return {"size": size, "sha256": h.hexdigest(), "content_type": ctype, "disposition": disp}
