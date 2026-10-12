import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import helpers  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

from ontic_pages.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def no_agent_skill(monkeypatch):
    """Commands run in tests never touch the real ~/.claude or ~/.codex (test_skills.py
    turns the sync back on against a temporary home)."""
    monkeypatch.setenv("ONTIC_AGENT_SKILLS", "0")


@pytest.fixture(autouse=True)
def berlin_time(monkeypatch):
    """Dates are shown in local time (the gateway runs in Europe/Berlin): the same zone in every
    test, also for node."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def store(s3):
    return Store(s3, "test-bucket")


@pytest.fixture
def serve(store):
    """Start gateways on the fake store (helpers.start keywords); all stop after the test."""
    servers = []

    def start(**kwargs):
        servers.append(helpers.start(store, **kwargs))
        return servers[-1]

    yield start
    for srv in servers:
        helpers.stop(srv)


@pytest.fixture
def site(tmp_path):
    """A small page folder: index, a stylesheet, a script, an image, a sub folder."""
    root = tmp_path / "site"
    (root / "img").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "index.html").write_text("<h1>version one</h1>")
    (root / "style.css").write_text("h1 { color: red }")
    (root / "app.js").write_text("console.log(1)")
    (root / "data.json").write_text('{"a": 1}')
    (root / "img" / "dot.png").write_bytes(b"\x89PNG\r\n\x1a\n0123456789")
    (root / "docs" / "index.html").write_text("<p>docs</p>")
    (root / ".DS_Store").write_bytes(b"junk")
    return root


@pytest.fixture
def cli_env(s3, tmp_path, monkeypatch):
    """Run the real command line straight against the fake store (--direct), no config file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ONTIC_PAGES_BUCKET", "test-bucket")
    monkeypatch.setenv("ONTIC_PAGES_URL", "https://pages.example.org")
    monkeypatch.setenv("ONTIC_PAGES_DIRECT", "1")
    monkeypatch.setattr("ontic_pages.cli.make_client", lambda cfg: s3)
    return s3


@pytest.fixture
def uploads(s3):
    """The bucket's side of presigned PUTs, into the fake store."""
    srv = helpers.upload_server(s3)
    yield srv
    helpers.stop(srv)


@pytest.fixture
def local(store, uploads):
    """Start gateways at their own address (helpers.start_local keywords), with a token
    secret and presigned uploads into the fake store; all stop after the test."""
    servers = []

    def start(**kwargs):
        servers.append(helpers.start_local(store, **kwargs))
        return servers[-1]

    yield start
    for srv in servers:
        helpers.stop(srv)


@pytest.fixture
def gateway_env(tmp_path, monkeypatch):
    """The command line in its default mode (through a gateway), no config file. Call it with
    the gateway and an email to sign in as (None: no token)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ONTIC_PAGES_DIRECT", raising=False)
    monkeypatch.setattr("ontic_pages.remote.RETRY_SECONDS", 0)

    def use(srv, email=None):
        from ontic_pages.remote import save_token
        from ontic_pages.tokens import sign

        monkeypatch.setenv("ONTIC_PAGES_URL", srv.url)
        if email:
            save_token(sign(helpers.SECRET, email))

    return use
