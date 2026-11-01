"""notes' ice as other plugins see it: an `iced` entity per act of icing, hiding what it
covers, including what notes finds for it in the graph."""

from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.plugin import Context
from move_forward.plugins.notes.config import NotesConfig
from move_forward.plugins.notes.covers import covers
from move_forward.plugins.notes.models import Ice, IcedItem
from move_forward.plugins.notes.panels import entities
from move_forward.plugins.notes.plugin import NotesPlugin
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository
from move_forward.repositories import KvRepository

WIDGETS = "/src/widgets"
BRANCH = Ref(kind="branch", id=f"{WIDGETS}\tme/x")


def _pr(number: int, repo: str, head: str) -> Entity:
    return Entity(
        ref=Ref(kind="pr", id=f"https://github.com/{repo}/pull/{number}"),
        title=f"PR {number}",
        attrs={"repo": repo, "head": head},
    )


def _ice(ice_id: int, *branch_ids: str) -> Ice:
    items = [IcedItem(kind="branch", ref_id=b, sha="1") for b in branch_ids]
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


def test_covers_are_replaced_wholesale_or_for_one_ice() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    first = notes.add_ice("a", NOW.date(), None, _ice(0, BRANCH.id).items)
    second = notes.add_ice("b", NOW.date(), None, _ice(0, f"{WIDGETS}\tme/y").items)
    one, two = Ref(kind="pr", id="1"), Ref(kind="pr", id="2")
    notes.replace_covers({first: [one], second: [two]})
    notes.replace_covers({first: [two], second: []}, only=first)
    assert notes.covers() == {first: [two], second: [two]}
    notes.replace_covers({second: [one]})
    assert notes.covers() == {second: [one]}
    notes.delete_ice(second)
    assert notes.covers() == {}


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
