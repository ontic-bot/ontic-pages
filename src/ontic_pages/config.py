"""Where the pages live: bucket, endpoint, credentials and the gateway's addresses.

Read from ~/.config/ontic-pages/config.toml, then overridden by environment variables:

    bucket            ONTIC_PAGES_BUCKET     (default "ontic-pages")
    endpoint          ONTIC_PAGES_ENDPOINT   (default Backblaze B2 eu-central-003)
    region            ONTIC_PAGES_REGION     (default "eu-central-003")
    prefix            ONTIC_PAGES_PREFIX     (default none: pages sit at the bucket root)
    url               ONTIC_PAGES_URL        (default "https://pages.onticlabs.io"; the gateway's
                                             apex: its scheme and host, the bar and the listing)
    content_suffix    ONTIC_PAGES_CONTENT_SUFFIX  (default: the host of url; each page's content is
                                             served from <name>.<content_suffix>)
    email             ONTIC_PAGES_EMAIL      (who you are; default: git user.email, else $USER)
    direct            ONTIC_PAGES_DIRECT     (1: use your own bucket key, not the gateway)
    access_key_id     AWS_ACCESS_KEY_ID
    secret_access_key AWS_SECRET_ACCESS_KEY
    token_secret      ONTIC_PAGES_TOKEN_SECRET  (the gateway only: signs the command line's tokens)
    github_token      ONTIC_PAGES_GITHUB_TOKEN  (the gateway only: syncs milestones with GitHub's,
                                             github.py; none: no sync)
    github_repo       ONTIC_PAGES_GITHUB_REPO   (default "onticlabs/experiments")
    github_owner      ONTIC_PAGES_GITHUB_OWNER  (an email: the owner of milestones made on GitHub
                                             without one; needed with a token)

Credentials left unset fall through to boto3's own lookup (AWS_PROFILE, ~/.aws). The command line
talks to the gateway at `url` with the token from `ontic-pages login` (token_path()), unless direct.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PREFIX = ""
DEFAULT_BUCKET = "ontic-pages"
DEFAULT_ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"
DEFAULT_REGION = "eu-central-003"
DEFAULT_URL = "https://pages.onticlabs.io"
DEFAULT_GITHUB_REPO = "onticlabs/experiments"

ENV = {
    "bucket": "ONTIC_PAGES_BUCKET",
    "endpoint": "ONTIC_PAGES_ENDPOINT",
    "region": "ONTIC_PAGES_REGION",
    "prefix": "ONTIC_PAGES_PREFIX",
    "url": "ONTIC_PAGES_URL",
    "content_suffix": "ONTIC_PAGES_CONTENT_SUFFIX",
    "email": "ONTIC_PAGES_EMAIL",
    "direct": "ONTIC_PAGES_DIRECT",
    "access_key_id": "AWS_ACCESS_KEY_ID",
    "secret_access_key": "AWS_SECRET_ACCESS_KEY",
    "token_secret": "ONTIC_PAGES_TOKEN_SECRET",
    "github_token": "ONTIC_PAGES_GITHUB_TOKEN",
    "github_repo": "ONTIC_PAGES_GITHUB_REPO",
    "github_owner": "ONTIC_PAGES_GITHUB_OWNER",
}


@dataclass(frozen=True)
class Config:
    bucket: str = DEFAULT_BUCKET
    endpoint: str = DEFAULT_ENDPOINT
    region: str = DEFAULT_REGION
    prefix: str = DEFAULT_PREFIX
    url: str = DEFAULT_URL
    content_suffix: str = ""
    email: str | None = None
    direct: str = ""
    access_key_id: str | None = None
    secret_access_key: str | None = None
    token_secret: str = ""
    github_token: str = ""
    github_repo: str = DEFAULT_GITHUB_REPO
    github_owner: str = ""

    @property
    def is_direct(self) -> bool:
        return self.direct.strip().lower() in ("1", "true", "yes")

    def page_url(self, name: str) -> str:
        return f"{self.url.rstrip('/')}/{name}/"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ontic-pages" / "config.toml"


def command_name() -> str:
    """How the user runs this tool: `ontic pages` through the ontic CLI (in its process, or
    through uvx with ONTIC_PAGES_PROG set), else `ontic-pages`. Used in help and messages."""
    if os.environ.get("ONTIC_PAGES_PROG"):
        return os.environ["ONTIC_PAGES_PROG"]
    import sys

    return "ontic pages" if Path(sys.argv[0]).name == "ontic" else "ontic-pages"


def token_path() -> Path:
    """Where `ontic-pages login` keeps its token (mode 600)."""
    return config_path().parent / "token"


def load_config(path: Path | None = None, env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    path = path or config_path()
    values: dict[str, str] = {}
    if path.exists():
        data = tomllib.loads(path.read_text())
        unknown = set(data) - set(ENV)
        if unknown:
            raise SystemExit(f"{path}: unknown keys {sorted(unknown)}")
        values = {k: str(v) for k, v in data.items()}
    for key, var in ENV.items():
        if env.get(var):
            values[key] = env[var]
    if "prefix" in values:
        values["prefix"] = values["prefix"].strip("/") + "/" if values["prefix"].strip("/") else ""
    return Config(**values)
