"""Publish a folder, one HTML file or one Markdown file as a new version of a page.

A Markdown update is rendered to index.html (markdown.py) when it is written, so every page is
static HTML; its source is kept next to it as update.md, and the files it points at (images,
videos) go up with it.
"""

from __future__ import annotations

import getpass
import html
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .store import (
    DEFAULT_PILLAR,
    PILLARS,
    Store,
    check_name,
    check_pillar,
    check_tags,
    check_visibility,
)

META_FILE = "page.json"
MARKDOWN_FILE = "update.md"
MAX_PATH_BYTES = 900  # S3 keys are at most 1024 bytes, with the name and version in front
MAX_MARKDOWN = 48 * 1024  # characters; it travels in one API request (Caddy: 64 KB)
TITLE_LIMIT = 200
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def strip_userinfo(url: str) -> str:
    """https://user:token@github.com/x -> https://github.com/x (never store a token)."""
    return re.sub(r"^(\w+://)[^/@]+@", r"\1", url)


def git_provenance(cwd: Path) -> dict | None:
    """Remote, branch, commit and dirty flag of the git repo at cwd, or None outside one."""
    commit = git(cwd, "rev-parse", "HEAD")
    if not commit:
        return None
    remote = git(cwd, "remote", "get-url", "origin")
    return {
        "remote": strip_userinfo(remote) if remote else None,
        "branch": git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": commit,
        "dirty": bool(git(cwd, "status", "--porcelain")),
    }


def publisher() -> str:
    return git(Path.cwd(), "config", "user.email") or os.environ.get("USER") or getpass.getuser()


def collect(source: Path) -> dict[str, Path]:
    """Relative path -> local file. A single .html file becomes index.html. Hidden files and
    folders (.git, .DS_Store) are skipped."""
    if source.is_file():
        if source.suffix.lower() not in (".html", ".htm"):
            raise SystemExit(f"{source}: a single file must be .html; publish its folder instead")
        return {"index.html": source}
    if not source.is_dir():
        raise SystemExit(f"{source}: no such file or folder")
    files = {}
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_file() and not any(part.startswith(".") for part in rel.parts):
            files[rel.as_posix()] = path
    if not files:
        raise SystemExit(f"{source}: nothing to publish")
    try:
        check_paths(files)
    except ValueError as err:
        raise SystemExit(f"{source}: {err}") from None
    return files


def clean_title(text: str) -> str:
    return " ".join(html.unescape(text or "").split())[:TITLE_LIMIT]


def html_title(path: Path) -> str:
    """The <title> of an HTML file (its first 64 KB), or ""."""
    try:
        with path.open("rb") as fh:
            head = fh.read(65536).decode("utf-8", "replace")
    except OSError:
        return ""
    m = TITLE_RE.search(head)
    return clean_title(m.group(1)) if m else ""


def read_markdown(source: Path) -> tuple[str, dict[str, Path]]:
    """A .md file's text and the local files it points at (relative paths next to it that
    exist), checked like a folder's paths."""
    from .markdown import media_refs

    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise SystemExit(f"{source}: cannot read it as UTF-8 text ({err})") from None
    if len(text) > MAX_MARKDOWN:
        raise SystemExit(f"{source}: at most {MAX_MARKDOWN // 1024} KB of Markdown")
    files = {}
    for rel in media_refs(text):
        path = source.parent / rel
        if path.is_file():
            files[rel] = path
    try:
        check_paths(files, markdown=True)
    except ValueError as err:
        raise SystemExit(f"{source}: {err}") from None
    return text, files


def markdown_files(text: str, title: str, byline: str) -> dict[str, bytes]:
    """The two files a Markdown update adds to its media: the page and its source."""
    from .markdown import page_html, render

    page = page_html(title, render(text, title), byline)
    return {"index.html": page.encode(), MARKDOWN_FILE: text.encode()}


def check_paths(paths, markdown: bool = False) -> None:
    """The file paths a version may have (the gateway checks them too): relative, no '.', '..',
    empty or hidden parts, and nothing the gateway answers itself."""
    for rel in paths:
        parts = rel.split("/")
        if (
            not rel
            or len(rel.encode()) > MAX_PATH_BYTES
            or "\\" in rel
            or any(not p or p in (".", "..") or p.startswith(".") for p in parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)
        ):
            raise ValueError(f"bad file path {rel!r}")
        if rel in (META_FILE, "_info"):
            raise ValueError(f"a top-level {rel} is reserved by ontic-pages")
        if parts[0] == "_v":
            raise ValueError("a top-level _v folder is reserved by ontic-pages (versions)")
        if markdown and rel in ("index.html", MARKDOWN_FILE):
            raise ValueError(f"{rel} is written from the Markdown; rename that file")


def new_version(
    store: Store, name: str, now: datetime | None = None, taken: set[str] | None = None
) -> str:
    """A UTC timestamp that is not taken yet (one second later on a clash). `taken`: versions
    already promised to an upload that has not finished."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    while True:
        version = now.strftime("%Y%m%dT%H%M%SZ")
        if version not in (taken or ()) and not store.exists(store.key(name, version, META_FILE)):
            return version
        now += timedelta(seconds=1)


LABEL_KEYS = ("pillar", "tags", "milestone", "title")


def effective(page: dict, overlay: dict) -> dict:
    """A page.json with what the overview changed since (labels.json) laid over it."""
    return {**page, **{k: overlay[k] for k in LABEL_KEYS if k in overlay}}


def kept_labels(page: dict) -> dict:
    """The pillar, tags and milestone of a page.json (other, none and none for one from before
    them)."""
    pillar = page.get("pillar") if page.get("pillar") in PILLARS else DEFAULT_PILLAR
    milestone = page.get("milestone")
    milestone = milestone if isinstance(milestone, str) and MILESTONE_RE.match(milestone) else ""
    try:
        tags = check_tags(page.get("tags") or [])
    except ValueError:
        tags = []
    return {"pillar": pillar, "tags": tags, "milestone": milestone}


def labels(
    store: Store,
    name: str,
    pillar: str | None,
    tags: list[str] | None,
    milestone: str | None = None,
    find: Callable[[str], dict | None] | None = None,
) -> dict:
    """The pillar, tags and milestone of a new version: the given ones, else those of the
    current version (a later publish without them keeps them), else other and none. A milestone
    ("" for none) must exist (`find`); without a pillar, a newly given milestone brings its own.
    """
    old: dict = {}
    if pillar is None or tags is None or milestone is None:
        current = store.current(name)
        old = kept_labels(
            effective(store.meta(name, current), store.labels(name)) if current else {}
        )
    found = None
    if milestone:
        found = find(milestone) if find else None
        if find and found is None:
            raise ValueError(f"no milestone {milestone!r} (see: milestones)")
    if pillar is None:
        new_milestone = milestone and milestone != old.get("milestone")
        pillar = found["pillar"] if found and new_milestone else old["pillar"]
    tags = old["tags"] if tags is None else tags
    milestone = old["milestone"] if milestone is None else milestone
    return {"pillar": check_pillar(pillar), "tags": check_tags(tags), "milestone": milestone}


def page_record(
    name: str,
    version: str,
    published_by: str,
    description: str,
    meta: dict,
    git: dict | None,
    files: int,
    labels: dict | None = None,
    title: str = "",
    fmt: str = "html",
) -> dict:
    """page.json of a version. labels: pillar, tags and milestone (labels())."""
    labels = labels or {}
    return {
        "name": name,
        "version": version,
        "published_at": datetime.strptime(version, "%Y%m%dT%H%M%SZ")
        .replace(tzinfo=UTC)
        .isoformat(),
        "published_by": published_by,
        "title": title,
        "description": description,
        "pillar": labels.get("pillar", DEFAULT_PILLAR),
        "tags": list(labels.get("tags") or []),
        "milestone": labels.get("milestone", ""),
        "format": fmt,
        "meta": dict(meta or {}),
        "git": git,
        "files": files,
    }


def page_json(page: dict) -> bytes:
    """page.json as written to the bucket."""
    return json.dumps(page, indent=2).encode()


def finish(store: Store, page: dict, visibility: str | None) -> str:
    """After the files: page.json, labels.json (the labels of this version, not deleted), then
    the visibility (only when given; a later publish without it keeps the level), then flip
    `current`. Returns page.json's ETag (may be empty)."""
    name, version = page["name"], page["version"]
    etag = store.put(store.key(name, version, META_FILE), page_json(page))
    # The labels start again from this version (and a deleted page is back).
    store.set_labels(name, {k: page.get(k) for k in LABEL_KEYS})
    if visibility is not None:
        store.set_visibility(name, visibility)
    store.set_current(name, version)
    return etag


def publish(
    store: Store,
    source: Path,
    name: str,
    description: str = "",
    meta: dict[str, str] | None = None,
    visibility: str | None = None,
    published_by: str | None = None,
    cwd: Path | None = None,
    now: datetime | None = None,
    pillar: str | None = None,
    tags: list[str] | None = None,
    milestone: str | None = None,
    title: str = "",
) -> dict:
    """Upload the files, then page.json, then the visibility (only when given; a later publish
    without it keeps the level), then flip `current`. Returns page.json. The pillar, tags and
    milestone, when not given, are those of the current version; the title, when not given,
    is the page's own (<title>, or the Markdown's first heading). A .md source is rendered."""
    from .milestones import load_milestones

    check_name(name)
    if visibility is not None:
        check_visibility(visibility)

    def find(milestone_id: str) -> dict | None:
        return next((m for m in load_milestones(store) if m.get("id") == milestone_id), None)

    tagged = labels(store, name, pillar, tags, milestone, find)
    who = published_by or publisher()
    version = new_version(store, name, now)
    if source.suffix.lower() == ".md" and source.is_file():
        from .info import stamp
        from .markdown import title_of

        text, files = read_markdown(source)
        title = clean_title(title) or clean_title(title_of(text)) or source.stem
        made = markdown_files(text, title, f"{who}, {stamp(version_iso(version))}")
        fmt = "markdown"
    else:
        files, made, fmt = collect(source), {}, "html"
        title = clean_title(title) or html_title(files.get("index.html", Path()))
    for rel, path in files.items():
        with path.open("rb") as fh:
            store.put(store.key(name, version, rel), fh)
    for rel, data in made.items():
        store.put(store.key(name, version, rel), data)
    page = page_record(
        name, version, who, description, meta or {}, git_provenance(cwd or Path.cwd()),
        len(files) + len(made), tagged, title, fmt,
    )  # fmt: skip
    finish(store, page, visibility)
    return page


def version_iso(version: str) -> str:
    return datetime.strptime(version, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
