/* 企业资料库 / 主体与订阅 / 平台账号与 CA 台账 */
(function () {
  "use strict";
  var h = App.h;
  var CATS = [["qualification", "资质证书"], ["basic", "基础资料"], ["performance", "业绩"], ["personnel", "人员"], ["solution", "方案素材"], ["history_bid", "历史标书"]];
  var HINT = {
    basic: "营业执照、审计报告、纳税/社保证明、信用查询截图、法人身份证等。投标时作为通用证明材料自动引用。",
    qualification: "资质证书、ISO 体系认证、安全生产许可证、专利/软著、荣誉。填写有效期，系统会提前提醒续期，并在投标前检查是否过期。",
    performance: "合同、中标通知书、验收报告。填写客户、金额、签订日期和标签，写标时按项目相关度自动挑选。",
    personnel: "项目经理、技术负责人等。填写岗位、证书和在建项目占用情况（同一人不能同时在两个项目任项目经理）。",
    solution: "技术方案、施工组织设计、服务方案、售后承诺等可复用素材。AI 写标时会检索这里，只借鉴写法，不照搬项目数据。",
    history_bid: "过往投标文件（标注中标/未中标）。登记结果后系统也会自动存入。"
  };

  // ------------------------------------------------------------ 资料库
  App.routes.library = function (main, r) {
    if (!App.state.companies.length) { main.appendChild(App.empty("还没有投标主体", "资料库按主体隔离，请先添加主体", h("a", { class: "btn primary", href: "#/companies" }, "添加主体"))); return; }
    var cid = App.state.cid || App.state.companies[0].id;
    var cat = r.q.cat || "qualification";
    var q = h("input", { type: "text", placeholder: "搜索名称、编号、标签", style: { width: "240px" } });
    var top = h("div", { class: "row mb" }, "主体", App.companySelect(cid, function (v) { cid = v; App.state.cid = v; try { localStorage.setItem("bid_cid", v); } catch (e) { /* 忽略 */ } load(); }), q, h("span", { class: "spacer" }));
    var addBtn = h("button", { class: "btn primary", on: { click: function () { editItem(null); } } }, "新增");
    var impBtn = h("button", { class: "btn", on: { click: importFile } }, "从文件导入（PDF/Word）");
    top.appendChild(impBtn);
    top.appendChild(addBtn);
    var tabsBox = h("div");
    var hint = h("p", { class: "small muted" });
    var list = h("div");
    main.appendChild(top);
    main.appendChild(tabsBox);
    main.appendChild(hint);
    main.appendChild(App.panel(null, list));
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") load(); });

    function load() {
      App.clear(tabsBox).appendChild(App.tabs(CATS.map(function (c) { return { key: c[0], label: c[1] }; }), cat, function (k) { cat = k; history.replaceState(null, "", "#/library?cat=" + k); load(); }));
      hint.textContent = HINT[cat];
      impBtn.classList.toggle("hidden", cat !== "solution" && cat !== "history_bid");
      App.clear(list).appendChild(App.empty("加载中…"));
      App.get("/api/library" + App.qs({ company_id: cid, category: cat, q: q.value.trim() })).then(function (items) {
        App.clear(list);
        var cols = [{ label: "名称", render: function (it) { return h("a", { href: "#", on: { click: function (e) { e.preventDefault(); editItem(it); } } }, it.name); } }];
        if (cat === "qualification" || cat === "basic") cols.push({ label: "等级", key: "level" }, { label: "编号", key: "number" }, { label: "发证机关", key: "issuer" }, { label: "有效期至", render: validity });
        if (cat === "performance") cols.push({ label: "客户", render: function (it) { return it.data.client || ""; } }, { label: "金额（万元）", cls: "num", render: function (it) { return it.data.amount_wan || ""; } }, { label: "签订日期", render: function (it) { return it.data.sign_date || ""; } }, { label: "标签", key: "tags" });
        if (cat === "personnel") cols.push({ label: "岗位", render: function (it) { return it.data.position || ""; } }, { label: "证书/职称", render: function (it) { return [it.data.certs, it.data.title].filter(Boolean).join("；"); } }, { label: "证书有效期", render: validity }, { label: "在建占用", render: function (it) { return it.data.occupied_by ? h("span", { class: "tag amber" }, it.data.occupied_by) : ""; } });
        if (cat === "solution" || cat === "history_bid") cols.push({ label: "标签", key: "tags" }, { label: "字数", key: "content_chars", cls: "num" }, { label: "内容", render: function (it) { return h("span", { class: "small muted" }, App.short(it.content_preview, 80)); } });
        cols.push({ label: "附件", render: function (it) { return it.file_ids.length ? it.file_ids.length + " 个" : ""; } });
        cols.push({ label: "", render: function (it) { return h("button", { class: "btn sm danger", on: { click: function () { App.confirm("删除「" + it.name + "」？", "删除").then(function (ok) { if (ok) App.del("/api/library/" + it.id).then(load); }); } } }, "删除"); } });
        list.appendChild(App.table(cols, items, { empty: "这一类还没有资料", emptyHint: "点右上角「新增」添加" }));
      });
    }
    function validity(it) {
      if (!it.valid_to) return h("span", { class: "muted" }, "长期");
      return h("span", { class: "tag " + (it.expire_state === "expired" ? "red" : it.expire_state === "soon" ? "amber" : "") }, it.valid_to + (it.expire_state === "expired" ? " 已过期" : it.expire_state === "soon" ? " 将到期" : ""));
    }

    function editItem(it) {
      var isNew = !it;
      var load1 = isNew ? Promise.resolve({ name: "", data: {}, file_ids: [], category: cat }) : App.get("/api/library/" + it.id);
      load1.then(function (item) {
        var c = item.category || cat;
        var fields = [{ key: "name", label: c === "personnel" ? "姓名" : "名称", full: true }];
        if (c === "qualification" || c === "basic") fields.push({ key: "level", label: "等级（如：二级、甲级）" }, { key: "number", label: "证书编号" }, { key: "issuer", label: "发证机关" }, { key: "valid_from", label: "发证日期", type: "date" }, { key: "valid_to", label: "有效期至（长期留空）", type: "date" });
        if (c === "performance") fields.push({ key: "d_client", label: "客户/采购人" }, { key: "d_amount_wan", label: "合同金额（万元）", type: "number" }, { key: "d_sign_date", label: "签订日期", type: "date" }, { key: "d_finish_date", label: "完成/验收日期", type: "date" }, { key: "d_industry", label: "行业" });
        if (c === "personnel") fields.push({ key: "d_position", label: "岗位（如：项目经理）" }, { key: "d_certs", label: "执业证书（如：一级建造师（机电））" }, { key: "number", label: "证书编号" }, { key: "valid_to", label: "证书有效期至", type: "date" }, { key: "d_title", label: "职称" }, { key: "d_occupied_by", label: "在建项目占用（没有留空）" });
        fields.push({ key: "tags", label: "标签（逗号分隔，便于检索匹配）", full: true });
        if (c === "performance") fields.push({ key: "content", label: "项目内容简介", type: "textarea", full: true, rows: 3 });
        if (c === "solution" || c === "history_bid") fields.push({ key: "content", label: "正文（支持 # 小标题、| 表格 |）", type: "textarea", full: true, rows: 14 });
        var vals = Object.assign({}, item);
        Object.keys(item.data || {}).forEach(function (k) { vals["d_" + k] = item.data[k]; });
        var f = App.form(fields, vals);
        var fileIds = (item.file_ids || []).slice();
        var fl = h("div", { class: "small" });
        function showFiles() {
          App.clear(fl);
          if (!fileIds.length) { fl.appendChild(h("span", { class: "muted" }, "还没有附件。证书扫描件请上传 JPG/PNG 图片，导出标书时会自动插入。")); return; }
          App.post("/api/files/meta", { ids: fileIds }).then(function (fs) {
            fs.forEach(function (x) {
              fl.appendChild(h("div", { class: "row" }, App.fileLink(x), h("span", { class: "muted" }, Math.round(x.size / 1024) + " KB"),
                h("button", { class: "btn link", on: { click: function () { fileIds = fileIds.filter(function (i) { return i !== x.id; }); showFiles(); } } }, "移除")));
            });
          });
        }
        showFiles();
        var up = h("button", { class: "btn sm", on: { click: function () {
          App.pickFiles(true, "image/*,.pdf,.doc,.docx").then(function (files) {
            Promise.all(files.map(function (x) { return App.upload(x, "library"); })).then(function (fs) { fs.forEach(function (x) { fileIds.push(x.id); }); showFiles(); });
          });
        } } }, "上传附件");
        App.modal({ title: (isNew ? "新增" : "编辑") + "：" + CATS.filter(function (x) { return x[0] === c; })[0][1], wide: c === "solution" || c === "history_bid", body: h("div", null, f.el, h("div", { class: "mt" }, h("div", { class: "row mb" }, h("b", null, "附件"), up), fl)), buttons: [{ text: "取消" }, { text: "保存", primary: true, onClick: function () {
          var v = f.values();
          var data = Object.assign({}, item.data || {});
          var body = { company_id: cid, category: c, file_ids: fileIds };
          Object.keys(v).forEach(function (k) { if (k.indexOf("d_") === 0) data[k.slice(2)] = v[k]; else body[k] = v[k]; });
          if (data.amount_wan !== undefined && data.amount_wan !== "") data.amount_wan = +data.amount_wan;
          body.data = data;
          var pr = isNew ? App.post("/api/library", body) : App.put("/api/library/" + item.id, body);
          return pr.then(function () { App.toast("已保存", "ok"); load(); });
        } }] });
      });
    }

    function importFile() {
      App.pickFiles(false, ".pdf,.docx,.txt").then(function (files) {
        if (!files.length) return;
        App.toast("上传并提取文字中…");
        App.upload(files[0], "library").then(function (f) {
          return App.post("/api/library/import_file", { company_id: cid, category: cat, file_id: f.id });
        }).then(function (it) { App.toast("已导入「" + it.name + "」，共 " + it.content_chars + " 字" + (it.warning ? "。" + it.warning : ""), it.warning ? "err" : "ok"); load(); });
      });
    }
    load();
  };

  // ------------------------------------------------------------ 主体与订阅
  var COMPANY_FIELDS = [
    { key: "name", label: "主体全称（与营业执照一致）", full: true },
    { key: "short_name", label: "简称" },
    { key: "credit_code", label: "统一社会信用代码" },
    { key: "legal_person", label: "法定代表人" },
    { key: "legal_person_title", label: "法定代表人职务", default: "法定代表人" },
    { key: "reg_capital", label: "注册资本（如：2000万元）" },
    { key: "founded_date", label: "成立日期", type: "date" },
    { key: "address", label: "注册地址", full: true },
    { key: "postcode", label: "邮编" },
    { key: "phone", label: "联系电话" },
    { key: "email", label: "电子邮箱" },
    { key: "fax", label: "传真" },
    { key: "contact_person", label: "投标授权代表/联系人" },
    { key: "contact_phone", label: "联系人电话" },
    { key: "bank_name", label: "开户银行" },
    { key: "bank_account_note", label: "银行账号（仅用于填写投标函，可留空）" },
    { key: "staff_count", label: "从业人员（人）", type: "number" },
    { key: "annual_revenue_wan", label: "上年营业收入（万元）", type: "number" },
    { key: "industries", label: "主营行业（逗号分隔）", full: true },
    { key: "business_scope", label: "经营范围", type: "textarea", full: true, rows: 3 },
    { key: "intro", label: "公司简介", type: "textarea", full: true, rows: 3 },
    { key: "is_sme", label: "属于中小企业（影响「专门面向中小企业」项目的判断）", type: "checkbox", full: true, default: true }
  ];

  App.routes.companies = function (main) {
    var box = h("div");
    main.appendChild(h("div", { class: "row mb" }, h("p", { class: "muted", style: { margin: 0 } }, "一个主体就是一家可以独立投标的公司。集团可以添加多家子公司，资料互相隔离。订阅决定系统每天给这个主体推荐哪些标讯。"), h("span", { class: "spacer" }),
      App.can("manager", "sales") ? h("button", { class: "btn primary", on: { click: function () { editCompany(null); } } }, "添加主体") : null));
    main.appendChild(box);
    function load() {
      App.loadCompanies().then(function (list) {
        App.clear(box);
        if (!list.length) { box.appendChild(App.empty("还没有投标主体", "先添加你们公司的基本信息，投标函、授权书等格式文件会自动用这些信息填写")); return; }
        list.forEach(function (c) { box.appendChild(companyPanel(c)); });
      });
    }
    function companyPanel(c) {
      var subsBox = h("div");
      var p = App.panel(c.name, h("div", null,
        h("dl", { class: "kv mb" }, [["法定代表人", c.legal_person], ["统一社会信用代码", c.credit_code], ["注册资本", c.reg_capital], ["地址", c.address], ["联系人", [c.contact_person, c.contact_phone].filter(Boolean).join(" ")], ["中小企业", c.is_sme ? "是" : "否"], ["资料库", c.library_count + " 条"]].filter(function (x) { return x[1]; }).map(function (x) { return [h("dt", null, x[0]), h("dd", null, x[1])]; })),
        h("div", { class: "row mb" }, h("b", null, "订阅（" + c.subscription_count + "）"), h("span", { class: "spacer" }),
          h("button", { class: "btn sm", on: { click: function () { editSub(c, null); } } }, "新增订阅"),
          h("button", { class: "btn sm", title: "订阅改动后，对最近 7 天已采集的公告重新匹配", on: { click: function (e) {
            var b = e.currentTarget; App.busy(b, true, "匹配中");
            App.post("/api/subscriptions/rematch", { days: 7 }).then(function (r) { App.busy(b, false); App.toast("扫描 " + r.scanned + " 条公告，匹配 " + r.matched + " 条", "ok"); }, function () { App.busy(b, false); });
          } } }, "用最近 7 天公告重新匹配")),
        subsBox),
        h("div", { class: "row" }, h("button", { class: "btn sm", on: { click: function () { editCompany(c); } } }, "编辑信息"), h("a", { class: "btn sm", href: "#/library" }, "资料库")));
      App.get("/api/subscriptions?company_id=" + c.id).then(function (subs) {
        subsBox.appendChild(App.table([
          { label: "名称", render: function (s) { return h("a", { href: "#", on: { click: function (e) { e.preventDefault(); editSub(c, s); } } }, s.name); } },
          { label: "包含关键词", key: "keywords_include" },
          { label: "排除", key: "keywords_exclude" },
          { label: "地区", key: "regions" },
          { label: "预算（万元）", render: function (s) { return (s.budget_min || s.budget_max) ? (s.budget_min || 0) + " ~ " + (s.budget_max || "不限") : "不限"; } },
          { label: "状态", render: function (s) { return h("span", { class: "tag " + (s.enabled ? "ok" : "") }, s.enabled ? "启用" : "停用"); } },
          { label: "", render: function (s) { return h("button", { class: "btn sm", on: { click: function () { previewSub(s); } } }, "预览效果"); } }
        ], subs, { empty: "还没有订阅", emptyHint: "没有订阅时不会推荐任何标讯" }));
      });
      return p;
    }
    function editCompany(c) {
      var f = App.form(COMPANY_FIELDS, c || {});
      App.modal({ title: c ? "编辑主体" : "添加投标主体", wide: true, body: f.el, buttons: [
        c && App.can() ? { text: "删除主体", danger: true, onClick: function () { return App.confirm("删除主体「" + c.name + "」及其资料库、订阅？有投标项目的主体不能删除。", "删除").then(function (ok) { if (ok) return App.del("/api/companies/" + c.id).then(load); return false; }); } } : null,
        { text: "取消" },
        { text: "保存", primary: true, onClick: function () {
          var pr = c ? App.put("/api/companies/" + c.id, f.values()) : App.post("/api/companies", f.values());
          return pr.then(function () { App.toast("已保存", "ok"); load(); });
        } }].filter(Boolean) });
    }
    function editSub(c, s) {
      var f = App.form([
        { key: "name", label: "订阅名称", full: true, placeholder: "如：消防工程、医疗设备维保" },
        { key: "keywords_include", label: "包含关键词（任一命中即可，逗号分隔）", full: true, hint: "建议写具体的业务词：如「消防, 消防设施, 火灾报警, 喷淋」" },
        { key: "keywords_exclude", label: "排除关键词（标题含有就不推）", full: true, hint: "如「食堂, 物业, 车辆」" },
        { key: "regions", label: "地区（省/市名，逗号分隔，留空不限）", full: true },
        { key: "categories", label: "品目关键词（如：货物, 工程）" },
        { key: "purchaser_keywords", label: "采购人关键词（如：医院, 学校）" },
        { key: "budget_min", label: "预算下限（万元）", type: "number" },
        { key: "budget_max", label: "预算上限（万元）", type: "number" },
        { key: "notice_types", label: "公告类型", type: "select", options: [["tender", "只看招标/采购公告"], ["tender,change", "招标 + 更正"], ["tender,intention", "招标 + 采购意向"], ["tender,change,award,fail,intention", "全部类型"]] },
        { key: "enabled", label: "启用", type: "checkbox", default: true }
      ], s || { enabled: 1 });
      App.modal({ title: (s ? "编辑订阅 · " : "新增订阅 · ") + (c.short_name || c.name), wide: true, body: f.el, buttons: [
        s ? { text: "删除", danger: true, onClick: function () { return App.del("/api/subscriptions/" + s.id).then(load); } } : null,
        { text: "取消" },
        { text: "保存", primary: true, onClick: function () {
          var v = f.values(); v.company_id = c.id;
          var pr = s ? App.put("/api/subscriptions/" + s.id, v) : App.post("/api/subscriptions", v);
          return pr.then(function () { App.toast("已保存。点「用最近 7 天公告重新匹配」可以立即看到效果", "ok"); load(); });
        } }].filter(Boolean) });
    }
    function previewSub(s) {
      App.post("/api/subscriptions/" + s.id + "/preview").then(function (d) {
        App.modal({ title: "订阅预览：" + s.name, wide: true, body: h("div", null,
          h("p", null, "在最近 30 天已采集的 " + d.total_scanned + " 条公告里，能匹配到 ", h("b", null, String(d.count)), " 条（下面是得分最高的 30 条）。"),
          App.table([{ label: "公告", key: "title" }, { label: "发布", render: function (x) { return (x.publish_time || "").slice(0, 10); } }, { label: "理由", render: function (x) { return h("span", { class: "small muted" }, x.reasons.join("；")); } }], d.items, { empty: "一条都没匹配到", emptyHint: "关键词可能太窄，或者采集的公告还不够多" })) });
      });
    }
    load();
  };

  // ------------------------------------------------------------ 平台账号与 CA 台账
  App.routes.accounts = function (main) {
    var box = h("div");
    main.appendChild(h("div", { class: "req mb" }, "记录各投标平台的注册情况和 CA 数字证书（U 盾）的持有人、到期日，到期前系统会提醒。系统不保存任何平台密码或 CA 口令。"));
    main.appendChild(h("div", { class: "row mb" }, h("span", { class: "spacer" }), App.can("manager", "sales") ? h("button", { class: "btn primary", on: { click: function () { edit(null); } } }, "新增平台账号") : null));
    main.appendChild(box);
    function load() {
      App.get("/api/platform_accounts").then(function (rows) {
        App.clear(box).appendChild(App.panel(null, App.table([
          { label: "主体", key: "company_name" },
          { label: "平台", render: function (a) { return a.url ? h("a", { href: a.url, target: "_blank", rel: "noopener" }, a.platform_name) : a.platform_name; } },
          { label: "账号名", key: "account_name" },
          { label: "CA 类型", key: "ca_type" },
          { label: "CA 持有人", key: "ca_holder" },
          { label: "CA 到期", render: function (a) {
            if (!a.ca_expire) return "";
            var d = App.daysLeft(a.ca_expire);
            return h("span", { class: "tag " + (d < 0 ? "red" : d <= 30 ? "amber" : "") }, a.ca_expire);
          } },
          { label: "备注", key: "note" },
          { label: "", render: function (a) { return h("button", { class: "btn sm", on: { click: function () { edit(a); } } }, "编辑"); } }
        ], rows, { empty: "还没有平台账号", emptyHint: "建议把常投的交易平台都登记上，避免临近截止才发现没注册或 CA 过期" })));
      });
    }
    function edit(a) {
      var f = App.form([
        { key: "company_id", label: "主体", type: "select", options: App.state.companies.map(function (c) { return [c.id, c.name]; }) },
        { key: "platform_name", label: "平台名称" },
        { key: "url", label: "平台网址", full: true },
        { key: "account_name", label: "账号名（不填密码）" },
        { key: "ca_type", label: "CA 类型（如：某 CA 中心 U 盾）" },
        { key: "ca_holder", label: "CA 持有人/保管人" },
        { key: "ca_expire", label: "CA 到期日", type: "date" },
        { key: "note", label: "备注", full: true }
      ], a || { company_id: App.state.cid || (App.state.companies[0] || {}).id });
      App.modal({ title: a ? "编辑平台账号" : "新增平台账号", body: f.el, buttons: [
        a ? { text: "删除", danger: true, onClick: function () { return App.del("/api/platform_accounts/" + a.id).then(load); } } : null,
        { text: "取消" },
        { text: "保存", primary: true, onClick: function () {
          var v = f.values();
          var pr = a ? App.put("/api/platform_accounts/" + a.id, v) : App.post("/api/platform_accounts", v);
          return pr.then(load);
        } }].filter(Boolean) });
    }
    load();
  };
})();
