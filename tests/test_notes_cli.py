"""`mfd notes ...`: reading and changing the notes from the command line."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from legacy import save_json_snapshot

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot
from move_forward.plugins.notes.repository import NotesRepository

PR = "https://github.com/acme/widgets/pull/5"
Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'email = "me@x"\ndb_path = "{tmp_path / "dashboard.db"}"\n\n'
        '[jira]\nsite = "https://j.example"\n\n[git]\nrepos = []\n'
    )
    return path


@pytest.fixture
def run(config: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def run(*args: str) -> tuple[int, str, str]:
        code = cli.main(["-c", str(config), "notes", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def _json(run: Run, *args: str) -> Any:
    code, out, err = run(*args, "--json")
    assert code == 0, err
    return json.loads(out)


def _db(config: Path) -> Database:
    return Database(config.parent / "dashboard.db")


def test_add_entries_and_todos_then_list_and_show(run: Run, config: Path) -> None:
    assert run("add-entry", "APP-1 Fix the widget", "--status", "in review", "--where", "repo") == (
        0,
        "added entry 1\n",
        "",
    )
    assert run("add-entry", "APP-1 Fix the widget")[0] == 1  # titles are unique
    created = _json(run, "todo", "add", "APP-1", "reply to the thread", "--size", "quick")
    assert created == {"notice": "added: reply to the thread", "id": 1}
    run("todo", "add", "1", f"review {PR}", "--link", "APP-2")
    run("todo", "add", "1", "big one", "--size", "big")

    code, out, _ = run("list")
    assert code == 0 and out == "1  APP-1 Fix the widget  [in review]  (3 open)\n"
    run("add-entry", "Done work")
    run("todo", "add", "Done work", "x")
    run("todo", "done", "4")
    assert run("list", "--open")[1].splitlines() == [
        "1  APP-1 Fix the widget  [in review]  (3 open)"
    ]
    assert len(run("list")[1].splitlines()) == 2

    [shown] = [e for e in _json(run, "list") if e["title"].startswith("APP-1")]
    assert [t["text"] for t in shown["todos"]] == ["reply to the thread", f"review {PR}", "big one"]
    assert [(link["kind"], link["ref"]) for link in shown["todos"][1]["links"]] == [
        ("pr", PR),
        ("ticket", "APP-2"),
    ]
    text = run("show", "1")[1]
    assert "#1 [quick] reply to the thread" in text and "about: APP-1" in text
    assert f"#2 [open] review {PR}  {PR}  APP-2" in text


def test_entries_are_found_by_id_title_pr_or_ticket(run: Run) -> None:
    run("add-entry", "APP-1 Fix", "--link", PR)
    run("add-entry", "APP-3 Other", "--link", PR)
    assert run("show", "APP-3 Other")[0] == 0 and run("show", "2")[0] == 0
    assert run("show", "APP-3")[0] == 0  # the ticket key in its title is a link
    code, _, err = run("show", PR)
    assert code == 1 and "about 2 entries" in err
    assert run("show", "nope")[2] == "mfd notes show: no entry 'nope'\n"


def test_set_link_conditions_and_decline(run: Run, config: Path) -> None:
    run("add-entry", "APP-1 Fix")
    assert run("set", "APP-1", "status", "in review")[1] == "APP-1 Fix: status is now in review\n"
    run("set", "APP-1", "blocked-on", "design review")
    run("set", "APP-1", "where", "engineering, branch me/app-1")
    assert run("link", "APP-1", PR)[0] == 0
    code, _, err = run("link", "APP-1", PR)
    assert code == 1 and "already about" in err
    assert run("link", "APP-1", "not a link")[0] == 1
    run("condition", "add", "APP-1", "wait for the release")
    assert run("condition", "add", "APP-1", "wait for the release")[0] == 1
    run("condition", "add", "APP-1", "ask Sam")
    assert run("condition", "remove", "APP-1", "ask Sam")[0] == 0
    assert run("condition", "remove", "APP-1", "ask Sam")[0] == 1
    run("decline", "APP-1", "nudge the reviewers")

    [entry] = NotesRepository(_db(config)).entries()
    assert (entry.status, entry.blocked_on) == ("in review", "design review")
    assert entry.where == "engineering, branch me/app-1"
    assert entry.conditions == ["wait for the release"]
    assert [(d.text, d.on) for d in entry.declined] == [
        ("nudge the reviewers", datetime.now(UTC).date())
    ]
    assert [link.ref for link in entry.links] == [PR, "APP-1"]  # by kind, then ref
    assert entry.updated_on == datetime.now(UTC).date()


def test_remove_forgets_an_entry_with_its_todos(run: Run, config: Path) -> None:
    run("add-entry", "APP-1 Fix")
    run("add-entry", "APP-2 Keep")
    run("todo", "add", "APP-1", "a")
    assert run("remove", "APP-1")[1] == "removed APP-1 Fix\n"
    assert run("remove", "APP-1")[0] == 1
    assert [e.title for e in NotesRepository(_db(config)).entries()] == ["APP-2 Keep"]
    assert _db(config).query("SELECT 1 FROM notes_todos") == []  # the todo went with it


def test_todos_are_changed_by_id(run: Run, config: Path) -> None:
    run("add-entry", "APP-1 Fix")
    run("todo", "add", "APP-1", "a")
    run("todo", "add", "APP-1", "b")
    assert run("todo", "done", "1")[1] == "done: a\n"
    assert run("todo", "done", "1")[0] == 1  # not open any more
    assert run("todo", "undone", "1")[1] == "reopened: a\n"
    assert run("todo", "link", "2", "APP-9")[1] == "linked APP-9\n"
    assert run("todo", "delete", "2")[1] == "dropped: b\n"
    code, _, err = run("todo", "delete", "2")
    assert code == 1 and "no todo 2" in err
    assert _json(run, "todo", "done", "1") == {"notice": "done: a"}  # --json on the leaf
    [entry] = NotesRepository(_db(config)).entries()
    assert [t.text for t in entry.todos] == ["a"] and entry.todos[0].done_on is not None


def test_ice_a_branch_and_thaw_it(run: Run, config: Path) -> None:
    repo = LocalRepo(
        dir="/src/app",
        default_branch="main",
        worktrees=[],
        branches=[Branch(name="feature", committed_at=datetime.now(UTC))],
    )
    save_json_snapshot(_db(config), "git", LocalSnapshot(repos=[repo]))
    assert run("ice", "--branch", "/src/app", "nope")[0] == 1
    assert run("ice", "--branch", "/elsewhere", "feature")[0] == 1
    assert run("ice", "--branch", "/src/app", "feature", "--note", "waiting on infra")[0] == 0
    assert run("ice", "--branch", "/src/app", "feature")[0] == 1  # already on ice

    [ice] = _json(run, "ices")
    assert (ice["title"], ice["note"]) == ("feature", "waiting on infra")
    assert "feature" in run("ices")[1] and "waiting on infra" in run("ices")[1]
    assert run("thaw", str(ice["id"]))[1] == "thawed feature\n"
    assert run("thaw", str(ice["id"]))[0] == 1
    assert run("ices")[1] == "\n"


def test_a_plugin_command_needs_its_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{tmp_path / "d.db"}"\n')
    assert cli.main(["-c", str(config), "notes", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []  # notes is always on
