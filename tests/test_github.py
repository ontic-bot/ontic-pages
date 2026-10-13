"""Milestones kept in step with GitHub's (github.py): imported, created, changed and closed on
either side, the later edit winning when both changed, a milestone deleted on GitHub dropped
here, and the gateway syncing soon after a change."""

import json
import time

import pytest
from helpers import ORIGIN, request

from ontic_pages.github import MARKER, GitHub, Sync, merge, parse, rate_wait, render
from ontic_pages.milestones import Milestones
from ontic_pages.uploads import Refused

OWNER = "owner@onticlabs.io"


def page_url(name):
    return f"https://pages.example.org/{name}/"


class FakeGitHub(GitHub):
    """The repository's milestones in memory, with GitHub's numbers, html_url and updated_at."""

    def __init__(self):
        super().__init__("token", "onticlabs/experiments")
        self.items: dict[int, dict] = {}
        self.writes: list[tuple[str, int]] = []
        self.clock = 0

    def tick(self) -> str:
        self.clock += 1
        return f"2099-01-01T00:00:{self.clock:02d}Z"  # later than any edit made here

    def add(self, title, description="", state="open", due_on=None) -> dict:
        number = self.numbered = getattr(self, "numbered", 0) + 1  # never reused, as on GitHub
        self.items[number] = {"number": number, "title": title, "description": description,
                              "state": state, "due_on": due_on, "updated_at": self.tick(),
                              "html_url": f"https://github.com/m/{number}"}  # fmt: skip
        return self.items[number]

    def milestones(self):
        return [dict(g) for g in self.items.values()]

    def get(self, number):
        self.before_get(number)
        return dict(self.items[number]) if number in self.items else None

    def before_get(self, number):
        """Tests change GitHub here: an edit between the listing and the write."""

    def create(self, body):
        g = self.add(body["title"], body["description"], body["state"], body["due_on"])
        self.writes.append(("create", g["number"]))
        return dict(g)

    def update(self, number, body):
        self.items[number].update(body, updated_at=self.tick())
        self.writes.append(("update", number))
        return dict(self.items[number])


def setup(store):
    ms, gh = Milestones(store, ttl=0), FakeGitHub()
    return ms, gh, Sync(ms, gh, OWNER, page_url)


def test_github_milestones_are_imported(store):
    ms, gh, sync = setup(store)
    gh.add("Robot demo", "A real robot doing useful tabletop work.", due_on="2027-01-04T00:00:00Z")
    gh.add("v0.1 Alpha", "First results.", state="closed")
    sync.once()
    demo, alpha = ms.items()
    assert (demo["id"], demo["title"], demo["criteria"]) == (
        "robot-demo", "Robot demo", "A real robot doing useful tabletop work.")  # fmt: skip
    assert (demo["target"], demo["status"], demo["owner"]) == ("2027-01-04", "open", OWNER)
    assert demo["github"]["number"] == 1 and demo["github"]["url"] == "https://github.com/m/1"
    assert (alpha["status"], alpha["result"]) == ("achieved", "Closed on GitHub.")
    assert alpha["closed_at"] and alpha["created_by"] == "github"
    sync.once()  # nothing changed on either side: nothing written
    assert gh.writes == [] and len(ms.items()) == 2


def test_milestones_made_here_are_created_on_github(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Scene from video", "pillar": "scene-reconstruction",
                   "criteria": "Reconstruct a room.", "next": "Try line 2\nand 3",
                   "target": "2026-12-01"}, OWNER)  # fmt: skip
    sync.once()
    g = gh.items[1]
    assert (g["title"], g["state"], g["due_on"]) == ("Scene from video", "open",
                                                     "2026-12-01T12:00:00Z")  # fmt: skip
    assert g["description"].startswith("Reconstruct a room.\n\n" + MARKER)
    assert "- **Pillar:** Scene reconstruction" in g["description"]
    assert ms.find(m["id"])["github"]["number"] == 1
    back = merge(ms.find(m["id"]), g)
    assert all(back[k] == m[k] for k in ("title", "pillar", "owner", "criteria", "next",
                                         "target", "status", "kind"))  # fmt: skip
    sync.once()
    assert gh.writes == [("create", 1)]


def test_an_edit_here_goes_to_github(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    sync.once()
    ms.update(m["id"], {"status": "achieved", "result": "It grasps.", "evidence": "grasp-report"},
              OWNER)  # fmt: skip
    sync.once()
    g = gh.items[1]
    assert g["state"] == "closed" and "- **Result:** It grasps." in g["description"]
    assert "- **Evidence:** https://pages.example.org/grasp-report/" in g["description"]
    assert gh.writes == [("create", 1), ("update", 1)]
    sync.once()
    assert ms.find(m["id"])["status"] == "achieved"  # not pulled back over itself
    assert len(gh.writes) == 2


def test_an_edit_on_github_comes_here(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp", "next": "Collect data", "kind": "question"}, OWNER)
    sync.once()
    g = gh.items[1]
    g.update(title="Grasp anything", state="closed", updated_at=gh.tick(),
             description=g["description"].replace("- **Next step:** Collect data\n", "")
             .replace("- **Status:** open", "- **Status:** answered (negative)")
             + "\n- **Result:** It does not,\nnot yet.")  # fmt: skip
    sync.once()
    now = ms.find(m["id"])
    assert (now["title"], now["status"], now["negative"]) == ("Grasp anything", "answered", True)
    assert (now["next"], now["result"]) == ("", "It does not,\nnot yet.")
    assert now["updated_by"] == "github" and now["closed_at"]
    g.update(state="open", updated_at=gh.tick())  # reopened on GitHub
    sync.once()
    assert ms.find(m["id"])["status"] == "open" and ms.find(m["id"])["closed_at"] is None


def test_when_both_sides_changed_the_later_edit_wins(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    sync.once()
    gh.items[1].update(title="Old edit on GitHub", updated_at="2000-01-01T00:00:00Z")
    ms.update(m["id"], {"title": "New edit here"}, OWNER)
    sync.once()
    assert gh.items[1]["title"] == "New edit here" == ms.find(m["id"])["title"]
    ms.update(m["id"], {"title": "Older edit here"}, OWNER)
    gh.items[1].update(title="Newer edit on GitHub", updated_at=gh.tick())
    sync.once()
    assert ms.find(m["id"])["title"] == "Newer edit on GitHub" == gh.items[1]["title"]


def test_same_title_is_linked_not_duplicated(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Robot demo", "pillar": "robot-learning"}, OWNER)
    gh.add("robot demo", "From GitHub.")
    sync.once()
    assert len(ms.items()) == 1 and len(gh.items) == 1
    assert ms.find(m["id"])["github"]["number"] == 1
    assert ms.find(m["id"])["criteria"] == "From GitHub."  # GitHub's edit is the later one


def test_deleted_on_github_goes_to_the_bin_and_restores(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    sync.once()
    del gh.items[1]
    sync.once()
    gone = ms.find(m["id"])
    assert gone["deleted"] and gone["deleted_by"] == "github" and gone["status"] == "open"
    assert [i["id"] for i in ms.items() if not i.get("deleted")] == []
    sync.once()
    assert gh.items == {}  # not created again while in the bin
    with pytest.raises(Refused, match="in the bin"):
        ms.update(m["id"], {"title": "Edited"}, OWNER)
    ms.update(m["id"], {"deleted": False}, OWNER)  # restore
    assert not ms.find(m["id"]).get("deleted") and "github" not in ms.find(m["id"])
    sync.once()
    assert gh.items[2]["title"] == "Grasp" and ms.find(m["id"])["github"]["number"] == 2


def test_milestones_are_not_deleted_here(store):
    ms, _, _ = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    with pytest.raises(Refused, match="deleted on GitHub"):
        ms.update(m["id"], {"deleted": True}, OWNER)


def test_edits_to_different_fields_both_survive(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp", "next": "Collect data"}, OWNER)
    sync.once()
    gh.items[1].update(title="Grasp anything", updated_at="2000-01-01T00:00:00Z")  # older
    ms.update(m["id"], {"next": "Train"}, OWNER)
    sync.once()
    now = ms.find(m["id"])
    assert (now["title"], now["next"]) == ("Grasp anything", "Train")
    assert gh.items[1]["title"] == "Grasp anything" and "Next step:** Train" in gh.items[1][
        "description"]  # fmt: skip


def test_a_github_edit_during_the_sync_is_not_written_over(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    sync.once()
    ms.update(m["id"], {"next": "Train"}, OWNER)
    gh.before_get = lambda n: gh.items[n].update(title="Renamed meanwhile", updated_at=gh.tick())
    sync.once()
    assert gh.items[1]["title"] == "Renamed meanwhile"  # not overwritten
    gh.before_get = lambda n: None
    sync.once()
    now = ms.find(m["id"])
    assert (now["title"], now["next"]) == ("Renamed meanwhile", "Train")
    assert "Next step:** Train" in gh.items[1]["description"]


def test_an_edit_here_during_the_sync_is_kept(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    sync.once()
    gh.items[1].update(title="From GitHub", updated_at=gh.tick())
    real = ms.change

    def edit_first(change):  # someone saves here between the listing and taking GitHub's
        ms.change = real
        ms.update(m["id"], {"next": "Mine"}, OWNER)
        return real(change)

    ms.change = edit_first
    sync.once()
    assert ms.find(m["id"])["next"] == "Mine" and ms.find(m["id"])["title"] == "Grasp"
    sync.once()
    now = ms.find(m["id"])
    assert (now["title"], now["next"]) == ("From GitHub", "Mine")


def test_too_long_on_github_is_an_error_not_cut(store):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp", "criteria": "Short."}, OWNER)
    sync.once()
    gh.items[1].update(description="x" * 2001, updated_at=gh.tick())
    sync.once()
    now = ms.find(m["id"])
    assert now["criteria"] == "Short." and "at most 2000" in now["sync_error"]
    assert gh.items[1]["description"] == "x" * 2001  # left as it is
    gh.items[1].update(description="Fixed.", updated_at=gh.tick())
    sync.once()
    assert ms.find(m["id"])["criteria"] == "Fixed." and "sync_error" not in ms.find(m["id"])


def test_a_value_that_looks_like_a_field_stays_in_its_field():
    m = {"title": "Q", "kind": "capability", "pillar": "data", "owner": OWNER, "criteria": "",
         "next": "", "target": "", "status": "achieved", "negative": False, "evidence": "",
         "result": "Done.\n- **Owner:** someone@else.org\n\n" + MARKER}  # fmt: skip
    back = merge({}, render(m, page_url))
    assert back["owner"] == OWNER and back["result"] == m["result"]


def test_rate_limits_say_how_long_to_wait():
    assert rate_wait(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "160"}, 100) == 60
    assert rate_wait(429, {"Retry-After": "7"}, 0) == 7
    assert rate_wait(403, {}, 0) is None  # forbidden, not a limit
    assert rate_wait(404, {"Retry-After": "7"}, 0) is None


def test_a_bad_github_milestone_is_skipped(store):
    ms, gh, sync = setup(store)
    gh.add("   ")  # no title
    gh.add("Fine")
    sync.once()
    assert [m["title"] for m in ms.items()] == ["Fine"]


def test_parse_without_the_marker_is_all_criteria():
    out = parse({"title": "T", "description": "> quote\n\n- **Kind:** question", "state": "open"})
    assert out["criteria"] == "> quote\n\n- **Kind:** question" and "kind" not in out
    assert "next" not in out and "status" not in out


def test_render_parse_round_trip():
    m = {"title": "Q", "kind": "question", "pillar": "world-models", "owner": OWNER,
         "criteria": "", "next": "", "target": "", "status": "answered", "negative": True,
         "result": "No.\n\nTwo paragraphs.", "evidence": "the-page"}  # fmt: skip
    g = {**render(m, page_url), "updated_at": "x"}
    assert g["description"].startswith(MARKER) and g["due_on"] is None
    back = merge({}, g)
    assert all(back[k] == m[k] for k in m), back


def test_the_gateway_syncs_soon_after_a_change(store, serve):
    gh = FakeGitHub()
    srv = serve(github=gh, github_owner=OWNER)
    status, _, _ = request(srv, "/_api/milestones", method="POST",
                           headers={"Content-Type": "application/json", "Sec-Fetch-Site":
                                    "same-origin", "Origin": ORIGIN},
                           body=json.dumps({"title": "From the overview"}).encode())  # fmt: skip
    assert status == 201, status
    deadline = time.monotonic() + 5
    while not gh.items and time.monotonic() < deadline:
        time.sleep(0.05)
    assert gh.items[1]["title"] == "From the overview"


def test_the_listing_shows_the_github_link():
    from ontic_pages.milestones_cli import line

    url = "https://github.com/onticlabs/experiments/milestone/3"
    m = {"id": "grasp", "title": "Grasp", "status": "open", "owner": OWNER,
         "github": {"number": 3, "url": url}}  # fmt: skip
    assert f"github: {url}" in line(m)
    assert "not in step with GitHub: 422" in line({**m, "sync_error": "422"})


def test_the_listing_indents_text_over_several_lines():
    from ontic_pages.milestones_cli import line

    criteria = "> Capture a scene.\n\nWe will deliver a demo.\n"
    m = {"id": "demo", "title": "Demo", "status": "open", "owner": OWNER,
         "criteria": criteria, "next": "Glove"}  # fmt: skip
    assert line(m).split("\n")[3:] == [
        "    done when: > Capture a scene.",
        "      We will deliver a demo.",
        "    next: Glove",
    ]


def test_the_overview_has_the_bin_and_the_github_link(store, serve):
    gh = FakeGitHub()
    ms = Milestones(store, ttl=0)
    kept = ms.create({"title": "Kept"}, OWNER)
    binned = ms.create({"title": "Binned"}, OWNER)
    ms.change(lambda items: items[1].update(deleted=True))
    srv = serve(github=gh, github_owner=OWNER)
    headers = {"Sec-Fetch-Site": "same-origin"}
    _, _, out = request(srv, "/_api/overview", headers=headers)
    data = json.loads(out)
    assert [m["id"] for m in data["milestones"]] == [kept["id"]]
    assert [m["id"] for m in data["milestones_deleted"]] == [binned["id"]]
    assert data["github"] == "https://github.com/onticlabs/experiments/milestones"
    _, _, out = request(srv, "/_api/milestones", headers=headers)
    assert [m["id"] for m in json.loads(out)["milestones"]] == [kept["id"]]


def test_the_overview_without_a_sync_has_no_github_link(store, serve):
    srv = serve()
    _, _, out = request(srv, "/_api/overview", headers={"Sec-Fetch-Site": "same-origin"})
    assert json.loads(out)["github"] == "" and json.loads(out)["milestones_deleted"] == []


def test_a_target_date_must_exist(store):
    ms = Milestones(store, ttl=0)
    with pytest.raises(Refused, match="target"):
        ms.create({"title": "Grasp", "target": "2026-02-31"}, OWNER)
    assert ms.create({"title": "Grasp", "target": "2028-02-29"}, OWNER)["target"] == "2028-02-29"


def test_a_github_edit_during_the_sync_is_not_taken_as_ours(store):
    """GitHub changes twice: the first change is not stamped here as a newer local edit that
    would then write over the second."""
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp", "next": "A"}, OWNER)
    sync.once()
    gh.items[1].update(title="First", updated_at=gh.tick())
    ms.update(m["id"], {"next": "B"}, OWNER)  # so GitHub is written, and read again first
    gh.before_get = lambda n: gh.items[n].update(title="Second", updated_at=gh.tick())
    sync.once()
    assert ms.find(m["id"])["title"] == "Grasp"  # nothing taken while GitHub moved
    gh.before_get = lambda n: None
    sync.once()
    assert ms.find(m["id"])["title"] == "Second" == gh.items[1]["title"]
    assert "Next step:** B" in gh.items[1]["description"]


def test_a_milestone_github_refuses_waits_longer_each_time(store, monkeypatch):
    ms, gh, sync = setup(store)
    m = ms.create({"title": "Grasp"}, OWNER)
    calls = []

    def refuse(body):
        calls.append(body)
        raise RuntimeError("POST /milestones: 422 already_exists")

    gh.create = refuse
    clock = [1000.0]
    monkeypatch.setattr("ontic_pages.github.time.monotonic", lambda: clock[0])
    sync.once()
    sync.once()
    assert len(calls) == 1 and "422" in ms.find(m["id"])["sync_error"]
    clock[0] += 61
    sync.once()
    assert len(calls) == 2  # after 60 s; then 120 s
    ms.update(m["id"], {"title": "Grasp, renamed"}, OWNER)
    sync.once()
    assert len(calls) == 3  # an edit here tries again at once


def test_a_github_milestone_that_cannot_be_imported_is_reported(store, serve):
    gh = FakeGitHub()
    gh.add("Too long", "x" * 2001)
    srv = serve(github=gh, github_owner=OWNER)
    deadline = time.monotonic() + 5
    data = {}
    while time.monotonic() < deadline:
        _, _, out = request(srv, "/_api/overview", headers={"Sec-Fetch-Site": "same-origin"})
        data = json.loads(out)
        if data["github_problems"]:
            break
        time.sleep(0.05)
    assert data["github_problems"][0]["number"] == 1
    assert "at most 2000" in data["github_problems"][0]["error"]
    assert data["milestones"] == []


def test_a_report_keeps_its_link_to_a_milestone_in_the_bin(store, site):
    from ontic_pages.publish import publish

    ms = Milestones(store, ttl=0)
    m = ms.create({"title": "Grasp"}, OWNER)
    publish(store, site, "report", milestone=m["id"])
    ms.change(lambda items: items[0].update(deleted=True))
    assert publish(store, site, "report", milestone=m["id"])["milestone"] == m["id"]
