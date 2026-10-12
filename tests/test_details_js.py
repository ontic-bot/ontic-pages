"""The Details panel of the info button (details.js, in the served bar.js), in node with the small
fake DOM of test_edit_js: asked only when opened, once per version, drawn with textContent."""

import pytest
from test_edit_js import NODE, run, served

pytestmark = pytest.mark.skipif(not NODE, reason="needs node")

CURRENT, OLD = "20261008T090000Z", "20261007T153000Z"
SHA = "0123456789abcdef0123456789abcdef01234567"
FULL = {
    "name": "report", "version": OLD, "current": CURRENT, "visibility": "ontic",
    "published_at": "2026-10-07T15:30:00+00:00", "published_by": "owner@onticlabs.io",
    "description": "Depth <b>eval</b>", "files": 12, "pillar": "world-models",
    "tags": ["depth", "<b>x</b>"],
    "meta": {"edited_from": "20261006T120000Z", "seed": "7", "run": "https://wandb.ai/x/y",
             "cmd": "javascript:alert(1)"},
    "git": {"remote": "git@github.com:onticlabs/experiments.git", "branch": "main",
            "commit": SHA, "dirty": True, "repo_url": "https://github.com/onticlabs/experiments",
            "commit_url": f"https://github.com/onticlabs/experiments/commit/{SHA}"},
}  # fmt: skip

PANEL = r"""
const PAGE = { name: "report", content_origin: CONTENT, current: CURRENT, role: "viewer",
  visibility: "ontic", published_by: "owner@onticlabs.io", email_domain: "onticlabs.io",
  versions: [{ version: CURRENT, published_at: "", description: "" },
             { version: OLD, published_at: "", description: "", pillar: "data",
               tags: ["eval", "<i>x</i>"] }] };
const facts = { page: PAGE, view: { path: "/", version: VIEWING } };
const frame = E("iframe", { id: "frame" });
frame.contentWindow = { postMessage() {} };
const access = E("select", { id: "s-access" });
access.options = [];
access.selectedIndex = 0;
const ids = ["m-by", "m-owner", "m-time", "m-desc", "m-labels", "m-versions", "old", "s-note",
  "s-explain", "s-owner", "share-btn", "share-panel", "m-raw", "slow", "retry"];
let focused = null;
El.prototype.focus = function () { focused = this.id; };
const titleBtn = E("button", { id: "title-btn" });
const details = E("button", { id: "m-details" });
const main = E("div", { id: "m-main" }, [details]);
const menu = E("div", { id: "title-menu", role: "menu" }, [main]);
const infoBtn = E("button", { id: "info-btn", "aria-expanded": "false" });
const dtitle = E("span", { id: "d-title", tabindex: "-1" }, ["Details"]);
const dbody = E("div", { id: "d-body" });
const panel = E("div", { id: "info-panel", role: "dialog" }, [
  E("div", { class: "d-head" }, [dtitle, E("a", { id: "d-open" })]), dbody]);
menu.hidden = panel.hidden = true;
const stage = E("main", { id: "stage" }, [frame,
  E("div", { id: "status" }, [E("div", { class: "spinner" })])]);
const bar = E("header", { class: "bar" }, [E("span", { class: "grow" }), titleBtn, menu, infoBtn,
  panel, access, ...ids.map((id) => E("div", { id }))]);
const body = E("body", {}, [bar, stage]);
const byId = (id) => (id === "ontic-facts" ? { textContent: JSON.stringify(facts) }
  : body.querySelectorAll("*").find((n) => n.id === id) || null);
const document = target({ body, getElementById: byId, visibilityState: "visible",
  createElement: (t) => new El(t), querySelector: (s) => body.querySelector(s),
  querySelectorAll: (s) => body.querySelectorAll(s) });
const window = target({});
const fetches = [], copied = [];
function fetch(url, options) {
  fetches.push([url, (options && options.method) || "GET"]);
  return Promise.resolve({ ok: STATUS === 200, status: STATUS,
    json: () => Promise.resolve(ANSWER) });
}
const navigator = { clipboard: {
  writeText: (t) => { copied.push(t); return Promise.resolve(); } } };
class FixedDate extends Date { static now() { return Date.parse("2026-10-09T15:30:00Z"); } }
const location = { origin: APEX, href: APEX + "/report/", hash: "", assign() {} };
vm.runInNewContext(SOURCE, { window, document, location, history: { replaceState() {} }, URL,
  fetch, navigator, JSON, Date: FixedDate, setTimeout: () => 0, clearTimeout() {},
  setInterval: () => 0, encodeURIComponent });
fire(document, "DOMContentLoaded");
// The panel as text: [text](href) for links (+ " new" in a new tab), {copy} for copy buttons.
function show(n) {
  if (n.nodeType === 3) return n.data;
  const inner = n.childNodes.map(show).join("");
  if (n.tagName === "A") return "[" + inner + "](" + n.href + (n.target ? " new" : "") + ")";
  if (n.tagName === "BUTTON" && n.classList.contains("d-copy")) return "{copy}";
  if (["P", "DT", "DD", "DIV", "BUTTON"].includes(n.tagName)) {
    return "\n" + n.tagName.toLowerCase() + ": " + inner;
  }
  return inner;
}
const state = () => ({ menu: !menu.hidden, panel: !panel.hidden,
  expanded: infoBtn.getAttribute("aria-expanded"), focused });
// VIA: "button" opens with the info button first, then the menu's Details item; "menu" the
// other way round.
const first = VIA === "menu" ? details : infoBtn, second = VIA === "menu" ? infoBtn : details;
(async () => {
  const out = {};
  titleBtn.click();
  out.menu = [state(), fetches.length];  // nothing asked before the panel opens
  out.labels = [byId("m-labels").textContent, byId("m-labels").hidden];
  first.click();
  out.loading = [state(), dbody.childNodes.map(show).join(""), fetches.slice()];
  for (let i = 0; i < 5; i++) await tick();
  out.panel = dbody.childNodes.map(show).join("").split("\n").filter(Boolean);
  const copy = dbody.querySelectorAll("button").filter((b) => b.classList.contains("d-copy"));
  copy.forEach((b) => b.click());
  out.copied = copied.slice();
  out.copyLabels = copy.map((b) => b.getAttribute("aria-label"));
  focused = null;
  fire(panel, "keydown", { key: "Escape" });
  out.escape = state();
  titleBtn.click();
  second.click();
  out.reopened = [state(), fetches.length];  // the same version is not asked again
  details.click();
  out.again = state();  // the Details item leaves an open panel open
  infoBtn.click();
  out.toggled = state();
  console.log(JSON.stringify(out));
})();
"""


def panel(answer, viewing=None, status=200, via="button"):
    """Opens the title menu, then the panel (with the info button, or the menu's Details item),
    copies every value, Escape, opens it the other way, again from the menu, then the button."""
    return run(
        PANEL, served("bar.js"), CURRENT=CURRENT, OLD=OLD, VIEWING=viewing, ANSWER=answer,
        STATUS=status, VIA=via,
    )  # fmt: skip


def test_panel_for_an_old_version():
    out = panel(FULL, viewing=OLD)
    menu = {"menu": True, "panel": False, "expanded": "false", "focused": None}
    shown = {"menu": False, "panel": True, "expanded": "true", "focused": "d-title"}
    closed = {"menu": False, "panel": False, "expanded": "false", "focused": "info-btn"}
    assert out["menu"] == [menu, 0]
    assert out["labels"] == ["Data \u00b7 eval \u00b7 <i>x</i>", False]  # the viewed version's
    state, text, fetches = out["loading"]
    assert state == shown  # the info button closes the menu and focus moves into the panel
    assert text == "\np: Loading…"
    assert fetches == [[f"/_api/pages/report/versions/{OLD}", "GET"]]
    assert out["panel"] == [
        "p: Depth <b>eval</b>",  # text, never HTML
        "p: Edited in the browser from [20261006T120000Z](/report/_v/20261006T120000Z/)",
        "dt: Published",
        "dd: ",
        "div: by owner@onticlabs.io",
        "div: 07.10.2026 17:30 (2 days ago)",  # the viewer's time zone (Berlin here)
        "dt: Version",
        f"dd: {OLD}{{copy}}",  # an old version: no "current" tag
        "dt: Visibility",
        "dd: Team",
        "dt: Pillar",
        "dd: World models",
        "dt: Tags",
        "dd: depth<b>x</b>",  # text, never HTML
        "dt: Git",
        "dd: ",
        "div: [git@github.com:onticlabs/experiments.git]"
        "(https://github.com/onticlabs/experiments new)",
        "div: branch main",
        f"div: commit [01234567](https://github.com/onticlabs/experiments/commit/{SHA} new)"
        "{copy}uncommitted changes",
        "dt: Files",
        "dd: 12 files",
        "p: Metadata",
        "dt: seed",
        "dd: 7{copy}",
        "dt: run",
        "dd: [https://wandb.ai/x/y](https://wandb.ai/x/y new){copy}",
        "dt: cmd",
        "dd: javascript:alert(1){copy}",  # only http(s) values become links
    ]
    assert out["copied"] == [OLD, SHA, "7", "https://wandb.ai/x/y", "javascript:alert(1)"]
    assert out["copyLabels"][:2] == ["Copy version id", "Copy commit"]
    assert out["escape"] == closed  # Escape closes it and focus goes back to the info button
    assert out["reopened"] == [shown, 1]  # the menu's Details item: closes the menu, cached
    assert out["again"] == shown
    assert out["toggled"] == {**closed, "focused": "d-title"}  # the button closes it again


def test_panel_from_the_title_menu():
    out = panel(FULL, viewing=OLD, via="menu")
    assert out["menu"][1] == 0
    state, text, fetches = out["loading"]
    assert state == {"menu": False, "panel": True, "expanded": "true", "focused": "d-title"}
    assert fetches == [[f"/_api/pages/report/versions/{OLD}", "GET"]]
    assert out["panel"][0] == "p: Depth <b>eval</b>"
    assert out["escape"]["focused"] == "info-btn" and not out["escape"]["panel"]
    assert out["reopened"] == [state, 1]  # the info button: cached


def test_panel_current_version_without_github():
    answer = {**FULL, "version": CURRENT, "meta": {},
              "git": {"remote": "https://gitlab.com/a/b.git", "commit": SHA, "dirty": False,
                      "repo_url": None, "commit_url": None}}  # fmt: skip
    out = panel(answer)
    assert out["loading"][2] == [[f"/_api/pages/report/versions/{CURRENT}", "GET"]]
    text = out["panel"]
    assert f"dd: {CURRENT}current{{copy}}" in text
    assert "div: https://gitlab.com/a/b.git" in text
    assert "div: commit 01234567{copy}" in text  # no link, no dirty marker
    assert not any("Edited in the browser" in line or "Metadata" in line for line in text)


def test_panel_empty_and_signed_out():
    out = panel({"name": "report", "version": CURRENT, "current": CURRENT, "visibility": "public"})
    assert out["panel"] == [
        "p: No details recorded.",
        "dt: Version",
        f"dd: {CURRENT}current{{copy}}",
        "dt: Visibility",
        "dd: Public",
    ]
    out = panel({"error": "sign in to see the details"}, status=401)
    assert out["panel"] == [
        "p: Sign in to see the details. "
        "[Sign in](/oauth2/start?rd=https%3A%2F%2Fpages.test%2Freport%2F)"
    ]
    assert out["reopened"][1] == 2  # a failure is asked again
    out = panel({"error": "not allowed"}, status=403)
    assert out["panel"] == ["p: not allowed", "button: Try again"]


def test_panel_never_uses_html():
    source = served("bar.js")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source


def test_current_tag_follows_the_bar():
    # A kept answer may name an older current version (a save or a publish made another one
    # current since): the tag follows the bar's facts.
    out = panel({**FULL, "version": CURRENT, "current": OLD, "meta": {}})
    assert f"dd: {CURRENT}current{{copy}}" in out["panel"]
    out = panel({**FULL, "current": OLD}, viewing=OLD)
    assert f"dd: {OLD}{{copy}}" in out["panel"]
