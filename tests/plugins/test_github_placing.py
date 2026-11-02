"""Placing PRs fetched on their own into github's lists, as its searches would find them."""

from conftest import ME, approved_review, pr, snapshot

from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.placing import place


def _numbers(gh: GitHubSnapshot, name: str) -> list[int]:
    return [p.number for p in getattr(gh, name)]


def test_a_pr_of_mine_moves_to_merged_or_closed_once_it_is() -> None:
    gh = snapshot(mine=[pr(1), pr(2), pr(3)])
    placed = place(gh, [pr(1, state="MERGED"), pr(2, state="CLOSED")], [])
    assert _numbers(placed, "mine") == [3]
    assert _numbers(placed, "merged_recent") == [1] and _numbers(placed, "closed_recent") == [2]


def test_a_fetched_pr_replaces_the_one_found_before_where_it_was() -> None:
    gh = snapshot(mine=[pr(1), pr(2)])
    placed = place(gh, [pr(1, title="renamed")], [])
    assert [p.title for p in placed.mine] == ["renamed", "PR 2"]


def test_review_requests_join_and_leave_by_whom_they_ask() -> None:
    asked = pr(5, author="alice", requested_users=[ME])
    for_team = pr(6, author="alice", requested_teams=["web-reviewers"])
    gh = snapshot(direct_requests=[pr(7, author="bob", requested_users=[ME])])
    placed = place(gh, [asked, for_team, pr(7, author="bob")], ["web-reviewers"])
    assert _numbers(placed, "direct_requests") == [5]  # 7 no longer asks me
    assert _numbers(placed, "team_requests") == [6]
    assert _numbers(placed, "mine") == []


def test_reviewed_by_me_stays_once_reviewed_until_it_closes() -> None:
    reviewed = pr(5, author="alice", reviews=[approved_review(ME)])
    gh = snapshot(reviewed_by_me=[pr(8, author="bob")])
    placed = place(gh, [reviewed, pr(8, author="bob")], [])
    assert _numbers(placed, "reviewed_by_me") == [8, 5]  # 8: reviewed before, whatever it shows now
    closed = place(placed, [pr(8, author="bob", state="MERGED")], [])
    assert _numbers(closed, "reviewed_by_me") == [5]
    assert _numbers(closed, "merged_recent") == []  # not mine


def test_prs_not_fetched_are_left_as_they_were() -> None:
    gh = snapshot(mine=[pr(1)], direct_requests=[pr(5, author="alice", requested_users=[ME])])
    assert place(gh, [], []) == gh
