"""Browser regressions: uv run --with playwright pytest -q tests/test_direct_files_browser.py.

Uses installed Chromium (CHROMIUM_PATH overrides), real gateway, and a local private bucket.
No production credentials or network services. The bucket models method/path/expiry and CORS,
not real B2 signatures: production CORS and media acceptance remain deployment checks.
"""

import base64
import os
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from direct_bucket import DirectBucket, fetch
from helpers import cookie_identity, stop

from ontic_pages.cache import DirectLinks
from ontic_pages.gateway import Site, make_server
from ontic_pages.publish import publish

playwright = pytest.importorskip("playwright.sync_api", reason="run with uv run --with playwright")
CHROMIUM = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(not CHROMIUM, reason="needs Chromium (or CHROMIUM_PATH)")
OWNER = "owner@onticlabs.io"
T1 = datetime(2026, 10, 14, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 14, 2, tzinfo=UTC)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def browser_site(store, tmp_path, monkeypatch):
    root = tmp_path / "page"
    root.mkdir()
    files = {
        "index.html": (
            '<!doctype html><link rel="stylesheet" href="style.css">'
            '<div id="bg">test</div><script src="plain.js"></script>'
        ),
        "style.css": (
            '@import "nested.css"; #bg {background-image:url("dot.png");width:50px;height:50px}'
        ),
        "nested.css": "#bg {color:rgb(1,2,3)}",
        "plain.js": "window.PLAIN = 1;",
        "leaf.js": "export const answer = 42;",
        "parent.js": 'import {answer} from "./leaf.js"; export {answer};',
        "meta.js": 'export const asset = new URL("./data.json", import.meta.url).href;',
        "worker.js": 'postMessage("worker-ok");',
        "data.json": '{"version":1}',
        "dot.png": PNG,
        "seek.webm": (Path(__file__).parent / "fixtures/direct-files/seek.webm").read_bytes(),
    }
    for path, data in files.items():
        (root / path).write_bytes(data if isinstance(data, bytes) else data.encode())
    publish(store, root, "demo", published_by=OWNER, now=T1)
    bucket = DirectBucket(store, monkeypatch)
    monkeypatch.setattr(
        "ontic_pages.gateway.DirectLinks",
        lambda store: DirectLinks(store, clock=lambda: bucket.now),
    )
    # Bind once to obtain the final content origin before building the handler.
    server = make_server(store, "127.0.0.1", 0)
    port = server.server_port
    server.server_close()
    server = make_server(
        store,
        "127.0.0.1",
        port,
        site=Site(f"http://pages.test:{port}"),
        identity=cookie_identity,
        ttl=0,
        direct_files=True,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://demo.pages.test:{port}"
    bucket.origins.add(origin)
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=CHROMIUM,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--no-proxy-server",
                "--host-resolver-rules=MAP pages.test 127.0.0.1, MAP *.pages.test 127.0.0.1",
                # Production is HTTPS: enable Fetch Metadata on these local HTTP fixtures.
                f"--unsafely-treat-insecure-origin-as-secure={origin},http://pages.test:{port}",
            ],
        )
        context = browser.new_context()
        context.add_cookies([{"name": "who", "value": OWNER, "url": origin}])
        page = context.new_page()
        try:
            yield page, origin, bucket, root
        finally:
            browser.close()
            stop(server)
            bucket.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_dependencies_workers_and_canvas(browser_site, store, legacy):
    page, origin, bucket, _ = browser_site
    if legacy:
        store.set_labels("demo", {})
    page.goto(origin + "/?raw=1", wait_until="networkidle")
    result = page.evaluate("""async () => {
        const out = {plain:window.PLAIN, css:getComputedStyle(document.querySelector('#bg')).color};
        out.module = (await import('./parent.js')).answer;
        out.metaAsset = await (await fetch((await import('./meta.js')).asset)).json();
        out.worker = await new Promise((resolve,reject) => {
            const w=new Worker('./worker.js');
            w.onmessage=e=>{resolve(e.data);w.terminate();};
            w.onerror=e=>{reject(new Error(e.message));w.terminate();};
            setTimeout(()=>{reject(new Error('worker timeout'));w.terminate();},3000);
        });
        for (const cors of [false,true]) {
            out['canvas_'+cors] = await new Promise((resolve,reject) => {
                const i=new Image(); if(cors)i.crossOrigin='anonymous';
                i.onload=()=>{try{const c=document.createElement('canvas');c.width=c.height=1;
                    const x=c.getContext('2d');x.drawImage(i,0,0);
                    x.getImageData(0,0,1,1);resolve('ok');
                }catch(e){reject(e);}};
                i.onerror=()=>reject(new Error('image failed'));
                i.src='./dot.png?cors='+cors;
            });
        }
        out.head=(await fetch('./data.json',{method:'HEAD'})).status;
        return out;
    }""")
    assert result == {
        "plain": 1,
        "css": "rgb(1, 2, 3)",
        "module": 42,
        "metaAsset": {"version": 1},
        "worker": "worker-ok",
        "canvas_false": "ok",
        "canvas_true": "ok",
        "head": 200,
    }
    if legacy:
        assert bucket.requests == []
    else:
        assert any(
            path.endswith("/data.json") and code == 200 for _, path, code, _ in bucket.requests
        )
        assert any(
            path.endswith("/dot.png") and code == 200 for _, path, code, _ in bucket.requests
        )
        assert all(code == 200 for _, _, code, _ in bucket.requests)
        assert not any(path.endswith((".css", ".js")) for _, path, _, _ in bucket.requests)


def test_publish_reload_expiry_and_logout(browser_site, store):
    page, origin, bucket, root = browser_site
    page.goto(origin + "/?raw=1")
    read = (
        "async () => {const r=await fetch('./data.json');return {url:r.url,data:await r.json()};}"
    )
    first = page.evaluate(read)
    assert first["data"] == {"version": 1}
    (root / "data.json").write_text('{"version":2}')
    publish(store, root, "demo", published_by=OWNER, now=T2)
    assert page.evaluate(read)["data"] == {"version": 2}
    bucket.now = 3601
    assert fetch(first["url"])[0] == 403
    page.reload()
    renewed = page.evaluate(read)
    assert renewed["data"] == {"version": 2} and renewed["url"] != first["url"]
    page.context.clear_cookies()
    assert page.evaluate("async () => (await fetch('./data.json')).status") == 401
    # Previously issued URLs remain bearer grants, even after logout.
    assert fetch(renewed["url"])[0] == 200


def test_video_range_seek_and_reload_after_expiry(browser_site):
    page, origin, bucket, _ = browser_site
    page.goto(origin + "/?raw=1")
    play = """async () => {
        const v=document.createElement('video');v.crossOrigin='anonymous';v.muted=true;
        v.preload='metadata';document.body.append(v);
        await new Promise((resolve,reject)=>{v.onloadedmetadata=resolve;
            v.onerror=()=>reject(new Error('media failed'));
            v.src='./seek.webm';setTimeout(()=>reject(new Error('media timeout')),5000);});
        await new Promise((resolve,reject)=>{v.onseeked=resolve;
            v.onerror=()=>reject(new Error('seek failed'));
            v.currentTime=2;setTimeout(()=>reject(new Error('seek timeout')),5000);});
        return {duration:v.duration,time:v.currentTime};
    }"""
    for now in [0, 3601]:
        bucket.now = now
        page.reload()
        result = page.evaluate(play)
        assert result["duration"] == pytest.approx(3, abs=0.1)
        assert result["time"] == pytest.approx(2, abs=0.1)
    assert any(
        code == 206 and ranged
        for _, path, code, ranged in bucket.requests
        if path.endswith(".webm")
    )
    assert all(code == 206 for _, path, code, _ in bucket.requests if path.endswith(".webm"))


def test_markdown_media_uses_direct_delivery_without_publisher_options(browser_site, store):
    page, origin, bucket, root = browser_site
    source = root / "note.md"
    source.write_text("# Media\n\n![plot](dot.png)\n\n![clip](seek.webm)")
    publish(store, source, "demo", published_by=OWNER, now=T2)
    page.goto(origin + "/?raw=1", wait_until="networkidle")
    page.wait_for_function("document.querySelector('img').naturalWidth > 0")
    page.wait_for_function("document.querySelector('video').readyState >= 1")
    assert any(path.endswith("/dot.png") and code == 200 for _, path, code, _ in bucket.requests)
    assert any(path.endswith("/seek.webm") and code == 206 for _, path, code, _ in bucket.requests)


def test_json_custom_headers_follow_redirect_preflight(browser_site):
    page, origin, bucket, _ = browser_site
    page.goto(origin + "/?raw=1")
    # Match jQuery's same-origin XHR header without a third-party CDN dependency.
    read = """async () => {
        const xhr = await new Promise((resolve, reject) => {
            const x = new XMLHttpRequest(); x.open('GET', './data.json');
            x.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            x.onload = () => resolve(JSON.parse(x.responseText)); x.onerror = reject; x.send();
        });
        const data = await (await fetch('./data.json', {
            headers: {'Content-Type': 'application/json'}
        })).json();
        return [xhr, data];
    }"""
    assert page.evaluate(read) == [{"version": 1}, {"version": 1}]
    assert any(path.endswith("/data.json") for _, path, _, _ in bucket.requests)


@pytest.mark.parametrize("visibility", ["public", "ontic", "private"])
@pytest.mark.parametrize("legacy", [True, False])
def test_composer_preview_existing_and_dropped_media(browser_site, store, visibility, legacy):
    page, origin, bucket, root = browser_site
    source = root / "note.md"
    source.write_text("# Preview\n\n![existing](dot.png)\n\n![clip](seek.webm)")
    publish(store, source, "demo", published_by=OWNER, now=T2)
    store.set_visibility("demo", visibility)
    if legacy:
        store.set_labels("demo", {})
    apex = origin.replace("demo.pages.test", "pages.test")
    page.context.add_cookies([{"name": "who", "value": OWNER, "url": apex}])
    page.goto(apex)
    page.locator("tr.r").first.hover()
    page.get_by_role("button", name="More", exact=True).first.click()
    page.get_by_role("button", name="Edit the update", exact=True).click()
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Attach", exact=True).click()
    chooser.value.set_files([str(root / "dot.png"), str(root / "seek.webm")])
    page.get_by_role("tab", name="Preview", exact=True).click()
    frame = page.frame_locator('iframe[title="Preview"]')
    for index in range(2):
        frame.locator("img").nth(index).scroll_into_view_if_needed()
        frame.locator("img").nth(index).evaluate("i => i.decode()")
    page.wait_for_function("""() => {
        const d=document.querySelector('iframe[title="Preview"]').contentDocument;
        const videos=[...d.querySelectorAll('video')];
        return videos.length===2 && videos.every(v=>v.readyState>=1);
    }""")
    assert frame.locator("[crossorigin]").count() == 0
    assert frame.locator('img[src^="blob:"]').count() == 1
    assert frame.locator('video[src^="blob:"]').count() == 1
    # The real composer previews new files from blob URLs and existing media via the
    # authenticated page host. Neither needs a bucket download or apex download CORS.
    assert bucket.requests == []
