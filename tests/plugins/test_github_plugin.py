"""The GitHub plugin's contract with the others: the `pr` and `person` entities it
publishes."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ME, NOW, pr, snapshot, stored
from legacy import save_json_snapshot

from move_forward import refresh
from move_forward.change_checks import ChangeChecks
from move_forward.db import Database
from move_forward.graph import Graph, Ref
from move_forward.page import Identity
from move_forward.plugin import (
    Context,
    RefRequest,
    RefreshRequest,
    Request,
)
from move_forward.plugins.github import client, derive
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import (
    Check,
    DerivedSnapshot,
    GitHubSnapshot,
    PullRequest,
    Review,
    ReviewerSuggestion,
    TeamPrScore,
    Thread,
    TimelineEvent,
)
from move_forward.plugins.github.people import PeopleRepository
from move_forward.plugins.github.plugin import (
    EVERYTHING,
    GitHubPlugin,
    pr_id_of,
    wants_reviewer,
)
from move_forward.plugins.github.snapshots import DerivedRepository, PrRepository
from move_forward.plugins.github.tables import MIGRATIONS
from move_forward.registry import Enabled
from move_forward.repositories import ChangeRepository, KvRepository, ProblemRepository
from move_forward.shell import CommandError

_BEFORE_REBUILD = MIGRATIONS[: [m.name for m in MIGRATIONS].index("0009_account_again")]
"""The migrations before the one that rebuilt the PR tables."""


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
        for e in GitHubPlugin().entities(
            Request(_context(teams={"web": "web-reviewers"}), Graph(), gh)
        )
        if e.ref.kind == "pull_request"
    }
    assert [(e.state, e.attrs["mine"]) for e in entities.values()] == [
        ("open", True),
        ("merged", True),
        ("closed", True),
        ("open", False),
    ]
    assert [e.lifecycle for e in entities.values()] == ["open", "done", "done", "open"]
    # What alice's PR names can't hide it from me: she wrote it.
    assert [e.mentions_hide for e in entities.values()] == [True, True, True, False]
    first = entities[mine.url]
    assert first.attrs["requested_teams"] == ["web"]
    assert first.attrs["head_oid"] == mine.head_oid  # for git, to see a checkout fall behind
    assert first.mentions == [mine.head, mine.title, mine.body]  # where ticket keys turn up
    assert first.attrs["review_blockers"] == []
    assert first.attrs["review_stale"] is True  # no review activity at all
    assert "review_blockers" not in entities[theirs.url].attrs


def test_i_am_a_person_and_so_is_everyone_with_a_known_email() -> None:
    ctx = _context()
    people = PeopleRepository(ctx.db)
    people.save_looked_up("alice", "a@x", NOW)
    people.save_looked_up(ME, "me@x", NOW)
    people = {
        e.ref.id: e.attrs
        for e in GitHubPlugin().entities(Request(ctx, Graph(), snapshot()))
        if e.ref.kind == "person"
    }
    assert people == {
        f"github.com/{ME}": {"handle": ME, "me": True, "email": "me@x"},
        "github.com/alice": {"handle": "alice", "me": False, "email": "a@x"},
    }


def test_pr_entity_review_blockers_check_the_requested_team_member() -> None:
    ctx = _context(team_reviewer_assignment={"bacon": "individual"})
    missing = pr(1, requested_teams=["bacon", "eggs"], requested_users=["erin"])
    shared = pr(2, requested_teams=["bacon", "eggs"], requested_users=["shared"])
    gh = snapshot(
        mine=[missing, shared],
        team_memberships={"acme/bacon": ["alex", "shared"], "acme/eggs": ["erin", "shared"]},
    )
    entities = {
        e.ref.id: e
        for e in GitHubPlugin().entities(Request(ctx, Graph(), gh))
        if e.ref.kind == "pull_request"
    }
    assert entities[missing.url].attrs["review_blockers"] == ["request a member of bacon"]
    assert entities[shared.url].attrs["review_blockers"] == []


def test_an_approved_pr_says_so_in_its_review_blockers() -> None:
    """slack reads this exact text to leave a PR's reviewers alone."""
    approved = pr(1, review_decision="APPROVED")
    [entity] = [
        e
        for e in GitHubPlugin().entities(Request(_context(), Graph(), snapshot(mine=[approved])))
        if e.ref.kind == "pull_request"
    ]
    assert entity.attrs["review_blockers"] == ["already approved"]


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
        RefRequest(_context(), "pull_request", ["https://pr/1", "https://pr/2", "https://gone"])
    )
    assert [(e.ref.id, e.state, e.attrs["closed_at"]) for e in fetched] == [
        ("https://pr/1", "merged", "2026-06-01T00:00:00+00:00"),
        ("https://pr/2", "open", None),
    ]


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
            TimelineEvent(
                type="ReviewRequestedEvent", at=NOW, reviewer_kind="team", reviewer="web"
            ),
        ],
        files=["a.py", "b.py"],
        file_lines=[0, 0],  # the JSON import predates the lines column
        merged_at=NOW,
        in_merge_queue=True,
        checks=[
            Check(
                name="PR policy: guidelines",
                state="FAILURE",
                description="Review required. PR must have approval from primary reviewer.",
            )
        ],
    )
    gh = snapshot(
        mine=[full, pr(2)],
        team_requests=[full.model_copy(update={"files": ["only/here.py"], "file_lines": [0]})],
        merged_recent=[pr(3, state="MERGED")],
    )
    db = Database(":memory:")
    save_json_snapshot(db, "github", gh)
    db.migrate("github", _BEFORE_REBUILD)
    assert db.query("SELECT login FROM github_account")[0]["login"] == gh.login
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    assert store.load() is None  # the rebuild requests a full collect
    store.replace(gh)
    assert store.load() == gh
    smaller = snapshot(mine=[pr(4)], team_members=["x"])
    store.replace(smaller)
    assert store.load() == smaller
    assert db.query("SELECT * FROM github_pr_files") == []  # gone with their PRs


def test_a_threads_comment_count_is_kept() -> None:
    thread = Thread(resolved=False, outdated=False, url="https://c/1", comment_count=4)
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    store.replace(snapshot(mine=[pr(1, threads=[thread])]))
    assert store.load() == snapshot(mine=[pr(1, threads=[thread])])


def test_one_person_can_belong_to_multiple_stored_github_teams() -> None:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    found = snapshot(
        team_memberships={
            "acme/bacon": ["alex", "shared"],
            "acme/eggs": ["shared"],
            "acme/empty": [],
        }
    )
    store.replace(found)
    assert store.load() == found
    assert [
        tuple(row)
        for row in db.query("SELECT team, login FROM github_team_memberships ORDER BY team, login")
    ] == [
        ("acme/bacon", "alex"),
        ("acme/bacon", "shared"),
        ("acme/eggs", "shared"),
    ]
    assert [
        row["team"] for row in db.query("SELECT team FROM github_membership_teams ORDER BY team")
    ] == [
        "acme/bacon",
        "acme/eggs",
        "acme/empty",
    ]


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


def test_github_says_who_i_am_by_my_login() -> None:
    assert GitHubPlugin().identity(Request(_context(), Graph(), snapshot())) == Identity(
        tool="GitHub", login=ME
    )
    assert GitHubPlugin().identity(Request(_context(), Graph(), None)) is None


def test_collect_fetches_only_the_prs_that_changed_until_a_full_collect_is_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context()
    plugin = GitHubPlugin()
    searched: list[datetime] = []
    fetched: list[list[client.PrId]] = []
    failing = False

    def collect(
        host: str,
        org: str,
        teams: list[str],
        days: int,
        now: datetime,
        scope: str = "org",
        require_member: Callable[[str], bool] | None = None,
    ) -> object:
        searched.append(now)
        return snapshot(mine=[pr(1), pr(2)])

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[object]:
        fetched.append(ids)
        if failing:
            raise CommandError("gh: Could not resolve to a PullRequest")
        return [pr(2, state="MERGED")]

    monkeypatch.setattr(client, "collect", collect)
    monkeypatch.setattr(client, "fetch_prs", fetch_prs)

    checks, changes = ChangeChecks(), ChangeRepository(ctx.db)

    def refresh(minutes: int, *changed: str) -> GitHubSnapshot:
        """A refresh after a change check saw `changed`, with `EVERYTHING` for a change it
        can't pin on a PR, as core gives it to `collect`."""
        at = replace(ctx, now=NOW + timedelta(minutes=minutes))
        prs = [c for c in changed if c != EVERYTHING]
        changes.checked("github", None, at.now, prs, EVERYTHING in changed)
        e = Enabled(plugin, at)
        taken = checks.take(e, None)
        gh = plugin.collect(RefreshRequest(at, Graph(), taken.full, taken.subjects))
        plugin.store(Request(at, Graph(), gh))
        checks.stored(e, taken)
        assert changes.get("github").subjects == set()
        return gh

    refresh(
        0, "https://github.com/acme/widgets/pull/2"
    )  # nothing stored to place them in yet: everything
    assert len(searched) == 1 and fetched == []
    gh = refresh(1, "https://github.com/acme/widgets/pull/2")
    assert fetched == [[client.PrId(repo="acme/widgets", number=2)]] and len(searched) == 1
    assert [p.number for p in gh.mine] == [1] and [p.number for p in gh.merged_recent] == [2]
    refresh(
        2, "https://github.com/acme/widgets/pull/2", EVERYTHING
    )  # a change not about a PR: everything
    assert len(searched) == 2 and len(fetched) == 1
    failing = True
    refresh(3, "https://github.com/acme/widgets/pull/2")  # fetching them failed: everything
    assert len(searched) == 3 and len(fetched) == 2
    failing = False
    refresh(
        9, "https://github.com/acme/widgets/pull/2"
    )  # refresh_seconds (300) since everything last ran
    assert len(searched) == 4 and len(fetched) == 2
    many = [f"https://github.com/acme/widgets/pull/{n}" for n in range(client.PAGE_SIZE + 1)]
    refresh(10, *many)  # more than one request should carry: everything
    assert len(searched) == 5 and len(fetched) == 2


def test_partial_collect_fetches_membership_for_a_new_individual_policy_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(team_reviewer_assignment={"bacon": "individual"})
    PrRepository(ctx.db).replace(snapshot(mine=[pr(1)]))

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[PullRequest]:
        return [pr(2, requested_teams=["bacon"], requested_users=["shared"])]

    def team_members(host: str, org: str, team: str) -> list[str]:
        assert (org, team) == ("acme", "bacon")
        return ["shared"]

    monkeypatch.setattr(client, "fetch_prs", fetch_prs)
    monkeypatch.setattr(client, "team_members", team_members)
    found = GitHubPlugin().collect(
        RefreshRequest(ctx, Graph(), False, frozenset({"https://github.com/acme/widgets/pull/2"}))
    )
    assert found.team_memberships["acme/bacon"] == ["shared"]


def test_a_partial_collect_keeps_my_teams_members_by_their_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(teams={"core": "core-squad"})  # a team whose slug isn't its name
    PrRepository(ctx.db).replace(
        snapshot(mine=[pr(1)], team_memberships={"acme/core-squad": ["ann"], "acme/x": ["zed"]})
    )

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[PullRequest]:
        return [pr(1)]

    monkeypatch.setattr(client, "fetch_prs", fetch_prs)
    found = GitHubPlugin().collect(
        RefreshRequest(ctx, Graph(), False, frozenset({"https://github.com/acme/widgets/pull/1"}))
    )
    assert found.team_members == ["ann"]  # mine, not acme/x's


def test_a_refresh_of_one_pr_keeps_the_rest_of_its_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = stored(github=snapshot(mine=[pr(1), pr(2)]))
    ChangeRepository(db).collected("github", 0, NOW)  # a full refresh just ran
    asked: list[list[client.PrId]] = []

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[object]:
        asked.append(ids)
        return [pr(1, title="Fixed it")]

    monkeypatch.setattr(client, "fetch_prs", fetch_prs)
    one = Ref(kind="pull_request", id=pr(1).url)
    refresh.refresh_subjects(config, db, [one], NOW)
    assert asked == [[client.PrId(repo="acme/widgets", number=1)]]
    kept = PrRepository(db).load()
    assert kept is not None
    assert [(p.number, p.title) for p in kept.mine] == [(1, "Fixed it"), (2, pr(2).title)]


def test_a_url_that_isnt_one_of_the_hosts_prs_isnt_fetched() -> None:
    assert pr_id_of("github.com", pr(1).url) == client.PrId(repo="acme/widgets", number=1)
    assert pr_id_of("github.com", "https://github.acme.com/a/b/pull/1") is None
    assert pr_id_of("github.com", "https://github.com/a/b/issues/1") is None


@pytest.mark.parametrize(
    ("fields", "repos", "wanted"),
    [
        ({}, [], True),
        ({"in_merge_queue": True}, [], False),
        ({"is_draft": True}, [], False),
        ({"is_draft": True}, ["ACME/widgets"], True),  # a draft may get its primary reviewer
    ],
)
def test_reviewers_are_suggested_where_one_may_yet_be_picked(
    fields: dict[str, object], repos: list[str], wanted: bool
) -> None:
    config = GitHubConfig(org="acme", primary_reviewer_repos=repos)
    assert wants_reviewer(config, pr(1, **fields)) is wanted


def test_after_collect_suggests_reviewers_only_where_one_may_be_picked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picked: list[int] = []

    def collect(*args: object, wants_reviewer: Callable[[PullRequest], bool]) -> DerivedSnapshot:
        mine = [pr(1), pr(2, in_merge_queue=True), pr(3, is_draft=True)]
        picked.extend(p.number for p in mine if wants_reviewer(p))
        return DerivedSnapshot()

    monkeypatch.setattr(derive, "collect", collect)
    ctx = _context()
    GitHubPlugin().after_collect(Request(ctx, Graph(), snapshot(mine=[pr(1)])))
    assert picked == [1]


def test_reviewers_whose_email_lookup_fails_are_one_problem_until_none_does(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    failing = {"bob", "carol"}

    def commit_email(host: str, repo: str, login: str, domains: list[str]) -> str | None:
        if login in failing:
            raise CommandError(f"no commits for {login}")
        return f"{login}@acme.com"

    monkeypatch.setattr(client, "commit_email", commit_email)

    def derived(*args: object, **kwargs: object) -> DerivedSnapshot:
        return DerivedSnapshot()

    monkeypatch.setattr(derive, "collect", derived)
    ctx = _context()
    gh = snapshot(mine=[pr(1, requested_users=["alice", "bob"]), pr(2, requested_users=["carol"])])
    for _ in range(2):  # tried again each refresh, but said once
        GitHubPlugin().after_collect(Request(ctx, Graph(), gh))
    said = "looking up the commit emails of bob, carol failed: no commits for bob"
    assert ProblemRepository(ctx.db).of([""]) == {"": [said]}
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [said]
    assert PeopleRepository(ctx.db).emails() == {"alice": "alice@acme.com"}
    failing.clear()
    GitHubPlugin().after_collect(Request(ctx, Graph(), gh))
    assert ProblemRepository(ctx.db).of([""]) == {}


def test_a_collect_of_some_prs_fetches_those_into_their_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[client.PrId]] = []

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[PullRequest]:
        asked.append(ids)
        return [pr(1, title="Renamed"), pr(3, author="alice", requested_users=[ME])]

    monkeypatch.setattr(client, "fetch_prs", fetch_prs)
    ctx = _context()
    PrRepository(ctx.db).replace(snapshot(mine=[pr(1), pr(2)]))
    urls = frozenset(pr(n).url for n in (1, 3, 4))  # 4: gone
    found = GitHubPlugin().collect(RefreshRequest(ctx, Graph(), False, urls))
    assert asked == [[client.PrId(repo="acme/widgets", number=n) for n in (1, 3, 4)]]
    assert [(p.number, p.title) for p in found.mine] == [(1, "Renamed"), (2, "PR 2")]
    assert [p.number for p in found.direct_requests] == [3]


@pytest.mark.parametrize(
    ("url", "stored"),
    [
        ("https://github.example.com/acme/widgets/pull/1", True),  # another host
        ("https://github.com/acme/widgets/issues/1", True),
        ("https://github.com/acme/widgets/pull/x", True),
        (pr(1).url, False),  # nothing stored yet to place it in
    ],
)
def test_a_collect_of_what_it_cant_fetch_alone_searches_everything(
    monkeypatch: pytest.MonkeyPatch, url: str, stored: bool
) -> None:
    searched: list[bool] = []

    def collect(*args: object) -> object:
        searched.append(True)
        return snapshot()

    def fetch_prs(host: str, ids: list[client.PrId]) -> list[object]:
        raise AssertionError("fetched alone")

    monkeypatch.setattr(client, "collect", collect)
    monkeypatch.setattr(client, "fetch_prs", fetch_prs)
    ctx = _context()
    if stored:
        PrRepository(ctx.db).replace(snapshot(mine=[pr(1)]))
    GitHubPlugin().collect(RefreshRequest(ctx, Graph(), False, frozenset({url})))
    assert searched == [True]


def test_the_daily_prune_looks_up_old_misses_again() -> None:
    ctx = _context()
    people = PeopleRepository(ctx.db)
    people.save_looked_up("bob", None, NOW.replace(year=2025))
    people.save_looked_up("alice", "a@x", NOW.replace(year=2025))
    assert GitHubPlugin().prune(Request(ctx, Graph(), None)) == [
        "will look up 1 reviewer email(s) again"
    ]
    assert people.emails() == {"alice": "a@x"}


def test_a_reviews_state_and_whether_it_has_text_are_kept() -> None:
    review = TimelineEvent(
        type="PullRequestReview", at=NOW, actor="bob", state="APPROVED", has_body=True
    )
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    store.replace(snapshot(mine=[pr(1, events=[review])]))
    assert store.load() == snapshot(mine=[pr(1, events=[review])])


def test_each_files_changed_lines_are_kept() -> None:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    store = PrRepository(db)
    changed = pr(1, files=["a.py", "b.py"], file_lines=[3, 40])
    store.replace(snapshot(mine=[changed]))
    assert store.load() == snapshot(mine=[changed])


def test_the_mentioned_list_round_trips_and_the_rebuild_asks_for_a_fresh_collect() -> None:
    db = Database(":memory:")
    db.migrate("github", _BEFORE_REBUILD)
    db.execute("INSERT INTO github_account VALUES (?)", ("me",))
    store = PrRepository(db)
    db.migrate("github", MIGRATIONS)
    assert store.load() is None  # no account: the next refresh collects everything
    store.replace(snapshot(mentioned=[pr(2, author="alice")]))
    loaded = store.load()
    assert loaded is not None and [p.number for p in loaded.mentioned] == [2]


def test_a_prs_labels_are_its_tags_but_its_shapes() -> None:
    labelled = pr(1, labels=["Garbage Day", "needs-team", "too-big"])
    entities = GitHubPlugin().entities(
        Request(
            _context(placeholder_labels=["needs-team"], split_pr_labels=["too-big"]),
            Graph(),
            snapshot(mine=[labelled]),
        )
    )
    [entity] = [e for e in entities if e.ref.kind == "pull_request"]
    assert entity.tags == ["Garbage Day"]


def test_merge_settings_are_kept_between_refreshes() -> None:
    settings = pr(
        1,
        merge_queue=True,
        merge_methods=["SQUASH", "REBASE"],
        default_merge_method="SQUASH",
        auto_merge_allowed=True,
        delete_branch_on_merge=True,
        auto_merge=True,
        auto_merge_method="SQUASH",
    )
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    PrRepository(db).replace(snapshot(mine=[settings]))
    kept = PrRepository(db).load()
    assert kept is not None and kept.mine == [settings]
