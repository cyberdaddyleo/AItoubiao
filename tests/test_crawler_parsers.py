"""用 2026-10-15 从真实页面取下的样本测试解析器。"""
from conftest import fixture_text

from app.crawler import ccgp, generic
from app.crawler.base import looks_blocked
from app.extract import classify_notice


def test_ccgp_list_page():
    html = fixture_text("ccgp_list_gkzb.html")
    items = ccgp.parse_list(html, "http://www.ccgp.gov.cn/cggg/zygg/gkzb/")
    assert len(items) == 14
    first = items[0]
    assert first["url"] == "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261015_27496414.htm"
    # 列表上显示的是截断标题，应取 title 属性里的完整标题
    assert first["title"].endswith("公开招标公告")
    assert "KHZBCG-2026-095#" in first["title"]
    assert first["publish_time"] == "2026-10-15 14:16"
    assert first["region"] == "江西"
    assert first["purchaser"] == "上饶市信州区消防救援大队"
    assert first["type_label"] == "公开招标"
    assert first["notice_type"] == "tender"
    assert ccgp.parse_page_count(html) == 25


def test_ccgp_aggregate_list_with_type_labels():
    html = fixture_text("ccgp_list_dfgg.html")
    items = ccgp.parse_list(html, "http://www.ccgp.gov.cn/cggg/dfgg/")
    types = [(i["type_label"], i["notice_type"]) for i in items]
    assert ("更正公告", "change") in types
    assert ("中标公告", "award") in types
    assert ("公开招标", "tender") in types
    assert ("竞争性磋商", "tender") in types
    # 「结果（废标）公告」虽然在「其他公告」栏目，也要识别为废标
    fail = [i for i in items if "废标" in i["title"]][0]
    assert fail["notice_type"] == "fail"
    assert items[0]["url"].startswith("http://www.ccgp.gov.cn/cggg/dfgg/gzgg/202610/")


def test_ccgp_page_urls():
    assert ccgp.list_url("dfgg/gkzb", 1) == "http://www.ccgp.gov.cn/cggg/dfgg/gkzb/"
    assert ccgp.list_url("dfgg/gkzb", 2) == "http://www.ccgp.gov.cn/cggg/dfgg/gkzb/index_1.htm"
    assert ccgp.list_url("zygg/zbgg", 5) == "http://www.ccgp.gov.cn/cggg/zygg/zbgg/index_4.htm"


def test_ccgp_tender_detail():
    html = fixture_text("ccgp_detail_tender.html")
    d = ccgp.parse_detail(html, "http://www.ccgp.gov.cn/cggg/zygg/gkzb/202610/t20261015_27496413.htm")
    assert d["title"] == "吉林省消防救援总队-2026年消防专项建设项目第五标段公开招标公告"
    assert d["publish_time"] == "2026-10-15 14:16"
    assert d["table"]["品目"] == "货物/设备/政法、消防、检测设备/消防设备"
    f = d["fields"]
    assert f["project_no"] == "JLSZC20260089720"
    assert f["budget"] == 2550000.0
    assert f["deadline"] == "2026-11-05 09:30"
    assert f["open_time"] == "2026-11-05 09:30"
    assert f["doc_get_end"] == "2026-10-22"
    assert f["purchaser"] == "吉林省消防救援总队"
    assert f["agency"].startswith("吉林省公共资源交易中心")
    assert f["region"] == "长春市"
    assert f["joint_venture"] == "不接受"
    assert f["sme_only"] == 1
    assert "特定资格要求：无" in f["qualification_text"]
    assert d["attachments"] == [
        {"name": "招标文件正文.pdf", "url": "http://download.ccgp.gov.cn/oss/download?uuid=02A893651E69989AAF1480DB813DD0"}
    ]
    # 页脚等噪声不应进入正文
    assert "主办单位" not in d["content_text"]


def test_ccgp_award_detail_winner():
    html = fixture_text("ccgp_detail_award.html")
    d = ccgp.parse_detail(html)
    assert d["winners"][0]["name"] == "贵州恒信智通科技有限公司"
    assert d["winners"][0]["amount"] == 478029.0
    assert d["winners"][0]["score"] == 92.09
    assert d["fields"]["project_no"] == "YLZB2026QDN/06"
    assert d["fields"]["award_amount"] == 478000.0
    assert classify_notice(d["title"]) == "award"


def test_ccgp_search_page_and_block_detection():
    items, total = ccgp.parse_search(fixture_text("ccgp_search.html"))
    assert total == 216
    assert len(items) == 2
    it = items[1]
    assert it["title"].startswith("吉林省消防救援总队-2026年消防专项建设项目")
    assert it["purchaser"] == "吉林省消防救援总队"
    assert it["agency"].startswith("吉林省公共资源交易中心")
    assert it["type_label"] == "公开招标公告"
    assert it["region"] == "吉林"
    assert it["publish_time"] == "2026-10-15 14:16"
    assert "消防设备" in it["category"]
    assert looks_blocked(fixture_text("ccgp_search_blocked.html"))
    assert not looks_blocked(fixture_text("ccgp_detail_tender.html"))


def test_generic_detail_on_ccgp_page():
    d = generic.parse_detail(fixture_text("ccgp_detail_tender.html"), "http://example.com/x.htm")
    assert "项目编号" in d["content_text"]
    assert d["fields"]["project_no"] == "JLSZC20260089720"
    assert d["fields"]["deadline"] == "2026-11-05 09:30"


def test_generic_list_auto_detect():
    html = """
    <ul><li><a href="/a/1.html">首页</a></li>
    <li><a href="/notice/2026/1001.html">某市人民医院信息化运维服务项目竞争性磋商公告</a><span>2026-10-14</span></li>
    <li><a href="/notice/2026/1002.html" title="某区教育局智慧教室设备采购项目中标结果公告">某区教育局智慧教室设备...</a> 2026年10月13日</li>
    <li><a href="javascript:void(0)">更多公告信息</a></li></ul>"""
    items = generic.parse_list(html, "http://ggzy.example.gov.cn/list.html", {})
    assert [i["url"] for i in items] == [
        "http://ggzy.example.gov.cn/notice/2026/1001.html",
        "http://ggzy.example.gov.cn/notice/2026/1002.html",
    ]
    assert items[0]["publish_time"] == "2026-10-14"
    assert items[1]["notice_type"] == "award"
    assert items[1]["title"] == "某区教育局智慧教室设备采购项目中标结果公告"
