"""The gateway: a small web server for the pages in the bucket.

On the apex (the host of ONTIC_PAGES_URL, pages.onticlabs.io):

    GET  /                              a listing of the pages this viewer may open
    GET  /<name>/<path>                 the shell: the bar, and a frame showing <path> from the
                                        page's own host
    GET  /<name>/_v/<version>/<path>    the same, for one version
    GET  /<name>/_info                  what page.json says about the page, and its versions
    GET  /public/<name>/<path>          old links: redirects to /<name>/<path>
    GET  /_api/pages/<name>             the page facts this viewer may see, as JSON
    GET  /_api/pages/<name>/info        every version's page.json (signed in)
    GET  /_api/pages/<name>/versions/<version>   one version's details for the bar's Details
                                        panel (signed in, from the bar or the command line)
    GET  /_api/pages/<name>/files       a version's files with presigned GET URLs (pull.py)
    GET  /_api/pages                    the pages this viewer may open (signed in)
    GET  /_api/me                       who is asking
    POST /_api/pages/<name>/visibility  {"visibility": ...}, by the owner only
    POST /_api/pages/<name>/current     {"version": ...}, by the owner only
    POST /_api/pages/<name>/versions    publish: start (uploads.py); then .../<version>/files for
                                        more of the file list, and .../<version>/commit
    GET  /_api/pages/<name>/comments    every comment thread (comments.py), signed in
    POST /_api/pages/<name>/comments    {"anchor", "version", "body"}: a new thread; then
                                        .../<thread>/reply {"body"}, .../<thread>/resolve
                                        {"resolved"}, .../<thread>/<comment>/delete (its author)
    POST /_api/pages/<name>/edits       {version, path, changes}: text edited in the bar, saved
                                        as a new version, by the owner only (edits.py)
    POST /_api/pages/<name>/labels      {pillar, tags, milestone, title, description}: the
                                        owner relinks a report (a new version, files copied)
    GET  /_api/pages/<name>/source      a Markdown update's source, for Edit in the overview
    GET  /                              the research overview (static/overview.js), signed in
    GET  /_api/overview                 what it draws: reports, milestones, saved views
    GET  /_api/milestones               every milestone (milestones.py), team only
    POST /_api/milestones               a new one; .../<id> changes it (anyone on the team)
    POST /_api/views                    a saved view; .../<id> changes it, .../<id>/delete
                                        removes it (its creator)
    GET  /_cli/login?code=              the command line's sign-in: Allow (tokens.py)
    POST /_api/cli/approve              {"code": ...}, from that page
    GET  /_api/cli/token?code=          the command line asks for its token
    GET  /_bar/bar.<hash>.js|css        the shell's script and style (and login.<hash>.js)

On a page's own host, <name>.<content suffix> (the content host):

    GET  /<path>                        the current version's file, index.html for folders
    GET  /_v/<version>/<path>           a given version's file
    GET  /_bridge.<hash>.js             the bridge, added to HTML shown in the shell's frame

    A browser opening a content URL as a page of its own (Sec-Fetch-Dest: document) is sent to
    the shell, unless the URL has ?raw=1 or the page itself navigated there.

On any host: GET /_health. On other hosts only (Caddy asks on 127.0.0.1): GET /_tls-ask?domain=.

Who is asking comes from oauth2-proxy (auth.py), never from identity request headers, or from a
signed token (`Authorization: Bearer`, the command line, tokens.py), which is refused from a
browser (any Origin or Sec-Fetch-Site). Access, checked before every answer: public anyone,
ontic an @<email domain> address, private the current version's publisher. Writes go to the apex
only: from the apex itself (Sec-Fetch-Site, Origin) or with a token.
"""

from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from botocore.exceptions import ClientError

from .auth import (
    DEFAULT_AUTH_URL,
    Identity,
    IdentityUnavailable,
    OAuth2Proxy,
    RateLimit,
    allowed,
    fixed,
)
from .cache import MB, FileCache, LabelCache, PageCache
from .comments import Comments, listing
from .comments import view as thread_view
from .config import DEFAULT_URL
from .edits import Phases
from .edits import save as save_edits
from .info import (
    DESCRIPTION_CHARS,
    NAME_CHARS,
    PILLAR_LABELS,
    details,
    history,
    info_html,
    pillar_of,
    short,
    stamp,
    tags_of,
    version_stamp,
)
from .milestones import ID_RE as MILESTONE_ID_RE
from .milestones import VIEW_ID_RE, Milestones, Views, now
from .publish import LABEL_KEYS, MARKDOWN_FILE, clean_title, effective
from .pull import presigned_files
from .shell import (
    Assets,
    inject,
    login_html,
    overview_csp,
    overview_html,
    shell_csp,
    shell_html,
)
from .store import (
    NAME_RE,
    PILLARS,
    RESERVED_NAMES,
    VERSION_RE,
    VISIBILITIES,
    Store,
    check_name,
    content_type,
)
from .tokens import LoginCodes, check_secret, sign, verify
from .uploads import Refused, Uploads

CHUNK = 256 * 1024
YEAR = "max-age=31536000, immutable"
VERSIONED = re.compile(r"^_v/(\d{8}T\d{6}Z)(/.*)?$")
API_PAGE = re.compile(r"^/_api/pages/([^/]+)$")
API_INFO = re.compile(r"^/_api/pages/([^/]+)/info$")
API_FILES = re.compile(r"^/_api/pages/([^/]+)/files$")
API_VERSION = re.compile(r"^/_api/pages/([^/]+)/versions/(\d{8}T\d{6}Z)$")
API_WRITE = re.compile(
    r"^/_api/pages/([^/]+)/"
    r"(visibility|current|edits|labels|delete|versions|versions/(\d{8}T\d{6}Z)/(files|commit))$"
)
API_SOURCE = re.compile(r"^/_api/pages/([^/]+)/source$")
RESEARCH_WRITE = re.compile(
    r"^/_api/(?:milestones(?:/([a-z0-9][a-z0-9-]{0,47}))?|views(?:/([0-9a-f]{8})(/delete)?)?)$"
)
API_COMMENTS = re.compile(r"^/_api/pages/([^/]+)/comments$")
COMMENT_WRITE = re.compile(
    r"^/_api/pages/([^/]+)/comments(?:/([0-9a-f]{16})/(?:(reply|resolve)|([0-9a-f]{16})/delete))?$"
)
APPROVE = "/_api/cli/approve"
PREVIEW = "/_api/preview"
SECRET_QUERY = re.compile(r"\b(code)=[^&\s]+")
EXPIRED = "not signed in, or the sign-in expired: run ontic pages login"
SMALL_BODY, LIST_BODY = 4096, 64 * 1024  # Caddy refuses anything over 64 KB anyway
COMMENT_BODY = 32 * 1024  # 4000 characters of text, as UTF-8 and JSON, and the anchor
LOGIN_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
PLAIN_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)


@dataclass(frozen=True)
class Site:
    """Where the gateway answers: url is the apex (scheme and host), and each page's content
    lives at <name>.<suffix> (the apex host when suffix is empty)."""

    url: str = DEFAULT_URL
    suffix: str = ""
    email_domain: str = "onticlabs.io"

    @property
    def apex(self) -> str:
        return urlsplit(self.url).netloc.lower()

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme or "https"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.apex}"

    @property
    def content_suffix(self) -> str:
        return (self.suffix or self.apex).lower().strip(".")

    def content_origin(self, name: str) -> str:
        return f"{self.scheme}://{name}.{self.content_suffix}"

    def page_of(self, host: str) -> str | None:
        """The page name of a content host, or None."""
        host, end = host.lower().rstrip("."), "." + self.content_suffix
        name = host[: -len(end)] if host.endswith(end) else ""
        return name if NAME_RE.match(name) and name not in RESERVED_NAMES else None

    def sign_in(self, url: str) -> str:
        return f"{self.origin}/oauth2/start?rd={quote(url, safe='')}"


class BadToken(Exception):
    """A Bearer token that is not ours, or expired."""


class Gateway:
    """Everything the request handler shares."""

    def __init__(
        self,
        store: Store,
        site: Site,
        identity: Identity,
        ttl: float = 5.0,
        cache_bytes: int = 256 * MB,
        file_bytes: int = 4 * MB,
        token_secret: str = "",
    ):
        self.store, self.site, self.identity = store, site, identity
        self.files = FileCache(store, cache_bytes, file_bytes)
        self.pages = PageCache(store, self.files, ttl)
        self.assets = Assets(site.origin)
        self.limit = RateLimit()
        self.token_secret = check_secret(token_secret) if token_secret else ""
        self.codes = LoginCodes()
        self.uploads = Uploads(store)
        self.comments = Comments(store, ttl)
        self.milestones = Milestones(store, ttl)
        self.views = Views(store, ttl)
        self.labels = LabelCache(store, ttl)
        self.uploads.find = self.milestones.find
        self._upload_origin: str | None = None

    @property
    def upload_origin(self) -> str:
        """Where browsers PUT files (the bucket's presigned URLs), for the overview's policy."""
        if self._upload_origin is None:
            url = urlsplit(self.store.presign_put(self.store.key("_probe"), 60)[0])
            self._upload_origin = f"{url.scheme}://{url.netloc}" if url.netloc else ""
        return self._upload_origin

    def team(self, email: str) -> bool:
        """Signed in with the team's domain: may see and change milestones and views."""
        return bool(email) and allowed("ontic", "", email, self.site.email_domain)

    def overview(self, email: str) -> dict:
        """What the overview draws: every report this viewer may open, newest first, and for
        the team the milestones and saved views."""
        reports = [report(*row) for row in self.visible(email)]
        reports.sort(key=lambda r: r["published_at"], reverse=True)
        team = self.team(email)
        return {
            "viewer": email,
            "team": team,
            "pillars": [{"id": p, "label": PILLAR_LABELS[p]} for p in PILLARS],
            "reports": reports,
            "milestones": self.milestones.items() if team else [],
            "views": self.views.items() if team else [],
            "deleted": [report(*row) for row in self.visible(email, deleted=True)],
            "content_origin": self.site.content_origin("__name__"),
        }

    def lookup(self, name: str) -> tuple[str | None, str, str]:
        """(current, visibility, owner) of a page to serve: a deleted one has no current."""
        version, visibility, owner = self.pages.get(name)
        if version and self.labels.get(name).get("deleted"):
            return None, visibility, owner
        return version, visibility, owner

    def meta(self, name: str, version: str) -> dict:
        """The current version's page.json with the labels changed since (labels.json)."""
        return effective(self.files.meta(name, version), self.labels.get(name))

    def visible(self, email: str, deleted: bool = False):
        """(name, current, visibility, owner, page.json with its labels) of each page this viewer
        may open; with deleted, only the deleted ones this viewer owns."""
        for name in self.store.names():
            version, visibility, owner = self.pages.get(name)
            if not version or not allowed(visibility, owner, email, self.site.email_domain):
                continue
            if bool(self.labels.get(name).get("deleted")) != deleted:
                continue
            if deleted and owner.strip().lower() != email.strip().lower():
                continue
            yield name, version, visibility, owner, self.meta(name, version)

    def facts(self, name: str, email: str) -> dict:
        """What the bar shows. Emails only for a signed-in viewer."""
        current, visibility, owner = self.pages.get(name)
        versions = []
        for version in reversed(self.pages.versions(name)):
            meta = self.files.meta(name, version)
            if version == current:
                meta = self.meta(name, version)
            item = {
                "version": version,
                "published_at": meta.get("published_at", ""),
                "description": meta.get("description", ""),
                "pillar": pillar_of(meta),
                "tags": tags_of(meta),
                "title": meta.get("title") or "",
                "milestone": meta.get("milestone") or "",
                "format": meta.get("format") or "html",
            }
            if email:
                item["published_by"] = meta.get("published_by", "")
            versions.append(item)
        return {
            "name": name,
            "visibility": visibility,
            "current": current,
            "published_by": owner if email else "",
            "role": "owner" if email and email.lower() == owner.strip().lower() else "viewer",
            "viewer": email,
            "email_domain": self.site.email_domain,
            "content_origin": self.site.content_origin(name),
            "versions": versions,
            "milestones": self.milestone_titles(versions) if self.team(email) else {},
        }

    def milestone_titles(self, versions: list[dict]) -> dict:
        """id -> {title, status} of the milestones these versions point at, for the bar."""
        ids = {v["milestone"] for v in versions if v.get("milestone")}
        return {
            m["id"]: {"title": m.get("title", ""), "status": m.get("status", "open")}
            for m in self.milestones.items()
            if m.get("id") in ids
        }


def report(name: str, version: str, visibility: str, owner: str, meta: dict) -> dict:
    """One row of the overview's reports table."""
    milestone = meta.get("milestone")
    return {
        "name": name,
        "version": version,
        "title": meta.get("title") or "",
        "description": meta.get("description") or "",
        "pillar": pillar_of(meta),
        "tags": tags_of(meta),
        "milestone": milestone if isinstance(milestone, str) else "",
        "author": owner,
        "published_at": meta.get("published_at") or "",
        "visibility": visibility,
        "format": meta.get("format") or "html",
    }


def listing_link(pillar: str, tag: str) -> str:
    query = "&".join(f"{k}={quote(v)}" for k, v in (("pillar", pillar), ("tag", tag)) if v)
    return "/" + (f"?{query}" if query else "")


def listing_html(gw: Gateway, email: str, query: str = "") -> str:
    """Every page the viewer may open, with the pillar filters on top (?pillar=<id>&tag=<tag>):
    the overview's fallback without script (styled by static/overview.css)."""
    e = html.escape
    asked = parse_qs(query)
    pillar = asked.get("pillar", [""])[0]
    pillar = pillar if pillar in PILLARS else ""
    tag = asked.get("tag", [""])[0].strip().lower()
    counts = {p: 0 for p in PILLARS}
    rows = []
    for name, version, visibility, owner, meta in gw.visible(email):
        if tag and tag not in tags_of(meta):
            continue
        counts[pillar_of(meta)] += 1
        if pillar and pillar_of(meta) != pillar:
            continue
        tags = " ".join(
            f'<a class="tag" href="{e(listing_link(pillar, t))}">{e(t)}</a>' for t in tags_of(meta)
        )
        cells = [
            f'<a href="/{quote(name)}/">{short(name, NAME_CHARS)}</a>',
            f'<span class="badge {visibility}">{visibility}</span>',
            f'<a class="pillar" href="{e(listing_link(pillar_of(meta), tag))}">'
            f"{e(PILLAR_LABELS[pillar_of(meta)])}</a>",
            short(meta.get("description", ""), DESCRIPTION_CHARS) + (f" {tags}" if tags else ""),
            e(stamp(meta.get("published_at")) or version_stamp(version)),
            e(owner),
            f'<a href="/{quote(name)}/_info">info</a>',
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    empty = "No pages here." if pillar or tag else "No pages yet."
    body = "\n".join(rows) or f'<tr><td colspan="7">{empty}</td></tr>'
    choices = [("", "All", sum(counts.values()))]
    choices += [(p, PILLAR_LABELS[p], counts[p]) for p in PILLARS]
    nav = " ".join(
        f'<a href="{e(listing_link(p, tag))}"{' aria-current="page"' if p == pillar else ""}>'
        f'{e(label)} <span class="n">{n}</span></a>'
        for p, label, n in choices
    )
    if tag:
        nav += (
            f' <span class="filter">tag <b>{e(tag)}</b> '
            f'<a href="{e(listing_link(pillar, ""))}">clear</a></span>'
        )
    return f"""<div class="plain"><h1>Ontic pages</h1>
<nav aria-label="Pillars">{nav}</nav>
<table><tr><th>Page</th><th>Visibility</th><th>Pillar</th><th>Description</th><th>Published</th><th>By</th><th></th></tr>
{body}
</table></div>"""


def bad_path(rest: str) -> bool:
    return any(p in (".", "..") or "\\" in p for p in rest.split("/"))


def etag_matches(header: str | None, etag: str) -> bool:
    tags = [t.strip().removeprefix("W/") for t in (header or "").split(",")]
    return "*" in tags or etag in tags


def make_handler(gw: Gateway):
    site = gw.site

    class Handler(BaseHTTPRequestHandler):
        server_version = "ontic-pages"
        protocol_version = "HTTP/1.1"
        timeout = 120

        # --- who is asking ------------------------------------------------------------------

        def begin(self):
            self.who: str | None = None  # None: not asked yet
            self.cookies: list[str] = []
            self.on_apex = False
            scheme, _, value = (self.headers.get("Authorization") or "").partition(" ")
            self.token = value.strip() if scheme.lower() == "bearer" else None

        def token_refused(self) -> bool:
            """Answer and return True for a token that must not be used here: from a browser
            (browsers never hold tokens), or with no token secret configured."""
            if self.token is None:
                return False
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                self.send_json(403, {"error": "tokens are for the command line, not browsers"})
            elif not gw.token_secret:
                self.send_json(503, {"error": "command line sign-in is not set up here"})
            else:
                return False
            return True

        def email(self, required: bool = True) -> str:
            """The signed-in email or "". If oauth2-proxy does not answer, raise, unless the
            answer only adds to a page anyone may open (required=False). A token decides alone:
            a bad one raises BadToken."""
            if self.who is None and self.token is not None:
                self.who = verify(gw.token_secret, self.token) or ""
                if not self.who:
                    raise BadToken()
            if self.who is None:
                try:
                    self.who, self.cookies = gw.identity(self.headers.get("Cookie") or "")
                except IdentityUnavailable:
                    if required:
                        raise
                    return ""
            return self.who

        def may_open(self, name: str, visibility: str, owner: str, url: str, page=True) -> bool:
            """Answer and return False unless this viewer may open the page. Signed out, a page
            load goes to sign-in and anything else gets 401."""
            if visibility == "public":
                return True
            email = self.email()
            if allowed(visibility, owner, email, site.email_domain):
                return True
            if email:
                self.forbidden(name, visibility)
            elif page:
                self.redirect(site.sign_in(url), 302)
            else:
                self.send_text(401, f"sign in at {site.origin}/ first\n")
            return False

        def log_message(self, fmt, *args):
            who = getattr(self, "who", None) or "-"
            line = SECRET_QUERY.sub(r"\1=<hidden>", fmt % args)  # login codes stay out of logs
            sys.stderr.write(f"{self.log_date_time_string()} {who} {line}\n")

        # --- answers ------------------------------------------------------------------------

        def end_headers(self):
            for cookie in getattr(self, "cookies", []):
                self.send_header("Set-Cookie", cookie)
            self.cookies = []
            super().end_headers()

        def send_body(self, code: int, body: bytes, headers: dict[str, str]):
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_text(self, code: int, text: str, ctype="text/plain; charset=utf-8"):
            csp = PLAIN_CSP if self.on_apex else f"frame-ancestors 'self' {site.origin}"
            self.send_body(code, text.encode(), {
                "Content-Type": ctype,
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": csp,
            })  # fmt: skip

        def send_html(self, code: int, text: str):
            self.send_text(code, text, "text/html; charset=utf-8")

        def send_json(self, code: int, value: dict):
            self.send_body(code, json.dumps(value).encode(), {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            })  # fmt: skip

        def redirect(self, location: str, code: int = 301):
            self.send_body(code, b"", {"Location": location})

        def forbidden(self, name: str, visibility: str):
            who = "its publisher" if visibility == "private" else f"@{site.email_domain} accounts"
            self.send_html(
                403,
                f"<!doctype html><title>Not allowed</title><p>{html.escape(name)} is a "
                f"{visibility} page, open to {html.escape(who)} only.</p>\n",
            )

        def send_asset(self, data: bytes, ctype: str, immutable: bool):
            self.send_body(200, data, {
                "Content-Type": ctype,
                "Cache-Control": f"public, {YEAR}" if immutable else "no-cache",
                "X-Content-Type-Options": "nosniff",
            })  # fmt: skip

        # --- routing ------------------------------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self.begin()
            url = urlsplit(self.path)
            path, query = unquote(url.path), url.query
            host = (self.headers.get("Host") or "").lower()
            try:
                if path == "/_health":
                    return self.send_text(200, "ok\n")
                if self.token_refused():
                    return
                if host == site.apex:
                    self.on_apex = True
                    return self.apex_get(path, query)
                name = site.page_of(host)
                if name:
                    return self.content_get(name, path, query)
                if path == "/_tls-ask":
                    return self.tls_ask(query)
                return self.send_text(404, f"unknown host; the pages are at {site.origin}/\n")
            except IdentityUnavailable as err:
                self.log_error("oauth2-proxy did not answer: %s", err)
                return self.send_text(503, "sign-in is not answering, try again\n")
            except BadToken:
                return self.send_json(401, {"error": EXPIRED})
            except ClientError as err:
                self.log_error("store error: %s", err)
                return self.send_text(502, "store error\n")

        def tls_ask(self, query: str):
            """Caddy asks before getting a certificate for a host: only existing pages."""
            domain = parse_qs(query).get("domain", [""])[0]
            name = site.page_of(domain)
            exists = bool(name and gw.lookup(name)[0])
            return self.send_text(200 if exists else 404, "ok\n" if exists else "no such page\n")

        def apex_get(self, path: str, query: str):
            q = f"?{query}" if query else ""
            if path == "/":
                email = self.email()
                if not email:
                    return self.redirect(site.sign_in(f"{site.origin}/"), 302)
                page = overview_html(email, gw.assets, listing_html(gw, email, query))
                return self.send_body(200, page.encode(), {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-cache",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": overview_csp(
                        gw.upload_origin, f"{site.scheme}://*.{site.content_suffix}"
                    ),
                    "Referrer-Policy": "no-referrer",
                })  # fmt: skip
            if found := gw.assets.lookup(path, content_host=False):
                return self.send_asset(*found)
            if m := API_PAGE.match(path):
                return self.api_get(m.group(1))
            if m := API_INFO.match(path):
                return self.api_info(m.group(1))
            if m := API_COMMENTS.match(path):
                return self.api_comments(m.group(1))
            if m := API_FILES.match(path):
                return self.api_files(m.group(1), query)
            if m := API_VERSION.match(path):
                return self.api_version(m.group(1), m.group(2))
            if path == "/_api/pages":
                return self.api_list()
            if path in ("/_api/overview", "/_api/milestones"):
                return self.api_research(path)
            if m := API_SOURCE.match(path):
                return self.api_source(m.group(1))
            if path == "/_api/me":
                if email := self.email():
                    return self.send_json(200, {"email": email})
                return self.send_json(401, {"error": EXPIRED})
            if path == "/_api/cli/token":
                return self.cli_token(query)
            if path == "/_cli/login":
                return self.cli_login(query)
            if path.startswith("/public/"):
                return self.redirect(quote(path[len("/public") :]) + q)
            name, slash, rest = path[1:].partition("/")
            if not NAME_RE.match(name) or name in RESERVED_NAMES or bad_path(rest):
                return self.send_text(404, "not found\n")
            if not slash:
                return self.redirect(f"/{quote(name)}/{q}")
            version, visibility, owner = gw.lookup(name)
            if not version:
                return self.send_text(404, f"no page named {name}\n")
            here = f"{site.origin}{quote(path)}{q}"
            if not self.may_open(name, visibility, owner, here):
                return
            if rest == "_info":
                if not self.email():  # metadata stays signed in, even for public pages
                    return self.redirect(site.sign_in(here), 302)
                page = info_html(name, *history(gw.store, name), visibility)
                return self.send_html(200, page)
            viewing = None
            if m := VERSIONED.match(rest):
                viewing = m.group(1)
                if viewing not in gw.pages.versions(name):
                    return self.send_text(404, f"{name} has no version {viewing}\n")
                if m.group(2) is None:
                    return self.redirect(f"/{quote(name)}/_v/{viewing}/{q}")
            self.send_shell(name, viewing, "/" + rest, query, here)

        def send_shell(self, name: str, viewing: str | None, path: str, query: str, here: str):
            page = gw.facts(name, self.email(required=False))
            src = site.content_origin(name) + quote(path) + (f"?{query}" if query else "")
            view = {
                "version": viewing,
                "path": path + (f"?{query}" if query else ""),
                "src": src,
                "raw": src + ("&" if query else "?") + "raw=1",
                "signin": site.sign_in(here),
            }
            self.send_body(200, shell_html(page, view, gw.assets).encode(), {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "Content-Security-Policy": shell_csp(f"{site.scheme}://*.{site.content_suffix}"),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            })  # fmt: skip

        # --- the JSON API (apex only) -------------------------------------------------------

        def api_page(self, name: str, required: bool):
            """(current, visibility, owner, email) after the access check, else None (answered)."""
            if not NAME_RE.match(name) or name in RESERVED_NAMES:
                return self.send_json(404, {"error": "no such page"})
            version, visibility, owner = gw.lookup(name)
            if not version:
                return self.send_json(404, {"error": f"no page named {name}"})
            email = self.email(required=required or visibility != "public")
            if not allowed(visibility, owner, email, site.email_domain):
                code, error = (403, "not allowed") if email else (401, "sign in first")
                return self.send_json(code, {"error": error})
            return version, visibility, owner, email

        def api_get(self, name: str):
            if found := self.api_page(name, required=False):
                self.send_json(200, gw.facts(name, found[3]))

        def api_info(self, name: str):
            """Every version's page.json, newest first (like /<name>/_info, signed in only)."""
            if found := self.api_page(name, required=True):
                if not found[3]:
                    return self.send_json(401, {"error": EXPIRED})
                current, metas = history(gw.store, name)
                self.send_json(200, {
                    "name": name, "current": current, "visibility": found[1], "versions": metas,
                })  # fmt: skip

        def api_version(self, name: str, version: str):
            """One version's page.json fields for the bar's Details panel. Signed in only, like
            /<name>/_info (the emails in it), and never for a page's own scripts."""
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return self.send_json(403, {"error": "details are read by the bar"})
            found = self.api_page(name, required=True)
            if not found:
                return
            if not found[3]:
                return self.send_json(401, {"error": "sign in to see the details"})
            if version not in gw.pages.versions(name):
                return self.send_json(404, {"error": f"{name} has no version {version}"})
            meta = gw.files.meta(name, version)
            self.send_json(200, details(name, version, found[0], found[1], meta))

        def api_files(self, name: str, query: str):
            """A version's files (the current one by default) with presigned GET URLs, for
            `ontic-pages pull`: the bytes come from the bucket, not through here. Signed in."""
            if found := self.api_page(name, required=True):
                if not found[3]:
                    return self.send_json(401, {"error": EXPIRED})
                version = parse_qs(query).get("version", [found[0]])[0]
                if not VERSION_RE.match(version) or version not in gw.store.versions(name):
                    return self.send_json(404, {"error": f"{name} has no version {version}"})
                self.send_json(200, {
                    "name": name, "version": version, "page": gw.store.meta(name, version),
                    "files": presigned_files(gw.store, name, version),
                })  # fmt: skip

        def api_list(self):
            email = self.email()
            if not email:
                return self.send_json(401, {"error": EXPIRED})
            pages = [
                {
                    "name": name,
                    "visibility": visibility,
                    "current": version,
                    "published_by": owner,
                    "published_at": meta.get("published_at", ""),
                    "description": meta.get("description", ""),
                    "pillar": pillar_of(meta),
                    "tags": tags_of(meta),
                    "title": meta.get("title") or "",
                    "milestone": meta.get("milestone") or "",
                }
                for name, version, visibility, owner, meta in gw.visible(email)
            ]
            self.send_json(200, {"pages": pages})

        def api_research(self, path: str):
            """The overview's data, or the milestones alone (the command line). Signed in; a
            page's own scripts get nothing."""
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return self.send_json(403, {"error": "read by the overview"})
            email = self.email()
            if not email:
                return self.send_json(401, {"error": EXPIRED})
            if path == "/_api/overview":
                return self.send_json(200, gw.overview(email))
            if not gw.team(email):
                return self.send_json(403, {"error": f"only @{site.email_domain} accounts"})
            self.send_json(200, {"milestones": gw.milestones.items()})

        def api_source(self, name: str):
            """A Markdown update's source and labels, for Edit in the overview (its owner)."""
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return self.send_json(403, {"error": "read by the overview"})
            found = self.api_page(name, required=True)
            if not found:
                return
            current, email = found[0], found[3]
            if not email:
                return self.send_json(401, {"error": EXPIRED})
            meta = gw.files.meta(name, current)
            if meta.get("format") != "markdown":
                return self.send_json(404, {"error": f"{name} is not a Markdown update"})
            raw = gw.files.read(name, current, MARKDOWN_FILE)
            self.send_json(200, {
                "name": name, "version": current, "markdown": (raw or b"").decode("utf-8"),
                **{k: v for k, v in report(name, current, found[1], found[2], meta).items()
                   if k in ("title", "description", "pillar", "tags", "milestone")},
            })  # fmt: skip

        def api_comments(self, name: str):
            """Every thread of the page, newest activity first, for a signed-in viewer who may
            open it. Signed out (a public page) there are none; a page's own scripts get none."""
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return self.send_json(403, {"error": "comments are read by the bar"})
            found = self.api_page(name, required=True)
            if not found:
                return
            current, email = found[0], found[3]
            if not email:
                return self.send_json(401, {"error": "sign in to see comments"})
            try:
                threads = listing(gw.comments.threads(name), email)
            except Refused as err:
                return self.send_json(err.status, {"error": err.message})
            self.send_json(200, {
                "name": name, "current": current,
                "open": sum(1 for t in threads if not t["resolved"]), "threads": threads,
            })  # fmt: skip

        # --- the command line's sign-in (tokens.py) ----------------------------------------

        def cli_login(self, query: str):
            """The page `ontic-pages login` opens: who you are, the end of the code, Allow."""
            if not gw.token_secret:
                return self.send_text(503, "command line sign-in is not set up here\n")
            email = self.email()
            if not email:
                return self.redirect(site.sign_in(f"{site.origin}/_cli/login?{query}"), 302)
            code = parse_qs(query).get("code", [""])[0]
            problem = ""
            if not allowed("ontic", "", email, site.email_domain):
                problem = f"Only @{site.email_domain} accounts can sign in the command line."
            elif not gw.codes.open(code):
                problem = "This sign-in link is used up or too old. Run ontic pages login again."
            page = login_html(email, code, problem, gw.assets)
            self.send_body(400 if problem else 200, page.encode(), {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "Content-Security-Policy": LOGIN_CSP,
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            })  # fmt: skip

        def cli_token(self, query: str):
            """The command line polls with its code: 202 until someone allowed it, then the
            token, once."""
            if not gw.token_secret:
                return self.send_json(503, {"error": "command line sign-in is not set up here"})
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                return self.send_json(403, {"error": "for the command line, not browsers"})
            email = gw.codes.claim(parse_qs(query).get("code", [""])[0])
            if not email:
                return self.send_json(202, {"status": "waiting for Allow in the browser"})
            self.send_json(200, {"token": sign(gw.token_secret, email), "email": email})

        # --- writes (apex only) -------------------------------------------------------------

        def do_POST(self):
            self.begin()
            path = unquote(urlsplit(self.path).path)
            host = (self.headers.get("Host") or "").lower()
            self.on_apex = host == site.apex
            m = API_WRITE.match(path)
            c = COMMENT_WRITE.match(path)
            r = RESEARCH_WRITE.match(path)
            big = bool(m and m.group(2).startswith("versions") and m.group(4) != "commit")
            big = big or bool(m and m.group(2) == "edits") or path == PREVIEW
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            limit = COMMENT_BODY if c or r else LIST_BODY if big else SMALL_BODY
            if not 0 <= length <= limit:
                self.close_connection = True
                return self.send_json(413, {"error": "body too large"})
            body = self.rfile.read(length)
            if not self.on_apex or not (m or c or r or path in (APPROVE, PREVIEW)):
                return self.send_json(404 if self.on_apex else 405, {"error": "no writes here"})
            if self.token_refused():
                return
            if self.token is None:
                # A page's own scripts run on <name>.<suffix>, same-site with the apex, so the
                # browser sends the viewer's cookie with their requests too. Only the apex's
                # own pages pass these two checks.
                same = self.headers.get("Sec-Fetch-Site") == "same-origin"
                if not same or self.headers.get("Origin") != site.origin:
                    return self.send_json(403, {"error": "writes only from the bar"})
            elif path == APPROVE:  # a token never makes another token
                return self.send_json(403, {"error": "approve in the browser"})
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self.send_json(415, {"error": "send application/json"})
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                data = None
            try:
                if c:
                    return self.comment_write(c, data)
                if r:
                    return self.research_write(r, data)
                if path == PREVIEW:
                    return self.preview(data)
                if path == APPROVE:
                    return self.approve(data)
                name = m.group(1)
                if m.group(2) == "versions":
                    return self.publish_start(name, data)
                if m.group(3):
                    return self.publish_more(name, m.group(3), m.group(4), data)
                if m.group(2) == "edits":
                    return self.save_edits(name, data)
                if m.group(2) == "labels":
                    return self.relabel(name, data)
                if m.group(2) == "delete":
                    return self.delete_page(name, data)
                found = self.api_page(name, required=True)
                if not found:
                    return
                _, _, owner, email = found
                if not email:
                    return self.send_json(401, {"error": EXPIRED})
                if not gw.limit.allow(email.lower()):
                    return self.send_json(429, {"error": "too many changes, wait a minute"})
                if not isinstance(data, dict):
                    data = {}
                if m.group(2) == "visibility":
                    level = data.get("visibility")
                    if level not in VISIBILITIES:
                        error = "visibility: private, ontic or public"
                        return self.send_json(400, {"error": error})
                else:
                    version = data.get("version")
                    if (
                        not isinstance(version, str)
                        or not VERSION_RE.match(version)
                        or (version not in gw.store.versions(name))
                    ):
                        return self.send_json(400, {"error": f"{name} has no such version"})
                if email.strip().lower() != owner.strip().lower():
                    return self.send_json(403, {"error": "only the owner can change this"})
                if m.group(2) == "visibility":
                    gw.store.set_visibility(name, level)
                else:
                    gw.store.set_current(name, version)
                gw.pages.forget(name)
                self.send_json(200, gw.facts(name, email))
            except IdentityUnavailable:
                self.send_json(503, {"error": "sign-in is not answering, try again"})
            except BadToken:
                self.send_json(401, {"error": EXPIRED})
            except Refused as err:
                self.send_json(err.status, {"error": err.message})
            except ClientError as err:
                self.log_error("store error: %s", err)
                self.send_json(502, {"error": "store error"})

        def approve(self, data):
            """Allow on the login page: the code's token is for the signed-in email."""
            if not gw.token_secret:
                return self.send_json(503, {"error": "command line sign-in is not set up here"})
            email = self.email()
            if not allowed("ontic", "", email, site.email_domain):
                return self.send_json(403 if email else 401, {"error": "sign in first"})
            if not gw.limit.allow(email.lower()):
                return self.send_json(429, {"error": "too many changes, wait a minute"})
            code = data.get("code") if isinstance(data, dict) else None
            if not isinstance(code, str) or not gw.codes.approve(code, email):
                return self.send_json(400, {"error": "this sign-in link is used up or too old"})
            self.send_json(200, {"email": email})

        def comment_write(self, c, data):
            """A new thread, a reply, resolve or reopen, or deleting one's own comment, by
            anyone signed in who may open the page (comments.py checks the rest)."""
            name, thread, action, comment = c.groups()
            found = self.api_page(name, required=True)
            if not found:
                return
            current, email = found[0], found[3]
            if not email:
                return self.send_json(401, {"error": EXPIRED})
            if not gw.limit.allow(email.lower()):
                return self.send_json(429, {"error": "too many changes, wait a minute"})
            data = data if isinstance(data, dict) else {}
            status, what = 200, action or ("delete" if comment else "new")
            if thread is None:
                version = data.get("version") or current
                if not isinstance(version, str) or version not in gw.pages.versions(name):
                    raise Refused(400, f"{name} has no such version")
                anchor, text = data.get("anchor"), data.get("body")
                done, status = gw.comments.create(name, version, anchor, email, text), 201
            elif action == "reply":
                done = gw.comments.reply(name, thread, email, data.get("body"))
            elif action == "resolve":
                if not isinstance(data.get("resolved"), bool):
                    raise Refused(400, "resolved: true or false")
                done = gw.comments.resolve(name, thread, data["resolved"], email)
            else:
                done = gw.comments.delete(name, thread, comment, email)
            self.log_message("comment %s %s %s", what, name, done["id"])
            self.send_json(status, {"thread": thread_view(done, email)})

        def research_write(self, r, data):
            """A milestone (new or changed) or a saved view (new, changed, deleted), by anyone
            signed in with the team's domain (milestones.py checks the fields)."""
            milestone, view, delete = r.groups()
            email = self.email()
            if not email:
                return self.send_json(401, {"error": EXPIRED})
            if not gw.team(email):
                return self.send_json(403, {"error": f"only @{site.email_domain} accounts"})
            if not gw.limit.allow(email.lower()):
                return self.send_json(429, {"error": "too many changes, wait a minute"})
            if self.path.split("?")[0].startswith("/_api/milestones"):
                if milestone is None:
                    done, status = gw.milestones.create(data, email), 201
                else:
                    if not MILESTONE_ID_RE.match(milestone):
                        return self.send_json(404, {"error": "no such milestone"})
                    done, status = gw.milestones.update(milestone, data, email), 200
                self.log_message("milestone %s", done["id"])
                return self.send_json(status, {"milestone": done})
            if view is None:
                done, status = gw.views.create(data, email), 201
            elif not VIEW_ID_RE.match(view):
                return self.send_json(404, {"error": "no such view"})
            elif delete:
                done, status = gw.views.delete(view, email), 200
            else:
                done, status = gw.views.update(view, data, email), 200
            self.send_json(status, {"view": done})

        def preview(self, data):
            """The composer's preview: the page a Markdown update becomes (nothing is saved)."""
            from .markdown import page_html, render

            if not self.email():
                return self.send_json(401, {"error": EXPIRED})
            data = data if isinstance(data, dict) else {}
            text, title = data.get("markdown"), data.get("title") or ""
            if not isinstance(text, str) or not isinstance(title, str):
                raise Refused(400, "send {markdown, title}")
            self.send_json(200, {"html": page_html(title, render(text, title), "Preview")})

        def relabel(self, name: str, data):
            """Anyone on the team who may open the page changes its pillar, tags, milestone or
            title, as in a Notion table: labels.json, no new version."""
            from .uploads import page_labels

            found = self.api_page(name, required=True)
            if not found:
                return
            current, visibility, owner, email = found
            if not email:
                raise Refused(401, EXPIRED)
            if not gw.team(email):
                raise Refused(403, f"only @{site.email_domain} accounts change labels")
            if not gw.limit.allow(email.lower()):
                raise Refused(429, "too many changes, wait a minute")
            data = data if isinstance(data, dict) else {}
            pillar, tags, milestone = page_labels(data)
            if milestone and gw.milestones.find(milestone) is None:
                raise Refused(400, f"no milestone {milestone!r}")
            title = data.get("title")
            if title is not None and (not isinstance(title, str) or not title.strip()):
                raise Refused(400, "title: text")
            given = {"pillar": pillar, "tags": tags, "milestone": milestone,
                     "title": None if title is None else clean_title(title)}  # fmt: skip
            labels = {**gw.labels.get(name)}
            meta = gw.meta(name, current)
            for key in LABEL_KEYS:
                labels.setdefault(key, meta.get(key))
            labels.update({k: v for k, v in given.items() if v is not None})
            labels.update(updated_by=email, updated_at=now())
            gw.store.set_labels(name, labels)
            gw.labels.put(name, labels)
            self.log_message("labels %s", name)
            self.send_json(200, {"report": report(name, current, visibility, owner,
                                                  gw.meta(name, current))})  # fmt: skip

        def delete_page(self, name: str, data):
            """The owner deletes a page, or brings it back ({"deleted": false}): it is hidden
            everywhere and its addresses answer 404; nothing is removed from the bucket."""
            if not NAME_RE.match(name):
                raise Refused(404, "no such page")
            current, visibility, owner = gw.pages.get(name)
            if not current:
                raise Refused(404, f"no page named {name}")
            email = self.email()
            if not email:
                raise Refused(401, EXPIRED)
            if email.strip().lower() != owner.strip().lower():
                raise Refused(403, f"only {owner or 'its owner'} can delete {name}")
            if not gw.limit.allow(email.lower()):
                raise Refused(429, "too many changes, wait a minute")
            deleted = (data or {}).get("deleted", True) if isinstance(data, dict) else True
            if not isinstance(deleted, bool):
                raise Refused(400, "deleted: true or false")
            labels = {**gw.labels.get(name), "deleted": deleted, "deleted_by": email,
                      "deleted_at": now()}  # fmt: skip
            if not deleted:
                for key in ("deleted", "deleted_by", "deleted_at"):
                    labels.pop(key, None)
            gw.store.set_labels(name, labels)
            gw.labels.put(name, labels)
            self.log_message("%s %s", "delete" if deleted else "restore", name)
            self.send_json(200, {"name": name, "deleted": deleted})

        def may_publish(self, name: str) -> str:
            """The publisher's email: a team member for a new page, the owner for a new version
            of an existing one. Raises Refused otherwise."""
            email = self.email()
            if not email:
                raise Refused(401, EXPIRED)
            gw.pages.forget(name)
            current, _, owner = gw.pages.get(name)
            if current and email.strip().lower() != owner.strip().lower():
                raise Refused(
                    403,
                    f"{name} belongs to {owner or 'someone else'}; only they can "
                    "publish a new version of it (pick another name)",
                )
            if not current and not allowed("ontic", "", email, site.email_domain):
                raise Refused(403, f"only @{site.email_domain} accounts can create pages")
            return email

        def publish_start(self, name: str, data):
            try:
                check_name(name)
            except ValueError as err:
                raise Refused(400, str(err)) from None
            if not isinstance(data, dict):
                raise Refused(400, "send {files, description, meta, git, visibility}")
            email = self.may_publish(name)
            if not gw.limit.allow(email.lower()):
                raise Refused(429, "too many changes, wait a minute")
            up, uploads = gw.uploads.start(name, email, data)
            self.log_message("publish start %s %s (%d files)", name, up.version, len(up.files))
            self.send_json(200, {"version": up.version, "uploads": uploads})

        def publish_more(self, name: str, version: str, step: str, data):
            email = self.may_publish(name)
            if step == "files":
                files = data.get("files") if isinstance(data, dict) else None
                uploads = gw.uploads.add(name, version, email, files)
                return self.send_json(200, {"version": version, "uploads": uploads})
            if not gw.limit.allow(email.lower()):
                raise Refused(429, "too many changes, wait a minute")
            page, _ = gw.uploads.commit(name, version, email)
            gw.pages.forget(name)
            self.log_message("publish commit %s %s", name, version)
            self.send_json(200, {
                "page": page, "visibility": gw.store.visibility(name),
                "url": f"{site.origin}/{quote(name)}/",
            })  # fmt: skip

        def save_edits(self, name: str, data):
            """Text edited in the bar, as a new version (edits.py checks owner and version). The
            answer is built from what the save put in the caches; one log line times it."""
            phases = Phases()
            found = self.api_page(name, required=True)
            if not found:
                return
            email = found[3]
            if not email:
                raise Refused(401, EXPIRED)
            if email.strip().lower() != found[2].strip().lower():
                raise Refused(403, "only the owner can edit this page")
            if not gw.limit.allow(email.lower()):
                raise Refused(429, "too many changes, wait a minute")
            phases.lap("lookup")
            page = save_edits(gw.store, name, email, data, gw.uploads, gw.files, gw.pages, phases)
            facts = gw.facts(name, email)
            phases.lap("answer")
            edited = page["meta"]["edited_from"]
            self.log_message("edit %s %s from %s: %s", name, page["version"], edited, phases.line())
            self.send_json(200, {"version": page["version"], "page": facts})

        # --- the content host ---------------------------------------------------------------

        def content_get(self, name: str, path: str, query: str):
            q = f"?{query}" if query else ""
            if found := gw.assets.lookup(path, content_host=True):
                return self.send_asset(*found)
            rest = path[1:]
            if bad_path(rest):
                return self.send_text(404, "not found\n")
            current, visibility, owner = gw.lookup(name)
            if not current:
                return self.send_text(404, f"no page named {name}\n")
            version, base = current, "/"
            if m := VERSIONED.match(rest):
                version, base = m.group(1), f"/_v/{m.group(1)}/"
                if m.group(2) is None:
                    return self.redirect(f"{base}{q}")
                rest = m.group(2)[1:]
            dest = self.headers.get("Sec-Fetch-Dest", "")
            raw = parse_qs(query).get("raw") == ["1"]
            itself = self.headers.get("Sec-Fetch-Site") == "same-origin"
            if dest == "document" and not raw and not itself:
                return self.redirect(f"{site.origin}/{quote(name)}{quote(path)}{q}", 302)
            here = f"{site.content_origin(name)}{quote(path)}{q}"
            if not self.may_open(name, visibility, owner, here, page=dest in ("document", "")):
                return
            self.send_content(name, version, rest, base, q, visibility, dest == "iframe")

        def send_content(self, name, version, rest, base, q, visibility, frame):
            if rest == "" or rest.endswith("/"):
                rest += "index.html"
            public = visibility == "public"
            revalidate = "no-cache" if public else "private, no-cache"
            cache = f"{'public' if public else 'private'}, {YEAR}" if base != "/" else revalidate
            headers = {
                "Content-Type": content_type(rest),
                "Cache-Control": cache,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": f"frame-ancestors 'self' {site.origin}",
            }
            key = gw.store.key(name, version, rest)
            if self.headers.get("Range"):
                try:
                    obj = gw.store.get(key, self.headers.get("Range"))
                except ClientError as err:
                    if err.response.get("Error", {}).get("Code") == "InvalidRange":
                        return self.send_text(416, "range not satisfiable\n")
                    raise
                if obj is None:
                    return self.send_text(404, "not found\n")
                return self.send_stream(obj, headers)
            found = gw.files.get(name, version, rest)
            if found is None:
                if not rest.endswith("index.html") and gw.store.exists(f"{key}/index.html"):
                    return self.redirect(f"{base}{quote(rest)}/{q}")
                return self.send_text(404, "not found\n")
            entry, body = found
            try:
                is_html = headers["Content-Type"].startswith("text/html")
                bridge = is_html and frame
                etag = entry.etag
                # Rewritten HTML: its own ETag, and always revalidated. The tag carries the
                # version, which the file's ETag starts with, so the ETag changes with it.
                if bridge:
                    etag = f'{etag[:-1]}-b{gw.assets.bridge_hash}"'
                    headers["Cache-Control"] = revalidate
                headers["ETag"] = etag
                if is_html:
                    headers["Vary"] = "Sec-Fetch-Dest"
                if etag_matches(self.headers.get("If-None-Match"), etag):
                    self.send_response(304)
                    for k in ("ETag", "Cache-Control", "Vary"):
                        if k in headers:
                            self.send_header(k, headers[k])
                    return self.end_headers()
                data = entry.data
                if bridge and data is None:
                    body = body or gw.store.get(key)["Body"]
                    data = body.read()
                if bridge:
                    tag = gw.assets.bridge_tag(version)
                    return self.send_body(200, inject(data, tag), headers)
                headers["Accept-Ranges"] = "bytes"
                if data is not None:
                    return self.send_body(200, data, headers)
                body = body or gw.store.get(key)["Body"]
                self.send_stream({"Body": body, "ContentLength": entry.size}, headers)
                body = None  # send_stream closed it
            finally:
                if body is not None:
                    body.close()

        def send_stream(self, obj: dict, headers: dict[str, str]):
            self.send_response(206 if obj.get("ContentRange") else 200)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(obj["ContentLength"]))
            if obj.get("ContentRange"):
                self.send_header("Content-Range", obj["ContentRange"])
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            body = obj["Body"]
            try:
                while self.command != "HEAD" and (chunk := body.read(CHUNK)):
                    self.wfile.write(chunk)
            finally:
                body.close()

    return Handler


def make_server(
    store: Store,
    host: str,
    port: int,
    site: Site | None = None,
    identity: Identity | None = None,
    ttl: float = 5.0,
    local_email: str = "",
    auth_url: str = DEFAULT_AUTH_URL,
    cache_bytes: int = 256 * MB,
    file_bytes: int = 4 * MB,
    token_secret: str = "",
) -> ThreadingHTTPServer:
    """identity: who sent a Cookie header; default: --local-as, else oauth2-proxy at auth_url.
    token_secret: signs the command line's tokens; without it their routes answer 503."""
    if identity is None:
        identity = fixed(local_email) if local_email else OAuth2Proxy(auth_url, ttl=ttl)
    gw = Gateway(store, site or Site(), identity, ttl, cache_bytes, file_bytes, token_secret)
    server = ThreadingHTTPServer((host, port), make_handler(gw))
    server.daemon_threads = True
    return server


def serve(store: Store, host: str, port: int, **kwargs) -> None:
    server = make_server(store, host, port, **kwargs)
    site = kwargs.get("site") or Site()
    print(f"serving {store.bucket}/{store.prefix} on http://{host}:{server.server_port}/")
    print(f"open {site.origin}/ (pages at {site.content_origin('<name>')}/)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
