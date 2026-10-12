// The bar around every ontic page: the title menu, the Share panel, the frame's fade-in, and
// keeping the address, the tab title and the version in step with the page in the frame.
// Runs on the apex under a strict policy; the page in the frame cannot reach this document.
// Messages are accepted only from the page's exact content origin and from our own frames,
// and nothing sent to the frame carries an email or a page fact. Everything a person wrote
// (title, description, emails) is set with textContent, never as HTML.
//
// Loaded without defer, before the frame exists, so no message from the frame is missed.
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("ontic-facts").textContent);
  var page = data.page;
  var view = data.view;
  var name = page.name;
  var origin = page.content_origin;
  var path = view.path; // the frame's path on the content host
  var scrollY = 0;
  var revealed = false;
  var loaded = false;
  var pending = null; // the next version's frame while it loads
  var pendingTimer = 0;
  var VERSION_PATH = /^\/_v\/(\d{8}T\d{6}Z)(\/.*)?$/;

  function $(id) { return document.getElementById(id); }
  function frame() { return $("frame"); }

  // Features in files of their own (details.js, edit.js, served after this one) hook in here:
  // message(msg) for each message from the frame, render() after the bar is drawn, opened(id)
  // when a menu or panel opens, holds() true to keep a new version from fading in over unsaved
  // work. update(facts) draws new page facts and fades in a new current version; adopt(facts)
  // only draws them (the frame shows their current version already). The page in the frame
  // cannot reach this object.
  var features = [];
  window.onticBar = {
    add: function (feature) { features.push(feature); },
    page: function () { return page; },
    view: view,
    origin: origin,
    frame: frame,
    update: function (next) { update(next); },
    adopt: function (next) { adopt(next); },
    swap: function () { swap(); },
    close: function () { closeAll(); },
    open: function (panelId) { openPop(panelId); },
    copy: function (button, text) { copy(button, text); },
    relative: function (iso) { return relative(iso); },
    when: function (iso) { return when(iso); },
    pillar: function (id) { return pillarLabel(id); }
  };
  function isNumber(y) { return typeof y === "number" && isFinite(y) && y >= 0 && y < 1e9; }

  // ---- the frame: fade in when ready, a spinner only when slow ------------------------------

  function reveal() {
    if (revealed) return;
    revealed = true;
    var f = frame();
    if (f) f.classList.add("shown");
    if (loaded) $("status").hidden = true;
    else $("status").querySelector(".spinner").hidden = true;
  }
  function onLoaded() {
    loaded = true;
    reveal();
    $("status").hidden = true;
  }
  setTimeout(function () { if (!revealed && $("status")) $("status").hidden = false; }, 300);
  setTimeout(reveal, 1500); // the page may block the bridge: show it anyway
  setTimeout(function () {
    if (loaded || !$("status")) return;
    $("status").hidden = false;
    $("slow").hidden = false;
  }, 8000);
  // A frame's load event does not bubble, but it does pass the document on the way down.
  document.addEventListener("load", function (event) {
    if (event.target === frame()) onLoaded();
    else if (pending && event.target === pending) promote();
  }, true);

  // ---- messages from the frame ---------------------------------------------------------------

  window.addEventListener("message", function (event) {
    if (event.origin !== origin) return;
    var msg = event.data;
    if (!msg || typeof msg !== "object" || typeof msg.ontic !== "string") return;
    var f = frame();
    var fromFrame = f && event.source === f.contentWindow;
    var fromPending = pending && event.source === pending.contentWindow;
    if (!fromFrame && !fromPending) return;
    // A new version's frame that says it is ready becomes the frame, then the features hear it.
    if (fromPending && msg.ontic === "ready") {
      promote();
      fromFrame = true;
    }
    if (fromFrame) features.forEach(function (f) { if (f.message) f.message(msg); });
    switch (msg.ontic) {
      case "ready":
        if (!fromPending) reveal();
        behind(msg.version);
        break;
      case "nav":
        if (fromFrame && typeof msg.path === "string" && typeof msg.title === "string") {
          onNav(msg.path, msg.title);
        }
        break;
      case "engaged":
        if (fromFrame) closeAll();
        break;
      case "scroll":
        if (fromFrame && isNumber(msg.y)) scrollY = msg.y;
        break;
      case "open":
        if (fromFrame && typeof msg.url === "string") openTop(msg.url);
        break;
      // Comments (comments.js) and edit text in place (edit.js) handle their own types.
    }
  });

  // A page that says it was served from another version than the current one (fetched before a
  // save or a publish made a new one current) is replaced by the current one, unless a feature
  // holds the frame; once per current version, so a lagging server cannot make it loop.
  var VERSION_ID = /^\d{8}T\d{6}Z$/;
  var replacedFor = null;
  function behind(version) {
    if (view.version || typeof version !== "string" || !VERSION_ID.test(version)) return;
    if (version === page.current || replacedFor === page.current) return;
    if (features.some(function (f) { return f.holds && f.holds(); })) return;
    replacedFor = page.current;
    swap();
  }

  function onNav(p, title) {
    if (p.charAt(0) !== "/" || p.charAt(1) === "/" || p.length > 4096) return;
    path = p;
    try {
      history.replaceState(history.state, "", "/" + name + p);
    } catch (error) { /* not a URL of ours */ }
    document.title = title ? title.slice(0, 300) : name;
    var raw = $("m-raw");
    if (raw) raw.href = rawUrl(p);
  }

  // A link in the page to the apex (another page, the listing): the frame may not navigate the
  // tab, so it asks. Only URLs on this very origin.
  function openTop(href) {
    var url;
    try { url = new URL(href, location.href); } catch (error) { return; }
    if (url.origin !== location.origin) return;
    location.assign(url.href);
  }

  function rawUrl(p) {
    var hash = p.indexOf("#") >= 0 ? p.slice(p.indexOf("#")) : "";
    var rest = hash ? p.slice(0, p.indexOf("#")) : p;
    return origin + rest + (rest.indexOf("?") >= 0 ? "&" : "?") + "raw=1" + hash;
  }

  // ---- menus ---------------------------------------------------------------------------------

  var pops = [["title-btn", "title-menu"], ["info-btn", "info-panel"],
    ["share-btn", "share-panel"]];
  function closeAll(except) {
    pops.forEach(function (pair) {
      if (pair[1] === except) return;
      var button = $(pair[0]), box = $(pair[1]);
      if (box && !box.hidden) {
        box.hidden = true;
        button.setAttribute("aria-expanded", "false");
      }
    });
  }
  function toggle(pair) {
    var button = $(pair[0]), box = $(pair[1]);
    var open = box.hidden;
    closeAll(pair[1]);
    box.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open) return;
    features.forEach(function (f) { if (f.opened) f.opened(pair[1]); });
    render();
  }
  // Opens the pop whose box is `id` (closing the others), unless it is open already.
  function openPop(id) {
    var pair = pops.filter(function (p) { return p[1] === id; })[0];
    var box = pair && $(pair[1]);
    if (box && box.hidden) toggle(pair);
  }

  // ---- what the bar shows ------------------------------------------------------------------

  function relative(iso) {
    var then = new Date(iso).getTime();
    if (!iso || isNaN(then)) return "";
    var s = Math.max(0, (Date.now() - then) / 1000);
    var steps = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"]];
    if (s < 45) return "just now";
    var n = s, unit = "second";
    for (var i = 0; i < steps.length && n >= steps[i][0]; i++) {
      n = n / steps[i][0];
      unit = i + 1 < steps.length ? steps[i + 1][1] : "year";
    }
    n = Math.round(n);
    return n + " " + unit + (n === 1 ? "" : "s") + " ago";
  }
  // DD.MM.YYYY hh:mm in the viewer's time zone.
  function when(iso) {
    var t = new Date(iso);
    if (!iso || isNaN(t.getTime())) return iso || "";
    function two(n) { return (n < 10 ? "0" : "") + n; }
    return two(t.getDate()) + "." + two(t.getMonth() + 1) + "." + t.getFullYear() + " " +
      two(t.getHours()) + ":" + two(t.getMinutes());
  }
  // The research pillars of the mission repo, as info.PILLAR_LABELS names them.
  var PILLARS = {
    "data": "Data",
    "scene-reconstruction": "Scene reconstruction",
    "world-models": "World models",
    "robot-learning": "Robot learning",
    "other": "Other"
  };
  function pillarLabel(id) {
    return Object.prototype.hasOwnProperty.call(PILLARS, id) ? PILLARS[id] : "";
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function render() {
    var viewing = view.version || page.current;
    var shown = page.versions.filter(function (v) { return v.version === viewing; })[0] || {};
    $("m-by").hidden = !page.published_by;
    $("m-owner").textContent = page.published_by || "";
    var time = $("m-time");
    time.setAttribute("datetime", shown.published_at || "");
    time.textContent = relative(shown.published_at) || when(shown.published_at);
    time.title = when(shown.published_at);
    $("m-desc").textContent = shown.description || "";
    $("m-desc").hidden = !shown.description;
    var labels = $("m-labels");
    if (labels) {
      var words = [pillarLabel(shown.pillar)].concat(shown.tags || []).filter(Boolean);
      labels.textContent = words.join(" \u00b7 ");
      labels.hidden = !words.length;
    }
    // The milestone this version is evidence for (named for the team only), in the overview.
    var goal = $("m-milestone"), known = (page.milestones || {})[shown.milestone];
    if (goal) {
      goal.hidden = !known;
      if (known) {
        $("m-milestone-link").textContent = known.title;
        $("m-milestone-link").href = "/#m-" + encodeURIComponent(shown.milestone);
      }
    }

    var list = $("m-versions");
    list.textContent = "";
    page.versions.forEach(function (v) {
      var current = v.version === page.current;
      var a = el("a");
      a.setAttribute("role", "menuitem");
      a.href = current ? "/" + name + "/" : "/" + name + "/_v/" + v.version + "/";
      if (v.version === viewing) a.setAttribute("aria-current", "page");
      var label = el("span", "when", when(v.published_at) || v.version);
      label.title = (v.published_by ? "by " + v.published_by + ", " : "") + v.version;
      a.appendChild(label);
      if (current) a.appendChild(el("span", "tag", "current"));
      a.appendChild(el("span", "desc", v.description || ""));
      var li = el("li");
      li.appendChild(a);
      list.appendChild(li);
    });

    $("old").hidden = !(view.version && view.version !== page.current);
    document.body.setAttribute("data-visibility", page.visibility);
    var select = $("s-access");
    select.value = page.visibility;
    select.disabled = page.role !== "owner";
    $("s-note").hidden = page.role === "owner";
    var option = select.options[select.selectedIndex];
    $("s-explain").textContent = option ? option.getAttribute("data-explain") : "";
    $("s-owner").textContent = page.published_by || "Sign in to see who";
    features.forEach(function (f) { if (f.render) f.render(); });
  }

  // ---- copy, share ---------------------------------------------------------------------------

  // Copies `text` (the page's address by default), then says so on the button for a moment.
  // Called inside the click handler, as the clipboard asks; else a selected textarea and
  // execCommand.
  function copy(button, text) {
    text = typeof text === "string" ? text : location.href;
    var say = function (word) {
      var before = button.getAttribute("data-label") || button.textContent;
      button.setAttribute("data-label", before);
      button.textContent = word;
      setTimeout(function () { button.textContent = before; }, 1400);
    };
    var fallback = function () {
      var area = el("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;opacity:0;top:0;left:0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (error) { /* unsupported */ }
      area.remove();
      say(ok ? "Copied" : "Could not copy");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { say("Copied"); }, fallback);
    } else {
      fallback();
    }
  }

  function setVisibility(level) {
    var select = $("s-access"), error = $("s-error");
    error.hidden = true;
    select.disabled = true;
    fetch("/_api/pages/" + encodeURIComponent(name) + "/visibility", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visibility: level })
    }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (result.ok && result.body && result.body.name === name) {
        page = result.body;
      } else {
        error.textContent = (result.body && result.body.error) || "Could not change it.";
        error.hidden = false;
      }
      render();
    }, function () {
      error.textContent = "Could not reach the server.";
      error.hidden = false;
      render();
    });
  }

  // ---- live updates: a new current version fades in where you were -------------------------

  var lastFetch = 0;
  function refresh() {
    if (document.visibilityState !== "visible" || Date.now() - lastFetch < 2000) return;
    lastFetch = Date.now();
    var asked = generation;
    fetch("/_api/pages/" + encodeURIComponent(name), { credentials: "same-origin", cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (next) {
        if (asked === generation) update(next);
      }, function () { /* offline: try again later */ });
  }

  var generation = 0; // adopt() counts up: polls asked before it answer with older facts
  function valid(next) { return next && next.name === name && Array.isArray(next.versions); }

  // New page facts: draw them, and fade in a new current version unless a feature holds the
  // frame (unsaved edits in it).
  function update(next) {
    if (!valid(next)) return;
    var changed = next.current !== page.current;
    page = next;
    render();
    var held = features.some(function (f) { return f.holds && f.holds(); });
    if (changed && !view.version && !held) swap();
  }

  // New page facts whose current version the frame shows already (an edit saved from the text
  // on screen): drawn, never faded in. Since they are now page.current, a poll that brings the
  // same version changes nothing, and a poll asked before them is dropped.
  function adopt(next) {
    if (!valid(next)) return;
    generation += 1;
    page = next;
    render();
  }

  function swap() {
    if (pending) pending.remove();
    clearTimeout(pendingTimer);
    var old = frame();
    pending = old.cloneNode(false);
    pending.removeAttribute("id");
    pending.classList.remove("shown");
    pending.classList.add("next");
    pending.src = origin + path;
    $("stage").appendChild(pending);
    pendingTimer = setTimeout(function () { if (pending) { pending.remove(); pending = null; } }, 20000);
  }

  function promote() {
    if (!pending) return;
    var next = pending, old = frame();
    pending = null;
    clearTimeout(pendingTimer);
    try {
      next.contentWindow.postMessage({ ontic: "scroll-to", y: scrollY }, origin);
    } catch (error) { /* gone */ }
    // From here the new frame is the frame (its nav messages count); the old one stays
    // underneath until the fade is done.
    if (old) old.removeAttribute("id");
    next.id = "frame";
    next.classList.add("shown");
    setTimeout(function () {
      if (old) old.remove();
      next.classList.remove("next");
    }, 160);
  }

  // ---- wiring, once the bar exists -----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    render();
    pops.forEach(function (pair) {
      $(pair[0]).addEventListener("click", function (event) {
        event.stopPropagation();
        toggle(pair);
      });
      $(pair[1]).addEventListener("click", function (event) { event.stopPropagation(); });
    });
    document.addEventListener("click", function () { closeAll(); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeAll();
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-copy]"), function (button) {
      button.addEventListener("click", function () { copy(button); });
    });
    $("s-access").addEventListener("change", function (event) {
      if (event.target.value !== page.visibility) setVisibility(event.target.value);
    });
    var signin = $("signin");
    if (signin) {
      signin.addEventListener("click", function () {
        signin.href = "/oauth2/start?rd=" + encodeURIComponent(location.href);
      });
    }
    $("retry").addEventListener("click", function () {
      $("slow").hidden = true;
      var f = frame();
      f.src = origin + path;
    });
    // #comment=<id> is for the bar (comments.js), not the page.
    if (location.hash && path.indexOf("#") < 0 && location.hash.indexOf("#comment=") !== 0) {
      path += location.hash;
      frame().src = origin + path;
    }
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    setInterval(refresh, 30000);
  });
})();
