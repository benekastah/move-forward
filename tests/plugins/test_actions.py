from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import issue, know_jira_projects

from move_forward import refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph, Ref
from move_forward.plugin import KEPT, ActionError
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot
from move_forward.plugins.git.repository import LocalRepository
from move_forward.plugins.git_stack.config import GitStackConfig
from move_forward.plugins.git_stack.models import GitStackSnapshot, StackBranch, StackRepo
from move_forward.plugins.git_stack.repository import StackRepository
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.jira import client as jira_client
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraIssue
from move_forward.plugins.notes import markdown
from move_forward.plugins.notes.actions import ICED_FROM_DASHBOARD_NOTE
from move_forward.plugins.notes.models import Entry, Link
from move_forward.plugins.notes.repository import NotesRepository
from move_forward.registry import enabled, migrate
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
        db_path=tmp_path / "dashboard.db",
    )


def _stored(config: Config) -> Database:
    """The test's database as a refresh leaves it: git's snapshot of the repos, and whatever
    the notes hold from earlier actions."""
    db = Database(config.db_path)
    migrate(config, db)
    if config.jira:
        know_jira_projects(db, "APP")
    if config.git:
        # Just the branches: notes checks a repo is configured by the graph having its branches.
        repos = [
            LocalRepo(
                dir=str(repo),
                default_branch="main",
                worktrees=[],
                branches=[
                    Branch(name=name, sha=sha, committed_at=datetime.now(UTC))
                    for name, sha in (
                        line.split()
                        for line in git(
                            repo,
                            "for-each-ref",
                            "--format=%(refname:short) %(objectname)",
                            "refs/heads",
                        ).splitlines()
                    )
                ],
            )
            for repo in config.git.repos
        ]
        LocalRepository(db).replace(LocalSnapshot(repos=repos))
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
    with pytest.raises(ActionError, match="doesn't exist"):  # a revision, not a branch
        run("git/delete_branch", config, {"repo": str(repo), "branch": "done~0"})
    with pytest.raises(ActionError, match="nor a worktree of one"):
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


def test_push_goes_where_git_would_with_remotes_not_called_origin(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    git(repo, "remote", "rename", "origin", "upstream")
    fork = tmp_path / "fork.git"
    _init(fork, bare=True)
    push = {"repo": str(repo), "branch": "wip"}
    assert run("git/push_branch", config, push) == "pushed wip"  # one remote: that one
    assert git(repo, "rev-parse", "--abbrev-ref", "wip@{upstream}").strip() == "upstream/wip"
    git(repo, "remote", "add", "fork", str(fork))
    git(repo, "branch", "-q", "--no-track", "idea", "wip")
    idea = {"repo": str(repo), "branch": "idea"}
    with pytest.raises(ActionError, match="remote.pushDefault"):  # two, and nothing says which
        run("git/push_branch", config, idea)
    git(repo, "config", "remote.pushDefault", "fork")
    run("git/push_branch", config, idea)
    assert git(fork, "rev-parse", "idea") == git(repo, "rev-parse", "idea")
    # A fork's triangle: wip tracks upstream but pushes to the fork, under its own name.
    git(repo, "switch", "-q", "wip")
    _commit(repo, "c.txt")
    run("git/push_branch", config, push)
    assert git(fork, "rev-parse", "wip") == git(repo, "rev-parse", "wip")
    assert git(repo, "rev-parse", "upstream/wip") != git(repo, "rev-parse", "wip")


def test_wip_commit_takes_tracked_files_only(config: Config, repo: Path) -> None:
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("untracked")
    notice = run("git/wip_commit", config, {"repo": str(repo), "worktree": str(repo)})
    assert notice.startswith("committed 1 files as WIP")
    assert git(repo, "log", "-1", "--format=%s").strip() == "WIP"
    assert git(repo, "status", "--porcelain").strip() == "?? new.txt"
    with pytest.raises(ActionError, match="no modified"):
        run("git/wip_commit", config, {"repo": str(repo), "worktree": str(repo)})


def test_wip_commit_refuses_a_checkout_that_isnt_the_repos(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(elsewhere, "init", "-q")
    (elsewhere / "f").write_text("x")
    git(elsewhere, "add", "f")
    git(elsewhere, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    (elsewhere / "f").write_text("changed")
    with pytest.raises(ActionError, match="not a worktree of"):
        run("git/wip_commit", config, {"repo": str(repo), "worktree": str(elsewhere)})
    assert git(elsewhere, "log", "--format=%s").strip() == "init"


def _notes(config: Config) -> NotesRepository:
    return NotesRepository(_stored(config))


def _ice(config: Config, repo: Path, branch: str) -> str:
    return run("notes/ice", config, {"repo": str(repo), "branch": branch})


def test_ice_records_the_branch_and_its_commit_and_thaw_forgets_it(
    config: Config, repo: Path
) -> None:
    assert _ice(config, repo, "wip") == "put wip on ice (1 branches)"
    [ice] = _notes(config).ices()
    assert (ice.title, ice.note) == ("wip", ICED_FROM_DASHBOARD_NOTE)
    assert [(i.kind, i.ref_id, i.sha) for i in ice.items] == [
        ("branch", f"{repo}\twip", git(repo, "rev-parse", "wip").strip())
    ]
    iced_wip = graph(config).get("iced", str(ice.id))
    assert iced_wip is not None and iced_wip.attrs["shas"] == {f"{repo}\twip": ice.items[0].sha}
    assert graph(config).hidden(Ref(kind="branch", id=f"{repo}\twip"))
    with pytest.raises(ActionError, match="already on ice"):
        _ice(config, repo, "wip")
    assert run("notes/thaw", config, {"ice": str(ice.id)}) == "thawed wip"
    assert _notes(config).ices() == []
    with pytest.raises(ActionError, match="not on ice"):
        run("notes/thaw", config, {"ice": str(ice.id)})
    with pytest.raises(ActionError, match="no such stack"):
        run("notes/thaw", config, {"ice": "wip"})


def test_iced_branches_are_deleted_together_by_git_and_stay_on_ice(
    config: Config, repo: Path
) -> None:
    _ice(config, repo, "wip")
    _ice(config, repo, "done")
    targets = f"{repo}\twip\n{repo}\tdone"
    assert run("git/delete_together", config, {"targets": targets}) == "deleted wip, done"
    assert git(repo, "branch", "--list", "wip", "done") == ""
    assert [i.title for i in _notes(config).ices()] == ["wip", "done"]


def test_deleting_together_checks_every_branch_before_deleting_any(
    config: Config, repo: Path
) -> None:
    git(repo, "switch", "-q", "wip")
    targets = f"{repo}\tdone\n{repo}\twip"
    with pytest.raises(ActionError, match="checked out"):
        run("git/delete_together", config, {"targets": targets})
    assert git(repo, "branch", "--list", "done").strip() == "done"
    with pytest.raises(ActionError, match="nor a worktree of one"):
        run("git/delete_together", config, {"targets": "/elsewhere\tdone"})


def test_an_iced_branch_with_commits_since_it_was_iced_isnt_deleted(
    config: Config, repo: Path
) -> None:
    _ice(config, repo, "done")
    git(repo, "switch", "-q", "done")
    _commit(repo, "later.txt")
    git(repo, "switch", "-q", "main")
    with pytest.raises(ActionError, match="commits since it was iced"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "done"})
    assert git(repo, "branch", "--list", "done").strip() == "done"


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
    StackRepository(db).replace(snapshot)
    assert refresh.run_action(config, db, "notes/ice", {"repo": str(repo), "branch": "top"}) == (
        "put done on ice (4 branches)"
    )
    [ice] = NotesRepository(db).ices()
    # In git-stack's order, as `git stack show-stack` gives it.
    assert [i.ref_id.partition("\t")[2] for i in ice.items] == ["done", "wip", "top", "beside"]


def test_ice_takes_the_other_stacks_in_the_group_as_git_stack_would(
    config: Config, repo: Path
) -> None:
    for name in ("other", "other-top", "apart"):
        git(repo, "branch", name, "main")
    config = config.model_copy(update={"git_stack": GitStackConfig()})
    db = _stored(config)
    tracked = [
        StackBranch(name="main"),
        StackBranch(name="done", parent="main", group="g"),
        StackBranch(name="wip", parent="done"),
        StackBranch(name="other", parent="main", group="g"),  # another stack in the group
        StackBranch(name="other-top", parent="other"),
        StackBranch(name="apart", parent="main"),  # a stack of its own
    ]
    snapshot = GitStackSnapshot(repos=[StackRepo(dir=str(repo), branches=tracked)])
    StackRepository(db).replace(snapshot)
    assert refresh.run_action(config, db, "notes/ice", {"repo": str(repo), "branch": "wip"}) == (
        "put done on ice (4 branches)"
    )
    [ice] = NotesRepository(db).ices()
    assert [i.ref_id.partition("\t")[2] for i in ice.items] == ["done", "wip", "other", "other-top"]


def test_a_branch_iced_in_another_repo_can_be_iced_here(config: Config, repo: Path) -> None:
    db = _stored(config)
    other = markdown.read_on_ice(
        "## wip (~/elsewhere)\n- Iced: 2026-10-01\n- Branches:\n  - wip 1234\n", date.today()
    )[0]
    NotesRepository(db).replace_all([], other, [])
    assert _ice(config, repo, "wip") == "put wip on ice (1 branches)"
    with pytest.raises(ActionError, match="already on ice"):
        _ice(config, repo, "wip")


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
APP_1 = "APP-1 Fix the widget"


@pytest.fixture
def work_notes(config: Config) -> None:
    entries, sections = markdown.read_work_notes(WORK_NOTES)
    NotesRepository(_stored(config)).replace_all(entries, [], sections)


def _entry(config: Config, title: str = APP_1) -> Entry:
    return next(e for e in _notes(config).entries() if e.title == title)


def _open(config: Config, title: str = APP_1) -> list[str]:
    return [f"[{t.size}] {t.text}" for t in _entry(config, title).open_todos]


def _todo(config: Config, text: str, title: str = APP_1) -> dict[str, str]:
    """The fields that name a todo, by id, as its buttons post them."""
    [todo] = [t for t in _entry(config, title).todos if t.text == text]
    return {"todo": str(todo.id)}


def test_todo_done_and_undone(config: Config, work_notes: None) -> None:
    fields = _todo(config, "reply to the thread")
    result = refresh.perform_action(config, _stored(config), "notes/todo_done", fields)
    assert result.notice == "done: reply to the thread"
    assert result.params == {KEPT: f"notes:{fields['todo']}"}  # kept on the focus page
    entry = _entry(config)
    assert _open(config) == ["[big] get it merged", "[None] no size here"]
    assert [t.done_on for t in entry.todos if t.text == "reply to the thread"] == [
        datetime.now(UTC).date()
    ]
    assert entry.updated_on == datetime.now(UTC).date()
    assert _open(config, "APP-2 Other") == ["[quick] only one"]

    assert run("notes/todo_undone", config, fields) == "reopened: reply to the thread"
    assert _open(config) == [
        "[quick] reply to the thread",
        "[big] get it merged",
        "[None] no size here",
    ]
    with pytest.raises(ActionError, match="isn't done"):
        run("notes/todo_undone", config, fields)


def test_todo_delete_and_errors(config: Config, work_notes: None) -> None:
    fields = _todo(config, "no size here")
    assert run("notes/todo_delete", config, fields) == "dropped: no size here"
    assert _open(config) == ["[quick] reply to the thread", "[big] get it merged"]
    with pytest.raises(ActionError, match=f"no todo {fields['todo']}"):
        run("notes/todo_delete", config, fields)
    with pytest.raises(ActionError, match="bad todo id"):
        run("notes/todo_done", config, {"todo": "APP-9"})


def test_todo_link_links_a_jira_ticket_or_a_pr(config: Config, work_notes: None) -> None:
    fields = {**_todo(config, "get it merged"), "input": " APP-9 "}
    with pytest.raises(ActionError, match="nor a ticket key"):  # no jira: not a key
        run("notes/todo_link", config, fields)
    config = config.model_copy(update={"jira": JiraConfig(site="https://j.example/")})
    assert run("notes/todo_link", config, fields) == "linked APP-9"
    with pytest.raises(ActionError, match="already linked"):
        run("notes/todo_link", config, fields)
    pr = "https://github.com/acme/widgets/pull/5"
    run("notes/todo_link", config, {**fields, "input": pr})
    [todo] = [t for t in _entry(config).todos if t.text == "get it merged"]
    assert [(link.kind, link.ref) for link in todo.links] == [("pr", pr), ("ticket", "APP-9")]
    with pytest.raises(ActionError, match="neither a PR URL nor a ticket key"):
        run("notes/todo_link", config, {**fields, "input": "something else"})


def test_todo_add(config: Config, work_notes: None) -> None:
    assert run("notes/todo_add", config, {"entry": APP_1, "input": " write docs "}) == (
        "added: write docs"
    )
    run("notes/todo_add", config, {"entry": APP_1, "text": "ship it", "size": "quick"})
    assert _open(config)[-2:] == ["[None] write docs", "[quick] ship it"]
    pr = "https://github.com/acme/widgets/pull/8"
    run("notes/todo_add", config, {"entry": APP_1, "text": f"review {pr}"})
    assert _entry(config).todos[-1].links == [Link(kind="pr", ref=pr)]
    with pytest.raises(ActionError, match="empty"):
        run("notes/todo_add", config, {"entry": APP_1, "input": "  "})
    with pytest.raises(ActionError, match="size must be"):
        run("notes/todo_add", config, {"entry": APP_1, "text": "x", "size": "huge"})


def test_todo_add_can_create_the_entry(config: Config, work_notes: None) -> None:
    fields = {
        "entry": "APP-7 New thing",
        "text": "start",
        "create": "1",
        "where": "https://github.com/acme/widgets/pull/9",
    }
    with pytest.raises(ActionError, match="no work-notes entry"):
        run("notes/todo_add", config, {k: v for k, v in fields.items() if k != "create"})
    assert run("notes/todo_add", config, fields) == "added: start"
    run("notes/todo_add", config, {**fields, "text": "again"})  # not created twice
    entry = _entry(config, "APP-7 New thing")
    assert (entry.status, entry.where) == ("inferred", "https://github.com/acme/widgets/pull/9")
    assert [(link.kind, link.ref) for link in entry.links] == [
        ("pr", "https://github.com/acme/widgets/pull/9"),  # APP-7, in its title, isn't a link
    ]
    assert _open(config, "APP-7 New thing") == ["[None] start", "[None] again"]


def test_linking_a_ticket_in_a_project_new_to_jira_fetches_it_and_jira_learns_the_project(
    config: Config, work_notes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config.model_copy(update={"jira": JiraConfig(site="https://j.example/")})
    asked: list[list[str]] = []

    def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
        asked.append(keys)
        return [issue(key) for key in keys if key == "OPS-5"]

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira_client, "token", token)
    monkeypatch.setattr(jira_client, "tickets", tickets)
    fields = {**_todo(config, "get it merged"), "input": "OPS-5"}
    assert run("notes/todo_link", config, fields) == "linked OPS-5"
    with pytest.raises(ActionError, match="nor a ticket key Jira knows"):
        run("notes/todo_link", config, {**fields, "input": "NEW-404"})
    assert asked == [["OPS-5"], ["NEW-404"]]
    assert graph(config).named("see OPS-6") == [Ref(kind="ticket", id="OPS-6")]


def test_a_worktree_of_a_configured_repo_will_do_for_its_branches(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    second = tmp_path / "repo2"
    git(repo, "worktree", "add", "-q", "-b", "side", str(second))
    notice = run("git/delete_branch", config, {"repo": str(second), "branch": "done"})
    assert notice == f"deleted done in {second}"
    assert git(repo, "branch", "--list", "done").strip() == ""
    (second / "a.txt").write_text("changed")
    run("git/wip_commit", config, {"repo": str(second), "worktree": str(second)})
    assert git(second, "log", "-1", "--format=%s").strip() == "WIP"


def test_deleted_iced_branches_are_restored_at_the_commits_they_were_iced_at(
    config: Config, repo: Path
) -> None:
    wip = git(repo, "rev-parse", "wip").strip()
    _ice(config, repo, "wip")
    run("git/delete_together", config, {"targets": f"{repo}\twip"})
    built = refresh.build_dashboard(config, _stored(config))
    [on_ice] = built.panel("notes.iced").items
    [restore] = [a for a in on_ice.actions if a.name == "git/restore_together"]
    assert restore.fields == {"targets": f"{repo}\twip"}
    assert run("git/restore_together", config, restore.fields) == "restored wip"
    assert git(repo, "rev-parse", "wip").strip() == wip
    with pytest.raises(ActionError, match="already exists"):
        run("git/restore_together", config, restore.fields)
    with pytest.raises(ActionError, match="isn't on ice"):
        run("git/restore_together", config, {"targets": f"{repo}\t--force"})
