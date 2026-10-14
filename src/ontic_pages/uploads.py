"""Publishing through the gateway, its side: the files never pass through it.

    start   the publisher sends the file list (paths and sizes) and the metadata; the gateway
            checks them, picks the version id and answers a presigned PUT URL per file
    files   more of the file list, for folders whose list does not fit in one request body
    commit  the gateway checks that every file is in the bucket with its size, then writes
            page.json (published_by is the signed-in email), the visibility and `current`,
            exactly as publish.py does

A Markdown update (`markdown` in the start request, from the overview's composer or a .md file
on the command line) lists only its media as files; at commit the gateway renders the Markdown
and writes index.html and update.md itself.

Started versions are kept in memory only (at most MAX_PENDING, for PENDING_SECONDS). A version
that is never committed leaves its files in the bucket, unseen; nothing is deleted.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .info import stamp
from .publish import (
    MARKDOWN_FILE,
    MAX_MARKDOWN,
    META_FILE,
    MILESTONE_RE,
    check_paths,
    clean_title,
    finish,
    labels,
    markdown_files,
    new_version,
    page_record,
    version_iso,
)
from .store import Store, check_pillar, check_tags, check_visibility

MAX_FILES = 20_000
MAX_BYTES = 20 * 1024**3
MAX_PENDING = 100
URL_SECONDS = 3600
PENDING_SECONDS = 2 * 3600
GIT_KEYS = {"remote": str, "branch": str, "commit": str, "dirty": bool}


class Refused(Exception):
    """The request cannot be done: (HTTP status, message)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Pending:
    name: str
    version: str
    who: str
    description: str
    meta: dict
    git: dict | None
    visibility: str | None
    started: float
    pillar: str | None = None  # None: keep the current version's (labels())
    tags: list[str] | None = None
    milestone: str | None = None
    title: str = ""
    markdown: str | None = None
    keep: bool = False  # a Markdown edit: the current version's other files come along
    files: dict[str, int] = field(default_factory=dict)


def file_list(value, markdown: bool = False) -> dict[str, int]:
    """[{path, size}] -> {path: size}, checked. A Markdown update may have no files."""
    if markdown and value in (None, []):
        return {}
    if not isinstance(value, list) or not value:
        raise Refused(400, "files: a non-empty list of {path, size}")
    files: dict[str, int] = {}
    for item in value:
        path, size = (item.get("path"), item.get("size")) if isinstance(item, dict) else (0, 0)
        if not isinstance(path, str) or type(size) is not int or size < 0:
            raise Refused(400, "files: each one is {path: text, size: whole number of bytes}")
        if path in files:
            raise Refused(400, f"files: {path!r} twice")
        files[path] = size
    try:
        check_paths(files, markdown=markdown)
    except ValueError as err:
        raise Refused(400, str(err)) from None
    return files


def page_labels(body: dict) -> tuple[str | None, list[str] | None, str | None]:
    """pillar, tags and milestone from a start request, checked; None (or absent) keeps the
    current ones, "" is no milestone."""
    pillar, tags, milestone = body.get("pillar"), body.get("tags"), body.get("milestone")
    if milestone is not None and not (
        isinstance(milestone, str) and (milestone == "" or MILESTONE_RE.match(milestone))
    ):
        raise Refused(400, "milestone: a milestone id, or empty for none")
    try:
        return (
            None if pillar is None else check_pillar(pillar),
            None if tags is None else check_tags(tags),
            milestone,
        )
    except ValueError as err:
        raise Refused(400, str(err)) from None


def page_text(body: dict) -> tuple[str, str | None]:
    """title and markdown from a start request, checked."""
    title, markdown = body.get("title") or "", body.get("markdown")
    if not isinstance(title, str):
        raise Refused(400, "title: text")
    if markdown is not None and (not isinstance(markdown, str) or len(markdown) > MAX_MARKDOWN):
        raise Refused(400, f"markdown: text, at most {MAX_MARKDOWN // 1024} KB")
    return clean_title(title), markdown


def metadata(body: dict) -> tuple[str, dict, dict | None, str | None]:
    """description, meta, git and visibility from a start request, checked."""
    description, meta = body.get("description") or "", body.get("meta") or {}
    git, visibility = body.get("git"), body.get("visibility")
    if not isinstance(description, str):
        raise Refused(400, "description: text")
    if not isinstance(meta, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in meta.items()
    ):
        raise Refused(400, "meta: a map of text to text")
    if git is not None:
        if not isinstance(git, dict) or not all(
            k in GIT_KEYS and (v is None or isinstance(v, GIT_KEYS[k])) for k, v in git.items()
        ):
            raise Refused(400, "git: {remote, branch, commit, dirty} or null")
    if visibility is not None:
        try:
            check_visibility(visibility)
        except ValueError as err:
            raise Refused(400, str(err)) from None
    return description, meta, git, visibility


class Uploads:
    def __init__(self, store: Store, seconds: float = PENDING_SECONDS, cap: int = MAX_PENDING):
        self.store, self.seconds, self.cap = store, seconds, cap
        self.find = None  # milestone id -> milestone or None (the gateway's Milestones.find)
        self.lock = threading.Lock()
        self.pending: dict[tuple[str, str], Pending] = {}
        self.reserved: set[tuple[str, str]] = set()  # versions an edit is writing (edits.py)

    def _prune(self, now: float) -> None:
        for key in [k for k, p in self.pending.items() if now - p.started > self.seconds]:
            del self.pending[key]

    def start(self, name: str, who: str, body: dict) -> tuple[Pending, list[dict]]:
        """A new pending version with the first files, and their upload URLs. The caller has
        checked the name and that `who` may publish it."""
        description, meta, git, visibility = metadata(body)
        pillar, tags, milestone = page_labels(body)
        title, markdown = page_text(body)
        if milestone and self.find and self.find(milestone) is None:
            raise Refused(400, f"no milestone {milestone!r} (see: milestones)")
        files = file_list(body.get("files"), markdown is not None)
        keep = body.get("keep", False)
        if not isinstance(keep, bool) or (keep and markdown is None):
            raise Refused(400, "keep: true only with markdown (an edit of an update)")
        now = time.monotonic()
        with self.lock:
            self._prune(now)
            if len(self.pending) >= self.cap:
                raise Refused(503, "too many publishes under way, try again in a few minutes")
            version = new_version(self.store, name, taken=self._taken(name))
            up = Pending(
                name, version, who, description, meta, git, visibility, now, pillar, tags,
                milestone, title, markdown, keep,
            )  # fmt: skip
            self.pending[(name, version)] = up
        return up, self._add(up, files)

    def _taken(self, name: str) -> set[str]:
        """Versions promised to an upload or an edit that has not finished (under the lock)."""
        return {v for (n, v) in [*self.pending, *self.reserved] if n == name}

    def reserve(self, name: str) -> str:
        """A new version id for a gateway-side write (a saved edit); release() it when done."""
        with self.lock:
            version = new_version(self.store, name, taken=self._taken(name))
            self.reserved.add((name, version))
        return version

    def release(self, name: str, version: str) -> None:
        with self.lock:
            self.reserved.discard((name, version))

    def get(self, name: str, version: str, who: str) -> Pending:
        with self.lock:
            self._prune(time.monotonic())
            up = self.pending.get((name, version))
        if up is None or up.who.lower() != who.lower():
            raise Refused(404, f"no publish of {name} {version} under way (they last 2 hours)")
        return up

    def add(self, name: str, version: str, who: str, value) -> list[dict]:
        up = self.get(name, version, who)
        return self._add(up, file_list(value, up.markdown is not None))

    def _add(self, up: Pending, files: dict[str, int]) -> list[dict]:
        with self.lock:
            both = {**up.files, **files}
            if len(both) < len(up.files) + len(files):
                raise Refused(400, "files: a path that was already sent")
            if len(both) > MAX_FILES:
                raise Refused(413, f"at most {MAX_FILES} files per version")
            if sum(both.values()) > MAX_BYTES:
                raise Refused(413, f"at most {MAX_BYTES // 1024**3} GB per version")
            up.files = both
        uploads = []
        for path in files:
            url, headers = self.store.presign_put(
                self.store.key(up.name, up.version, path), URL_SECONDS
            )
            uploads.append({"path": path, "url": url, "headers": headers})
        return uploads

    def commit(self, name: str, version: str, who: str) -> tuple[dict, str | None]:
        """Check every file is there with its size, then write the version. Returns page.json and
        the visibility it set (or None)."""
        up = self.get(name, version, who)
        with self.lock:  # one commit at a time; put back if the files are not all there
            if self.pending.pop((name, version), None) is None:
                raise Refused(409, f"{name} {version} is being committed already")
        try:
            return self._commit(up)
        except BaseException:
            with self.lock:
                self.pending[(name, version)] = up
            raise

    def _commit(self, up: Pending) -> tuple[dict, str | None]:
        name, version = up.name, up.version
        found = self.store.sizes(self.store.key(name, version) + "/")
        wrong = [
            f"{path} ({'missing' if path not in found else f'{found[path]} bytes'}, "
            f"expected {size})"
            for path, size in sorted(up.files.items())
            if found.get(path) != size
        ]
        if wrong:
            more = f" and {len(wrong) - 5} more" if len(wrong) > 5 else ""
            raise Refused(409, "not uploaded as announced: " + ", ".join(wrong[:5]) + more)
        try:
            tagged = labels(self.store, name, up.pillar, up.tags, up.milestone, self.find)
        except ValueError as err:
            raise Refused(400, str(err)) from None
        title, fmt, made, kept = up.title, "html", {}, 0
        current = self.store.current(name) if up.keep else None
        if current:
            src, dst = self.store.key(name, current) + "/", self.store.key(name, version) + "/"
            skip = {*up.files, META_FILE, "index.html", MARKDOWN_FILE}
            for path in [p for p in self.store.sizes(src) if p not in skip]:
                self.store.copy(src + path, dst + path)
                kept += 1
        if up.markdown is not None:
            from .markdown import title_of

            title = title or clean_title(title_of(up.markdown)) or name
            made = markdown_files(up.markdown, title, f"{up.who}, {stamp(version_iso(version))}")
            fmt = "markdown"
            for rel, data in made.items():
                self.store.put(self.store.key(name, version, rel), data)
        page = page_record(
            name, version, up.who, up.description, up.meta, up.git,
            len(up.files) + len(made) + kept, tagged, title, fmt,
        )  # fmt: skip
        finish(self.store, page, up.visibility)
        return page, up.visibility
