// The research overview at the apex, as a Notion database: two tables, reports (every page you
// may open) and milestones (what the team works toward), each with views the team saves and
// shares (filter, sort, group, properties shown, layout: table, board or list). New writes a
// Markdown update (images and videos dropped in go straight to the bucket through presigned
// URLs; the gateway renders the page, uploads.py) or adds a milestone.
//
// Everything comes from /_api/overview. Text people wrote is set with textContent, never as
// HTML; the composer's preview is the rendered page in a sandboxed frame without scripts.
(function () {
  "use strict";

  var data = null;
  var viewer = document.body.getAttribute("data-viewer") || "";
  var state = { db: "reports", view: { reports: "all", milestones: "m-open" },
    draft: { reports: null, milestones: null }, query: "", pop: "", edit: "", menu: "", editQuery: "" };

  function view(id, name, db, config) {
    var base = { database: db, layout: "table", group: "", filters: {},
      sort: { field: db === "reports" ? "published" : "updated", dir: "desc" } };
    Object.keys(config).forEach(function (k) { base[k] = config[k]; });
    return { id: id, name: name, config: base };
  }
  var BUILTIN = {
    reports: [
      view("all", "All reports", "reports", { fields: ["title", "pillar", "milestone", "author", "published"] }),
      view("exploratory", "Exploratory", "reports", { fields: ["title", "pillar", "author", "published", "tags"],
        filters: { milestone: ["-"] } }),
      view("by-pillar", "By pillar", "reports", { layout: "board", group: "pillar",
        fields: ["title", "milestone", "author", "published"] })
    ],
    milestones: [
      view("m-open", "Open", "milestones", { group: "pillar", sort: { field: "target", dir: "asc" },
        fields: ["title", "kind", "owner", "target", "reports", "next"], filters: { status: ["open"] } }),
      view("m-learned", "Learned", "milestones", { fields: ["title", "status", "result", "owner", "updated"],
        filters: { status: ["achieved", "answered"] } }),
      view("m-board", "Board", "milestones", { layout: "board", group: "status",
        fields: ["title", "pillar", "owner", "reports"] })
    ]
  };
  var LAYOUTS = { table: "Table", board: "Board", list: "List" };
  var VIS = { private: "Private", ontic: "Team", public: "Public" };
  var STATUS = { open: "Open", achieved: "Achieved", answered: "Answered", dropped: "Dropped" };
  var KIND = { capability: "Capability", question: "Question" };
  var ICON = {
    capability: '<path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6"/><path d="M12 17.2v.1"/>',
    check: '<path d="M5 12.5l4.2 4.2L19 7"/>',
    cross: '<path d="M6 6l12 12M18 6L6 18"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    filter: '<path d="M4 6h16M7 12h10M10 18h4"/>',
    sort: '<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5"/>',
    group: '<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    table: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10"/>',
    board: '<rect x="3.5" y="4.5" width="5" height="15" rx="1.5"/><rect x="9.5" y="4.5" width="5" height="10" rx="1.5"/><rect x="15.5" y="4.5" width="5" height="12" rx="1.5"/>',
    list: '<path d="M8 6.5h12M8 12h12M8 17.5h12"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/>',
    more: '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M20 16l-5-5-8 8"/>',
    doc: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 16h6"/>',
    flag: '<path d="M5.5 21V4M5.5 4h11l-2 4 2 4h-11"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    chevron: '<path d="M7 10l5 5 5-5"/>'
  };

  // ---- small helpers ---------------------------------------------------------------------------

  function $(id) { return document.getElementById(id); }
  function h(tag, props) {
    var node = document.createElement(tag);
    var kids = Array.prototype.slice.call(arguments, 2);
    Object.keys(props || {}).forEach(function (key) {
      var value = props[key];
      if (value === undefined || value === null || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value === true ? "" : value);
    });
    kids.forEach(function add(kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) kid.forEach(add);
      else node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    });
    return node;
  }
  // Our own icon paths (constants above), never text from anyone.
  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICON[name];
    return svg;
  }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function when(iso, withTime) {
    var t = new Date(iso);
    if (!iso || isNaN(t.getTime())) return "";
    var day = two(t.getDate()) + "." + two(t.getMonth() + 1) + "." + t.getFullYear();
    return withTime === false ? day : day + " " + two(t.getHours()) + ":" + two(t.getMinutes());
  }
  function dateOnly(ymd) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd || "");
    return m ? m[3] + "." + m[2] + "." + m[1] : "";
  }
  function hue(text) {
    var n = 0;
    for (var i = 0; i < text.length; i++) n = (n * 31 + text.charCodeAt(i)) % 360;
    return "hsl(" + n + " 45% 48%)";
  }
  function person(email) { return String(email || "").split("@")[0]; }
  function avatar(email) {
    return h("span", { class: "avatar", style: "background:" + hue(email || "?"), title: email || "" },
      (person(email).charAt(0) || "?").toUpperCase());
  }
  function who(email) { return h("span", { class: "who" }, avatar(email), person(email)); }
  function pillarLabel(id) {
    var p = (data.pillars || []).filter(function (x) { return x.id === id; })[0];
    return p ? p.label : "Other";
  }
  function pc(id) { return "--pc: var(--p-" + (id || "other") + ")"; }
  function pillarTag(id) { return h("span", { class: "sel", style: pc(id) }, h("span", { class: "dot" }), pillarLabel(id)); }
  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem("ontic-overview:" + key);
      localStorage.setItem("ontic-overview:" + key, value);
    } catch (err) { return null; }
    return null;
  }
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function slug(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48)
      .replace(/-+$/, "");
  }
  function milestone(id) {
    return (data.milestones || []).filter(function (m) { return m.id === id; })[0] || null;
  }
  function titleOf(r) { return r.title || r.description || r.name; }
  function reportUrl(r) { return "/" + encodeURIComponent(r.name) + "/"; }
  function mine(r) { return r.author && r.author.toLowerCase() === viewer.toLowerCase(); }
  function linked(m) { return data.reports.filter(function (r) { return r.milestone === m.id; }); }
  function statusLabel(m) { return m.status === "answered" && m.negative ? "Answered, negative" : STATUS[m.status]; }

  function api(method, path, body) {
    var options = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (json) {
        if (!resp.ok) throw new Error(json.error || ("HTTP " + resp.status));
        return json;
      });
    });
  }

  var toastTimer = 0;
  function toast(message, link, bad) {
    var old = document.querySelector(".toast");
    if (old) old.remove();
    var action = link && link.onclick ? h("button", { class: "toast-act", type: "button", text: link.text,
      onclick: function () { node.remove(); link.onclick(); } }) : link ? h("a", { href: link.href, text: link.text }) : null;
    var node = h("div", { class: "toast" + (bad ? " bad" : ""), role: "status" }, h("span", { text: message }), action);
    document.body.appendChild(node);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { node.remove(); }, bad ? 7000 : 4500);
  }

  // ---- the two databases: their properties ---------------------------------------------------

  // Per property: label, icon, value (to sort), cell (the node shown), groups (keys and labels a
  // row falls in), options (what a filter can pick). Rows are reports or milestones.
  var PROPS = {
    reports: {
      title: { label: "Title", icon: "doc", value: function (r) { return titleOf(r).toLowerCase(); },
        cell: function (r) {
          return h("div", {}, h("a", { class: "t-title", href: reportUrl(r), text: titleOf(r) }),
            r.title && r.description ? h("div", { class: "t-desc", text: r.description }) : null);
        } },
      pillar: { label: "Pillar", value: function (r) { return pillarLabel(r.pillar); }, cell: function (r) { return pillarTag(r.pillar); },
        groups: function (r) { return [[r.pillar, pillarLabel(r.pillar)]]; },
        options: function () { return data.pillars.map(function (p) { return [p.id, p.label]; }); } },
      milestone: { label: "Milestone", icon: "flag", value: function (r) { var m = milestone(r.milestone); return m ? m.title : "~"; },
        cell: function (r) {
          var m = milestone(r.milestone);
          return m ? h("span", { class: "sel accent", title: m.title }, m.title) : h("span", { class: "faint", text: "Exploratory" });
        },
        groups: function (r) { var m = milestone(r.milestone); return [[r.milestone || "-", m ? m.title : "Exploratory"]]; },
        options: function () {
          return [["-", "Exploratory"]].concat(data.milestones.map(function (m) { return [m.id, m.title]; }));
        } },
      tags: { label: "Tags", value: function (r) { return (r.tags || []).join(" "); },
        cell: function (r) { return (r.tags || []).map(function (t) { return h("span", { class: "sel", text: t }); }); },
        groups: function (r) { return (r.tags || []).length ? r.tags.map(function (t) { return [t, t]; }) : [["", "No tags"]]; },
        options: function () {
          var all = {};
          data.reports.forEach(function (r) { (r.tags || []).forEach(function (t) { all[t] = true; }); });
          return Object.keys(all).sort().map(function (t) { return [t, t]; });
        } },
      author: { label: "Author", value: function (r) { return r.author; }, cell: function (r) { return who(r.author); },
        groups: function (r) { return [[r.author, person(r.author)]]; },
        options: function () { return people(data.reports.map(function (r) { return r.author; })); } },
      published: { label: "Published", value: function (r) { return r.published_at; },
        cell: function (r) { return h("span", { class: "muted nowrap", title: r.version, text: when(r.published_at) }); } },
      visibility: { label: "Visibility", value: function (r) { return r.visibility; },
        cell: function (r) { return h("span", { class: "muted", text: VIS[r.visibility] }); },
        groups: function (r) { return [[r.visibility, VIS[r.visibility]]]; },
        options: function () { return Object.keys(VIS).map(function (k) { return [k, VIS[k]]; }); } },
      name: { label: "Page", value: function (r) { return r.name; }, cell: function (r) { return h("span", { class: "muted", text: r.name }); } }
    },
    milestones: {
      title: { label: "Title", icon: "flag", value: function (m) { return m.title.toLowerCase(); },
        cell: function (m) {
          return h("span", { class: "t-title row" }, h("span", { class: "kind", style: pc(m.pillar), title: KIND[m.kind] }, icon(m.kind)),
            h("span", { text: m.title }), m.negative ? h("span", { class: "sel st-answered neg", text: "Negative" }) : null);
        } },
      status: { label: "Status", value: function (m) { return Object.keys(STATUS).indexOf(m.status); },
        cell: function (m) { return h("span", { class: "sel st-" + m.status + (m.negative ? " neg" : "") }, statusLabel(m)); },
        groups: function (m) { return [[m.status, STATUS[m.status]]]; },
        options: function () { return Object.keys(STATUS).map(function (k) { return [k, STATUS[k]]; }); } },
      kind: { label: "Kind", value: function (m) { return m.kind; }, cell: function (m) { return h("span", { class: "muted", text: KIND[m.kind] }); },
        groups: function (m) { return [[m.kind, KIND[m.kind]]]; },
        options: function () { return Object.keys(KIND).map(function (k) { return [k, KIND[k]]; }); } },
      pillar: { label: "Pillar", value: function (m) { return pillarLabel(m.pillar); }, cell: function (m) { return pillarTag(m.pillar); },
        groups: function (m) { return [[m.pillar, pillarLabel(m.pillar)]]; },
        options: function () { return data.pillars.map(function (p) { return [p.id, p.label]; }); } },
      owner: { label: "Owner", value: function (m) { return m.owner; }, cell: function (m) { return who(m.owner); },
        groups: function (m) { return [[m.owner, person(m.owner)]]; },
        options: function () { return people(data.milestones.map(function (m) { return m.owner; })); } },
      target: { label: "Target", value: function (m) { return m.target || "9999"; },
        cell: function (m) { return h("span", { class: "muted nowrap", text: dateOnly(m.target) }); } },
      reports: { label: "Reports", value: function (m) { return linked(m).length; },
        cell: function (m) {
          var n = linked(m).length;
          return h("span", { class: n ? "nowrap" : "faint nowrap", text: n ? n + (n === 1 ? " report" : " reports") : "None" });
        } },
      next: { label: "Next step", value: function (m) { return m.next; }, cell: function (m) { return h("span", { class: "clamp", text: m.next }); } },
      criteria: { label: "Done when", value: function (m) { return m.criteria; }, cell: function (m) { return h("span", { class: "clamp", text: m.criteria }); } },
      result: { label: "Result", value: function (m) { return m.result; }, cell: function (m) { return h("span", { class: "clamp", text: m.result }); } },
      updated: { label: "Updated", value: function (m) { return m.updated_at; },
        cell: function (m) { return h("span", { class: "muted nowrap", text: when(m.updated_at) }); } }
    }
  };
  function people(list) {
    var all = {};
    list.forEach(function (e) { if (e) all[e] = true; });
    return Object.keys(all).sort().map(function (e) { return [e, person(e)]; });
  }
  function rowsOf(db) { return db === "reports" ? data.reports : data.milestones; }
  function rowText(db, row) {
    if (db === "milestones") return [row.title, row.criteria, row.next, row.result, row.owner].join(" ");
    var m = milestone(row.milestone);
    return [row.title, row.description, row.name, row.author, (row.tags || []).join(" "), m ? m.title : ""].join(" ");
  }
  function filterValue(db, row, key) {
    if (db === "reports" && key === "milestone") return [row.milestone || "-"];
    if (key === "tags") return row.tags || [];
    return [row[key]];
  }

  // ---- views ---------------------------------------------------------------------------------

  function views(db) {
    return BUILTIN[db].concat((data.views || []).filter(function (v) {
      return (v.config.database || "reports") === db;
    }));
  }
  function currentView() {
    var db = state.db;
    return views(db).filter(function (v) { return v.id === state.view[db]; })[0] || BUILTIN[db][0];
  }
  function config() { return state.draft[state.db] || currentView().config; }
  function setConfig(change) {
    var next = copy(config());
    change(next);
    state.draft[state.db] = same(next, currentView().config) ? null : next;
    render();
  }

  function rows() {
    var db = state.db, c = config(), f = c.filters || {}, props = PROPS[db];
    var q = state.query.trim().toLowerCase();
    var out = rowsOf(db).filter(function (row) {
      if (q && rowText(db, row).toLowerCase().indexOf(q) < 0) return false;
      return Object.keys(f).every(function (key) {
        if (key === "text") return rowText(db, row).toLowerCase().indexOf(f.text.toLowerCase()) >= 0;
        if (key === "since") return new Date(row.published_at || row.updated_at).getTime() >= Date.now() - f.since * 864e5;
        var have = filterValue(db, row, key);
        return key === "tags" ? f.tags.every(function (t) { return have.indexOf(t) >= 0; })
          : f[key].some(function (v) { return have.indexOf(v) >= 0; });
      });
    });
    var prop = props[c.sort.field], dir = c.sort.dir === "asc" ? 1 : -1;
    out.sort(function (a, b) {
      var x = prop.value(a), y = prop.value(b);
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
    return out;
  }

  function groupsOf(list, key) {
    var prop = key && PROPS[state.db][key];
    if (!prop) return [{ key: "", label: "", items: list }];
    var groups = {}, order = [];
    list.forEach(function (row) {
      prop.groups(row).forEach(function (g) {
        if (!groups[g[0]]) { groups[g[0]] = { key: g[0], label: g[1], items: [] }; order.push(g[0]); }
        groups[g[0]].items.push(row);
      });
    });
    var known = prop.options ? prop.options().map(function (o) { return o[0]; }) : [];
    order.sort(function (a, b) {
      var i = known.indexOf(a), j = known.indexOf(b);
      if (i < 0) i = 1e6;
      if (j < 0) j = 1e6;
      return i - j || groups[a].label.localeCompare(groups[b].label);
    });
    return order.map(function (k) { return groups[k]; });
  }

  // ---- loading and drawing -------------------------------------------------------------------

  function load() {
    return api("GET", "/_api/overview").then(function (answer) {
      data = answer;
      if (!state.loaded) {
        state.loaded = true;
        ["reports", "milestones"].forEach(function (db) {
          var saved = store("view:" + db);
          if (saved && views(db).some(function (v) { return v.id === saved; })) state.view[db] = saved;
        });
        if (store("db") === "milestones" && data.team) state.db = "milestones";
      }
      render();
    }).catch(function (err) {
      var app = $("app");
      app.textContent = "";
      app.appendChild(h("div", { class: "page" }, h("p", { class: "error", text: "Could not load: " + err.message })));
    });
  }

  function render() {
    var app = $("app");
    var scroll = window.scrollY;
    var focused = document.activeElement && document.activeElement.id;
    app.textContent = "";
    var dbs = [["reports", "Reports", "doc", data.reports.length]];
    if (data.team) dbs.push(["milestones", "Milestones", "flag", data.milestones.length]);
    app.appendChild(h("div", { class: "page" },
      h("header", { class: "head" },
        h("h1", {}, h("span", { class: "mark", "aria-hidden": "true" }), "Ontic research"),
        h("span", { class: "grow" }), avatar(viewer)),
      dbs.length > 1 ? h("nav", { class: "dbs", "aria-label": "Tables" }, dbs.map(function (d) {
        return h("button", { class: "db", type: "button", "aria-pressed": String(state.db === d[0]),
          onclick: function () { state.db = d[0]; state.pop = ""; store("db", d[0]); render(); } },
          icon(d[2]), d[1], h("span", { class: "n", text: String(d[3]) }));
      })) : null,
      database()));
    window.scrollTo(0, scroll);
    if (focused && $(focused)) {
      var el = $(focused);
      el.focus();
      if (el.setSelectionRange && el.value) el.setSelectionRange(el.value.length, el.value.length);
    }
  }

  function database() {
    var c = config(), list = rows(), v = currentView(), db = state.db;
    var custom = !!v.created_by;
    var tabs = views(db).map(function (x) {
      var on = x.id === v.id;
      return h("button", { class: "vtab", type: "button", role: "tab", "aria-selected": String(on),
        title: x.created_by ? "Saved by " + x.created_by : "",
        onclick: function () {
          if (on && x.created_by) { state.pop = state.pop === "view" ? "" : "view"; render(); return; }
          state.view[db] = x.id; state.draft[db] = null; state.pop = ""; store("view:" + db, x.id); render();
        } }, icon(x.config.layout), x.name, on && state.draft[db] ? h("span", { class: "edited", title: "Changed, not saved" }) : null);
    });
    tabs.push(h("button", { class: "vtab add", type: "button", title: "New view", "aria-label": "New view",
      onclick: function () { newView(); } }, icon("plus")));
    var tools = h("div", { class: "tools" },
      state.draft[db] ? h("button", { class: "tb", type: "button", onclick: function () { state.draft[db] = null; render(); } }, "Reset") : null,
      state.draft[db] ? h("button", { class: "tb save", type: "button", onclick: function () {
        if (custom) saveView(v.id); else newView(true);
      } }, custom ? "Save for everyone" : "Save as view") : null,
      tool("filter", "Filter", activeFilters(c).length), tool("sort", "Sort"), tool("group", "Group", c.group ? 1 : 0),
      tool("props", "Properties"), tool("layout", LAYOUTS[c.layout]),
      h("label", { class: "find" }, icon("search"), h("input", { id: "q", type: "search", placeholder: "Search",
        value: state.query, "aria-label": "Search", oninput: function (e) { state.query = e.target.value; render(); } })),
      h("button", { class: "new", type: "button", onclick: function () {
        if (db === "reports") composer(null); else milestoneDialog(null);
      } }, icon("plus"), "New"));
    var bar = h("div", { class: "vbar" }, h("div", { class: "vtabs", role: "tablist" }, tabs,
      state.pop === "view" ? viewMenu(v) : null), tools);
    var chips = activeFilters(c);
    var filters = chips.length ? h("div", { class: "fchips" }, chips.map(function (f) {
      return h("button", { class: "fchip", type: "button", onclick: function () { state.pop = "filter"; render(); } },
        h("span", { class: "faint", text: f.label + ": " }), f.values,
        h("span", { class: "x", role: "button", "aria-label": "Remove filter", onclick: function (e) {
          e.stopPropagation();
          setConfig(function (n) { delete n.filters[f.key]; });
        } }, icon("cross")));
    })) : null;
    var body;
    if (!list.length) body = h("div", { class: "none", text: rowsOf(db).length ? "No rows match this view." : db === "reports"
      ? "No reports yet. Publish one, or press New." : "No milestones yet. Press New: a capability to show, or a question to resolve." });
    else if (c.layout === "board") body = board(list, c);
    else if (c.layout === "list") body = listLayout(list, c);
    else body = table(list, c);
    var gone = db === "reports" && (data.deleted || []).length;
    return h("section", { class: "dbview" }, bar, filters, body,
      h("div", { class: "count" }, list.length + (list.length === 1 ? " row" : " rows"),
        gone ? h("button", { class: "linkish", type: "button", onclick: function () { deletedDialog(); } },
          "Deleted (" + data.deleted.length + ")") : null));
  }

  function activeFilters(c) {
    var props = PROPS[state.db], f = c.filters || {};
    return Object.keys(f).filter(function (k) { return props[k] && props[k].options; }).map(function (k) {
      var names = {};
      props[k].options().forEach(function (o) { names[o[0]] = o[1]; });
      return { key: k, label: props[k].label, values: f[k].map(function (v) { return names[v] || v; }).join(", ") };
    });
  }

  // A toolbar button and, when open, its popover.
  function tool(key, label, count) {
    var open = state.pop === key;
    return h("div", { class: "popwrap" },
      h("button", { class: "tb" + (count ? " on" : ""), type: "button", "aria-expanded": String(open),
        onclick: function () { state.pop = open ? "" : key; render(); } },
        icon(key === "props" ? "eye" : key === "layout" ? config().layout : key), label),
      open ? h("div", { class: "pop", role: "dialog", "aria-label": label }, popover(key)) : null);
  }

  function popover(key) {
    var c = config(), props = PROPS[state.db];
    function item(label, on, onclick, extra) {
      return h("button", { class: "pitem", type: "button", "aria-pressed": String(!!on), onclick: onclick },
        extra || null, h("span", { class: "grow", text: label }), on ? icon("check") : null);
    }
    if (key === "filter") {
      return Object.keys(props).filter(function (k) { return props[k].options; }).map(function (k) {
        var chosen = (c.filters && c.filters[k]) || [];
        return h("div", { class: "psec" }, h("div", { class: "plabel", text: props[k].label }),
          h("div", { class: "chips" }, props[k].options().map(function (o) {
            var on = chosen.indexOf(o[0]) >= 0;
            return h("button", { class: "chip", type: "button", "aria-pressed": String(on),
              style: k === "pillar" ? pc(o[0]) : null,
              onclick: function () {
                setConfig(function (n) {
                  var list = (n.filters[k] || []).filter(function (x) { return x !== o[0]; });
                  if (!on) list.push(o[0]);
                  if (list.length) n.filters[k] = list; else delete n.filters[k];
                });
              } }, k === "pillar" ? h("span", { class: "dot" }) : null, o[1]);
          })));
      });
    }
    if (key === "sort") {
      return [h("div", { class: "plabel", text: "Sort by" })].concat(Object.keys(props).filter(function (k) {
        return sortable(k);
      }).map(function (k) {
        var on = c.sort.field === k;
        return item(props[k].label, on, function () {
          setConfig(function (n) { n.sort = { field: k, dir: on && n.sort.dir === "desc" ? "asc" : "desc" }; });
        }, on ? icon(c.sort.dir === "asc" ? "up" : "down") : h("span", { class: "sp" }));
      }), h("div", { class: "hint pad", text: "Click again to flip the order." }));
    }
    if (key === "group") {
      return [h("div", { class: "plabel", text: "Group by" }), item("None", !c.group, function () { setConfig(function (n) { n.group = ""; }); })]
        .concat(Object.keys(props).filter(function (k) { return props[k].groups; }).map(function (k) {
          return item(props[k].label, c.group === k, function () { setConfig(function (n) { n.group = k; }); });
        }));
    }
    if (key === "props") {
      return [h("div", { class: "plabel", text: "Shown" })].concat(Object.keys(props).map(function (k) {
        var on = c.fields.indexOf(k) >= 0;
        return item(props[k].label, on, function () {
          setConfig(function (n) {
            n.fields = on ? n.fields.filter(function (x) { return x !== k; })
              : Object.keys(props).filter(function (x) { return x === k || n.fields.indexOf(x) >= 0; });
          });
        });
      }));
    }
    return [h("div", { class: "plabel", text: "Layout" })].concat(Object.keys(LAYOUTS).map(function (k) {
      return item(LAYOUTS[k], c.layout === k, function () { setConfig(function (n) { n.layout = k; }); }, icon(k));
    }));
  }
  var SORTABLE = { reports: ["published", "title", "author", "pillar", "milestone", "name"],
    milestones: ["updated", "title", "target", "status", "pillar", "owner"] };
  function sortable(k) { return SORTABLE[state.db].indexOf(k) >= 0; }

  function viewMenu(v) {
    var mineView = (v.created_by || "").toLowerCase() === viewer.toLowerCase();
    return h("div", { class: "pop left", role: "menu" },
      h("div", { class: "hint pad", text: "Saved by " + v.created_by }),
      h("button", { class: "pitem", type: "button", onclick: function () { state.pop = ""; renameView(v); } }, icon("edit"), "Rename"),
      mineView ? h("button", { class: "pitem danger", type: "button", onclick: function () { state.pop = ""; deleteView(v); } }, icon("cross"), "Delete view") : null);
  }

  // ---- editing in place ------------------------------------------------------------------------

  // What can be changed in a cell, per table: the kind of editor and how it saves. Anyone on the
  // team may (the gateway checks again); reports keep their versions, only labels.json changes.
  var EDIT = {
    reports: {
      pillar: { type: "select", options: function () { return PROPS.reports.pillar.options(); },
        save: function (r, v) { saveLabels(r, { pillar: v }); } },
      milestone: { type: "select", search: true,
        options: function () { return [["", "Exploratory (no milestone)"]].concat(data.milestones.map(function (m) { return [m.id, m.title]; })); },
        value: function (r) { return r.milestone || ""; },
        save: function (r, v) { saveLabels(r, { milestone: v === "-" ? "" : v }); } },
      tags: { type: "multi", save: function (r, v) { saveLabels(r, { tags: v }); } },
      title: { type: "text", value: function (r) { return titleOf(r); }, save: function (r, v) { saveLabels(r, { title: v }); } }
    },
    milestones: {
      status: { type: "select", options: function () { return PROPS.milestones.status.options(); },
        save: function (m, v) {
          if (v === "achieved" || v === "answered") milestoneDialog(m, "close");
          else saveMilestoneField(m, { status: v });
        } },
      kind: { type: "select", options: function () { return PROPS.milestones.kind.options(); },
        save: function (m, v) { saveMilestoneField(m, { kind: v }); } },
      pillar: { type: "select", options: function () { return PROPS.milestones.pillar.options(); },
        save: function (m, v) { saveMilestoneField(m, { pillar: v }); } },
      owner: { type: "text", save: function (m, v) { saveMilestoneField(m, { owner: v }); } },
      target: { type: "date", save: function (m, v) { saveMilestoneField(m, { target: v }); } },
      next: { type: "text", save: function (m, v) { saveMilestoneField(m, { next: v }); } },
      title: { type: "text", value: function (m) { return m.title; }, save: function (m, v) { saveMilestoneField(m, { title: v }); } }
    }
  };
  function rowId(row) { return state.db === "reports" ? row.name : row.id; }
  function editable(key) { return data.team && EDIT[state.db][key]; }
  function editing(row, key) { return state.edit === rowId(row) + "\n" + key; }
  function startEdit(row, key) {
    return function (e) {
      if (!editable(key) || e.target.closest(".pop, a, .acts")) return;
      e.stopPropagation();
      state.edit = rowId(row) + "\n" + key;
      state.pop = state.menu = "";
      render();
    };
  }
  function stopEdit() { state.edit = ""; render(); }

  function editor(row, key) {
    var spec = EDIT[state.db][key];
    var value = spec.value ? spec.value(row) : row[key];
    var box = h("div", { class: "pop cell", role: "dialog", onclick: function (e) { e.stopPropagation(); } });
    if (spec.type === "text" || spec.type === "date") {
      var input = h("input", { id: "cell-input", type: spec.type === "date" ? "date" : "text", value: value || "" });
      var done = false;
      function commit() {
        if (done) return;
        done = true;
        var v = input.value.trim();
        state.edit = "";
        if (v !== (value || "") && (v || key !== "title")) spec.save(row, v); else render();
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { done = true; e.stopPropagation(); stopEdit(); }
      });
      input.addEventListener("change", function () { if (spec.type === "date") commit(); });
      box.appendChild(input);
      box.appendChild(h("div", { class: "hint pad", text: spec.type === "date" ? "Pick a date" : "Enter to save, Escape to cancel" }));
      return box;
    }
    if (spec.type === "select") {
      var options = spec.options();
      var search = spec.search && options.length > 6 ? h("input", { id: "cell-input", type: "text", placeholder: "Search",
        value: state.editQuery || "", oninput: function (e) { state.editQuery = e.target.value; render(); } }) : null;
      if (search) box.appendChild(search);
      var q = (state.editQuery || "").toLowerCase();
      options.filter(function (o) { return !q || o[1].toLowerCase().indexOf(q) >= 0; }).forEach(function (o) {
        var on = o[0] === (value || "");
        box.appendChild(h("button", { class: "pitem", type: "button", "aria-pressed": String(on), onclick: function () {
          state.edit = "";
          state.editQuery = "";
          if (on) render(); else spec.save(row, o[0]);
        } }, key === "pillar" ? h("span", { class: "dot", style: pc(o[0]) }) : null,
          key === "status" ? h("span", { class: "sel st-" + o[0], text: o[1] }) : h("span", { class: "grow", text: o[1] }),
          key === "status" ? h("span", { class: "grow" }) : null, on ? icon("check") : null));
      });
      return box;
    }
    // multi: the tags, with a box to find or create one.
    var tags = (row.tags || []).slice();
    var all = PROPS.reports.tags.options().map(function (o) { return o[0]; });
    var q2 = (state.editQuery || "").trim().toLowerCase().replace(/\s+/g, "-");
    function set(next) { state.editQuery = ""; spec.save(row, next); }
    var input2 = h("input", { id: "cell-input", type: "text", placeholder: "Find or create a tag", value: state.editQuery || "",
      oninput: function (e) { state.editQuery = e.target.value; render(); },
      onkeydown: function (e) {
        if (e.key === "Enter" && q2) { e.preventDefault(); if (tags.indexOf(q2) < 0) set(tags.concat([q2])); }
        if (e.key === "Backspace" && !e.target.value && tags.length) set(tags.slice(0, -1));
        if (e.key === "Escape") { e.stopPropagation(); state.editQuery = ""; stopEdit(); }
      } });
    box.appendChild(h("div", { class: "taglist" }, tags.map(function (t) {
      return h("span", { class: "sel" }, t, h("button", { class: "x", type: "button", "aria-label": "Remove " + t,
        onclick: function () { set(tags.filter(function (x) { return x !== t; })); } }, icon("cross")));
    }), input2));
    var free = all.filter(function (t) { return tags.indexOf(t) < 0 && (!q2 || t.indexOf(q2) >= 0); });
    if (q2 && all.indexOf(q2) < 0) {
      box.appendChild(h("button", { class: "pitem", type: "button", onclick: function () { set(tags.concat([q2])); } },
        icon("plus"), h("span", { class: "grow" }, "Create ", h("span", { class: "sel", text: q2 }))));
    }
    if (free.length) box.appendChild(h("div", { class: "plabel", text: "Pick one" }));
    free.slice(0, 12).forEach(function (t) {
      box.appendChild(h("button", { class: "pitem", type: "button", onclick: function () { set(tags.concat([t])); } },
        h("span", { class: "sel", text: t })));
    });
    return box;
  }

  // Shown at once, saved in the background; on an error the truth comes back from the server.
  function saveLabels(r, change) {
    Object.keys(change).forEach(function (k) { r[k] = change[k]; });
    render();
    api("POST", "/_api/pages/" + encodeURIComponent(r.name) + "/labels", change).then(function (answer) {
      Object.keys(answer.report).forEach(function (k) { r[k] = answer.report[k]; });
      render();
    }).catch(function (err) { toast(err.message, null, true); load(); });
  }
  function saveMilestoneField(m, change) {
    Object.keys(change).forEach(function (k) { m[k] = change[k]; });
    render();
    api("POST", "/_api/milestones/" + m.id, change).then(function (answer) {
      Object.keys(answer.milestone).forEach(function (k) { m[k] = answer.milestone[k]; });
      render();
    }).catch(function (err) { toast(err.message, null, true); load(); });
  }

  // ---- folding groups (remembered in this browser) --------------------------------------------

  var folded = {};
  try { folded = JSON.parse(store("folded") || "{}") || {}; } catch (err) { folded = {}; }
  function foldKey(group, key) { return [state.db, currentView().id, group, key].join("\n"); }
  function isFolded(group, key) { return !!folded[foldKey(group, key)]; }
  function toggleFold(group, key) {
    return function (e) {
      e.stopPropagation();
      var k = foldKey(group, key);
      if (folded[k]) delete folded[k]; else folded[k] = 1;
      store("folded", JSON.stringify(folded));
      render();
    };
  }
  function chevron(open) { return h("span", { class: "chev" + (open ? " open" : "") }, icon("chevron")); }

  // ---- layouts -------------------------------------------------------------------------------

  function open(row) {
    return function (e) {
      if (e.target.closest("button, a, .acts, .pop, input")) return;
      if (state.edit || state.menu) { state.edit = state.menu = ""; render(); return; }
      if (state.db === "reports") location.href = reportUrl(row);
      else milestoneDialog(row);
    };
  }

  // The row's menu: open, rename, edit an update, delete (the owner).
  function rowActions(row) {
    var id = rowId(row);
    var items = [];
    if (state.db === "reports") {
      items.push(h("a", { class: "pitem", href: reportUrl(row) }, icon("doc"), "Open"));
      if (data.team) items.push(h("button", { class: "pitem", type: "button", onclick: function () {
        state.menu = ""; state.edit = id + "\ntitle"; render();
      } }, icon("edit"), "Rename"));
      if (mine(row) && row.format === "markdown") items.push(h("button", { class: "pitem", type: "button",
        onclick: function () { state.menu = ""; render(); editUpdate(row); } }, icon("edit"), "Edit the update"));
      if (mine(row)) items.push(h("button", { class: "pitem danger", type: "button",
        onclick: function () { state.menu = ""; render(); confirmDelete(row); } }, icon("cross"), "Delete"));
    } else {
      items.push(h("button", { class: "pitem", type: "button", onclick: function () { state.menu = ""; render(); milestoneDialog(row); } },
        icon("edit"), "Open"));
    }
    return h("span", { class: "acts" + (state.menu === id ? " shown" : "") },
      h("button", { class: "icon", type: "button", title: "More", "aria-label": "More", "aria-expanded": String(state.menu === id),
        onclick: function (e) { e.stopPropagation(); state.menu = state.menu === id ? "" : id; state.edit = ""; render(); } }, icon("more")),
      state.menu === id ? h("div", { class: "pop menu", role: "menu" }, items) : null);
  }

  function cellNode(row, key) {
    if (editing(row, key)) return [PROPS[state.db][key].cell(row), editor(row, key)];
    return PROPS[state.db][key].cell(row);
  }

  function table(list, c) {
    var props = PROPS[state.db];
    var fields = c.fields.length ? c.fields : ["title"];
    var head = h("tr", {}, fields.map(function (key) {
      var sorted = c.sort.field === key;
      return h("th", { scope: "col", class: key === "title" ? "wide" : null, onclick: sortable(key) ? function () {
        setConfig(function (n) { n.sort = { field: key, dir: sorted && n.sort.dir === "desc" ? "asc" : "desc" }; });
      } : null }, h("span", { class: "row" }, props[key].icon ? icon(props[key].icon) : null, props[key].label,
        sorted ? icon(c.sort.dir === "asc" ? "up" : "down") : null));
    }));
    var body = [];
    groupsOf(list, c.group).forEach(function (g) {
      var shut = c.group && isFolded(c.group, g.key);
      if (c.group) {
        body.push(h("tr", { class: "grow-row", onclick: toggleFold(c.group, g.key) }, h("td", { colspan: String(fields.length) },
          h("span", { class: "row", style: c.group === "pillar" ? pc(g.key) : null }, chevron(!shut),
            c.group === "pillar" ? h("span", { class: "dot" }) : null,
            h("b", { text: g.label }), h("span", { class: "faint", text: String(g.items.length) })))));
      }
      if (shut) return;
      g.items.forEach(function (row) {
        body.push(h("tr", { class: "r", onclick: open(row) }, fields.map(function (key, i) {
          var cls = (key === "title" ? "wide" : "") + (editable(key) && key !== "title" ? " ed" : "") + (editing(row, key) ? " editing" : "");
          return h("td", { class: cls.trim() || null, onclick: key === "title" ? null : startEdit(row, key) },
            cellNode(row, key), i === 0 ? rowActions(row) : null);
        })));
      });
    });
    return h("div", { class: "scroll" }, h("table", { class: "grid" }, h("thead", {}, head), h("tbody", {}, body)));
  }

  function props(row, c, skip) {
    var all = PROPS[state.db];
    return c.fields.filter(function (k) { return skip.indexOf(k) < 0; }).map(function (k) {
      return h("span", { class: "prop" + (editable(k) && k !== "title" ? " ed" : "") + (editing(row, k) ? " editing" : ""),
        title: all[k].label, onclick: k === "title" ? null : startEdit(row, k) }, cellNode(row, k));
    });
  }

  function board(list, c) {
    var key = c.group || (state.db === "reports" ? "pillar" : "status");
    var movable = editable(key) && key !== "tags";
    return h("div", { class: "board" }, groupsOf(list, key).map(function (g) {
      var shut = isFolded(key, g.key);
      var lane = h("div", { class: "lane" + (shut ? " shut" : "") },
        h("div", { class: "lane-head", style: key === "pillar" ? pc(g.key) : null, onclick: toggleFold(key, g.key),
          title: shut ? "Unfold" : "Fold" }, chevron(!shut),
          key === "pillar" ? h("span", { class: "dot" }) : null, h("span", { text: g.label }), h("span", { class: "faint", text: String(g.items.length) })),
        shut ? null : g.items.map(function (row) {
          return h("div", { class: "card", draggable: movable ? "true" : null, onclick: open(row),
            ondragstart: function (e) { e.dataTransfer.setData("text/plain", rowId(row)); e.dataTransfer.effectAllowed = "move"; } },
            h("div", { class: "card-top" }, cellNode(row, "title"), rowActions(row)),
            h("div", { class: "props" }, props(row, c, ["title", key])));
        }));
      if (movable) {
        lane.addEventListener("dragover", function (e) { e.preventDefault(); lane.classList.add("over"); });
        lane.addEventListener("dragleave", function (e) { if (!lane.contains(e.relatedTarget)) lane.classList.remove("over"); });
        lane.addEventListener("drop", function (e) {
          e.preventDefault();
          lane.classList.remove("over");
          var id = e.dataTransfer.getData("text/plain");
          var row = rowsOf(state.db).filter(function (x) { return rowId(x) === id; })[0];
          var now = row && (key === "milestone" ? (row.milestone || "-") : row[key]);
          if (row && now !== g.key) EDIT[state.db][key].save(row, g.key);
        });
      }
      return lane;
    }));
  }

  function listLayout(list, c) {
    var out = [];
    groupsOf(list, c.group).forEach(function (g) {
      var shut = c.group && isFolded(c.group, g.key);
      if (c.group) out.push(h("div", { class: "lgroup", onclick: toggleFold(c.group, g.key) }, chevron(!shut),
        h("b", { text: g.label }), h("span", { class: "faint", text: String(g.items.length) })));
      if (shut) return;
      g.items.forEach(function (row) {
        out.push(h("div", { class: "litem", onclick: open(row) }, cellNode(row, "title"), rowActions(row),
          h("span", { class: "grow" }), h("div", { class: "props" }, props(row, c, ["title"]))));
      });
    });
    return h("div", { class: "list" }, out);
  }

  // ---- deleting a page ----------------------------------------------------------------------------

  function confirmDelete(r) {
    var go = h("button", { class: "btn danger-fill", type: "button", "data-label": "Delete" }, "Delete");
    var error = errorLine();
    go.addEventListener("click", function () {
      busy(go, true, "Deleting");
      setDeleted(r, true).then(function () {
        close();
        toast("Deleted " + titleOf(r), { text: "Undo", onclick: function () { setDeleted(r, false); } });
      }).catch(function (err) { busy(go, false, "x"); fail(error, err); });
    });
    var close = dialog("Delete this page?", [h("p", { class: "prose" }, h("b", { text: titleOf(r) }),
      " disappears from the overview and its address stops working, for everyone. Its files are kept: you can restore it under Deleted, below the table."), error],
      [h("span", { class: "grow" }), h("button", { class: "btn", type: "button", onclick: function () { close(); } }, "Cancel"), go]);
  }
  function setDeleted(r, deleted) {
    return api("POST", "/_api/pages/" + encodeURIComponent(r.name) + "/delete", { deleted: deleted }).then(load);
  }
  function deletedDialog() {
    var close = dialog("Deleted pages", [h("div", { class: "hint", text: "Pages you deleted. Restore brings one back, with its address." }),
      h("ul", { class: "evidence" }, data.deleted.map(function (r) {
        return h("li", {}, h("span", { class: "grow", text: titleOf(r) }), h("span", { class: "when", text: r.name }),
          h("button", { class: "btn small", type: "button", onclick: function () {
            setDeleted(r, false).then(function () { close(); toast("Restored " + titleOf(r)); })
              .catch(function (err) { toast(err.message, null, true); });
          } }, "Restore"));
      }))], [h("span", { class: "grow" }), h("button", { class: "btn", type: "button", onclick: function () { close(); } }, "Close")]);
  }

  // ---- saving views --------------------------------------------------------------------------

  function saveView(id) {
    api("POST", "/_api/views/" + id, { config: config() }).then(function () {
      state.draft[state.db] = null;
      return load();
    }).then(function () { toast("View saved for everyone"); }).catch(function (err) { toast(err.message, null, true); });
  }
  // A new view: from the current one (with its changes), named first.
  function newView(fromDraft) {
    var db = state.db;
    ask("New view", "Name", fromDraft ? currentView().name + " (changed)" : "", function (name) {
      return api("POST", "/_api/views", { name: name, config: config() }).then(function (answer) {
        state.draft[db] = null;
        state.view[db] = answer.view.id;
        store("view:" + db, answer.view.id);
        return load().then(function () { toast("View saved for the team"); });
      });
    });
  }
  function renameView(v) {
    ask("Rename the view", "Name", v.name, function (name) {
      return api("POST", "/_api/views/" + v.id, { name: name }).then(load);
    });
  }
  function deleteView(v) {
    api("POST", "/_api/views/" + v.id + "/delete", {}).then(function () {
      state.view[state.db] = BUILTIN[state.db][0].id;
      state.draft[state.db] = null;
      return load();
    }).then(function () { toast("View deleted"); }).catch(function (err) { toast(err.message, null, true); });
  }

  // ---- dialogs -------------------------------------------------------------------------------

  var closeDialog = null;
  function dialog(title, body, foot, wide) {
    if (closeDialog) closeDialog();
    var last = document.activeElement;
    var box = h("div", { class: "dialog" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "d-head" }, h("h3", { text: title }), h("span", { class: "grow" }),
        h("button", { class: "icon", type: "button", "aria-label": "Close", onclick: function () { close(); } }, icon("cross"))),
      h("div", { class: "d-body" }, body), h("div", { class: "d-foot" }, foot));
    var scrim = h("div", { class: "scrim", onmousedown: function (e) { if (e.target === scrim) close(); } }, box);
    function key(e) { if (e.key === "Escape") { e.preventDefault(); close(); } }
    function close() {
      scrim.remove();
      document.removeEventListener("keydown", key, true);
      closeDialog = null;
      if (last && last.focus) last.focus();
    }
    document.addEventListener("keydown", key, true);
    document.body.appendChild(scrim);
    closeDialog = close;
    var first = box.querySelector("input, textarea, select");
    if (first) first.focus();
    return close;
  }
  function errorLine() { return h("div", { class: "error", role: "alert", hidden: true }); }
  function fail(line, err) { line.textContent = err.message || String(err); line.hidden = false; }
  function busy(button, on, label) { button.disabled = on; if (label) button.textContent = on ? label : button.getAttribute("data-label"); }

  function ask(title, label, value, done) {
    var input = h("input", { type: "text", value: value, maxlength: "60" });
    var error = errorLine();
    var save = h("button", { class: "btn primary", type: "button", "data-label": "Save" }, "Save");
    function go() {
      if (!input.value.trim()) return;
      busy(save, true, "Saving");
      done(input.value.trim()).then(function () { close(); }).catch(function (err) { busy(save, false, "x"); fail(error, err); });
    }
    save.addEventListener("click", go);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
    var close = dialog(title, [h("label", {}, label, input), error], [h("span", { class: "grow" }), save]);
  }

  function pillarSelect(value) {
    return h("select", {}, data.pillars.map(function (p) { return h("option", { value: p.id, selected: p.id === value, text: p.label }); }));
  }
  function milestoneSelect(value, includeClosed) {
    var options = [h("option", { value: "", text: "No milestone (exploratory)", selected: !value })];
    data.milestones.filter(function (m) { return includeClosed || m.status === "open" || m.id === value; }).forEach(function (m) {
      options.push(h("option", { value: m.id, selected: m.id === value, text: m.title + (m.status !== "open" ? " (" + STATUS[m.status].toLowerCase() + ")" : "") }));
    });
    return h("select", {}, options);
  }

  function saveMilestone(id, body, message) {
    return api("POST", "/_api/milestones/" + id, body).then(load).then(function () { toast(message); })
      .catch(function (err) { toast(err.message, null, true); });
  }

  // mode "close": the status and result first.
  function milestoneDialog(m, mode) {
    var isNew = !m;
    m = m || { kind: "capability", pillar: "world-models", owner: viewer, status: "open" };
    var kind = m.kind;
    var kindChips = h("div", { class: "chips" });
    function drawKinds() {
      kindChips.textContent = "";
      [["capability", "Capability to show"], ["question", "Question to resolve"]].forEach(function (k) {
        kindChips.appendChild(h("button", { class: "chip", type: "button", "aria-pressed": String(kind === k[0]),
          onclick: function () { kind = k[0]; drawKinds(); drawStatus(); } }, icon(k[0]), k[1]));
      });
    }
    drawKinds();
    var title = h("input", { class: "title-in", type: "text", value: m.title || "", maxlength: "140",
      placeholder: "e.g. Predict 1 s of particle dynamics from RGB", "aria-label": "Title" });
    var pillar = pillarSelect(m.pillar);
    var owner = h("input", { type: "email", value: m.owner || viewer });
    var target = h("input", { type: "date", value: m.target || "" });
    var criteria = h("textarea", { rows: "3", maxlength: "2000", placeholder: "What shows it, or what answers it. A useful negative finding counts." });
    criteria.value = m.criteria || "";
    var next = h("input", { type: "text", value: m.next || "", maxlength: "500", placeholder: "The next step" });
    var status = h("select", {});
    var result = h("textarea", { rows: "3", maxlength: "2000", placeholder: "What was shown or learned" });
    result.value = m.result || "";
    var negative = h("input", { type: "checkbox" });
    negative.checked = !!m.negative;
    var evidence = h("select", {}, [h("option", { value: "", text: "None" })].concat(
      data.reports.filter(function (r) { return r.milestone === m.id || r.name === m.evidence; }).map(function (r) {
        return h("option", { value: r.name, selected: r.name === m.evidence, text: titleOf(r) });
      })));
    var closing = h("div", { class: "d-body", style: "padding:0;gap:12px" });
    function drawStatus() {
      var current = status.value || (mode === "close" ? (kind === "question" ? "answered" : "achieved") : m.status);
      status.textContent = "";
      ["open", kind === "question" ? "answered" : "achieved", "dropped"].forEach(function (s) {
        status.appendChild(h("option", { value: s, selected: s === current, text: STATUS[s] }));
      });
      if (!status.value) status.value = "open";
      closing.hidden = status.value === "open";
      negative.parentNode && (negative.parentNode.hidden = status.value !== "answered");
    }
    status.addEventListener("change", drawStatus);
    closing.appendChild(h("label", {}, "Result", result));
    closing.appendChild(h("label", { class: "check" }, negative, "A useful negative finding: the answer is no, or it does not work"));
    closing.appendChild(h("label", {}, "Shown in", evidence));
    var body = [kindChips, title,
      h("div", { class: "split" }, h("label", {}, "Pillar", pillar), h("label", {}, "Owner", owner), h("label", {}, "Target date", target)),
      h("label", {}, "Done when", criteria),
      h("label", {}, "Next step", next)];
    if (!isNew) body.push(h("label", {}, "Status", status), closing);
    if (!isNew) {
      var linked = data.reports.filter(function (r) { return r.milestone === m.id; });
      body.push(h("div", {}, h("div", { class: "field-label", text: "Reports (" + linked.length + ")" }),
        linked.length ? h("ul", { class: "evidence" }, linked.slice(0, 20).map(function (r) {
          return h("li", {}, h("a", { href: reportUrl(r), text: titleOf(r) }),
            h("span", { class: "when", text: when(r.published_at, false) + ", " + person(r.author) }));
        })) : h("div", { class: "hint", text: "None yet: publish with --milestone " + m.id + ", or link a report from its row." })));
    }
    var error = errorLine();
    body.push(error);
    var save = h("button", { class: "btn primary", type: "button", "data-label": isNew ? "Add milestone" : "Save" }, isNew ? "Add milestone" : "Save");
    save.addEventListener("click", function () {
      var payload = { title: title.value, kind: kind, pillar: pillar.value, owner: owner.value.trim(), target: target.value,
        criteria: criteria.value, next: next.value };
      if (!isNew) {
        payload.status = status.value;
        payload.result = result.value;
        payload.negative = status.value === "answered" && negative.checked;
        payload.evidence = evidence.value;
      }
      busy(save, true, "Saving");
      api("POST", isNew ? "/_api/milestones" : "/_api/milestones/" + m.id, payload).then(function (answer) {
        close();
        return load().then(function () { toast(isNew ? "Milestone added" : "Milestone saved"); });
      }).catch(function (err) { busy(save, false, "x"); fail(error, err); });
    });
    var close = dialog(isNew ? "New milestone" : mode === "close" ? (kind === "question" ? "Answer the question" : "Mark achieved") : "Edit milestone",
      body, [h("span", { class: "hint", text: isNew ? "Anyone on the team can edit it later." : "Id " + m.id }), h("span", { class: "grow" }), save]);
    drawStatus();
    if (mode === "close") result.focus();
  }

  // ---- the composer: a Markdown update with images and videos ---------------------------------

  var VIDEO = /\.(mp4|webm|mov|m4v|ogv)$/i;

  function mediaName(file, taken) {
    var dot = file.name.lastIndexOf(".");
    var ext = dot > 0 ? file.name.slice(dot).toLowerCase().replace(/[^a-z0-9.]/g, "") : "";
    if (!ext && file.type) ext = "." + file.type.split("/")[1].replace(/[^a-z0-9]/g, "");
    var stem = slug(dot > 0 ? file.name.slice(0, dot) : file.name) || "file";
    var name = "media/" + stem + ext, n = 2;
    while (taken[name]) name = "media/" + stem + "-" + n++ + ext;
    taken[name] = true;
    return name;
  }
  function size(n) {
    return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  }
  function freeName(base) {
    var names = {};
    data.reports.forEach(function (r) { names[r.name] = true; });
    base = base || "update";
    var name = base, n = 2;
    while (names[name]) name = base + "-" + n++;
    return name;
  }

  function editUpdate(r) {
    api("GET", "/_api/pages/" + encodeURIComponent(r.name) + "/source").then(function (source) {
      composer(source);
    }).catch(function (err) { toast(err.message, null, true); });
  }

  // source: an update to edit (from /source), or null for a new one.
  function composer(source, milestoneId) {
    var editing = !!source;
    var files = [];  // {file, path, url, bar}
    var taken = {};
    var title = h("input", { class: "title-in", type: "text", maxlength: "200", placeholder: "Title",
      value: source ? source.title : "", "aria-label": "Title" });
    var text = h("textarea", { placeholder: "Write in Markdown, math as $x^2$ or $$...$$. Drop or paste images and videos here.\n\n## What we tried\n\n## What we learned\n\n## Next",
      "aria-label": "Update, in Markdown", spellcheck: "true" });
    text.value = source ? source.markdown : "";
    var frame = h("iframe", { class: "preview", sandbox: "allow-same-origin", title: "Preview", hidden: true });
    var drop = h("div", { class: "drop", hidden: true }, icon("image"), "Drop to attach");
    var write = h("button", { class: "tab", type: "button", role: "tab", "aria-selected": "true" }, "Write");
    var look = h("button", { class: "tab", type: "button", role: "tab", "aria-selected": "false" }, "Preview");
    var picker = h("input", { type: "file", multiple: true, accept: "image/*,video/*", hidden: true });
    var attach = h("button", { class: "btn small ghost", type: "button", onclick: function () { picker.click(); } }, icon("image"), "Attach");
    var editor = h("div", { class: "editor" }, h("div", { class: "editor-bar", role: "tablist" }, write, look, h("span", { class: "grow" }), attach),
      text, frame, drop);
    var strip = h("div", { class: "files" });
    var pillar = pillarSelect(source ? source.pillar : "other");
    var chosen = source ? source.milestone : milestoneId || "";
    var ms = milestoneSelect(chosen, false);
    var follow = !source;
    pillar.addEventListener("change", function () { follow = false; });
    function fromMilestone() { var m = milestone(ms.value); if (m && follow) pillar.value = m.pillar; }
    ms.addEventListener("change", fromMilestone);
    fromMilestone();
    var tags = h("input", { type: "text", placeholder: "comma, separated", value: source ? (source.tags || []).join(", ") : "" });
    var error = errorLine();

    function insert(snippet) {
      var start = text.selectionStart || text.value.length, end = text.selectionEnd || start;
      var before = text.value.slice(0, start), after = text.value.slice(end);
      var pad = before && !/\n\n$/.test(before) ? (/\n$/.test(before) ? "\n" : "\n\n") : "";
      text.value = before + pad + snippet + "\n\n" + after;
      var at = (before + pad + snippet + "\n\n").length;
      text.setSelectionRange(at, at);
    }
    function add(list) {
      Array.prototype.forEach.call(list, function (file) {
        if (!/^(image|video)\//.test(file.type) && !VIDEO.test(file.name)) {
          fail(error, new Error(file.name + ": only images and videos can be attached"));
          return;
        }
        var path = mediaName(file, taken);
        var item = { file: file, path: path, url: URL.createObjectURL(file), bar: h("span", { class: "bar", style: "width:0" }) };
        files.push(item);
        var alt = (file.name.replace(/\.[^.]+$/, "") || "image").replace(/[\[\]]/g, "");
        insert("![" + alt + "](" + path + ")");
        var thumb = /^video\//.test(file.type) || VIDEO.test(file.name)
          ? h("video", { src: item.url, muted: true, preload: "metadata" }) : h("img", { src: item.url, alt: "" });
        var remove = h("button", { class: "icon", type: "button", "aria-label": "Remove " + file.name, title: "Remove" }, icon("cross"));
        var chip = h("div", { class: "file" }, thumb, h("div", { style: "min-width:0" }, h("div", { class: "nm", text: file.name }),
          h("div", { class: "sz", text: size(file.size) })), remove, item.bar);
        remove.addEventListener("click", function () {
          files = files.filter(function (f) { return f !== item; });
          text.value = text.value.split("](" + item.path + ")").join("](removed)");
          chip.remove();
        });
        strip.appendChild(chip);
      });
    }
    picker.addEventListener("change", function () { add(picker.files); picker.value = ""; });
    var depth = 0;
    editor.addEventListener("dragenter", function (e) { if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, "Files") >= 0) { depth++; drop.hidden = false; } });
    editor.addEventListener("dragleave", function () { if (--depth <= 0) { depth = 0; drop.hidden = true; } });
    editor.addEventListener("dragover", function (e) { e.preventDefault(); });
    editor.addEventListener("drop", function (e) {
      e.preventDefault();
      depth = 0;
      drop.hidden = true;
      if (e.dataTransfer && e.dataTransfer.files.length) add(e.dataTransfer.files);
    });
    text.addEventListener("paste", function (e) {
      var items = e.clipboardData ? e.clipboardData.files : [];
      if (items && items.length) { e.preventDefault(); add(items); }
    });

    function showPreview(on) {
      write.setAttribute("aria-selected", String(!on));
      look.setAttribute("aria-selected", String(on));
      text.hidden = on;
      frame.hidden = !on;
      if (!on) { text.focus(); return; }
      api("POST", "/_api/preview", { markdown: text.value, title: title.value }).then(function (answer) {
        var page = answer.html;
        files.forEach(function (f) { page = page.split('"' + f.path + '"').join('"' + f.url + '"'); });
        if (editing && data.content_origin) {
          // Media of the version being edited come from the page's own host.
          var base = data.content_origin.replace("__name__", source.name);
          page = page.replace("<head>", '<head><base href="' + base + '/">');
        }
        frame.srcdoc = page;
      }).catch(function (err) { fail(error, err); });
    }
    write.addEventListener("click", function () { showPreview(false); });
    look.addEventListener("click", function () { showPreview(true); });

    var publish = h("button", { class: "btn primary", type: "button", "data-label": editing ? "Publish new version" : "Publish" },
      editing ? "Publish new version" : "Publish");
    publish.addEventListener("click", function () {
      error.hidden = true;
      if (!title.value.trim() && !/^#\s/m.test(text.value)) { fail(error, new Error("Give it a title")); title.focus(); return; }
      if (!text.value.trim()) { fail(error, new Error("Write something first")); text.focus(); return; }
      var used = files.filter(function (f) { return text.value.indexOf("(" + f.path + ")") >= 0; });
      var name = editing ? source.name : freeName(slug(title.value || (/^#\s+(.*)$/m.exec(text.value) || [])[1]));
      var tagList = tags.value.split(",").map(function (t) { return t.trim().toLowerCase(); }).filter(Boolean);
      busy(publish, true, "Publishing");
      upload(name, {
        markdown: text.value, title: title.value.trim(), pillar: pillar.value, milestone: ms.value, tags: tagList,
        keep: editing, files: used.map(function (f) { return { path: f.path, size: f.file.size }; })
      }, used).then(function (answer) {
        close();
        return load().then(function () {
          toast(editing ? "New version published" : "Update published", { href: "/" + encodeURIComponent(answer.page.name) + "/", text: "Open" });
        });
      }).catch(function (err) { busy(publish, false, "x"); fail(error, err); });
    });

    var close = dialog(editing ? "Edit update" : "New update", [title, editor, strip,
      h("div", { class: "split" }, h("label", {}, "Milestone", ms), h("label", {}, "Pillar", pillar), h("label", {}, "Tags", tags)),
      error],
      [h("span", { class: "hint", text: editing ? "Images already in it stay." : "Visible to the team. Images and videos are stored with the update." }),
        h("span", { class: "grow" }), publish], true);
    (editing ? text : title).focus();
  }

  // start (the gateway signs one upload URL per file), PUT each file to the bucket, commit.
  function upload(name, body, used) {
    var path = "/_api/pages/" + encodeURIComponent(name) + "/versions";
    return api("POST", path, body).then(function (start) {
      var byPath = {};
      used.forEach(function (f) { byPath[f.path] = f; });
      return Promise.all(start.uploads.map(function (u) { return put(u, byPath[u.path]); })).then(function () {
        return api("POST", path + "/" + start.version + "/commit", {});
      });
    });
  }
  function put(u, item) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", u.url);
      Object.keys(u.headers || {}).forEach(function (k) { xhr.setRequestHeader(k, u.headers[k]); });
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) item.bar.style.width = Math.round(100 * e.loaded / e.total) + "%";
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) { item.bar.style.width = "100%"; resolve(); }
        else reject(new Error("upload of " + item.file.name + " failed (HTTP " + xhr.status + ")"));
      };
      xhr.onerror = function () {
        reject(new Error("upload of " + item.file.name + " failed: the bucket refused the browser (CORS)"));
      };
      xhr.send(item.file);
    });
  }

  // ---- wiring --------------------------------------------------------------------------------

  document.addEventListener("keydown", function (e) {
    if (closeDialog) return;
    if (e.key === "Escape" && (state.pop || state.edit || state.menu)) {
      state.pop = state.edit = state.menu = state.editQuery = "";
      render();
      return;
    }
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName || "")) {
      e.preventDefault();
      if ($("q")) $("q").focus();
    }
  });
  // A click outside the open popover closes it.
  document.addEventListener("mousedown", function (e) {
    if (closeDialog) return;
    var inside = e.target.closest(".popwrap, .pop, .vtab, td.editing, .prop.editing, .acts");
    if ((state.pop || state.edit || state.menu) && !inside) {
      state.pop = state.edit = state.menu = state.editQuery = "";
      render();
    }
  });
  window.addEventListener("focus", function () { if (data && !closeDialog && !state.pop && !state.edit) load(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
