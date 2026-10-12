import json
from datetime import UTC, datetime

import pytest
from helpers import TEAM as TEAM_VIEWER
from helpers import request

from ontic_pages.cli import main
from ontic_pages.info import commit_url, github_url, short
from ontic_pages.publish import publish
from ontic_pages.store import Store

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
GIT = {
    "remote": "git@github.com:onticlabs/experiments.git",
    "branch": "main",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "dirty": True,
}
LONG = "A very long description that goes on and on about depth estimation results"
OUTSIDER = "someone@gmail.com"


def test_github_url():
    for remote in (
        "git@github.com:onticlabs/x.git",
        "https://github.com/onticlabs/x",
        "https://github.com/onticlabs/x.git",
        "ssh://git@github.com/onticlabs/x.git",
    ):
        assert github_url(remote) == "https://github.com/onticlabs/x"
    assert github_url("https://gitlab.com/a/b") is None
    assert github_url(None) is None


def test_commit_url():
    sha = "0123456789abcdef0123456789abcdef01234567"
    for remote in ("git@github.com:onticlabs/x.git", "https://github.com/onticlabs/x.git"):
        link = commit_url({"remote": remote, "commit": sha})
        assert link == f"https://github.com/onticlabs/x/commit/{sha}"
    assert commit_url({"remote": "https://gitlab.com/a/b.git", "commit": sha}) is None
    assert commit_url({"remote": "git@github.com:a/b.git", "commit": "nope<>"}) is None
    assert commit_url({"remote": None, "commit": sha}) is None
    assert commit_url(None) is None


def test_short():
    assert short("abc", 5) == '<span title="abc">abc</span>'
    cut = short("a<b> " + "x" * 20, 8)
    assert cut == '<span title="a&lt;b&gt; xxxxxxxxxxxxxxxxxxxx">a&lt;b&gt; xx…</span>'


def publish_two(store, site, monkeypatch):
    monkeypatch.setattr("ontic_pages.publish.git_provenance", lambda cwd: GIT)
    first = publish(
        store, site, "report", "first <i>try</i>", {"seed": "7"}, now=T1,
        pillar="world-models", tags=["Depth", "eval", "depth"],
    )  # fmt: skip
    publish(store, site, "report", LONG, now=T2)  # keeps the pillar and tags
    store.set_current("report", first["version"])
    return first


def test_page_json_fields(store, site, monkeypatch):
    first = publish_two(store, site, monkeypatch)
    saved = store.meta("report", first["version"])
    assert list(saved) == [
        "name", "version", "published_at", "published_by", "title", "description", "pillar",
        "tags", "milestone", "format", "meta", "git", "files",
    ]  # fmt: skip
    assert (saved["pillar"], saved["tags"]) == ("world-models", ["depth", "eval"])
    assert "inputs" not in saved


def test_info_page(store, site, monkeypatch, serve):
    publish_two(store, site, monkeypatch)
    srv = serve()
    status, headers, body = request(srv, "/report/_info")
    listing = request(srv, "/")[2].decode()
    assert request(srv, "/nope/_info")[0] == 404
    text = body.decode()
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    assert "<script" not in text
    assert "first &lt;i&gt;try&lt;/i&gt;" in text
    assert "<th>Visibility</th><td>ontic</td>" in text
    assert "<th>seed</th><td>7</td>" in text
    assert '<a href="https://github.com/onticlabs/experiments">' in text
    assert (
        '<a href="https://github.com/onticlabs/experiments/commit/'
        '0123456789abcdef0123456789abcdef01234567">01234567</a>'
    ) in text
    assert "uncommitted changes" in text
    assert "<tr class=current><td>20261007T153000Z (current)</td><td>07.10.2026 17:30</td>" in text
    assert "<th>Published</th><td>07.10.2026 17:30 by " in text
    assert "<th>Pillar</th><td>World models</td>" in text
    assert "<th>Tags</th><td>depth, eval</td>" in text
    assert f'<span title="{LONG}">{LONG[:59].rstrip()}…</span>' in text
    assert '<a href="/report/_info">info</a>' in listing


def details_of(srv, path, who=TEAM_VIEWER, headers=None):
    status, _, body = request(srv, path, who=who, headers=headers)
    return status, json.loads(body)


def test_details_api(store, site, monkeypatch, serve):
    first = publish_two(store, site, monkeypatch)["version"]
    second = store.versions("report")[-1]
    srv = serve()
    status, d = details_of(srv, f"/_api/pages/report/versions/{first}")
    assert status == 200
    assert d == {
        "name": "report", "version": first, "current": first, "visibility": "ontic",
        "published_at": "2026-10-07T15:30:00+00:00", "published_by": d["published_by"],
        "description": "first <i>try</i>", "meta": {"seed": "7"}, "files": 6,
        "pillar": "world-models", "tags": ["depth", "eval"],
        "git": {
            **GIT,
            "repo_url": "https://github.com/onticlabs/experiments",
            "commit_url": f"https://github.com/onticlabs/experiments/commit/{GIT['commit']}",
        },
    }  # fmt: skip
    # An old version: its own fields; empty ones are left out.
    status, d = details_of(srv, f"/_api/pages/report/versions/{second}")
    assert (status, d["version"], d["current"], d["description"]) == (200, second, first, LONG)
    assert (d["pillar"], d["tags"]) == ("world-models", ["depth", "eval"])
    assert "meta" not in d
    assert details_of(srv, "/_api/pages/report/versions/20990101T000000Z")[0] == 404
    assert details_of(srv, "/_api/pages/ghost/versions/20990101T000000Z")[0] == 404
    # Signed in only, like /_info; and never for the page's own scripts.
    store.set_visibility("report", "public")
    assert details_of(srv, f"/_api/pages/report/versions/{first}", who=None)[0] == 401
    assert details_of(srv, f"/_api/pages/report/versions/{first}", who=OUTSIDER)[0] == 200
    cross = {"Sec-Fetch-Site": "same-site"}
    assert details_of(srv, f"/_api/pages/report/versions/{first}", headers=cross)[0] == 403
    store.set_visibility("report", "ontic")
    assert details_of(srv, f"/_api/pages/report/versions/{first}", who=OUTSIDER)[0] == 403
    store.set_visibility("report", "private")
    assert details_of(srv, f"/_api/pages/report/versions/{first}", who=TEAM_VIEWER)[0] == 403


def test_details_links(store, site, monkeypatch, serve):
    sha = "89abcdef" * 5
    remotes = {
        "ssh": ("git@github.com:onticlabs/x.git", "https://github.com/onticlabs/x"),
        "https": ("https://github.com/onticlabs/x", "https://github.com/onticlabs/x"),
        "gitlab": ("https://gitlab.com/onticlabs/x.git", None),
    }
    versions = {}
    for i, (key, (remote, _)) in enumerate(remotes.items()):
        git = {"remote": remote, "branch": "b", "commit": sha, "dirty": False}
        monkeypatch.setattr("ontic_pages.publish.git_provenance", lambda cwd, git=git: git)
        page = publish(store, site, key, now=datetime(2026, 10, 7, 10, i, tzinfo=UTC))
        versions[key] = page["version"]
    edited = publish(store, site, "edited", meta={"edited_from": "20261001T000000Z"}, now=T1)
    srv = serve()
    for key, (remote, repo) in remotes.items():
        git = details_of(srv, f"/_api/pages/{key}/versions/{versions[key]}")[1]["git"]
        assert (git["remote"], git["repo_url"]) == (remote, repo)
        assert git["commit_url"] == (f"{repo}/commit/{sha}" if repo else None)
        assert git["dirty"] is False
    d = details_of(srv, f"/_api/pages/edited/versions/{edited['version']}")[1]
    assert d["meta"] == {"edited_from": "20261001T000000Z"}


def test_info_command(cli_env, site, monkeypatch, capsys):
    store = Store(cli_env, "test-bucket")
    publish_two(store, site, monkeypatch)
    main(["info", "report"])
    out = capsys.readouterr().out
    assert "visibility   ontic" in out
    assert "current      20261007T153000Z" in out
    assert "meta         seed = 7" in out
    assert "input" not in out
    assert "git@github.com:onticlabs/experiments.git main 01234567 (uncommitted changes)" in out
    assert "  * 20261007T153000Z" in out and "    20261008T090000Z" in out
    with pytest.raises(SystemExit, match="no page named"):
        main(["info", "ghost"])


def test_from_is_gone(cli_env, site):
    with pytest.raises(SystemExit):
        main(["publish", str(site), "--name", "r", "--from", "model:x"])
