from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from move_forward import notes, refresh, server
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import (
    Branch,
    GitStackSnapshot,
    LocalRepo,
    LocalSnapshot,
    StackBranch,
    StackRepo,
)
from move_forward.graph import Graph
from move_forward.plugin import ActionError
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git_stack.config import GitStackConfig
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.notes import actions as notes_actions
from move_forward.plugins.notes.config import NotesConfig
from move_forward.registry import enabled
from move_forward.repositories import NotesImportRepository, SnapshotRepository
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
        notes=NotesConfig(dir=tmp_path / "notes"),
    )


def _stored(config: Config) -> Database:
    """A database as a refresh leaves it: git's snapshot of the repos, and the notes imported."""
    db = Database(":memory:")
    if config.git:
        # Just the branches: notes checks a repo is configured by the graph having its branches.
        repos = [
            LocalRepo(
                dir=str(repo),
                default_branch="main",
                worktrees=[],
                branches=[
                    Branch(name=name, committed_at=datetime.now(UTC))
                    for name in git(
                        repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"
                    ).split()
                ],
            )
            for repo in config.git.repos
        ]
        SnapshotRepository(db).save("git", datetime.now(UTC), LocalSnapshot(repos=repos))
    NotesImportRepository(db).replace(notes.load(config.notes.dir))
    return db


def run(name: str, config: Config, fields: dict[str, str]) -> str:
    """Run an action as the server does, `<plugin>/<action>`, over a fresh refresh's data."""
    return refresh.run_action(config, _stored(config), name, fields)


def graph(config: Config) -> Graph:
    """The graph an action would get, for calling a notes action directly."""
    db = _stored(config)
    built, _ = refresh.stored_graph(db, enabled(config, db, datetime.now(UTC)))
    return built


def test_delete_branch(config: Config, repo: Path) -> None:
    assert run("git/delete_branch", config, {"repo": str(repo), "branch": "done"}).startswith(
        "deleted done"
    )
    assert "done" not in git(repo, "branch", "--list", "done")


def test_delete_refuses_checked_out_missing_and_unknown_repo(config: Config, repo: Path) -> None:
    with pytest.raises(ActionError, match="checked out"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "main"})
    with pytest.raises(ActionError, match="doesn't exist"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "nope"})
    with pytest.raises(ActionError, match="not a configured repo"):
        run("git/delete_branch", config, {"repo": "/elsewhere", "branch": "done"})
    with pytest.raises(ActionError, match="missing field"):
        run("git/delete_branch", config, {"repo": str(repo)})
    with pytest.raises(ActionError, match="unknown action"):
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
    with pytest.raises(ActionError, match="no modified"):
        run("git/wip_commit", config, {"repo": str(repo), "worktree": str(repo)})


def test_ice_appends_an_entry_the_notes_parser_reads_and_thaw_removes_it(
    config: Config, repo: Path
) -> None:
    config.notes.dir.mkdir()
    (config.notes.dir / notes.ON_ICE_FILE).write_text(
        "# Stacks on ice\n\nLast asked: 2026-10-30\n\n## other (~/x)\n- Iced: 2026-10-01\n"
        "- Branches:\n  - other 1234\n"
    )
    notice = notes_actions.ice(
        config.notes, graph(config), {"repo": str(repo), "branch": "wip"}, today=date(2026, 10, 30)
    )
    assert notice == "put wip on ice (1 branches)"
    stacks = notes.parse_on_ice((config.notes.dir / notes.ON_ICE_FILE).read_text())
    assert [s.bottom_branch for s in stacks] == ["other", "wip"]
    assert stacks[1].iced == "2026-10-30"
    assert stacks[1].note == notes_actions.ICED_FROM_DASHBOARD_NOTE
    assert stacks[1].branches[0].sha == git(repo, "rev-parse", "wip").strip()
    assert stacks[1].repo_dir == notes_actions._tilde(repo)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ActionError, match="already on ice"):
        notes_actions.ice(config.notes, graph(config), {"repo": str(repo), "branch": "wip"})

    assert run("notes/thaw", config, {"branch": "wip"}) == "thawed wip"
    text = (config.notes.dir / notes.ON_ICE_FILE).read_text()
    assert [s.bottom_branch for s in notes.parse_on_ice(text)] == ["other"]
    assert "Last asked: 2026-10-30" in text
    with pytest.raises(ActionError, match="not on ice"):
        run("notes/thaw", config, {"branch": "wip"})


def test_delete_iced_removes_branches_and_the_entry(config: Config, repo: Path) -> None:
    config.notes.dir.mkdir()
    notes_actions.ice(config.notes, graph(config), {"repo": str(repo), "branch": "wip"})
    notes_actions.ice(config.notes, graph(config), {"repo": str(repo), "branch": "done"})
    notice = run("notes/delete_iced", config, {"branch": "wip"})
    assert notice == "deleted 1 branches of wip and took it off ice"
    assert git(repo, "branch", "--list", "wip") == ""
    text = (config.notes.dir / notes.ON_ICE_FILE).read_text()
    assert [s.bottom_branch for s in notes.parse_on_ice(text)] == ["done"]
    with pytest.raises(ActionError, match="not on ice"):
        run("notes/delete_iced", config, {"branch": "wip"})


def test_delete_iced_refuses_a_checked_out_branch_before_deleting_anything(
    config: Config, repo: Path
) -> None:
    config.notes.dir.mkdir()
    git(repo, "switch", "-q", "wip")
    notes_actions.ice(config.notes, graph(config), {"repo": str(repo), "branch": "wip"})
    with pytest.raises(ActionError, match="checked out"):
        run("notes/delete_iced", config, {"branch": "wip"})
    assert notes.parse_on_ice((config.notes.dir / notes.ON_ICE_FILE).read_text())


def test_ice_takes_the_whole_stack_git_stack_knows(config: Config, repo: Path) -> None:
    git(repo, "branch", "top", "wip")
    git(repo, "branch", "beside", "done")
    config = config.model_copy(update={"git_stack": GitStackConfig()})
    db = _stored(config)
    tracked = [
        StackBranch(name="main"),
        StackBranch(name="done", parent="main"),
        StackBranch(name="wip", parent="done"),
        StackBranch(name="top", parent="wip"),
        StackBranch(name="beside", parent="done"),
    ]
    snapshot = GitStackSnapshot(repos=[StackRepo(dir=str(repo), branches=tracked)])
    SnapshotRepository(db).save("git_stack", datetime.now(UTC), snapshot)
    built, _ = refresh.stored_graph(db, enabled(config, db, datetime.now(UTC)))
    config.notes.dir.mkdir()
    notice = notes_actions.ice(config.notes, built, {"repo": str(repo), "branch": "top"})
    assert notice == "put done on ice (4 branches)"
    [stack] = notes.parse_on_ice((config.notes.dir / notes.ON_ICE_FILE).read_text())
    assert [b.name for b in stack.branches] == ["done", "beside", "wip", "top"]


def test_ice_creates_the_file_when_missing(config: Config, repo: Path) -> None:
    config.notes.dir.mkdir()
    notes_actions.ice(config.notes, graph(config), {"repo": str(repo), "branch": "done"})
    text = (config.notes.dir / notes.ON_ICE_FILE).read_text()
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
    config.notes.dir.mkdir()
    path = config.notes.dir / notes.WORK_NOTES_FILE
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
        notes_actions.todo_done(config.notes, graph(config), fields, today=date(2026, 10, 31))
        == "done: reply to the thread"
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
        notes_actions.todo_undone(config.notes, graph(config), fields, today=date(2026, 11, 1))
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
    with pytest.raises(ActionError, match="no done todo"):
        notes_actions.todo_undone(config.notes, graph(config), fields)


def test_todo_done_on_the_last_todo_and_undone_recreates_left_to_do(
    config: Config, work_notes: Path
) -> None:
    fields = {"entry": "APP-2 Other", "text": "only one"}
    notes_actions.todo_done(config.notes, graph(config), fields, today=date(2026, 10, 31))
    text = work_notes.read_text()
    assert (
        "## APP-2 Other\n- Left to do:\n- Done 2026-10-31: [quick] only one\n- Updated: 2026-10-31"
        in text
    )
    assert _todos(work_notes, "APP-2 Other") == []
    # The skill may drop an empty `Left to do:`; undone must cope without it.
    work_notes.write_text(text.replace("- Left to do:\n- Done", "- Done"))
    notes_actions.todo_undone(config.notes, graph(config), fields, today=date(2026, 10, 31))
    assert _todos(work_notes, "APP-2 Other") == ["[quick] only one"]
    assert "- Done" not in work_notes.read_text()


def test_todo_delete_and_errors(config: Config, work_notes: Path) -> None:
    notice = run(
        "notes/todo_delete", config, {"entry": "APP-1 Fix the widget", "text": "no size here"}
    )
    assert notice == "dropped: no size here"
    assert _todos(work_notes, "APP-1 Fix the widget") == [
        "[quick] reply to the thread",
        "[big] get it merged",
    ]
    assert "no size here" not in work_notes.read_text()
    with pytest.raises(ActionError, match="no open todo"):
        run("notes/todo_delete", config, {"entry": "APP-1 Fix the widget", "text": "no size here"})
    with pytest.raises(ActionError, match="no work-notes entry"):
        run("notes/todo_done", config, {"entry": "APP-9", "text": "x"})
    # Text is matched literally, so regex characters in a todo are fine.
    work_notes.write_text(work_notes.read_text().replace("get it merged", "fix a.b (c) [d]"))
    run("notes/todo_done", config, {"entry": "APP-1 Fix the widget", "text": "fix a.b (c) [d]"})
    assert _todos(work_notes, "APP-1 Fix the widget") == ["[quick] reply to the thread"]


def test_redirect_params_keep_a_ticked_todo_on_the_page() -> None:
    fields = {"entry": "APP-1", "text": "x"}
    redirect = server._redirect_params  # pyright: ignore[reportPrivateUsage]
    assert redirect("notes/todo_done", fields) == {"done_entry": "APP-1", "done_text": "x"}
    assert redirect("notes/todo_undone", fields) == {}


def test_todo_link_appends_a_jira_or_pr_url(config: Config, work_notes: Path) -> None:
    fields = {"entry": "APP-1 Fix the widget", "text": "get it merged", "input": " APP-9 "}
    with pytest.raises(ActionError, match="no \\[jira\\] site"):
        run("notes/todo_link", config, fields)
    config = config.model_copy(update={"jira": JiraConfig(site="https://j.example/")})
    assert (
        notes_actions.todo_link(config.notes, graph(config), fields, today=date(2026, 10, 31))
        == "linked https://j.example/browse/APP-9"
    )
    assert "  - [big] get it merged https://j.example/browse/APP-9\n" in work_notes.read_text()
    assert "- Updated: 2026-10-31" in work_notes.read_text()
    # The todo's text now includes the link, so that's how the next action names it.
    linked = {**fields, "text": "get it merged https://j.example/browse/APP-9"}
    with pytest.raises(ActionError, match="already in that todo"):
        notes_actions.todo_link(config.notes, graph(config), linked)
    pr = {**linked, "input": "https://github.com/acme/widgets/pull/5"}
    notes_actions.todo_link(config.notes, graph(config), pr)
    assert _todos(work_notes, "APP-1 Fix the widget")[1] == (
        "[big] get it merged https://j.example/browse/APP-9 https://github.com/acme/widgets/pull/5"
    )
    with pytest.raises(ActionError, match="neither a Jira key nor a URL"):
        notes_actions.todo_link(config.notes, graph(config), {**fields, "input": "something else"})
    with pytest.raises(ActionError, match="no open todo"):
        notes_actions.todo_link(
            config.notes, graph(config), fields
        )  # the old text no longer matches


def test_todo_add_appends_under_left_to_do_or_creates_it(config: Config, work_notes: Path) -> None:
    entry = "APP-1 Fix the widget"
    assert (
        notes_actions.todo_add(
            config.notes, graph(config), {"entry": entry, "input": " write docs "}
        )
        == "added: write docs"
    )
    assert run("notes/todo_add", config, {"entry": entry, "text": "ship it", "size": "quick"})
    assert _todos(work_notes, entry)[-2:] == ["[None] write docs", "[quick] ship it"]
    work_notes.write_text(
        work_notes.read_text().replace("- Left to do:\n  - [quick] only one\n", "")
    )
    run("notes/todo_add", config, {"entry": "APP-2 Other", "text": "fresh", "size": "big"})
    assert _todos(work_notes, "APP-2 Other") == ["[big] fresh"]
    with pytest.raises(ActionError, match="empty"):
        run("notes/todo_add", config, {"entry": entry, "input": "  "})
    with pytest.raises(ActionError, match="size must be"):
        run("notes/todo_add", config, {"entry": entry, "text": "x", "size": "huge"})


def test_todo_add_can_create_the_entry(config: Config, work_notes: Path) -> None:
    fields = {
        "entry": "APP-7 New thing",
        "text": "start",
        "create": "1",
        "where": "https://github.com/acme/widgets/pull/9",
    }
    with pytest.raises(ActionError, match="no work-notes entry"):
        run("notes/todo_add", config, {k: v for k, v in fields.items() if k != "create"})
    assert (
        notes_actions.todo_add(config.notes, graph(config), fields, today=date(2026, 10, 31))
        == "added: start"
    )
    assert work_notes.read_text().endswith(
        "\n## APP-7 New thing\n- Status: inferred\n- Where: https://github.com/acme/widgets/pull/9\n"
        "- Left to do:\n  - start\n- Updated: 2026-10-31\n"
    )
    notes_actions.todo_add(
        config.notes, graph(config), {**fields, "text": "again"}
    )  # an existing entry isn't recreated
    assert work_notes.read_text().count("## APP-7 New thing") == 1
    assert _todos(work_notes, "APP-7 New thing") == ["[None] start", "[None] again"]
