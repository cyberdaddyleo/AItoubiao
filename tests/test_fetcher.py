"""用本地 HTTP 服务验证抓取器的合规行为与自定义来源的真实网络链路。"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import fixture_text

from app import db
from app.crawler import runner
from app.crawler.base import Fetcher, RobotsDisallowed, StopCrawl

LIST = """<html><body><ul class="list">
<li><a href="/notice/1.html">某市第一人民医院消防设施维保服务项目竞争性磋商公告</a><span>2026-10-15</span></li>
<li><a href="/notice/2.html">某市教育局学生课桌椅采购项目公开招标公告</a><span>2026-10-15</span></li>
<li><a href="/about.html">关于我们</a></li>
</ul></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path
        if p == "/robots.txt":
            return self._send(200, "User-agent: *\nDisallow: /private\n", "text/plain")
        if p.startswith("/list"):
            return self._send(200, LIST)
        if p.startswith("/notice/"):
            return self._send(200, fixture_text("ccgp_detail_tender.html"))
        if p == "/blocked":
            return self._send(200, fixture_text("ccgp_search_blocked.html"))
        if p == "/forbidden":
            return self._send(403, "forbidden")
        return self._send(200, "<html><body>ok 正常页面</body></html>")


@pytest.fixture(scope="module")
def server(monkeypatch_module=None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def test_fetcher_compliance(server):
    db.init_db()
    with Fetcher(interval=1) as f:
        assert "正常页面" in f.get(server + "/ok")
        with pytest.raises(RobotsDisallowed):
            f.get(server + "/private/page")
        with pytest.raises(StopCrawl):
            f.get(server + "/blocked")
        with pytest.raises(StopCrawl):
            f.get(server + "/forbidden")
        t0 = time.monotonic()
        f.get(server + "/ok", interval=1.2)
        f.get(server + "/ok", interval=1.2)
        assert time.monotonic() - t0 >= 1.1  # 同一域名两次请求之间至少间隔 interval


def test_custom_source_end_to_end(server):
    db.init_db()
    from app.config import now_str

    cid = db.insert("companies", {"name": "测试维保公司", "created_at": now_str(), "updated_at": now_str()})
    db.insert("subscriptions", {"company_id": cid, "name": "维保", "keywords_include": "维保", "notice_types": "tender", "enabled": 1, "created_at": now_str(), "updated_at": now_str()})
    preview = runner.test_custom_source({"list_url": server + "/list"})
    assert preview["count"] == 2
    assert preview["first_detail"]["fields"]["project_no"] == "JLSZC20260089720"
    sid = db.insert("sources", {"name": "本地测试站", "type": "custom_list", "config_json": db.jdump({"list_url": server + "/list", "interval": 1}), "enabled": 1, "created_at": now_str()})
    stats = runner.run_source(sid, trigger="manual")
    assert stats["status"] == "ok", stats
    assert stats["new"] == 2
    assert stats["detail"] == 1  # 只有标题含「维保」的那条抓详情
    m = db.q1("SELECT m.* FROM matches m JOIN notices n ON n.id = m.notice_id WHERE m.company_id = ? AND n.title LIKE '%维保%'", (cid,))
    assert m is not None
