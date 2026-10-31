"""The GitHub plugin's contract with the others: the `pr` entities it publishes."""

from conftest import ME, NOW, pr, snapshot

from move_forward.db import Database
from move_forward.plugin import Context
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.repositories import KvRepository


def _context(**config: object) -> Context[GitHubConfig]:
    db = Database(":memory:")
    return Context(
        config=GitHubConfig.model_validate({"org": "acme", **config}),
        email="me@acme.com",
        teams=["core", "web"],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "github"),
    )


def test_pr_entities_carry_state_ownership_tickets_and_team_names() -> None:
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
    assert [r.id for r in first.refs] == ["APP-1"]
    assert first.attrs["review_blockers"] == []
    assert first.attrs["review_stale"] is True  # no review activity at all
    assert "review_blockers" not in entities[theirs.url].attrs


def test_i_am_a_person() -> None:
    entities = GitHubPlugin().entities(_context(), snapshot())
    me = next(e for e in entities if e.ref.kind == "person")
    assert me.ref.id == ME and me.attrs == {"me": True}
