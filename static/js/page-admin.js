/* 采集来源 + 系统设置（AI 模型 / 采集 / 推送 / 用户 / 审计） */
(function () {
  "use strict";
  var h = App.h;

  // ------------------------------------------------------------ 采集来源
  App.routes.sources = function (main) {
    var box = h("div");
    main.appendChild(h("div", { class: "req mb" }, "合规采集：只采集公开发布的公告；遵守 robots.txt；每个网站限速；遇到「访问过于频繁」或验证码会立即停止并暂停该来源，系统不会识别或绕过验证码，也不使用代理换 IP。"));
    var top = h("div", { class: "row mb" }, h("span", { class: "spacer" }));
    if (App.can("manager", "sales")) top.appendChild(h("button", { class: "btn", on: { click: App.runCrawl } }, "立即采集全部"));
    if (App.can()) top.appendChild(h("button", { class: "btn primary", on: { click: function () { customDialog(null); } } }, "添加自定义来源"));
    main.appendChild(top);
    main.appendChild(box);
    var meta = {};
    function load() {
      App.get("/api/sources").then(function (d) {
        meta = d;
        App.clear(box);
        d.sources.forEach(function (s) { box.appendChild(sourcePanel(s)); });
        var runs = h("div");
        box.appendChild(App.panel("最近运行记录", runs));
        App.get("/api/crawl/runs?limit=20").then(function (rows) {
          runs.appendChild(App.table([
            { label: "开始", render: function (r) { return r.started_at.slice(5, 16); } },
            { label: "来源", key: "source_name" },
            { label: "结果", render: function (r) { return h("span", { class: "tag " + (r.status === "ok" ? "ok" : r.status === "running" ? "blue" : "red") }, r.status); } },
            { label: "列表/新增/详情", render: function (r) { return r.list_count + " / " + r.new_count + " / " + r.detail_count; } },
            { label: "请求数", key: "request_count", cls: "num" },
            { label: "日志", render: function (r) { return h("details", null, h("summary", { class: "small" }, App.short((r.message || "").split("\n")[0], 40)), h("div", { class: "pre small muted" }, r.message)); } }
          ], rows, { empty: "还没有运行记录" }));
        });
      });
    }
    function sourcePanel(s) {
      var st;
      if (!s.enabled) st = h("span", { class: "tag" }, "未启用");
      else if (s.paused_until && s.paused_until > App.nowStr()) st = h("span", { class: "tag red" }, "已暂停至 " + s.paused_until.slice(5, 16));
      else st = h("span", { class: "tag ok" }, "启用中");
      var tools = h("div", { class: "row" });
      if (App.can()) {
        tools.appendChild(h("button", { class: "btn sm", on: { click: function () { App.put("/api/sources/" + s.id, { enabled: s.enabled ? 0 : 1 }).then(load); } } }, s.enabled ? "停用" : "启用"));
        if (s.paused_until) tools.appendChild(h("button", { class: "btn sm", on: { click: function () { App.put("/api/sources/" + s.id, { resume: true }).then(load); } } }, "解除暂停"));
        tools.appendChild(h("button", { class: "btn sm", on: { click: function () { configDialog(s); } } }, "设置"));
      }
      if (App.can("manager", "sales")) tools.appendChild(h("button", { class: "btn sm primary", on: { click: function () {
        App.runJob("采集：" + s.name, App.post("/api/sources/" + s.id + "/run")).then(function (j) { App.toast((j.result && j.result.message) || "完成", "ok"); load(); });
      } } }, "立即运行"));
      return App.panel(s.name, h("div", null,
        h("div", { class: "row mb" }, st, h("span", { class: "tag line" }, s.type_name), h("span", { class: "small muted" }, "已入库 " + s.notice_count + " 条" + (s.last_run_at ? " · 上次 " + s.last_run_at.slice(5, 16) : ""))),
        h("p", { class: "small muted", style: { margin: "0 0 6px" } }, s.description || ""),
        s.last_message ? h("div", { class: "small pre", style: { color: s.last_status === "ok" ? "var(--ink-2)" : "var(--seal)" } }, s.last_message) : null), tools);
    }
    function configDialog(s) {
      var cfg = s.config || {};
      var body, collect;
      if (s.type === "ccgp_list") {
        var chs = meta.ccgp_channels;
        var boxes = Object.keys(chs).map(function (k) {
          var cb = h("input", { type: "checkbox", checked: (cfg.channels || []).indexOf(k) >= 0 });
          cb.dataset.k = k;
          return h("label", { class: "chk", style: { width: "48%" } }, cb, chs[k].label, chs[k].verified ? null : h("span", { class: "small muted" }, "（未实测）"));
        });
        var f = App.form([
          { key: "max_pages", label: "每个栏目最多翻页（每页 20 条，站点最多 25 页）", type: "number" },
          { key: "lookback_days", label: "只采集最近几天的公告", type: "number" },
          { key: "interval", label: "请求间隔（秒，建议 ≥ 3）", type: "number" },
          { key: "max_details", label: "单次最多抓取详情数", type: "number", default: 300 }
        ], cfg);
        body = h("div", null, h("h4", null, "采集栏目"), h("div", { class: "row" }, boxes), h("div", { class: "mt" }, f.el));
        collect = function () {
          var v = f.values();
          return { channels: boxes.map(function (b) { return b.querySelector("input"); }).filter(function (c) { return c.checked; }).map(function (c) { return c.dataset.k; }), max_pages: +v.max_pages || 25, lookback_days: +v.lookback_days || 3, interval: Math.max(2, +v.interval || 4), max_details: +v.max_details || 300 };
        };
      } else if (s.type === "ccgp_search") {
        var f2 = App.form([
          { key: "days", label: "搜索最近几天", type: "number" },
          { key: "pages", label: "每个关键词取几页（每页 20 条）", type: "number" },
          { key: "max_keywords", label: "每次最多搜索几个关键词", type: "number" },
          { key: "interval", label: "请求间隔（秒，最少 20，建议 40）", type: "number" }
        ], cfg);
        body = h("div", null, h("p", { class: "small muted" }, "关键词取自所有启用订阅的「包含关键词」。该站搜索限频很严，请保持低频。"), f2.el);
        collect = function () { var v = f2.values(); return { days: +v.days || 2, pages: +v.pages || 1, max_keywords: +v.max_keywords || 6, interval: Math.max(20, +v.interval || 40), bid_type: cfg.bid_type || "1" }; };
      } else if (s.type === "ggzy") {
        var f3 = App.form([
          { key: "classify", label: "业务类型", type: "select", options: [["02,01", "政府采购 + 工程建设"], ["02", "仅政府采购"], ["01", "仅工程建设"]] },
          { key: "max_pages", label: "每类最多翻页", type: "number" },
          { key: "interval", label: "请求间隔（秒，最少 15）", type: "number" },
          { key: "keyword", label: "关键词（可选）" }
        ], Object.assign({}, cfg, { classify: (cfg.classify || ["02", "01"]).join(",") }));
        body = h("div", null, h("p", { class: "small muted" }, "实验性来源：该平台限频严格，可能要求图形验证码；遇到会自动暂停。"), f3.el);
        collect = function () { var v = f3.values(); return { classify: v.classify.split(","), max_pages: +v.max_pages || 2, interval: Math.max(15, +v.interval || 30), keyword: v.keyword }; };
      } else {
        customDialog(s);
        return;
      }
      App.modal({ title: "来源设置：" + s.name, wide: true, body: body, buttons: [
        { text: "删除来源", danger: true, onClick: function () { return App.confirm("删除来源「" + s.name + "」？已采集的公告会保留。", "删除").then(function (ok) { if (ok) return App.del("/api/sources/" + s.id).then(load); return false; }); } },
        { text: "取消" },
        { text: "保存", primary: true, onClick: function () { return App.put("/api/sources/" + s.id, { config: collect() }).then(function () { App.toast("已保存", "ok"); load(); }); } }
      ] });
    }
    function customDialog(s) {
      var cfg = (s && s.config) || {};
      var f = App.form([
        { key: "name", label: "来源名称", full: true, placeholder: "如：云南省公共资源交易中心·政府采购" },
        { key: "list_url", label: "公告列表页地址", full: true, hint: "翻页时用 {page} 表示页码，如 https://example.gov.cn/list_{page}.html" },
        { key: "pages", label: "翻几页", type: "number", default: 1 },
        { key: "interval", label: "请求间隔（秒）", type: "number", default: 5 },
        { key: "item_selector", label: "列表项 CSS 选择器（可留空自动识别）", placeholder: "如 ul.news-list li" },
        { key: "link_pattern", label: "详情链接需匹配的正则（可留空）", placeholder: "如 /jyxx/.*\\.html" },
        { key: "region", label: "默认地区（列表页没有地区时使用）" }
      ], Object.assign({ name: s ? s.name : "" }, cfg));
      var out = h("div", { class: "mt" });
      App.modal({ title: s ? "编辑自定义来源" : "添加自定义来源", wide: true, body: h("div", null,
        h("p", { class: "small muted" }, "适合省市公共资源交易中心、高校/医院/国企的采购公告栏目。只填列表页地址也可以，系统会自动识别标题里含「公告/招标/采购/中标」的链接；先点「测试」看效果再保存。"),
        f.el, out),
        buttons: [
          { text: "测试", onClick: function () {
            var v = f.values();
            App.clear(out).appendChild(App.empty("正在抓取测试…"));
            return App.post("/api/sources/test", { config: v }).then(function (r) {
              App.clear(out);
              out.appendChild(h("p", null, "解析到 ", h("b", null, String(r.count)), " 条："));
              out.appendChild(App.table([{ label: "标题", key: "title" }, { label: "日期", key: "publish_time" }, { label: "类型", key: "notice_type" }], r.items));
              if (r.first_detail) out.appendChild(h("details", { class: "mt", open: true }, h("summary", null, "第一条详情解析效果"), h("dl", { class: "kv" }, Object.keys(r.first_detail.fields || {}).map(function (k) { return [h("dt", null, k), h("dd", null, App.short(String(r.first_detail.fields[k]), 120))]; })), h("div", { class: "pre small muted mt" }, r.first_detail.text_preview)));
              return false;
            }, function () { App.clear(out); return false; });
          } },
          { text: "取消" },
          { text: "保存", primary: true, onClick: function () {
            var v = f.values();
            var config = { list_url: v.list_url, pages: +v.pages || 1, interval: Math.max(2, +v.interval || 5), item_selector: v.item_selector, link_pattern: v.link_pattern, region: v.region };
            var pr = s ? App.put("/api/sources/" + s.id, { name: v.name, config: config }) : App.post("/api/sources", { name: v.name, type: "custom_list", config: config, description: "自定义列表页" });
            return pr.then(function () { App.toast("已保存", "ok"); load(); });
          } }
        ] });
    }
    load();
  };

  // ------------------------------------------------------------ 系统设置
  App.routes.settings = function (main, r) {
    if (!App.can()) { main.appendChild(App.empty("只有管理员可以修改系统设置")); return; }
    var tab = r.q.tab || "ai";
    var tabsBox = h("div");
    var content = h("div");
    main.appendChild(tabsBox);
    main.appendChild(content);
    var data = null;
    function show(t) {
      tab = t;
      history.replaceState(null, "", "#/settings?tab=" + t);
      App.clear(tabsBox).appendChild(App.tabs([{ key: "ai", label: "AI 模型" }, { key: "crawl", label: "采集与提醒" }, { key: "notify", label: "群机器人推送" }, { key: "users", label: "用户与角色" }, { key: "audit", label: "操作日志" }], t, show));
      App.clear(content);
      if (t === "ai") aiTab();
      if (t === "crawl") crawlTab();
      if (t === "notify") notifyTab();
      if (t === "users") usersTab();
      if (t === "audit") auditTab();
    }
    function reload(t) { App.get("/api/settings").then(function (d) { data = d; App.state.llm = d.llm_enabled; show(t || tab); }); }
    function save(values, t) { return App.put("/api/settings", values).then(function (d) { data = d; App.state.llm = d.llm_enabled; App.toast("已保存", "ok"); show(t || tab); }); }

    function aiTab() {
      var s = data.settings;
      var presets = data.presets;
      var preset = h("select", null, h("option", { value: "" }, "选择常用模型快速填写…"), Object.keys(presets).map(function (k) { return h("option", { value: k, selected: s.llm_preset === k }, presets[k].label); }));
      var f = App.form([
        { key: "llm_provider", label: "接口类型", type: "select", options: [["none", "不使用 AI"], ["openai", "OpenAI 兼容接口（DeepSeek/通义/豆包/智谱/Kimi/Ollama 等）"], ["anthropic", "Anthropic Claude"]] },
        { key: "llm_model", label: "模型名称" },
        { key: "llm_base_url", label: "接口地址（Base URL）", full: true },
        { key: "llm_api_key", label: "API Key", type: "password", full: true, hint: "保存后只显示首尾几位；本地 Ollama 可留空" },
        { key: "llm_temperature", label: "温度（0~1，越低越稳）", type: "number" },
        { key: "llm_timeout", label: "超时（秒）", type: "number" },
        { key: "llm_max_tokens", label: "单次最大输出 tokens", type: "number" },
        { key: "ai_auto_check_top_n", label: "每次采集后自动 AI 复核资格的条数（0 关闭）", type: "number" }
      ], s);
      preset.addEventListener("change", function () {
        var p = presets[preset.value];
        if (!p) return;
        f.inputs.llm_provider.value = p.provider;
        f.inputs.llm_base_url.value = p.base_url;
        f.inputs.llm_model.value = p.model;
      });
      var result = h("div", { class: "small mt" });
      content.appendChild(App.panel("AI 模型", h("div", null,
        h("div", { class: "req mb" }, "AI 用于：资格比对复核、标讯大白话解读、招标文件深度解析、技术方案起草与改写、合规体检逐条核对。调用时会把招标文件内容和企业资料摘要发送给所选模型服务商。标书属于商业机密，建议选用国内合规的模型服务，或用 Ollama 私有化部署，数据不出内网。"),
        h("div", { class: "row mb" }, preset, h("span", { class: "tag " + (data.llm_enabled ? "ok" : "") }, data.llm_enabled ? "已启用" : "未启用")),
        f.el, result,
        h("div", { class: "row end mt" },
          h("button", { class: "btn", on: { click: function (e) {
            var b = e.currentTarget; App.busy(b, true, "测试中");
            var v = f.values();
            App.post("/api/settings/llm_test", { provider: v.llm_provider, base_url: v.llm_base_url, model: v.llm_model, api_key: v.llm_api_key }, { quiet: true }).then(function (r) {
              App.busy(b, false); result.style.color = "var(--ok)"; result.textContent = "连接成功（" + r.seconds + " 秒）：" + r.reply;
            }, function (e2) { App.busy(b, false); result.style.color = "var(--seal)"; result.textContent = e2.message; });
          } } }, "测试连接"),
          h("button", { class: "btn primary", on: { click: function () { var v = f.values(); v.llm_preset = preset.value; save(v, "ai"); } } }, "保存")))));
    }

    function crawlTab() {
      var s = data.settings;
      var f = App.form([
        { key: "crawl_enabled", label: "启用定时采集", type: "checkbox", full: true },
        { key: "crawl_hours", label: "每天哪些整点采集（逗号分隔）", full: true, hint: "政府采购网地方公开招标栏目更新很快，建议工作时间每 1~2 小时一次" },
        { key: "crawl_detail_policy", label: "详情抓取策略", type: "select", options: [["matched", "只抓标题命中订阅的（推荐，请求少）"], ["all", "全部抓取（更全，请求多）"], ["none", "不抓详情"]] },
        { key: "crawl_lookback_days", label: "采集最近几天", type: "number" },
        { key: "crawl_keep_days", label: "未关注公告保留天数", type: "number" },
        { key: "crawl_respect_robots", label: "遵守 robots.txt（建议保持开启）", type: "checkbox" },
        { key: "crawl_user_agent", label: "采集 User-Agent", full: true },
        { key: "digest_time", label: "每日早报时间", placeholder: "08:30" },
        { key: "reminder_time", label: "每日提醒时间", placeholder: "09:00" },
        { key: "remind_days", label: "节点提前提醒天数（逗号分隔）" },
        { key: "expire_warn_days", label: "证书到期提前提醒天数", type: "number" },
        { key: "digest_top_n", label: "早报推荐条数", type: "number" },
        { key: "system_name", label: "系统名称", full: true }
      ], s);
      var nr = data.next_runs || {};
      content.appendChild(App.panel("采集与提醒", h("div", null, f.el,
        h("p", { class: "small muted mt" }, "下次运行：采集 " + (nr.crawl || "—") + "，早报 " + (nr.digest || "—") + "，提醒 " + (nr.reminders || "—")),
        h("div", { class: "row end" },
          h("button", { class: "btn", on: { click: function (e) {
            var b = e.currentTarget; App.busy(b, true, "生成中");
            App.post("/api/digest/run").then(function (r) { App.busy(b, false); App.modal({ title: "早报预览", body: h("div", { class: "pre" }, r.markdown + "\n\n已生成提醒 " + r.reminders + " 条") }); }, function () { App.busy(b, false); });
          } } }, "立即生成早报"),
          h("button", { class: "btn primary", on: { click: function () { save(f.values(), "crawl"); } } }, "保存")))));
    }

    function notifyTab() {
      var s = data.settings;
      var f = App.form([
        { key: "notify_wecom_webhook", label: "企业微信群机器人 Webhook", full: true, type: "password" },
        { key: "notify_dingtalk_webhook", label: "钉钉群机器人 Webhook", full: true, type: "password" },
        { key: "notify_dingtalk_secret", label: "钉钉加签密钥（可选）", full: true, type: "password" },
        { key: "notify_feishu_webhook", label: "飞书群机器人 Webhook", full: true, type: "password" },
        { key: "notify_feishu_secret", label: "飞书签名校验密钥（可选）", full: true, type: "password" }
      ], s);
      content.appendChild(App.panel("群机器人推送", h("div", null,
        h("p", { class: "small muted" }, "配置后，系统会推送：新匹配到的标讯、每日早报、截止节点提醒、证书/CA 到期提醒。在群设置里添加「自定义机器人」即可拿到 Webhook 地址。钉钉若设置了关键词安全校验，请把关键词设为「标讯」。"),
        f.el,
        h("div", { class: "row end mt" },
          h("button", { class: "btn", on: { click: function (e) {
            var b = e.currentTarget; App.busy(b, true, "发送中");
            App.post("/api/settings/notify_test").then(function (r) { App.busy(b, false); App.modal({ title: "推送测试结果", body: h("div", { class: "pre" }, r.results.join("\n")) }); }, function () { App.busy(b, false); });
          } } }, "发送测试消息"),
          h("button", { class: "btn primary", on: { click: function () { save(f.values(), "notify"); } } }, "保存")))));
    }

    function usersTab() {
      App.get("/api/users").then(function (users) {
        var roles = App.state.roles;
        content.appendChild(App.panel("用户与角色", h("div", null,
          h("p", { class: "small muted" }, "角色：管理员（全部）· 负责人（业务全部 + 审核）· 商务（找标、立项、订阅）· 标书编写 · 审核 · 财务（保证金）。"),
          App.table([
            { label: "用户名", key: "username" }, { label: "姓名", key: "display_name" }, { label: "角色", key: "role_name" }, { label: "电话", key: "phone" },
            { label: "状态", render: function (u) { return h("span", { class: "tag " + (u.active ? "ok" : "") }, u.active ? "正常" : "停用"); } },
            { label: "", render: function (u) { return h("button", { class: "btn sm", on: { click: function () { editUser(u); } } }, "编辑"); } }
          ], users),
          h("div", { class: "row end mt" }, h("button", { class: "btn primary", on: { click: function () { editUser(null); } } }, "新增用户")))));
        function editUser(u) {
          var fields = [
            { key: "username", label: "用户名（登录用）" },
            { key: "display_name", label: "姓名" },
            { key: "role", label: "角色", type: "select", options: Object.keys(roles).map(function (k) { return [k, roles[k]]; }) },
            { key: "phone", label: "电话" },
            { key: "password", label: u ? "重置密码（不改留空）" : "初始密码（至少 8 位，含字母和数字）", type: "password" }
          ];
          if (u) fields.push({ key: "active", label: "账号启用", type: "checkbox" });
          var f = App.form(fields, u || { role: "sales" });
          if (u) f.inputs.username.disabled = true;
          App.modal({ title: u ? "编辑用户" : "新增用户", body: f.el, buttons: [{ text: "取消" }, { text: "保存", primary: true, onClick: function () {
            var v = f.values();
            if (u && !v.password) delete v.password;
            var pr = u ? App.put("/api/users/" + u.id, v) : App.post("/api/users", v);
            return pr.then(function () { App.toast("已保存", "ok"); show("users"); });
          } }] });
        }
      });
    }

    function auditTab() {
      App.get("/api/audit?limit=300").then(function (rows) {
        content.appendChild(App.panel("操作日志（最近 300 条）", App.table([
          { label: "时间", key: "created_at" }, { label: "用户", key: "username" }, { label: "操作", key: "action" }, { label: "对象", key: "target" }, { label: "说明", render: function (r) { return h("span", { class: "small muted" }, App.short(r.detail, 80)); } }
        ], rows)));
      });
    }
    reload(tab);
  };
})();
