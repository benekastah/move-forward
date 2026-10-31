from datetime import date
from pathlib import Path

import pytest

from move_forward import actions, notes, refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.jira.config import JiraConfig
from move_forward.repositories import NotesImportRepository
from move_forward.shell import git


def _init(path: Path, bare: bool = False) -> None:
    path.mkdir()
    git(path, "init", "-q", "-b", "main", *(["--bare"] if bare else []))
    if not bare:
        for key, value in (
            ("user.email", "me@x"),
            ("user.name", "Me"),
            ("commit.gpgsign", "false"),
        ):
            git(path, "config", key, value)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare origin, on main with one commit, plus branches `done` and `wip`."""
    origin = tmp_path / "origin.git"
    _init(origin, bare=True)
    repo = tmp_path / "repo"
    _init(repo)
    _commit(repo, "a.txt")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    git(repo, "branch", "done")
    git(repo, "switch", "-q", "-c", "wip")
    _commit(repo, "b.txt")
    git(repo, "switch", "-q", "main")
    return repo


@pytest.fixture
def config(repo: Path, tmp_path: Path) -> Config:
    return Config(
        email="me@acme.com",
        github=GitHubConfig(org="acme"),
        teams=["core"],
        git=GitConfig(repos=[repo]),
        notes_dir=tmp_path / "notes",
    )


def run(name: str, config: Config, fields: dict[str, str]) -> str:
    """Run an action as the server does: a plugin's over the notes as imported, else a legacy one."""
    if "/" not in name:
        return actions.run(name, config, fields)
    db = Database(":memory:")
    NotesImportRepository(db).replace(notes.load(config.notes_dir))
    return refresh.run_action(config, db, name, fields)


def test_delete_branch(config: Config, repo: Path) -> None:
    assert run("git/delete_branch", config, {"repo": str(repo), "branch": "done"}).startswith(
        "deleted done"
    )
    assert "done" not in git(repo, "branch", "--list", "done")


def test_delete_refuses_checked_out_missing_and_unknown_repo(config: Config, repo: Path) -> None:
    with pytest.raises(actions.ActionError, match="checked out"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "main"})
    with pytest.raises(actions.ActionError, match="doesn't exist"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "nope"})
    with pytest.raises(actions.ActionError, match="not a configured repo"):
        run("git/delete_branch", config, {"repo": "/elsewhere", "branch": "done"})
    with pytest.raises(actions.ActionError, match="missing field"):
        run("git/delete_branch", config, {"repo": str(repo)})
    with pytest.raises(actions.ActionError, match="unknown action"):
        run("git/rm_rf", config, {})


def test_delete_branches_continues_past_failures(config: Config, repo: Path) -> None:
    targets = "\n".join([f"{repo}\tdone", f"{repo}\tmain", f"{repo}\tnope", "", "/elsewhere\tx"])
    notice = run("git/delete_branches", config, {"targets": targets})
    assert notice.startswith("deleted 1 branches; failed: main: ")
    assert "nope: " in notice and "x: " in notice
    assert git(repo, "branch", "--list", "done") == ""


def test_push_sets_upstream_then_pushes_plainly(config: Config, repo: Path) -> None:
    assert run("git/push_branch", config, {"repo": str(repo), "branch": "wip"}) == "pushed wip"
    assert git(repo, "rev-parse", "--abbrev-ref", "wip@{upstream}").strip() == "origin/wip"
    git(repo, "switch", "-q", "wip")
    _commit(repo, "c.txt")
    run("git/push_branch", config, {"repo": str(repo), "branch": "wip"})
    assert git(repo, "rev-parse", "wip") == git(repo, "rev-parse", "origin/wip")


def test_wip_commit_takes_tracked_files_only(config: Config, repo: Path) -> None:
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("untracked")
    notice = run("git/wip_commit", config, {"repo": str(repo), "worktree": str(repo)})
    assert notice.startswith("committed 1 files as WIP")
    assert git(repo, "log", "-1", "--format=%s").strip() == "WIP"
    assert git(repo, "status", "--porcelain").strip() == "?? new.txt"
    with pytest.raises(actions.ActionError, match="no modified"):
        run("git/wip_commit", config, {"repo": str(repo), "worktree": str(repo)})


def test_ice_appends_an_entry_the_notes_parser_reads_and_thaw_removes_it(
    config: Config, repo: Path
) -> None:
    config.notes_dir.mkdir()
    (config.notes_dir / notes.ON_ICE_FILE).write_text(
        "# Stacks on ice\n\nLast asked: 2026-10-30\n\n## other (~/x)\n- Iced: 2026-10-01\n"
        "- Branches:\n  - other 1234\n"
    )
    notice = actions.ice(config, {"repo": str(repo), "branch": "wip"}, today=date(2026, 10, 30))
    assert notice == "put wip on ice (1 branches)"
    stacks = notes.parse_on_ice((config.notes_dir / notes.ON_ICE_FILE).read_text())
    assert [s.bottom_branch for s in stacks] == ["other", "wip"]
    assert stacks[1].iced == "2026-10-30"
    assert stacks[1].note == actions.ICED_FROM_DASHBOARD_NOTE
    assert stacks[1].branches[0].sha == git(repo, "rev-parse", "wip").strip()
    assert stacks[1].repo_dir == actions._tilde(repo)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(actions.ActionError, match="already on ice"):
        actions.ice(config, {"repo": str(repo), "branch": "wip"})

    assert run("thaw", config, {"branch": "wip"}) == "thawed wip"
    text = (config.notes_dir / notes.ON_ICE_FILE).read_text()
    assert [s.bottom_branch for s in notes.parse_on_ice(text)] == ["other"]
    assert "Last asked: 2026-10-30" in text
    with pytest.raises(actions.ActionError, match="not on ice"):
        run("thaw", config, {"branch": "wip"})


def test_delete_iced_removes_branches_and_the_entry(config: Config, repo: Path) -> None:
    config.notes_dir.mkdir()
    actions.ice(config, {"repo": str(repo), "branch": "wip"})
    actions.ice(config, {"repo": str(repo), "branch": "done"})
    notice = run("delete_iced", config, {"branch": "wip"})
    assert notice == "deleted 1 branches of wip and took it off ice"
    assert git(repo, "branch", "--list", "wip") == ""
    text = (config.notes_dir / notes.ON_ICE_FILE).read_text()
    assert [s.bottom_branch for s in notes.parse_on_ice(text)] == ["done"]
    with pytest.raises(actions.ActionError, match="not on ice"):
        run("delete_iced", config, {"branch": "wip"})


def test_delete_iced_refuses_a_checked_out_branch_before_deleting_anything(
    config: Config, repo: Path
) -> None:
    config.notes_dir.mkdir()
    git(repo, "switch", "-q", "wip")
    actions.ice(config, {"repo": str(repo), "branch": "wip"})
    with pytest.raises(actions.ActionError, match="checked out"):
        run("delete_iced", config, {"branch": "wip"})
    assert notes.parse_on_ice((config.notes_dir / notes.ON_ICE_FILE).read_text())


def test_stack_clean_refuses_a_repo_with_iced_stacks(config: Config, repo: Path) -> None:
    config.notes_dir.mkdir()
    actions.ice(config, {"repo": str(repo), "branch": "done"})
    with pytest.raises(actions.ActionError, match="iced stacks \\(done\\)"):
        run("git/stack_clean", config, {"repo": str(repo)})
    with pytest.raises(actions.ActionError, match="not a configured repo"):
        run("git/stack_clean", config, {"repo": "/elsewhere"})


def test_ice_creates_the_file_when_missing(config: Config, repo: Path) -> None:
    config.notes_dir.mkdir()
    actions.ice(config, {"repo": str(repo), "branch": "done"})
    text = (config.notes_dir / notes.ON_ICE_FILE).read_text()
    assert text.startswith("# Stacks on ice\n")
    assert len(notes.parse_on_ice(text)) == 1


WORK_NOTES = """\
# Work notes

## APP-1 Fix the widget
- Status: in review
- Left to do:
  - [quick] reply to the thread
  - [big] get it merged
  - no size here
- Blocked on: reviews
- Updated: 2026-10-29

## APP-2 Other
- Left to do:
  - [quick] only one
- Updated: 2026-10-20
"""


@pytest.fixture
def work_notes(config: Config) -> Path:
    config.notes_dir.mkdir()
    path = config.notes_dir / notes.WORK_NOTES_FILE
    path.write_text(WORK_NOTES)
    return path


def _todos(path: Path, entry: str) -> list[str]:
    item = next(i for i in notes.parse_work_notes(path.read_text()) if i.title == entry)
    return [f"[{t.size}] {t.text}" for t in item.todos]


def test_todo_done_moves_the_line_to_done_and_undone_brings_it_back(
    config: Config, work_notes: Path
) -> None:
    fields = {"entry": "APP-1 Fix the widget", "text": "reply to the thread"}
    assert (
        actions.todo_done(config, fields, today=date(2026, 10, 31)) == "done: reply to the thread"
    )
    text = work_notes.read_text()
    assert "  - [quick] reply to the thread\n" not in text
    assert (
        "  - no size here\n- Done 2026-10-31: [quick] reply to the thread\n- Blocked on: reviews\n"
        "- Updated: 2026-10-31\n" in text
    )
    assert _todos(work_notes, "APP-1 Fix the widget") == [
        "[big] get it merged",
        "[None] no size here",
    ]
    assert "## APP-2 Other\n- Left to do:\n  - [quick] only one\n- Updated: 2026-10-20\n" in text

    assert (
        actions.todo_undone(config, fields, today=date(2026, 11, 1))
        == "reopened: reply to the thread"
    )
    text = work_notes.read_text()
    assert "- Done" not in text
    assert _todos(work_notes, "APP-1 Fix the widget") == [
        "[big] get it merged",
        "[None] no size here",
        "[quick] reply to the thread",
    ]
    assert "- Updated: 2026-11-01\n" in text
    with pytest.raises(actions.ActionError, match="no done todo"):
        actions.todo_undone(config, fields)


def test_todo_done_on_the_last_todo_and_undone_recreates_left_to_do(
    config: Config, work_notes: Path
) -> None:
    fields = {"entry": "APP-2 Other", "text": "only one"}
    actions.todo_done(config, fields, today=date(2026, 10, 31))
    text = work_notes.read_text()
    assert (
        "## APP-2 Other\n- Left to do:\n- Done 2026-10-31: [quick] only one\n- Updated: 2026-10-31"
        in text
    )
    assert _todos(work_notes, "APP-2 Other") == []
    # The skill may drop an empty `Left to do:`; undone must cope without it.
    work_notes.write_text(text.replace("- Left to do:\n- Done", "- Done"))
    actions.todo_undone(config, fields, today=date(2026, 10, 31))
    assert _todos(work_notes, "APP-2 Other") == ["[quick] only one"]
    assert "- Done" not in work_notes.read_text()


def test_todo_delete_and_errors(config: Config, work_notes: Path) -> None:
    notice = run("todo_delete", config, {"entry": "APP-1 Fix the widget", "text": "no size here"})
    assert notice == "dropped: no size here"
    assert _todos(work_notes, "APP-1 Fix the widget") == [
        "[quick] reply to the thread",
        "[big] get it merged",
    ]
    assert "no size here" not in work_notes.read_text()
    with pytest.raises(actions.ActionError, match="no open todo"):
        run("todo_delete", config, {"entry": "APP-1 Fix the widget", "text": "no size here"})
    with pytest.raises(actions.ActionError, match="no work-notes entry"):
        run("todo_done", config, {"entry": "APP-9", "text": "x"})
    # Text is matched literally, so regex characters in a todo are fine.
    work_notes.write_text(work_notes.read_text().replace("get it merged", "fix a.b (c) [d]"))
    run("todo_done", config, {"entry": "APP-1 Fix the widget", "text": "fix a.b (c) [d]"})
    assert _todos(work_notes, "APP-1 Fix the widget") == ["[quick] reply to the thread"]


def test_redirect_params_keep_a_ticked_todo_on_the_page() -> None:
    fields = {"entry": "APP-1", "text": "x"}
    assert actions.redirect_params("todo_done", fields) == {"done_entry": "APP-1", "done_text": "x"}
    assert actions.redirect_params("todo_undone", fields) == {}
    assert actions.redirect_params("delete_branch", {"repo": "r", "branch": "b"}) == {}


def test_todo_link_appends_a_jira_or_pr_url(config: Config, work_notes: Path) -> None:
    fields = {"entry": "APP-1 Fix the widget", "text": "get it merged", "input": " APP-9 "}
    with pytest.raises(actions.ActionError, match="no \\[jira\\] site"):
        run("todo_link", config, fields)
    config = config.model_copy(update={"jira": JiraConfig(site="https://j.example/")})
    assert (
        actions.todo_link(config, fields, today=date(2026, 10, 31))
        == "linked https://j.example/browse/APP-9"
    )
    assert "  - [big] get it merged https://j.example/browse/APP-9\n" in work_notes.read_text()
    assert "- Updated: 2026-10-31" in work_notes.read_text()
    # The todo's text now includes the link, so that's how the next action names it.
    linked = {**fields, "text": "get it merged https://j.example/browse/APP-9"}
    with pytest.raises(actions.ActionError, match="already in that todo"):
        actions.todo_link(config, linked)
    pr = {**linked, "input": "https://github.com/acme/widgets/pull/5"}
    actions.todo_link(config, pr)
    assert _todos(work_notes, "APP-1 Fix the widget")[1] == (
        "[big] get it merged https://j.example/browse/APP-9 https://github.com/acme/widgets/pull/5"
    )
    with pytest.raises(actions.ActionError, match="neither a Jira key nor a URL"):
        actions.todo_link(config, {**fields, "input": "something else"})
    with pytest.raises(actions.ActionError, match="no open todo"):
        actions.todo_link(config, fields)  # the old text no longer matches


def test_todo_add_appends_under_left_to_do_or_creates_it(config: Config, work_notes: Path) -> None:
    entry = "APP-1 Fix the widget"
    assert (
        actions.todo_add(config, {"entry": entry, "input": " write docs "}) == "added: write docs"
    )
    assert run("todo_add", config, {"entry": entry, "text": "ship it", "size": "quick"})
    assert _todos(work_notes, entry)[-2:] == ["[None] write docs", "[quick] ship it"]
    work_notes.write_text(
        work_notes.read_text().replace("- Left to do:\n  - [quick] only one\n", "")
    )
    run("todo_add", config, {"entry": "APP-2 Other", "text": "fresh", "size": "big"})
    assert _todos(work_notes, "APP-2 Other") == ["[big] fresh"]
    with pytest.raises(actions.ActionError, match="empty"):
        run("todo_add", config, {"entry": entry, "input": "  "})
    with pytest.raises(actions.ActionError, match="size must be"):
        run("todo_add", config, {"entry": entry, "text": "x", "size": "huge"})


def test_todo_add_can_create_the_entry(config: Config, work_notes: Path) -> None:
    fields = {
        "entry": "APP-7 New thing",
        "text": "start",
        "create": "1",
        "where": "https://github.com/acme/widgets/pull/9",
    }
    with pytest.raises(actions.ActionError, match="no work-notes entry"):
        run("todo_add", config, {k: v for k, v in fields.items() if k != "create"})
    assert actions.todo_add(config, fields, today=date(2026, 10, 31)) == "added: start"
    assert work_notes.read_text().endswith(
        "\n## APP-7 New thing\n- Status: inferred\n- Where: https://github.com/acme/widgets/pull/9\n"
        "- Left to do:\n  - start\n- Updated: 2026-10-31\n"
    )
    actions.todo_add(config, {**fields, "text": "again"})  # an existing entry isn't recreated
    assert work_notes.read_text().count("## APP-7 New thing") == 1
    assert _todos(work_notes, "APP-7 New thing") == ["[None] start", "[None] again"]
