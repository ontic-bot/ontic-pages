"""The bar around every page.

The shell is a small HTML page on the apex: the bar drawn in static HTML, the page facts as JSON,
and a frame showing the page from its own host, <name>.<suffix>. Its script and style are served
at content-hashed URLs so browsers keep them for a year. The bridge (static/bridge.js) is added
to a page's HTML only when that HTML is loaded into the frame; it reports navigation, scrolling
and readiness to the shell by postMessage, and hands links to the apex to the shell, which opens
them in the whole tab (the apex refuses to be framed). Stored files are never changed.

The info button left of the comments (and the title menu's Details item) opens the Details panel
(static/details.js, details.css): what page.json says about the version being viewed, asked from
the API when it opens.

Signed-in viewers also get the comments (static/comments.js and comments.css in the bar,
static/pins.js served as the second part of the bridge): threads attached to elements of the
page, with pins and outlines on it, a thread popover and a side panel with every thread. The
owner, on the current version, can edit the page's text in place (static/edit.js in bar.js,
static/edit-bridge.js in the bridge); there is no button for it.

The command line's sign-in page (login_html, static/login.js) lives here too: it is the other
HTML page on the apex with a script.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from importlib.resources import files

from .info import PILLAR_LABELS, stamp
from .store import content_type

ACCESS = {
    "private": ("Private", "Only the owner can open it, signed in."),
    "ontic": ("Team", "Anyone signed in with an @{domain} account can open it."),
    "public": ("Public", "Anyone with the link can open it, without signing in."),
}
SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
SANDBOX += " allow-downloads"
BAR_RE = re.compile(r"^/_bar/(bar|login|comments|overview)\.([0-9a-f]{1,64})\.(js|css)$")
# The bridge is served as one script: bridge.js, then the comment pins (pins.js).
BRIDGE_RE = re.compile(r"^/_bridge\.([0-9a-f]{1,64})\.js$")

ICONS = {
    "home": '<path d="M4 11.5 12 5l8 6.5"/><path d="M6.5 10v9h11v-9"/>',
    "chevron": '<path d="M7 10l5 5 5-5"/>',
    "private": '<rect x="5.5" y="11" width="13" height="9" rx="2"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',  # noqa: E501
    "ontic": '<circle cx="9" cy="9" r="3"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><path d="M15.5 6.2a3 3 0 0 1 0 5.6"/><path d="M17 14.3a5.5 5.5 0 0 1 3.5 4.7"/>',  # noqa: E501
    "public": '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/>',  # noqa: E501
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.6v.4"/>',
    "comment": '<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>',  # noqa: E501
}


def icon(name: str, cls: str = "") -> str:
    extra = f' class="{cls}"' if cls else ""
    return f'<svg{extra} viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


# The files served, each made of these static files in order: a feature in a file of its own
# (comment pins, edit text in place) comes after the file it extends.
BUNDLES = {
    "bar.js": ("bar.js", "details.js", "edit.js"),
    "bar.css": ("bar.css", "details.css", "edit.css"),
    "comments.js": ("comments.js",),
    "comments.css": ("comments.css",),
    "bridge.js": ("bridge.js", "pins.js", "edit-bridge.js"),
    "login.js": ("login.js",),
    "overview.js": ("overview.js",),
    "overview.css": ("overview.css",),
}


class Assets:
    """The files in BUNDLES from the package, each with a hash of its bytes. The apex origin is
    written into the bridge, which posts only to it."""

    def __init__(self, apex_origin: str):
        self.items: dict[str, tuple[bytes, str]] = {}
        static = files("ontic_pages") / "static"
        for fname, parts in BUNDLES.items():
            data = b"\n".join((static / part).read_bytes() for part in parts)
            data = data.replace(b"__APEX__", json.dumps(apex_origin).encode())
            self.items[fname] = (data, hashlib.sha256(data).hexdigest()[:16])

    def url(self, fname: str) -> str:
        stem, ext = fname.split(".")
        digest = self.items[fname][1]
        return f"/_bridge.{digest}.js" if stem == "bridge" else f"/_bar/{stem}.{digest}.{ext}"

    def lookup(self, path: str, content_host: bool) -> tuple[bytes, str, bool] | None:
        """(bytes, content type, immutable) for an asset URL. Any hash answers with the current
        file, so a shell from before a deploy still works, but only the current hash is kept."""
        m = BRIDGE_RE.match(path) if content_host else BAR_RE.match(path)
        if not m:
            return None
        if content_host:
            fname, asked = "bridge.js", m.group(1)
        else:
            fname, asked = f"{m.group(1)}.{m.group(3)}", m.group(2)
        if fname not in self.items:  # login.css
            return None
        data, digest = self.items[fname]
        return data, content_type(fname), asked == digest

    def bridge_tag(self, version: str) -> bytes:
        """The bridge's script tag, with the version the page was served from: the bridge
        tells the bar (ready, nav), so the bar knows what the frame really shows."""
        src, served = self.url("bridge.js"), html.escape(version, quote=True)
        return f'<script src="{src}" data-version="{served}"></script>'.encode()

    @property
    def bridge_hash(self) -> str:
        return self.items["bridge.js"][1]


def shell_csp(frame_origins: str) -> str:
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; connect-src 'self'; frame-src {frame_origins}; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    )


HEAD_RES = [
    re.compile(rb"<head(?:\s[^>]*)?>", re.I),
    re.compile(rb"<html(?:\s[^>]*)?>", re.I),
    re.compile(rb"<!doctype[^>]*>", re.I),
]
BOM = b"\xef\xbb\xbf"


def inject(data: bytes, tag: bytes) -> bytes:
    """`tag` right after <head>, else after <html>, else after the doctype, else first (after a
    byte order mark), so it runs before the page's own scripts and never sets quirks mode."""
    start = data[:65536]
    for rx in HEAD_RES:
        m = rx.search(start)
        if m:
            return data[: m.end()] + tag + data[m.end() :]
    skip = len(BOM) if data.startswith(BOM) else 0
    return data[:skip] + tag + data[skip:]


def json_for_html(value) -> str:
    """JSON that cannot end a <script> element or open a comment inside one."""
    text = json.dumps(value, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def versions_html(page: dict, viewing: str | None) -> str:
    e, name = html.escape, page["name"]
    items = []
    for v in page["versions"]:
        version = v["version"]
        current = version == page["current"]
        href = f"/{name}/" if current else f"/{name}/_v/{version}/"
        here = ' aria-current="page"' if version == (viewing or page["current"]) else ""
        tag = '<span class="tag">current</span>' if current else ""
        items.append(
            f'<li><a role="menuitem" href="{e(href)}"{here}><span class="when">'
            f"{e(stamp(v.get('published_at', '')) or version)}</span>{tag}"
            f'<span class="desc">{e(v.get("description", ""))}</span></a></li>'
        )
    return "".join(items)


def comments_html() -> str:
    """The comment toggle (with the count of open threads) and its menu: for signed-in viewers
    only, wired by static/comments.js."""
    return (
        '<div class="pop cm" id="cm">'
        '<button class="icon-btn cm-toggle" id="cm-btn" type="button" aria-pressed="false" '
        f'aria-label="Comment" title="Comment">{icon("comment")}'
        '<span class="cm-count" id="cm-count" hidden></span></button>'
        '<button class="icon-btn cm-more" id="cm-more" type="button" aria-haspopup="menu" '
        'aria-expanded="false" aria-controls="cm-menu" aria-label="Comment options">'
        f"{icon('chevron', 'chev')}</button>"
        '<div class="menu" id="cm-menu" role="menu" hidden>'
        '<button class="item" type="button" role="menuitem" id="cm-all">Show all comments'
        "</button>"
        '<button class="item" type="button" role="menuitemcheckbox" aria-checked="false" '
        'id="cm-resolved">Show resolved<span class="cm-switch" aria-hidden="true"></span></button>'
        "</div></div>\n"
    )


def info_html(name: str) -> str:
    """The info button and its Details panel, for every viewer, wired by static/details.js."""
    return (
        '<div class="pop" id="info-pop">'
        '<button class="icon-btn" id="info-btn" type="button" aria-haspopup="dialog" '
        'aria-expanded="false" aria-controls="info-panel" aria-label="Details" title="Details">'
        f"{icon('info')}</button>"
        '<div class="panel info-panel" id="info-panel" role="dialog" aria-labelledby="d-title" '
        'hidden><div class="d-head">'
        '<span class="d-title" id="d-title" tabindex="-1">Details</span>'
        f'<a class="d-open" id="d-open" href="/{html.escape(name)}/_info">Open as a page</a></div>'
        '<div class="d-body" id="d-body" aria-live="polite"></div>'
        "</div></div>\n"
    )


def shell_html(page: dict, view: dict, assets: Assets) -> str:
    """page: the facts from the API. view: version (None for current), path (in the frame),
    src (the frame's URL), raw (the same without the bar), signin (a URL, or "")."""
    e, name = html.escape, page["name"]
    viewing = view["version"]
    shown = next((v for v in page["versions"] if v["version"] == (viewing or page["current"])), {})
    by = page.get("published_by", "")
    viewer = page.get("viewer", "")
    comments_head = comments = ""
    if viewer:
        initial = e(viewer[:1].upper())
        who = f'<span class="avatar" title="Signed in as {e(viewer)}">{initial}</span>'
        comments_head = (
            f'<link rel="stylesheet" href="{assets.url("comments.css")}">\n'
            f'<script src="{assets.url("comments.js")}"></script>\n'
        )
        comments = comments_html()
    else:
        who = f'<a class="signin" id="signin" href="{e(view["signin"])}">Sign in</a>'
    owner_can = page.get("role") == "owner"
    options = "".join(
        f'<option value="{level}" data-explain="{e(text.format(domain=page["email_domain"]))}"'
        f"{' selected' if level == page['visibility'] else ''}>{label}</option>"
        for level, (label, text) in ACCESS.items()
    )
    explain = ACCESS[page["visibility"]][1].format(domain=page["email_domain"])
    old = viewing is not None and viewing != page["current"]
    labels = " \u00b7 ".join(
        filter(None, [PILLAR_LABELS.get(shown.get("pillar", "")), *shown.get("tags", [])])
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{e(name)}</title>
<link rel="stylesheet" href="{assets.url("bar.css")}">
<noscript><style>.frame {{ opacity: 1 }}</style></noscript>
<script id="ontic-facts" type="application/json">{json_for_html({"page": page, "view": view})}</script>
<script src="{assets.url("bar.js")}"></script>
{comments_head}</head>
<body data-visibility="{e(page["visibility"])}">
<header class="bar">
<a class="icon-btn" href="/" title="All pages" aria-label="All pages">{icon("home")}</a>
<div class="pop" id="title-pop">
<button class="title" id="title-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="title-menu"><span class="title-text" id="title-text">{e(name)}</span>{icon("chevron", "chev")}</button>
<div class="menu" id="title-menu" role="menu" hidden>
<div id="m-main">
<div class="facts">
<p id="m-by"{"" if by else " hidden"}>Page by <span id="m-owner">{e(by)}</span></p>
<p class="muted" id="m-updated">Updated <time id="m-time" datetime="{e(shown.get("published_at", ""))}">{e(stamp(shown.get("published_at", "")))}</time></p>
<p id="m-desc"{"" if shown.get("description") else " hidden"}>{e(shown.get("description", ""))}</p>
<p class="muted" id="m-labels"{"" if labels else " hidden"}>{e(labels)}</p>
<p id="m-milestone" hidden>Milestone: <a id="m-milestone-link" href="/"></a></p>
</div>
<p class="label">Versions</p>
<ol class="versions" id="m-versions">{versions_html(page, viewing)}</ol>
<div class="sep"></div>
<button class="item" type="button" role="menuitem" data-copy>Copy link</button>
<a class="item" role="menuitem" id="m-raw" href="{e(view["raw"])}" target="_blank" rel="noopener">Open without the bar</a>
<button class="item" type="button" role="menuitem" id="m-details" aria-controls="info-panel">Details</button>
<a class="item" role="menuitem" href="/">All pages</a>
</div>
</div>
</div>
<a class="old" id="old" href="/{e(name)}/"{"" if old else " hidden"} title="You are looking at an older version. Open the current one.">Old version<span class="wide"> &middot; view current</span></a>
<span class="grow"></span>
{info_html(name)}{comments}{who}
<div class="pop" id="share-pop">
<button class="share" id="share-btn" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="share-panel">{icon("private", "vis vis-private")}{icon("ontic", "vis vis-ontic")}{icon("public", "vis vis-public")}<span class="wide">Share</span></button>
<div class="panel" id="share-panel" role="dialog" aria-label="Share {e(name)}" hidden>
<p class="label">Owner</p>
<p class="owner"><span class="avatar">{e((by or "?")[:1].upper())}</span><span id="s-owner">{e(by or "Sign in to see who")}</span></p>
<label class="label" for="s-access">General access</label>
<select id="s-access"{"" if owner_can else " disabled"}>{options}</select>
<p class="muted" id="s-explain">{e(explain)}</p>
<p class="muted" id="s-note"{" hidden" if owner_can else ""}>Only the owner can change this.</p>
<p class="error" id="s-error" hidden></p>
<div class="sep"></div>
<button class="item" type="button" data-copy>Copy link</button>
</div>
</div>
</header>
<main class="stage" id="stage">
<iframe class="frame" id="frame" src="{e(view["src"])}" title="{e(name)}" sandbox="{SANDBOX}" referrerpolicy="no-referrer" allow="fullscreen; clipboard-write; autoplay" allowfullscreen></iframe>
<div class="status" id="status" hidden><div class="spinner" aria-hidden="true"></div><p id="slow" hidden>This page is slow to load. <button type="button" id="retry">Retry</button></p></div>
</main>
</body></html>
"""  # noqa: E501


def overview_csp(upload_origin: str, content_origins: str) -> str:
    """The overview's policy: its own script, and uploads (PUT) straight to the bucket. Media
    dropped into the composer are previewed from blob: URLs, an edited update's own from the
    pages' hosts."""
    upload = f" {upload_origin}" if upload_origin else ""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob: {content_origins}; media-src 'self' blob: {content_origins}; "
        f"connect-src 'self'{upload}; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    )


def overview_html(email: str, assets: Assets, fallback: str) -> str:
    """The research overview at the apex: milestones, findings and the reports table, drawn by
    static/overview.js from /_api/overview. `fallback`: the plain list, without script."""
    e = html.escape
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Ontic research</title>
<link rel="stylesheet" href="{assets.url("overview.css")}">
<script src="{assets.url("overview.js")}" defer></script>
</head>
<body data-viewer="{e(email)}">
<div id="app"><noscript>{fallback}</noscript></div>
</body></html>
"""


def login_html(email: str, code: str, problem: str, assets: Assets) -> str:
    """`ontic-pages login` opens this: who you are, the end of the code to compare with the
    terminal, and Allow (static/login.js posts the code). `problem` replaces the button."""
    e = html.escape
    if problem:
        action = f'<p class="error">{e(problem)}</p>'
    else:
        action = (
            f'<p>Your terminal shows a code ending in <b class="code">{e(code[-4:])}</b>. Allow '
            "only if it matches and you just ran <code>ontic pages login</code>.</p>\n"
            f'<p><button id="allow" type="button" data-code="{e(code)}">Allow</button></p>\n'
            '<p id="result" role="status"></p>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sign in ontic-pages</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<script src="{assets.url("login.js")}"></script>
<style>
body {{ font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 34rem; padding: 0 1rem; }}
.code {{ font-family: ui-monospace, monospace; font-size: 1.3rem; letter-spacing: .1rem; }}
button {{ font: inherit; padding: .4rem 1.4rem; border-radius: .4rem; cursor: pointer; }}
.error {{ color: #b33; }}
</style></head><body>
<h1>Sign in the command line</h1>
<p>The <code>ontic-pages</code> command line on your computer asks to act as
<b>{e(email)}</b>: publish pages and change your own, for 30 days.</p>
{action}
</body></html>
"""
