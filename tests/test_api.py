"""The command line's routes on the gateway: sign-in, tokens, publishing with presigned URLs."""

import json
import re
import urllib.request
from datetime import UTC, datetime

import pytest
from helpers import SECRET, request

from ontic_pages.publish import publish
from ontic_pages.tokens import sign
from ontic_pages.uploads import MAX_BYTES, MAX_FILES

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
OWNER = "owner@onticlabs.io"
OTHER = "other@onticlabs.io"
OUTSIDER = "someone@gmail.com"
CODE = "k" * 39 + "WXYZ"


def host(srv) -> str:
    return srv.url.removeprefix("http://")


def call(srv, path, email=None, body=None, method=None, token=None, headers=None, who=None):
    """(status, JSON) of an API call with a token for `email` (or the raw `token`)."""
    sent = {"Content-Type": "application/json", **(headers or {})}
    if email or token:
        sent["Authorization"] = f"Bearer {token or sign(SECRET, email)}"
    data = json.dumps(body).encode() if body is not None else None
    status, _, out = request(
        srv, path, host=host(srv), who=who, headers=sent,
        method=method or ("POST" if body is not None else "GET"), body=data,
    )  # fmt: skip
    return status, json.loads(out) if out.startswith(b"{") else out


def browser_post(srv, path, body, who):
    """A POST from the apex's own page, signed in by cookie."""
    headers = {"Origin": srv.url, "Sec-Fetch-Site": "same-origin"}
    return call(srv, path, body=body, headers=headers, who=who)


# --- sign-in ------------------------------------------------------------------------------


def test_login_flow(local):
    srv = local()
    path = f"/_cli/login?code={CODE}"
    # Signed out: sign in first, then back here.
    status, headers, _ = request(srv, path, host=host(srv), who=None)
    assert status == 302 and "/oauth2/start?rd=" in headers["Location"]
    # The page: who, the end of the code, Allow; scripts from the apex only.
    status, headers, body = request(srv, path, host=host(srv), who=OWNER)
    assert status == 200 and OWNER.encode() in body and b">WXYZ</b>" in body
    assert f'data-code="{CODE}"'.encode() in body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'; script-src 'self'")
    assert headers["Referrer-Policy"] == "no-referrer"
    script = re.search(rb'src="(/_bar/login\.[0-9a-f]+\.js)"', body).group(1).decode()
    assert request(srv, script, host=host(srv), who=None)[0] == 200
    assert request(srv, "/_bar/login.0123.css", host=host(srv), who=None)[0] == 404

    assert call(srv, f"/_api/cli/token?code={CODE}")[0] == 202  # not allowed yet
    # Allow only from the page itself (cookie, same origin), never with a token.
    assert call(srv, "/_api/cli/approve", body={"code": CODE}, who=OWNER)[0] == 403
    assert call(srv, "/_api/cli/approve", OWNER, body={"code": CODE})[0] == 403
    status, answer = browser_post(srv, "/_api/cli/approve", {"code": "w" * 43}, OWNER)
    assert status == 400  # a code no page showed
    status, answer = browser_post(srv, "/_api/cli/approve", {"code": CODE}, OWNER)
    assert (status, answer) == (200, {"email": OWNER})
    assert browser_post(srv, "/_api/cli/approve", {"code": CODE}, OTHER)[0] == 400  # once

    assert call(srv, "/_api/cli/token?code=" + "w" * 43)[0] == 202  # wrong code: nothing
    # Browsers never fetch tokens.
    status, _ = call(srv, f"/_api/cli/token?code={CODE}", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, answer = call(srv, f"/_api/cli/token?code={CODE}")
    assert status == 200 and answer["email"] == OWNER
    token = answer["token"]
    assert call(srv, f"/_api/cli/token?code={CODE}")[0] == 202  # handed out once
    assert call(srv, "/_api/me", token=token) == (200, {"email": OWNER})
    # The used code cannot be shown again.
    assert request(srv, path, host=host(srv), who=OWNER)[0] == 400


def test_login_codes_stay_out_of_the_log(local, capfd):
    srv = local()
    request(srv, f"/_cli/login?code={CODE}", host=host(srv), who=OWNER)
    call(srv, f"/_api/cli/token?code={CODE}")
    err = capfd.readouterr().err
    assert "/_cli/login?code=<hidden>" in err and "token?code=<hidden>" in err
    assert CODE not in err


def test_login_needs_a_team_account_and_a_secret(local, serve):
    srv = local()
    status, _, body = request(srv, f"/_cli/login?code={CODE}", host=host(srv), who=OUTSIDER)
    assert status == 400 and b"Only @onticlabs.io accounts" in body
    assert browser_post(srv, "/_api/cli/approve", {"code": CODE}, OUTSIDER)[0] == 403
    bare = local(token_secret="")
    assert request(bare, f"/_cli/login?code={CODE}", host=host(bare), who=OWNER)[0] == 503
    assert call(bare, f"/_api/cli/token?code={CODE}")[0] == 503
    assert call(bare, "/_api/me", OWNER)[0] == 503


# --- tokens -------------------------------------------------------------------------------


def test_tokens_identify(local, store, site):
    publish(store, site, "mine", visibility="private", published_by=OWNER, now=T1)
    srv = local()
    assert call(srv, "/_api/me", OWNER) == (200, {"email": OWNER})
    status, answer = call(srv, "/_api/me")
    assert status == 401 and "run ontic pages login" in answer["error"]
    assert call(srv, "/_api/pages/mine", OWNER)[0] == 200
    assert call(srv, "/_api/pages/mine", OTHER)[0] == 403
    expired = sign(SECRET, OWNER, now=1_000_000)
    status, answer = call(srv, "/_api/pages/mine", token=expired)
    assert status == 401 and "run ontic pages login" in answer["error"]
    assert call(srv, "/_api/me", token=sign("y" * 40, OWNER))[0] == 401
    # The token decides alone: a cookie does not rescue a bad one.
    assert call(srv, "/_api/me", token="op1.x.y", who=OWNER)[0] == 401


def test_tokens_refused_from_browsers(local, store, site):
    publish(store, site, "mine", visibility="private", published_by=OWNER, now=T1)
    srv = local()
    for headers in ({"Origin": srv.url}, {"Sec-Fetch-Site": "same-origin"},
                    {"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"}):  # fmt: skip
        status, answer = call(srv, "/_api/pages/mine", OWNER, headers=headers)
        assert (status, answer["error"]) == (403, "tokens are for the command line, not browsers")
        status, _ = call(srv, "/_api/pages/mine/visibility", OWNER, headers=headers,
                         body={"visibility": "public"})  # fmt: skip
        assert status == 403
    assert store.visibility("mine") == "private"


# --- reading and changing pages -----------------------------------------------------------


def test_list_info_share_set_current(local, store, site):
    first = publish(store, site, "mine", "one", visibility="private", published_by=OWNER, now=T1)
    publish(store, site, "mine", "two", published_by=OWNER)
    publish(store, site, "team", "theirs", published_by=OTHER, now=T1)
    srv = local()
    status, answer = call(srv, "/_api/pages", OWNER)
    assert status == 200 and [p["name"] for p in answer["pages"]] == ["mine", "team"]
    assert answer["pages"][0]["description"] == "two"
    assert [p["name"] for p in call(srv, "/_api/pages", OTHER)[1]["pages"]] == ["team"]
    assert call(srv, "/_api/pages")[0] == 401

    status, info = call(srv, "/_api/pages/mine/info", OWNER)
    assert status == 200 and info["visibility"] == "private"
    assert [m["description"] for m in info["versions"]] == ["two", "one"]
    assert info["versions"][1] == first
    assert call(srv, "/_api/pages/mine/info", OTHER)[0] == 403

    # Only the owner changes a page, with a token as from the bar.
    assert call(srv, "/_api/pages/team/visibility", OWNER, {"visibility": "public"})[0] == 403
    status, page = call(srv, "/_api/pages/mine/visibility", OWNER, {"visibility": "ontic"})
    assert (status, page["visibility"]) == (200, "ontic")
    v1 = first["version"]
    assert call(srv, "/_api/pages/mine/current", OTHER, {"version": v1})[0] == 403
    assert call(srv, "/_api/pages/mine/current", OWNER, {"version": "20990101T000000Z"})[0] == 400
    assert call(srv, "/_api/pages/mine/current", OWNER, {"version": "x"})[0] == 400
    status, page = call(srv, "/_api/pages/mine/current", OWNER, {"version": v1})
    assert (status, page["current"]) == (200, v1) and store.current("mine") == v1
    assert call(srv, "/_api/pages/ghost/current", OWNER, {"version": v1})[0] == 404


# --- publishing ---------------------------------------------------------------------------


def files_of(*items):
    return [{"path": p, "size": n} for p, n in items]


def upload(answer, contents):
    """PUT each file to its presigned URL, with the headers the gateway asked for."""
    for item in answer["uploads"]:
        data = contents[item["path"]]
        req = urllib.request.Request(item["url"], data=data, method="PUT", headers=item["headers"])
        urllib.request.urlopen(req, timeout=5).read()


def test_publish_new_page(local, store, s3, uploads):
    srv = local()
    body = {
        "files": files_of(("index.html", 5), ("img/a.png", 3)),
        "description": "first",
        "meta": {"model": "abc"},
        "git": {
            "remote": "git@github.com:o/r.git",
            "branch": "main",
            "commit": "c0",
            "dirty": False,
        },
        "visibility": "private",
        "published_by": "boss@onticlabs.io",  # never read
    }
    status, answer = call(srv, "/_api/pages/fresh/versions", OTHER, body)
    assert status == 200 and re.fullmatch(r"\d{8}T\d{6}Z", version := answer["version"])
    item = {u["path"]: u for u in answer["uploads"]}
    assert item["index.html"]["headers"] == {"Content-Type": "text/html; charset=utf-8"}
    assert item["img/a.png"]["url"].startswith(f"{s3.upload_base}/test-bucket/fresh/{version}/")
    assert "expires=3600" in item["img/a.png"]["url"]
    assert s3.calls.count("generate_presigned_url") == 2
    assert store.current("fresh") is None  # nothing shows before the commit

    upload(answer, {"index.html": b"<p/>!", "img/a.png": b"png"})
    status, done = call(srv, f"/_api/pages/fresh/versions/{version}/commit", OTHER, {})
    assert status == 200 and done["url"] == f"{srv.url}/fresh/"
    page = done["page"]
    assert page == json.loads(s3.objects[("test-bucket", f"fresh/{version}/page.json")][0])
    assert page["published_by"] == OTHER  # from the token
    assert (page["description"], page["meta"], page["files"]) == ("first", {"model": "abc"}, 2)
    assert page["git"]["commit"] == "c0"
    assert store.current("fresh") == version and store.visibility("fresh") == "private"
    assert s3.objects[("test-bucket", f"fresh/{version}/img/a.png")] == (b"png", "image/png")
    # Committed once only.
    assert call(srv, f"/_api/pages/fresh/versions/{version}/commit", OTHER, {})[0] == 404


def test_who_may_publish(local, store, site):
    publish(store, site, "theirs", visibility="ontic", published_by=OWNER, now=T1)
    srv = local()
    body = {"files": files_of(("index.html", 1))}
    status, answer = call(srv, "/_api/pages/theirs/versions", OTHER, body)
    assert status == 403 and "only they can publish" in answer["error"]
    assert call(srv, "/_api/pages/theirs/versions", OWNER, body)[0] == 200
    status, answer = call(srv, "/_api/pages/brand-new/versions", OUTSIDER, body)
    assert status == 403 and "@onticlabs.io" in answer["error"]
    assert browser_post(srv, "/_api/pages/brand-new/versions", body, None)[0] == 401
    assert call(srv, "/_api/pages/Bad_Name/versions", OWNER, body)[0] == 400
    assert call(srv, "/_api/pages/oauth2/versions", OWNER, body)[0] == 400
    # A cookie from a page's own scripts does not pass either.
    status, _ = call(srv, "/_api/pages/brand-new/versions", body=body, who=OWNER)
    assert status == 403


def test_owner_checked_again_at_commit(local, store, site):
    srv = local()
    body = {"files": files_of(("index.html", 0))}
    _, mine = call(srv, "/_api/pages/race/versions", OTHER, body)
    publish(store, site, "race", published_by=OWNER, now=T1)  # someone else got there first
    status, answer = call(srv, f"/_api/pages/race/versions/{mine['version']}/commit", OTHER, {})
    assert status == 403
    assert store.meta("race", store.current("race"))["published_by"] == OWNER


@pytest.mark.parametrize(
    "files",
    [
        [{"path": "../x.html", "size": 1}],
        [{"path": "/abs.html", "size": 1}],
        [{"path": "a//b.html", "size": 1}],
        [{"path": "a/./b.html", "size": 1}],
        [{"path": "a\\b.html", "size": 1}],
        [{"path": ".git/config", "size": 1}],
        [{"path": "page.json", "size": 1}],
        [{"path": "_info", "size": 1}],
        [{"path": "_v/x.html", "size": 1}],
        [{"path": "ok.html", "size": -1}],
        [{"path": "ok.html", "size": 1.5}],
        [{"path": "ok.html", "size": True}],
        [{"path": "ok.html"}],
        [{"path": "a.html", "size": 1}, {"path": "a.html", "size": 1}],
        [],
        "index.html",
    ],
)
def test_publish_refuses_bad_files(local, store, files):
    srv = local()
    status, _ = call(srv, "/_api/pages/fresh/versions", OWNER, {"files": files})
    assert status == 400
    assert store.versions("fresh") == []


@pytest.mark.parametrize(
    "labels",
    [
        {"pillar": "infra"},
        {"pillar": 3},
        {"tags": "eval"},
        {"tags": ["has space"]},
        {"tags": [1]},
        {"tags": [f"t{i}" for i in range(21)]},
    ],
)
def test_publish_refuses_bad_pillar_or_tags(local, store, labels):
    srv = local()
    body = {"files": files_of(("index.html", 1)), **labels}
    status, answer = call(srv, "/_api/pages/fresh/versions", OWNER, body)
    assert status == 400, answer


def test_publish_limits_and_batches(local, store, monkeypatch):
    srv = local()
    too_many = files_of(*((f"{i}", 0) for i in range(MAX_FILES + 1)))
    monkeypatch.setattr("ontic_pages.gateway.LIST_BODY", 10**7)  # past Caddy, to reach the count
    assert call(srv, "/_api/pages/fresh/versions", OWNER, {"files": too_many})[0] == 413
    huge = files_of(("a.bin", MAX_BYTES - 1), ("b.bin", 2))
    status, answer = call(srv, "/_api/pages/fresh/versions", OWNER, {"files": huge})
    assert status == 413 and "20 GB" in answer["error"]
    # More of the list in a second request; the same limits count across both.
    _, answer = call(srv, "/_api/pages/fresh/versions", OWNER, {"files": files_of(("a", 1))})
    more = f"/_api/pages/fresh/versions/{answer['version']}/files"
    status, extra = call(srv, more, OWNER, {"files": files_of(("b", 2))})
    assert status == 200 and [u["path"] for u in extra["uploads"]] == ["b"]
    assert call(srv, more, OWNER, {"files": files_of(("a", 1))})[0] == 400  # sent already
    assert call(srv, more, OTHER, {"files": files_of(("c", 1))})[0] == 404  # not theirs
    assert call(srv, more, OWNER, {"files": files_of(("c", MAX_BYTES))})[0] == 413


def test_commit_checks_sizes(local, store, s3):
    srv = local()
    body = {"files": files_of(("index.html", 5), ("b.css", 2))}
    _, answer = call(srv, "/_api/pages/fresh/versions", OWNER, body)
    version = answer["version"]
    commit = f"/_api/pages/fresh/versions/{version}/commit"
    status, err = call(srv, commit, OWNER, {})
    assert status == 409 and "b.css (missing, expected 2)" in err["error"]
    upload(answer, {"index.html": b"<p>x</p>", "b.css": b"ab"})  # 8 bytes, not 5
    status, err = call(srv, commit, OWNER, {})
    assert status == 409 and "index.html (8 bytes, expected 5)" in err["error"]
    assert store.current("fresh") is None
    assert call(srv, commit, OTHER, {})[0] == 404  # someone else's publish
    s3.objects[("test-bucket", f"fresh/{version}/index.html")] = (b"<p/>!", "text/html")
    assert call(srv, commit, OWNER, {})[0] == 200  # fixed: it goes through
    assert store.current("fresh") == version


def test_versions_do_not_clash(local, store):
    srv = local()
    body = {"files": files_of(("index.html", 0))}
    a = call(srv, "/_api/pages/fresh/versions", OWNER, body)[1]["version"]
    b = call(srv, "/_api/pages/fresh/versions", OWNER, body)[1]["version"]
    assert a != b  # the first is promised, even though nothing is in the bucket yet


def test_publish_body_limits(local):
    srv = local()
    # The file list may take up to 64 KB (Caddy's cap), anything else stays small.
    big = {"files": files_of(*((f"p{i:05}.html", 1) for i in range(1500)))}
    assert len(json.dumps(big)) > 4096
    assert call(srv, "/_api/pages/fresh/versions", OWNER, big)[0] == 200
    status, _ = call(srv, "/_api/pages/fresh/visibility", OWNER, {"visibility": "x" * 5000})
    assert status == 413
