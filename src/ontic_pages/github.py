"""Milestones kept in step with the GitHub milestones of one repository (onticlabs/experiments,
the ones the Ontic Roadmap project shows in its Milestone field), both ways: a milestone added,
edited, closed or reopened on either side shows on the other within a sync.

The gateway runs `Sync.loop` in a thread when it has a GitHub token: every `interval` seconds,
and soon after a milestone is changed here (`poke`), it lists the repository's milestones once
and then, per milestone:

- a GitHub milestone not linked here is linked to the unlinked one of the same title, else
  imported as a new milestone;
- a milestone here not linked yet is created on GitHub;
- a linked pair is merged field by field against `base`, the fields both sides had at the last
  sync (kept in the milestone's `github` field with a digest of the GitHub milestone then): a
  field changed on one side takes that side's value; changed on both, the later edit wins.
  GitHub is read again just before it is written, and a change here meanwhile waits for the
  next round, so neither side's edit is written over unseen;
- a linked GitHub milestone that is gone (deleted there) moves the one here to the bin
  (`deleted`; hidden, read-only, restorable). Milestones are deleted on GitHub only. Restoring
  one here unlinks it, so the next sync creates it on GitHub again.

GitHub has a title, a description, open or closed and a due date. Title, target date and open
or closed map directly; a target date goes out as noon UTC and comes back as its UTC date, so a
date stays the same date everywhere (GitHub's own time of day is not kept). The description holds
the criteria, then the rest after a marker line, one `- **Key:** value` item each (kind, pillar,
owner, next step, status, result, evidence; a value's further lines indented two spaces), so
both sides carry everything and people on GitHub can read and edit it. A description without
the marker is all criteria. Closing on GitHub closes here as achieved (a question: answered),
with "Closed on GitHub." as the result when there is none. What cannot be taken (too long, a
title GitHub already has) is not cut or guessed: the milestone keeps its `sync_error`, shown in
the overview, until someone fixes it on either side.

GitHub is asked with the standard library (urllib), with a fine-grained token that may write
the repository's issues (milestones belong to issues). A rate limit or a failing GitHub makes
the loop wait as GitHub says, else longer each time, up to ten minutes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .info import PILLAR_LABELS
from .milestones import EMAIL_RE, KINDS, Milestones, clean_milestone, now, set_fields, slug
from .uploads import Refused

API = "https://api.github.com"
MARKER = "<!-- ontic-pages: the lines below are synced, keep the format -->"
WHO = "github"
KEYS = {"kind": "Kind", "pillar": "Pillar", "owner": "Owner", "next": "Next step",
        "status": "Status", "result": "Result", "evidence": "Evidence"}  # fmt: skip
MARKER_LINE = re.compile(rf"^{re.escape(MARKER)}$", re.MULTILINE)
ITEM_RE = re.compile(r"^- \*\*([A-Za-z ]+):\*\* ?(.*)$")
# What a sync carries: the fields both sides have.
SYNCED = ("title", "kind", "pillar", "owner", "criteria", "next", "target", "status", "result",
          "negative", "evidence")  # fmt: skip
MAX_WAIT = 600.0


def log(message: str) -> None:
    print(f"github sync: {message}", file=sys.stderr, flush=True)


class RateLimited(RuntimeError):
    def __init__(self, message: str, wait: float):
        super().__init__(message)
        self.wait = wait


class GitHub:
    """The milestone calls of the REST API, for one repository."""

    def __init__(self, token: str, repo: str, api: str = API):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(f"{self.api}/repos/{self.repo}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ontic-pages",
            **({"Content-Type": "application/json"} if data else {})})  # fmt: skip
        try:
            with urlopen(req, timeout=20) as answer:
                return json.loads(answer.read() or b"null")
        except HTTPError as err:
            detail = err.read()[:300].decode(errors="replace")
            message = f"{method} {path}: {err.code} {detail}"
            wait = rate_wait(err.code, err.headers, time.time())
            if wait is not None:
                raise RateLimited(message, wait) from None
            raise RuntimeError(message) from None

    def milestones(self) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self.call("GET", f"/milestones?state=all&per_page=100&page={page}")
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def get(self, number: int) -> dict | None:
        try:
            return self.call("GET", f"/milestones/{number}")
        except RuntimeError as err:
            if isinstance(err, RateLimited) or ": 404 " not in str(err):
                raise
            return None

    def create(self, body: dict) -> dict:
        return self.call("POST", "/milestones", body)

    def update(self, number: int, body: dict) -> dict:
        return self.call("PATCH", f"/milestones/{number}", body)


def rate_wait(code: int, headers, moment: float) -> float | None:
    """Seconds to wait when GitHub says it is limiting us (Retry-After, or the primary limit's
    reset time), else None."""
    if code not in (403, 429) or headers is None:
        return None
    if headers.get("Retry-After", "").isdigit():
        return float(headers["Retry-After"])
    if (
        headers.get("X-RateLimit-Remaining") == "0"
        and headers.get("X-RateLimit-Reset", "").isdigit()
    ):
        return max(float(headers["X-RateLimit-Reset"]) - moment, 1.0)
    return 60.0 if code == 429 else None


def digest(values) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]


def remote_digest(g: dict) -> str:
    return digest([g.get("title") or "", g.get("description") or "", g.get("state"),
                   (g.get("due_on") or "")[:10]])  # fmt: skip


def synced(m: dict) -> dict:
    return {k: m.get(k) for k in SYNCED}


def render(m: dict, page_url: Callable[[str], str]) -> dict:
    """A milestone as the body of a GitHub milestone."""
    status = m["status"] + (" (negative)" if m.get("negative") else "")
    values = {"kind": m["kind"], "pillar": PILLAR_LABELS.get(m["pillar"], m["pillar"]),
              "owner": m["owner"], "next": m.get("next"), "status": status,
              "result": m.get("result"),
              "evidence": page_url(m["evidence"]) if m.get("evidence") else ""}  # fmt: skip
    lines = [f"- **{KEYS[k]}:** " + v.replace("\n", "\n  ") for k, v in values.items() if v]
    criteria = (m.get("criteria") or "").strip()
    description = "\n\n".join(p for p in (criteria, MARKER + "\n" + "\n".join(lines)) if p)
    return {
        "title": m["title"],
        "description": description,
        "state": "open" if m["status"] == "open" else "closed",
        "due_on": f"{m['target']}T12:00:00Z" if m.get("target") else None,
    }


def parse(g: dict) -> dict:
    """A GitHub milestone as milestone fields: title, criteria and target always, the rest only
    when its description has the marker (the last one). Nothing is cut: clean_milestone refuses
    what is too long."""
    description = (g.get("description") or "").replace("\r\n", "\n")
    # The last marker on a line of its own (a value's lines are indented, so never one).
    at = [m.start() for m in MARKER_LINE.finditer(description)]
    marked = bool(at)
    criteria, rest = description, ""
    if marked:
        criteria, rest = description[: at[-1]], description[at[-1] + len(MARKER) :]
    names = {v.lower(): k for k, v in KEYS.items()}
    found: dict[str, str] = {}
    key = None
    for line in rest.split("\n"):
        item = ITEM_RE.match(line)
        if item and item.group(1).lower() in names:
            key = names[item.group(1).lower()]
            found[key] = item.group(2)
        elif key:  # a value that runs over several lines, indented as render() writes it
            found[key] += "\n" + line.removeprefix("  ")
    out = {"title": (g.get("title") or "").strip(), "criteria": criteria.strip(),
           "target": (g.get("due_on") or "")[:10]}  # fmt: skip
    found = {k: v.strip() for k, v in found.items()}
    if found.get("kind") in KINDS:
        out["kind"] = found["kind"]
    pillars = {label.lower(): p for p, label in PILLAR_LABELS.items()}
    pillar = found.get("pillar", "").lower()
    if pillar in PILLAR_LABELS or pillar in pillars:
        out["pillar"] = pillars.get(pillar, pillar)
    if EMAIL_RE.match(found.get("owner", "")):
        out["owner"] = found["owner"].lower()
    if not marked:
        return out
    for k in ("next", "result"):  # a line taken out on GitHub empties the field
        out[k] = found.get(k, "")
    out["evidence"] = found.get("evidence", "").rstrip("/").rsplit("/", 1)[-1]
    status = found.get("status", "")
    out["negative"] = status.endswith("(negative)")
    out["status"] = status.removesuffix("(negative)").strip()
    return out


def merge(m: dict, g: dict) -> dict:
    """The milestone `m` with what the GitHub milestone `g` says; GitHub's open or closed wins
    over the status written in its description."""
    fields = {**m, **parse(g)}
    if g.get("state") == "open":
        fields["status"] = "open"
    elif fields.get("status") not in ("achieved", "answered", "dropped"):
        fields["status"] = "answered" if fields.get("kind") == "question" else "achieved"
    if fields["status"] in ("achieved", "answered") and not (fields.get("result") or "").strip():
        fields["result"] = "Closed on GitHub."
    return fields


def three_way(base: dict, ours: dict, theirs: dict, theirs_later: bool) -> dict:
    """Per field: the side that changed it since `base`; both, the later edit."""
    out = {}
    for k in SYNCED:
        o, t = ours.get(k), theirs.get(k)
        if o == t or (k in base and t == base[k]):
            out[k] = o
        elif k in base and o == base[k]:
            out[k] = t
        else:
            out[k] = t if theirs_later else o
    return out


class Sync:
    def __init__(self, milestones: Milestones, github: GitHub, owner: str,
                 page_url: Callable[[str], str], interval: float = 30.0):  # fmt: skip
        self.milestones, self.github, self.owner = milestones, github, owner
        self.page_url, self.interval = page_url, interval
        self.wake = threading.Event()
        # Milestones whose GitHub calls failed: id -> (failures, next try (monotonic), a digest
        # of its fields then; an edit here since tries again at once).
        self.failing: dict[str, tuple[int, float, str]] = {}
        self.problems: dict[int, str] = {}  # GitHub milestones that cannot be imported

    def _due(self, m: dict) -> bool:
        count, at, updated = self.failing.get(m["id"], (0, 0.0, ""))
        return at <= time.monotonic() or updated != digest(synced(m))

    def _failed(self, m: dict) -> None:
        count = self.failing.get(m["id"], (0, 0.0, ""))[0] + 1
        wait = min(self.interval * 2**count, MAX_WAIT)
        self.failing[m["id"]] = (count, time.monotonic() + wait, digest(synced(m)))

    def poke(self) -> None:
        """A milestone changed here: sync soon."""
        self.wake.set()

    def loop(self) -> None:
        failures = 0
        while True:
            try:
                self.once()
                failures = 0
            except RateLimited as err:
                log(f"rate limited, waiting {err.wait:.0f} s: {err}")
                time.sleep(min(err.wait, 3600))
                continue
            except Exception as err:  # noqa: BLE001 (a failed sync is retried, later each time)
                failures += 1
                wait = min(self.interval * 2**failures, MAX_WAIT)
                log(f"failed, next try in {wait:.0f} s: {err}")
                time.sleep(wait)
                continue
            self.wake.wait(self.interval)
            self.wake.clear()
            time.sleep(1)  # a burst of edits goes out as one sync

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.loop, name="github-sync", daemon=True)
        thread.start()
        return thread

    def once(self) -> None:
        remote = {g["number"]: g for g in self.github.milestones()}
        items = self.milestones.items()
        linked = {m["github"]["number"] for m in items if m.get("github", {}).get("number")}
        for number, g in remote.items():
            if number in linked:
                continue
            title = (g.get("title") or "").strip().lower()
            mine = next((m for m in items if not m.get("github") and not m.get("deleted")
                         and m["title"].lower() == title), None)  # fmt: skip
            try:
                if mine:  # no common base: per field, the later edit wins
                    self._link(mine["id"], g, base={})
                else:
                    self._import(g)
                self.problems.pop(number, None)
            except Refused as err:
                self.problems[number] = str(err)
                log(f"GitHub #{number}: {err}")
        for number in set(self.problems) - set(remote):
            del self.problems[number]
        for m in self.milestones.items():
            if m.get("deleted"):
                continue
            link = m.get("github")
            if not self._due(m):
                continue  # GitHub failed on it lately: waits longer each time
            try:
                if not link:
                    self._create(m)
                elif link["number"] not in remote:
                    self._gone(m)
                else:
                    self._pair(m, remote[link["number"]])
                self.failing.pop(m["id"], None)
            except RateLimited:
                raise
            except (RuntimeError, OSError, Refused) as err:
                if not isinstance(err, Refused):  # a check here costs GitHub nothing
                    self._failed(m)
                self._error(m["id"], str(err))
                log(f"{m['id']}: {err}")

    def _pair(self, m: dict, g: dict) -> None:
        link = m["github"]
        base, ours = link.get("base") or {}, synced(m)
        theirs_changed = remote_digest(g) != link.get("remote")
        if not theirs_changed and ours == base:
            if m.get("sync_error"):
                self._error(m["id"], "")
            return
        theirs = dict(base)
        if theirs_changed:
            theirs = synced(clean_milestone(merge({**ours, **base}, g), self.owner, new=False))
        # Both times are UTC; their first 19 characters compare as text (GitHub ends in Z).
        later = (g.get("updated_at") or "")[:19] > (m.get("updated_at") or "")[:19]
        result = synced(clean_milestone(three_way(base, ours, theirs, later), self.owner, False))
        if result != theirs:  # GitHub is written: read again first, before taking anything
            again = self.github.get(link["number"])
            if again is None or remote_digest(again) != remote_digest(g):
                return  # changed on GitHub meanwhile (or deleted): next round
        if result != ours and not self._take(m, result):
            return  # changed here meanwhile: next round
        if result != theirs:
            # GitHub has no conditional write for milestones: an edit there in the moment
            # between the read above and this write is the one case still written over.
            g = self.github.update(link["number"], render(result, self.page_url))
            log(f"{m['id']}: sent to GitHub #{g['number']}")
        self._link(m["id"], g, base=result)

    def _take(self, m: dict, fields: dict) -> bool:
        """Set fields that came from GitHub, unless the milestone changed here since `m`."""

        def change(items):
            item = next((i for i in items if i["id"] == m["id"]), None)
            if item != m:
                return False
            set_fields(item, fields, WHO)
            return True

        if self.milestones.change(change):
            log(f"{m['id']}: took GitHub's #{m['github']['number']}")
            return True
        return False

    def _create(self, m: dict) -> None:
        g = self.github.create(render(m, self.page_url))
        self._link(m["id"], g, base=synced(m))
        log(f"{m['id']}: created GitHub #{g['number']}")

    def _link(self, milestone_id: str, g: dict, base: dict) -> None:
        """Note what both sides had at this sync. `base` is what was sent or taken, so an edit
        here during the calls still goes out next round."""

        def change(items):
            item = next((i for i in items if i["id"] == milestone_id), None)
            if item is not None:
                item.pop("sync_error", None)
                # No base yet (linked by title): GitHub's side counts as changed too.
                remote = remote_digest(g) if base else ""
                item["github"] = {"number": g["number"], "url": g.get("html_url", ""),
                                  "remote": remote, "base": base}  # fmt: skip

        self.milestones.change(change)

    def _error(self, milestone_id: str, message: str) -> None:
        """Keep (or clear) why this milestone does not sync, written only when it changes."""
        found = self.milestones.find(milestone_id)
        if found is None or (found.get("sync_error") or "") == message:
            return

        def change(items):
            item = next((i for i in items if i["id"] == milestone_id), None)
            if item is not None:
                if message:
                    item["sync_error"] = message[:300]
                else:
                    item.pop("sync_error", None)

        self.milestones.change(change)

    def _import(self, g: dict) -> None:
        fields = clean_milestone(merge({"owner": self.owner}, g), self.owner, new=True)

        def change(items):
            taken = {i.get("id") for i in items}
            base = slug(fields["title"])
            milestone_id, n = base, 2
            while milestone_id in taken:
                milestone_id, n = f"{base}-{n}", n + 1
            when = now()
            item = {"id": milestone_id, **fields, "created_by": WHO, "created_at": when,
                    "updated_by": WHO, "updated_at": when,
                    "closed_at": None if fields["status"] == "open" else when,
                    "github": {"number": g["number"], "url": g.get("html_url", ""),
                               "remote": remote_digest(g), "base": synced(fields)}}  # fmt: skip
            items.append(item)
            return item

        log(f"{self.milestones.change(change)['id']}: imported GitHub #{g['number']}")

    def _gone(self, m: dict) -> None:
        """Deleted on GitHub: to the bin here (hidden, kept, restorable)."""

        def change(items):
            item = next((i for i in items if i["id"] == m["id"]), None)
            if item is not None:
                item.update(deleted=True, deleted_at=now(), deleted_by=WHO)

        self.milestones.change(change)
        log(f"{m['id']}: GitHub #{m['github']['number']} is gone, moved to the bin")
