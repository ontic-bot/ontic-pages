"""The command line's side of the gateway: the sign-in token, JSON calls, and uploads.

The token comes from `ontic-pages login` (tokens.py has the gateway's side) and is kept in
~/.config/ontic-pages/token, mode 600. Every call sends it as `Authorization: Bearer`. A publish
sends the file list to the gateway, PUTs each file straight to the bucket with the presigned URL
it got back (the bytes never pass through the gateway), then asks the gateway to commit.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

from .config import command_name, token_path
from .publish import collect, git_provenance, html_title, read_markdown

POLL_SECONDS = 2.0
LOGIN_SECONDS = 300
BATCH_BYTES = 60_000  # the file list goes in requests under Caddy's 64 KB body cap
META_BYTES = 30_000  # description, --meta and git, in the first of them
PARALLEL = 8
ATTEMPTS = 3
RETRY_SECONDS = 1.0


def not_signed_in() -> str:
    return f"not signed in, or the sign-in expired: run {command_name()} login"


def save_token(token: str, path: Path | None = None) -> None:
    path = path or token_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(token + "\n")


def load_token(path: Path | None = None) -> str | None:
    path = path or token_path()
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


class Api:
    """JSON calls to the gateway at `url` (the apex)."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 120):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def call(self, method: str, path: str, body=None, auth: bool = True) -> tuple[int, dict]:
        """(status, JSON answer). Raises SystemExit when the gateway cannot be reached."""
        headers = {"Accept": "application/json"}
        if auth:
            if not self.token:
                raise SystemExit(not_signed_in())
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as err:
            status, raw = err.code, err.read()
        except (urllib.error.URLError, OSError) as err:
            raise SystemExit(f"cannot reach {self.url}: {getattr(err, 'reason', err)}") from None
        try:
            answer = json.loads(raw or b"{}")
        except ValueError:
            answer = {"error": raw[:200].decode(errors="replace").strip()}
        return status, answer if isinstance(answer, dict) else {}

    def json(self, method: str, path: str, body=None) -> dict:
        """The answer of a call that must succeed; a clear SystemExit otherwise."""
        status, answer = self.call(method, path, body)
        if status == 401:
            raise SystemExit(not_signed_in())
        if status >= 300:
            raise SystemExit(f"{answer.get('error') or 'failed'} (HTTP {status})")
        return answer

    def get(self, path: str) -> dict:
        return self.json("GET", path)

    def post(self, path: str, body: dict) -> dict:
        return self.json("POST", path, body)


def page_path(name: str, *rest: str) -> str:
    return "/".join([f"/_api/pages/{quote(name, safe='')}", *rest])


def login(api: Api, out=None) -> str:
    """The browser hand-off: open the login page with a fresh code, wait for Allow, keep the
    token. Returns the email."""
    out = out or sys.stdout
    code = secrets.token_urlsafe(32)
    link = f"{api.url}/_cli/login?code={code}"
    print(f"Opening {link}", file=out)
    print(
        f"Check that the browser shows the code ending in {code[-4:]}, then click Allow.", file=out
    )
    print("(No browser? Open that link on this machine yourself.)", file=out, flush=True)
    webbrowser.open(link)
    deadline = time.monotonic() + LOGIN_SECONDS
    while time.monotonic() < deadline:
        status, answer = api.call("GET", f"/_api/cli/token?code={code}", auth=False)
        if status == 200 and answer.get("token"):
            save_token(answer["token"])
            return answer.get("email", "")
        if status != 202:
            raise SystemExit(f"login failed: {answer.get('error') or status}")
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"no Allow within 5 minutes; run {command_name()} login again")


def batches(sizes: dict[str, int], first_extra: int) -> list[list[dict]]:
    """The file list in pieces whose JSON stays under BATCH_BYTES (the first one also carries
    the metadata, `first_extra` bytes)."""
    out, batch, used = [], [], first_extra
    for path, size in sizes.items():
        item = {"path": path, "size": size}
        cost = len(json.dumps(item)) + 2
        if batch and used + cost > BATCH_BYTES:
            out.append(batch)
            batch, used = [], 0
        batch.append(item)
        used += cost
    return out + [batch]


class Progress:
    def __init__(self, files: int, total: int, out=None):
        self.files, self.total, self.out = files, total, out or sys.stderr
        self.done_files = self.done_bytes = 0
        self.lock = threading.Lock()
        self.show()

    def add(self, size: int) -> None:
        with self.lock:
            self.done_files += 1
            self.done_bytes += size
            self.show()

    def show(self) -> None:
        mb = 1024 * 1024
        line = (
            f"\ruploading {self.done_files}/{self.files} files, "
            f"{self.done_bytes / mb:.1f}/{self.total / mb:.1f} MB"
        )
        self.out.write(line + ("\n" if self.done_files == self.files else ""))
        self.out.flush()


def put(url: str, path: Path, size: int, headers: dict[str, str]) -> None:
    """PUT one file to its presigned URL, streamed from disk, tried ATTEMPTS times."""
    for attempt in range(ATTEMPTS):
        try:
            with path.open("rb") as fh:
                req = urllib.request.Request(
                    url,
                    data=fh if size else b"",
                    method="PUT",
                    headers={**headers, "Content-Length": str(size)},
                )
                with urllib.request.urlopen(req, timeout=300) as resp:
                    resp.read()
            return
        except urllib.error.HTTPError as err:
            error = f"HTTP {err.code}: {err.read()[:300].decode(errors='replace')}"
        except (urllib.error.URLError, OSError) as err:
            error = str(getattr(err, "reason", err))
        if attempt + 1 < ATTEMPTS:
            time.sleep(RETRY_SECONDS * (attempt + 1))
    raise SystemExit(f"upload of {path} failed {ATTEMPTS} times: {error}")


def publish(
    api: Api,
    source: Path,
    name: str,
    description: str = "",
    meta: dict[str, str] | None = None,
    visibility: str | None = None,
    cwd: Path | None = None,
    pillar: str | None = None,
    tags: list[str] | None = None,
    milestone: str | None = None,
    title: str = "",
) -> dict:
    """Publish through the gateway. Returns its commit answer: {page (page.json), visibility,
    url}. A pillar, tags or milestone left as None keep the current version's. A .md file goes
    as Markdown (the gateway renders it) with the files it points at."""
    markdown = None
    if source.suffix.lower() == ".md" and source.is_file():
        markdown, files = read_markdown(source)
        title = title or ""
    else:
        files = collect(source)
        title = title or html_title(files.get("index.html", Path()))
    sizes = {rel: path.stat().st_size for rel, path in files.items()}
    start = {
        "description": description,
        "meta": dict(meta or {}),
        "git": git_provenance(cwd or Path.cwd()),
        "visibility": visibility,
        "pillar": pillar,
        "tags": tags,
        "milestone": milestone,
        "title": title,
    }
    extra = len(json.dumps(start))
    if extra > META_BYTES:
        raise SystemExit("the description and --meta are too long")
    if markdown is not None:
        # The Markdown fills the first request; the files it points at follow.
        start["markdown"] = markdown
        if len(json.dumps(start)) > BATCH_BYTES:
            raise SystemExit(f"{source}: too long to publish in one request; split it")
        first, rest = [], [b for b in batches(sizes, 0) if b]
    else:
        first, *rest = batches(sizes, extra)
    answer = api.post(page_path(name, "versions"), {**start, "files": first})
    version, uploads = answer["version"], answer["uploads"]
    for batch in rest:
        uploads += api.post(page_path(name, "versions", version, "files"), {"files": batch})[
            "uploads"
        ]
    progress = Progress(len(uploads), sum(sizes.values()))

    def one(item: dict) -> None:
        rel = item["path"]
        put(item["url"], files[rel], sizes[rel], item.get("headers") or {})
        progress.add(sizes[rel])

    pool = ThreadPoolExecutor(PARALLEL)
    try:
        for future in as_completed([pool.submit(one, item) for item in uploads]):
            future.result()  # the first failure stops the rest; nothing is committed
    finally:
        pool.shutdown(cancel_futures=True)
    return api.post(page_path(name, "versions", version, "commit"), {})
