"""The Slack plugin: who to ping about my PRs, the Nudge panel, the Ping link and page."""

import html as html_module
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlencode

import pytest
from conftest import (
    NOW,
    Reviewer,
    approved_review,
    dashboard,
    event,
    iced,
    panel,
    pings,
    pr,
    snapshot,
    stored,
    thread,
    titles,
)

from move_forward import render, server
from move_forward.graph import Graph
from move_forward.plugin import Context, PageRequest, PruneRequest
from move_forward.plugins.github.models import Check
from move_forward.plugins.slack import plugin as slack_plugin
from move_forward.plugins.slack.config import (
    DEFAULT_PING_FIRST,
    DEFAULT_PING_FOLLOW_UP,
    SlackConfig,
)
from move_forward.plugins.slack.models import Ping, TeamChannel
from move_forward.plugins.slack.pings import Pinger
from move_forward.plugins.slack.plugin import ping_page
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.refresh import stored_graph
from move_forward.registry import enabled


def _teams() -> dict[str, TeamChannel]:
    return {"web": TeamChannel(slug="web", channel_name="#team-web", channel_id="C1")}


def _users() -> dict[str, Reviewer]:
    return {"alice": Reviewer(login="alice", email="a@x", slack_id="U1")}


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
    approved = pr(
        6, requested_users=["alice"], reviews=[approved_review()], review_decision="APPROVED"
    )
    nobody = pr(7)
    draft = pr(8, is_draft=True, requested_users=["alice"])
    stored: dict[str, object] = {
        "github": snapshot(
            mine=[fresh, stale, policy_only, broken, threads, approved, nobody, draft]
        ),
        "policy_checks": ["review-policy/owners"],
    }
    found = pings(**stored)
    assert found[fresh.url].blockers == [] and not found[fresh.url].stale
    assert found[stale.url].stale
    assert found[policy_only.url].blockers == ["failing policy checks"]
    assert found[broken.url].blockers == ["failing checks"]
    assert found[threads.url].blockers == ["unresolved threads"]
    assert found[approved.url].blockers == ["already approved"]
    assert found[nobody.url].blockers == ["no reviewer requested"]
    assert draft.url not in found
    built = dashboard(slack=True, **stored)
    assert titles(built, "slack.nudges") == ["PR 2"]
    nudged = [i.title for i in built.act_now() if "the reviewers" in i.title]
    assert sorted(nudged) == ["Ping the reviewers: PR 2"]


def test_ping_links_appear_on_my_prs_wherever_they_are_and_only_with_slack() -> None:
    conflicted = pr(1, merge_state="DIRTY", requested_users=["alice"])
    for slack, expected in ((True, ["Ping…"]), (False, [])):
        built = dashboard(github=snapshot(mine=[conflicted]), **({"slack": True} if slack else {}))
        [item] = built.panel("github.conflicts").items
        assert [a.label for a in item.actions if a.href] == expected
    built = dashboard(
        slack=True, github=snapshot(mine=[pr(2, requested_users=["alice"], threads=[thread()])])
    )
    [link] = [a for a in built.panel("github.feedback").items[0].actions if a.href]
    assert link.label == "Ping…" and link.muted and link.title == "Not in order: unresolved threads"


def test_ping_page_has_a_form_per_target() -> None:
    candidate = pr(1, title="Fix <it>", requested_users=["alice"], requested_teams=["web"])
    ping = pings(github=snapshot(mine=[candidate]))[candidate.url]
    html = ping_page(ping, NOW)
    [action] = set(re.findall(r"action='(/actions/slack/mark_sent\?[^']*)'", html))
    query = html_module.unescape(action).partition("?")[2]
    assert html.count(action) == 2
    assert server._back_to_page(query) == f"/slack/ping?{urlencode({'pr': candidate.url})}"  # pyright: ignore[reportPrivateUsage]
    assert "name='target' value='user:alice'" in html and "Fix &lt;it&gt;" in html
    assert render.e(DEFAULT_PING_FIRST.format(url=candidate.url)) in html
    assert "data-reload" not in html


def test_old_pings_are_pruned() -> None:
    config, db = stored(slack=True)
    repo = SlackRepository(db)
    for when in (NOW - timedelta(days=100), NOW - timedelta(days=10)):
        repo.add_ping(Ping(pr_url="https://pr/1", target="user:a", kind="first", sent_at=when))
    [slack] = [e for e in enabled(config, db, NOW) if e.name == "slack"]
    assert slack.plugin.prune(PruneRequest(slack.ctx, Graph())) == ["deleted 1 old ping(s)"]
    assert [p.sent_at for p in repo.pings()] == [NOW - timedelta(days=10)]


def test_iced_prs_get_no_pings() -> None:
    parked = pr(1, requested_users=["alice"])
    stack = iced(parked.head, "x")
    assert pings(github=snapshot(mine=[parked]), iced_stacks=[stack]) == {}


def test_a_team_with_no_channel_gets_one_from_team_channel() -> None:
    mine = pr(1, requested_teams=["web", "core"])
    found = pings(
        github=snapshot(mine=[mine]),
        teams=_teams(),  # web has a channel; core doesn't
        team_channel="#team-{team}",
    )
    destinations = {t.name: t.destination for t in found[mine.url].targets}
    assert destinations["core"] == "#team-core (guessed from team_channel)"
    assert destinations["web"].startswith("#team-web (C1)")
    unguessed = pings(github=snapshot(mine=[mine]), teams=_teams())
    assert {t.name: t.destination for t in unguessed[mine.url].targets}["core"] == "team core"


def test_team_channel_must_be_a_channel_with_the_team_in_it() -> None:
    with pytest.raises(ValueError, match="must be a channel name with"):
        SlackConfig(team_channel="team-{team}")
    with pytest.raises(ValueError, match="must be a channel name with"):
        SlackConfig(team_channel="#team")
    assert SlackConfig(team_channel="#t-{team}").guessed_channel("web") == "#t-web"


def test_pings_are_worked_out_once_per_page_not_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    built_pingers: list[Pinger] = []

    def counted(ctx: Context[SlackConfig], graph: Graph) -> Pinger:
        built_pingers.append(found := original(ctx, graph))
        return found

    original = slack_plugin.pinger
    monkeypatch.setattr(slack_plugin, "pinger", counted)
    mine = [pr(n, merge_state="DIRTY", requested_users=["alice"]) for n in (1, 2, 3)]
    built = dashboard(slack=True, github=snapshot(mine=mine))
    assert len(built.panel("github.conflicts").items) == 3
    assert len(built_pingers) == 2  # the Nudge panel's, and the Ping links'


def test_the_nudge_panels_description_says_how_long_a_review_must_be_quiet() -> None:
    def description(hours: int) -> str:
        built = dashboard(slack=True, github=snapshot(mine=[pr(1)]), stale_review_hours=hours)
        return next(p for p in built.panels if p.key == "slack.nudges").description

    assert description(24).endswith("quiet for a day.")
    assert description(72).endswith("quiet for 3 days.")
    assert description(6).endswith("quiet for 6 hours.")


def test_the_workspace_url_must_be_https() -> None:
    assert SlackConfig(workspace_url="https://acme.slack.com").workspace_url
    assert SlackConfig(workspace_url="").workspace_url == ""
    for bad in ("http://acme.slack.com", "javascript:alert(1)", "acme.slack.com"):
        with pytest.raises(ValueError, match="https://"):
            SlackConfig(workspace_url=bad)


def test_open_slack_is_only_a_link_for_an_http_url() -> None:
    def form(link: str | None) -> str:
        target = slack_plugin.PingTarget(
            kind="user",
            name="alice",
            slack_link=link,
            destination="alice (DM)",
            ping_kind="first",
            message="hi",
        )
        ping = slack_plugin.PrPing(
            pr_url="https://x/pull/1", title="t", targets=[target], blockers=[], stale=True
        )
        return slack_plugin._target_form(ping, target, NOW)  # pyright: ignore[reportPrivateUsage]

    assert "href='https://acme.slack.com/archive/C1'" in form("https://acme.slack.com/archive/C1")
    assert "javascript" not in form("javascript:alert(1)")
    assert "no Slack link known" in form("javascript:alert(1)")


def test_failing_policy_checks_hold_a_nudge_and_appear_in_review_stage() -> None:
    policy = pr(
        1,
        requested_teams=["web"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(days=2), reviewer="web")],
        checks=[
            Check(name="CODEOWNERS", state="FAILURE"),
            Check(name="guidelines", state="ERROR"),
            Check(name="tests", state="SUCCESS"),
        ],
    )
    built = dashboard(
        slack=True,
        github=snapshot(mine=[policy]),
        policy_checks=["CODEOWNERS", "guidelines", "tests"],
    )
    assert panel(built, "slack.nudges").items == []
    [item] = panel(built, "github.fix_before_review").items
    assert "policy failing: CODEOWNERS, guidelines" in item.details


def test_no_ping_link_with_nobody_to_chase() -> None:
    approved = dashboard(
        slack=True,
        github=snapshot(
            mine=[
                pr(
                    1,
                    requested_users=["alice"],
                    reviews=[approved_review("bob")],
                    review_decision="APPROVED",
                )
            ]
        ),
    )
    nobody = dashboard(slack=True, github=snapshot(mine=[pr(2)]))
    not_required = dashboard(slack=True, github=snapshot(mine=[pr(3, review_decision=None)]))
    for built in (approved, nobody, not_required):
        items = [i for p in built.panels for i in p.items]
        assert items and not any(
            a.href and "/slack/ping" in a.href for i in items for a in i.actions
        )


def test_a_quiet_pr_with_nobody_requested_is_not_nudged() -> None:
    built = dashboard(slack=True, github=snapshot(mine=[pr(1, review_decision=None)]))
    assert built.panel("slack.nudges").items == []
    assert not any(
        "the reviewers" in i.title or "the reviewers" in str(i.details) for i in built.act_now()
    )


def test_a_nudge_without_a_target_counts_for_every_target_whatever_came_before_it() -> None:
    candidate = pr(1, requested_users=["alice", "bob"])
    found = pings(
        github=snapshot(mine=[candidate]),
        pings=[
            Ping(
                pr_url=candidate.url,
                target="unknown (from work notes)",
                kind="first",
                sent_at=NOW - timedelta(days=2),
            ),
            Ping(
                pr_url=candidate.url,
                target="user:alice",
                kind="first",
                sent_at=NOW - timedelta(days=1),
            ),
        ],
    )
    targets = {t.name: t for t in found[candidate.url].targets}
    assert targets["alice"].last_ping == NOW - timedelta(days=1)
    assert targets["bob"].ping_kind == "follow-up"  # the nudge still counts for bob
    assert targets["bob"].last_ping == NOW - timedelta(days=2)


def test_a_teams_channel_set_in_the_config_isnt_the_maps_old_channel() -> None:
    known = {"web": TeamChannel(slug="web", channel_name="#web-old", channel_id="C1")}
    config = SlackConfig(teams={"web": "#web-new"})
    [web] = slack_plugin._team_channels(config, known).values()  # pyright: ignore[reportPrivateUsage]
    assert (web.channel_name, web.channel_id) == ("#web-new", None)
    same = SlackConfig(teams={"web": "#web-old"})
    assert slack_plugin._team_channels(same, known)["web"].channel_id == "C1"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("message", ["Ping {pr}", "Look {", "{0}"])
def test_a_first_ping_that_names_more_than_its_url_is_refused(message: str) -> None:
    with pytest.raises(ValueError, match="can name only"):
        SlackConfig(ping_first=message)


def test_the_ping_page_comes_back_to_itself_whatever_the_pr_url_holds_and_says_what_happened() -> (
    None
):
    candidate = pr(1, requested_users=["alice"]).model_copy(
        update={"url": "https://github.com/acme/r+x/pull/1"}
    )
    ping = pings(github=snapshot(mine=[candidate]))[candidate.url]
    html = ping_page(ping, NOW, "marked as sent")
    [action] = set(re.findall(r"action='(/actions/slack/mark_sent\?[^']*)'", html))
    back = server._back_to_page(html_module.unescape(action).partition("?")[2])  # pyright: ignore[reportPrivateUsage]
    assert parse_qs(back.partition("?")[2])["pr"] == [candidate.url]
    assert "<p class='notice'>marked as sent</p>" in html


@pytest.mark.parametrize(
    "message", ["Ping {url.foo}", "Ping {url[0]}", "Ping {url:{x}}", "Ping {url!z}", "Ping {url:d}"]
)
def test_a_first_ping_reaching_into_its_url_is_refused(message: str) -> None:
    with pytest.raises(ValueError, match="can name only"):
        SlackConfig(ping_first=message)


def test_the_ping_page_says_what_its_last_button_did() -> None:
    candidate = pr(1, requested_users=["alice"])
    config, db = stored(github=snapshot(mine=[candidate]), slack=True)
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]
    query = {"pr": [candidate.url], "notice": ["marked the first ping to alice as sent"]}
    page = slack.plugin.pages()["ping"](PageRequest(slack.ctx, graph, query, "/slack/ping"))
    assert isinstance(page, str) and "marked the first ping to alice as sent" in page
