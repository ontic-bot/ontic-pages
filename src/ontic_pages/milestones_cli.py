"""`milestones` and `milestone add|update`: the research milestones from the command line.

Milestones are written by the gateway only (milestones.py), so these always go through it with
the token from `login`. Agents read `milestones` to pick the one a report serves and pass its id
to `publish --milestone`.
"""

from __future__ import annotations

import json

from .comments_cli import api, write
from .info import PILLAR_LABELS, stamp
from .milestones import KINDS, STATUSES
from .store import PILLARS

MARK = {"open": " ", "achieved": "+", "answered": "+", "dropped": "-"}


def field(label: str, value: str) -> str:
    """A labelled value; its further lines indented under the first, blank lines left out (a
    blank line separates milestones)."""
    first, *rest = [part for part in value.strip().split("\n") if part.strip()] or [""]
    return "\n".join([f"    {label}: {first}"] + [f"      {part}" for part in rest])


def line(m: dict) -> str:
    kind = "question" if m.get("kind") == "question" else "capability"
    status = m.get("status", "open")
    if status == "answered" and m.get("negative"):
        status = "answered (negative)"
    target = f"  by {m['target']}" if m.get("target") else ""
    out = [
        f"{MARK.get(m.get('status'), ' ')} {m['id']}",
        f"    {m.get('title', '')}",
        f"    {kind}, {PILLAR_LABELS.get(m.get('pillar'), 'Other')}, {status}, "
        f"owner {m.get('owner', '')}{target}",
    ]
    if m.get("criteria"):
        out.append(field("done when", m["criteria"]))
    if m.get("status") == "open" and m.get("next"):
        out.append(field("next", m["next"]))
    if m.get("result"):
        out.append(field("result", m["result"]))
    if m.get("evidence"):
        out.append(f"    evidence: {m['evidence']}")
    if m.get("closed_at"):
        out.append(f"    closed {stamp(m['closed_at'])}")
    if (m.get("github") or {}).get("url"):
        out.append(f"    github: {m['github']['url']}")
    if m.get("sync_error"):
        out.append(field("not in step with GitHub", m["sync_error"]))
    return "\n".join(out)


def cmd_milestones(args) -> None:
    items = api().get("/_api/milestones")["milestones"]
    if args.pillar:
        items = [m for m in items if m.get("pillar") == args.pillar]
    shown = items if args.all else [m for m in items if m.get("status") == "open"]
    if args.json:
        print(json.dumps({"milestones": shown}, indent=2))
        return
    if not shown:
        closed = len(items) - len(shown)
        print("no open milestones" + (f" ({closed} closed: --all shows them)" if closed else ""))
        return
    print("\n\n".join(line(m) for m in shown))


def fields(args) -> dict:
    out = {}
    for key in ("title", "kind", "pillar", "owner", "criteria", "next", "target", "status",
                "result", "evidence"):  # fmt: skip
        value = getattr(args, key, None)
        if value is not None:
            out[key] = value
    if getattr(args, "negative", False):
        out["negative"] = True
    return out


def cmd_add(args) -> None:
    done = write(api(), "/_api/milestones", fields(args))["milestone"]
    print(f"added {done['id']}\n{line(done)}")


def cmd_update(args) -> None:
    body = fields(args)
    if not body:
        raise SystemExit("nothing to change: give --status, --result, --next, ...")
    done = write(api(), f"/_api/milestones/{args.id}", body)["milestone"]
    print(f"updated {done['id']}\n{line(done)}")


def add_commands(sub) -> None:
    s = sub.add_parser(
        "milestones", help="list the open research milestones (what reports point at)"
    )
    s.add_argument("--all", action="store_true", help="closed ones too")
    s.add_argument("--pillar", choices=PILLARS, help="only this pillar's")
    s.add_argument("--json", action="store_true", help="as JSON")
    s.set_defaults(func=cmd_milestones)

    s = sub.add_parser("milestone", help="add or update a milestone")
    which = s.add_subparsers(dest="action", required=True)

    def common(p, new: bool) -> None:
        p.add_argument("--kind", choices=KINDS, help="a capability to show or a question")
        p.add_argument("--pillar", choices=PILLARS)
        p.add_argument("--owner", help="an email (default: you)")
        p.add_argument("--criteria", help="done when: what shows it, or what answers it")
        p.add_argument("--next", help="the next step")
        p.add_argument("--target", help="a target date, YYYY-MM-DD")
        if not new:
            p.add_argument("--title")
            p.add_argument("--status", choices=STATUSES)
            p.add_argument("--result", help="what was shown or learned (needed to close)")
            p.add_argument(
                "--negative", action="store_true", help="the answer is a useful negative finding"
            )
            p.add_argument("--evidence", help="the page that shows it")

    a = which.add_parser("add", help="a new milestone (open)")
    a.add_argument("title")
    common(a, new=True)
    a.set_defaults(func=cmd_add)

    u = which.add_parser("update", help="change a milestone, or close it with --status")
    u.add_argument("id")
    common(u, new=False)
    u.set_defaults(func=cmd_update)
