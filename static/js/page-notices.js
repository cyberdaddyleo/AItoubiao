/* 标讯中心：筛选列表 + 详情抽屉 + 处理动作 + 手动导入 */
(function () {
  "use strict";
  var h = App.h;
  var TYPES = [["", "全部类型"], ["tender", "招标/采购"], ["change", "更正/澄清"], ["award", "中标/成交"], ["fail", "废标/终止"], ["intention", "采购意向"], ["other", "其他"]];
  var filters = { company_id: 0, only_matched: 1, level: "", status: "", notice_type: "tender", region: "", q: "", hide_expired: 1, sort: "score", page: 1 };

  App.routes.notices = function (main, r) {
    if (r.q.all) { filters.only_matched = 0; filters.sort = "publish"; }
    var listBox = h("div", { class: "nlist" });
    var pager = h("div", { class: "row", style: { padding: "10px 16px" } });
    var totalEl = h("span", { class: "muted small" });

    var q = h("input", { type: "text", placeholder: "搜索标题 / 采购人 / 项目编号", value: filters.q });
    var region = h("input", { type: "text", placeholder: "地区", value: filters.region });
    var level = sel([["", "全部结论"], ["能投", "能投"], ["差一项", "差一项"], ["待评估", "待评估"], ["不能投", "不能投"]], filters.level);
    var status = sel([["", "未忽略的"], ["new", "未处理"], ["fav", "已收藏"], ["project", "已立项"], ["ignored", "已忽略"]], filters.status);
    var type = sel(TYPES, filters.notice_type);
    var scope = sel([["1", "只看匹配的"], ["0", "全部公告"]], String(filters.only_matched));
    var sort = sel([["score", "按匹配度"], ["publish", "按发布时间"], ["deadline", "按截止时间"], ["budget", "按预算"]], filters.sort);
    var exp = h("input", { type: "checkbox", checked: !!filters.hide_expired });
    var go = h("button", { class: "btn primary" }, "筛选");

    var bar = h("div", { class: "filters" }, q, region, level, status, type, scope, go);
    var bar2 = h("div", { class: "row", style: { padding: "0 16px 12px", borderBottom: "1px solid var(--line-2)" } },
      "主体", App.companySelect(filters.company_id, function (v) { filters.company_id = v; filters.page = 1; load(); }, true),
      "排序", sort,
      h("label", { class: "chk" }, exp, "隐藏已截止"),
      h("span", { class: "spacer" }), totalEl,
      h("button", { class: "btn", on: { click: importDialog } }, "导入公告"),
      App.can("manager", "sales") ? h("button", { class: "btn", on: { click: runCrawl } }, "立即采集") : null);
    [sort, level, status, type, scope].forEach(function (s) { s.addEventListener("change", apply); });
    exp.addEventListener("change", apply);
    go.addEventListener("click", apply);
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") apply(); });
    region.addEventListener("keydown", function (e) { if (e.key === "Enter") apply(); });

    function apply() {
      filters.q = q.value.trim(); filters.region = region.value.trim(); filters.level = level.value; filters.status = status.value;
      filters.notice_type = type.value; filters.only_matched = +scope.value; filters.sort = sort.value; filters.hide_expired = exp.checked ? 1 : 0; filters.page = 1;
      load();
    }

    main.appendChild(h("div", { class: "panel" }, bar, bar2, listBox, pager));

    function load() {
      App.clear(listBox).appendChild(App.empty("加载中…"));
      App.get("/api/notices" + App.qs(filters)).then(function (d) {
        App.clear(listBox);
        totalEl.textContent = "共 " + d.total + " 条";
        if (!d.items.length) {
          listBox.appendChild(filters.only_matched
            ? App.empty("没有匹配的标讯", "可能还没到采集时间，或订阅关键词太窄。可以切换到「全部公告」看看今天采到了什么。", h("a", { class: "btn", href: "#/companies" }, "调整订阅"))
            : App.empty("还没有采集到公告", "到「采集来源」点一次「立即采集」，或用「导入公告」粘贴链接"));
        }
        d.items.forEach(function (n) { listBox.appendChild(row(n, load)); });
        App.clear(pager);
        var pages = Math.ceil(d.total / d.size);
        if (pages > 1) {
          pager.appendChild(h("button", { class: "btn sm", disabled: filters.page <= 1, on: { click: function () { filters.page--; load(); } } }, "上一页"));
          pager.appendChild(h("span", { class: "muted small" }, "第 " + filters.page + " / " + pages + " 页"));
          pager.appendChild(h("button", { class: "btn sm", disabled: filters.page >= pages, on: { click: function () { filters.page++; load(); } } }, "下一页"));
        }
      });
    }
    load();
    if (r.id) openNotice(r.id, load);
  };

  function sel(opts, val) {
    return h("select", null, opts.map(function (o) { return h("option", { value: o[0], selected: String(val) === String(o[0]) }, o[1]); }));
  }

  function row(n, reload) {
    var m = n.match;
    var meta = h("div", { class: "meta" },
      h("span", { class: "tag line" }, n.type_label || n.type_name),
      n.region ? h("span", null, n.region) : null,
      n.purchaser ? h("span", null, n.purchaser) : null,
      n.budget ? h("span", null, "预算 " + App.fmtAmount(n.budget)) : null,
      h("span", null, "发布 " + (n.publish_time || "").slice(0, 16)),
      h("span", null, n.source_name));
    var why = m && m.reasons && m.reasons.length ? h("div", { class: "why" }, App.short(m.reasons.slice(0, 2).join("；"), 110)) : null;
    var miss = m && m.missing && m.missing.length ? h("div", { class: "why", style: { color: "var(--seal)" } }, "缺：" + App.short(m.missing.join("、"), 90)) : null;
    var right = h("div", { class: "side-r" });
    if (m) {
      right.appendChild(h("div", { class: "score" }, String(Math.round(m.score)), h("small", null, " 分")));
      right.appendChild(App.level(m.level, m.level_by));
      right.appendChild(h("span", { class: "small muted" }, m.company_name + (m.status === "fav" ? " · 已收藏" : m.status === "project" ? " · 已立项" : m.status === "ignored" ? " · 已忽略" : "")));
    }
    return h("div", { class: "nitem" + (m && m.status !== "new" ? " read" : ""), on: { click: function () { history.replaceState(null, "", "#/notices/" + n.id); openNotice(n.id, reload); } } },
      App.seal(n.notice_type === "tender" ? n.deadline : ""),
      h("div", null, h("div", { class: "title" }, n.title), meta, why, miss),
      right);
  }

  // ------------------------------------------------------------ 详情
  function openNotice(id, reload) {
    var dr = App.drawer({ title: "加载中…", onClose: function () { history.replaceState(null, "", "#/notices"); if (reload) reload(); } });
    function render() {
      App.get("/api/notices/" + id).then(function (n) {
        App.clear(dr.head.querySelector("h2") || dr.head).textContent = n.title;
        var body = App.clear(dr.body);
        var foot = App.clear(dr.foot);
        var tab = "info";
        var tabsBox = h("div");
        var content = h("div");
        body.appendChild(h("div", { class: "row mb" }, App.seal(n.notice_type === "tender" ? n.deadline : ""),
          h("div", null,
            h("div", null, h("span", { class: "tag blue" }, n.type_label || n.type_name), " ", h("a", { href: n.url.indexOf("manual://") === 0 ? "#" : n.url, target: "_blank", rel: "noopener" }, n.url.indexOf("manual://") === 0 ? "人工导入" : "打开原公告")),
            h("div", { class: "small muted" }, n.source_name + " · 发布 " + (n.publish_time || "未知") + (n.detail_fetched ? "" : " · 尚未抓取详情")))));
        body.appendChild(tabsBox);
        body.appendChild(content);
        function show(t) {
          tab = t;
          App.clear(tabsBox).appendChild(App.tabs([
            { key: "info", label: "概要与匹配" }, { key: "ai", label: "AI 解读" }, { key: "text", label: "公告正文" }, { key: "history", label: "甲方历史（" + n.purchaser_history.length + "）" }
          ], tab, show));
          App.clear(content);
          if (t === "info") content.appendChild(infoTab(n, render));
          if (t === "ai") content.appendChild(aiTab(n));
          if (t === "text") content.appendChild(h("div", { class: "pre", style: { fontSize: "13px" } }, n.content_text || "（还没有正文，点下方「抓取详情」）"));
          if (t === "history") content.appendChild(historyTab(n));
        }
        show(tab);

        var m = n.matches.filter(function (x) { return !App.state.cid || x.company_id === App.state.cid; })[0] || n.matches[0];
        if (n.projects.length) {
          n.projects.forEach(function (p) { foot.appendChild(h("a", { class: "btn primary", href: "#/projects/" + p.id }, "打开项目：" + App.companyName(p.company_id))); });
        }
        if (App.can("manager", "sales", "writer")) foot.appendChild(h("button", { class: "btn" + (n.projects.length ? "" : " primary"), on: { click: function () { createProject(n, m); } } }, "立项准备投标"));
        if (m) {
          foot.appendChild(h("button", { class: "btn", on: { click: function () { setStatus(m.id, m.status === "fav" ? "new" : "fav").then(render); } } }, m.status === "fav" ? "取消收藏" : "收藏"));
          foot.appendChild(h("button", { class: "btn", on: { click: function () { setStatus(m.id, m.status === "ignored" ? "new" : "ignored").then(function () { dr.close(); }); } } }, m.status === "ignored" ? "取消忽略" : "不感兴趣"));
          foot.appendChild(h("button", { class: "btn", on: { click: function () { assign(m); } } }, "指派给同事"));
        }
        foot.appendChild(h("span", { class: "spacer" }));
        if (n.url.indexOf("manual://") !== 0) foot.appendChild(h("button", { class: "btn", on: { click: function (e) {
          var b = e.currentTarget; App.busy(b, true, "抓取中");
          App.post("/api/notices/" + n.id + "/fetch_detail").then(function () { App.toast("已更新", "ok"); render(); }, function () { App.busy(b, false); });
        } } }, n.detail_fetched ? "重新抓取" : "抓取详情"));
      });
    }
    render();
  }

  function infoTab(n, rerender) {
    var box = h("div");
    var kv = [
      ["项目编号", n.project_no], ["采购人", n.purchaser], ["代理机构", n.agency], ["地区", n.region], ["品目", n.category],
      ["预算/限价", App.fmtAmount(n.budget)], ["获取文件截止", n.doc_get_end], ["投标截止", n.deadline], ["开标时间", n.open_time],
      ["保证金", n.bond_text], ["联合体", n.joint_venture], ["中小企业", n.sme_only ? "专门面向中小企业" : ""]
    ].filter(function (x) { return x[1]; });
    box.appendChild(h("dl", { class: "kv mb" }, kv.map(function (x) { return [h("dt", null, x[0]), h("dd", null, x[1])]; })));
    if (n.winners && n.winners.length) {
      box.appendChild(h("h4", null, "中标/成交人"));
      box.appendChild(App.table([{ label: "供应商", key: "name" }, { label: "金额", render: function (w) { return w.amount ? App.fmtAmount(w.amount) : (w.amount_text || ""); } }, { label: "得分", key: "score" }], n.winners));
    }
    if (n.qualification_text) {
      box.appendChild(h("h4", null, "资格要求（公告原文）"));
      box.appendChild(h("div", { class: "req" }, n.qualification_text.replace(/\n{2,}/g, "\n")));
    }
    box.appendChild(h("h4", null, "与各投标主体的匹配"));
    if (!n.matches.length) box.appendChild(App.empty("没有命中任何主体的订阅", "可以直接「立项准备投标」，或调整订阅关键词"));
    n.matches.forEach(function (m) {
      var aiBtn = h("button", { class: "btn sm", on: { click: function (e) {
        var b = e.currentTarget; App.busy(b, true, "AI 复核中");
        App.post("/api/matches/" + m.id + "/ai_check").then(function () { App.toast("AI 复核完成", "ok"); rerender(); }, function () { App.busy(b, false); });
      } } }, "AI 复核资格");
      box.appendChild(h("div", { class: "panel mt" },
        h("div", { class: "hd" }, h("h3", null, m.company_short || m.company_name), App.level(m.level, m.level_by), h("span", { class: "muted small" }, Math.round(m.score) + " 分"), h("span", { class: "spacer" }), aiBtn),
        h("div", { class: "bd" },
          m.reasons.length ? h("ul", { style: { margin: 0, paddingLeft: "18px" } }, m.reasons.map(function (r) { return h("li", null, r); })) : null,
          m.missing.length ? h("div", { class: "mt", style: { color: "var(--seal)" } }, "缺少/不满足：" + m.missing.join("；")) : null)));
    });
    if (n.duplicates && n.duplicates.length) {
      box.appendChild(h("h4", null, "同一项目的其他转载"));
      box.appendChild(h("ul", null, n.duplicates.map(function (d) { return h("li", null, h("a", { href: d.url, target: "_blank", rel: "noopener" }, d.title), " ", h("span", { class: "muted small" }, d.source_name)); })));
    }
    if (n.attachments && n.attachments.length) {
      box.appendChild(h("h4", null, "公告附件"));
      box.appendChild(h("ul", null, n.attachments.map(function (a) { return h("li", null, h("a", { href: a.url, target: "_blank", rel: "noopener" }, a.name)); })));
      box.appendChild(h("div", { class: "small muted" }, "立项后可在项目里一键下载附件并自动解析招标文件"));
    }
    return box;
  }

  function aiTab(n) {
    var box = h("div");
    function show(s) {
      App.clear(box);
      if (!s) {
        box.appendChild(App.empty("还没有 AI 解读", "用大白话说清楚：买什么、要什么资质、怎么评分、有什么坑、值不值得投",
          h("button", { class: "btn primary", on: { click: function (e) {
            var b = e.currentTarget; App.busy(b, true, "AI 解读中");
            App.post("/api/notices/" + n.id + "/ai_summary").then(function (d) { n.ai_summary = d; show(d); }, function () { App.busy(b, false); });
          } } }, "生成 AI 解读")));
        return;
      }
      box.appendChild(h("div", { class: "req mb", style: { fontSize: "14px", color: "var(--ink)" } }, s.one_line || ""));
      function list(title, arr) { if (arr && arr.length) { box.appendChild(h("h4", null, title)); box.appendChild(h("ul", null, arr.map(function (x) { return h("li", null, String(x)); }))); } }
      list("资格要求", s.requirements);
      if (s.scoring) { box.appendChild(h("h4", null, "评分方式")); box.appendChild(h("p", null, s.scoring)); }
      list("关键时间", s.key_dates);
      list("注意事项", s.risks);
      if (s.suggestion) { box.appendChild(h("h4", null, "建议")); box.appendChild(h("p", null, s.suggestion)); }
      box.appendChild(h("p", { class: "small muted" }, "AI 解读基于公告原文，仅供参考，以招标文件为准。"));
    }
    show(n.ai_summary);
    return box;
  }

  function historyTab(n) {
    if (!n.purchaser_history.length) return App.empty("还没有这个采购人的中标记录", "系统采集到该采购人的中标公告后会自动出现在这里");
    return App.table([
      { label: "公告", render: function (x) { return h("a", { href: "#/notices/" + x.id }, x.title); } },
      { label: "中标人", render: function (x) { return (x.winners || []).map(function (w) { return w.name; }).join("、") || "—"; } },
      { label: "金额", render: function (x) { return App.fmtAmount(x.award_amount || ((x.winners || [])[0] || {}).amount) || "—"; } },
      { label: "时间", render: function (x) { return (x.publish_time || "").slice(0, 10); } }
    ], n.purchaser_history);
  }

  function setStatus(mid, st) { return App.post("/api/matches/" + mid + "/status", { status: st }).then(function () { App.toast("已更新", "ok"); App.refreshBell(); }); }

  function assign(m) {
    App.get("/api/users").then(function (users) {
      var f = App.form([{ key: "uid", label: "指派给", type: "select", options: users.filter(function (u) { return u.active; }).map(function (u) { return [u.id, u.display_name + "（" + u.role_name + "）"]; }) }]);
      App.modal({ title: "指派标讯", body: f.el, buttons: [{ text: "取消" }, { text: "指派", primary: true, onClick: function () {
        return App.post("/api/matches/" + m.id + "/status", { assigned_to: +f.values().uid, status: "fav" }).then(function () { App.toast("已指派并收藏", "ok"); });
      } }] });
    });
  }

  function createProject(n, m) {
    if (!App.state.companies.length) { App.toast("请先在「主体与订阅」里添加投标主体", "err"); return; }
    var def = m ? m.company_id : (App.state.cid || App.state.companies[0].id);
    var f = App.form([{ key: "company_id", label: "投标主体", type: "select", options: App.state.companies.map(function (c) { return [c.id, c.name]; }) }], { company_id: def });
    var others = n.projects.filter(function (p) { return true; });
    App.modal({ title: "立项准备投标", body: h("div", null, f.el,
      others.length ? h("p", { class: "mt", style: { color: "var(--seal)" } }, "注意：本系统里已经有主体在投这个项目。存在控股、管理关系或负责人为同一人的单位不得参加同一标段投标。") : null,
      h("p", { class: "small muted mt" }, "立项后会带入公告里的项目编号、采购人、预算、截止时间等信息，可以继续上传招标文件、编写标书。")),
      buttons: [{ text: "取消" }, { text: "立项", primary: true, onClick: function () {
        return App.post("/api/projects", { company_id: +f.values().company_id, notice_id: n.id }).then(function (p) {
          document.querySelectorAll(".drawer,.mask").forEach(function (x) { x.remove(); });
          location.hash = "#/projects/" + p.id;
        });
      } }] });
  }
  App.createProjectFromNotice = createProject;

  function importDialog() {
    var url = h("input", { type: "url", placeholder: "https://…（公告详情页链接）" });
    var title = h("input", { type: "text", placeholder: "公告标题（粘贴正文时填写）" });
    var text = h("textarea", { rows: 10, placeholder: "或者把公告正文整段粘贴进来（适合需要登录才能看的平台）" });
    App.modal({ title: "导入公告", body: h("div", null,
      h("label", { class: "fld mb" }, h("span", null, "方式一：公告链接（系统自动抓取并解析）"), url),
      h("label", { class: "fld mb" }, h("span", null, "方式二：粘贴公告正文"), title, h("div", { style: { height: "6px" } }), text),
      h("p", { class: "small muted" }, "导入后会自动提取项目编号、预算、截止时间、资格要求等，并按订阅匹配各主体。")),
      buttons: [{ text: "取消" }, { text: "导入", primary: true, onClick: function () {
        var body = text.value.trim() ? { title: title.value, text: text.value, url: url.value } : { url: url.value.trim() };
        return App.post("/api/notices/import", body).then(function (d) { App.toast("已导入", "ok"); location.hash = "#/notices/" + d.id; App.route(); });
      } }] });
  }

  function runCrawl() {
    App.runJob("采集全部来源", App.post("/api/crawl/run_all")).then(function (j) {
      var res = (j && j.result) || [];
      App.toast(res.map(function (r) { return (r.source || "") + "：" + (r.message || r.skipped || r.status); }).join("；") || "采集完成", "ok");
      App.route();
    });
  }
  App.runCrawl = runCrawl;
})();
