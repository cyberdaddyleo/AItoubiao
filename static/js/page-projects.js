/* 投标项目：看板 + 详情（概览 / 招标文件 / 标书编写 / 合规体检 / 审核投递） */
(function () {
  "use strict";
  var h = App.h;
  var COLS = [["evaluating", "待评估"], ["preparing", "准备中"], ["reviewing", "审核中"], ["ready", "待投递"], ["submitted", "已投递"], ["opened", "已开标"]];
  var DONE = ["won", "lost", "abandoned"];

  App.routes.projects = function (main, r) {
    if (r.id) { projectDetail(main, r.id, r.q.tab || "overview"); return; }
    var cid = 0;
    var body = h("div");
    var q = h("input", { type: "text", placeholder: "搜索项目名称/编号/采购人", style: { width: "260px" } });
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") load(); });
    main.appendChild(h("div", { class: "row mb" }, "主体", App.companySelect(0, function (v) { cid = v; load(); }, true), q,
      h("span", { class: "spacer" }),
      App.can("manager", "sales", "writer") ? h("button", { class: "btn primary", on: { click: manualProject } }, "手动新建项目") : null));
    main.appendChild(body);
    function load() {
      App.clear(body).appendChild(App.empty("加载中…"));
      App.get("/api/projects" + App.qs({ company_id: cid, q: q.value.trim() })).then(function (d) {
        App.clear(body);
        if (!d.items.length) { body.appendChild(App.empty("还没有投标项目", "在标讯中心打开一条标讯，点「立项准备投标」；或者手动新建")); return; }
        var kb = h("div", { class: "kanban" });
        COLS.forEach(function (c) {
          var items = d.items.filter(function (p) { return p.status === c[0]; });
          kb.appendChild(h("div", { class: "kcol" }, h("h4", null, c[1], h("span", null, String(items.length))), items.map(card)));
        });
        body.appendChild(kb);
        var done = d.items.filter(function (p) { return DONE.indexOf(p.status) >= 0; });
        if (done.length) {
          body.appendChild(App.panel("已结束（" + done.length + "）", App.table([
            { label: "项目", render: function (p) { return h("a", { href: "#/projects/" + p.id }, p.name); } },
            { label: "主体", key: "company_name" },
            { label: "结果", render: function (p) { return h("span", { class: "tag " + (p.status === "won" ? "ok" : p.status === "lost" ? "red" : "") }, p.status_name); } },
            { label: "中标人", key: "winner" },
            { label: "金额", render: function (p) { return App.fmtAmount(p.result_amount || p.bid_amount); } },
            { label: "开标", render: function (p) { return (p.open_time || "").slice(0, 10); } }
          ], done))).style.marginTop = "16px";
        }
      });
    }
    load();
  };

  function card(p) {
    var doc = p.doc_status ? { draft: "标书编写中", reviewing: "标书审核中", approved: "标书已定稿" }[p.doc_status] : "未建标书";
    var deadline = ["submitted", "opened"].indexOf(p.status) >= 0 ? p.open_time : p.deadline;
    return h("div", { class: "pcard", on: { click: function () { location.hash = "#/projects/" + p.id; } } },
      h("div", null, h("div", { class: "n" }, p.name), h("div", { class: "m" }, p.company_name + " · " + doc), p.owner_name ? h("div", { class: "m" }, "负责人 " + p.owner_name) : null),
      App.seal(deadline, true));
  }

  function manualProject() {
    if (!App.state.companies.length) { App.toast("请先添加投标主体", "err"); return; }
    var f = App.form([
      { key: "company_id", label: "投标主体", type: "select", options: App.state.companies.map(function (c) { return [c.id, c.name]; }) },
      { key: "name", label: "项目名称" },
      { key: "project_no", label: "项目编号" },
      { key: "purchaser", label: "采购人/招标人" },
      { key: "budget", label: "预算/最高限价（元）", type: "number" },
      { key: "deadline", label: "投标截止", type: "datetime-local" }
    ]);
    App.modal({ title: "手动新建项目", body: f.el, buttons: [{ text: "取消" }, { text: "创建", primary: true, onClick: function () {
      var v = f.values(); v.deadline = (v.deadline || "").replace("T", " ");
      return App.post("/api/projects", v).then(function (p) { location.hash = "#/projects/" + p.id; });
    } }] });
  }

  // ------------------------------------------------------------ 详情
  function projectDetail(main, id, tab) {
    var head = h("div");
    var tabsBox = h("div");
    var content = h("div");
    main.appendChild(head);
    main.appendChild(tabsBox);
    main.appendChild(content);
    var P = null;
    function reload(t) {
      App.get("/api/projects/" + id).then(function (p) {
        P = p;
        App.setTitle(p.name);
        renderHead(p);
        show(t || tab);
      });
    }
    function renderHead(p) {
      App.clear(head);
      var dl = ["submitted", "opened"].indexOf(p.status) >= 0 ? p.open_time : p.deadline;
      var warn = p.same_notice_projects && p.same_notice_projects.length
        ? h("div", { class: "req mt", style: { borderLeftColor: "var(--seal)", color: "var(--seal)" } }, "串标风险：本系统中「" + p.same_notice_projects.map(function (x) { return x.company_name; }).join("、") + "」也在投这个项目。存在控股/管理关系或负责人为同一人的单位不得参加同一标段投标，请负责人确认。")
        : null;
      head.appendChild(h("div", { class: "panel mb" }, h("div", { class: "bd row", style: { alignItems: "flex-start", gap: "16px" } },
        App.seal(dl),
        h("div", { style: { flex: 1 } },
          h("div", { class: "row" }, h("b", { style: { fontSize: "17px" } }, p.name), h("span", { class: "tag blue" }, p.status_name)),
          h("div", { class: "small muted mt" }, [p.company_name, p.project_no ? "编号 " + p.project_no : "", p.purchaser, p.budget ? "预算 " + App.fmtAmount(p.budget) : "", p.deadline ? "截止 " + p.deadline : "", p.open_time ? "开标 " + p.open_time : ""].filter(Boolean).join("　")),
          p.notice ? h("div", { class: "small mt" }, "来源公告：", h("a", { href: "#/notices/" + p.notice.id }, App.short(p.notice.title, 60))) : null,
          warn),
        h("div", { class: "row" }, statusSelect(p)))));
    }
    function statusSelect(p) {
      var s = h("select", { style: { width: "auto" } }, Object.keys({ evaluating: 1, preparing: 1, reviewing: 1, ready: 1, submitted: 1, opened: 1, won: 1, lost: 1, abandoned: 1 }).map(function (k) {
        var names = { evaluating: "待评估", preparing: "准备中", reviewing: "审核中", ready: "待投递", submitted: "已投递", opened: "已开标", won: "已中标", lost: "未中标", abandoned: "已放弃" };
        return h("option", { value: k, selected: p.status === k }, names[k]);
      }));
      s.addEventListener("change", function () { App.post("/api/projects/" + p.id + "/status", { status: s.value }).then(function () { App.toast("状态已更新", "ok"); reload(); }); });
      return h("label", { class: "row small" }, "状态", s);
    }
    function show(t) {
      tab = t;
      history.replaceState(null, "", "#/projects/" + id + (t === "overview" ? "" : "?tab=" + t));
      App.clear(tabsBox).appendChild(App.tabs([
        { key: "overview", label: "概览与节点" }, { key: "tender", label: "招标文件与清单" }, { key: "doc", label: "标书编写" }, { key: "check", label: "合规体检" }, { key: "submit", label: "审核与投递" }
      ], t, show));
      App.clear(content);
      if (t === "overview") overview(content, P, reload);
      if (t === "tender") tenderTab(content, P, reload);
      if (t === "doc") App.renderEditor(content, P, function () { reload("doc"); });
      if (t === "check") App.renderCheck(content, P);
      if (t === "submit") submitTab(content, P, reload);
    }
    reload();
  }

  // ------------------------------------------------------------ 概览
  function overview(box, p, reload) {
    var f = App.form([
      { key: "name", label: "项目名称", full: true },
      { key: "project_no", label: "项目编号" },
      { key: "purchaser", label: "采购人/招标人" },
      { key: "agency", label: "代理机构" },
      { key: "region", label: "地区" },
      { key: "budget", label: "预算/最高限价（元）", type: "number" },
      { key: "signup_deadline", label: "报名/获取文件截止", hint: "格式 2026-11-01 17:00" },
      { key: "clarify_deadline", label: "答疑/澄清截止" },
      { key: "deadline", label: "投标截止" },
      { key: "open_time", label: "开标时间" },
      { key: "bond_amount", label: "保证金（元）", type: "number" },
      { key: "bond_deadline", label: "保证金缴纳截止" },
      { key: "bond_status", label: "保证金状态", type: "select", options: [["none", "无需/未缴"], ["paid", "已缴纳"], ["refunded", "已退还"], ["forfeited", "被没收"]] },
      { key: "bond_paid_at", label: "缴纳日期" },
      { key: "bond_refunded_at", label: "退还日期" },
      { key: "note", label: "备注", type: "textarea", full: true, rows: 3 }
    ], p);
    var save = h("button", { class: "btn primary", on: { click: function (e) {
      var b = e.currentTarget; App.busy(b, true, "保存中");
      App.put("/api/projects/" + p.id, f.values()).then(function () { App.toast("已保存", "ok"); reload("overview"); }, function () { App.busy(b, false); });
    } } }, "保存");
    var events = App.table([
      { label: "时间", render: function (e) { return h("span", { class: "nowrap" }, e.created_at.slice(5, 16)); } },
      { label: "人员", key: "user_name" },
      { label: "动作", key: "action" },
      { label: "说明", key: "comment" }
    ], p.events, { empty: "暂无记录" });
    var res = null;
    if (p.result_notice) {
      res = App.panel("已关联的结果公告", h("div", null,
        h("p", null, h("a", { href: "#/notices/" + p.result_notice.id }, p.result_notice.title)),
        h("p", null, "中标人：" + ((p.result_notice.winners || []).map(function (w) { return w.name + (w.amount ? "（" + App.fmtAmount(w.amount) + "）" : ""); }).join("、") || "未识别")),
        h("p", { class: "small muted" }, "到「审核与投递」里确认并登记最终结果。")));
    }
    box.appendChild(h("div", { class: "grid2" }, App.panel("项目信息与节点", h("div", null, f.el, h("div", { class: "row end mt" }, save))), h("div", null, res, App.panel("项目动态", events))));
  }

  // ------------------------------------------------------------ 招标文件
  function tenderTab(box, p, reload) {
    var top = h("div", { class: "row mb" });
    var list = h("div");
    box.appendChild(top);
    box.appendChild(list);
    top.appendChild(h("button", { class: "btn primary", on: { click: function () {
      App.pickFiles(false, ".pdf,.docx,.txt").then(function (files) {
        if (!files.length) return;
        App.toast("上传并解析中…");
        App.upload(files[0], "tender").then(function (f) {
          return App.post("/api/projects/" + p.id + "/tender_docs", { file_id: f.id });
        }).then(function () { App.toast("解析完成", "ok"); reload("tender"); });
      });
    } } }, "上传招标文件（PDF / Word）"));
    if (p.notice && p.notice.attachments && p.notice.attachments.length) {
      top.appendChild(h("button", { class: "btn", on: { click: function () {
        App.runJob("下载公告附件并解析", App.post("/api/projects/" + p.id + "/download_attachments")).then(function (j) {
          var files = (j.result && j.result.files) || [];
          App.toast("已下载 " + files.filter(function (x) { return x.file_id; }).length + " 个附件" + (files.some(function (x) { return x.error; }) ? "（部分失败）" : ""), "ok");
          reload("tender");
        });
      } } }, "从公告下载附件（" + p.notice.attachments.length + "）"));
    }
    top.appendChild(h("span", { class: "spacer" }));
    top.appendChild(h("button", { class: "btn", on: { click: function (e) { App.download("/api/projects/" + p.id + "/checklist/export", e.currentTarget); } } }, "导出准备清单（Word）"));

    App.get("/api/projects/" + p.id + "/tender_docs").then(function (docs) {
      var latest = docs[0];
      if (!latest) list.appendChild(App.panel("招标文件", App.empty("还没有上传招标文件", "上传后系统会解析资格要求、评分标准、废标条款和文件格式，并对照资料库生成准备清单。没有招标文件时，清单按公告里的资格要求生成。")));
      else list.appendChild(analysisView(latest, p, reload));
      if (docs.length > 1) list.appendChild(App.panel("历史上传", App.table([{ label: "文件", key: "filename" }, { label: "上传时间", key: "created_at" }], docs.slice(1))));
      App.get("/api/projects/" + p.id + "/checklist").then(function (ck) {
        list.appendChild(App.panel("投标准备清单（对照资料库）", checklistTable(ck.items)));
      });
    });
  }

  function checklistTable(items) {
    var tone = { "已具备": "ok", "系统生成": "blue", "需人工确认": "amber", "缺失": "red", "已过期": "red", "等级不足": "red" };
    return App.table([
      { label: "类别", key: "group" },
      { label: "要求", render: function (i) { return h("div", { style: { maxWidth: "520px" } }, i.requirement); } },
      { label: "状态", render: function (i) { return h("span", { class: "tag " + (tone[i.status] || "") }, i.status); } },
      { label: "对应资料", render: function (i) { return (i.items || []).map(function (x) { return x.name + (x.valid_to ? "（至 " + x.valid_to + "）" : ""); }).join("；") || "—"; } },
      { label: "说明", key: "note" }
    ], items, { empty: "暂无清单" });
  }

  function analysisView(td, p, reload) {
    var a = td.analysis || {};
    var box = h("div");
    var tools = h("div", { class: "row" },
      h("span", { class: "tag " + (td.analysis_by === "ai" ? "ok" : "") }, td.analysis_by === "ai" ? "AI 解析" : "规则解析"),
      h("button", { class: "btn sm", on: { click: function (e) {
        var b = e.currentTarget; App.busy(b, true, "解析中");
        App.post("/api/tender_docs/" + td.id + "/analyze", { ai: false }).then(function () { reload("tender"); }, function () { App.busy(b, false); });
      } } }, "规则重新解析"),
      h("button", { class: "btn sm primary", on: { click: function () {
        App.runJob("AI 深度解析招标文件", App.post("/api/tender_docs/" + td.id + "/analyze", { ai: true })).then(function () { App.toast("AI 解析完成", "ok"); reload("tender"); });
      } } }, "AI 深度解析"));
    var info = h("div");
    info.appendChild(h("div", { class: "small muted mb" }, td.filename + " · " + (td.pages ? td.pages + " 页 · " : "") + td.text_chars + " 字" + (td.warning ? " · " : ""), td.warning ? h("span", { style: { color: "var(--seal)" } }, td.warning) : null));
    (a.warnings || []).forEach(function (w) { info.appendChild(h("div", { class: "small", style: { color: "var(--amber)" } }, "· " + w)); });
    if (a.summary) info.appendChild(h("div", { class: "req mb mt" }, a.summary));
    var ki = a.key_info || {};
    var kv = [["投标截止", ki.deadline], ["开标时间", ki.open_time], ["澄清截止", ki.clarify_deadline], ["保证金", ki.bond || (ki.bond_amount ? App.fmtAmount(ki.bond_amount) : "")], ["保证金截止", ki.bond_deadline], ["投标有效期", ki.validity], ["投递方式", ki.submit_method], ["投递平台", ki.platform]].filter(function (x) { return x[1]; });
    if (kv.length) info.appendChild(h("dl", { class: "kv mb" }, kv.map(function (x) { return [h("dt", null, x[0]), h("dd", null, String(x[1]))]; })));

    var sub = "qual";
    var tabsBox = h("div");
    var c = h("div");
    function show(t) {
      sub = t;
      App.clear(tabsBox).appendChild(App.tabs([
        { key: "qual", label: "资格要求（" + (a.qualifications || []).length + "）" },
        { key: "score", label: "评分标准（" + (a.scoring || []).length + "）" },
        { key: "reject", label: "废标/否决（" + (a.reject_clauses || []).length + "）" },
        { key: "star", label: "★条款（" + (a.starred || []).length + "）" },
        { key: "format", label: "文件组成与格式" },
        { key: "clarify", label: "澄清问题（" + (a.clarify_questions || []).length + "）" }
      ], t, show));
      App.clear(c);
      if (t === "qual") c.appendChild(App.table([{ label: "类型", key: "type" }, { label: "要求", key: "text" }, { label: "出处", key: "source" }], a.qualifications, { empty: "没有识别到资格要求" }));
      if (t === "score") {
        var total = (a.scoring || []).reduce(function (s, x) { return s + (+x.weight || 0); }, 0);
        c.appendChild(h("div", { class: "small muted mb" }, "合计 " + total + " 分；标书编写时，「技术」类评分项会自动变成技术方案的章节（评分点 → 响应）。"));
        c.appendChild(App.table([{ label: "类别", render: function (x) { return h("span", { class: "tag " + (x.category === "技术" ? "blue" : x.category === "价格" ? "amber" : "") }, x.category); } }, { label: "评分项", key: "item" }, { label: "分值", key: "weight", cls: "num" }, { label: "评分标准", render: function (x) { return h("div", { style: { maxWidth: "560px" } }, x.criteria); } }, { label: "出处", key: "source" }], a.scoring, { empty: "没有识别到评分标准" }));
      }
      if (t === "reject") c.appendChild(App.table([{ label: "", render: function (x) { return x.starred ? "★" : ""; } }, { label: "条款", key: "text" }, { label: "怎么避免", key: "tip" }, { label: "出处", key: "source" }], a.reject_clauses, { empty: "没有识别到废标条款" }));
      if (t === "star") c.appendChild(App.table([{ label: "★ 实质性条款（必须满足，不允许负偏离）", key: "text" }, { label: "出处", key: "source" }], a.starred, { empty: "没有★条款" }));
      if (t === "format") {
        var fm = a.format || {};
        c.appendChild(h("div", { class: "grid2e" },
          h("div", null, h("h4", null, "投标文件组成（按顺序）"), (fm.toc || []).length ? h("ol", null, fm.toc.map(function (x) { return h("li", null, x); })) : App.empty("没有识别到目录")),
          h("div", null, h("h4", null, "必须使用的格式文件"), h("div", { class: "row" }, (fm.required_forms || []).map(function (x) { return h("span", { class: "tag blue" }, x); })),
            h("h4", null, "编制 / 签章 / 装订 / 加密要求"), (fm.rules || []).length ? h("ul", null, fm.rules.map(function (x) { return h("li", null, x); })) : App.empty("无"))));
      }
      if (t === "clarify") c.appendChild((a.clarify_questions || []).length ? h("ol", null, a.clarify_questions.map(function (x) { return h("li", null, x); })) : App.empty("没有澄清问题", "AI 深度解析会列出招标文件里表述不清、可以向招标人提问的地方"));
    }
    show(sub);
    box.appendChild(App.panel("招标文件解析", h("div", null, info, tabsBox, c), tools));
    return box;
  }

  // ------------------------------------------------------------ 审核与投递
  function submitTab(box, p, reload) {
    var left = h("div");
    var right = h("div");
    box.appendChild(h("div", { class: "grid2e" }, left, right));
    // 审核
    var comment = h("textarea", { rows: 3, placeholder: "审核意见 / 说明" });
    var docState = p.doc ? { draft: "编写中", reviewing: "待审核", approved: "已审核通过" }[p.doc.status] : "还没有标书";
    var btns = h("div", { class: "row mt" });
    if (p.doc && p.doc.status === "draft") btns.appendChild(h("button", { class: "btn primary", on: { click: function () { act("submit"); } } }, "提交审核"));
    if (p.doc && p.doc.status === "reviewing" && App.can("manager", "reviewer")) {
      btns.appendChild(h("button", { class: "btn primary", on: { click: function () { act("approve"); } } }, "审核通过"));
      btns.appendChild(h("button", { class: "btn danger", on: { click: function () { act("reject"); } } }, "退回修改"));
    }
    function act(action, force) {
      App.post("/api/projects/" + p.id + "/review", { action: action, comment: comment.value, force: force || false }, { quiet: true }).then(function () {
        App.toast("已提交", "ok"); reload("submit");
      }).catch(function (e) {
        if (action === "approve" && e.status === 400 && e.message.indexOf("必须修改") >= 0) {
          App.confirm(e.message + "\n\n确定要强制通过吗？（会记录在项目动态里）", "强制通过").then(function (ok) { if (ok) act("approve", true); });
        } else App.toast(e.message, "err");
      });
    }
    left.appendChild(App.panel("标书审核", h("div", null,
      h("p", null, "当前状态：", h("b", null, docState)),
      h("p", { class: "small muted" }, "流程：编写 → 合规体检 → 提交审核 → 审核通过（定稿）→ 投递。审核通过后标书锁定，修改需先退回。"),
      comment, btns)));

    // 投递登记
    App.get("/api/platform_accounts" + App.qs({ company_id: p.company_id })).then(function (accs) {
      var f = App.form([
        { key: "platform_name", label: "投递平台", hint: "例如：某省公共资源交易平台" },
        { key: "platform_account_id", label: "平台账号（台账）", type: "select", options: [["", "—"]].concat(accs.map(function (a) { return [a.id, a.platform_name + " / " + (a.account_name || "") + (a.ca_holder ? " / CA：" + a.ca_holder : "")]; })) },
        { key: "ca_holder", label: "CA 证书持有人/经办人" },
        { key: "submit_method", label: "投递方式", type: "select", options: [["electronic", "电子投标（CA 加密上传）"], ["paper", "纸质递交"], ["both", "电子 + 纸质"]] },
        { key: "submitted_at", label: "投递时间", hint: "留空则记为现在" }
      ], p);
      var receipts = (p.receipt_file_ids || []).slice();
      var rlist = h("div", { class: "small" });
      function showR() {
        App.clear(rlist);
        if (!receipts.length) { rlist.appendChild(h("span", { class: "muted" }, "还没有上传回执")); return; }
        App.post("/api/files/meta", { ids: receipts }).then(function (fs) { fs.forEach(function (x) { rlist.appendChild(h("div", null, App.fileLink(x))); }); });
      }
      showR();
      right.appendChild(App.panel("投递登记", h("div", null,
        h("div", { class: "req mb" }, "系统不代替投递：电子投标请由经办人使用 CA 证书在官方平台加密上传；投递成功后在这里登记并上传回执截图，便于追溯。"),
        f.el,
        h("div", { class: "row mt" }, h("button", { class: "btn", on: { click: function () {
          App.pickFiles(true, "image/*,.pdf").then(function (files) {
            Promise.all(files.map(function (x) { return App.upload(x, "receipt"); })).then(function (fs) { fs.forEach(function (x) { receipts.push(x.id); }); showR(); });
          });
        } } }, "上传回执"), rlist),
        h("div", { class: "row end mt" }, h("button", { class: "btn primary", on: { click: function () {
          var v = f.values(); v.receipt_file_ids = receipts;
          App.post("/api/projects/" + p.id + "/submission", v).then(function () { App.toast("已登记投递", "ok"); reload("submit"); });
        } } }, "登记已投递")))));

      // 结果
      var rf = App.form([
        { key: "result", label: "结果", type: "select", options: [["won", "中标"], ["lost", "未中标"], ["failed", "流标/废标"], ["abandoned", "放弃投标"]], default: p.result || "lost" },
        { key: "result_amount", label: "中标金额（元）", type: "number" },
        { key: "winner", label: "中标人" },
        { key: "our_score", label: "我方得分", type: "number" },
        { key: "result_note", label: "复盘：失分点 / 经验", type: "textarea", full: true, rows: 3 }
      ], p);
      right.appendChild(App.panel("开标结果与复盘", h("div", null, rf.el,
        h("p", { class: "small muted" }, "登记结果后，本项目的技术方案会自动存入「历史标书」，以后写同类标书时可被检索复用。"),
        h("div", { class: "row end" }, h("button", { class: "btn primary", on: { click: function () {
          App.post("/api/projects/" + p.id + "/result", rf.values()).then(function () { App.toast("已登记结果", "ok"); reload("submit"); });
        } } }, "登记结果")))));
    });
  }
})();
