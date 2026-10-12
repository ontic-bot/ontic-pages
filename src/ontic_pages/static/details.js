// Details, the bar's side (served as part of bar.js, after it): the info button (or the title
// menu's Details item) opens a panel with what page.json says about the version being viewed
// (the current one, or an old one under /_v/<version>/): description, who published it and
// when, the version id, visibility, git, where it was edited from, the files and every --meta
// pair. Asked from the gateway only when the panel opens, and kept per version. Focus moves into
// the panel; Escape closes it and focus goes back to the info button. Everything a person wrote
// is set with textContent, and only http(s) values become links.
(function () {
  "use strict";
  var bar = window.onticBar;
  if (!bar) return;

  var VERSION = /^\d{8}T\d{6}Z$/;
  var LINK = /^https?:\/\/[^\s]+$/i;
  var ACCESS = { private: "Private", ontic: "Team", public: "Public" };
  // version -> its details (answers only; a failure is asked again). Their `current` may be out
  // of date after a save or a publish: the bar's facts say which version is current.
  var cache = {};
  var showing = null; // the version the open panel shows
  var asked = 0; // only the latest request may draw

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }
  function link(text, href, outside) {
    var a = el("a", "", text);
    a.href = href;
    if (outside) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
    return a;
  }
  function focus(node) { if (node && node.focus) node.focus(); }
  function viewing() { return bar.view.version || bar.page().current; }
  function versionHref(version) {
    var page = bar.page();
    return "/" + page.name + "/" + (version === page.current ? "" : "_v/" + version + "/");
  }
  function copyButton(value, what) {
    var button = el("button", "d-copy", "Copy");
    button.type = "button";
    button.setAttribute("aria-label", "Copy " + what);
    button.title = "Copy " + what;
    button.addEventListener("click", function () { bar.copy(button, value); });
    return button;
  }
  function text(value) {
    if (value === null || value === undefined) return "";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  // ---- asking the gateway ----------------------------------------------------------------------

  function note(message, cls) {
    var body = $("d-body");
    body.textContent = "";
    var p = el("p", "d-note" + (cls ? " " + cls : ""), message);
    body.appendChild(p);
    return p;
  }

  function load(version) {
    showing = version;
    if (cache[version]) return draw(cache[version]);
    note("Loading…", "muted");
    var mine = ++asked;
    var url = "/_api/pages/" + encodeURIComponent(bar.page().name) + "/versions/" +
      encodeURIComponent(version);
    fetch(url, { credentials: "same-origin" }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, status: response.status, body: body };
      }, function () { return { ok: false, status: response.status, body: null }; });
    }).then(function (result) {
      if (mine !== asked || showing !== version) return;
      if (result.ok && result.body && result.body.version === version) {
        cache[version] = result.body;
        draw(result.body);
      } else {
        problem(result.status, result.body && result.body.error);
      }
    }, function () {
      if (mine === asked && showing === version) problem(0, "");
    });
  }

  function problem(status, error) {
    if (status === 401) {
      var p = note("Sign in to see the details. ");
      p.appendChild(link("Sign in", "/oauth2/start?rd=" + encodeURIComponent(location.href)));
      return;
    }
    note(status ? text(error) || "Could not load the details." : "Could not reach the server.",
      "error");
    var retry = el("button", "item", "Try again");
    retry.type = "button";
    retry.addEventListener("click", function () { load(showing); });
    $("d-body").appendChild(retry);
  }

  // ---- drawing ---------------------------------------------------------------------------------

  function draw(d) {
    var body = $("d-body");
    body.textContent = "";
    var meta = d.meta && typeof d.meta === "object" ? d.meta : {};
    var keys = Object.keys(meta).filter(function (k) { return k !== "edited_from"; });
    var recorded = d.description || d.published_by || d.published_at || d.git ||
      typeof d.files === "number" || Object.keys(meta).length;
    if (!recorded) body.appendChild(el("p", "d-note muted", "No details recorded."));
    if (d.description) body.appendChild(el("p", "d-desc", text(d.description)));
    var from = text(meta.edited_from);
    if (from) {
      var edited = el("p", "d-edited", "Edited in the browser from ");
      edited.appendChild(VERSION.test(from) ? link(from, versionHref(from)) : el("span", "", from));
      body.appendChild(edited);
    }

    var list = el("dl", "d-list");
    function row(label) {
      list.appendChild(el("dt", "", label));
      var dd = el("dd");
      list.appendChild(dd);
      return dd;
    }
    var dd;
    if (d.published_by || d.published_at) {
      dd = row("Published");
      if (d.published_by) dd.appendChild(el("div", "", "by " + text(d.published_by)));
      if (d.published_at) {
        var line = el("div");
        var time = el("time", "", bar.when(text(d.published_at)));
        time.setAttribute("datetime", text(d.published_at));
        line.appendChild(time);
        var ago = bar.relative(text(d.published_at));
        if (ago) line.appendChild(el("span", "muted", " (" + ago + ")"));
        dd.appendChild(line);
      }
    }

    dd = row("Version");
    dd.appendChild(el("code", "d-code", d.version));
    if (d.version === bar.page().current) dd.appendChild(el("span", "tag", "current"));
    dd.appendChild(copyButton(d.version, "version id"));

    if (d.visibility) row("Visibility").textContent = ACCESS[d.visibility] || text(d.visibility);

    var pillar = bar.pillar(text(d.pillar));
    if (pillar) row("Pillar").textContent = pillar;
    var tags = Array.isArray(d.tags) ? d.tags.map(text).filter(Boolean) : [];
    if (tags.length) {
      dd = row("Tags");
      dd.className = "d-tags";
      tags.forEach(function (tag) { dd.appendChild(el("span", "tag", tag)); });
    }

    if (d.git && typeof d.git === "object") {
      var git = d.git;
      dd = row("Git");
      if (git.remote) {
        var remote = text(git.remote);
        dd.appendChild(el("div", "d-wrap")).appendChild(
          LINK.test(text(git.repo_url)) ? link(remote, git.repo_url, true) : el("span", "", remote));
      }
      if (git.branch) {
        var branch = el("div", "", "branch ");
        branch.appendChild(el("code", "d-code", text(git.branch)));
        dd.appendChild(branch);
      }
      if (git.commit) {
        var commit = text(git.commit);
        var sha = el("div", "", "commit ");
        sha.appendChild(LINK.test(text(git.commit_url))
          ? link(commit.slice(0, 8), git.commit_url, true) : el("code", "d-code", commit.slice(0, 8)));
        sha.appendChild(copyButton(commit, "commit"));
        dd.appendChild(sha);
      }
      if (git.dirty) {
        var dirty = el("span", "tag d-dirty", "uncommitted changes");
        dirty.title = "The working tree had changes not yet committed when it was published.";
        dd.appendChild(dirty);
      }
    }

    if (typeof d.files === "number") {
      row("Files").textContent = d.files + (d.files === 1 ? " file" : " files");
    }
    if (list.childNodes.length) body.appendChild(list);

    if (keys.length) {
      body.appendChild(el("p", "label", "Metadata"));
      var pairs = el("dl", "d-list d-meta");
      keys.forEach(function (key) {
        var value = text(meta[key]);
        pairs.appendChild(el("dt", "d-wrap", key));
        var cell = el("dd", "d-wrap");
        cell.appendChild(LINK.test(value) ? link(value, value, true) : el("span", "", value));
        cell.appendChild(copyButton(value, key));
        pairs.appendChild(cell);
      });
      body.appendChild(pairs);
    }
  }

  // ---- wiring ----------------------------------------------------------------------------------

  bar.add({
    opened: function (id) {
      if (id !== "info-panel") return;
      load(viewing());
      focus($("d-title"));
    },
    // A new current version while the panel shows the current one: show that one instead.
    render: function () {
      var panel = $("info-panel");
      if (showing && panel && !panel.hidden && showing !== viewing()) load(viewing());
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    var panel = $("info-panel");
    if (!panel) return;
    var item = $("m-details");
    if (item) item.addEventListener("click", function () { bar.open("info-panel"); });
    panel.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      bar.close();
      focus($("info-btn"));
    });
  });
})();
