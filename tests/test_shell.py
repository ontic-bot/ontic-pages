import json
import re
from datetime import UTC, datetime

import pytest
from helpers import APEX, DOCUMENT, FRAME, ORIGIN, content, request

from ontic_pages.publish import publish
from ontic_pages.shell import inject

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
OWNER = "owner@onticlabs.io"
TAG = b'<script src="/_bridge.'


@pytest.fixture
def two(store, site):
    """report: two versions by OWNER, the second current."""
    first = publish(store, site, "report", "first </script><b>x</b>", published_by=OWNER, now=T1)
    (site / "index.html").write_text("<!doctype html><html><head><title>t</title></head><p>2")
    second = publish(store, site, "report", "second", published_by=OWNER, now=T2)
    return first["version"], second["version"]


def facts(body: bytes) -> dict:
    m = re.search(rb'<script id="ontic-facts" type="application/json">(.*?)</script>', body)
    return json.loads(m.group(1))


def test_shell(serve, two):
    server = serve()
    v1, v2 = two
    status, headers, body = request(server, "/report/docs/?x=1")
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; connect-src 'self'; frame-src https://*.{APEX}; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    )
    text = body.decode()
    assert f'src="https://report.{APEX}/docs/?x=1"' in text
    assert 'sandbox="allow-scripts allow-same-origin allow-forms allow-popups' in text
    assert 'referrerpolicy="no-referrer"' in text
    assert f'href="https://report.{APEX}/docs/?x=1&amp;raw=1"' in text  # open without the bar
    assert '<a class="old" id="old" href="/report/" hidden' in text
    # The info button, left of the comments, opens the Details panel; the title menu's Details
    # item opens the same panel; /_info stays as the plain page behind it.
    assert 'id="m-details" aria-controls="info-panel">Details</button>' in text
    assert 'id="info-btn" type="button" aria-haspopup="dialog" aria-expanded="false" ' in text
    assert text.index('id="info-btn"') < text.index('id="cm-btn"')
    assert 'id="info-panel" role="dialog" aria-labelledby="d-title" hidden' in text
    assert '<a class="d-open" id="d-open" href="/report/_info">Open as a page</a>' in text
    menu = text[text.index('id="title-menu"') : text.index('id="old"')]
    assert "m-details" in menu and "d-view" not in text and "d-title" not in menu
    assert "d-back" not in text
    assert "Page info" not in text
    data = facts(body)
    assert data["view"] == {
        "version": None,
        "path": "/docs/?x=1",
        "src": f"https://report.{APEX}/docs/?x=1",
        "raw": f"https://report.{APEX}/docs/?x=1&raw=1",
        "signin": f"{ORIGIN}/oauth2/start?rd={ORIGIN.replace(':', '%3A').replace('/', '%2F')}"
        "%2Freport%2Fdocs%2F%3Fx%3D1",
    }
    page = data["page"]
    assert (page["current"], page["role"], page["viewer"]) == (v2, "viewer", "tester@onticlabs.io")
    assert [v["version"] for v in page["versions"]] == [v2, v1]  # newest first

    # The description cannot end the script element; in HTML it is escaped.
    assert b"</script><b>" not in body
    assert "first \\u003c/script\\u003e\\u003cb\\u003ex" in text

    # An older version: its own frame address and the old-version marker.
    status, _, body = request(server, f"/report/_v/{v1}/")
    assert status == 200 and f'src="https://report.{APEX}/_v/{v1}/"'.encode() in body
    assert b'<a class="old" id="old" href="/report/" title=' in body
    assert facts(body)["view"]["version"] == v1
    assert request(server, f"/report/_v/{v1}/")[0] == 200
    status, headers, _ = request(server, f"/report/_v/{v1}?a=b")
    assert (status, headers["Location"]) == (301, f"/report/_v/{v1}/?a=b")
    assert request(server, "/report/_v/20990101T000000Z/")[0] == 404
    # Old /public/ links.
    status, headers, _ = request(server, "/public/report/img/dot.png?x=1", who=None)
    assert (status, headers["Location"]) == (301, "/report/img/dot.png?x=1")
    assert request(server, "/ghost/")[0] == 404


def test_assets(serve, two):
    server = serve()
    body = request(server, "/report/")[2].decode()
    for url in re.findall(r'"(/_bar/bar\.[0-9a-f]+\.(?:js|css))"', body):
        status, headers, data = request(server, url, who=None)
        assert status == 200 and data
        assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert len(re.findall(r"/_bar/bar\.", body)) == 2
    # Another hash (a shell from before a deploy) still works, but is not kept.
    status, headers, _ = request(server, "/_bar/bar.0123.js")
    assert (status, headers["Cache-Control"]) == (200, "no-cache")
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    # The bridge lives on content hosts, with the apex written in.
    framed = content(server, "report", "/", headers=FRAME)[2]
    bridge = re.search(rb'src="(/_bridge\.[0-9a-f]+\.js)"', framed).group(1).decode()
    status, headers, data = content(server, "report", bridge, who=None)
    assert status == 200 and f'var APEX = "{ORIGIN}";'.encode() in data
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert request(server, bridge)[0] == 404  # not on the apex
    assert content(server, "report", "/_bar/bar.0123.js")[0] == 404  # nor the bar on a page


def test_top_level_goes_to_the_shell(serve, two):
    server = serve()
    v1, _ = two
    status, headers, _ = content(server, "report", "/docs/?x=1", headers=DOCUMENT, who=None)
    assert (status, headers["Location"]) == (302, f"{ORIGIN}/report/docs/?x=1")
    status, headers, _ = content(server, "report", f"/_v/{v1}/", headers=DOCUMENT)
    assert (status, headers["Location"]) == (302, f"{ORIGIN}/report/_v/{v1}/")
    # ?raw=1 opens it without the bar, and links followed inside a raw page stay raw.
    status, _, body = content(server, "report", "/?raw=1", headers=DOCUMENT)
    assert status == 200 and TAG not in body
    inside = {"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "same-origin"}
    status, _, body = content(server, "report", "/docs/", headers=inside)
    assert (status, body) == (200, b"<p>docs</p>")
    # Not a browser (no Sec-Fetch-Dest): served as is.
    assert content(server, "report", "/")[0] == 200


def test_bridge_only_in_framed_html(serve, two):
    server = serve()
    v1, _ = two
    status, headers, body = content(server, "report", "/", headers=FRAME)
    assert status == 200
    assert body.startswith(b"<!doctype html><html><head>" + TAG)
    assert body.endswith(b"</script><title>t</title></head><p>2")
    assert headers["Content-Length"] == str(len(body))
    assert headers["Vary"] == "Sec-Fetch-Dest"
    framed_etag = headers["ETag"]
    _, raw_headers, raw = content(server, "report", "/")
    assert TAG not in raw and raw_headers["ETag"] != framed_etag
    assert framed_etag.startswith(raw_headers["ETag"][:-1] + "-b")
    status, _, body = content(
        server, "report", "/", headers={**FRAME, "If-None-Match": framed_etag}
    )
    assert (status, body) == (304, b"")
    # Not HTML, or not in a frame: unchanged.
    assert content(server, "report", "/style.css", headers=FRAME)[2] == b"h1 { color: red }"
    assert TAG not in content(server, "report", "/docs/", headers={"Sec-Fetch-Dest": "image"})[2]
    # Rewritten HTML is revalidated even at a version's own address.
    _, headers, body = content(server, "report", f"/_v/{v1}/", headers=FRAME)
    assert body.startswith(TAG) and headers["Cache-Control"] == "private, no-cache"


def test_bridge_tag_names_the_served_version(serve, store, two):
    server = serve()
    v1, v2 = two
    _, current, body = content(server, "report", "/", headers=FRAME)
    assert f'.js" data-version="{v2}"></script>'.encode() in body
    _, old, body = content(server, "report", f"/_v/{v1}/", headers=FRAME)
    assert f'data-version="{v1}"'.encode() in body
    # The same address serving another version: another tag, and another ETag.
    store.set_current("report", v1)
    _, again, body = content(server, "report", "/", headers=FRAME)
    assert f'data-version="{v1}"'.encode() in body
    assert again["ETag"] != current["ETag"] and again["ETag"].startswith(f'"{v1}-')
    status, _, _ = content(
        server, "report", "/", headers={**FRAME, "If-None-Match": current["ETag"]}
    )
    assert status == 200  # not the copy cached while v2 was current


def test_inject_places():
    tag = b"<s>"
    assert inject(b"<!DOCTYPE html><HTML lang=en><Head class=x><p>", tag) == (
        b"<!DOCTYPE html><HTML lang=en><Head class=x><s><p>"
    )
    assert inject(b"<!doctype html><html><header>", tag) == b"<!doctype html><html><s><header>"
    assert inject(b"<!doctype html><p>hi", tag) == b"<!doctype html><s><p>hi"
    assert inject(b"<p>hi", tag) == b"<s><p>hi"
    assert inject(b"\xef\xbb\xbf<p>hi", tag) == b"\xef\xbb\xbf<s><p>hi"


def test_tls_ask(serve, two):
    server = serve()
    local = "127.0.0.1:8790"
    assert request(server, f"/_tls-ask?domain=report.{APEX}", host=local)[::2] == (200, b"ok\n")
    for domain in (f"ghost.{APEX}", APEX, "report.evil.test", f"Bad_Name.{APEX}", ""):
        assert request(server, f"/_tls-ask?domain={domain}", host=local)[0] == 404, domain
    # Not through the public hosts.
    assert request(server, f"/_tls-ask?domain=report.{APEX}")[0] == 404
    assert content(server, "report", f"/_tls-ask?domain=report.{APEX}")[0] == 404


def test_api_facts(serve, store, two):
    server = serve()
    v1, v2 = two
    status, headers, body = request(server, "/_api/pages/report", who=OWNER)
    assert (status, headers["Cache-Control"]) == (200, "no-store")
    page = json.loads(body)
    assert page["name"] == "report" and page["role"] == "owner"
    assert (page["visibility"], page["current"], page["published_by"]) == ("ontic", v2, OWNER)
    assert page["content_origin"] == f"https://report.{APEX}"
    assert page["versions"][1] == {
        "version": v1,
        "published_at": "2026-10-07T15:30:00+00:00",
        "description": "first </script><b>x</b>",
        "pillar": "other",
        "tags": [],
        "title": "",
        "milestone": "",
        "format": "html",
        "published_by": OWNER,
    }
    assert json.loads(request(server, "/_api/pages/report")[2])["role"] == "viewer"
    status, _, body = request(server, "/_api/pages/report", who=None)
    assert (status, json.loads(body)) == (401, {"error": "sign in first"})
    assert request(server, "/_api/pages/report", who="someone@gmail.com")[0] == 403
    assert request(server, "/_api/pages/ghost")[0] == 404
    assert content(server, "report", "/_api/pages/report")[0] == 404  # never on a page's host
    # Signed out on a public page: no emails at all.
    store.set_visibility("report", "public")
    page = json.loads(request(server, "/_api/pages/report", who=None)[2])
    assert "@" not in json.dumps(page) and page["viewer"] == "" and page["role"] == "viewer"


WRITE = {
    "Content-Type": "application/json",
    "Origin": ORIGIN,
    "Sec-Fetch-Site": "same-origin",
}


def post(server, body, who=OWNER, headers=None, path="/_api/pages/report/visibility", **kw):
    data = json.dumps(body).encode() if not isinstance(body, bytes) else body
    status, _, out = request(
        server, path, who=who, headers={**WRITE, **(headers or {})}, method="POST", body=data, **kw
    )
    return status, json.loads(out)


def test_visibility_api(serve, store, two):
    server = serve(ttl=60)
    assert request(server, "/_api/pages/report")[0] == 200  # remembered for 60 s
    status, page = post(server, {"visibility": "private"})
    assert (status, page["visibility"], page["role"]) == (200, "private", "owner")
    assert store.visibility("report") == "private"
    # The gateway forgets what it remembered: the change shows at once.
    assert request(server, "/_api/pages/report")[0] == 403
    assert request(server, "/_api/pages/report", who=OWNER)[0] == 200

    assert post(server, {"visibility": "public"}, who="tester@onticlabs.io")[0] == 403
    assert post(server, {"visibility": "public"}, who=None)[0] == 401
    store.set_visibility("report", "ontic")
    server2 = serve()
    status, body = post(server2, {"visibility": "public"}, who="tester@onticlabs.io")
    assert (status, body) == (403, {"error": "only the owner can change this"})
    assert post(server2, {"visibility": "team"})[0] == 400
    assert post(server2, b"not json")[0] == 400
    assert post(server2, ["public"])[0] == 400
    assert post(server2, {"visibility": "public"}, path="/_api/pages/ghost/visibility")[0] == 404
    assert store.visibility("report") == "ontic"


def test_writes_only_from_the_bar(serve, store, two):
    server = serve()
    page_origin = f"https://report.{APEX}"
    refused = [
        {"Sec-Fetch-Site": "same-site"},  # a page's script on report.pages.test
        {"Origin": page_origin},
        {"Origin": ORIGIN + ".evil.test"},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
    ]
    for headers in refused:
        assert post(server, {"visibility": "public"}, headers=headers)[0] in (403, 415), headers
    # Missing headers count as wrong ones.
    status, _, _ = request(
        server, "/_api/pages/report/visibility", who=OWNER, method="POST",
        headers={"Content-Type": "application/json"}, body=b'{"visibility": "public"}',
    )  # fmt: skip
    assert status == 403
    # Never on a page's own host, whatever the headers.
    status, _ = post(server, {"visibility": "public"}, host=f"report.{APEX}")
    assert status == 405
    assert post(server, {"visibility": "public"}, path="/report/")[0] == 404
    assert post(server, b"x" * 5000)[0] == 413
    assert store.visibility("report") == "ontic"


def test_writes_are_rate_limited(serve, two):
    server = serve()
    for i in range(20):
        assert post(server, {"visibility": ["public", "ontic"][i % 2]})[0] == 200
    status, body = post(server, {"visibility": "public"})
    assert status == 429 and "wait" in body["error"]
    assert post(server, {"visibility": "public"}, who="tester@onticlabs.io")[0] == 403  # own limit
