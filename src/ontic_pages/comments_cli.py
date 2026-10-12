"""`comments`, `comment` and `resolve`: read and answer a page's comments from the command line.

Comments are written by the gateway only, so these always go through it with the token from
`login` (never --direct). A thread is named by its id or any unique start of it (6 characters
are printed). The gateway allows 20 changes a minute; past that these commands wait and retry.
"""

from __future__ import annotations

import json
import sys
import time

from . import remote
from .config import load_config
from .info import stamp
from .store import check_name

SHORT = 6
MIN_PREFIX = 4
WAIT_SECONDS = 10.0
WAITS = 7  # 70 seconds: longer than the gateway's minute


def api() -> remote.Api:
    return remote.Api(load_config().url, remote.load_token())


def fetch(client: remote.Api, name: str) -> dict:
    return client.get(remote.page_path(name, "comments"))


def short_ids(ids: list[str]) -> dict[str, str]:
    """Each id cut to SHORT characters, longer where that is not unique among `ids`."""
    out = {}
    for i in ids:
        n = SHORT
        while n < len(i) and any(j != i and j.startswith(i[:n]) for j in ids):
            n += 1
        out[i] = i[:n]
    return out


def find(answer: dict, ref: str) -> dict:
    """The thread whose id is `ref` or starts with it (unique, at least MIN_PREFIX characters)."""
    ref = ref.strip().lower()
    threads = answer.get("threads", [])
    exact = [t for t in threads if t["id"] == ref]
    if exact:
        return exact[0]
    if len(ref) < MIN_PREFIX:
        raise SystemExit(f"thread id {ref!r} is too short (use at least {MIN_PREFIX} characters)")
    found = [t for t in threads if t["id"].startswith(ref)]
    if not found:
        raise SystemExit(f"no comment thread {ref} on {answer.get('name')}")
    if len(found) > 1:
        raise SystemExit(f"{ref} matches {len(found)} threads; give more of the id")
    return found[0]


def write(client: remote.Api, path: str, body: dict, out=None) -> dict:
    """A POST that waits and tries again while the gateway says too many changes."""
    out = out or sys.stderr
    for attempt in range(WAITS + 1):
        status, answer = client.call("POST", path, body)
        if status != 429 or attempt == WAITS:
            break
        print(f"too many changes this minute, waiting {WAIT_SECONDS:.0f} s", file=out, flush=True)
        time.sleep(WAIT_SECONDS)
    if status == 401:
        raise SystemExit(remote.not_signed_in())
    if status >= 300:
        raise SystemExit(f"{answer.get('error') or 'failed'} (HTTP {status})")
    return answer


def thread_text(thread: dict, short: str, current: str | None) -> str:
    anchor = thread.get("anchor") or {}
    state = "resolved" if thread.get("resolved") else "open"
    where = anchor.get("path") or "/"
    if anchor.get("tag"):
        where += f"  <{anchor['tag']}>"
    if anchor.get("snippet"):
        where += f'  "{anchor["snippet"]}"'
    if thread.get("version") and thread["version"] != current:
        where += f"  (made on version {thread['version']})"
    lines = [f"{short}  {state}  {where}"]
    for c in thread.get("comments", []):
        lines.append(f"  {c.get('author', '')}, {stamp(c.get('created_at', ''))}:")
        body = "(deleted)" if c.get("deleted") else c.get("body", "")
        lines += [f"    {line}" for line in body.splitlines() or [""]]
    if thread.get("resolved"):
        lines.append(f"  resolved by {thread.get('resolved_by')}, {stamp(thread['resolved_at'])}")
    return "\n".join(lines)


def cmd_comments(args) -> None:
    check_name(args.name)
    answer = fetch(api(), args.name)
    threads = answer.get("threads", [])
    shown = threads if args.all else [t for t in threads if not t.get("resolved")]
    if args.json:
        print(json.dumps({**answer, "threads": shown}, indent=2))
        return
    hidden = len(threads) - len(shown)
    note = f" ({hidden} resolved, not shown: --all shows them)" if hidden else ""
    if not shown:
        print(f"no open comments on {args.name}{note}")
        return
    count = f"{len(shown)} {'thread' if len(shown) == 1 else 'threads'}"
    print(f"{count} on {args.name}{note}, newest activity first\n")
    shorts = short_ids([t["id"] for t in threads])
    print("\n\n".join(thread_text(t, shorts[t["id"]], answer.get("current")) for t in shown))


def cmd_comment(args) -> None:
    check_name(args.name)
    text = sys.stdin.read() if args.text == "-" else args.text
    client = api()
    thread = find(fetch(client, args.name), args.thread)
    path = remote.page_path(args.name, "comments", thread["id"], "reply")
    write(client, path, {"body": text})
    print(f"replied to {thread['id'][:SHORT]} on {args.name}")


def cmd_resolve(args) -> None:
    check_name(args.name)
    client = api()
    thread = find(fetch(client, args.name), args.thread)
    path = remote.page_path(args.name, "comments", thread["id"], "resolve")
    write(client, path, {"resolved": not args.reopen})
    print(f"{'reopened' if args.reopen else 'resolved'} {thread['id'][:SHORT]} on {args.name}")


def add_commands(sub) -> None:
    s = sub.add_parser("comments", help="list a page's open comment threads")
    s.add_argument("name")
    s.add_argument("--all", action="store_true", help="resolved threads too")
    s.add_argument("--json", action="store_true", help="the gateway's answer, as JSON")
    s.set_defaults(func=cmd_comments)

    s = sub.add_parser("comment", help="reply to a comment thread")
    s.add_argument("name")
    s.add_argument("thread", help="the thread id, or its first characters (from `comments`)")
    s.add_argument("text", help="the reply; - reads it from stdin")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("resolve", help="resolve a comment thread (or reopen it)")
    s.add_argument("name")
    s.add_argument("thread", help="the thread id, or its first characters (from `comments`)")
    s.add_argument("--reopen", action="store_true", help="open it again")
    s.set_defaults(func=cmd_resolve)
