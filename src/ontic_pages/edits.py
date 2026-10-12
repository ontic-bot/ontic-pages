"""Saving text edited in the bar as a new version. Nothing is overwritten.

The bar's edit mode (static/edit.js, static/edit-bridge.js) sends what the page's visible text was
and what it is now, per element: {version, path, changes: [{before, after}]}. Only the owner may
save, and only onto the current version. Each `before` is looked up in the HTML source of the file
at `path`, in its text only (not in tags, scripts, styles or the title), as written there: either
as is or with character references (&amp;, &#233;, &nbsp;). It must be found exactly once (a text
that fills a whole element wins over the same words inside a longer text); otherwise nothing is
saved and the answer names each change that could not be placed. Only the part that changed is
replaced, html-escaped.

The new version is the old one with that one file patched: every other file is copied inside the
bucket (copy_object; the bytes never pass through the gateway) while the gateway writes the
patched file (it is small), then page.json (published_by is the editor, the old description and
meta plus edited_from=<old version>, the old git, pillar and tags), then `current`, as publish.py
does.

It is quick: what the gateway keeps of the old version (page.json, its file list, the HTML it
served) is not read again, and what was written goes into those caches, so the answer and the
next save read nothing more. Only `current` is always asked of the bucket before it is written.
The time of each step goes into one log line per save.
"""

from __future__ import annotations

import html
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.entities import html5
from html.parser import HTMLParser
from urllib.parse import unquote

from .cache import FileCache, PageCache
from .publish import (
    META_FILE,
    check_paths,
    finish,
    kept_labels,
    new_version,
    page_json,
    page_record,
)
from .store import VERSION_RE, Store
from .uploads import Refused, Uploads

MAX_CHANGES = 500
MAX_TEXT = 20_000  # characters of one before or after
MAX_HTML = 5 * 1024 * 1024  # bytes of the HTML file the gateway reads and patches
COPY_THREADS = 8
# Text inside these is not page text the bar lets you edit; matches there do not count.
SKIP = frozenset(
    {"script", "style", "title", "template", "svg", "math", "canvas", "noscript", "textarea"}
)
STALE = "a newer version was published meanwhile: reload the page and edit again"

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def page_lock(name: str) -> threading.Lock:
    """One save per page at a time in this process."""
    with _guard:
        return _locks[name]


# --- where the text is in the source ------------------------------------------------------


class _Events(HTMLParser):
    """Where each piece of the source starts, and whether it is page text."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.lines = [0] + [m.end() for m in re.finditer("\n", source)]
        self.events: list[tuple[int, bool]] = []
        self.depth = 0  # inside how many SKIP elements

    def _at(self, text: bool) -> None:
        line, col = self.getpos()
        self.events.append((self.lines[line - 1] + col, text and not self.depth))

    def handle_starttag(self, tag, attrs):
        self._at(False)
        if tag in SKIP:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self._at(False)

    def handle_endtag(self, tag):
        self._at(False)
        if tag in SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        self._at(True)

    handle_entityref = handle_charref = handle_data

    def handle_comment(self, data):
        self._at(False)

    handle_decl = handle_pi = unknown_decl = handle_comment


def text_spans(source: str) -> list[tuple[int, int]]:
    """(start, end) of each run of page text in the source, character references included."""
    parser = _Events(source)
    parser.feed(source)
    parser.close()
    events = parser.events + [(len(source), False)]
    spans: list[tuple[int, int]] = []
    for (start, text), (end, _) in zip(events, events[1:], strict=False):
        if not text or start >= end:
            continue
        if spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans


_names: dict[str, list[str]] = {}


def _entity_names() -> dict[str, list[str]]:
    """Character -> the entity names that write it ("amp;", "amp", "AMP;", ...), with
    semicolon first."""
    if not _names:
        for name, value in html5.items():
            if len(value) == 1:
                _names.setdefault(value, []).append(name)
        for names in _names.values():
            names.sort(key=lambda n: (not n.endswith(";"), -len(n)))
    return _names


def _char(c: str) -> str:
    """A regex for one character of text as it may be written in HTML source."""
    if c == "\n":
        return r"(?:\r\n|\n|\r)"  # the parser turns CR LF and CR into LF
    if c.isascii() and c.isprintable() and c not in "&<>\"'":
        return re.escape(c)
    alts = [re.escape(c)] + [re.escape("&" + n) for n in _entity_names().get(c, [])]
    alts += [f"&#0*{ord(c)};", f"&#[xX]0*(?i:{ord(c):x});"]
    return "(?:" + "|".join(alts) + ")"


def _pattern(text: str) -> str:
    return "".join(_char(c) for c in text)


def short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def locate(source: str, spans: list[tuple[int, int]], before: str, after: str):
    """Where to put `after`: (start, end, replacement, (match start, match end)) for the part of
    `before` that changed, or a reason it cannot be placed (text)."""
    p = 0
    while p < min(len(before), len(after)) and before[p] == after[p]:
        p += 1
    s = 0
    while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    rx = re.compile(
        f"({_pattern(before[:p])})({_pattern(before[p : len(before) - s])})"
        f"({_pattern(before[len(before) - s :])})"
    )
    found = [m for a, b in spans if (m := rx.fullmatch(source, a, b))]  # fills a whole element
    if not found:
        found = [m for a, b in spans for m in rx.finditer(source, a, b)]
    if not found:
        return f'"{short(before)}" is not in the HTML source (a script may write it)'
    if len(found) > 1:
        return f'"{short(before)}" is in the page {len(found)} times; cannot tell which one'
    m = found[0]
    middle = html.escape(after[p : len(after) - s], quote=False)
    return m.start(2), m.end(2), middle, (m.start(), m.end())


def patch(source: str, changes: list[dict]) -> str:
    """The source with every change made, or Refused naming each change that cannot be placed
    (then none is made)."""
    spans = text_spans(source)
    placed, reasons = [], []
    for change in changes:
        where = locate(source, spans, change["before"], change["after"])
        if isinstance(where, str):
            reasons.append(where)
        else:
            placed.append((where, change))
    placed.sort(key=lambda item: item[0][3])
    for (prev, a), (here, b) in zip(placed, placed[1:], strict=False):
        if here[3][0] < prev[3][1]:
            reasons.append(f'"{short(b["before"])}" overlaps "{short(a["before"])}"')
    if reasons:
        more = f" (and {len(reasons) - 5} more)" if len(reasons) > 5 else ""
        raise Refused(
            422,
            f"Nothing saved: {len(reasons)} of {len(changes)} changes could not be placed. "
            + "; ".join(reasons[:5]) + more,
        )  # fmt: skip
    for (start, end, middle, _), _ in reversed(placed):
        source = source[:start] + middle + source[end:]
    return source


# --- the request and the new version ------------------------------------------------------


def check(body) -> tuple[str, str, list[dict]]:
    """version, the file path in the version, and the changes (only real ones), checked."""
    if not isinstance(body, dict):
        raise Refused(400, "send {version, path, changes}")
    version, path, changes = body.get("version"), body.get("path"), body.get("changes")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise Refused(400, "version: the version you edited")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 2048:
        raise Refused(400, "path: the page's path, like /index.html")
    rel = unquote(path.split("?")[0].split("#")[0])[1:]
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    if rel.startswith("_v/"):
        raise Refused(400, "only the current version can be edited")
    try:
        check_paths([rel])
    except ValueError as err:
        raise Refused(400, str(err)) from None
    if not rel.lower().endswith((".html", ".htm")):
        raise Refused(400, "only HTML files can be edited")
    if not isinstance(changes, list) or not 0 < len(changes) <= MAX_CHANGES:
        raise Refused(400, f"changes: a list of 1 to {MAX_CHANGES} {{before, after}}")
    out = []
    for item in changes:
        item = item if isinstance(item, dict) else {}
        before, after = item.get("before"), item.get("after")
        if not isinstance(before, str) or not isinstance(after, str) or not before:
            raise Refused(400, "changes: each one is {before: text, after: text}")
        if len(before) > MAX_TEXT or len(after) > MAX_TEXT:
            raise Refused(413, f"changes: at most {MAX_TEXT} characters per text")
        before, after = before.replace("\r\n", "\n"), after.replace("\r\n", "\n")
        if before != after:
            out.append({"before": before, "after": after})
    if not out:
        raise Refused(400, "nothing changed")
    return version, rel, out


class Phases:
    """How long each step of one save took, for one log line."""

    def __init__(self):
        self.start = self.mark = time.monotonic()
        self.ms: dict[str, float] = {}

    def lap(self, phase: str) -> None:
        """The time since the last lap (or skip) goes to `phase`."""
        now = time.monotonic()
        self.ms[phase] = self.ms.get(phase, 0.0) + (now - self.mark) * 1000
        self.mark = now

    def took(self, phase: str, seconds: float) -> None:
        self.ms[phase] = seconds * 1000

    def skip(self) -> None:
        """The time since the last lap is in no phase (it was recorded with took)."""
        self.mark = time.monotonic()

    def line(self) -> str:
        total = (time.monotonic() - self.start) * 1000
        parts = [f"{k} {v:.0f} ms" for k, v in self.ms.items()]
        return ", ".join([*parts, f"total {total:.0f} ms"])


def _timed(start: float, call, *args):
    """call(*args), and the seconds from `start` until it returned."""
    out = call(*args)
    return out, time.monotonic() - start


def save(
    store: Store,
    name: str,
    who: str,
    body,
    uploads: Uploads | None = None,
    files: FileCache | None = None,
    pages: PageCache | None = None,
    phases: Phases | None = None,
) -> dict:
    """Write the edited page as a new version and make it current. Returns its page.json.
    `uploads` reserves the version id against publishes under way. `files` and `pages` are the
    gateway's caches: what they hold (the old version's page.json, file list and HTML, the
    page's state) is not read again, and what was written goes into them. `phases` gets the
    time of each step."""
    version, rel, changes = check(body)
    files = files or FileCache(store)
    phases = phases or Phases()
    with page_lock(name), ThreadPoolExecutor(COPY_THREADS) as pool:
        # The kept current version may be a few seconds old: ask the bucket unless it agrees
        # (and it is asked again right before `current` is written).
        # The page's state as it is now, with its generation: kept after the commit only if no
        # other change to the page (visibility, current, a publish) came in meanwhile.
        snapshot = pages.snapshot(name) if pages else (0, 0.0, (None, "", ""))
        current, visibility, _ = snapshot[2]
        if current != version:
            current = store.current(name)
        if not current:
            raise Refused(404, f"no page named {name}")
        if version != current:
            raise Refused(409, STALE)
        old = files.meta(name, current)
        if who.strip().lower() != (old.get("published_by") or "").strip().lower():
            raise Refused(403, "only the owner can edit this page")
        if old.get("format") == "markdown":
            raise Refused(409, "a Markdown update is edited from the overview (Edit)")
        # Under way while the file is read and patched: the new version id, the file list, and
        # the versions for the answer.
        if uploads:
            reserving = pool.submit(uploads.reserve, name)
        else:
            reserving = pool.submit(new_version, store, name)
        listing = pool.submit(files.listing, name, current)
        listed = pool.submit(pages.versions, name) if pages else None
        try:
            sizes = listing.result()
            if rel not in sizes:
                raise Refused(404, f"{rel} is not in version {current}")
            if sizes[rel] > MAX_HTML:
                too_big = f"{rel} is over {MAX_HTML // 1024**2} MB; edit its source instead"
                raise Refused(413, too_big)
            raw = files.read(name, current, rel)
            if raw is None:
                raise Refused(404, f"{rel} is not in version {current}")
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise Refused(422, f"{rel} is not UTF-8 text; edit its source instead") from None
            phases.lap("lookup")
            patched = patch(source, changes).encode("utf-8")
            phases.lap("patch")
            new = reserving.result()
            phases.lap("reserve")

            # The patched file and the copies of the others, all at once.
            others = [p for p in sizes if p not in (rel, META_FILE)]
            src, dst = store.key(name, current) + "/", store.key(name, new) + "/"
            start = time.monotonic()
            put = pool.submit(_timed, start, store.put, dst + rel, patched)
            copies = {p: pool.submit(_timed, start, store.copy, src + p, dst + p) for p in others}
            tags = {p: done.result()[0] for p, done in copies.items()}
            html_tag, put_seconds = put.result()
            phases.took("copies", max((done.result()[1] for done in copies.values()), default=0))
            phases.took("put", put_seconds)
            phases.skip()

            page = page_record(
                name, new, who, old.get("description", ""),
                {**(old.get("meta") or {}), "edited_from": current}, old.get("git"),
                len(others) + 1, kept_labels(old), old.get("title") or "",
                old.get("format") or "html",
            )  # fmt: skip
            # `current` may not have moved while the files were written (set-current, a publish).
            if store.current(name) != current:
                if pages:
                    pages.forget(name)
                raise Refused(409, STALE)
            meta_tag = finish(store, page, None)
            phases.lap("commit")
        finally:
            if uploads and reserving.exception() is None:
                uploads.release(name, reserving.result())

        # Written: keep the new version as if it had been read, so the answer and the next save
        # cost no bucket reads. The other files are the old version's bytes; the ETags are the
        # bucket's for the new keys (the old file's when the copy answered none).
        data = page_json(page)
        files.seed_listing(name, new, {**sizes, rel: len(patched), META_FILE: len(data)})
        for path, tag in tags.items():
            kept = files.peek(name, current, path)
            if kept is not None:
                tag = tag or kept.etag[len(current) + 2 : -1]
                files.seed(name, new, path, tag, kept.data, kept.size)
        files.seed(name, new, rel, html_tag, patched, len(patched))
        files.seed(name, new, META_FILE, meta_tag, data, len(data))
        if pages:
            try:
                versions = sorted({*listed.result(), current, new})
            except Exception:  # the list is asked again when needed
                versions = None
            pages.put(name, snapshot, (new, visibility, who), versions)
    return page
