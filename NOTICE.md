# 开源借鉴说明（NOTICE）

本系统为独立编写的代码。开发前参考了以下开源项目的设计思路，在此致谢并按其许可证要求注明来源。
没有使用 AGPL、非商业许可或未声明许可证的项目代码。

| 项目 | 许可证 | 借鉴了什么 |
|------|--------|-----------|
| [tlyyxjz/BidAgent（标小智）](https://github.com/tlyyxjz/BidAgent) | Apache-2.0 | 中国政府采购网栏目地址与采集合规做法（域名限速、403 立即停止、不使用代理池、SimHash 去重、页面快照留证）。栏目翻页规则已重新实测更正（第二页为 index_1.htm）。 |
| [zeronezer/bidcraft（标书匠）](https://github.com/zeronezer/bidcraft) | Apache-2.0 | 招标文件「按专项分任务抽取 + 关键词窗口均匀采样」的解析方法；写标约束（数字必须有出处、缺依据写【待补充】、历史素材只借鉴写法不照搬项目数据）。提示词为本项目重新编写。 |
| [GiorgosPanagopoulos/bidpilot](https://github.com/GiorgosPanagopoulos/bidpilot) | MIT | 匹配思路：硬条件过滤 + 打分 + 可解释的推荐理由；资格检查分硬性/提示两级；草稿默认待审核、不提供自动投递。 |

产品形态与功能划分还参考了 OpenBidKit（易标）、BidMaster-Pro、标擎 BidEngine 的公开 README，
仅参考功能思路，未使用其代码（其许可证分别为 AGPL-3.0 / AGPL 与 MIT 矛盾 / PolyForm Noncommercial）。

## 第三方依赖

FastAPI、Uvicorn、httpx、Beautiful Soup、lxml、python-docx、pypdf、APScheduler、jieba、python-multipart、tzdata，
均为 MIT / BSD / Apache 等宽松许可证，详见各自项目。

## 测试样本

`tests/fixtures/` 下的公告页面片段取自中国政府采购网 2026-10-15 公开发布的公告，仅用于解析器测试，
其中个人联系电话已脱敏；`ccgp_list_dfgg.html` 的最后两条为按同一结构补充的测试样例，已在文件内注明。
