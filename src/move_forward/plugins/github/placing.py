"""Placing PRs fetched on their own (`client.fetch_prs`) into the snapshot's lists, as the
searches would have found them, so a refresh can fetch only the PRs that changed.

Each list's rule is its search's (see `client.collect`), read from the PR itself: mine open,
merged or closed; requesting my review, or my team's; reviewed by me before. A PR that no
longer fits a list leaves it; one that newly fits joins it at the end.
"""

from .models import GitHubSnapshot, PullRequest

LISTS = (
    "mine",
    "direct_requests",
    "reviewed_by_me",
    "team_requests",
    "merged_recent",
    "closed_recent",
)
"""The snapshot's lists of PRs, one per search."""


def _lists(pr: PullRequest, me: str, team_slugs: list[str], reviewed_before: bool) -> set[str]:
    """The snapshot lists `pr` belongs in."""
    mine = pr.author == me
    is_open = pr.state == "OPEN"
    found: set[str] = set()
    if mine:
        if is_open:
            found.add("mine")
        elif pr.state == "MERGED":
            found.add("merged_recent")
        else:
            found.add("closed_recent")
    if is_open and me in pr.requested_users:
        found.add("direct_requests")
    if is_open and not mine:
        if reviewed_before or any(review.author == me for review in pr.reviews):
            found.add("reviewed_by_me")
        if any(team in team_slugs for team in pr.requested_teams):
            found.add("team_requests")
    return found


def place(
    snapshot: GitHubSnapshot, fetched: list[PullRequest], team_slugs: list[str]
) -> GitHubSnapshot:
    """`snapshot` with each of `fetched` in the lists it now belongs in, and out of the rest."""
    by_url = {pr.url: pr for pr in fetched}
    reviewed = {pr.url for pr in snapshot.reviewed_by_me}
    belongs = {pr.url: _lists(pr, snapshot.login, team_slugs, pr.url in reviewed) for pr in fetched}
    update: dict[str, list[PullRequest]] = {}
    for name in LISTS:
        old: list[PullRequest] = getattr(snapshot, name)
        kept = [
            by_url.get(pr.url, pr) for pr in old if pr.url not in by_url or name in belongs[pr.url]
        ]
        present = {pr.url for pr in old}
        update[name] = kept + [
            pr for pr in fetched if name in belongs[pr.url] and pr.url not in present
        ]
    return snapshot.model_copy(update=update)
