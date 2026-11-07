"""The github plugin's buttons: changes to my PRs on GitHub, through `gh`.

Every action takes the form fields of its button, finds the PR among my open ones as the last
refresh found them, and returns a one-line notice, with the PR as `touched` so it's fetched
again at once.
"""

import re

from ...api import ActionError, ActionHandler, ActionRequest, ActionResult, Ref
from ...api.kinds import PULL_REQUEST as PR
from ...api.shell import CommandError
from . import client
from .config import GitHubConfig
from .models import PullRequest
from .prs import primary_reviewers
from .snapshots import PrRepository

LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
"""A GitHub login: also what keeps one from reading as an option of `gh`."""


def _my_open_pr(req: ActionRequest[GitHubConfig]) -> PullRequest:
    """The PR the `pr` field names by URL, if it's one of my open ones."""
    url = req.field("pr")
    snapshot = PrRepository(req.ctx.db).load()
    found = next((pr for pr in snapshot.mine if pr.url == url), None) if snapshot else None
    if found is None:
        raise ActionError(f"{url} isn't one of my open PRs")
    return found


def _same(one: str, other: str | None) -> bool:
    """Whether two logins are the same person: GitHub ignores their case."""
    return other is not None and one.casefold() == other.casefold()


def primary_reviewer(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Make `input`, a login, the PR's primary reviewer, in place of any other. They're asked
    to review too, as they must approve, unless they've been asked or have reviewed it:
    asking again would ask for a review already given."""
    pr = _my_open_pr(req)
    login = req.field("input").strip()
    if not LOGIN_RE.fullmatch(login):
        raise ActionError(f"{login!r} isn't a GitHub login")
    if not req.ctx.config.has_primary_reviewer(pr.repo):
        raise ActionError(f"{pr.repo} isn't in primary_reviewer_repos")
    if _same(login, pr.author):
        raise ActionError("a PR's author can't be its primary reviewer")
    current = primary_reviewers(pr)
    if any(_same(login, reviewer) for reviewer in current):
        raise ActionError(f"{login} is already the primary reviewer")
    reviewed = [review.author for review in pr.reviews]
    asked = any(_same(login, other) for other in [*pr.requested_users, *reviewed])
    try:
        client.set_primary_reviewer(pr.url, login, current, request=not asked)
    except CommandError as e:
        raise ActionError(str(e)) from e
    return ActionResult(
        f"{login} is now the primary reviewer of {pr.repo}#{pr.number}",
        touched=[Ref(kind=PR, id=pr.url)],
    )


ACTIONS: dict[str, ActionHandler] = {"primary_reviewer": primary_reviewer}
