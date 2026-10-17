/* 标书编写 + 合规体检 */
(function () {
  "use strict";
  var h = App.h;
  var KIND = { form: "格式文件", price: "报价", qualification: "资格证明", performance: "业绩", personnel: "人员", deviation: "偏离表", tech: "技术方案", text: "普通章节" };
  var LIBCAT = { qualification: "qualification", performance: "performance", personnel: "personnel" };

  App.renderEditor = function (box, p, reloadProject) {
    var state = { doc: null, cur: App.state.selectSection || null, preview: false };
    App.state.selectSection = null;
    var toolbar = h("div", { class: "row mb" });
    var wrap = h("div");
    box.appendChild(toolbar);
    box.appendChild(wrap);

    function load(keep) {
      App.get("/api/projects/" + p.id + "/bid_doc").then(function (doc) {
        state.doc = doc;
        App.clear(toolbar);
        App.clear(wrap);
        if (!doc) {
          wrap.appendChild(App.panel("标书", App.empty("还没有标书",
            "系统会按招标文件要求的目录搭好框架：投标函、授权书、声明函等格式文件按资料库自动填写；资格证明、业绩、人员只引用资料库里的真实材料；技术方案按评分点逐条生成章节。建议先上传招标文件再创建。",
            h("button", { class: "btn primary", on: { click: function (e) {
              var b = e.currentTarget; App.busy(b, true, "创建中");
              App.post("/api/projects/" + p.id + "/bid_doc").then(function () { load(); }, function () { App.busy(b, false); });
            } } }, "创建标书"))));
          return;
        }
        if (!keep || !doc.sections.some(function (s) { return s.id === state.cur; })) state.cur = state.cur && doc.sections.some(function (s) { return s.id === state.cur; }) ? state.cur : (doc.sections[0] || {}).id;
        renderToolbar(doc);
        renderBody(doc);
      });
    }

    function renderToolbar(doc) {
      var locked = doc.status === "approved";
      var ph = doc.sections.reduce(function (a, s) { return a + s.placeholders; }, 0);
      var chars = doc.sections.reduce(function (a, s) { return a + s.chars; }, 0);
      toolbar.appendChild(h("b", null, doc.title));
      toolbar.appendChild(h("span", { class: "tag " + (locked ? "ok" : doc.status === "reviewing" ? "amber" : "") }, { draft: "编写中", reviewing: "审核中", approved: "已定稿" }[doc.status]));
      toolbar.appendChild(h("span", { class: "small muted" }, chars + " 字 · " + (ph ? ph + " 处待补充" : "无待补充")));
      toolbar.appendChild(h("span", { class: "spacer" }));
      toolbar.appendChild(h("button", { class: "btn", on: { click: function () { priceDialog(doc); } } }, "报价" + (doc.price && doc.price.total ? "：" + App.fmtAmount(doc.price.total) : "")));
      toolbar.appendChild(h("button", { class: "btn", disabled: locked, on: { click: function () {
        App.confirm("为所有空章节生成内容：格式文件按资料库填写；技术章节" + (App.state.llm ? "由 AI 起草" : "搭好骨架并引用方案素材（配置 AI 后可自动起草）") + "。已有内容的章节不会被覆盖。", "开始生成").then(function (ok) {
          if (!ok) return;
          App.runJob("生成整本标书", App.post("/api/bid_docs/" + doc.id + "/generate_all", {})).then(function (j) {
            var r = j.result || {};
            App.toast("已生成 " + (r.generated || 0) + " 个章节" + ((r.errors || []).length ? "，失败 " + r.errors.length + " 个" : ""), (r.errors || []).length ? "err" : "ok");
            load(true);
          });
        });
      } } }, "一键生成"));
      toolbar.appendChild(h("button", { class: "btn", on: { click: function (e) { App.download("/api/bid_docs/" + doc.id + "/export", e.currentTarget); } } }, "导出 Word"));
      toolbar.appendChild(h("button", { class: "btn", disabled: locked, title: "按最新的招标文件解析结果重建目录", on: { click: function () {
        App.confirm("按最新的招标文件解析结果重建目录？现有内容会清空（旧内容保留在版本记录里）。", "重建").then(function (ok) {
          if (ok) App.post("/api/projects/" + p.id + "/bid_doc/rebuild").then(function () { load(); });
        });
      } } }, "重建目录"));
    }

    function renderBody(doc) {
      var outline = h("div", { class: "panel outline" });
      var editor = h("div");
      wrap.appendChild(h("div", { class: "editor" }, outline, editor));
      outline.appendChild(h("div", { class: "hd" }, h("h3", null, "目录"), h("span", { class: "spacer" }),
        h("button", { class: "btn sm", disabled: doc.status === "approved", on: { click: function () { addSection(doc); } } }, "添加章节")));
      var ol = h("div", { style: { padding: "6px" } });
      outline.appendChild(ol);
      doc.sections.forEach(function (s) {
        var dot = "dot";
        if (s.placeholders) dot += " red";
        else if (s.ai_generated && !s.reviewed) dot += " warn";
        else if ((s.content || "").trim()) dot += " ok";
        ol.appendChild(h("div", { class: "ol-item l" + s.level + (s.id === state.cur ? " on" : ""), title: s.title + (s.placeholders ? "（" + s.placeholders + " 处待补充）" : ""), on: { click: function () { state.cur = s.id; App.clear(wrap); renderBody(doc); } } },
          h("span", { class: dot }), h("span", { class: "t" }, s.title)));
      });
      outline.appendChild(h("div", { class: "small muted", style: { padding: "8px 12px", borderTop: "1px solid var(--line-2)" } },
        h("span", { class: "dot red", style: { display: "inline-block" } }), " 有待补充　", h("span", { class: "dot warn", style: { display: "inline-block" } }), " AI 起草待审核　", h("span", { class: "dot ok", style: { display: "inline-block" } }), " 已完成"));
      var s = doc.sections.filter(function (x) { return x.id === state.cur; })[0];
      if (s) sectionEditor(editor, s, doc);
    }

    function sectionEditor(box, s, doc) {
      var locked = doc.status === "approved";
      var title = h("input", { type: "text", value: s.title, disabled: locked, style: { flex: "1", width: "auto" } });
      var level = h("select", { style: { width: "90px" }, disabled: locked }, [1, 2, 3].map(function (l) { return h("option", { value: l, selected: s.level === l }, l + " 级"); }));
      var ta = h("textarea", { class: "ed-text", disabled: locked });
      ta.value = s.content || "";
      var prev = h("div", { class: "hidden", style: { minHeight: "420px", border: "1px solid var(--line)", borderRadius: "4px", padding: "10px 14px" } });
      var dirty = false;
      ta.addEventListener("input", function () { dirty = true; saveBtn.disabled = false; });
      ta.addEventListener("keydown", function (e) { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });
      var saveBtn = h("button", { class: "btn primary", disabled: true, on: { click: function () { save(); } } }, "保存");
      function save(extra) {
        var body = { title: title.value, level: +level.value, content: ta.value };
        Object.assign(body, extra || {});
        return App.put("/api/sections/" + s.id, body).then(function () { dirty = false; App.toast("已保存", "ok"); load(true); });
      }
      var toggle = h("button", { class: "btn", on: { click: function () {
        state.preview = !state.preview;
        App.clear(prev).appendChild(App.md(ta.value));
        prev.classList.toggle("hidden", !state.preview);
        ta.classList.toggle("hidden", state.preview);
        toggle.textContent = state.preview ? "编辑" : "预览";
      } } }, "预览");

      var actions = h("div", { class: "row" });
      if (!locked) {
        if (s.kind === "tech") {
          actions.appendChild(h("button", { class: "btn primary", on: { click: function () { genAI(s); } } }, "AI 起草本章"));
          var rw = h("select", { style: { width: "auto" } }, [["", "AI 改写…"], ["polish", "润色"], ["expand", "扩写"], ["shorten", "精简"], ["formal", "改成正式书面语"], ["score", "对照评分点补全"], ["custom", "自定义要求…"]].map(function (o) { return h("option", { value: o[0] }, o[1]); }));
          rw.addEventListener("change", function () {
            var v = rw.value; rw.value = "";
            if (!v) return;
            if (dirty) { App.toast("请先保存当前修改", "err"); return; }
            if (v === "custom") {
              var t = h("textarea", { rows: 4, placeholder: "例如：增加一节夜间施工降噪措施；把进度计划改成表格" });
              App.modal({ title: "自定义改写要求", body: t, buttons: [{ text: "取消" }, { text: "开始改写", primary: true, onClick: function () { rewrite(s, t.value); } }] });
            } else rewrite(s, v);
          });
          actions.appendChild(rw);
        }
        if (["form", "price", "qualification", "performance", "personnel", "deviation", "tech"].indexOf(s.kind) >= 0) {
          actions.appendChild(h("button", { class: "btn", title: s.kind === "tech" ? "不用 AI，按评分要求搭骨架并引用方案素材" : "按资料库最新数据重新填写", on: { click: function () {
            if ((s.content || "").trim() && !window.confirm("会覆盖当前内容（旧内容保留在版本记录里），继续？")) return;
            App.post("/api/sections/" + s.id + "/generate", { ai: false }).then(function () { App.toast("已重新生成", "ok"); load(true); });
          } } }, s.kind === "tech" ? "生成骨架" : "按资料库重新生成"));
        }
        if (LIBCAT[s.kind]) actions.appendChild(h("button", { class: "btn", on: { click: function () { pickRefs(s, doc); } } }, "选择引用的资料"));
      }
      actions.appendChild(h("button", { class: "btn", on: { click: function () { showVersions(s); } } }, "版本记录"));
      if (s.ai_generated && !s.reviewed && !locked) actions.appendChild(h("button", { class: "btn", on: { click: function () { save({ reviewed: true }); } } }, "标记已审核"));
      actions.appendChild(h("span", { class: "spacer" }));
      if (!locked) {
        actions.appendChild(h("button", { class: "btn sm", title: "上移", on: { click: function () { App.post("/api/sections/" + s.id + "/move", { direction: "up" }).then(function () { load(true); }); } } }, "↑"));
        actions.appendChild(h("button", { class: "btn sm", title: "下移", on: { click: function () { App.post("/api/sections/" + s.id + "/move", { direction: "down" }).then(function () { load(true); }); } } }, "↓"));
        actions.appendChild(h("button", { class: "btn sm danger", on: { click: function () {
          App.confirm("删除章节「" + s.title + "」？内容会保留在版本记录里。", "删除").then(function (ok) { if (ok) App.del("/api/sections/" + s.id).then(function () { state.cur = null; load(); }); });
        } } }, "删除"));
      }

      var req = null;
      if (s.requirement) req = h("details", { class: "mb", open: s.kind === "tech" }, h("summary", { class: "small muted" }, "本章评分要求 / 编写要求"), h("div", { class: "req" }, s.requirement));
      var badges = h("div", { class: "row small" },
        h("span", { class: "tag" }, KIND[s.kind] || s.kind),
        s.ai_generated ? h("span", { class: "tag " + (s.reviewed ? "ok" : "amber") }, s.reviewed ? "AI 起草 · 已审核" : "AI 起草 · 待审核") : null,
        s.placeholders ? h("span", { class: "tag red" }, s.placeholders + " 处【待补充】") : null,
        h("span", { class: "muted" }, s.chars + " 字"),
        (s.refs || []).length ? h("span", { class: "muted" }, "引用资料 " + s.refs.length + " 条") : null,
        h("span", { class: "spacer" }), h("span", { class: "muted" }, "Ctrl+S 保存"));
      var help = h("details", { class: "small muted mt" }, h("summary", null, "写作格式说明"),
        h("div", { class: "pre" }, "# 小标题 / ## 二级小标题\n| 表头1 | 表头2 |   ← 用竖线写表格，第一行为表头\n- 列表项\n**加粗**\n【待补充：说明】  ← 缺的信息先占位，体检会提示\n![图片说明](file:文件ID)  ← 插入资料库里的图片\n[[分页]]  ← 导出 Word 时分页"));
      box.appendChild(App.panel(null, h("div", null,
        h("div", { class: "row mb" }, title, level),
        badges, h("div", { style: { height: "8px" } }),
        req,
        h("div", { class: "row mb" }, actions),
        ta, prev,
        h("div", { class: "row end mt" }, toggle, saveBtn),
        help)));
      if (s.kind === "form" || s.kind === "price") box.appendChild(h("p", { class: "small muted" }, "格式文件按「主体与订阅」里的公司信息和「报价」自动填写。改了公司信息或报价后，点「按资料库重新生成」即可同步。"));
    }

    function genAI(s) {
      if (!App.state.llm) {
        App.toast("还没有配置 AI 模型，已改为生成骨架；管理员可在「系统设置 → AI 模型」配置", "err");
        App.post("/api/sections/" + s.id + "/generate", { ai: false }).then(function () { load(true); });
        return;
      }
      var extra = h("textarea", { rows: 3, placeholder: "可选：本章的特别要求，例如「重点写冬季施工保障」「突出本地化服务团队」" });
      App.modal({ title: "AI 起草：" + s.title, body: h("div", null,
        h("p", { class: "small muted" }, "AI 只依据招标文件原文、项目信息和资料库里的方案素材写作；不会编造证书、业绩和人员；找不到依据的数字会写成【待补充】。生成后请逐段核对再标记已审核。"),
        (s.content || "").trim() ? h("p", { class: "small", style: { color: "var(--amber)" } }, "当前内容会被替换（旧内容保留在版本记录里）。") : null,
        extra),
        buttons: [{ text: "取消" }, { text: "开始起草", primary: true, onClick: function () {
          App.runJob("AI 起草「" + s.title + "」", App.post("/api/sections/" + s.id + "/generate", { ai: true, extra: extra.value })).then(function () { App.toast("起草完成，请审核", "ok"); load(true); });
        } }] });
    }

    function rewrite(s, instruction) {
      App.runJob("AI 改写「" + s.title + "」", App.post("/api/sections/" + s.id + "/rewrite", { instruction: instruction })).then(function () { App.toast("改写完成，请审核", "ok"); load(true); });
    }

    function showVersions(s) {
      App.get("/api/sections/" + s.id + "/versions").then(function (vs) {
        var m = App.modal({ title: "版本记录：" + s.title, wide: true, body: App.table([
          { label: "时间", key: "created_at" }, { label: "人员", key: "user_name" }, { label: "说明", key: "note" }, { label: "字数", key: "chars", cls: "num" },
          { label: "内容预览", render: function (v) { return h("div", { class: "small pre", style: { maxWidth: "420px" } }, v.preview); } },
          { label: "", render: function (v) { return h("button", { class: "btn sm", on: { click: function () {
            App.post("/api/sections/" + s.id + "/restore", { version_id: v.id }).then(function () { App.toast("已恢复", "ok"); m.close(); load(true); });
          } } }, "恢复此版本"); } }
        ], vs, { empty: "还没有历史版本", emptyHint: "每次覆盖已有内容时，旧内容会自动保存在这里" }) });
      });
    }

    function pickRefs(s, doc) {
      var cat = LIBCAT[s.kind];
      App.get("/api/library" + App.qs({ company_id: p.company_id, category: cat })).then(function (items) {
        var chosen = (s.refs || []).map(function (r) { return r.item_id; });
        var boxes = items.map(function (it) {
          var cb = h("input", { type: "checkbox", checked: chosen.indexOf(it.id) >= 0 });
          cb.dataset.id = it.id;
          return h("label", { class: "chk", style: { display: "flex", padding: "6px 0", borderBottom: "1px solid var(--line-2)" } }, cb,
            h("span", null, it.name, " ", it.level ? h("span", { class: "tag" }, it.level) : null, " ",
              it.valid_to ? h("span", { class: "tag " + (it.expire_state === "expired" ? "red" : it.expire_state === "soon" ? "amber" : "") }, "至 " + it.valid_to) : null,
              it.data && it.data.client ? h("span", { class: "muted small" }, " " + it.data.client) : null,
              it.data && it.data.position ? h("span", { class: "muted small" }, " " + it.data.position + (it.data.certs ? " · " + it.data.certs : "")) : null));
        });
        App.modal({ title: "选择引用的资料（" + { qualification: "资质证书", performance: "业绩", personnel: "人员" }[cat] + "）", body: items.length ? h("div", null, h("p", { class: "small muted" }, "不选则自动引用：资质全部、业绩按项目相关度挑选。过期证书会在体检时报错。"), boxes) : App.empty("资料库里还没有这类资料", "", h("a", { class: "btn", href: "#/library" }, "去资料库添加")), buttons: [{ text: "取消" }, { text: "确定并重新生成本章", primary: true, onClick: function () {
          var ids = boxes.map(function (b) { return b.querySelector("input"); }).filter(function (c) { return c.checked; }).map(function (c) { return +c.dataset.id; });
          return App.put("/api/sections/" + s.id, { refs: ids }).then(function () { load(true); });
        } }] });
      });
    }

    function addSection(doc) {
      var f = App.form([
        { key: "title", label: "章节标题", full: true },
        { key: "kind", label: "类型", type: "select", options: [["tech", "技术方案（可 AI 起草）"], ["text", "普通章节"], ["qualification", "资格证明（引用资料库）"], ["performance", "业绩（引用资料库）"], ["personnel", "人员（引用资料库）"], ["deviation", "偏离表"]] },
        { key: "level", label: "层级", type: "select", options: [["1", "1 级"], ["2", "2 级"], ["3", "3 级"]] },
        { key: "requirement", label: "评分要求 / 编写要求（技术章节建议填写）", type: "textarea", full: true }
      ]);
      App.modal({ title: "添加章节（插在当前章节之后）", body: f.el, buttons: [{ text: "取消" }, { text: "添加", primary: true, onClick: function () {
        var v = f.values(); v.after_id = state.cur;
        return App.post("/api/bid_docs/" + doc.id + "/sections", v).then(function (s) { state.cur = s.id; load(true); });
      } }] });
    }

    function priceDialog(doc) {
      var price = doc.price || {};
      var rows = (price.items || []).slice();
      if (!rows.length) rows.push({ name: "", spec: "", qty: 1, unit: "项", unit_price: 0 });
      var tbody = h("tbody");
      var totalEl = h("b");
      var budgetHint = h("span", { class: "small" });
      function recalc() {
        var t = 0;
        Array.prototype.forEach.call(tbody.querySelectorAll("tr"), function (tr) {
          var q = +tr.querySelector("[data-k=qty]").value || 0, u = +tr.querySelector("[data-k=unit_price]").value || 0;
          tr.querySelector(".sub").textContent = (q * u).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          t += q * u;
        });
        totalEl.textContent = t.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " 元";
        if (p.budget) {
          var over = t > p.budget;
          budgetHint.textContent = over ? "　超过预算/最高限价 " + App.fmtAmount(p.budget) + "，会被否决！" : "　预算/最高限价 " + App.fmtAmount(p.budget) + "，占 " + Math.round(t / p.budget * 100) + "%";
          budgetHint.style.color = over ? "var(--seal)" : "var(--ink-3)";
        }
      }
      function addRow(r) {
        var tr = h("tr");
        [["name", "名称", "text"], ["spec", "品牌/规格", "text"], ["qty", "数量", "number"], ["unit", "单位", "text"], ["unit_price", "单价（元）", "number"]].forEach(function (c) {
          var i = h("input", { type: c[2], placeholder: c[1], value: r[c[0]] === undefined ? "" : r[c[0]] });
          i.dataset.k = c[0];
          i.addEventListener("input", recalc);
          tr.appendChild(h("td", null, i));
        });
        tr.appendChild(h("td", { class: "sub num" }));
        tr.appendChild(h("td", null, h("button", { class: "btn sm danger", on: { click: function () { tr.remove(); recalc(); } } }, "删")));
        tbody.appendChild(tr);
      }
      rows.forEach(addRow);
      var extra = App.form([
        { key: "delivery", label: "交货期/服务期/工期" },
        { key: "warranty", label: "质保期" },
        { key: "tax_note", label: "报价说明", full: true, placeholder: "例如：含税、含运输安装调试培训等全部费用" }
      ], price);
      var body = h("div", null,
        h("table", { class: "t" }, h("thead", null, h("tr", null, ["名称", "品牌/规格型号", "数量", "单位", "单价（元）", "小计（元）", ""].map(function (x) { return h("th", null, x); }))), tbody),
        h("div", { class: "row mt" }, h("button", { class: "btn sm", on: { click: function () { addRow({ qty: 1 }); } } }, "添加一行"), h("span", { class: "spacer" }), "合计：", totalEl, budgetHint),
        h("div", { class: "mt" }, extra.el),
        h("p", { class: "small muted" }, "价格由人决定，系统不做自动定价。保存后，开标一览表和投标函里的金额（含大写）会自动同步。"));
      recalc();
      App.modal({ title: "报价", wide: true, body: body, buttons: [{ text: "取消" }, { text: "保存报价", primary: true, onClick: function () {
        var items = Array.prototype.map.call(tbody.querySelectorAll("tr"), function (tr) {
          var o = {};
          Array.prototype.forEach.call(tr.querySelectorAll("input"), function (i) { o[i.dataset.k] = i.type === "number" ? +i.value || 0 : i.value; });
          return o;
        }).filter(function (o) { return o.name || o.unit_price; });
        var v = extra.values(); v.items = items;
        return App.put("/api/bid_docs/" + doc.id + "/price", v).then(function () { App.toast("报价已保存，投标函金额已同步", "ok"); load(true); });
      } }] });
    }

    load();
  };

  // ------------------------------------------------------------ 合规体检
  App.renderCheck = function (box, p) {
    var res = h("div");
    var bar = h("div", { class: "row mb" });
    box.appendChild(bar);
    box.appendChild(res);
    if (!p.doc) { res.appendChild(App.empty("还没有标书", "先到「标书编写」创建标书")); return; }
    bar.appendChild(h("button", { class: "btn primary", on: { click: function (e) {
      var b = e.currentTarget; App.busy(b, true, "体检中");
      App.post("/api/bid_docs/" + p.doc.id + "/check", {}).then(function (r) { App.busy(b, false); show(r); }, function () { App.busy(b, false); });
    } } }, "运行合规体检"));
    bar.appendChild(h("button", { class: "btn", on: { click: function () {
      if (!App.state.llm) { App.toast("还没有配置 AI 模型", "err"); return; }
      App.runJob("合规体检（含 AI 逐条核对）", App.post("/api/bid_docs/" + p.doc.id + "/check", { ai: true })).then(function (j) { show(j.result); });
    } } }, "体检 + AI 逐条核对★条款"));
    bar.appendChild(h("span", { class: "small muted" }, "检查：待补充、报价一致性与超限价、资质过期、旧项目信息残留、主体混用、格式文件缺失、★条款响应、评分点覆盖、雷同与串标风险。"));
    App.get("/api/bid_docs/" + p.doc.id + "/checks").then(function (hist) {
      if (hist.length) show(hist[0].result);
      else res.appendChild(App.empty("还没有体检记录", "建议每次修改后、提交审核前都跑一次"));
    });

    function show(r) {
      App.clear(res);
      var s = r.summary || {};
      res.appendChild(h("div", { class: "kpis" },
        tile(s.error || 0, "必须修改", s.error ? "var(--seal)" : "var(--ok)"),
        tile(s.warning || 0, "警告", "var(--amber)"),
        tile(s.info || 0, "提示", "var(--ink-2)"),
        tile(r.passed ? "通过" : "未通过", "体检时间 " + (r.checked_at || "").slice(5, 16), r.passed ? "var(--ok)" : "var(--seal)")));
      var list = h("div");
      (r.items || []).forEach(function (it) {
        list.appendChild(h("div", { class: "chk-item" },
          h("span", { class: "lvl " + it.level }, { error: "必须修改", warning: "警告", info: "提示" }[it.level]),
          h("div", null, h("div", null, h("span", { class: "tag" }, it.category), " ", it.title), it.detail ? h("div", { class: "small muted pre" }, it.detail) : null),
          it.section_id ? h("button", { class: "btn sm", on: { click: function () {
            App.state.selectSection = it.section_id;
            location.hash = "#/projects/" + p.id + "?tab=doc";
          } } }, "去修改") : h("span")));
      });
      res.appendChild(App.panel("体检结果", (r.items || []).length ? list : App.empty("没有发现问题")));
    }
    function tile(v, k, color) {
      return h("div", { class: "kpi" }, h("div", { class: "v", style: { color: color } }, String(v)), h("div", { class: "k" }, k));
    }
  };
})();
