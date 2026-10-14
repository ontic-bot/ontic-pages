"""Direct files (ONTIC_PAGES_DIRECT_FILES): files other than HTML are a redirect to a presigned
bucket URL, after the same access check; HTML is still answered by the gateway."""

import http.client
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from helpers import FRAME, content

from ontic_pages.cache import DirectLinks
from ontic_pages.config import load_config
from ontic_pages.publish import publish

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def fetch(url, headers=None):
    """GET a presigned URL from the fake bucket (helpers.upload_server)."""
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.request("GET", f"{u.path}?{u.query}", headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_files_redirect_to_the_bucket_html_stays(serve, store, site, s3, uploads):
    server = serve(direct_files=True)
    version = publish(store, site, "report", now=T1)["version"]
    for path, body in {
        "/style.css": b"h1 { color: red }",
        "/app.js": b"console.log(1)",
        "/data.json": b'{"a": 1}',
        "/img/dot.png": b"\x89PNG\r\n\x1a\n0123456789",
    }.items():
        status, headers, data = content(server, "report", path)
        assert (status, data) == (302, b""), path
        url = headers["Location"]
        assert urlsplit(url).path == f"/test-bucket/report/{version}{path}", url
        assert parse_qs(urlsplit(url).query)["expires"] == ["3600"]
        assert headers["Cache-Control"] == "private, max-age=3540"
        assert fetch(url) == (200, body), path
    # HTML (and folders, which answer index.html) come from the gateway as before.
    status, headers, data = content(server, "report", "/")
    assert (status, data) == (200, b"<h1>version one</h1>")
    assert content(server, "report", "/docs/")[2] == b"<p>docs</p>"
    # In the shell's frame the bridge is still added.
    _, _, data = content(server, "report", "/", headers=FRAME)
    assert b"<h1>version one</h1>" in data and b"_bridge." in data


def test_same_url_is_handed_out_again(serve, store, site, s3, uploads):
    server = serve(direct_files=True)
    publish(store, site, "report", now=T1)
    first = content(server, "report", "/style.css")[1]["Location"]
    s3.calls.clear()
    assert content(server, "report", "/style.css")[1]["Location"] == first
    assert "generate_presigned_url" not in s3.calls
    # The version's file list, kept from the first request, says the file exists: no new
    # listing, and the file's bytes are never read here (only page state is, ttl 0 in tests).
    reads = s3.calls.count("get_object")
    s3.calls.clear()
    assert content(server, "report", "/app.js")[0] == 302
    assert s3.calls.count("list_objects_v2") == 0
    assert s3.calls.count("get_object") == reads and s3.calls.count("generate_presigned_url") == 1


def test_links_expire_and_are_renewed():
    class Store:
        n = 0

        def presign_get(self, key, seconds):
            self.n += 1
            return f"https://bucket/{key}?n={self.n}&s={seconds}"

    now = [0.0]
    links = DirectLinks(Store(), clock=lambda: now[0])
    assert links.url("a") == ("https://bucket/a?n=1&s=3600", 3600)
    now[0] = 2999
    assert links.url("a") == ("https://bucket/a?n=1&s=3600", 601)  # still >= 600 s left
    now[0] = 3000
    assert links.url("a") == ("https://bucket/a?n=2&s=3600", 3600)  # renewed before it runs out
    assert links.url("b")[0] == "https://bucket/b?n=3&s=3600"


def test_links_keep_at_most_max(monkeypatch):
    class Store:
        def presign_get(self, key, seconds):
            return key

    monkeypatch.setattr(DirectLinks, "MAX_LINKS", 2)
    links = DirectLinks(Store(), clock=lambda: 0.0)
    for key in "abc":
        links.url(key)
    assert list(links.links) == ["b", "c"]


def test_versions_missing_files_and_folders(serve, store, site, s3, uploads):
    server = serve(direct_files=True)
    v1 = publish(store, site, "report", now=T1)["version"]
    (site / "style.css").write_text("h1 { color: blue }")
    publish(store, site, "report", now=T2)
    status, headers, _ = content(server, "report", f"/_v/{v1}/style.css")
    assert (
        status == 302
        and urlsplit(headers["Location"]).path == f"/test-bucket/report/{v1}/style.css"
    )
    assert fetch(headers["Location"])[1] == b"h1 { color: red }"
    assert fetch(content(server, "report", "/style.css")[1]["Location"])[1] == b"h1 { color: blue }"
    # Missing files are a 404 here, never a URL into the bucket; folders still get their slash.
    assert content(server, "report", "/nope.png")[0] == 404
    status, headers, _ = content(server, "report", "/docs?x=1")
    assert (status, headers["Location"]) == (301, "/docs/?x=1")


def test_access_is_checked_before_the_redirect(serve, store, site, s3, uploads):
    server = serve(direct_files=True)
    publish(store, site, "report", now=T1)  # visibility ontic: the team only
    style = {"Sec-Fetch-Dest": "style"}
    assert content(server, "report", "/style.css", who=None, headers=style)[0] == 401
    assert (
        content(server, "report", "/style.css", who="someone@example.com", headers=style)[0] == 403
    )
    status, headers, _ = content(server, "report", "/style.css", who=None)  # a page load: sign in
    assert status == 302 and urlsplit(headers["Location"]).path == "/oauth2/start"
    store.set_visibility("report", "public")
    status, headers, _ = content(server, "report", "/style.css", who=None, headers=style)
    assert status == 302 and "/test-bucket/report/" in headers["Location"]


def test_range_requests_go_to_the_bucket(serve, store, site, s3, uploads):
    server = serve(direct_files=True)
    publish(store, site, "report", now=T1)
    content(server, "report", "/style.css")  # page state and the file list read once
    s3.calls.clear()
    assert content(server, "report", "/app.js")[0] == 302
    plain = s3.calls.count("get_object")  # page state only (ttl 0 in tests)
    s3.calls.clear()
    status, headers, _ = content(server, "report", "/img/dot.png", headers={"Range": "bytes=8-11"})
    assert status == 302 and s3.calls.count("get_object") == plain  # the file is not read here


def test_off_by_default(serve, store, site, s3, uploads):
    server = serve()
    publish(store, site, "report", now=T1)
    assert content(server, "report", "/style.css")[:3:2] == (200, b"h1 { color: red }")


def test_config_switch():
    assert load_config(env={"ONTIC_PAGES_DIRECT_FILES": "1"}).is_direct_files
    assert not load_config(env={}).is_direct_files
