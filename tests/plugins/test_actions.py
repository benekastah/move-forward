from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import issue, know_jira_projects, section

from move_forward import refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph, Ref
from move_forward.plugin import KEPT, ActionError, SourceError
from move_forward.plugins.git import client
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
from move_forward.shell import CommandError, git


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


def _clone(origin: Path, path: Path, branch: str) -> Path:
    """A clone of `origin` on `branch`, with an identity to commit as (CI has none)."""
    git(path.parent, "clone", "-q", "-b", branch, str(origin), str(path))
    for key, value in (
        ("user.email", "other@x"),
        ("user.name", "Other"),
        ("commit.gpgsign", "false"),
    ):
        git(path, "config", key, value)
    return path


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
    return Config.model_validate(
        {
            "email": "me@acme.com",
            "github": GitHubConfig(org="acme"),
            "teams": ["core"],
            "git": GitConfig(repos=[repo]),
            "db_path": tmp_path / "dashboard.db",
        }
    )


def _stored(config: Config) -> Database:
    """The test's database as a refresh leaves it: git's snapshot of the repos, and whatever
    the notes hold from earlier actions."""
    db = Database(config.db_path)
    migrate(config, db)
    if config.section("jira"):
        know_jira_projects(db, "APP")
    if config.section("git"):
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
            for repo in section(config, "git", GitConfig).repos
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


def test_push_of_a_branch_tracking_a_local_one_goes_to_the_remote_not_into_it(
    config: Config, repo: Path
) -> None:
    git(repo, "branch", "-q", "--track", "feat", "main")  # its upstream is local main
    git(repo, "switch", "-q", "feat")
    _commit(repo, "c.txt")
    git(repo, "switch", "-q", "main")
    assert _branch(repo, "feat").upstream is None  # not "unpushed" against a local branch
    main_before = git(repo, "rev-parse", "main")
    assert run("git/push_branch", config, {"repo": str(repo), "branch": "feat"}) == "pushed feat"
    assert git(repo, "rev-parse", "main") == main_before  # main didn't move
    assert git(repo, "rev-parse", "origin/feat") == git(repo, "rev-parse", "feat")
    pushed = _branch(repo, "feat")
    assert (pushed.upstream, pushed.ahead) == ("origin/feat", 0)  # counted against its copy


def _branch(repo: Path, name: str) -> Branch:
    """The branch as git's collect reads it."""
    collected = client.collect_repo(repo, "me@x", datetime.now(UTC), 30, [])
    return next(b for b in collected.branches if b.name == name)


def test_push_of_a_branch_tracking_a_local_one_with_nowhere_to_go_is_refused(
    config: Config, repo: Path
) -> None:
    git(repo, "remote", "remove", "origin")
    git(repo, "branch", "-q", "--track", "feat", "main")
    with pytest.raises(ActionError, match="tracks a local branch"):
        run("git/push_branch", config, {"repo": str(repo), "branch": "feat"})


def test_delete_refuses_a_branch_moved_since_the_page_showed_it(config: Config, repo: Path) -> None:
    shown = git(repo, "rev-parse", "done").strip()
    git(repo, "switch", "-q", "done")
    _commit(repo, "after.txt")
    git(repo, "switch", "-q", "main")
    with pytest.raises(ActionError, match="moved since the page showed it"):
        run("git/delete_branch", config, {"repo": str(repo), "branch": "done", "sha": shown})
    notice = run("git/delete_branches", config, {"targets": f"{repo}\tdone\t{shown}"})
    assert "moved since" in notice and git(repo, "branch", "--list", "done").strip()


def test_pull_of_a_branch_tracking_a_local_one_takes_its_pushed_copy(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    git(repo, "branch", "-q", "--track", "feat", "main")
    run("git/push_branch", config, {"repo": str(repo), "branch": "feat"})
    other = _clone(tmp_path / "origin.git", tmp_path / "other", "feat")
    _commit(other, "theirs.txt")
    git(other, "push", "-q", "origin", "feat")
    main_before = git(repo, "rev-parse", "main")
    assert run("git/pull_branch", config, {"repo": str(repo), "branch": "feat"}) == "pulled feat"
    assert git(repo, "rev-parse", "feat") == git(other, "rev-parse", "feat")
    assert git(repo, "rev-parse", "main") == main_before


def test_delete_goes_through_for_a_branch_still_at_the_commit_shown_whatever_tags_say(
    config: Config, repo: Path
) -> None:
    shown = git(repo, "rev-parse", "done").strip()
    git(repo, "tag", "done", "wip")  # a tag of the same name, elsewhere
    assert run("git/delete_branch", config, {"repo": str(repo), "branch": "done", "sha": shown})
    assert git(repo, "branch", "--list", "done") == ""
    git(repo, "branch", "-q", "again", "main")
    again = git(repo, "rev-parse", "again").strip()
    git(repo, "branch", "-q", "plain", "main")
    lines = f"{repo}\tagain\t{again}\n{repo}\tplain\t"  # an empty column isn't checked
    assert run("git/delete_branches", config, {"targets": lines}) == "deleted 2 branches"


def test_a_branch_whose_upstream_is_gone_counts_what_isnt_on_the_default_branch(
    repo: Path,
) -> None:
    git(repo, "push", "-q", "-u", "origin", "wip")
    git(repo, "push", "-q", "origin", "--delete", "wip")
    git(repo, "fetch", "-q", "--prune")
    [wip] = [
        b
        for b in client.collect_repo(repo, "me@x", datetime.now(UTC), 30, []).branches
        if b.name == "wip"
    ]
    assert (wip.gone, wip.ahead_of_default) == (True, 1)


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
        ("git.branch", f"{repo}\twip", git(repo, "rev-parse", "wip").strip())
    ]
    iced_wip = graph(config).get("notes.iced", str(ice.id))
    assert iced_wip is not None and iced_wip.attrs["shas"] == {f"{repo}\twip": ice.items[0].sha}
    assert graph(config).hidden(Ref(kind="git.branch", id=f"{repo}\twip"))
    with pytest.raises(ActionError, match="already on ice"):
        _ice(config, repo, "wip")
    assert run("notes/thaw", config, {"ice": str(ice.id)}) == "thawed wip"
    assert _notes(config).ices() == []
    with pytest.raises(ActionError, match="not on ice"):
        run("notes/thaw", config, {"ice": str(ice.id)})
    with pytest.raises(ActionError, match="nothing on ice has the id"):
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
    config = config.with_section("git_stack", GitStackConfig())
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
    config = config.with_section("git_stack", GitStackConfig())
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


def test_an_entry_with_one_todo_can_be_done_from_the_dashboard(
    config: Config, work_notes: None
) -> None:
    built = refresh.build_dashboard(config, _stored(config))
    todos = built.find_panel("notes.todos")
    assert todos is not None
    single = next(item for item in todos.items if item.title == "APP-2 Other")
    assert [(a.name, a.fields) for a in single.actions[:1]] == [
        ("notes/todo_done", _todo(config, "only one", "APP-2 Other"))
    ]
    assert single.actions[0].checkbox


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
    config = config.with_section("jira", JiraConfig(site="https://j.example/"))
    assert run("notes/todo_link", config, fields) == "linked APP-9"
    with pytest.raises(ActionError, match="already linked"):
        run("notes/todo_link", config, fields)
    pr = "https://github.com/acme/widgets/pull/5"
    run("notes/todo_link", config, {**fields, "input": pr})
    [todo] = [t for t in _entry(config).todos if t.text == "get it merged"]
    assert [(link.kind, link.ref) for link in todo.links] == [
        ("pull_request", pr),
        ("ticket", "https://j.example/browse/APP-9"),  # by its URL, its id
    ]
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
    assert _entry(config).todos[-1].links == [Link(kind="pull_request", ref=pr)]
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
        (
            "pull_request",
            "https://github.com/acme/widgets/pull/9",
        ),  # APP-7, in its title, isn't a link
    ]
    assert _open(config, "APP-7 New thing") == ["[None] start", "[None] again"]


def test_linking_a_ticket_in_a_project_new_to_jira_fetches_it_and_jira_learns_the_project(
    config: Config, work_notes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config.with_section("jira", JiraConfig(site="https://j.example/"))
    asked: list[list[str]] = []

    def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
        asked.append(keys)
        return [issue(key, url=f"https://j.example/browse/{key}") for key in keys if key == "OPS-5"]

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira_client, "token", token)
    monkeypatch.setattr(jira_client, "tickets", tickets)
    fields = {**_todo(config, "get it merged"), "input": "OPS-5"}
    assert run("notes/todo_link", config, fields) == "linked OPS-5"
    with pytest.raises(ActionError, match="nor a ticket key Jira knows"):
        run("notes/todo_link", config, {**fields, "input": "NEW-404"})
    assert asked == [["OPS-5"], ["NEW-404"]]
    assert graph(config).named("see OPS-6") == [
        Ref(kind="ticket", id="https://j.example/browse/OPS-6")
    ]


def test_linking_a_ticket_says_why_when_jira_turns_the_fetch_away(
    config: Config, work_notes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config.with_section("jira", JiraConfig(site="https://j.example/"))

    def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
        raise SourceError("Jira rejected the API token")

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira_client, "token", token)
    monkeypatch.setattr(jira_client, "tickets", tickets)
    fields = {**_todo(config, "get it merged"), "input": "OPS-5"}
    with pytest.raises(ActionError, match="Jira rejected the API token"):
        run("notes/todo_link", config, fields)


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


def test_the_jira_transition_button_moves_the_ticket_and_reports_what_jira_refuses(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config.with_section("jira", JiraConfig(site="https://j.example/"))
    moved: list[tuple[str, list[str]]] = []

    def transition(config: JiraConfig, email: str, key: str, statuses: list[str]) -> str:
        if statuses[0] == "Nowhere":
            raise LookupError(f"{key} can't move to {statuses[0]}; it can go to: Done")
        moved.append((key, statuses))
        return statuses[-1]  # as if the workflow offered only the last

    monkeypatch.setattr(jira_client, "transition", transition)
    assert run("jira/transition", config, {"key": "APP-1", "to": "In Review"}) == (
        "moved APP-1 to In Review"
    )
    assert moved == [("APP-1", ["In Review"])]
    also = {"key": "APP-1", "to": "Done", "also": "Closed\nShipped"}
    assert run("jira/transition", config, also) == "moved APP-1 to Shipped"  # where it went
    assert moved[-1] == ("APP-1", ["Done", "Closed", "Shipped"])
    with pytest.raises(ActionError, match="can go to: Done"):
        run("jira/transition", config, {"key": "APP-1", "to": "Nowhere"})
    with pytest.raises(ActionError, match="not a ticket key"):
        run("jira/transition", config, {"key": "APP-1; drop", "to": "Done"})


def test_a_note_says_why_something_is_on_ice(config: Config, tmp_path: Path) -> None:
    db = Database(tmp_path / "db")
    migrate(config, db)
    notes = NotesRepository(db)
    ice_id = notes.add_ice("parked", date(2026, 1, 1), ICED_FROM_DASHBOARD_NOTE, [])
    refresh.perform_action(config, db, "notes/ice_note", {"ice": str(ice_id), "input": " waiting "})
    assert notes.ices()[0].note == "waiting"
    with pytest.raises(ActionError, match="empty"):
        refresh.perform_action(config, db, "notes/ice_note", {"ice": str(ice_id), "input": " "})
    with pytest.raises(ActionError, match="not on ice"):
        refresh.perform_action(config, db, "notes/ice_note", {"ice": "999", "input": "x"})


def test_a_todo_is_sized_as_its_added_and_afterwards(config: Config, tmp_path: Path) -> None:
    db = Database(tmp_path / "db")
    migrate(config, db)
    notes = NotesRepository(db)
    notes.add_entry("work", date(2026, 1, 1), "inferred", None, [])
    add = {"entry": "work", "input": "small", "choice": "quick"}
    refresh.perform_action(config, db, "notes/todo_add", add)
    refresh.perform_action(
        config, db, "notes/todo_add", {"entry": "work", "input": "none", "choice": ""}
    )
    small, unsized = notes.entries()[0].todos
    assert (small.size, unsized.size) == ("quick", None)
    refresh.perform_action(config, db, "notes/todo_size", {"todo": str(unsized.id), "size": "big"})
    assert notes.entries()[0].todos[1].size == "big"
    db.execute("UPDATE notes_entries SET updated_on = '2026-01-01'")
    refresh.perform_action(config, db, "notes/todo_size", {"todo": str(small.id), "size": "quick"})
    assert notes.entries()[0].updated_on == date(2026, 1, 1)  # nothing changed
    for bad in ("", "huge"):
        with pytest.raises(ActionError, match="size must be"):
            refresh.perform_action(
                config, db, "notes/todo_size", {"todo": str(small.id), "size": bad}
            )


def test_pull_fast_forwards_a_branch_checked_out_or_not_and_refuses_otherwise(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    fields = {"repo": str(repo), "branch": "wip"}
    run("git/push_branch", config, fields)
    other = tmp_path / "other"
    _clone(tmp_path / "origin.git", other, "wip")

    def pushed_elsewhere(name: str) -> str:
        _commit(other, name)
        git(other, "push", "-q")
        return git(other, "rev-parse", "HEAD")

    ahead = pushed_elsewhere("c.txt")
    assert run("git/pull_branch", config, fields) == "pulled wip"  # not checked out
    assert git(repo, "rev-parse", "wip") == ahead
    assert run("git/pull_branch", config, fields) == "wip is up to date"
    git(repo, "switch", "-q", "wip")
    ahead = pushed_elsewhere("d.txt")
    (repo / "b.txt").write_text("changed")
    with pytest.raises(ActionError, match="uncommitted changes"):
        run("git/pull_branch", config, fields)
    git(repo, "checkout", "--", "b.txt")
    assert run("git/pull_branch", config, fields) == "pulled wip"  # checked out
    assert git(repo, "rev-parse", "HEAD") == ahead
    pushed_elsewhere("e.txt")
    _commit(repo, "f.txt")  # diverged
    with pytest.raises(ActionError, match="can't fast-forward"):
        run("git/pull_branch", config, fields)


def test_pull_moves_a_branch_in_a_linked_worktree_and_never_one_being_rebased(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    fields = {"repo": str(repo), "branch": "wip"}
    run("git/push_branch", config, fields)
    other = tmp_path / "other"
    _clone(tmp_path / "origin.git", other, "wip")
    _commit(other, "c.txt")
    git(other, "push", "-q")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked), "wip")
    assert run("git/pull_branch", config, fields) == "pulled wip"
    assert git(linked, "rev-parse", "HEAD") == git(other, "rev-parse", "HEAD")
    git(repo, "worktree", "remove", str(linked))
    _commit(other, "d.txt")
    git(other, "push", "-q")
    git(repo, "switch", "-q", "wip")
    with pytest.raises(CommandError):  # stops mid-rebase, wip behind its upstream
        git(repo, "rebase", "-q", "--exec", "false", "HEAD~1")
    before = git(repo, "rev-parse", "refs/heads/wip")
    with pytest.raises(ActionError, match="couldn't pull wip"):  # git refuses: it's rebasing
        run("git/pull_branch", config, fields)
    assert git(repo, "rev-parse", "refs/heads/wip") == before


def test_pull_needs_an_upstream(config: Config, repo: Path) -> None:
    with pytest.raises(ActionError, match="no upstream"):
        run("git/pull_branch", config, {"repo": str(repo), "branch": "done"})
