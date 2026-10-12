from datetime import UTC, datetime

from helpers import APEX, content, request

from ontic_pages.cache import FileCache, PageCache
from ontic_pages.publish import publish
from ontic_pages.store import content_type

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def test_serves_current_and_switches(serve, store, site):
    server = serve()
    first = publish(store, site, "report", "first <b>bold</b>", now=T1)["version"]
    status, headers, body = content(server, "report", "/")
    assert (status, body) == (200, b"<h1>version one</h1>")
    assert headers["Content-Type"] == "text/html; charset=utf-8"

    (site / "index.html").write_text("<h1>version two</h1>")
    second = publish(store, site, "report", "second", now=T2)["version"]
    assert content(server, "report", "/")[2] == b"<h1>version two</h1>"

    store.set_current("report", first)
    assert content(server, "report", "/")[2] == b"<h1>version one</h1>"
    assert content(server, "report", "/index.html")[2] == b"<h1>version one</h1>"
    # Any version by its own address.
    assert content(server, "report", f"/_v/{second}/")[2] == b"<h1>version two</h1>"
    assert content(server, "report", f"/_v/{first}/style.css")[0] == 200


def test_content_types(serve, store, site):
    server = serve()
    publish(store, site, "report", now=T1)
    expected = {
        "/style.css": "text/css; charset=utf-8",
        "/app.js": "text/javascript; charset=utf-8",
        "/data.json": "application/json; charset=utf-8",
        "/img/dot.png": "image/png",
        "/docs/": "text/html; charset=utf-8",
    }
    for path, ctype in expected.items():
        status, headers, _ = content(server, "report", path)
        assert (status, headers["Content-Type"]) == (200, ctype), path
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Content-Security-Policy"] == f"frame-ancestors 'self' https://{APEX}"
    assert content_type("x/model.glb") == "model/gltf-binary"
    assert content_type("x/README") == "application/octet-stream"


def test_redirects_and_404s(serve, store, site):
    server = serve()
    version = publish(store, site, "report", now=T1)["version"]
    status, headers, _ = request(server, "/report?x=1")
    assert (status, headers["Location"]) == (301, "/report/?x=1")
    status, headers, _ = content(server, "report", "/docs?x=1")
    assert (status, headers["Location"]) == (301, "/docs/?x=1")
    status, headers, _ = content(server, "report", f"/_v/{version}/docs")
    assert (status, headers["Location"]) == (301, f"/_v/{version}/docs/")
    status, headers, _ = content(server, "report", f"/_v/{version}")
    assert (status, headers["Location"]) == (301, f"/_v/{version}/")
    assert content(server, "report", "/missing.png")[0] == 404
    assert content(server, "report", "/_v/20990101T000000Z/")[0] == 404
    assert content(server, "nope", "/")[0] == 404
    assert content(server, "report", "/.DS_Store")[0] == 404
    assert content(server, "report", "/../other/")[0] == 404
    assert content(server, "report", "/%2e%2e/x")[0] == 404
    assert request(server, "/nope/")[0] == 404
    assert request(server, "/Bad_Name/")[0] == 404
    assert request(server, "/", host="elsewhere.test")[0] == 404
    for host in (APEX, "report." + APEX, "127.0.0.1"):
        assert request(server, "/_health", host=host)[::2] == (200, b"ok\n")


def test_listing(serve, store, site):
    server = serve()
    assert b"No pages yet" in request(server, "/")[2]
    publish(store, site, "report", "first <b>bold</b>", now=T1, pillar="data", tags=["eval"])
    publish(store, site, "zeta", "another one", now=T2)
    status, headers, body = request(server, "/")
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    text = body.decode()
    assert '<a href="/report/"><span title="report">report</span></a>' in text
    assert "first &lt;b&gt;bold&lt;/b&gt;" in text
    assert "07.10.2026 17:30" in text and "another one" in text  # Berlin time
    assert '<a class="pillar" href="/?pillar=data">Data</a>' in text
    assert '<a class="tag" href="/?tag=eval">eval</a>' in text
    assert '<a href="/" aria-current="page">All <span class="n">2</span></a>' in text
    assert '<a href="/?pillar=data">Data <span class="n">1</span></a>' in text
    # The filters: a pillar, a tag, both.
    text = request(server, "/?pillar=data")[2].decode()
    assert "/report/" in text and "/zeta/" not in text
    assert '<a href="/?pillar=data" aria-current="page">Data' in text
    text = request(server, "/?pillar=other")[2].decode()
    assert "/report/" not in text and "/zeta/" in text
    text = request(server, "/?tag=eval")[2].decode()
    assert "/report/" in text and "/zeta/" not in text and "tag <b>eval</b>" in text
    assert "No pages here." in request(server, "/?pillar=robot-learning")[2].decode()
    assert "/zeta/" in request(server, "/?pillar=nonsense")[2].decode()  # ignored
    # Signed out, the listing is behind sign-in.
    status, headers, _ = request(server, "/", who=None)
    assert (status, headers["Location"]) == (
        302,
        f"https://{APEX}/oauth2/start?rd=https%3A%2F%2F{APEX}%2F",
    )


def test_range_and_head(serve, store, site):
    server = serve()
    publish(store, site, "report", now=T1)
    status, headers, body = content(
        server, "report", "/img/dot.png", headers={"Range": "bytes=8-11"}
    )
    assert (status, body) == (206, b"0123")
    assert headers["Content-Range"] == "bytes 8-11/18"
    assert content(server, "report", "/img/dot.png", headers={"Range": "bytes=99-"})[0] == 416
    status, headers, body = content(server, "report", "/style.css", method="HEAD")
    assert (status, body, headers["Content-Length"]) == (200, b"", "17")


def test_etag_304_and_cache_headers(serve, store, site):
    server = serve()
    v1 = publish(store, site, "report", now=T1)["version"]
    status, headers, _ = content(server, "report", "/style.css")
    etag = headers["ETag"]
    assert etag.startswith(f'"{v1}-') and etag.endswith('"')
    assert headers["Cache-Control"] == "private, no-cache"  # current address: revalidate
    status, headers, body = content(
        server, "report", "/style.css", headers={"If-None-Match": f'"x", W/{etag}'}
    )
    assert (status, body, headers["ETag"]) == (304, b"", etag)
    assert headers["Cache-Control"] == "private, no-cache"

    # A version's own address never changes.
    _, headers, _ = content(server, "report", f"/_v/{v1}/style.css")
    assert headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert headers["ETag"] == etag

    # A new current version: the old ETag no longer matches.
    (site / "style.css").write_text("h1 { color: blue }")
    publish(store, site, "report", now=T2)
    status, headers, body = content(server, "report", "/style.css", headers={"If-None-Match": etag})
    assert (status, body) == (200, b"h1 { color: blue }") and headers["ETag"] != etag

    store.set_visibility("report", "public")
    _, headers, _ = content(server, "report", f"/_v/{v1}/style.css", who=None)
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert content(server, "report", "/style.css", who=None)[1]["Cache-Control"] == "no-cache"


def test_files_are_kept_in_memory(serve, store, site, s3):
    server = serve(ttl=60, file_bytes=12)
    publish(store, site, "report", now=T1)
    s3.calls.clear()
    assert content(server, "report", "/data.json")[2] == b'{"a": 1}'  # 8 bytes: kept
    # current, visibility, page.json, labels.json, the file
    assert s3.calls == ["get_object"] * 5
    s3.calls.clear()
    for _ in range(3):
        assert content(server, "report", "/data.json")[2] == b'{"a": 1}'
    assert s3.calls == []
    # style.css (17 bytes) is over the per-file cap: streamed each time, but a 304 needs nothing.
    _, headers, body = content(server, "report", "/style.css")
    assert body == b"h1 { color: red }"
    s3.calls.clear()
    assert content(server, "report", "/style.css")[2] == b"h1 { color: red }"
    assert s3.calls == ["get_object"]
    s3.calls.clear()
    assert (
        content(server, "report", "/style.css", headers={"If-None-Match": headers["ETag"]})[0]
        == 304
    )
    assert s3.calls == []
    # Range requests go to the bucket every time.
    content(server, "report", "/data.json", headers={"Range": "bytes=0-1"})
    assert s3.calls == ["get_object"]


def test_file_cache_evicts_oldest(store, site, s3):
    publish(store, site, "report", now=T1)
    version = store.current("report")
    files = FileCache(store, max_bytes=30, max_file=20)
    for path in ("style.css", "app.js"):  # 17 + 14 bytes: over 30, style.css goes
        files.get("report", version, path)
    assert list(files.files) == [("report", version, "app.js")]
    assert files.total == 14
    assert files.get("report", version, "missing") is None


def test_current_is_cached(store, site, s3):
    publish(store, site, "report", now=T1)
    cache = PageCache(store, FileCache(store), ttl=60)
    s3.calls.clear()
    owner = store.meta("report", "20261007T153000Z")["published_by"]
    s3.calls.clear()
    assert cache.get("report") == cache.get("report") == ("20261007T153000Z", "ontic", owner)
    assert s3.calls == ["get_object"] * 3  # current, visibility and page.json, once
