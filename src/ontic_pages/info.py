"""What is known about a page, from its page.json files: for `ontic-pages info` and the
gateway's /<name>/_info page."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from urllib.parse import quote

from .store import DEFAULT_PILLAR, PILLARS, Store

GITHUB_RE = re.compile(r"^(?:https://|ssh://git@|git@)github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
NAME_CHARS, DESCRIPTION_CHARS = 32, 60
DETAIL_FIELDS = ("published_at", "published_by", "description", "pillar", "tags", "meta", "files")
GIT_FIELDS = ("remote", "branch", "commit", "dirty")
PILLAR_LABELS = {
    "data": "Data",
    "scene-reconstruction": "Scene reconstruction",
    "world-models": "World models",
    "robot-learning": "Robot learning",
    "other": "Other",
}


def stamp(iso: str | None) -> str:
    """An ISO time as DD.MM.YYYY hh:mm in this machine's time zone (the gateway runs in
    Europe/Berlin); "" for none, the text itself when it is no time."""
    try:
        moment = datetime.fromisoformat(iso or "")
    except ValueError:
        return iso or ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


def version_stamp(version: str) -> str:
    """A version id (20261012T101500Z) as DD.MM.YYYY hh:mm."""
    try:
        moment = datetime.strptime(version, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return version
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


def pillar_of(page: dict) -> str:
    """The page's pillar id; other for a page.json from before pillars."""
    return page.get("pillar") if page.get("pillar") in PILLARS else DEFAULT_PILLAR


def tags_of(page: dict) -> list[str]:
    tags = page.get("tags")
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def history(store: Store, name: str) -> tuple[str | None, list[dict]]:
    """The current version and every version's page.json, newest first."""
    versions = store.versions(name)
    metas = [store.meta(name, v) or {"name": name, "version": v} for v in reversed(versions)]
    return store.current(name), metas


def github_url(remote: str | None) -> str | None:
    m = GITHUB_RE.match(remote or "")
    return f"https://github.com/{m.group(1)}" if m else None


def commit_url(git: dict | None) -> str | None:
    """The commit on GitHub, for a github.com remote (ssh or https form) and a hex commit."""
    git = git or {}
    repo, commit = github_url(git.get("remote")), git.get("commit")
    if repo and isinstance(commit, str) and COMMIT_RE.match(commit):
        return f"{repo}/commit/{commit}"
    return None


def details(name: str, version: str, current: str, visibility: str, page: dict) -> dict:
    """What the bar's Details panel shows for one version: the page.json fields a signed-in
    viewer may see (empty ones left out), and the GitHub links worked out here."""
    out: dict = {"name": name, "version": version, "current": current, "visibility": visibility}
    for key in DETAIL_FIELDS:
        if page.get(key) not in (None, "", {}, []):
            out[key] = page[key]
    out["pillar"], out["tags"] = pillar_of(page), tags_of(page)
    git = page.get("git")
    if isinstance(git, dict) and git:
        out["git"] = {k: git[k] for k in GIT_FIELDS if git.get(k) not in (None, "")}
        out["git"]["repo_url"] = github_url(git.get("remote"))
        out["git"]["commit_url"] = commit_url(git)
    return out


def short(text: str, limit: int) -> str:
    """Escaped HTML for `text` cut to `limit` characters, the full text on hover."""
    text = text or ""
    shown = text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return f'<span title="{html.escape(text)}">{html.escape(shown)}</span>'


def git_label(git: dict | None) -> str:
    if not git:
        return "none recorded"
    dirty = " (uncommitted changes)" if git.get("dirty") else ""
    commit = (git.get("commit") or "")[:8]
    return f"{git.get('remote') or 'no remote'} {git.get('branch') or ''} {commit}{dirty}"


def shown(current: str | None, metas: list[dict]) -> dict:
    """The page.json of the current version (else the newest one)."""
    return next((m for m in metas if m.get("version") == current), metas[0] if metas else {})


def info_text(name: str, current: str | None, metas: list[dict], visibility: str) -> str:
    page = shown(current, metas)
    lines = [
        f"page         {name}",
        f"visibility   {visibility}",
        f"current      {current}",
        f"published    {stamp(page.get('published_at'))} by {page.get('published_by', '')}",
        f"description  {page.get('description', '')}",
        f"pillar       {pillar_of(page)}",
        f"tags         {', '.join(tags_of(page)) or 'none'}",
        f"git          {git_label(page.get('git'))}",
    ]
    lines += [f"meta         {k} = {v}" for k, v in page.get("meta", {}).items()]
    lines.append("versions")
    for m in metas:
        mark = "*" if m.get("version") == current else " "
        by, desc = m.get("published_by", ""), m.get("description", "")
        when = stamp(m.get("published_at")) or version_stamp(m.get("version", ""))
        lines.append(f"  {mark} {m.get('version')}  {when}  {by}  {desc}")
    return "\n".join(lines)


# The bar's look (static/bar.css): same font, colors, light and dark. No script.
INFO_CSS = """
:root { color-scheme: light dark; --bg: #fbfbfa; --card: #fff; --text: #1f1f1e;
  --muted: #6b6b68; --line: #e4e4e1; --hover: #efefec; --accent: #2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1d1d1c; --card: #262625; --text: #ececea; --muted: #a3a39f; --line: #353533;
    --hover: #30302e; --accent: #8aa8ff; }
}
body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
  padding: 24px 16px 48px; color: var(--text); background: var(--bg); }
body > * { max-width: 760px; margin-left: auto; margin-right: auto; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 20px; margin: 4px auto 6px; overflow-wrap: anywhere; }
h2 { font-size: 12px; font-weight: 600; color: var(--muted); margin: 24px auto 6px; }
p { margin: 0 auto 10px; overflow-wrap: anywhere; }
.nav { color: var(--muted); font-size: 13px; }
table { display: block; overflow-x: auto; border-collapse: collapse; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 4px 0; }
th, td { text-align: left; padding: 5px 12px; vertical-align: top; overflow-wrap: anywhere; }
th { color: var(--muted); font-weight: 500; white-space: nowrap; }
tr.current td { font-weight: 600; }
td:first-child { font-variant-numeric: tabular-nums; }
"""


def info_html(name: str, current: str | None, metas: list[dict], visibility: str) -> str:
    e = html.escape
    page = shown(current, metas)
    meta = "".join(
        f"<tr><th>{e(str(k))}</th><td>{e(str(v))}</td></tr>"
        for k, v in page.get("meta", {}).items()
    )
    git = page.get("git") or {}
    repo = github_url(git.get("remote"))
    commit = git.get("commit") or ""
    if git:
        remote = f'<a href="{e(repo)}">{e(repo)}</a>' if repo else e(git.get("remote") or "none")
        short_sha = e(commit[:8])
        link = commit_url(git)
        sha = f'<a href="{e(link)}">{short_sha}</a>' if link else short_sha
        dirty = " (uncommitted changes)" if git.get("dirty") else ""
        git_html = f"{remote}, branch {e(git.get('branch') or '')}, commit {sha}{dirty}"
    else:
        git_html = "none recorded"
    published = f"{e(stamp(page.get('published_at')))} by {e(page.get('published_by', ''))}"
    tags = ", ".join(tags_of(page)) or "none"
    rows = []
    for m in metas:
        is_current = m.get("version") == current
        rows.append(
            f"<tr{' class=current' if is_current else ''}><td>{e(m.get('version', ''))}"
            f"{' (current)' if is_current else ''}</td>"
            f"<td>{e(stamp(m.get('published_at')) or version_stamp(m.get('version', '')))}</td>"
            f"<td>{e(m.get('published_by', ''))}</td>"
            f"<td>{short(m.get('description', ''), DESCRIPTION_CHARS)}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{e(name)}: details</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<style>{INFO_CSS}</style></head><body>
<p class="nav"><a href="/">All pages</a> / <a href="/{quote(name)}/">{e(name)}</a></p>
<h1>{e(name)}</h1>
<p>{e(page.get("description", ""))}</p>
<table>
<tr><th>Visibility</th><td>{e(visibility)}</td></tr>
<tr><th>Published</th><td>{published}</td></tr>
<tr><th>Version</th><td>{e(current or "")}</td></tr>
<tr><th>Pillar</th><td>{e(PILLAR_LABELS[pillar_of(page)])}</td></tr>
<tr><th>Tags</th><td>{e(tags)}</td></tr>
<tr><th>Git</th><td>{git_html}</td></tr>
</table>
<h2>Metadata</h2>
{f"<table>{meta}</table>" if meta else "<p>None recorded.</p>"}
<h2>Versions</h2>
<table>{"".join(rows)}</table>
</body></html>
"""
