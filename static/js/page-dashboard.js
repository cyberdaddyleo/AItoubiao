/* 工作台 & 数据看板 */
(function () {
  "use strict";
  var h = App.h;

  App.routes.dashboard = function (main) {
    var box = h("div", null, App.empty("加载中…"));
    main.appendChild(box);
    App.get("/api/stats/dashboard").then(function (d) {
      App.clear(box);
      // 上手引导：没配完就显示
      var setup = [
        { done: d.setup.companies > 0, t: "添加投标主体", s: "公司名称、法人、地址等", hash: "#/companies" },
        { done: d.setup.subscriptions > 0, t: "设置订阅关键词", s: "决定每天推什么标", hash: "#/companies" },
        { done: d.setup.library > 0, t: "整理资料库", s: "资质、业绩、人员、方案", hash: "#/library" },
        { done: d.llm_enabled, t: "配置 AI 模型", s: "资格复核、解析、写标", hash: "#/settings" },
        { done: d.setup.notify, t: "接入群机器人", s: "企业微信/钉钉/飞书推送", hash: "#/settings" }
      ];
      if (setup.some(function (x) { return !x.done; })) {
        box.appendChild(App.panel("开始使用（还剩 " + setup.filter(function (x) { return !x.done; }).length + " 步）",
          h("div", { class: "steps" }, setup.map(function (s) {
            return h("a", { class: "step" + (s.done ? " done" : ""), href: s.hash, style: { textDecoration: "none", color: "inherit" } }, h("b", null, (s.done ? "✓ " : "") + s.t), h("span", null, s.s));
          }))));
        box.lastChild.style.marginBottom = "16px";
      }
      box.appendChild(h("div", { class: "kpis" },
        kpi(d.matches_new, "待处理的匹配标讯", "未截止、尚未处理", "#/notices"),
        kpi(d.notices_today, "今日新采集公告", "近 7 天 " + d.notices_7d + " 条", "#/notices?all=1"),
        kpi(d.projects_active, "准备中的投标", "已投递待开标 " + d.projects_submitted + " 个", "#/projects"),
        kpi(App.fmtAmount(d.bond_outstanding) || "0", "在途保证金", "已缴纳未退还", "#/projects")));

      var left = h("div");
      left.appendChild(App.panel("今日推荐", d.top_matches.length ? h("div", { class: "nlist" }, d.top_matches.map(function (m) {
        return h("div", { class: "nitem", on: { click: function () { location.hash = "#/notices/" + m.id; } } },
          App.seal(m.deadline),
          h("div", null,
            h("div", { class: "title" }, m.title),
            h("div", { class: "meta" }, h("span", null, m.short_name || m.company_name), m.region ? h("span", null, m.region) : null, m.budget ? h("span", null, "预算 " + App.fmtAmount(m.budget)) : null)),
          h("div", { class: "side-r" }, h("div", { class: "score" }, Math.round(m.score), h("small", null, " 分")), App.level(m.level)));
      })) : App.empty("还没有推荐", "设置订阅关键词后，系统每天按时采集并推荐", h("a", { class: "btn", href: "#/companies" }, "去设置订阅")), h("a", { href: "#/notices" }, "全部标讯")));

      var right = h("div");
      right.appendChild(App.panel("近期节点", d.upcoming.length ? App.table([
        { label: "时间", render: function (u) { return h("span", { class: "nowrap" }, u.when.slice(5, 16)); } },
        { label: "事项", key: "what" },
        { label: "项目", render: function (u) { return h("a", { href: "#/projects/" + u.project_id }, App.short(u.project, 22)); } }
      ], d.upcoming) : App.empty("10 天内没有节点")));
      right.appendChild(App.panel("即将到期的证书", d.expiring.length ? App.table([
        { label: "证书", key: "name" },
        { label: "主体", key: "company_name" },
        { label: "有效期至", render: function (r) { var past = r.valid_to < new Date().toISOString().slice(0, 10); return h("span", { class: "tag " + (past ? "red" : "amber") }, r.valid_to); } }
      ], d.expiring) : App.empty("60 天内没有到期的证书")));
      right.appendChild(App.panel("采集状态", App.table([
        { label: "来源", key: "name" },
        { label: "状态", render: function (s) {
          if (!s.enabled) return h("span", { class: "tag" }, "未启用");
          if (s.paused_until && s.paused_until > nowStr()) return h("span", { class: "tag red", title: s.last_message }, "暂停至 " + s.paused_until.slice(5, 16));
          if (s.last_status === "ok") return h("span", { class: "tag ok" }, "正常");
          if (!s.last_status) return h("span", { class: "tag" }, "等待首次运行");
          return h("span", { class: "tag amber", title: s.last_message }, s.last_status);
        } },
        { label: "上次运行", render: function (s) { return s.last_run_at ? s.last_run_at.slice(5, 16) : "—"; } }
      ], d.sources), App.can("manager", "sales") ? h("a", { href: "#/sources" }, "管理") : null));
      box.appendChild(h("div", { class: "grid2" }, left, right));
    });
  };

  function nowStr() {
    var d = new Date();
    function p(n) { return n < 10 ? "0" + n : "" + n; }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  App.nowStr = nowStr;

  function kpi(v, k, s, hash) {
    return h("a", { class: "kpi", href: hash, style: { textDecoration: "none", color: "inherit" } }, h("div", { class: "v" }, String(v)), h("div", { class: "k" }, k), h("div", { class: "s" }, s));
  }

  // ------------------------------------------------------------ 数据看板
  App.routes.stats = function (main) {
    var cid = 0;
    var body = h("div");
    var bar = h("div", { class: "row mb" }, "主体：", App.companySelect(0, function (v) { cid = v; load(); }, true));
    main.appendChild(bar);
    main.appendChild(body);
    function load() {
      App.clear(body).appendChild(App.empty("加载中…"));
      App.get("/api/stats/overview" + App.qs({ months: 6, company_id: cid })).then(function (d) {
        App.clear(body);
        var total = d.by_status.reduce(function (a, b) { return a + b.n; }, 0);
        body.appendChild(h("div", { class: "kpis" },
          kpi(total, "近 6 个月立项", "", "#/projects"),
          kpi(d.decided ? d.won + " / " + d.decided : "—", "中标 / 已出结果", "", "#/projects"),
          kpi(d.win_rate === null ? "—" : Math.round(d.win_rate * 100) + "%", "中标率", "按已出结果的项目计算", "#/projects"),
          kpi(d.ai_usage_30d ? d.ai_usage_30d.calls : 0, "近 30 天 AI 调用次数", "输入 " + fmtTok(d.ai_usage_30d.pt) + "，输出 " + fmtTok(d.ai_usage_30d.ct) + " tokens", "#/settings")));
        body.appendChild(App.panel("每月漏斗：采集 → 匹配 → 立项 → 投递 → 中标", chart(d.series)));
        var sm = {};
        d.by_status.forEach(function (s) { sm[s.status] = s.n; });
        body.appendChild(h("div", { class: "grid2e mt" },
          App.panel("按地区", App.table([
            { label: "地区", key: "region" }, { label: "立项", key: "n", cls: "num" }, { label: "中标", key: "won", cls: "num" },
            { label: "中标率", cls: "num", render: function (r) { return r.n ? Math.round(r.won / r.n * 100) + "%" : "—"; } }
          ], d.by_region, { empty: "暂无数据" })),
          App.panel("常见竞争对手（我方未中标时的中标人）", App.table([
            { label: "中标人", key: "winner" }, { label: "次数", key: "n", cls: "num" }
          ], d.competitors, { empty: "暂无数据", emptyHint: "登记未中标结果或系统自动关联中标公告后会出现" }))));
      });
    }
    load();
  };

  function fmtTok(n) { n = +n || 0; return n >= 10000 ? (n / 10000).toFixed(1) + "万" : String(n); }

  // 纯 SVG 分组柱状图（不依赖外部库，内网可用）
  function chart(series) {
    if (!series.length) return App.empty("暂无数据");
    var keys = [["notices", "采集", "#c6d3e3"], ["matches", "匹配", "#8fa9c8"], ["projects", "立项", "#2c5282"], ["submitted", "投递", "#a8650d"], ["won", "中标", "#2e7d4f"]];
    var W = 900, H = 260, pad = 36, gw = (W - pad * 2) / series.length;
    var bw = Math.min(16, (gw - 12) / keys.length);
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + (H + 30));
    function el(t, a, txt) { var e = document.createElementNS(NS, t); Object.keys(a).forEach(function (k) { e.setAttribute(k, a[k]); }); if (txt !== undefined) e.textContent = txt; svg.appendChild(e); return e; }
    // 采集量远大于其他，用对数刻度避免小数值看不见
    var max = 1;
    series.forEach(function (s) { keys.forEach(function (k) { max = Math.max(max, +s[k[0]] || 0); }); });
    var lmax = Math.log10(max + 1);
    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var y = H - f * (H - 20);
      el("line", { x1: pad, x2: W - pad, y1: y, y2: y, stroke: "#eef1f5" });
      el("text", { x: pad - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: "#7b8a99" }, f ? String(Math.round(Math.pow(10, f * lmax) - 1)) : "0");
    });
    series.forEach(function (s, i) {
      var x0 = pad + i * gw + (gw - bw * keys.length) / 2;
      keys.forEach(function (k, j) {
        var v = +s[k[0]] || 0;
        var bh = v ? (Math.log10(v + 1) / lmax) * (H - 20) : 0;
        var r = el("rect", { x: x0 + j * bw, y: H - bh, width: bw - 2, height: bh, fill: k[2], rx: 1 });
        var tt = document.createElementNS(NS, "title"); tt.textContent = s.month + " " + k[1] + "：" + v; r.appendChild(tt);
      });
      el("text", { x: pad + i * gw + gw / 2, y: H + 18, "text-anchor": "middle", "font-size": 11, fill: "#4a5a6b" }, s.month);
    });
    var legend = h("div", { class: "legend mt" }, keys.map(function (k) { return h("span", null, h("i", { style: { background: k[2] } }), k[1]); }), h("span", { class: "muted" }, "（纵轴为对数刻度；鼠标悬停看具体数字）"));
    var tbl = App.table([{ label: "月份", key: "month" }].concat(keys.map(function (k) { return { label: k[1], key: k[0], cls: "num" }; })).concat([{ label: "中标金额", cls: "num", render: function (r) { return App.fmtAmount(r.won_amount) || "—"; } }]), series);
    return h("div", { class: "chart" }, svg, legend, h("details", { class: "mt" }, h("summary", null, "查看数据表"), tbl));
  }
})();
