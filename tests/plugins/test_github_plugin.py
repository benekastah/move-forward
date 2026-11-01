"""The GitHub plugin's contract with the others: the `pr` and `person` entities it
publishes."""

from datetime import UTC, datetime

import pytest
from conftest import ME, NOW, pr, snapshot
from legacy import save_json_snapshot

from move_forward.db import Database
from move_forward.plugin import Context
from move_forward.plugins.github import client
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import (
    DerivedSnapshot,
    Review,
    ReviewerSuggestion,
    TeamPrScore,
    Thread,
    TimelineEvent,
)
from move_forward.plugins.github.people import PeopleRepository
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import DerivedRepository, PrRepository
from move_forward.plugins.github.tables import MIGRATIONS
from move_forward.repositories import KvRepository
from move_forward.shell import CommandError


def _context(**config: object) -> Context[GitHubConfig]:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    return Context(
        config=GitHubConfig.model_validate({"org": "acme", **config}),
        email="me@acme.com",
        teams=["core", "web"],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "github"),
    )


def test_pr_entities_carry_state_ownership_mentions_and_team_names() -> None:
    mine = pr(1, requested_teams=["web-reviewers"])
    merged = pr(2, state="MERGED")
    closed = pr(3)  # the closed list, not the PR's own state, says it's closed
    theirs = pr(4, author="alice")
    gh = snapshot(
        mine=[mine], merged_recent=[merged], closed_recent=[closed], direct_requests=[theirs, mine]
    )
    entities = {
        e.ref.id: e
        for e in GitHubPlugin().entities(_context(teams={"web": "web-reviewers"}), gh)
        if e.ref.kind == "pr"
    }
    assert [(e.state, e.attrs["mine"]) for e in entities.values()] == [
        ("open", True),
        ("merged", True),
        ("closed", True),
        ("open", False),
    ]
    first = entities[mine.url]
    assert first.attrs["requested_teams"] == ["web"]
    assert first.mentions == [mine.head, mine.title, mine.body]  # where ticket keys turn up
    assert first.attrs["review_blockers"] == []
    assert first.attrs["review_stale"] is True  # no review activity at all
    assert "review_blockers" not in entities[theirs.url].attrs


def test_i_am_a_person_and_so_is_everyone_with_a_known_email() -> None:
    ctx = _context()
    PeopleRepository(ctx.db).replace_imported({"alice": "a@x", ME: "me@x"})
    people = {
        e.ref.id: e.attrs
        for e in GitHubPlugin().entities(ctx, snapshot())
        if e.ref.kind == "person"
    }
    assert people == {ME: {"me": True, "email": "me@x"}, "alice": {"me": False, "email": "a@x"}}


def test_prs_are_fetched_with_when_they_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    states = {
        "https://pr/1": ("merged", datetime(2026, 6, 1, tzinfo=UTC)),
        "https://pr/2": ("open", None),
    }

    def pr_state(url: str) -> tuple[str, datetime | None]:
        if url not in states:
            raise CommandError("gh: not found")
        return states[url]

    monkeypatch.setattr(client, "pr_state", pr_state)
    fetched = GitHubPlugin().fetch(
        _context(), "pr", ["https://pr/1", "https://pr/2", "https://gone"]
    )
    assert [(e.ref.id, e.state, e.attrs["closed_at"]) for e in fetched] == [
        ("https://pr/1", "merged", "2026-06-01T00:00:00+00:00"),
        ("https://pr/2", "open", None),
    ]
    assert GitHubPlugin().fetch(_context(), "ticket", ["APP-1"]) == []


def test_the_snapshot_moves_from_json_and_round_trips() -> None:
    full = pr(
        1,
        labels=["bug", "ui"],
        requested_users=["alice"],
        requested_teams=["web", "core"],
        reviews=[Review(author="bob", state="APPROVED", submitted_at=NOW)],
        threads=[
            Thread(
                resolved=False,
                outdated=True,
                path="a.py",
                author="bob",
                body="hm",
                url="https://c/1",
                created_at=NOW,
            )
        ],
        events=[
            TimelineEvent(type="ReviewRequestedEvent", at=NOW, reviewer_kind="team", reviewer="web")
        ],
        files=["a.py", "b.py"],
        merged_at=NOW,
        in_merge_queue=True,
    )
    gh = snapshot(
        mine=[full, pr(2)],
        team_requests=[full.model_copy(update={"files": ["only/here.py"]})],
        merged_recent=[pr(3, state="MERGED")],
    )
    db = Database(":memory:")
    save_json_snapshot(db, "github", gh)
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    assert store.load() == gh
    smaller = snapshot(mine=[pr(4)], team_members=["x"])
    store.replace(smaller)
    assert store.load() == smaller
    assert db.query("SELECT * FROM github_pr_files") == []  # gone with their PRs


def test_derived_data_moves_from_json_and_round_trips() -> None:
    derived = DerivedSnapshot(
        suggestions={
            pr(1).url: [
                ReviewerSuggestion(login="bob", commits=3),
                ReviewerSuggestion(login="al", commits=1),
            ]
        },
        team_scores={pr(2).url: TeamPrScore(owned_files=2, required_files=1, my_files=0)},
    )
    db = Database(":memory:")
    save_json_snapshot(db, "github.derived", derived)
    db.migrate("github", MIGRATIONS)
    store = DerivedRepository(db)
    assert store.load() == derived
    store.replace(DerivedSnapshot())
    assert store.load() == DerivedSnapshot()


def test_a_pr_a_search_found_twice_is_kept_once_at_its_first_place() -> None:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    repo = PrRepository(db)
    repo.replace(snapshot(mine=[pr(1), pr(2), pr(1, title="again")]))
    loaded = repo.load()
    assert loaded is not None and [(p.number, p.title) for p in loaded.mine] == [
        (1, "PR 1"),
        (2, "PR 2"),
    ]
