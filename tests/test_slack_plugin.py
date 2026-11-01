"""The Slack plugin: who to ping about my PRs, the Nudge panel, the Ping link and page."""

from datetime import timedelta

from conftest import (
    NOW,
    approved_review,
    dashboard,
    event,
    iced,
    pings,
    pr,
    snapshot,
    stored,
    thread,
    titles,
)

from move_forward import render
from move_forward.domain import Check, Ping, ReviewerUser, TeamChannel
from move_forward.graph import Graph
from move_forward.plugins.slack.config import DEFAULT_PING_FIRST, DEFAULT_PING_FOLLOW_UP
from move_forward.plugins.slack.models import Ping as SlackPing
from move_forward.plugins.slack.plugin import ping_page
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.registry import enabled


def _teams() -> dict[str, TeamChannel]:
    return {"web": TeamChannel(slug="web", channel_name="#team-web", channel_id="C1")}


def _users() -> dict[str, ReviewerUser]:
    return {"alice": ReviewerUser(login="alice", email="a@x", slack_id="U1")}


def test_ping_targets_links_and_messages() -> None:
    candidate = pr(1, requested_users=["alice", "bob"], requested_teams=["web", "unknown"])
    found = pings(
        github=snapshot(mine=[candidate]),
        users=_users(),
        teams=_teams(),
        slack_workspace_url="https://acme.slack.com/",
    )
    ping = found[candidate.url]
    assert ping.blockers == []
    by_name = {t.name: t for t in ping.targets}
    assert by_name["alice"].slack_link == "https://acme.slack.com/team/U1"
    assert by_name["bob"].slack_link is None and by_name["bob"].destination == "bob (DM)"
    assert by_name["web"].slack_link == "https://acme.slack.com/archives/C1"
    assert by_name["web"].destination == "#team-web (C1)"
    assert by_name["unknown"].destination == "team unknown"
    assert by_name["alice"].message == DEFAULT_PING_FIRST.format(url=candidate.url)


def test_follow_up_after_a_recorded_ping_and_skill_nudges_count_for_all_targets() -> None:
    candidate = pr(1, requested_users=["alice"], requested_teams=["web"])
    other = pr(2, requested_users=["alice"])
    found = pings(
        github=snapshot(mine=[candidate, other]),
        pings=[
            Ping(
                pr_url=candidate.url,
                target="user:alice",
                kind="first",
                sent_at=NOW - timedelta(days=1),
            ),
            Ping(pr_url=other.url, target="unknown (from work notes)", kind="first", sent_at=NOW),
        ],
    )
    targets = {t.name: t for t in found[candidate.url].targets}
    assert targets["alice"].ping_kind == "follow-up"
    assert targets["alice"].message == DEFAULT_PING_FOLLOW_UP
    assert targets["web"].ping_kind == "first"
    assert found[other.url].targets[0].ping_kind == "follow-up"


def test_nudge_blockers_and_staleness() -> None:
    fresh = pr(
        1,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(hours=2), reviewer="alice")],
    )
    stale = pr(
        2,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(days=2), reviewer="alice")],
    )
    policy_only = pr(
        3,
        requested_teams=["web"],
        checks=[Check(name="review-policy/owners", state="FAILURE")],
    )
    broken = pr(4, requested_users=["alice"], checks=[Check(name="tests", state="FAILURE")])
    threads = pr(5, requested_users=["alice"], threads=[thread()])
    approved = pr(6, requested_users=["alice"], reviews=[approved_review()])
    nobody = pr(7)
    draft = pr(8, is_draft=True, requested_users=["alice"])
    stored: dict[str, object] = {
        "github": snapshot(
            mine=[fresh, stale, policy_only, broken, threads, approved, nobody, draft]
        ),
        "review_checks": ["review-policy/owners"],
    }
    found = pings(**stored)
    assert found[fresh.url].blockers == [] and not found[fresh.url].stale
    assert found[stale.url].stale
    assert found[policy_only.url].blockers == []
    assert found[broken.url].blockers == ["failing checks"]
    assert found[threads.url].blockers == ["unresolved threads"]
    assert found[approved.url].blockers == ["already approved"]
    assert found[nobody.url].blockers == ["no reviewers requested"]
    assert draft.url not in found
    assert sorted(titles(dashboard(slack=True, **stored), "nudges")) == ["PR 2", "PR 3"]


def test_ping_links_appear_on_my_prs_wherever_they_are_and_only_with_slack() -> None:
    conflicted = pr(1, merge_state="DIRTY", requested_users=["alice"])
    for slack, expected in ((True, ["Ping…"]), (False, [])):
        built = dashboard(github=snapshot(mine=[conflicted]), **({"slack": True} if slack else {}))
        [item] = built.panel("conflicts").items
        assert [a.label for a in item.actions if a.href] == expected
    built = dashboard(
        slack=True, github=snapshot(mine=[pr(2, requested_users=["alice"], threads=[thread()])])
    )
    [link] = [a for a in built.panel("feedback").items[0].actions if a.href]
    assert link.label == "Ping…" and link.muted and link.title == "Not in order: unresolved threads"


def test_ping_page_has_a_form_per_target() -> None:
    candidate = pr(1, title="Fix <it>", requested_users=["alice"], requested_teams=["web"])
    ping = pings(github=snapshot(mine=[candidate]))[candidate.url]
    html = ping_page(ping, NOW)
    assert html.count("<form method='post' action='/actions/slack/mark_sent'>") == 2
    assert "name='target' value='user:alice'" in html and "Fix &lt;it&gt;" in html
    assert render.e(DEFAULT_PING_FIRST.format(url=candidate.url)) in html
    assert "data-reload" not in html


def test_old_pings_are_pruned() -> None:
    config, db = stored(slack=True)
    repo = SlackRepository(db)
    for when in (NOW - timedelta(days=100), NOW - timedelta(days=10)):
        repo.add_ping(SlackPing(pr_url="https://pr/1", target="user:a", kind="first", sent_at=when))
    [slack] = [e for e in enabled(config, db, NOW) if e.name == "slack"]
    assert slack.plugin.prune(slack.ctx, Graph()) == ["deleted 1 old ping(s)"]
    assert [p.sent_at for p in repo.pings()] == [NOW - timedelta(days=10)]


def test_iced_prs_get_no_pings() -> None:
    parked = pr(1, requested_users=["alice"])
    stack = iced(parked.head, "x")
    assert pings(github=snapshot(mine=[parked]), iced_stacks=[stack]) == {}
