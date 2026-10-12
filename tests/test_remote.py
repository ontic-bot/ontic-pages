"""The command line through the gateway: login, publish with presigned uploads, the rest."""

import json
import stat
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import SECRET, request

from ontic_pages import remote
from ontic_pages.cli import main
from ontic_pages.config import token_path
from ontic_pages.publish import publish
from ontic_pages.store import Store
from ontic_pages.tokens import sign

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
OWNER = "owner@onticlabs.io"
OTHER = "other@onticlabs.io"


def test_login_whoami_logout(local, gateway_env, monkeypatch, capsys):
    srv = local()
    gateway_env(srv)
    monkeypatch.setattr(remote, "POLL_SECONDS", 0.01)
    apex = srv.url.removeprefix("http://")

    def browser(link):
        """The person: opens the link signed in, compares the code, clicks Allow."""
        assert link.startswith(f"{srv.url}/_cli/login?code=")
        code = parse_qs(urlsplit(link).query)["code"][0]
        path = link.removeprefix(srv.url)
        assert request(srv, path, host=apex, who=OWNER)[0] == 200
        status, _, _ = request(
            srv, "/_api/cli/approve", host=apex, who=OWNER, method="POST",
            body=json.dumps({"code": code}).encode(),
            headers={"Content-Type": "application/json", "Origin": srv.url,
                     "Sec-Fetch-Site": "same-origin"},
        )  # fmt: skip
        assert status == 200
        return True

    monkeypatch.setattr(remote.webbrowser, "open", browser)
    main(["login"])
    out = capsys.readouterr().out
    assert "code ending in" in out and f"signed in as {OWNER}" in out
    path = token_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    main(["whoami"])
    assert capsys.readouterr().out.strip() == OWNER
    main(["logout"])
    assert not path.exists() and "signed out" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="run ontic-pages login"):
        main(["whoami"])


def test_not_signed_in_or_expired(local, gateway_env, site):
    srv = local()
    gateway_env(srv)
    for argv in (["list"], ["info", "x"], ["share", "x", "public"], ["publish", str(site),
                 "--name", "x"]):  # fmt: skip
        with pytest.raises(SystemExit, match="run ontic-pages login"):
            main(argv)
    remote.save_token(sign(SECRET, OWNER, now=1_000_000))
    with pytest.raises(SystemExit, match="expired: run ontic-pages login"):
        main(["list"])


def test_messages_name_the_command_the_user_ran(local, gateway_env, monkeypatch):
    gateway_env(local())
    monkeypatch.setenv("ONTIC_PAGES_PROG", "ontic pages")
    with pytest.raises(SystemExit, match="run ontic pages login"):
        main(["list"])


def test_publish_through_the_gateway(local, gateway_env, store, s3, uploads, site, capsys):
    srv = local()
    gateway_env(srv, OWNER)
    main(["publish", str(site), "--name", "report", "--description", "first",
          "--meta", "model=abc", "--visibility", "private",
          "--pillar", "robot-learning", "--tag", "franka", "--tag", "eval"])  # fmt: skip
    out = capsys.readouterr().out
    version = store.current("report")
    assert f"published report version {version} (6 files)" in out
    assert f"private: only {OWNER} can open it" in out
    assert out.strip().endswith(f"{srv.url}/report/")
    page = store.meta("report", version)
    assert (page["published_by"], page["description"]) == (OWNER, "first")
    assert page["meta"] == {"model": "abc"}
    assert (page["pillar"], page["tags"]) == ("robot-learning", ["eval", "franka"])
    assert page["files"] == 6 and store.visibility("report") == "private"
    # Every file went straight to the bucket, with its signed content type.
    puts = dict(uploads.puts)
    assert sorted(puts) == sorted(
        f"report/{version}/{f}" for f in ("app.js", "data.json", "docs/index.html",
                                          "img/dot.png", "index.html", "style.css")
    )  # fmt: skip
    assert puts[f"report/{version}/style.css"] == "text/css; charset=utf-8"
    assert s3.objects[("test-bucket", f"report/{version}/img/dot.png")][0].startswith(b"\x89PNG")


def test_publish_in_batches_with_a_retry(local, gateway_env, store, uploads, site, monkeypatch):
    srv = local()
    gateway_env(srv, OWNER)
    monkeypatch.setattr(remote, "BATCH_BYTES", 300)  # a few files per request
    uploads.fail.append(1)  # the first PUT fails once
    main(["publish", str(site), "--name", "report"])
    assert store.meta("report", store.current("report"))["files"] == 6
    assert len(uploads.puts) == 7
    # Failing for good: nothing is committed.
    uploads.fail.extend([1] * 30)
    with pytest.raises(SystemExit, match="failed 3 times"):
        main(["publish", str(site), "--name", "report"])
    assert len(store.versions("report")) == 1


def test_pillar_and_tags_through_the_gateway(local, gateway_env, store, site):
    gateway_env(local(), OWNER)

    def published():
        page = store.meta("report", store.current("report"))
        return page["pillar"], page["tags"]

    main(["publish", str(site), "--name", "report"])
    assert published() == ("other", [])  # a new page without them
    main(["publish", str(site), "--name", "report", "--pillar", "data", "--tag", "eval"])
    assert published() == ("data", ["eval"])
    main(["publish", str(site), "--name", "report"])
    assert published() == ("data", ["eval"])  # kept
    main(["publish", str(site), "--name", "report", "--tag", "a", "--tag", "b"])
    assert published() == ("data", ["a", "b"])  # replaced
    main(["publish", str(site), "--name", "report", "--no-tags", "--pillar", "other"])
    assert published() == ("other", [])
    with pytest.raises(SystemExit, match="bad tag"):
        main(["publish", str(site), "--name", "report", "--tag", "no spaces"])
    with pytest.raises(SystemExit):
        main(["publish", str(site), "--name", "report", "--pillar", "infra"])


def test_publish_someone_elses_page(local, gateway_env, store, site):
    publish(store, site, "theirs", published_by=OTHER, now=T1)
    gateway_env(local(), OWNER)
    with pytest.raises(SystemExit, match="belongs to other@onticlabs.io"):
        main(["publish", str(site), "--name", "theirs"])
    assert len(store.versions("theirs")) == 1


def test_list_info_share_set_current(local, gateway_env, store, site, capsys):
    first = publish(store, site, "report", "first", published_by=OWNER, now=T1)["version"]
    later = publish(store, site, "report", "second", published_by=OWNER)["version"]
    publish(store, site, "theirs", "not mine", visibility="private", published_by=OTHER, now=T1)
    srv = local()
    gateway_env(srv, OWNER)

    main(["list"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("report") and " ontic " in lines[0]
    assert " other " in lines[0] and "second" in lines[0]
    main(["list", "--name", "report"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "visibility: ontic"
    assert lines[1].startswith(f"* {later}  ") and lines[1].endswith(f"  {OWNER}  second")
    assert lines[2].startswith(f"  {first}  07.10.2026 17:30  {OWNER}")
    assert lines[2].startswith(f"  {first}")

    main(["info", "report"])
    out = capsys.readouterr().out
    assert "page         report" in out and f"current      {later}" in out

    main(["share", "report", "private"])
    out = capsys.readouterr().out
    assert "report is now private" in out and f"only {OWNER} can open it" in out
    assert store.visibility("report") == "private"

    main(["set-current", "report", first])
    assert f"report now serves {first}" in capsys.readouterr().out
    assert store.current("report") == first

    with pytest.raises(SystemExit, match="not allowed"):
        main(["share", "theirs", "public"])  # not even visible: private
    with pytest.raises(SystemExit, match="no such version"):
        main(["set-current", "report", "20990101T000000Z"])
    main(["url", "report"])
    assert capsys.readouterr().out.strip() == f"{srv.url}/report/"


def test_direct_flag(s3, gateway_env, local, site, monkeypatch, capsys):
    """--direct uses the bucket key, not the gateway (no token needed)."""
    gateway_env(local())
    monkeypatch.setenv("ONTIC_PAGES_BUCKET", "test-bucket")
    monkeypatch.setattr("ontic_pages.cli.make_client", lambda cfg: s3)
    monkeypatch.setenv("ONTIC_PAGES_EMAIL", OWNER)
    main(["publish", "--direct", str(site), "--name", "report"])
    store = Store(s3, "test-bucket")
    assert store.meta("report", store.current("report"))["published_by"] == OWNER
    assert "generate_presigned_url" not in s3.calls
    main(["list", "--direct"])
    assert "report" in capsys.readouterr().out


def test_batches(monkeypatch):
    monkeypatch.setattr(remote, "BATCH_BYTES", 500)
    sizes = {f"dir/f{i:03}.html": i for i in range(100)}
    parts = remote.batches(sizes, 200)
    assert len(parts) > 5 and [f["path"] for p in parts for f in p] == list(sizes)
    assert len(json.dumps(parts[0])) + 200 <= 500
    assert all(len(json.dumps(p)) <= 500 for p in parts)
