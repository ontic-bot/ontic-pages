// Comments in the bar: the comment toggle and its menu, the thread popover, the side panel with
// every thread, and the pins the page's bridge draws (static/pins.js). A thread is attached to
// an element of the page: the bridge outlines it while its thread is open, and while the pointer
// is on its card here. For signed-in viewers only (the shell loads this file only for them).
// Every comment is read and written here, at the apex, with the viewer's own sign-in; the frame
// only gets ids, initials, colors, anchors and flags, never a comment's text or an email.
// Messages are accepted only from the page's exact content origin and from the frame itself.
// Everything a person wrote is set with textContent, never as HTML.
//
// Loaded after bar.js, without defer: bar.js handles the same messages first (it may promote a
// new version's frame to be the frame before this sees its "ready").
(function () {
  "use strict";

  var facts = JSON.parse(document.getElementById("ontic-facts").textContent);
  var page = facts.page;
  var view = facts.view;
  if (!page.viewer) return;
  var name = page.name;
  var origin = page.content_origin;
  var ID = /^[0-9a-f]{16}$/;
  var VERSION = /^\/_v\/\d{8}T\d{6}Z(?=\/)/;
  var NARROW = "(max-width: 600px)";
  var PALETTE = ["#d97706", "#2563eb", "#059669", "#db2777", "#7c3aed", "#0891b2", "#dc2626",
                 "#4d7c0f"];
  var NS = "http://www.w3.org/2000/svg";
  var ICONS = {
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.5 1.5",
           "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5"],
    close: ["M6 6l12 12", "M18 6 6 18"],
    trash: ["M5 7h14", "M10 7V5h4v2", "M7 7l1 12h8l1-12"],
    send: ["M5 12h13", "M13 6l6 6-6 6"],
    back: ["M15 6l-6 6 6 6"]
  };
  var state = {
    threads: [],
    current: page.current,
    framePath: view.path, // the frame's path on the content host, from the bridge
    on: false, // comment mode: a click on the page starts a thread
    showResolved: false,
    openId: null, // the thread in the popover
    compose: null, // {anchor}: a new thread being written
    point: null,
    side: false,
    panelId: null, // a thread shown in the side panel instead of the list
    pending: null, // a thread to show once the bridge says whether its pin can be placed
    orphans: {}, // threads on this path whose pins could not be placed
    seq: 0,
    lastPath: null
  };
  var hashId = (location.hash.match(/^#comment=([0-9a-f]{16})$/) || [])[1] || null;
  var pop = null, side = null;

  function $(id) { return document.getElementById(id); }
  function frame() { return $("frame"); }
  function post(message) {
    var f = frame();
    try { f.contentWindow.postMessage(message, origin); } catch (error) { /* not loaded yet */ }
  }
  function pagePath() { return ((state.framePath || "/").split("#")[0] || "/").replace(VERSION, ""); }
  function viewing() { return view.version || state.current; }
  function prefix() { return view.version ? "/_v/" + view.version : ""; }
  function narrow() { return window.matchMedia && window.matchMedia(NARROW).matches; }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }
  function icon(key) {
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    ICONS[key].forEach(function (d) {
      var path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }
  function button(cls, key, label) {
    var b = el("button", cls);
    b.type = "button";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.appendChild(icon(key));
    return b;
  }
  function colorFor(email) {
    var hash = 0, text = String(email || "").toLowerCase();
    for (var i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    return PALETTE[hash % PALETTE.length];
  }
  function initialOf(text) { return (String(text || "?").trim().charAt(0) || "?").toUpperCase(); }
  function avatar(nameText, email) {
    var node = el("span", "cm-avatar", initialOf(nameText || email));
    node.setAttribute("aria-hidden", "true");
    node.style.background = colorFor(email);
    return node;
  }
  function relative(iso) {
    var then = new Date(iso).getTime();
    if (!iso || isNaN(then)) return "";
    var s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 45) return "just now";
    var steps = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"]];
    var n = s, unit = "second";
    for (var i = 0; i < steps.length && n >= steps[i][0]; i++) {
      n = n / steps[i][0];
      unit = i + 1 < steps.length ? steps[i + 1][1] : "year";
    }
    n = Math.round(n);
    return n + " " + unit + (n === 1 ? "" : "s") + " ago";
  }
  function byline(nameText, email, iso) {
    var line = el("div", "cm-byline");
    line.appendChild(avatar(nameText, email));
    var who = el("span", "cm-who", nameText || email);
    who.title = email || "";
    line.appendChild(who);
    var when = el("span", "cm-when", relative(iso));
    when.title = window.onticBar ? window.onticBar.when(iso) : iso || "";
    line.appendChild(when);
    return line;
  }
  // The element a thread is attached to, named: its tag (from the anchor, or for older threads
  // the end of its selector) and the start of its text.
  function tagOf(anchor) {
    if (anchor.tag) return anchor.tag;
    var m = /(?:^|[\s>])([a-z][a-z0-9-]*)(?::nth-of-type\(\d+\))?$/i.exec(anchor.selector || "");
    return m ? m[1].toLowerCase() : "";
  }
  function elementName(anchor, container) {
    var tag = tagOf(anchor);
    if (tag) container.appendChild(el("span", "cm-tag", tag));
    if (anchor.snippet) container.appendChild(el("span", "cm-snippet", anchor.snippet));
    return container;
  }
  function threadById(id) {
    return state.threads.filter(function (t) { return t.id === id; })[0] || null;
  }
  function upsert(thread) {
    for (var i = 0; i < state.threads.length; i++) {
      if (state.threads[i].id === thread.id) { state.threads[i] = thread; return; }
    }
    state.threads.unshift(thread);
  }

  // ---- the API -------------------------------------------------------------------------------

  function api(method, suffix, body) {
    var init = { method: method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch("/_api/pages/" + encodeURIComponent(name) + "/comments" + suffix, init)
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (answer) {
          return { ok: response.ok, body: answer || {} };
        });
      });
  }

  // A write that answers one thread: merge it and repaint, or show the error in `error`.
  function write(request, error) {
    error.hidden = true;
    return request.then(function (result) {
      if (!result.ok || !result.body.thread) {
        error.textContent = result.body.error || "Could not save that.";
        error.hidden = false;
        return null;
      }
      upsert(result.body.thread);
      renderAll();
      return result.body.thread;
    }, function () {
      error.textContent = "Could not reach the server.";
      error.hidden = false;
      return null;
    });
  }

  var loading = false, lastLoad = 0;
  // The first load always runs (a page opened in a background tab still gets its count); later
  // ones only while the tab is visible, at most every 2 seconds.
  function load(first) {
    if (loading) return;
    if (!first && (document.visibilityState === "hidden" || Date.now() - lastLoad < 2000)) return;
    loading = true;
    lastLoad = Date.now();
    api("GET", "").then(function (result) {
      loading = false;
      if (!result.ok || !Array.isArray(result.body.threads)) return;
      state.threads = result.body.threads;
      if (result.body.current) state.current = result.body.current;
      renderAll();
      if (hashId) {
        var id = hashId;
        hashId = null;
        openFromList(id);
      }
    }, function () { loading = false; });
  }

  // ---- pins, mode, the count -----------------------------------------------------------------

  function shown(t) {
    return !t.resolved || state.showResolved || t.id === state.openId || t.id === state.pending
      || t.id === state.panelId;
  }
  function sendPins() {
    var path = pagePath();
    var pins = state.threads.filter(function (t) {
      return shown(t) && t.anchor && t.anchor.path === path;
    }).map(function (t) {
      return { id: t.id, initial: initialOf(t.created_by_name || t.created_by),
               color: colorFor(t.created_by), anchor: t.anchor, resolved: !!t.resolved,
               fallback: t.version === viewing(), active: t.id === state.openId };
    });
    if (state.compose) {
      pins.push({ id: "draft", initial: initialOf(page.viewer), color: "#6b7280",
                  anchor: state.compose.anchor, fallback: true, active: true });
    }
    // The bridge echoes `seq` with its answer, so an answer from a document the frame is leaving
    // cannot decide where a pending jump goes.
    state.seq += 1;
    post({ ontic: "pins", pins: pins, seq: state.seq });
  }
  function updateCount() {
    var open = state.threads.filter(function (t) { return !t.resolved; }).length;
    var count = $("cm-count");
    count.textContent = open > 99 ? "99+" : String(open);
    count.hidden = open === 0;
    var label = state.on ? "Stop commenting" : "Comment: click the page to add one";
    $("cm-btn").title = label + (open ? " (" + open + " open)" : "");
  }
  function setMode(on) {
    state.on = on;
    $("cm-btn").setAttribute("aria-pressed", on ? "true" : "false");
    document.body.classList.toggle("cm-mode", on);
    post({ ontic: "comment-mode", on: on });
    if (!on && state.compose && !draftText()) closePop();
    updateCount();
  }
  function renderAll() {
    updateCount();
    sendPins();
    if (state.side) renderSide();
    if (!pop.hidden && state.openId) {
      var thread = threadById(state.openId);
      if (thread) renderThread(pop, thread, { close: closePop }); else closePop();
    }
  }
  function onFrame() {
    var path = pagePath();
    if (state.lastPath !== null && path !== state.lastPath && !pop.hidden) closePop();
    state.lastPath = path;
    post({ ontic: "comment-mode", on: state.on });
    sendPins();
  }

  // ---- the reply box -------------------------------------------------------------------------

  function replyBox(placeholder, onSend) {
    var area = el("textarea");
    area.rows = 1;
    area.placeholder = placeholder;
    area.setAttribute("aria-label", placeholder);
    area.maxLength = 4000;
    var send = button("cm-send", "send", "Send");
    var error = el("p", "cm-error");
    error.hidden = true;
    var busy = false;
    function sync() {
      area.style.height = "auto";
      area.style.height = Math.min(area.scrollHeight, 140) + "px";
      send.disabled = busy || !area.value.trim();
    }
    function submit() {
      var text = area.value.trim();
      if (!text || busy) return;
      busy = true;
      sync();
      onSend(text, function (failure) {
        busy = false;
        if (failure) {
          error.textContent = failure;
          error.hidden = false;
        } else {
          area.value = "";
          error.hidden = true;
        }
        sync();
      });
    }
    area.addEventListener("input", sync);
    area.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });
    send.addEventListener("click", submit);
    var box = el("div", "cm-reply");
    box.appendChild(area);
    box.appendChild(send);
    return { node: box, error: error, area: area, sync: sync };
  }
  function draftText() {
    var area = pop && !pop.hidden ? pop.querySelector("textarea") : null;
    return area ? area.value.trim() : "";
  }

  // ---- the popover ---------------------------------------------------------------------------

  // Next to `point` (in the frame's viewport), inside the window. Narrow screens: a bottom sheet
  // (comments.css), whatever is set here.
  function placePop(point) {
    var box = frame().getBoundingClientRect();
    pop.style.left = "0px";
    pop.style.top = "0px";
    pop.hidden = false;
    var width = pop.offsetWidth, height = pop.offsetHeight;
    var x = box.left + point.x + 12, y = box.top + point.y - 8;
    if (x + width > window.innerWidth - 8) x = box.left + point.x - width - 12;
    x = Math.max(8, Math.min(x, window.innerWidth - width - 8));
    y = Math.max(48, Math.min(y, window.innerHeight - height - 8));
    pop.style.left = Math.round(x) + "px";
    pop.style.top = Math.round(y) + "px";
  }
  function closePop() {
    if (!pop || pop.hidden) return;
    pop.hidden = true;
    pop.textContent = "";
    state.openId = null;
    state.compose = null;
    sendPins();
  }
  function openCompose(anchor, point) {
    state.openId = null;
    state.compose = { anchor: anchor };
    state.point = point;
    pop.textContent = "";
    var head = el("div", "cm-head");
    head.appendChild(el("span", "cm-title", "New comment"));
    var close = button("cm-icon", "close", "Close");
    close.addEventListener("click", closePop);
    head.appendChild(close);
    pop.appendChild(head);
    var box = replyBox("Add a comment", function (text, done) {
      api("POST", "", { anchor: anchor, version: viewing(), body: text }).then(function (result) {
        if (!result.ok || !result.body.thread) {
          done(result.body.error || "Could not post the comment.");
          return;
        }
        done();
        state.compose = null;
        upsert(result.body.thread);
        openThread(result.body.thread.id, state.point);
        renderAll();
      }, function () { done("Could not reach the server."); });
    });
    pop.appendChild(box.node);
    pop.appendChild(box.error);
    placePop(point);
    box.sync();
    box.area.focus();
    sendPins();
  }
  function openThread(id, point) {
    var thread = threadById(id);
    if (!thread) return;
    state.compose = null;
    state.openId = id;
    state.point = point;
    renderThread(pop, thread, { close: closePop });
    placePop(point);
    sendPins();
  }

  function link(thread) {
    var path = (thread.anchor && thread.anchor.path) || "/";
    return location.origin + "/" + name + prefix() + path + "#comment=" + thread.id;
  }
  function copy(text, done) {
    var fallback = function () {
      var area = el("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;opacity:0;top:0;left:0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (error) { /* unsupported */ }
      area.remove();
      done(ok);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    } else {
      fallback();
    }
  }

  // One thread in `container` (the popover, or the side panel): where it points, Resolve or
  // Reopen, Copy link, Close, the comments, a reply box. A draft in the reply box survives a
  // repaint.
  function renderThread(container, thread, options) {
    var old = container.querySelector("textarea");
    var draft = old ? old.value : "";
    var focused = old && document.activeElement === old;
    container.textContent = "";
    var error = el("p", "cm-error");
    error.hidden = true;

    var head = el("div", "cm-head");
    if (options.back) {
      var back = button("cm-icon", "back", "All comments");
      back.addEventListener("click", options.back);
      head.appendChild(back);
    }
    var anchor = thread.anchor || {};
    var title = el("span", "cm-title");
    if (anchor.snippet || tagOf(anchor)) elementName(anchor, title);
    else title.textContent = "Comment on " + (anchor.path || "/");
    head.appendChild(title);
    var resolve = button("cm-icon cm-resolve", "check", thread.resolved ? "Reopen" : "Resolve");
    resolve.setAttribute("aria-pressed", thread.resolved ? "true" : "false");
    resolve.addEventListener("click", function () {
      write(api("POST", "/" + thread.id + "/resolve", { resolved: !thread.resolved }), error);
    });
    var linkButton = button("cm-icon", "link", "Copy link");
    linkButton.addEventListener("click", function () {
      copy(link(thread), function (ok) {
        linkButton.title = ok ? "Copied" : "Could not copy";
        linkButton.classList.add("cm-done");
        setTimeout(function () {
          linkButton.title = "Copy link";
          linkButton.classList.remove("cm-done");
        }, 1400);
      });
    });
    var close = button("cm-icon", "close", "Close");
    close.addEventListener("click", options.close);
    head.appendChild(resolve);
    head.appendChild(linkButton);
    head.appendChild(close);
    container.appendChild(head);

    if (thread.resolved) {
      container.appendChild(el("p", "cm-note", "Resolved by " + (thread.resolved_by_name
        || thread.resolved_by || "someone") + ", " + relative(thread.resolved_at)));
    }
    if (thread.version && thread.version !== viewing()) {
      container.appendChild(el("p", "cm-note", "Made on another version of the page."));
    }
    var items = el("div", "cm-items");
    thread.comments.forEach(function (comment) {
      var item = el("div", "cm-item");
      var line = byline(comment.author_name, comment.author, comment.created_at);
      if (comment.mine && !comment.deleted) {
        var remove = button("cm-icon cm-delete", "trash", "Delete comment");
        remove.addEventListener("click", function () {
          if (!window.confirm("Delete this comment?")) return;
          write(api("POST", "/" + thread.id + "/" + comment.id + "/delete", {}), error);
        });
        line.appendChild(remove);
      }
      item.appendChild(line);
      item.appendChild(comment.deleted ? el("p", "cm-text cm-deleted", "deleted")
        : el("p", "cm-text", comment.body));
      items.appendChild(item);
    });
    container.appendChild(items);
    var box = replyBox("Reply", function (text, done) {
      api("POST", "/" + thread.id + "/reply", { body: text }).then(function (result) {
        if (!result.ok || !result.body.thread) {
          done(result.body.error || "Could not post the reply.");
          return;
        }
        done();
        upsert(result.body.thread);
        renderAll();
      }, function () { done("Could not reach the server."); });
    });
    box.area.value = draft;
    container.appendChild(box.node);
    container.appendChild(error);
    container.appendChild(box.error);
    box.sync();
    if (focused) box.area.focus();
  }

  // ---- the side panel ------------------------------------------------------------------------

  function openSide() {
    state.side = true;
    side.hidden = false;
    document.body.classList.add("cm-side-open");
    renderSide();
  }
  function closeSide() {
    state.side = false;
    state.panelId = null;
    side.hidden = true;
    side.textContent = "";
    document.body.classList.remove("cm-side-open");
    post({ ontic: "highlight", id: null });
    sendPins();
  }
  function showInPanel(id) {
    state.panelId = id;
    if (!state.side) openSide(); else renderSide();
  }
  function renderSide() {
    if (state.panelId) {
      var thread = threadById(state.panelId);
      if (thread) {
        side.classList.add("cm-detail");
        renderThread(side, thread, {
          close: closeSide,
          back: function () { state.panelId = null; renderSide(); }
        });
        return;
      }
      state.panelId = null;
    }
    side.classList.remove("cm-detail");
    side.textContent = "";
    var head = el("div", "cm-head");
    head.appendChild(el("h2", "cm-title", "Comments"));
    var close = button("cm-icon", "close", "Close");
    close.addEventListener("click", closeSide);
    head.appendChild(close);
    side.appendChild(head);
    var list = el("div", "cm-list");
    var visible = state.threads.filter(function (t) { return state.showResolved || !t.resolved; });
    if (!visible.length) {
      list.appendChild(el("p", "cm-empty", state.threads.length
        ? "No open comments. Turn on Show resolved to see the rest."
        : "No comments yet. Click the comment button, then the page, to add one."));
    }
    visible.forEach(function (thread) { list.appendChild(card(thread)); });
    side.appendChild(list);
  }
  function card(thread) {
    var first = thread.comments[0] || { body: "" };
    var anchor = thread.anchor || {};
    var path = anchor.path || "/";
    var here = path === pagePath();
    var node = el("button", "cm-card");
    node.type = "button";
    if (anchor.snippet || tagOf(anchor)) node.appendChild(elementName(anchor, el("span", "cm-quote")));
    node.appendChild(byline(thread.created_by_name, thread.created_by, thread.created_at));
    node.appendChild(first.deleted ? el("span", "cm-text cm-deleted", "deleted")
      : el("span", "cm-text", first.body));
    var meta = el("span", "cm-meta");
    var replies = thread.comments.length - 1;
    if (replies > 0) meta.appendChild(el("span", "", replies + (replies === 1 ? " reply" : " replies")));
    if (thread.resolved) meta.appendChild(el("span", "cm-badge cm-ok", "Resolved"));
    if (!here) meta.appendChild(el("span", "cm-badge", "On " + path));
    else if (state.orphans[thread.id]) meta.appendChild(el("span", "cm-badge cm-warn", "Not on this version"));
    if (thread.version && thread.version !== viewing()) {
      meta.appendChild(el("span", "cm-badge", "Older version"));
    }
    if (meta.childNodes.length) node.appendChild(meta);
    node.addEventListener("click", function () { openFromList(thread.id); });
    // Its element on the page, outlined while the pointer or the focus is on the card.
    if (here) {
      var on = function () { post({ ontic: "highlight", id: thread.id }); };
      var off = function () { post({ ontic: "highlight", id: null }); };
      node.addEventListener("mouseenter", on);
      node.addEventListener("focus", on);
      node.addEventListener("mouseleave", off);
      node.addEventListener("blur", off);
    }
    return node;
  }

  // Show one thread on the page: go to its path first when the frame shows another one, then let
  // the bridge place its pin. Its answer ("orphans") decides between the pin and the side panel.
  function openFromList(id) {
    var thread = threadById(id);
    if (!thread) return;
    state.pending = id;
    if (narrow() && state.side) closeSide();
    var path = (thread.anchor && thread.anchor.path) || "/";
    if (path !== pagePath()) {
      state.seq += 1; // answers on their way from the current document are stale
      frame().src = origin + prefix() + path;
      return;
    }
    sendPins();
  }

  // ---- messages from the bridge --------------------------------------------------------------

  function finite(point) {
    return point && typeof point.x === "number" && typeof point.y === "number"
      && isFinite(point.x) && isFinite(point.y);
  }
  window.addEventListener("message", function (event) {
    if (event.origin !== origin) return;
    var f = frame();
    if (!f || event.source !== f.contentWindow) return;
    var msg = event.data;
    if (!msg || typeof msg !== "object" || typeof msg.ontic !== "string") return;
    if (msg.ontic === "nav" && typeof msg.path === "string" && msg.path.charAt(0) === "/") {
      state.framePath = msg.path;
    }
    if (!pop) return; // the bar is not wired yet; it sends the pins once it is
    switch (msg.ontic) {
      case "ready":
      case "nav":
        onFrame();
        break;
      case "engaged":
        setMenu(false);
        if (!draftText()) closePop();
        break;
      case "comment-at":
        if (!state.on || !msg.anchor || typeof msg.anchor !== "object" || !finite(msg.point)) return;
        if (draftText()) return; // keep what was typed
        openCompose(msg.anchor, msg.point);
        break;
      case "pin-open":
        if (typeof msg.id === "string" && ID.test(msg.id) && finite(msg.point)) {
          openThread(msg.id, msg.point);
        }
        break;
      case "orphans":
        state.orphans = {};
        (Array.isArray(msg.ids) ? msg.ids : []).forEach(function (id) {
          if (typeof id === "string" && ID.test(id)) state.orphans[id] = true;
        });
        if (state.pending && msg.seq === state.seq) {
          var pending = state.pending;
          state.pending = null;
          if (state.orphans[pending]) showInPanel(pending);
          else post({ ontic: "pin-focus", id: pending });
        }
        if (state.side && !state.panelId) renderSide();
        break;
      case "comment-escape":
        onEscape();
        break;
    }
  });

  // ---- the controls --------------------------------------------------------------------------

  function setMenu(open) {
    var menu = $("cm-menu");
    if (!menu) return;
    menu.hidden = !open;
    $("cm-more").setAttribute("aria-expanded", open ? "true" : "false");
  }
  function onEscape() {
    if (!$("cm-menu").hidden) { setMenu(false); $("cm-more").focus(); return; }
    if (!pop.hidden) { closePop(); return; }
    if (state.on) { setMode(false); return; }
    if (state.side) closeSide();
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("cm")) return;
    pop = el("div", "cm-pop");
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "Comment");
    pop.hidden = true;
    side = el("aside", "cm-side");
    side.setAttribute("aria-label", "Comments");
    side.hidden = true;
    document.body.appendChild(pop);
    document.body.appendChild(side);

    $("cm-btn").addEventListener("click", function () { setMode(!state.on); });
    $("cm-more").addEventListener("click", function () { setMenu($("cm-menu").hidden); });
    $("cm-all").addEventListener("click", function () {
      setMenu(false);
      state.panelId = null;
      openSide();
    });
    $("cm-resolved").addEventListener("click", function () {
      state.showResolved = !state.showResolved;
      $("cm-resolved").setAttribute("aria-checked", state.showResolved ? "true" : "false");
      renderAll();
    });
    document.addEventListener("pointerdown", function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      if (!target.closest("#cm")) setMenu(false);
      if (!pop.hidden && !pop.contains(target) && !target.closest("#cm") && !draftText()) {
        closePop();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") onEscape();
    });
    // Fresh comments every 30 seconds while the tab is visible, and whenever it comes back.
    var later = function () { load(false); };
    setInterval(later, 30000);
    window.addEventListener("focus", later);
    document.addEventListener("visibilitychange", later);
    updateCount();
    load(true);
  });
})();
