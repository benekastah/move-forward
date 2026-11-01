"""notes' ice as other plugins see it: an `iced` entity per act of icing, hiding what it
covers, including what notes finds for it in the graph."""

from pathlib import Path

import pytest
from conftest import NOW, dashboard, entry, issue, panel, pr, snapshot, stored, titles, todo

from move_forward import refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.plugin import ActionError, Context
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.notes import plugin as notes_plugin
from move_forward.plugins.notes.cli import export_notes
from move_forward.plugins.notes.config import NotesConfig
from move_forward.plugins.notes.covers import covers
from move_forward.plugins.notes.models import Ice, IcedItem
from move_forward.plugins.notes.panels import Builder, entities
from move_forward.plugins.notes.plugin import NotesPlugin
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository
from move_forward.refresh import build_dashboard
from move_forward.registry import enabled
from move_forward.repositories import KvRepository

WIDGETS = "/src/widgets"
BRANCH = Ref(kind="branch", id=f"{WIDGETS}\tme/x")
ICE = "notes/ice_item"


def _pr(number: int, repo: str, head: str) -> Entity:
    return Entity(
        ref=Ref(kind="pr", id=f"https://github.com/{repo}/pull/{number}"),
        title=f"PR {number}",
        attrs={"repo": repo, "head": head},
    )


def _ice(ice_id: int, *branch_ids: str) -> Ice:
    items = [
        IcedItem(
            kind="branch",
            ref_id=b,
            name=b.partition("\t")[2],
            repo_dir=b.partition("\t")[0],
            sha="1",
        )
        for b in branch_ids
    ]
    return Ice(id=ice_id, title="me/x", iced_on=NOW.date(), items=items)


def test_an_ice_covers_its_branches_prs_in_their_repo_and_what_tracks_them() -> None:
    graph = Graph(
        [
            Entity(ref=Ref(kind="repo", id="acme/widgets"), title="w", attrs={"dir": WIDGETS}),
            _pr(1, "acme/widgets", "me/x"),
            _pr(2, "acme/other", "me/x"),  # same name, another repo
            _pr(3, "acme/widgets", "me/y"),
            Entity(ref=Ref(kind="stack_branch", id=BRANCH.id), title="me/x", refs=[BRANCH]),
        ]
    )
    found = covers(graph, [_ice(7, BRANCH.id), _ice(8, f"{WIDGETS}\tme/none")])
    assert found == {
        7: [_pr(1, "acme/widgets", "me/x").ref, Ref(kind="stack_branch", id=BRANCH.id)],
    }


def test_without_its_checkout_an_ice_covers_its_branch_names_prs_in_repos_git_lacks() -> None:
    graph = Graph(
        [
            Entity(ref=Ref(kind="repo", id="acme/other"), title="o", attrs={"dir": "/src/other"}),
            _pr(1, "acme/widgets", "me/x"),
            _pr(2, "acme/more", "me/x"),
            _pr(3, "acme/other", "me/x"),  # checked out elsewhere: not this branch's
        ]
    )
    assert covers(graph, [_ice(7, BRANCH.id)]) == {
        7: [_pr(1, "acme/widgets", "me/x").ref, _pr(2, "acme/more", "me/x").ref]
    }


def test_iced_entities_hide_their_items_and_covers() -> None:
    pr = Ref(kind="pr", id="https://github.com/acme/widgets/pull/1")
    published = entities([], [_ice(7, BRANCH.id)], {7: [pr]})
    [iced] = [e for e in published if e.ref.kind == "iced"]
    assert (iced.ref.id, iced.title, iced.refs) == ("7", "me/x", [BRANCH, pr])
    assert iced.attrs["shas"] == {BRANCH.id: "1"}
    graph = Graph(published, hiding=["iced"])
    assert graph.hidden(BRANCH) and graph.hidden(pr)


def test_covers_are_replaced_only_for_the_ices_named_and_only_while_they_exist() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    first = notes.add_ice("a", NOW.date(), None, _ice(0, BRANCH.id).items)
    second = notes.add_ice("b", NOW.date(), None, _ice(0, f"{WIDGETS}\tme/y").items)
    one, two = Ref(kind="pr", id="1"), Ref(kind="pr", id="2")
    notes.replace_covers([first, second], {first: [one], second: [two]})
    notes.replace_covers([first], {first: [two], second: []})  # second's are left alone
    assert notes.covers() == {first: [two], second: [two]}
    notes.delete_ice(second)  # thawed while a refresh was finding covers for it
    notes.replace_covers([first, second], {first: [one], second: [one]})
    assert notes.covers() == {first: [one]}


def test_after_a_refresh_notes_finds_its_covers_again() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    ctx = Context(
        config=NotesConfig(),
        email="me@acme.com",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "notes"),
    )
    ice_id = NotesRepository(db).add_ice("me/x", NOW.date(), None, _ice(0, BRANCH.id).items)
    pr = _pr(1, "acme/widgets", "me/x")
    NotesPlugin().after_collect(ctx, Graph([pr]))
    assert NotesRepository(db).covers() == {ice_id: [pr.ref]}


def _work() -> tuple[Config, Database]:
    """Two conflicting PRs of mine, mentioning APP-1 and APP-2, those tickets, and an entry
    about APP-1."""
    return stored(
        github=snapshot(mine=[pr(1, merge_state="DIRTY"), pr(2, merge_state="DIRTY")]),
        jira=JiraSnapshot(issues=[issue("APP-1"), issue("APP-2")]),
        work_items=[entry("APP-1 Fix", [todo("finish")])],
    )


def _shown(config: Config, db: Database) -> set[str]:
    """Every item's title, but On ice's."""
    built = build_dashboard(config, db, NOW)
    return {i.title.split()[0] for p in built.panels if p.key != "notes.iced" for i in p.items}


def _ice_item(config: Config, db: Database, kind: str, ref_id: str) -> str:
    return refresh.run_action(config, db, "notes/ice_item", {"kind": kind, "id": ref_id})


def test_icing_a_ticket_hides_the_prs_mentioning_it_and_the_entry_about_it() -> None:
    config, db = _work()
    assert {"PR", "APP-1", "APP-2"} <= _shown(config, db)
    assert _ice_item(config, db, "ticket", "APP-1") == "put APP-1 Do the thing on ice"
    shown = _shown(config, db)
    assert "APP-1" not in shown and "APP-2" in shown  # the entry and the ticket's own items
    built = build_dashboard(config, db, NOW)
    assert titles(built, "github.conflicts") == ["PR 2"]
    [on_ice] = panel(built, "notes.iced").items
    assert (on_ice.title, on_ice.url) == ("APP-1 Do the thing", "https://jira/browse/APP-1")


def test_icing_an_entry_hides_it_and_what_its_tickets_link() -> None:
    config, db = _work()
    _ice_item(config, db, "entry", "APP-1 Fix")
    built = build_dashboard(config, db, NOW)
    assert titles(built, "github.conflicts") == ["PR 2"]
    assert titles(built, "notes.todos") == []


def test_icing_a_pr_hides_only_it() -> None:
    config, db = _work()
    _ice_item(config, db, "pr", pr(1).url)
    built = build_dashboard(config, db, NOW)
    assert titles(built, "github.conflicts") == ["PR 2"]
    assert titles(built, "notes.todos") == ["APP-1 Fix"]  # its ticket and entry aren't on ice


def test_ice_item_refuses_other_kinds_unknown_ids_and_what_is_on_ice() -> None:
    config, db = _work()
    with pytest.raises(ActionError, match="not a 'branch'"):
        _ice_item(config, db, "branch", "/src/widgets\tme/x")
    with pytest.raises(ActionError, match="no ticket 'APP-9'"):
        _ice_item(config, db, "ticket", "APP-9")
    _ice_item(config, db, "ticket", "APP-1")
    with pytest.raises(ActionError, match="already on ice"):
        _ice_item(config, db, "ticket", "APP-1")
    with pytest.raises(ActionError, match="already on ice"):
        _ice_item(config, db, "pr", pr(1).url)  # hidden through APP-1


def test_put_on_ice_is_offered_on_my_open_prs_tickets_and_entries() -> None:
    mine, theirs = pr(1, merge_state="DIRTY"), pr(3, author="alice")
    built = dashboard(
        github=snapshot(mine=[mine], direct_requests=[theirs]),
        work_items=[entry("APP-1 Fix", [todo("finish")])],
    )

    def ice_fields(key: str) -> list[dict[str, str]]:
        return [a.fields for i in panel(built, key).items for a in i.actions if a.name == ICE]

    assert ice_fields("github.conflicts") == [{"kind": "pr", "id": mine.url}]
    assert ice_fields("notes.todos") == [{"kind": "entry", "id": "APP-1 Fix"}]
    assert ice_fields("github.reviews_for_me") == []  # not mine


def test_export_writes_only_stacks_to_on_ice_md(tmp_path: Path) -> None:
    config, db = _work()
    _ice_item(config, db, "ticket", "APP-1")
    notes = next(e for e in enabled(config, db, NOW) if e.name == "notes")
    notice = export_notes(notes.ctx, tmp_path, force=False)
    assert "1 other thing(s) on ice stay in the database only" in notice
    assert "APP-1" not in (tmp_path / "on-ice.md").read_text()


def test_notes_reads_its_tables_once_per_page_for_its_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builders: list[Builder] = []

    def counted(ctx: Context[NotesConfig], graph: Graph) -> Builder:
        builders.append(found := original(ctx, graph))
        return found

    original = notes_plugin._builder  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(notes_plugin, "_builder", counted)
    mine = [pr(n, merge_state="DIRTY") for n in (1, 2, 3)]
    built = dashboard(github=snapshot(mine=mine))
    assert all(
        any(a.name == ICE for a in i.actions) for i in panel(built, "github.conflicts").items
    )
    assert len(builders) == 2  # its panels', and its buttons'
