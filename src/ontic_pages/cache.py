"""What the gateway keeps in memory.

A version never changes once written, so its files are kept (least recently used out first) by
(name, version, path), and so is the list of its files. Only `current`, `visibility`, the list
of versions and labels.json can change; they are kept for a few seconds. A saved edit (edits.py)
puts what it wrote straight in, so the new version costs no read.

With direct files on (gateway.py), DirectLinks keeps the presigned GET URL of each file it has
handed out, so a browser asking again gets the same URL and can use its own cache.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from .store import Store

MB = 1024 * 1024


@dataclass(frozen=True)
class File:
    etag: str  # a strong ETag, quoted, from the version and the bucket's ETag
    size: int
    data: bytes | None  # None when the file is over the per-file cap: stream it instead


def etag(version: str, tag: str, data: bytes | None) -> str:
    """A file's ETag: its version and the bucket's ETag (a hash of the bytes without one)."""
    return f'"{version}-{tag or hashlib.sha256(data or b"").hexdigest()[:32]}"'


class FileCache:
    MAX_LISTINGS = 1024  # versions whose file list is kept

    def __init__(self, store: Store, max_bytes: int = 256 * MB, max_file: int = 4 * MB):
        self.store, self.max_bytes, self.max_file = store, max_bytes, max_file
        self.lock = threading.Lock()
        self.files: OrderedDict[tuple[str, str, str], File] = OrderedDict()
        self.total = 0
        self.listings: OrderedDict[tuple[str, str], dict[str, int]] = OrderedDict()

    def get(self, name: str, version: str, path: str):
        """(File, open body or None), or None when the file does not exist. The body is open
        only for a big file fetched just now; the caller closes it. For a big file seen before,
        only its size and ETag are kept and there is no body: fetch it again to send it."""
        key = (name, version, path)
        with self.lock:
            hit = self.files.get(key)
            if hit:
                self.files.move_to_end(key)
                return hit, None
        obj = self.store.get(self.store.key(name, version, path))
        if obj is None:
            return None
        size, body = obj["ContentLength"], obj["Body"]
        data = None
        if size <= self.max_file:
            try:
                data = body.read()
            finally:
                body.close()
            body = None
        entry = File(etag(version, (obj.get("ETag") or "").strip('"'), data), size, data)
        self._keep(key, entry, replace=False)
        return entry, body

    def _keep(self, key: tuple[str, str, str], entry: File, replace: bool) -> None:
        with self.lock:
            if key in self.files and not replace:
                return
            old = self.files.pop(key, None)
            self.total -= len(old.data or b"") if old else 0
            self.files[key] = entry
            self.total += len(entry.data or b"")
            while self.total > self.max_bytes and self.files:
                _, old = self.files.popitem(last=False)
                self.total -= len(old.data or b"")

    def peek(self, name: str, version: str, path: str) -> File | None:
        """The kept entry, without asking the bucket."""
        with self.lock:
            return self.files.get((name, version, path))

    def seed(self, name: str, version: str, path: str, tag: str, data: bytes | None, size: int):
        """Keep a file just written (or copied) as if it had been read: `tag` is the bucket's
        ETag for it (empty: a hash of the bytes), `data` its bytes (None: only size and ETag).
        Over the per-file cap only size and ETag are kept, as get() does."""
        if data is not None and size > self.max_file:
            data = None
        self._keep((name, version, path), File(etag(version, tag, data), size, data), True)

    def read(self, name: str, version: str, path: str) -> bytes | None:
        """A file's bytes (kept, or read now), or None when it does not exist."""
        found = self.get(name, version, path)
        if found is None:
            return None
        entry, body = found
        if entry.data is not None:
            return entry.data
        if body is None:
            obj = self.store.get(self.store.key(name, version, path))
            if obj is None:
                return None
            body = obj["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def listing(self, name: str, version: str) -> dict[str, int]:
        """Path -> size of each file in a version (page.json too)."""
        key = (name, version)
        with self.lock:
            if key in self.listings:
                self.listings.move_to_end(key)
                return dict(self.listings[key])
        sizes = self.store.sizes(self.store.key(name, version) + "/")
        if sizes:  # nothing listed: not written yet, or not there; ask again next time
            self.seed_listing(name, version, sizes)
        return sizes

    def seed_listing(self, name: str, version: str, sizes: dict[str, int]) -> None:
        with self.lock:
            self.listings[(name, version)] = dict(sizes)
            self.listings.move_to_end((name, version))
            while len(self.listings) > self.MAX_LISTINGS:
                self.listings.popitem(last=False)

    def meta(self, name: str, version: str) -> dict:
        found = self.get(name, version, "page.json")
        if found is None or found[0].data is None:
            return {}
        return json.loads(found[0].data)


class PageCache:
    """(current version, visibility, owner) and the versions of each page, kept for `ttl`
    seconds so a page with many assets costs one lookup. The owner is the current version's
    published_by.

    Every change the gateway makes to a page (visibility, current, a publish) ends with
    forget(), which also counts up the page's generation. A value read before a forget is never
    kept after it: get() keeps what it read only when the generation did not move meanwhile, and
    put() (a saved edit) only when it is still the generation of the snapshot it started from."""

    def __init__(self, store: Store, files: FileCache, ttl: float):
        self.store, self.files, self.ttl = store, files, ttl
        self.lock = threading.Lock()
        self.entries: dict[str, tuple[float, tuple]] = {}
        self.lists: dict[str, tuple[float, list[str]]] = {}
        self.generations: dict[str, int] = {}

    def get(self, name: str) -> tuple[str | None, str, str]:
        return self.snapshot(name)[2]

    def snapshot(self, name: str) -> tuple[int, float, tuple[str | None, str, str]]:
        """(generation, when the value was read, (current, visibility, owner))."""
        now = time.monotonic()
        with self.lock:
            hit = self.entries.get(name)
            generation = self.generations.get(name, 0)
        if hit and now - hit[0] < self.ttl:
            return generation, hit[0], hit[1]
        version = self.store.current(name)
        visibility = self.store.visibility(name)
        owner = self.files.meta(name, version).get("published_by", "") if version else ""
        value = (version, visibility, owner)
        with self.lock:
            if self.generations.get(name, 0) == generation:
                self.entries[name] = (now, value)
        return generation, now, value

    def versions(self, name: str) -> list[str]:
        """Oldest first, like Store.versions."""
        now = time.monotonic()
        with self.lock:
            hit = self.lists.get(name)
            generation = self.generations.get(name, 0)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        versions = self.store.versions(name)
        with self.lock:
            if self.generations.get(name, 0) == generation:
                self.lists[name] = (now, versions)
        return versions

    def put(
        self,
        name: str,
        snapshot: tuple[int, float, tuple],
        value: tuple[str, str, str],
        versions: list[str] | None,
    ) -> bool:
        """What a saved edit made true: (current, visibility, owner), and the versions when
        known, kept as old as the snapshot the save started from (its visibility was read
        then). Only when nothing changed the page since that snapshot; otherwise the page is
        forgotten, so the next request reads it fresh. True when kept."""
        generation, read_at, _ = snapshot
        with self.lock:
            if self.generations.get(name, 0) != generation:
                self.entries.pop(name, None)
                self.lists.pop(name, None)
                return False
            # A change of its own: a read still under way (of the state before) is not kept.
            self.generations[name] = generation + 1
            self.entries[name] = (read_at, value)
            if versions is None:
                self.lists.pop(name, None)
            else:
                self.lists[name] = (read_at, list(versions))
            return True

    def forget(self, name: str) -> None:
        """After a change to the page: read it fresh next time, and never keep a value read
        before now."""
        with self.lock:
            self.generations[name] = self.generations.get(name, 0) + 1
            self.entries.pop(name, None)
            self.lists.pop(name, None)


class LabelCache:
    """Each page's labels.json (Store.labels), kept for `ttl` seconds; a change the gateway
    makes is put straight in."""

    def __init__(self, store: Store, ttl: float):
        self.store, self.ttl = store, ttl
        self.lock = threading.Lock()
        self.entries: dict[str, tuple[float, dict]] = {}

    def get(self, name: str) -> dict:
        now = time.monotonic()
        with self.lock:
            hit = self.entries.get(name)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        labels = self.store.labels(name)
        with self.lock:
            self.entries[name] = (now, labels)
        return labels

    def put(self, name: str, labels: dict) -> None:
        with self.lock:
            self.entries[name] = (time.monotonic(), labels)

    def forget(self, name: str) -> None:
        with self.lock:
            self.entries.pop(name, None)


class DirectLinks:
    """Presigned GET URLs for files a browser fetches from the bucket itself (direct files).

    A URL is valid for LINK_SECONDS and handed out again until REUSE_SECONDS have passed, so the
    same file keeps the same URL for a while (the browser's cache works) and every URL handed out
    has at least LINK_SECONDS - REUSE_SECONDS left. Least recently used out first past MAX_LINKS."""

    LINK_SECONDS = 3600
    REUSE_SECONDS = 3000
    MAX_LINKS = 100_000

    def __init__(self, store: Store, clock=time.monotonic):
        self.store, self.clock = store, clock
        self.lock = threading.Lock()
        self.links: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def url(self, key: str) -> tuple[str, int]:
        """(URL, seconds it stays valid at least) for the bucket key."""
        now = self.clock()
        with self.lock:
            hit = self.links.get(key)
            if hit and now - hit[0] < self.REUSE_SECONDS:
                self.links.move_to_end(key)
                return hit[1], int(self.LINK_SECONDS - (now - hit[0]))
        url = self.store.presign_get(key, self.LINK_SECONDS)
        with self.lock:
            self.links[key] = (now, url)
            self.links.move_to_end(key)
            while len(self.links) > self.MAX_LINKS:
                self.links.popitem(last=False)
        return url, self.LINK_SECONDS
