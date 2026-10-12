"""Milestones and saved views: the research overview's shared state, two JSON files at the
bucket root, next to the pages (page names cannot start with `_`, so nothing collides):

    <prefix>_milestones.json   {"milestones": [{id, title, kind, pillar, owner, criteria, next,
                                target, status, result, negative, evidence, created_by,
                                created_at, updated_by, updated_at, closed_at}]}
    <prefix>_views.json        {"views": [{id, name, config, created_by, created_at,
                                updated_by, updated_at}]}

A milestone is a capability to demonstrate or a question to resolve, with an owner and the
criteria that say when it is done. It closes as achieved (a capability shown), answered (a
question resolved; `negative` marks a useful negative finding) or dropped, always with a
result: what was learned. Reports point at a milestone from their page.json (`milestone`), so
the evidence of a milestone is the reports that name it, and `evidence` is the one that closed
it.

A view is a saved way to look at one of the overview's two tables, reports or milestones, as
in a Notion database: filters, sort, grouping, the properties shown, layout. Views are shared
with the team; their config is checked here and drawn by static/overview.js.

The gateway is the only writer, as with comments: every change reads, changes and writes the
whole file under one lock; the file is kept in memory and read again at most every `ttl`
seconds. Nothing is deleted from the bucket (a deleted view leaves the file without it).
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .comments import CONTROL
from .store import PILLARS, Store, check_name
from .uploads import Refused

MILESTONES_FILE, VIEWS_FILE = "_milestones.json", "_views.json"
KINDS = ("capability", "question")
STATUSES = ("open", "achieved", "answered", "dropped")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
VIEW_ID_RE = re.compile(r"^[0-9a-f]{8}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
LIMITS = {"title": 140, "criteria": 2000, "next": 500, "result": 2000}
MAX_MILESTONES, MAX_VIEWS = 500, 200

# What a view may say (static/overview.js draws it), per database: its properties (the
# columns), what it sorts and groups by, and the properties it filters on (by value).
DATABASES = {
    "reports": {
        "fields": ("title", "pillar", "milestone", "tags", "author", "published", "visibility",
                   "name"),
        "sorts": ("published", "title", "author", "pillar", "milestone", "name"),
        "groups": ("", "pillar", "milestone", "author", "tags", "visibility"),
        "filters": ("pillar", "milestone", "author", "tags", "visibility"),
        "default_sort": "published",
    },
    "milestones": {
        "fields": ("title", "status", "kind", "pillar", "owner", "target", "reports", "next",
                   "criteria", "result", "updated"),
        "sorts": ("updated", "title", "target", "status", "pillar", "owner"),
        "groups": ("", "status", "pillar", "kind", "owner"),
        "filters": ("status", "kind", "pillar", "owner"),
        "default_sort": "updated",
    },
}  # fmt: skip
LAYOUTS = ("table", "board", "list")
VIEW_NAME_LIMIT, FILTER_ITEMS, TEXT_LIMIT = 60, 50, 200


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def text(value, field: str, limit: int, required: bool = False) -> str:
    """Clean text: a string, CRLF made LF, control characters dropped, trimmed, not too long."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refused(400, f"{field}: text")
    value = CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if required and not value:
        raise Refused(400, f"{field}: required")
    if len(value) > limit:
        raise Refused(400, f"{field}: at most {limit} characters")
    return value


def slug(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return words[:40].rstrip("-") or "milestone"


def parse(raw: str | None, key: str) -> dict:
    if raw is None:
        return {key: []}
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise Refused(502, f"the {key} file cannot be read")
    return data


def load_milestones(store: Store) -> list[dict]:
    """Every milestone, read from the bucket (the command line's --direct publish)."""
    return parse(store.read_text(store.key(MILESTONES_FILE)), "milestones")["milestones"]


class Doc:
    """One JSON file of the bucket root: a list under `key`, cached `ttl` seconds, changed under
    a lock. A cached list is never changed in place, so readers may keep what they got."""

    def __init__(self, store: Store, file: str, key: str, ttl: float = 5.0):
        self.store, self.file, self.key, self.ttl = store, file, key, ttl
        self.lock = threading.Lock()
        self.cache: tuple[float, dict] | None = None

    def _read(self) -> dict:
        moment = time.monotonic()
        if self.cache and moment - self.cache[0] < self.ttl:
            return self.cache[1]
        data = parse(self.store.read_text(self.store.key(self.file)), self.key)
        self.cache = (moment, data)
        return data

    def items(self) -> list[dict]:
        with self.lock:
            return self._read()[self.key]

    def change(self, change: Callable[[list[dict]], dict]) -> dict:
        with self.lock:
            data = copy.deepcopy(self._read())
            out = change(data[self.key])
            self.store.put(self.store.key(self.file), json.dumps(data, indent=1).encode())
            self.cache = (time.monotonic(), data)
            return out


class Milestones(Doc):
    def __init__(self, store: Store, ttl: float = 5.0):
        super().__init__(store, MILESTONES_FILE, "milestones", ttl)

    def find(self, milestone_id: str) -> dict | None:
        return next((m for m in self.items() if m.get("id") == milestone_id), None)

    def create(self, body, who: str) -> dict:
        fields = clean_milestone(body if isinstance(body, dict) else {}, who, new=True)

        def change(items):
            if len(items) >= MAX_MILESTONES:
                raise Refused(409, f"at most {MAX_MILESTONES} milestones")
            taken = {m.get("id") for m in items}
            base = slug(fields["title"])
            milestone_id, n = base, 2
            while milestone_id in taken:
                milestone_id, n = f"{base}-{n}", n + 1
            when = now()
            item = {"id": milestone_id, **fields, "created_by": who, "created_at": when,
                    "updated_by": who, "updated_at": when, "closed_at": None}  # fmt: skip
            if item["status"] != "open":
                item["closed_at"] = when
            items.append(item)
            return item

        return self.change(change)

    def update(self, milestone_id: str, body, who: str) -> dict:
        if not isinstance(body, dict) or not body:
            raise Refused(400, "send the fields to change")

        def change(items):
            item = next((m for m in items if m.get("id") == milestone_id), None)
            if item is None:
                raise Refused(404, f"no milestone {milestone_id}")
            merged = clean_milestone({**item, **body}, who, new=False)
            was_open = item.get("status") == "open"
            item.update(merged, updated_by=who, updated_at=now())
            if was_open and item["status"] != "open":
                item["closed_at"] = item["updated_at"]
            elif item["status"] == "open":
                item["closed_at"] = None
            return item

        return self.change(change)


def clean_milestone(body: dict, who: str, new: bool) -> dict:
    kind = body.get("kind") or "capability"
    if kind not in KINDS:
        raise Refused(400, "kind: capability or question")
    pillar = body.get("pillar") or "other"
    if pillar not in PILLARS:
        raise Refused(400, f"pillar: one of {', '.join(PILLARS)}")
    status = body.get("status") or "open"
    if status not in STATUSES:
        raise Refused(400, f"status: one of {', '.join(STATUSES)}")
    owner = text(body.get("owner") or who, "owner", 254).lower()
    if not EMAIL_RE.match(owner):
        raise Refused(400, "owner: an email address")
    target = text(body.get("target"), "target", 10)
    if target and not DATE_RE.match(target):
        raise Refused(400, "target: a date, YYYY-MM-DD")
    evidence = text(body.get("evidence"), "evidence", 64)
    if evidence:
        try:
            check_name(evidence)
        except ValueError:
            raise Refused(400, "evidence: a page name") from None
    negative = body.get("negative", False)
    if not isinstance(negative, bool):
        raise Refused(400, "negative: true or false")
    out = {
        "title": text(body.get("title"), "title", LIMITS["title"], required=True),
        "kind": kind,
        "pillar": pillar,
        "owner": owner,
        "criteria": text(body.get("criteria"), "criteria", LIMITS["criteria"]),
        "next": text(body.get("next"), "next", LIMITS["next"]),
        "target": target,
        "status": status,
        "result": text(body.get("result"), "result", LIMITS["result"]),
        "negative": negative and status == "answered",
        "evidence": evidence,
    }
    if status in ("achieved", "answered") and not out["result"]:
        raise Refused(400, "result: say what was shown or learned to close a milestone")
    return out


class Views(Doc):
    def __init__(self, store: Store, ttl: float = 5.0):
        super().__init__(store, VIEWS_FILE, "views", ttl)

    def create(self, body, who: str) -> dict:
        body = body if isinstance(body, dict) else {}
        name = text(body.get("name"), "name", VIEW_NAME_LIMIT, required=True)
        config = clean_config(body.get("config"))

        def change(items):
            if len(items) >= MAX_VIEWS:
                raise Refused(409, f"at most {MAX_VIEWS} saved views")
            when = now()
            item = {"id": secrets.token_hex(4), "name": name, "config": config,
                    "created_by": who, "created_at": when, "updated_by": who,
                    "updated_at": when}  # fmt: skip
            items.append(item)
            return item

        return self.change(change)

    def update(self, view_id: str, body, who: str) -> dict:
        body = body if isinstance(body, dict) else {}
        name = body.get("name")
        name = None if name is None else text(name, "name", VIEW_NAME_LIMIT, required=True)
        config = None if body.get("config") is None else clean_config(body["config"])

        def change(items):
            item = next((v for v in items if v.get("id") == view_id), None)
            if item is None:
                raise Refused(404, "no such view")
            if name is not None:
                item["name"] = name
            if config is not None:
                item["config"] = config
            item.update(updated_by=who, updated_at=now())
            return item

        return self.change(change)

    def delete(self, view_id: str, who: str) -> dict:
        """Only the view's creator removes it from the file."""

        def change(items):
            item = next((v for v in items if v.get("id") == view_id), None)
            if item is None:
                raise Refused(404, "no such view")
            if (item.get("created_by") or "").lower() != who.lower():
                raise Refused(403, f"only {item.get('created_by')} can delete this view")
            items.remove(item)
            return item

        return self.change(change)


def clean_config(config) -> dict:
    """A view's config, checked against its database: unknown keys are dropped, bad values
    refused."""
    if not isinstance(config, dict):
        raise Refused(400, "config: an object")
    database = config.get("database") or "reports"
    if database not in DATABASES:
        raise Refused(400, f"config.database: one of {', '.join(DATABASES)}")
    known = DATABASES[database]
    layout = config.get("layout") or "table"
    if layout not in LAYOUTS:
        raise Refused(400, f"config.layout: one of {', '.join(LAYOUTS)}")
    sort = config.get("sort") or {}
    if not isinstance(sort, dict):
        raise Refused(400, "config.sort: {field, dir}")
    field, direction = sort.get("field") or known["default_sort"], sort.get("dir") or "desc"
    if field not in known["sorts"] or direction not in ("asc", "desc"):
        raise Refused(
            400, f"config.sort: field one of {', '.join(known['sorts'])}, dir asc or desc"
        )
    group = config.get("group") or ""
    if group not in known["groups"]:
        raise Refused(400, f"config.group: one of {', '.join(g for g in known['groups'] if g)}")
    fields = config.get("fields") or list(known["fields"][:6])
    if not isinstance(fields, list) or not all(f in known["fields"] for f in fields):
        raise Refused(400, f"config.fields: some of {', '.join(known['fields'])}")
    filters = config.get("filters") or {}
    if not isinstance(filters, dict):
        raise Refused(400, "config.filters: an object")
    clean: dict = {}
    for key in known["filters"]:
        values = filters.get(key) or []
        if not isinstance(values, list) or len(values) > FILTER_ITEMS:
            raise Refused(400, f"config.filters.{key}: a list of at most {FILTER_ITEMS}")
        if values:
            clean[key] = [text(v, f"config.filters.{key}", 254) for v in values]
    query = text(filters.get("text"), "config.filters.text", TEXT_LIMIT)
    if query:
        clean["text"] = query
    since = filters.get("since") or 0
    if type(since) is not int or not 0 <= since <= 3650:
        raise Refused(400, "config.filters.since: days, 0 for any time")
    if since:
        clean["since"] = since
    return {
        "database": database,
        "layout": layout,
        "sort": {"field": field, "dir": direction},
        "group": group,
        "fields": list(dict.fromkeys(fields)),
        "filters": clean,
    }
