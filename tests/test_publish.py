import json
from datetime import UTC, datetime

import pytest

from ontic_pages.cli import main
from ontic_pages.publish import publish
from ontic_pages.store import Store

T1 = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
T2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def keys(s3):
    return sorted(k for _, k in s3.objects)


def test_publish_folder(store, s3, site, tmp_path):
    page = publish(store, site, "report", "first try", {"model": "abc123"}, cwd=tmp_path, now=T1)
    assert page["version"] == "20261007T153000Z"
    assert page["published_at"] == "2026-10-07T15:30:00+00:00"
    assert page["meta"] == {"model": "abc123"}
    assert page["git"] is None
    assert page["published_by"]
    v = "report/20261007T153000Z/"
    assert keys(s3) == sorted(
        [
            v + f
            for f in (
                "app.js",
                "data.json",
                "docs/index.html",
                "img/dot.png",
                "index.html",
                "page.json",
                "style.css",
            )
        ]
        + ["report/current", "report/labels.json"]
    )
    assert store.current("report") == "20261007T153000Z"
    assert json.loads(s3.objects[("test-bucket", v + "page.json")][0]) == page
    assert s3.objects[("test-bucket", v + "style.css")][1] == "text/css; charset=utf-8"


def test_single_html_becomes_index(store, s3, tmp_path):
    f = tmp_path / "plot.html"
    f.write_text("<p>hi</p>")
    page = publish(store, f, "plot", now=T1)
    obj = store.get(store.key("plot", page["version"], "index.html"))
    assert obj["Body"].read() == b"<p>hi</p>"


def test_refuses_bad_input(store, tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(SystemExit, match="must be .html"):
        publish(store, tmp_path / "notes.txt", "notes")
    with pytest.raises(ValueError, match="bad page name"):
        publish(store, tmp_path / "notes.txt", "Bad Name")
    for name in ("under_score", "trailing-", "a..b", "oauth2"):  # not a host name, or reserved
        with pytest.raises(ValueError, match="bad page name"):
            publish(store, tmp_path / "notes.txt", name)
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "page.json").write_text("{}")
    with pytest.raises(SystemExit, match="reserved"):
        publish(store, tmp_path / "f", "f")
    (tmp_path / "g" / "_v").mkdir(parents=True)
    (tmp_path / "g" / "_v" / "x.html").write_text("x")
    with pytest.raises(SystemExit, match="_v folder is reserved"):
        publish(store, tmp_path / "g", "g")


def test_same_second_gets_next_version(store, site):
    a = publish(store, site, "report", now=T1)["version"]
    b = publish(store, site, "report", now=T1)["version"]
    assert (a, b) == ("20261007T153000Z", "20261007T153001Z")


def test_cli_publish_list_set_current(cli_env, site, capsys):
    main(
        [
            "publish",
            str(site),
            "--name",
            "report",
            "--description",
            "first",
            "--meta",
            "model=abc",
            "--meta",
            "data=s3://x",
            "--pillar",
            "data",
            "--tag",
            "Labels",
        ]
    )
    out = capsys.readouterr().out
    assert "https://pages.example.org/report/" in out
    store_keys = keys(cli_env)
    first = next(k for k in store_keys if k.endswith("page.json")).split("/")[1]

    (site / "index.html").write_text("<h1>version two</h1>")
    later = datetime(2030, 1, 1, tzinfo=UTC)
    store = Store(cli_env, "test-bucket")
    publish(store, site, "report", "second", now=later)
    publish(store, site, "other", "another page", now=later)

    main(["list"])
    lines = capsys.readouterr().out.splitlines()
    # The second version kept the pillar and tags; the new page is other. Berlin time.
    assert lines[0].split() == ["other", "ontic", "other", "01.01.2030", "01:00", "another", "page"]
    assert lines[1].split() == [
        "report", "ontic", "data", "01.01.2030", "01:00", "second", "[labels]",
    ]  # fmt: skip
    main(["list", "--pillar", "data"])
    assert capsys.readouterr().out.split()[0] == "report"
    main(["list", "--tag", "labels"])
    assert capsys.readouterr().out.split()[0] == "report"
    main(["list", "--pillar", "world-models"])
    assert capsys.readouterr().out.strip() == ""

    main(["list", "--name", "report"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "visibility: ontic"
    assert lines[1].startswith("* 20300101T000000Z  01.01.2030 01:00")
    assert lines[2].startswith(f"  {first}") and "first" in lines[2]

    main(["set-current", "report", first])
    assert "report now serves" in capsys.readouterr().out
    assert store.current("report") == first

    main(["list", "--name", "report"])
    assert capsys.readouterr().out.splitlines()[2].startswith(f"* {first}")

    main(["url", "report"])
    assert capsys.readouterr().out.strip() == "https://pages.example.org/report/"


def test_cli_set_current_unknown_version(cli_env, site):
    main(["publish", str(site), "--name", "report"])
    with pytest.raises(SystemExit, match="no version"):
        main(["set-current", "report", "20990101T000000Z"])
    with pytest.raises(SystemExit, match="key=value"):
        main(["publish", str(site), "--name", "report", "--meta", "nokey"])
