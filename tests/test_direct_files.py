"""Direct delivery is per page, with browser fallbacks and fresh access checks."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from direct_bucket import DirectBucket, fetch
from helpers import FRAME, TEAM, request
from helpers import content as content_at_test_host
from test_api import OTHER, OWNER, browser_post, call

from ontic_pages.cache import DirectLinks
from ontic_pages.cli import main
from ontic_pages.config import load_config
from ontic_pages.edits import save
from ontic_pages.publish import publish
from ontic_pages.uploads import Uploads

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
CORS = {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


def content(server, name, path, **kwargs):
    if hasattr(server, "url"):
        return request(server, path, host=f"{name}.{urlsplit(server.url).netloc}", **kwargs)
    return content_at_test_host(server, name, path, **kwargs)


@pytest.fixture
def bucket(store, monkeypatch):
    bucket = DirectBucket(store, monkeypatch)
    monkeypatch.setattr(
        "ontic_pages.gateway.DirectLinks",
        lambda store: DirectLinks(store, clock=lambda: bucket.now),
    )
    yield bucket
    bucket.close()


def test_eligible_files_redirect_and_html_stays(serve, store, site, bucket):
    server = serve(direct_files=True)
    publish(store, site, "report", now=T1)
    for path in ("data.json", "img/dot.png"):
        status, headers, body = content(server, "report", f"/{path}", headers=CORS)
        assert (status, body) == (302, b"")
        assert headers["Cache-Control"] == "private, no-store"
        assert fetch(headers["Location"])[::2] == (200, (site / path).read_bytes())
    assert content(server, "report", "/")[::2] == (200, b"<h1>version one</h1>")
    assert content(server, "report", "/docs/")[2] == b"<p>docs</p>"
    assert b"_bridge." in content(server, "report", "/", headers=FRAME)[2]


@pytest.mark.parametrize(
    "path",
    [
        "style.css",
        "app.js",
        "module.mjs",
        "scene.gltf",
        "a.svg",
        "a.xhtml",
        "list.m3u",
        "list.m3u8",
    ],
)
def test_dependency_bearing_files_stay_on_gateway(serve, store, site, path):
    (site / path).write_text("content")
    publish(store, site, "report", now=T1)
    assert content(serve(direct_files=True), "report", f"/{path}", headers=CORS)[::2] == (
        200,
        b"content",
    )


@pytest.mark.parametrize(
    "mode,dest,expected",
    [
        ("cors", "image", 302),
        ("no-cors", "image", 200),
        ("same-origin", "empty", 200),
        ("cors", "worker", 200),
        ("cors", "sharedworker", 200),
        ("cors", "script", 200),
        ("navigate", "document", 200),
        ("cors", "iframe", 200),
    ],
)
def test_browser_request_context(serve, store, site, mode, dest, expected):
    publish(store, site, "report", now=T1)
    status, headers, _ = content(
        serve(direct_files=True),
        "report",
        "/img/dot.png?raw=1",
        headers={
            "Sec-Fetch-Mode": mode,
            "Sec-Fetch-Dest": dest,
        },
    )
    assert status == expected
    if expected == 200:
        assert headers["Vary"] == "Sec-Fetch-Dest, Sec-Fetch-Mode"


def test_head_stays_on_gateway(serve, store, site, bucket):
    publish(store, site, "report", now=T1)
    server = serve(direct_files=True)
    url = content(server, "report", "/data.json", headers=CORS)[1]["Location"]
    assert fetch(url, method="HEAD")[0] == 403  # the bucket enforces method-bound grants
    status, headers, body = content(server, "report", "/data.json", method="HEAD", headers=CORS)
    assert (status, body) == (200, b"")
    assert int(headers["Content-Length"]) == (site / "data.json").stat().st_size
    assert "Location" not in headers


def test_same_url_is_reused_without_reading_file(serve, store, site, s3, bucket):
    publish(store, site, "report", now=T1)
    server = serve(direct_files=True)
    first = content(server, "report", "/data.json")[1]["Location"]
    s3.calls.clear()
    assert content(server, "report", "/data.json")[1]["Location"] == first
    assert "generate_presigned_url" not in s3.calls
    reads = s3.calls.count("get_object")
    s3.calls.clear()
    assert content(server, "report", "/img/dot.png")[0] == 302
    assert "list_objects_v2" not in s3.calls
    assert s3.calls.count("get_object") == reads


def test_expiry_and_renewal_through_gateway(serve, store, site, bucket):
    publish(store, site, "report", now=T1)
    server = serve(direct_files=True)
    first = content(server, "report", "/data.json")[1]["Location"]
    bucket.now = 2999
    assert content(server, "report", "/data.json")[1]["Location"] == first
    bucket.now = 3000
    second = content(server, "report", "/data.json")[1]["Location"]
    assert second != first and fetch(first)[0] == fetch(second)[0] == 200
    bucket.now = 3600
    assert fetch(first)[0] == 403 and fetch(second)[0] == 200
    bucket.now = 7200
    assert fetch(second)[0] == 403
    assert fetch(content(server, "report", "/data.json")[1]["Location"])[0] == 200


def test_links_promote_recent_hits_and_evict(monkeypatch):
    class Store:
        def presign_get(self, key, seconds):
            return key

    monkeypatch.setattr(DirectLinks, "MAX_LINKS", 2)
    links = DirectLinks(Store(), clock=lambda: 0.0)
    for key in "abac":
        links.url(key)
    assert list(links.links) == ["a", "c"]


def test_versions_missing_files_folders_and_ranges(serve, store, site, bucket):
    v1 = publish(store, site, "report", now=T1)["version"]
    (site / "data.json").write_text('{"a": 2}')
    publish(store, site, "report", now=T2)
    server = serve(direct_files=True)
    for path, expected in [(f"/_v/{v1}/data.json", b'{"a": 1}'), ("/data.json", b'{"a": 2}')]:
        status, headers, _ = content(server, "report", path)
        assert status == 302 and headers["Cache-Control"] == "private, no-store"
        assert fetch(headers["Location"])[2] == expected
    assert content(server, "report", "/missing.png")[0] == 404
    status, headers, _ = content(server, "report", "/docs?x=1")
    assert (status, headers["Location"]) == (301, "/docs/?x=1")
    for ranged, expected in [
        ("bytes=8-11", b"0123"),
        ("bytes=-4", b"6789"),
        ("bytes=14-", b"6789"),
    ]:
        status, headers, _ = content(server, "report", "/img/dot.png", headers={"Range": ranged})
        assert status == 302
        code, result, data = fetch(headers["Location"], headers={"Range": ranged})
        assert (code, data) == (206, expected)
        assert result["Content-Range"].endswith("/18")
    assert fetch(headers["Location"], headers={"Range": "bytes=99-"})[0] == 416


def test_access_changes_do_not_revoke_issued_links(local, store, site, bucket):
    publish(store, site, "report", now=T1, published_by=OWNER)
    server = local(direct_files=True)
    assert content(server, "report", "/data.json", who=None, headers=CORS)[0] == 401
    assert (
        content(server, "report", "/data.json", who="outsider@example.org", headers=CORS)[0] == 403
    )
    assert call(server, "/_api/pages/report/visibility", OWNER, {"visibility": "public"})[0] == 200
    assert content(server, "report", "/data.json", who=None, headers=CORS)[0] == 302
    assert call(server, "/_api/pages/report/visibility", OWNER, {"visibility": "ontic"})[0] == 200
    link = content(server, "report", "/data.json", who=OTHER, headers=CORS)[1]["Location"]
    assert call(server, "/_api/pages/report/visibility", OWNER, {"visibility": "private"})[0] == 200
    assert content(server, "report", "/data.json", who=OTHER, headers=CORS)[0] == 403
    assert content(server, "report", "/data.json", who=OWNER, headers=CORS)[0] == 302
    assert fetch(link)[0] == 200
    assert call(server, "/_api/pages/report/delete", OWNER, {"deleted": True})[0] == 200
    assert content(server, "report", "/data.json", who=OWNER, headers=CORS)[0] == 404
    assert fetch(link)[0] == 200
    bucket.now = 3600
    assert fetch(link)[0] == 403


@pytest.mark.parametrize("legacy", [False, True])
def test_delivery_survives_publish_edit_switch_and_restore(local, store, site, legacy):
    first = publish(store, site, "report", now=T1, published_by=OWNER)
    expected = "server" if legacy else "direct"
    if legacy:
        store.set_labels("report", {})  # metadata written before delivery settings existed
    server = local(direct_files=True)
    expected_status = 200 if legacy else 302
    assert content(server, "report", "/data.json")[0] == expected_status
    second = publish(store, site, "report", now=T2, published_by=OWNER)
    assert store.labels("report")["delivery"] == expected
    edited = save(
        store,
        "report",
        OWNER,
        {
            "version": second["version"],
            "path": "/",
            "changes": [{"before": "version one", "after": "version two"}],
        },
    )
    assert store.labels("report")["delivery"] == expected
    for action, body in [
        ("current", {"version": first["version"]}),
        ("labels", {"title": "New title"}),
        ("delete", {"deleted": True}),
        ("delete", {"deleted": False}),
        ("current", {"version": edited["version"]}),
    ]:
        assert call(server, f"/_api/pages/report/{action}", OWNER, body)[0] == 200
        assert store.labels("report")["delivery"] == expected
    assert content(server, "report", "/data.json")[0] == expected_status


def test_gateway_publish_defaults_only_new_names_to_direct(store, site):
    publish(store, site, "legacy", published_by=TEAM)
    store.set_labels("legacy", {})
    uploads = Uploads(store)
    for name, expected in [("fresh", "direct"), ("legacy", "server")]:
        up, _ = uploads.start(name, TEAM, {"markdown": "# New page", "files": []})
        uploads.commit(name, up.version, TEAM)
        assert store.labels(name)["delivery"] == expected


def test_owner_setting_and_cli(local, gateway_env, store, site, capsys):
    publish(store, site, "report", published_by=OWNER, now=T1)
    server = local(direct_files=True)
    path = "/_api/pages/report/delivery"
    assert call(server, path, OTHER, {"delivery": "server"})[0] == 403
    assert call(server, path, OWNER, {"delivery": "typo"})[0] == 400
    assert browser_post(server, path, {"delivery": "server"}, None)[0] == 401
    assert browser_post(server, path, {"delivery": "server"}, OWNER)[0] == 200
    assert content(server, "report", "/data.json")[0] == 200
    gateway_env(server, OWNER)
    main(["delivery", "report", "direct"])
    assert "report: direct delivery" in capsys.readouterr().out
    assert content(server, "report", "/data.json")[0] == 302
    main(["delivery", "report", "server"])
    publish(store, site, "report", now=T2, published_by=OWNER)
    assert store.labels("report")["delivery"] == "server"


def test_global_override(serve, store, site):
    publish(store, site, "report", now=T1)
    assert content(serve(direct_files=False), "report", "/data.json")[::2] == (200, b'{"a": 1}')


@pytest.mark.parametrize(
    "value,expected", [("1", True), ("true", True), ("yes", True), ("0", False)]
)
def test_config_reaches_server(tmp_path, monkeypatch, store, value, expected):
    config = load_config(tmp_path / "none", env={"ONTIC_PAGES_DIRECT_FILES": value})
    monkeypatch.setattr("ontic_pages.cli.open_store", lambda args: (store, config))
    received = []
    monkeypatch.setattr("ontic_pages.gateway.serve", lambda *args, **kw: received.append(kw))
    main(["gateway"])
    assert received[0]["direct_files"] is expected


def test_direct_delivery_is_enabled_by_default(tmp_path):
    assert load_config(tmp_path / "none", env={}).is_direct_files


def test_server_default_automatically_distinguishes_new_and_legacy(store, site):
    import threading

    from helpers import SITE, stop

    from ontic_pages.gateway import make_server

    publish(store, site, "fresh", now=T1)
    publish(store, site, "legacy", now=T1)
    store.set_labels("legacy", {})
    server = make_server(store, "127.0.0.1", 0, site=SITE, local_email=TEAM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert content(server, "fresh", "/data.json")[0] == 302
        assert content(server, "legacy", "/data.json")[0] == 200
    finally:
        stop(server)


def test_media_gets_full_hour_even_when_other_links_are_aged(serve, store, site, bucket):
    (site / "clip.mp4").write_bytes(b"video")
    (site / "sound.mp3").write_bytes(b"audio")
    publish(store, site, "report", now=T1)
    server = serve(direct_files=True)
    for path in ("clip.mp4", "sound.mp3"):
        bucket.now = 0
        first = content(server, "report", "/" + path, headers=CORS)[1]["Location"]
        bucket.now = 2999
        fresh = content(server, "report", "/" + path, headers=CORS)[1]["Location"]
        assert fresh != first
        bucket.now = 3601
        assert fetch(first)[0] == 403 and fetch(fresh)[0] == 200
        bucket.now = 6599
        assert fetch(fresh)[0] == 403
