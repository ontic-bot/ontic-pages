import hashlib
import os
import stat

import pytest

from ontic_pages import cli, skills
from ontic_pages.skills import CLI_MARKER, MARKER, REVISION

TEXT = skills.bundled()
WANTED = skills.installed_text(TEXT)
# The marker every ontic-pages wrote before skill revisions.
OLD_MARKER = (
    "<!-- managed by ontic-pages: updated on every run; delete this line to keep your own "
    "edits, ONTIC_AGENT_SKILLS=0 to stop -->"
)
# SKILL.md as of skills.REVISION. When the text changes, bump the revision with it.
SKILL_SHA256 = "2515168177f820e7a8dd5de4dbe5bc67f827d6f16c84b693f7ed6e06e0579ef8"
SKILL_REVISION = 8


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary home with ~/.claude and ~/.codex, the sync turned on."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("ONTIC_AGENT_SKILLS", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ONTIC_PAGES_URL", raising=False)
    return home


def skill_file(root):
    return root / "skills" / "ontic-pages" / "SKILL.md"


def run_url(capsys):
    """Run a command that syncs (url needs no network); return (stdout, stderr)."""
    cli.main(["url", "depth-eval"])
    return capsys.readouterr()


def test_packaged_skill_has_frontmatter():
    lines = TEXT.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    keys = {line.split(":", 1)[0] for line in lines[1:end]}
    assert keys == {"name", "description"}
    assert "name: ontic-pages" in lines[1:end]
    assert "\u2014" not in TEXT


def test_marker_goes_after_frontmatter():
    lines = WANTED.splitlines()
    end = lines.index("---", 1)
    assert lines[end + 1] == MARKER
    assert WANTED.replace(MARKER + "\n", "", 1) == TEXT


def test_crlf_frontmatter():
    text = "---\r\nname: x\r\ndescription: y\r\n---\r\n# body\r\n"
    assert skills.installed_text(text) == (
        f"---\r\nname: x\r\ndescription: y\r\n---\r\n{MARKER}\r\n# body\r\n"
    )


def test_first_install(home, capsys):
    out, err = run_url(capsys)
    assert out == "https://pages.onticlabs.io/depth-eval/\n"
    for root in (home / ".claude", home / ".codex"):
        assert skill_file(root).read_text() == WANTED
        assert str(skill_file(root)) in err
    assert err.count("\n") == 2
    assert not list(skill_file(home / ".claude").parent.glob(".*.tmp"))


def test_update_of_marked_copy_is_silent(home, capsys):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text(WANTED.replace("# Ontic pages", "# Old text"))
    skill_file(home / ".codex").parent.mkdir(parents=True)
    skill_file(home / ".codex").write_text(WANTED)
    out, err = run_url(capsys)
    assert target.read_text() == WANTED
    assert err == ""


def test_old_cli_marker_is_replaced(home, capsys):
    target = skill_file(home / ".codex")
    target.parent.mkdir(parents=True)
    target.write_text(f"---\nname: ontic-pages\n---\n{CLI_MARKER}\nold `ontic pages publish`\n")
    run_url(capsys)
    assert target.read_text() == WANTED


def test_identical_text_without_marker_is_adopted(home, capsys):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text(TEXT)
    _, err = run_url(capsys)
    assert target.read_text() == WANTED
    assert str(target) not in err


def test_own_copy_is_left_alone(home, capsys):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: ontic-pages\n---\nmy own notes\n")
    _, err = run_url(capsys)
    assert target.read_text() == "---\nname: ontic-pages\n---\nmy own notes\n"
    assert str(target) not in err


def test_symlinked_skill_file_is_left_alone(home, tmp_path, capsys):
    mine = tmp_path / "dotfiles-skill.md"
    mine.write_text(f"---\nname: ontic-pages\n---\n{MARKER}\nold\n")
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.symlink_to(mine)
    run_url(capsys)
    assert target.is_symlink()
    assert mine.read_text() == f"---\nname: ontic-pages\n---\n{MARKER}\nold\n"


def test_symlinked_skill_folder_is_written(home, tmp_path, capsys):
    folder = tmp_path / "dotfiles-folder"
    folder.mkdir()
    (home / ".claude" / "skills").mkdir()
    (home / ".claude" / "skills" / "ontic-pages").symlink_to(folder)
    run_url(capsys)
    assert (folder / "SKILL.md").read_text() == WANTED


def test_no_agent_folders(tmp_path, monkeypatch, capsys):
    home = tmp_path / "bare"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("ONTIC_AGENT_SKILLS", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    _, err = run_url(capsys)
    assert list(home.iterdir()) == []
    assert err == ""


def test_config_dir_variables(home, tmp_path, monkeypatch, capsys):
    claude, codex = tmp_path / "claude-config", tmp_path / "codex-home"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    run_url(capsys)
    assert skill_file(claude).read_text() == WANTED
    assert skill_file(codex).read_text() == WANTED
    assert not (home / ".claude" / "skills").exists()
    assert not (home / ".codex" / "skills").exists()


def test_opt_out(home, monkeypatch, capsys):
    monkeypatch.setenv("ONTIC_AGENT_SKILLS", "0")
    _, err = run_url(capsys)
    assert not (home / ".claude" / "skills").exists()
    assert err == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_read_only_folder_does_not_raise(home, capsys):
    claude = home / ".claude"
    claude.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        out, err = run_url(capsys)
    finally:
        claude.chmod(stat.S_IRWXU)
    assert out == "https://pages.onticlabs.io/depth-eval/\n"
    assert not (claude / "skills").exists()
    assert skill_file(home / ".codex").read_text() == WANTED


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_failed_write_leaves_no_temp_file(home, monkeypatch, capsys):
    folder = skill_file(home / ".claude").parent
    folder.mkdir(parents=True)

    def fail(src, dst):
        raise PermissionError("no")

    monkeypatch.setattr(skills.os, "replace", fail)
    run_url(capsys)
    assert list(folder.iterdir()) == []


def test_gateway_does_not_sync(home, monkeypatch):
    monkeypatch.setattr(cli, "cmd_gateway", lambda args: None)
    cli.main(["gateway"])
    assert not (home / ".claude" / "skills").exists()


def test_skill_sync_installs_quietly_and_never_overwrites_your_copy(home, capsys):
    mine = skill_file(home / ".claude")
    mine.parent.mkdir(parents=True)
    mine.write_text("mine\n")
    cli.main(["skill", "--sync"])
    assert capsys.readouterr().out == ""
    assert mine.read_text() == "mine\n"
    assert skill_file(home / ".codex").read_text() == WANTED


def test_skill_status(home, capsys):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text("mine\n")
    cli.main(["skill"])
    out = capsys.readouterr().out
    assert f"Claude Code: {target}\n  {skills.YOURS}\n" in out
    assert f"Codex: {skill_file(home / '.codex')}\n  {skills.MISSING}\n" in out
    assert "--install" in out
    assert target.read_text() == "mine\n"  # skill only reports
    assert not skill_file(home / ".codex").exists()


def test_skill_status_states(home, tmp_path, monkeypatch, capsys):
    skill_file(home / ".claude").parent.mkdir(parents=True)
    skill_file(home / ".claude").write_text(WANTED)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    cli.main(["skill"])
    out = capsys.readouterr().out
    assert (
        f"\n  {skills.CURRENT}; installed skill revision {REVISION}, this install {REVISION}\n"
        in out
    )
    assert f"\n  {skills.NO_AGENT}\n" in out
    monkeypatch.setenv("ONTIC_AGENT_SKILLS", "0")
    cli.main(["skill"])
    assert capsys.readouterr().out.count(skills.OFF) == 2


def test_skill_outdated_and_symlink(home, tmp_path, capsys):
    skill_file(home / ".claude").parent.mkdir(parents=True)
    skill_file(home / ".claude").write_text(f"{CLI_MARKER}\nold\n")
    skill_file(home / ".codex").parent.mkdir(parents=True)
    skill_file(home / ".codex").symlink_to(tmp_path / "elsewhere.md")
    cli.main(["skill"])
    out = capsys.readouterr().out
    assert (
        f"\n  {skills.OUTDATED}; installed copy has no skill revision, this install {REVISION}\n"
        in out
    )
    assert f"\n  {skills.SYMLINK}\n" in out


def test_skill_install(home, tmp_path, capsys):
    claude = skill_file(home / ".claude")
    claude.parent.mkdir(parents=True)
    claude.write_text("mine\n")
    codex = skill_file(home / ".codex")
    codex.parent.mkdir(parents=True)
    (tmp_path / "elsewhere.md").write_text("linked\n")
    codex.symlink_to(tmp_path / "elsewhere.md")
    cli.main(["skill", "--install"])
    out = capsys.readouterr().out
    assert claude.read_text() == WANTED
    assert (tmp_path / "elsewhere.md").read_text() == "linked\n"
    assert f"Claude Code: {claude}\n  installed\n" in out
    assert f"\n  {skills.SYMLINK}\n" in out


def test_revision_is_bumped_with_the_text():
    digest = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert (digest, REVISION) == (SKILL_SHA256, SKILL_REVISION), (
        "SKILL.md changed: bump skills.REVISION and update this hash"
    )


def test_marker_carries_the_revision():
    assert f"(skill revision {REVISION})" in MARKER
    assert skills.revision(WANTED) == REVISION
    assert skills.revision(f"---\n---\n{OLD_MARKER}\nold\n") == 0  # ours, before revisions
    assert skills.revision(f"{CLI_MARKER}\nold\n") == 0
    assert skills.revision("my own notes\n") is None
    assert OLD_MARKER not in WANTED  # older installs leave a revised copy alone


def with_revision(revision, body="# Other text\n"):
    marker = MARKER.replace(f"(skill revision {REVISION})", f"(skill revision {revision})")
    return f"---\nname: ontic-pages\n---\n{marker}\n{body}"


def test_newer_revision_is_left_alone(home, capsys):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text(with_revision(REVISION + 1))
    _, err = run_url(capsys)
    assert target.read_text() == with_revision(REVISION + 1)  # never back to older text
    assert skill_file(home / ".codex").read_text() == WANTED
    cli.main(["skill"])
    out = capsys.readouterr().out
    assert f"This install has skill revision {REVISION}.\n" in out
    assert (
        f"Claude Code: {target}\n  {skills.NEWER}; installed skill revision {REVISION + 1}, "
        f"this install {REVISION}\n"
    ) in out
    cli.main(["skill", "--install"])  # forced: ours goes over it
    assert target.read_text() == WANTED


@pytest.mark.parametrize(
    "installed",
    [
        with_revision(REVISION - 1),  # lower
        with_revision(REVISION),  # the same revision, other text
        f"---\nname: ontic-pages\n---\n{OLD_MARKER}\nold\n",  # before revisions
        f"{CLI_MARKER}\nold\n",
    ],
)
def test_older_or_changed_copies_are_rewritten(home, capsys, installed):
    target = skill_file(home / ".claude")
    target.parent.mkdir(parents=True)
    target.write_text(installed)
    _, err = run_url(capsys)
    assert target.read_text() == WANTED
    assert str(target) not in err  # an update, not a first install: quiet


def test_skill_status_revisions(home, capsys):
    skill_file(home / ".claude").parent.mkdir(parents=True)
    skill_file(home / ".claude").write_text(with_revision(REVISION - 1))
    skill_file(home / ".codex").parent.mkdir(parents=True)
    skill_file(home / ".codex").write_text(f"{OLD_MARKER}\nold\n")
    cli.main(["skill"])
    out = capsys.readouterr().out
    lower = f"installed skill revision {REVISION - 1}, this install {REVISION}"
    assert f"\n  {skills.OUTDATED}; {lower}\n" in out
    none = f"installed copy has no skill revision, this install {REVISION}"
    assert f"\n  {skills.OUTDATED}; {none}\n" in out


def test_skill_print(home, capsys):
    cli.main(["skill", "--print"])
    assert capsys.readouterr().out == TEXT
    assert not (home / ".claude" / "skills").exists()
