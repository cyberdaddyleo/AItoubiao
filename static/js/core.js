/* 核心：DOM 构建、接口调用、弹窗、表单、路由、外壳。
   约定：不用模板字符串；页面交互统一走 data-act 事件委托或在 h() 里绑定 on 事件。 */
(function () {
  "use strict";

  var App = (window.App = {});
  var STATE = (App.state = { me: null, companies: [], cid: 0, roles: {}, systemName: "AI智能投标系统" });
  var ROUTES = (App.routes = {});
  var ACTS = (App.acts = {});

  // 开发者署名水印（侧栏底部、登录页、退出页）
  var DEV_MARK = "@赛博老爸 cyberdaddy";
  function devMark() { return h("div", { class: "dev-mark", title: "开发者" }, DEV_MARK); }

  // ------------------------------------------------------------ DOM
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "html") el.innerHTML = v; // 仅用于受信任的静态内容
        else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
        else if (k === "on") Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); });
        else if (k === "dataset") Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; });
        else if (k === "value") el.value = v;
        else if (k === "checked") el.checked = !!v;
        else if (k === "disabled") el.disabled = !!v;
        else el.setAttribute(k, v === true ? "" : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { append(el, x); }); return; }
    if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  App.h = h; App.clear = clear;

  // ------------------------------------------------------------ 格式化
  App.fmtAmount = function (v) {
    if (v === null || v === undefined || v === "") return "";
    var n = Number(v);
    if (isNaN(n)) return "";
    if (Math.abs(n) >= 10000) return (n / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " 万元";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " 元";
  };
  App.toDate = function (s) {
    if (!s) return null;
    var m = String(s).match(/(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], m[4] ? +m[4] : 23, m[5] ? +m[5] : 59);
  };
  App.daysLeft = function (s) {
    var d = App.toDate(s);
    if (!d) return null;
    var now = new Date();
    var a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var b = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((b - a) / 86400000);
    if (days === 0 && d < now) return -0.5;
    return days;
  };
  // 印章倒计时
  App.seal = function (deadline, small) {
    var d = App.daysLeft(deadline);
    var cls = "seal" + (small ? " sm" : "");
    if (d === null) return h("div", { class: cls + " unk", title: "截止时间未知（抓取详情或上传招标文件后显示）" }, h("b", null, "未知"), h("small", null, "截止"));
    if (d < 0) return h("div", { class: cls + " over", title: "已截止：" + deadline }, h("b", null, "止"), h("small", null, "已截止"));
    if (d === 0) return h("div", { class: cls, title: "今天截止：" + deadline }, h("b", null, "今"), h("small", null, "天截止"));
    var tone = d <= 3 ? "" : d <= 7 ? " mid" : " far";
    return h("div", { class: cls + tone, title: "截止：" + deadline }, h("b", null, String(d)), h("small", null, "天截止"));
  };
  App.level = function (lv, by) {
    return h("span", { class: "lv " + (lv || "待评估"), title: by === "ai" ? "AI 复核结论" : "规则初判" }, (lv || "待评估") + (by === "ai" ? " · AI" : ""));
  };
  App.short = function (s, n) { s = s || ""; return s.length > n ? s.slice(0, n) + "…" : s; };

  // ------------------------------------------------------------ 提示
  App.toast = function (msg, type) {
    var el = h("div", { class: "toast " + (type || "") }, msg);
    document.getElementById("toasts").appendChild(el);
    setTimeout(function () { el.remove(); }, type === "err" ? 6000 : 3000);
  };

  // ------------------------------------------------------------ 接口
  App.api = function (method, url, body, opts) {
    opts = opts || {};
    var init = { method: method, credentials: "same-origin", headers: {} };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined && body !== null) { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
    return fetch(url, init).then(function (r) {
      if (r.status === 401 && !opts.allow401) { App.showLogin(); throw new Error("请先登录"); }
      if (opts.raw) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || ("HTTP " + r.status)); });
        return r;
      }
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = { detail: t }; }
        if (!r.ok) {
          var msg = (data && data.detail) || ("请求失败（HTTP " + r.status + "）");
          if (typeof msg !== "string") msg = JSON.stringify(msg);
          if (!opts.quiet) App.toast(msg, "err");
          var err = new Error(msg); err.status = r.status; throw err;
        }
        return data;
      });
    });
  };
  App.get = function (u, o) { return App.api("GET", u, null, o); };
  App.post = function (u, b, o) { return App.api("POST", u, b || {}, o); };
  App.put = function (u, b, o) { return App.api("PUT", u, b || {}, o); };
  App.del = function (u, o) { return App.api("DELETE", u, null, o); };
  App.qs = function (obj) {
    var p = [];
    Object.keys(obj).forEach(function (k) { if (obj[k] !== "" && obj[k] !== null && obj[k] !== undefined) p.push(encodeURIComponent(k) + "=" + encodeURIComponent(obj[k])); });
    return p.length ? "?" + p.join("&") : "";
  };

  // 下载文件（带 cookie）
  App.download = function (url, btn) {
    if (btn) busy(btn, true);
    return App.api("GET", url, null, { raw: true }).then(function (r) {
      var cd = r.headers.get("content-disposition") || "";
      var m = cd.match(/filename\*=utf-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
      var name = m ? decodeURIComponent(m[1]) : "下载文件";
      return r.blob().then(function (b) {
        var a = h("a", { href: URL.createObjectURL(b), download: name });
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
      });
    }).catch(function (e) { App.toast(e.message, "err"); }).then(function () { if (btn) busy(btn, false); });
  };

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) {
      btn.dataset.label = btn.textContent;
      btn.disabled = true;
      clear(btn); btn.appendChild(h("span", { class: "spin" })); btn.appendChild(document.createTextNode(label || "处理中"));
    } else {
      btn.disabled = false;
      btn.textContent = btn.dataset.label || btn.textContent;
    }
  }
  App.busy = busy;

  // 后台任务轮询
  App.pollJob = function (jobId, onUpdate) {
    return new Promise(function (resolve, reject) {
      function tick() {
        App.get("/api/jobs/" + jobId, { quiet: true }).then(function (j) {
          if (onUpdate) onUpdate(j);
          if (j.status === "done") resolve(j);
          else if (j.status === "error") reject(new Error(j.message || "任务失败"));
          else setTimeout(tick, 1500);
        }).catch(function () { setTimeout(tick, 3000); });
      }
      tick();
    });
  };
  // 带进度弹窗的任务
  App.runJob = function (title, promise) {
    var msg = h("div", { class: "pre muted" }, "已提交，正在处理…");
    var bar = h("i", { style: { width: "5%" } });
    var m = App.modal({ title: title, body: h("div", null, h("div", { class: "bar mb" }, bar), msg), buttons: [{ text: "在后台继续", onClick: function () { m.close(); } }] });
    return promise.then(function (res) {
      if (!res || !res.job_id) { m.close(); return res; }
      return App.pollJob(res.job_id, function (j) {
        msg.textContent = j.message || "处理中…";
        var pct = j.total ? Math.max(5, Math.round((j.progress / j.total) * 100)) : 30;
        bar.style.width = pct + "%";
      }).then(function (j) { m.close(); return j; }, function (e) { m.close(); App.toast(e.message, "err"); throw e; });
    }, function (e) { m.close(); throw e; });
  };

  // ------------------------------------------------------------ 弹窗 / 抽屉
  App.modal = function (o) {
    var mask = h("div", { class: "mask modal-mask" });
    var foot = h("div", { class: "mf" });
    var box = h("div", { class: "modal" + (o.wide ? " wide" : ""), role: "dialog" },
      h("div", { class: "mh" }, o.title || "", h("button", { class: "x", title: "关闭", on: { click: close } }, "×")),
      h("div", { class: "mb" }, o.body),
      foot);
    function close() { mask.remove(); box.remove(); document.removeEventListener("keydown", esc); if (o.onClose) o.onClose(); }
    function esc(e) { if (e.key === "Escape") close(); }
    (o.buttons || [{ text: "关闭" }]).forEach(function (b) {
      var btn = h("button", { class: "btn" + (b.primary ? " primary" : "") + (b.danger ? " danger" : "") }, b.text);
      btn.addEventListener("click", function () {
        if (!b.onClick) { close(); return; }
        var r = b.onClick(btn, close);
        if (r && r.then) {
          busy(btn, true);
          r.then(function (v) { busy(btn, false); if (v !== false) close(); }, function () { busy(btn, false); });
        } else if (r !== false) close();
      });
      foot.appendChild(btn);
    });
    mask.addEventListener("click", close);
    document.body.appendChild(mask);
    document.body.appendChild(box);
    document.addEventListener("keydown", esc);
    var first = box.querySelector("input,textarea,select");
    if (first) setTimeout(function () { first.focus(); }, 30);
    return { close: close, el: box };
  };
  App.confirm = function (msg, okText) {
    return new Promise(function (resolve) {
      App.modal({ title: "请确认", body: h("div", { class: "pre" }, msg), buttons: [
        { text: "取消", onClick: function () { resolve(false); } },
        { text: okText || "确定", primary: true, onClick: function () { resolve(true); } }
      ] });
    });
  };
  App.drawer = function (o) {
    var mask = h("div", { class: "mask" });
    var body = h("div", { class: "db" });
    var foot = h("div", { class: "df" });
    var head = h("div", { class: "dh" }, o.head || h("h2", null, o.title || ""), h("button", { class: "x", title: "关闭", on: { click: close } }, "×"));
    var box = h("div", { class: "drawer", role: "dialog" }, head, body, foot);
    function close() { mask.remove(); box.remove(); document.removeEventListener("keydown", esc); if (o.onClose) o.onClose(); }
    function esc(e) { if (e.key === "Escape" && !document.querySelector(".modal")) close(); }
    mask.addEventListener("click", close);
    document.body.appendChild(mask);
    document.body.appendChild(box);
    document.addEventListener("keydown", esc);
    return { close: close, body: body, foot: foot, head: head, el: box };
  };

  // ------------------------------------------------------------ 表单
  // fields: [{key,label,type,options,full,hint,placeholder,rows}]
  App.form = function (fields, values) {
    values = values || {};
    var inputs = {};
    var el = h("div", { class: "form" });
    fields.forEach(function (f) {
      var v = values[f.key];
      var inp;
      if (f.type === "select") {
        inp = h("select", null, (f.options || []).map(function (o) {
          var val = Array.isArray(o) ? o[0] : o, lab = Array.isArray(o) ? o[1] : o;
          return h("option", { value: val, selected: String(v === undefined ? f.default : v) === String(val) }, lab);
        }));
      } else if (f.type === "textarea") {
        inp = h("textarea", { rows: f.rows || 4, placeholder: f.placeholder || "" });
        inp.value = v === undefined || v === null ? "" : v;
      } else if (f.type === "checkbox") {
        inp = h("input", { type: "checkbox", checked: v === undefined ? f.default : !!Number(v) || v === true });
      } else {
        inp = h("input", { type: f.type || "text", placeholder: f.placeholder || "" });
        inp.value = v === undefined || v === null ? (f.default || "") : v;
      }
      inputs[f.key] = inp;
      var lab;
      if (f.type === "checkbox") lab = h("label", { class: "chk" + (f.full ? " full" : "") }, inp, f.label);
      else lab = h("label", { class: "fld" + (f.full ? " full" : "") }, h("span", null, f.label), inp, f.hint ? h("em", null, f.hint) : null);
      el.appendChild(lab);
    });
    return {
      el: el,
      inputs: inputs,
      values: function () {
        var out = {};
        fields.forEach(function (f) {
          var i = inputs[f.key];
          out[f.key] = f.type === "checkbox" ? (i.checked ? 1 : 0) : i.value;
        });
        return out;
      }
    };
  };

  App.table = function (cols, rows, opts) {
    opts = opts || {};
    if (!rows || !rows.length) return App.empty(opts.empty || "暂无数据", opts.emptyHint);
    return h("table", { class: "t" },
      h("thead", null, h("tr", null, cols.map(function (c) { return h("th", { class: c.cls || "" }, c.label); }))),
      h("tbody", null, rows.map(function (r) {
        var tr = h("tr", opts.onRow ? { style: { cursor: "pointer" }, on: { click: function (e) { if (!e.target.closest("button,a,input,select")) opts.onRow(r); } } } : null);
        cols.forEach(function (c) {
          var v = c.render ? c.render(r) : r[c.key];
          tr.appendChild(h("td", { class: c.cls || "" }, v === null || v === undefined ? "" : v));
        });
        return tr;
      })));
  };
  App.empty = function (title, hint, action) {
    return h("div", { class: "empty" }, h("b", null, title), hint ? h("div", null, hint) : null, action ? h("div", { class: "mt" }, action) : null);
  };
  App.tabs = function (items, current, onChange) {
    var el = h("div", { class: "tabs", role: "tablist" });
    items.forEach(function (it) {
      el.appendChild(h("button", { class: it.key === current ? "on" : "", role: "tab", on: { click: function () { onChange(it.key); } } }, it.label));
    });
    return el;
  };
  App.panel = function (title, body, tools) {
    return h("div", { class: "panel" }, title !== null ? h("div", { class: "hd" }, h("h3", null, title), h("span", { class: "spacer" }), tools || null) : null, h("div", { class: "bd" }, body));
  };

  // 文件选择与上传
  App.pickFiles = function (multiple, accept) {
    return new Promise(function (resolve) {
      var inp = h("input", { type: "file", multiple: !!multiple, accept: accept || "" });
      inp.addEventListener("change", function () { resolve(Array.prototype.slice.call(inp.files)); });
      inp.click();
    });
  };
  App.upload = function (file, kind) {
    var fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind || "");
    return App.api("POST", "/api/files", fd);
  };
  App.fileLink = function (f) {
    return h("a", { href: "#", on: { click: function (e) { e.preventDefault(); App.download("/api/files/" + f.id); } } }, f.filename || ("文件" + f.id));
  };

  // ------------------------------------------------------------ 简化 Markdown 安全渲染
  App.md = function (text) {
    var root = h("div", { class: "md" });
    var lines = String(text || "").split("\n");
    var i = 0;
    function inline(parent, s) {
      var parts = s.split(/(\*\*[^*]+\*\*|【待补充[^】]*】)/);
      parts.forEach(function (p) {
        if (!p) return;
        if (p.indexOf("**") === 0 && p.lastIndexOf("**") === p.length - 2 && p.length > 4) parent.appendChild(h("b", null, p.slice(2, -2)));
        else if (p.indexOf("【待补充") === 0) parent.appendChild(h("span", { class: "ph" }, p));
        else parent.appendChild(document.createTextNode(p));
      });
      return parent;
    }
    while (i < lines.length) {
      var s = lines[i].trim();
      if (!s) { i++; continue; }
      if (s.charAt(0) === "|") {
        var tb = h("tbody");
        while (i < lines.length && lines[i].trim().charAt(0) === "|") {
          var cells = lines[i].trim().replace(/^\||\|$/g, "").split("|");
          var isSep = cells.every(function (c) { return /^\s*:?-{2,}:?\s*$/.test(c) || !c.trim(); });
          if (!isSep) tb.appendChild(h("tr", null, cells.map(function (c) { return inline(h("td"), c.trim()); })));
          i++;
        }
        root.appendChild(h("table", null, tb));
        continue;
      }
      var m = s.match(/^(#{1,4})\s+(.*)$/);
      if (m) { root.appendChild(inline(h("h" + Math.min(4, m[1].length + 1)), m[2])); i++; continue; }
      m = s.match(/^!\[([^\]]*)\]\(file:(\d+)\)$/);
      if (m) { root.appendChild(h("div", null, h("img", { src: "/api/files/" + m[2] + "?inline=1", alt: m[1], loading: "lazy" }), h("div", { class: "small muted" }, m[1]))); i++; continue; }
      if (/^[-*•]\s/.test(s)) {
        var ul = h("ul");
        while (i < lines.length && /^[-*•]\s/.test(lines[i].trim())) { ul.appendChild(inline(h("li"), lines[i].trim().slice(2))); i++; }
        root.appendChild(ul);
        continue;
      }
      if (s.indexOf("> ") === 0) { root.appendChild(inline(h("blockquote"), s.slice(2))); i++; continue; }
      if (s === "[[分页]]") { root.appendChild(h("hr")); i++; continue; }
      root.appendChild(inline(h("p"), s));
      i++;
    }
    return root;
  };

  // ------------------------------------------------------------ 主体
  App.loadCompanies = function () {
    return App.get("/api/companies").then(function (list) {
      STATE.companies = list || [];
      var saved = 0;
      try { saved = +localStorage.getItem("bid_cid") || 0; } catch (e) { saved = 0; }
      if (!STATE.companies.some(function (c) { return c.id === STATE.cid; })) {
        STATE.cid = STATE.companies.some(function (c) { return c.id === saved; }) ? saved : 0;
      }
      return STATE.companies;
    });
  };
  App.companyName = function (id) {
    var c = STATE.companies.filter(function (x) { return x.id === id; })[0];
    return c ? (c.short_name || c.name) : "";
  };
  App.companySelect = function (value, onChange, allowAll) {
    var sel = h("select", { style: { width: "auto", minWidth: "160px" } },
      allowAll ? h("option", { value: "0" }, "全部主体") : null,
      STATE.companies.map(function (c) { return h("option", { value: c.id, selected: c.id === value }, c.short_name || c.name); }));
    sel.addEventListener("change", function () { onChange(+sel.value); });
    return sel;
  };
  App.can = function () {
    var role = STATE.me && STATE.me.role;
    if (role === "admin") return true;
    for (var i = 0; i < arguments.length; i++) if (arguments[i] === role) return true;
    return false;
  };

  // ------------------------------------------------------------ 外壳与路由
  var NAV = [
    { grp: "日常" },
    { hash: "#/dashboard", label: "工作台" },
    { hash: "#/notices", label: "标讯中心", badge: "matches" },
    { hash: "#/projects", label: "投标项目" },
    { grp: "资料" },
    { hash: "#/library", label: "企业资料库" },
    { hash: "#/companies", label: "主体与订阅" },
    { hash: "#/accounts", label: "平台账号与 CA" },
    { grp: "管理" },
    { hash: "#/stats", label: "数据看板" },
    { hash: "#/sources", label: "采集来源" },
    { hash: "#/settings", label: "系统设置", roles: ["admin"] }
  ];
  var TITLES = { dashboard: "工作台", notices: "标讯中心", projects: "投标项目", library: "企业资料库", companies: "主体与订阅", accounts: "平台账号与 CA 台账", stats: "数据看板", sources: "采集来源", settings: "系统设置" };

  var shell = {};
  function renderShell() {
    var root = clear(document.getElementById("root"));
    var nav = h("nav", { class: "nav" });
    NAV.forEach(function (n) {
      if (n.grp) { nav.appendChild(h("div", { class: "grp" }, n.grp)); return; }
      if (n.roles && n.roles.indexOf(STATE.me.role) < 0) return;
      var a = h("a", { href: n.hash, dataset: { nav: n.hash } }, n.label);
      if (n.badge) { shell.badge = h("span", { class: "cnt hidden" }); a.appendChild(shell.badge); }
      nav.appendChild(a);
    });
    shell.nav = nav;
    shell.title = h("h1");
    shell.bellCount = h("i", { class: "hidden" });
    shell.notes = null;
    var bell = h("button", { class: "btn bell", title: "通知", on: { click: toggleNotes } }, "通知", shell.bellCount);
    var userBtn = h("button", { class: "btn", on: { click: userMenu } }, (STATE.me.display_name || STATE.me.username) + "（" + STATE.me.role_name + "）");
    shell.main = h("main", { class: "main", id: "main" });
    root.appendChild(h("div", { class: "app" },
      h("aside", { class: "side" },
        h("div", { class: "brand" }, h("b", null, STATE.systemName), h("small", null, "找标 · 读标 · 写标 · 管投标")),
        nav,
        h("div", { class: "side-foot" }, h("div", null, STATE.desktop ? "本机版 · 数据只保存在这台电脑" : "数据存放在本机/内网服务器"), devMark())),
      h("div", null,
        h("header", { class: "top" }, shell.title, h("span", { class: "grow" }), bell, userBtn),
        shell.main)));
    refreshBell();
    setInterval(refreshBell, 60000);
  }

  function refreshBell() {
    App.get("/api/notifications?unread=1", { quiet: true }).then(function (d) {
      shell.bellCount.textContent = d.unread;
      shell.bellCount.classList.toggle("hidden", !d.unread);
    }).catch(function () {});
    App.get("/api/stats/dashboard", { quiet: true }).then(function (d) {
      if (!shell.badge) return;
      shell.badge.textContent = d.matches_new;
      shell.badge.classList.toggle("hidden", !d.matches_new);
    }).catch(function () {});
  }
  App.refreshBell = refreshBell;

  function toggleNotes() {
    if (shell.notes) { shell.notes.remove(); shell.notes = null; return; }
    var box = h("div", { class: "notes" }, h("div", { class: "empty" }, "加载中…"));
    shell.notes = box;
    document.body.appendChild(box);
    App.get("/api/notifications").then(function (d) {
      clear(box);
      box.appendChild(h("div", { class: "row", style: { padding: "8px 14px", borderBottom: "1px solid var(--line)" } }, h("b", null, "通知"), h("span", { class: "spacer" }),
        h("button", { class: "btn sm", on: { click: function () { App.post("/api/notifications/read", {}).then(function () { toggleNotes(); refreshBell(); }); } } }, "全部已读")));
      if (!d.items.length) box.appendChild(App.empty("没有通知"));
      d.items.forEach(function (n) {
        box.appendChild(h("div", { class: "note" + (n.read ? "" : " unread"), on: { click: function () {
          App.post("/api/notifications/read", { ids: [n.id] });
          if (n.link) location.hash = n.link;
          toggleNotes(); refreshBell();
        } } }, h("b", null, n.title), n.body ? h("div", { class: "small pre muted" }, App.short(n.body, 240)) : null, h("small", null, n.created_at)));
      });
    });
  }

  function userMenu() {
    var f = App.form([
      { key: "old_password", label: "原密码", type: "password" },
      { key: "new_password", label: "新密码（至少 8 位，含字母和数字）", type: "password" }
    ]);
    var desk = null;
    if (STATE.desktop) {
      desk = h("div", { class: "req mb" },
        h("div", null, "本机版：数据只保存在这台电脑上。"),
        h("div", { class: "small" }, "数据位置：" + STATE.dataDir),
        h("div", { class: "small" }, "关闭浏览器标签不会停止后台（定时采集照常进行）。要停止，点下面的「退出程序」，或再次双击「AI投标系统」选择「停止运行」。"));
    }
    var buttons = [];
    if (STATE.desktop && App.can()) {
      buttons.push({ text: "退出程序", danger: true, onClick: function () {
        return App.confirm("退出后定时采集也会停止。下次双击「AI投标系统」即可重新打开。", "退出程序").then(function (ok) {
          if (!ok) return false;
          return App.post("/api/desktop/quit").then(function () {
            var root = App.clear(document.getElementById("root"));
            root.appendChild(h("div", { class: "login-wrap" }, h("div", { class: "login" }, h("h2", null, "已退出"), h("p", { class: "muted" }, "AI投标系统已停止运行，可以关闭这个页面了。下次双击「AI投标系统」即可重新打开。")), devMark()));
          });
        });
      } });
    } else {
      buttons.push({ text: "退出登录", danger: true, onClick: function () { return App.post("/api/auth/logout").then(function () { location.reload(); }); } });
    }
    buttons.push({ text: "修改密码", primary: true, onClick: function () { return App.post("/api/auth/password", f.values()).then(function () { App.toast("密码已修改", "ok"); }); } });
    App.modal({ title: "账号", body: h("div", null, desk, h("p", null, "当前登录：" + STATE.me.username + "（" + STATE.me.role_name + "）"), h("h4", null, "修改密码"), f.el), buttons: buttons });
  }

  App.parseHash = function () {
    var raw = (location.hash || "#/dashboard").slice(2);
    var q = {};
    var qi = raw.indexOf("?");
    if (qi >= 0) {
      raw.slice(qi + 1).split("&").forEach(function (kv) { var p = kv.split("="); q[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || ""); });
      raw = raw.slice(0, qi);
    }
    var parts = raw.split("/").filter(Boolean);
    return { page: parts[0] || "dashboard", id: parts[1] ? +parts[1] || parts[1] : null, sub: parts[2] || null, q: q };
  };

  function route() {
    if (!STATE.me) return;
    var r = App.parseHash();
    if (shell.notes) { shell.notes.remove(); shell.notes = null; }
    // 切换页面时关闭残留的抽屉和弹窗（不触发它们的关闭回调）
    Array.prototype.forEach.call(document.querySelectorAll(".drawer, .mask, .modal"), function (x) { x.remove(); });
    Array.prototype.forEach.call(shell.nav.querySelectorAll("a"), function (a) {
      a.classList.toggle("on", a.dataset.nav === "#/" + r.page);
    });
    shell.title.textContent = TITLES[r.page] || "";
    var fn = ROUTES[r.page];
    clear(shell.main);
    window.scrollTo(0, 0);
    if (!fn) { shell.main.appendChild(App.empty("页面不存在")); return; }
    try { fn(shell.main, r); } catch (e) { console.error(e); shell.main.appendChild(App.empty("页面出错", e.message)); }
  }
  App.route = route;
  App.setTitle = function (t) { shell.title.textContent = t; };

  App.showLogin = function () {
    if (document.getElementById("login-user")) return;
    STATE.me = null;
    var user = h("input", { type: "text", id: "login-user", placeholder: "用户名", autocomplete: "username" });
    var pass = h("input", { type: "password", placeholder: "密码", autocomplete: "current-password" });
    var btn = h("button", { class: "btn primary", style: { width: "100%", height: "38px" }, type: "submit" }, "登录");
    var form = h("form", { class: "login" },
      h("h2", null, STATE.systemName),
      h("p", { class: "muted small" }, "首次使用：管理员账号 admin，初始密码在服务器 data 目录的「初始管理员密码.txt」里。"),
      h("label", { class: "fld mb" }, h("span", null, "用户名"), user),
      h("label", { class: "fld mb" }, h("span", null, "密码"), pass),
      btn);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      busy(btn, true, "登录中");
      App.post("/api/auth/login", { username: user.value, password: pass.value }, { allow401: true }).then(function () {
        boot();
      }, function () { busy(btn, false); });
    });
    var root = clear(document.getElementById("root"));
    root.appendChild(h("div", { class: "login-wrap" }, form, devMark()));
    setTimeout(function () { user.focus(); }, 30);
    App.get("/api/health", { quiet: true }).then(function (d) {
      if (d && d.desktop) {
        var tip = form.querySelector("p");
        tip.textContent = "本机版：重新双击「AI投标系统」即可自动登录。也可以用 admin 账号登录，初始密码在数据文件夹的「初始管理员密码.txt」里。";
      }
    }).catch(function () {});
  };

  function boot() {
    return App.get("/api/auth/me", { allow401: true, quiet: true }).then(function (d) {
      STATE.me = d.user; STATE.roles = d.roles; STATE.systemName = d.system_name || STATE.systemName; STATE.llm = !!d.llm_enabled;
      STATE.desktop = !!d.desktop; STATE.dataDir = d.data_dir || "";
      document.title = STATE.systemName;
      return App.loadCompanies();
    }).then(function () {
      renderShell();
      route();
    }).catch(function () { App.showLogin(); });
  }

  App.start = function () {
    window.addEventListener("hashchange", route);
    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-act]");
      if (el && ACTS[el.dataset.act]) { e.preventDefault(); ACTS[el.dataset.act](el, e); }
    });
    boot();
  };
})();
