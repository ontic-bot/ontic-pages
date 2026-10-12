"""The research overview: milestones, saved views, reports linked to milestones, Markdown
updates (from the command line and from the composer, with media), relinking, and the overview's
data and page."""

import json
import urllib.request
from datetime import UTC, datetime

import pytest
from helpers import SECRET, request

from ontic_pages.cli import main
from ontic_pages.publish import publish
from ontic_pages.tokens import sign

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
OWNER = "owner@onticlabs.io"
OTHER = "other@onticlabs.io"
OUTSIDER = "someone@gmail.com"


def host(srv) -> str:
    return srv.url.removeprefix("http://")


def call(srv, path, email=None, body=None, who=None, headers=None):
    """(status, JSON): with a token for `email`, or as the browser on the apex for `who`."""
    sent = {"Content-Type": "application/json"}
    if email:
        sent["Authorization"] = f"Bearer {sign(SECRET, email)}"
    elif who and body is not None:
        sent.update({"Origin": srv.url, "Sec-Fetch-Site": "same-origin"})
    sent.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    status, _, out = request(
        srv, path, host=host(srv), who=who, headers=sent,
        method="POST" if body is not None else "GET", body=data,
    )  # fmt: skip
    return status, json.loads(out) if out.startswith(b"{") else out


def put(upload: dict, data: bytes) -> int:
    req = urllib.request.Request(upload["url"], data=data, method="PUT", headers=upload["headers"])
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def add_milestone(srv, **fields):
    body = {"title": "Predict particle dynamics", "pillar": "world-models", **fields}
    status, answer = call(srv, "/_api/milestones", OWNER, body)
    assert status == 201, answer
    return answer["milestone"]


# --- milestones ------------------------------------------------------------------------------


def test_milestones_lifecycle(local, store):
    srv = local()
    m = add_milestone(srv, criteria="1 s rollout under 5 mm error", next="train on arctic")
    assert m["id"] == "predict-particle-dynamics" and m["status"] == "open"
    assert (m["owner"], m["created_by"], m["closed_at"]) == (OWNER, OWNER, None)
    assert add_milestone(srv)["id"] == "predict-particle-dynamics-2"  # ids stay unique
    q = add_milestone(srv, title="Does depth help?", kind="question", pillar="data")
    # Closing needs a result; negative only marks an answered question.
    path = f"/_api/milestones/{q['id']}"
    assert call(srv, path, OTHER, {"status": "answered"})[0] == 400
    close = {"status": "answered", "result": "No, flat.", "negative": True,
             "evidence": "depth-eval"}  # fmt: skip
    status, answer = call(srv, path, OTHER, close)
    done = answer["milestone"]
    assert status == 200 and done["negative"] and done["closed_at"] and done["updated_by"] == OTHER
    assert done["evidence"] == "depth-eval"
    reopened = call(srv, path, OWNER, {"status": "open"})[1]["milestone"]
    assert reopened["closed_at"] is None and reopened["negative"] is False
    status, answer = call(srv, "/_api/milestones", OWNER)
    assert status == 200 and [m["id"] for m in answer["milestones"]][-1] == q["id"]
    saved = json.loads(store.read_text("_milestones.json"))
    assert len(saved["milestones"]) == 3
    assert "_milestones.json" not in store.names()  # never a page


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "x", "kind": "wish"},
        {"title": "x", "pillar": "infra"},
        {"title": "x", "status": "achieved"},  # no result
        {"title": "x", "target": "next week"},
        {"title": "x", "owner": "nobody"},
        {"title": "x" * 141},
        {"title": "x", "negative": "yes"},
        {"title": "x", "evidence": "Bad_Name"},
    ],
)
def test_milestone_checks(local, body):
    assert call(local(), "/_api/milestones", OWNER, body)[0] == 400


def test_milestones_are_for_the_team(local):
    srv = local()
    add_milestone(srv)
    assert call(srv, "/_api/milestones", OUTSIDER, {"title": "x"})[0] == 403
    assert call(srv, "/_api/milestones", OUTSIDER)[0] == 403
    assert call(srv, "/_api/milestones/nope", OWNER, {"title": "x"})[0] == 404
    # From a page's own scripts (same-site, not the apex): refused.
    cross = {"Origin": "https://report.example", "Sec-Fetch-Site": "same-site"}
    status, _ = call(srv, "/_api/milestones", body={"title": "x"}, who=OWNER, headers=cross)
    assert status == 403
    assert call(srv, "/_api/milestones", body={"title": "From the browser"}, who=OWNER)[0] == 201


def test_milestones_on_the_command_line(local, gateway_env, capsys):
    srv = local()
    gateway_env(srv, OWNER)
    main(["milestone", "add", "Grasp from imagination", "--pillar", "robot-learning",
          "--criteria", "10 grasps in a row", "--next", "collect data"])  # fmt: skip
    assert "added grasp-from-imagination" in capsys.readouterr().out
    main(["milestones"])
    out = capsys.readouterr().out
    assert "grasp-from-imagination" in out and "next: collect data" in out
    assert "Robot learning" in out and "done when: 10 grasps in a row" in out
    main(["milestone", "update", "grasp-from-imagination", "--status", "achieved",
          "--result", "8 of 10", "--evidence", "grasps"])  # fmt: skip
    assert "result: 8 of 10" in capsys.readouterr().out
    main(["milestones"])
    assert "no open milestones (1 closed" in capsys.readouterr().out
    main(["milestones", "--all", "--json"])
    assert json.loads(capsys.readouterr().out)["milestones"][0]["status"] == "achieved"
    with pytest.raises(SystemExit, match="result"):
        main(["milestone", "update", "grasp-from-imagination", "--result", ""])


# --- reports point at milestones -------------------------------------------------------------


def test_publish_with_a_milestone(local, gateway_env, store, site):
    srv = local()
    gateway_env(srv, OWNER)
    m = add_milestone(srv)
    with pytest.raises(SystemExit, match="no milestone 'nope'"):
        main(["publish", str(site), "--name", "report", "--milestone", "nope"])
    (site / "index.html").write_text("<title>Rollouts &amp; errors</title><h1>x</h1>")
    main(["publish", str(site), "--name", "report", "--milestone", m["id"]])
    page = store.meta("report", store.current("report"))
    # The milestone brings its pillar; the title comes from the page.
    assert (page["milestone"], page["pillar"]) == (m["id"], "world-models")
    assert (page["title"], page["format"]) == ("Rollouts & errors", "html")
    main(["publish", str(site), "--name", "report", "--pillar", "data"])
    page = store.meta("report", store.current("report"))
    assert (page["milestone"], page["pillar"]) == (m["id"], "data")  # kept, pillar as given
    main(["publish", str(site), "--name", "report", "--no-milestone", "--title", "Own title"])
    page = store.meta("report", store.current("report"))
    assert (page["milestone"], page["title"]) == ("", "Own title")


def test_direct_publish_with_a_milestone(store, site):
    from ontic_pages.milestones import Milestones

    m = Milestones(store).create({"title": "Scene from video", "pillar": "scene-reconstruction"},
                                 OWNER)  # fmt: skip
    page = publish(store, site, "report", milestone=m["id"], now=T1)
    assert (page["milestone"], page["pillar"]) == ("scene-from-video", "scene-reconstruction")
    with pytest.raises(ValueError, match="no milestone"):
        publish(store, site, "report", milestone="gone")


# --- Markdown updates --------------------------------------------------------------------------

UPDATE = """# Depth helps, a bit

We tried **depth** as input.

![rollout](img/roll.png)

![clip](img/clip.mp4)

<script>alert(1)</script>
"""


def test_markdown_from_the_command_line(local, gateway_env, store, tmp_path, s3):
    srv = local()
    gateway_env(srv, OWNER)
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "roll.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "img" / "clip.mp4").write_bytes(b"\x00\x00\x00 ftyp fake")
    (tmp_path / "notes.md").write_text(UPDATE)
    main(["publish", str(tmp_path / "notes.md"), "--name", "depth", "--tag", "eval"])
    version = store.current("depth")
    page = store.meta("depth", version)
    assert (page["format"], page["title"], page["files"]) == ("markdown", "Depth helps, a bit", 4)
    index = store.read_text(f"depth/{version}/index.html")
    assert "<h1>Depth helps, a bit</h1>" in index and '<img src="img/roll.png"' in index
    assert "<video" in index and 'src="img/clip.mp4"' in index
    assert "<script>" not in index and "&lt;script&gt;" in index
    assert "owner@onticlabs.io, 07.10.2026" not in index and OWNER in index  # the byline
    assert store.read_text(f"depth/{version}/update.md") == UPDATE
    assert s3.objects[("test-bucket", f"depth/{version}/img/clip.mp4")][1] == "video/mp4"
    # Direct, the same page.
    direct = publish(store, tmp_path / "notes.md", "depth-direct", published_by=OWNER, now=T1)
    assert (direct["format"], direct["files"]) == ("markdown", 4)
    assert "07.10.2026 17:30" in store.read_text(f"depth-direct/{direct['version']}/index.html")


def test_markdown_refuses_reserved_names(tmp_path, store):
    (tmp_path / "update.md").write_text("# x")
    (tmp_path / "a.md").write_text("[the source](update.md)")
    with pytest.raises(SystemExit, match="written from the Markdown"):
        publish(store, tmp_path / "a.md", "x")


def test_composer_publishes_and_edits_with_media(local, store, s3):
    srv = local()
    m = add_milestone(srv)
    path = "/_api/pages/first-try/versions"
    body = {"markdown": "# First try\n\n![plot](media/plot.png)\n", "milestone": m["id"],
            "tags": ["Eval"], "files": [{"path": "media/plot.png", "size": 4}]}  # fmt: skip
    status, start = call(srv, path, body=body, who=OWNER)
    assert status == 200 and [u["path"] for u in start["uploads"]] == ["media/plot.png"]
    assert put(start["uploads"][0], b"\x89PNG") == 200
    status, done = call(srv, f"{path}/{start['version']}/commit", body={}, who=OWNER)
    page = done["page"]
    assert status == 200 and (page["title"], page["milestone"], page["pillar"]) == (
        "First try",
        m["id"],
        "world-models",
    )
    assert page["tags"] == ["eval"] and page["files"] == 3
    # Edit: new text, no new files; the earlier media come along (keep).
    body = {"markdown": "# First try, again\n\n![plot](media/plot.png)\n", "keep": True}
    status, start = call(srv, path, body=body, who=OWNER)
    assert status == 200 and start["uploads"] == []
    status, done = call(srv, f"{path}/{start['version']}/commit", body={}, who=OWNER)
    new = done["page"]["version"]
    assert done["page"]["files"] == 3 and done["page"]["milestone"] == m["id"]
    assert s3.objects[("test-bucket", f"first-try/{new}/media/plot.png")][0] == b"\x89PNG"
    assert "First try, again" in store.read_text(f"first-try/{new}/index.html")
    # The source, for the next edit; a page that is not Markdown has none.
    status, source = call(srv, "/_api/pages/first-try/source", who=OWNER)
    assert status == 200 and source["markdown"].startswith("# First try, again")
    publish(store, store_site(store), "plain", published_by=OWNER, now=T1)
    assert call(srv, "/_api/pages/plain/source", who=OWNER)[0] == 404
    # In-place text editing is for HTML pages; an update is edited here.
    edit = {"version": new, "path": "/", "changes": [{"before": "again", "after": "x"}]}
    status, answer = call(srv, "/_api/pages/first-try/edits", body=edit, who=OWNER)
    assert status == 409 and "Markdown" in answer["error"]


def store_site(store):
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp())
    (root / "index.html").write_text("<title>Plain</title><p>plain</p>")
    return root


@pytest.mark.parametrize(
    "body",
    [
        {"markdown": 3},
        {"markdown": "x" * (48 * 1024 + 1)},
        {"markdown": "# x", "files": [{"path": "index.html", "size": 1}]},
        {"markdown": "# x", "files": [{"path": "update.md", "size": 1}]},
        {"keep": True, "files": [{"path": "a.html", "size": 1}]},
        {"markdown": "# x", "milestone": "Bad Id"},
        {"markdown": "# x", "milestone": "missing"},
    ],
)
def test_composer_checks(local, body):
    srv = local()
    assert call(srv, "/_api/pages/fresh/versions", body=body, who=OWNER)[0] == 400


def test_preview(local):
    srv = local()
    status, answer = call(srv, "/_api/preview", body={"markdown": "**hi** <b>x</b>", "title": "T"},
                          who=OWNER)  # fmt: skip
    assert status == 200 and "<strong>hi</strong>" in answer["html"]
    assert "&lt;b&gt;" in answer["html"] and "<title>T</title>" in answer["html"]
    assert call(srv, "/_api/preview", body={"markdown": "x"}, who=None)[0] in (401, 403)


# --- relinking a report ------------------------------------------------------------------------


def test_labels_edited_in_the_table(local, store, site):
    """Anyone on the team changes pillar, tags, milestone or title in place: labels.json, no
    new version; the next publish keeps them."""
    srv = local()
    m = add_milestone(srv)
    first = publish(store, site, "report", "about", published_by=OWNER, now=T1)["version"]
    path = "/_api/pages/report/labels"
    assert call(srv, path, body={"tags": ["x"]}, who=OUTSIDER)[0] in (403, 404)
    assert call(srv, path, body={"milestone": "missing"}, who=OTHER)[0] == 400
    assert call(srv, path, body={"pillar": "infra"}, who=OTHER)[0] == 400
    body = {"milestone": m["id"], "tags": ["New", "eval"], "pillar": "robot-learning"}
    status, answer = call(srv, path, body=body, who=OTHER)
    row = answer["report"]
    assert status == 200 and store.current("report") == first  # no new version
    assert (row["milestone"], row["tags"], row["pillar"]) == (
        m["id"],
        ["eval", "new"],
        "robot-learning",
    )
    assert store.labels("report")["updated_by"] == OTHER
    call(srv, path, body={"title": "  A better   title "}, who=OTHER)
    data = call(srv, "/_api/overview", who=OWNER)[1]
    assert data["reports"][0]["title"] == "A better title"
    assert data["reports"][0]["tags"] == ["eval", "new"]  # unchanged fields stay
    assert (
        call(srv, "/_api/pages/report", who=OWNER)[1]["versions"][0]["pillar"] == "robot-learning"
    )
    # A publish without labels keeps them, and starts labels.json again from its page.json.
    page = publish(store, site, "report", published_by=OWNER)
    assert (page["pillar"], page["tags"], page["milestone"]) == ("robot-learning", ["eval", "new"],
                                                                  m["id"])  # fmt: skip
    assert "updated_by" not in store.labels("report")


def test_delete_and_restore(local, store, site, gateway_env, capsys):
    srv = local()
    publish(store, site, "report", published_by=OWNER, now=T1)
    publish(store, site, "other", published_by=OWNER, now=T1)
    assert call(srv, "/_api/pages/report/delete", body={}, who=OTHER)[0] == 403
    status, answer = call(srv, "/_api/pages/report/delete", body={}, who=OWNER)
    assert (status, answer) == (200, {"name": "report", "deleted": True})
    assert store.current("report") and store.exists(
        "report/" + store.current("report") + "/index.html"
    )
    data = call(srv, "/_api/overview", who=OWNER)[1]
    assert [r["name"] for r in data["reports"]] == ["other"]
    assert [r["name"] for r in data["deleted"]] == ["report"]  # the owner's, to restore
    assert call(srv, "/_api/overview", who=OTHER)[1]["deleted"] == []
    assert request(srv, "/report/", host=host(srv), who=OWNER)[0] == 404
    assert call(srv, "/_api/pages/report", who=OWNER)[0] == 404
    # Someone else cannot take the name; the owner publishing again brings it back.
    start = {"files": [{"path": "index.html", "size": 1}]}
    assert call(srv, "/_api/pages/report/versions", OTHER, start)[0] == 403
    status, answer = call(srv, "/_api/pages/report/delete", body={"deleted": False}, who=OWNER)
    assert status == 200 and not store.labels("report").get("deleted")
    assert request(srv, "/report/", host=host(srv), who=OWNER)[0] == 200
    # The command line.
    gateway_env(srv, OWNER)
    main(["delete", "other"])
    assert "deleted other" in capsys.readouterr().out and store.labels("other")["deleted"]
    main(["list"])
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["report"]
    main(["restore", "other"])
    assert "restored other" in capsys.readouterr().out
    main(["label", "other", "--pillar", "data", "--tag", "probe"])
    assert "other: data, milestone none, tags probe" in capsys.readouterr().out


# --- saved views -------------------------------------------------------------------------------

CONFIG = {"layout": "board", "group": "pillar", "sort": {"field": "title", "dir": "asc"},
          "fields": ["title", "author"], "filters": {"milestone": ["-"], "since": 30}}  # fmt: skip


def test_views(local):
    srv = local()
    status, answer = call(srv, "/_api/views", body={"name": "Exploratory board",
                                                    "config": CONFIG}, who=OWNER)  # fmt: skip
    view = answer["view"]
    assert status == 201 and view["config"]["filters"] == {"milestone": ["-"], "since": 30}
    assert view["config"]["layout"] == "board" and view["created_by"] == OWNER
    path = f"/_api/views/{view['id']}"
    status, answer = call(srv, path, body={"name": "Renamed"}, who=OTHER)
    assert status == 200 and answer["view"]["name"] == "Renamed"
    assert answer["view"]["config"] == view["config"]
    assert call(srv, path + "/delete", body={}, who=OTHER)[0] == 403
    assert call(srv, path + "/delete", body={}, who=OWNER)[0] == 200
    assert call(srv, path, body={"name": "x"}, who=OWNER)[0] == 404


def test_views_per_table(local):
    """A view belongs to the reports or the milestones table, with that table's properties."""
    srv = local()
    config = {
        "database": "milestones", "group": "status", "sort": {"field": "target"},
        "fields": ["title", "status", "reports"], "filters": {"status": ["open"]},
    }  # fmt: skip
    status, answer = call(srv, "/_api/views", body={"name": "Open by status", "config": config},
                          who=OWNER)  # fmt: skip
    saved = answer["view"]["config"]
    assert status == 201 and saved["database"] == "milestones"
    assert (saved["group"], saved["sort"], saved["filters"]) == (
        "status",
        {"field": "target", "dir": "desc"},
        {"status": ["open"]},
    )
    default = call(srv, "/_api/views", body={"name": "r", "config": {}}, who=OWNER)[1]["view"]
    assert default["config"]["database"] == "reports"
    assert default["config"]["sort"]["field"] == "published"
    # A property of the other table is refused.
    wrong = {"database": "milestones", "fields": ["published"]}
    assert call(srv, "/_api/views", body={"name": "x", "config": wrong}, who=OWNER)[0] == 400
    wrong = {"database": "reports", "group": "status"}
    assert call(srv, "/_api/views", body={"name": "x", "config": wrong}, who=OWNER)[0] == 400
    wrong = {"database": "people"}
    assert call(srv, "/_api/views", body={"name": "x", "config": wrong}, who=OWNER)[0] == 400


@pytest.mark.parametrize(
    "config",
    [
        {"layout": "gallery"},
        {"sort": {"field": "size"}},
        {"group": "colour"},
        {"fields": ["title", "secret"]},
        {"filters": {"pillar": "data"}},
        {"filters": {"since": -1}},
        "table",
    ],
)
def test_view_checks(local, config):
    srv = local()
    assert call(srv, "/_api/views", body={"name": "x", "config": config}, who=OWNER)[0] == 400


# --- the overview ------------------------------------------------------------------------------


def test_overview_data(local, store, site):
    srv = local()
    m = add_milestone(srv)
    publish(store, site, "older", published_by=OWNER, now=T1, milestone=m["id"])
    publish(store, site, "mine", "private one", visibility="private", published_by=OWNER)
    publish(store, site, "theirs", visibility="private", published_by=OTHER)
    call(srv, "/_api/views", body={"name": "v", "config": {}}, who=OWNER)
    status, data = call(srv, "/_api/overview", who=OWNER)
    assert status == 200 and data["team"] and data["viewer"] == OWNER
    assert [r["name"] for r in data["reports"]] == ["mine", "older"]  # newest first, visible
    older = data["reports"][1]
    assert (older["milestone"], older["pillar"], older["author"]) == (
        m["id"],
        "world-models",
        OWNER,
    )
    assert [x["id"] for x in data["milestones"]] == [m["id"]] and len(data["views"]) == 1
    assert data["pillars"][0] == {"id": "data", "label": "Data"}
    assert data["content_origin"].replace("__name__", "x").startswith("http://x.")
    # Outside the team: only the pages they may open, no milestones or views.
    store.set_visibility("older", "public")
    status, data = call(srv, "/_api/overview", who=OUTSIDER)
    assert status == 200 and [r["name"] for r in data["reports"]] == ["older"]
    assert (data["team"], data["milestones"], data["views"]) == (False, [], [])
    assert call(srv, "/_api/overview")[0] == 401
    cross = {"Sec-Fetch-Site": "same-site"}
    assert call(srv, "/_api/overview", who=OWNER, headers=cross)[0] == 403


def test_overview_page(local, store, site, uploads):
    srv = local()
    publish(store, site, "report", "first", published_by=OWNER, now=T1)
    status, headers, body = request(srv, "/", host=host(srv), who=OWNER)
    text = body.decode()
    assert status == 200 and "<title>Ontic research</title>" in text
    assert '<script src="/_bar/overview.' in text and '<noscript><div class="plain">' in text
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    bucket = f"http://127.0.0.1:{uploads.server_port}"
    assert f"connect-src 'self' {bucket}" in csp  # the browser uploads to the bucket
    asset = text.split('<script src="')[1].split('"')[0]
    status, headers, body = request(srv, asset, host=host(srv))
    assert status == 200 and b"/_api/overview" in body


def test_bar_names_the_milestone(local, store, site):
    srv = local()
    m = add_milestone(srv)
    publish(store, site, "report", published_by=OWNER, milestone=m["id"], now=T1)
    status, facts = call(srv, "/_api/pages/report", who=OWNER)
    assert status == 200 and facts["versions"][0]["milestone"] == m["id"]
    assert facts["milestones"] == {m["id"]: {"title": m["title"], "status": "open"}}
    store.set_visibility("report", "public")
    assert call(srv, "/_api/pages/report", who=OUTSIDER)[1]["milestones"] == {}
    _, _, body = request(srv, "/report/", host=host(srv), who=OWNER)
    assert b'id="m-milestone"' in body
