"""The ontic-pages agent skill, installed for Claude Code and Codex on every run.

The skill text is package data (`skills/ontic-pages/SKILL.md`). Every command except `gateway`
and `skill` calls `sync_skill`, which copies it to `<CLAUDE_CONFIG_DIR or ~/.claude>/skills/` and
`<CODEX_HOME or ~/.codex>/skills/` when those agent folders exist (they are never created), so a
reinstall of the tool, which runs no code, still reaches the agents on the next run.

The installed copy carries a marker line after the frontmatter, with the skill's REVISION in it.
A file with our marker (or the one ontic-cli 0.29.0 wrote) is ours. It is rewritten when its
revision is lower, when it has none (the markers before revisions), or when it has this revision
with other text; one with a higher revision came from a newer ontic-pages and is left alone, so
an older install never puts its older text back. (Older installs look for their own exact
marker, which this one is not, so they leave a revised copy alone too.) A file without a marker
is the user's and is left alone, unless it is exactly the bundled text. A symlinked SKILL.md is
left alone. The sync costs a stat and a small read per target, prints only on a first install (to
stderr) and never raises. `ONTIC_AGENT_SKILLS=0` turns it off.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

NAME = "ontic-pages"
# Bump it whenever SKILL.md changes (tests/test_skills.py pins the text's hash to it).
REVISION = 7
MARKER = (
    f"<!-- managed by ontic-pages (skill revision {REVISION}): updated on every run; delete "
    "this line to keep your own edits, ONTIC_AGENT_SKILLS=0 to stop -->"
)
# Ours, of any revision, and the marker before revisions (no number).
OUR_MARKER = re.compile(r"<!-- managed by ontic-pages(?: \(skill revision (\d+)\))?: ")
# What ontic-cli 0.29.0 put in the copies it installed; those are ours too.
CLI_MARKER = (
    "<!-- managed by ontic-cli: edits are overwritten on update, "
    "set ONTIC_AGENT_SKILLS=0 to stop -->"
)
OPT_OUT = "ONTIC_AGENT_SKILLS"

CURRENT = "installed and current"
OUTDATED = "outdated (rewritten on the next run)"
NEWER = "newer than this install (left alone)"
MISSING = "not installed (installed on the next run)"
YOURS = "yours (no marker, left alone)"
SYMLINK = "symlink, left alone"
NO_AGENT = "no agent folder"
OFF = "turned off (ONTIC_AGENT_SKILLS=0)"


def bundled() -> str:
    """The skill text shipped in the package."""
    return (files("ontic_pages") / "skills" / NAME / "SKILL.md").read_text(encoding="utf-8")


def installed_text(text: str) -> str:
    """`text` with the marker on its own line right after the closing `---` of the frontmatter,
    so the agents still read the frontmatter first; before everything without one."""
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r\n") == "---":
                end = "\r\n" if lines[i].endswith("\r\n") else "\n"
                if not lines[i].endswith(("\n", "\r")):
                    lines[i] += end
                return "".join(lines[: i + 1] + [MARKER + end] + lines[i + 1 :])
    return f"{MARKER}\n{text}"


def turned_off(env: Mapping[str, str]) -> bool:
    return env.get(OPT_OUT, "").strip().lower() in ("0", "false", "no", "off")


def targets(env: Mapping[str, str] | None = None) -> list[tuple[str, Path, Path]]:
    """(agent, agent folder, SKILL.md path) for Claude Code and Codex."""
    env = os.environ if env is None else env
    homes = (
        ("Claude Code", env.get("CLAUDE_CONFIG_DIR") or "~/.claude"),
        ("Codex", env.get("CODEX_HOME") or "~/.codex"),
    )
    out = []
    for agent, home in homes:
        root = Path(home).expanduser()
        out.append((agent, root, root / "skills" / NAME / "SKILL.md"))
    return out


def revision(installed: str) -> int | None:
    """The skill revision in an installed copy's marker: 0 for our markers from before
    revisions, None without a marker of ours."""
    found = OUR_MARKER.search(installed)
    if found:
        return int(found.group(1) or 0)
    return 0 if CLI_MARKER in installed else None


def examine(root: Path, target: Path, text: str, wanted: str) -> tuple[str, int | None]:
    """(one of the states above but OFF, the installed revision) for one target; may raise
    OSError."""
    if not root.is_dir():
        return NO_AGENT, None
    if target.is_symlink():
        return SYMLINK, None
    try:
        current = target.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return MISSING, None
    found = revision(current)
    if current == wanted:
        return CURRENT, found
    if found is not None:
        return (NEWER if found > REVISION else OUTDATED), found
    return (OUTDATED if current == text else YOURS), None


def state(root: Path, target: Path, text: str, wanted: str) -> str:
    return examine(root, target, text, wanted)[0]


def write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, target)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)


def sync_skill(env: Mapping[str, str] | None = None) -> None:
    """Install or refresh the skill for each agent whose folder exists. Never raises."""
    env = os.environ if env is None else env
    if turned_off(env):
        return
    try:
        text = bundled()
    except OSError:
        return
    wanted = installed_text(text)
    for agent, root, target in targets(env):
        try:
            found = state(root, target, text, wanted)
            if found in (MISSING, OUTDATED):
                write_atomic(target, wanted)
                if found == MISSING:
                    print(
                        f"installed the {NAME} agent skill for {agent} at {target} "
                        f"({OPT_OUT}=0 to stop)",
                        file=sys.stderr,
                    )
        except OSError:
            continue


def status(env: Mapping[str, str] | None = None) -> list[tuple[str, Path, str]]:
    """(agent, SKILL.md path, state) for each target; the state names the installed copy's
    revision when it is ours."""
    env = os.environ if env is None else env
    text = bundled()
    wanted = installed_text(text)
    out = []
    for agent, root, target in targets(env):
        if turned_off(env):
            found = OFF
        else:
            try:
                found, rev = examine(root, target, text, wanted)
            except OSError as err:
                found, rev = f"unreadable ({err.strerror or err})", None
            if rev:
                found += f"; installed skill revision {rev}, this install {REVISION}"
            elif rev == 0:
                found += f"; installed copy has no skill revision, this install {REVISION}"
        out.append((agent, target, found))
    return out


def install(env: Mapping[str, str] | None = None) -> list[tuple[str, Path, str]]:
    """Write ours to each agent that has a folder, over any regular file (never over a
    symlink); ONTIC_AGENT_SKILLS=0 does not stop it. Returns (agent, path, what happened)."""
    wanted = installed_text(bundled())
    out = []
    for agent, root, target in targets(env):
        try:
            if not root.is_dir():
                done = NO_AGENT
            elif target.is_symlink():
                done = SYMLINK
            elif target.is_file() and target.read_bytes().decode("utf-8", "replace") == wanted:
                done = CURRENT
            else:
                write_atomic(target, wanted)
                done = "installed"
        except OSError as err:
            done = f"failed ({err.strerror or err})"
        out.append((agent, target, done))
    return out
