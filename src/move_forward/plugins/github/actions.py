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
from .prs import decisions, failed_runs, primary_reviewers
from .snapshots import PrRepository

LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?")
"""A GitHub login (with `_` for Enterprise Managed Users' `jdoe_acme`): also what keeps one
from reading as an option of `gh`."""


def _my_open_pr(req: ActionRequest[GitHubConfig]) -> PullRequest:
    """The PR the `pr` field names by URL, if it's one of my open ones."""
    url = req.field("pr")
    found = PrRepository(req.ctx.db).my_open_pr(url)
    if found is None:
        raise ActionError(f"{url} isn't one of my open PRs")
    return found


def _same(one: str, other: str | None) -> bool:
    """Whether two logins are the same person: GitHub ignores their case."""
    return other is not None and one.casefold() == other.casefold()


def _login(req: ActionRequest[GitHubConfig], pr: PullRequest) -> str:
    """The `input` field, if it could be asked about the PR: a login, and not its author's."""
    login = req.field("input").strip()
    if not LOGIN_RE.fullmatch(login):
        raise ActionError(f"{login!r} isn't a GitHub login")
    if _same(login, pr.author):
        raise ActionError("a PR's author can't review it")
    return login


def request_reviewer(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Ask `input`, a login, to review the PR, unless they've been asked already."""
    pr = _my_open_pr(req)
    login = _login(req, pr)
    if any(_same(login, other) for other in pr.requested_users):
        raise ActionError(f"{login} is already asked to review it")
    try:
        client.request_reviewer(pr.url, login)
    except CommandError as e:
        raise ActionError(str(e)) from e
    return ActionResult(
        f"asked {login} to review {pr.repo}#{pr.number}", touched=[Ref(kind=PR, id=pr.url)]
    )


def mark_ready(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Take the draft PR out of draft."""
    pr = _my_open_pr(req)
    if not pr.is_draft:
        raise ActionError(f"{pr.repo}#{pr.number} isn't a draft")
    try:
        client.mark_ready(pr.url)
    except CommandError as e:
        raise ActionError(str(e)) from e
    return ActionResult(
        f"{pr.repo}#{pr.number} is ready for review", touched=[Ref(kind=PR, id=pr.url)]
    )


def update_branch(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Bring the PR's branch up to date with its base, on GitHub, if it's behind."""
    pr = _my_open_pr(req)
    if pr.merge_state != "BEHIND":
        raise ActionError(f"{pr.repo}#{pr.number} isn't behind {pr.base}")
    try:
        client.update_branch(pr.url)
    except CommandError as e:
        raise ActionError(str(e)) from e
    return ActionResult(
        f"updated {pr.repo}#{pr.number} with {pr.base}", touched=[Ref(kind=PR, id=pr.url)]
    )


def primary_reviewer(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Make `input`, a login, the PR's primary reviewer, in place of any other. They're asked
    to review too, as they must approve, unless they've been asked or have approved it:
    asking again would ask for an approval already given. Any other last decision (changes
    requested, a dismissed approval) gets them asked; a comment after an approval doesn't."""
    pr = _my_open_pr(req)
    login = _login(req, pr)
    if not req.ctx.config.has_primary_reviewer(pr.repo):
        raise ActionError(f"{pr.repo} isn't in primary_reviewer_repos")
    current = primary_reviewers(pr)
    if any(_same(login, reviewer) for reviewer in current):
        raise ActionError(f"{login} is already the primary reviewer")
    approvers = [who for who, (_, state) in decisions(pr).items() if state == "APPROVED"]
    asked = any(_same(login, other) for other in [*pr.requested_users, *approvers])
    try:
        client.set_primary_reviewer(pr.url, login, current, request=not asked)
    except CommandError as e:
        raise ActionError(str(e)) from e
    return ActionResult(
        f"{login} is now the primary reviewer of {pr.repo}#{pr.number}",
        touched=[Ref(kind=PR, id=pr.url)],
    )


def rerun_failed(req: ActionRequest[GitHubConfig]) -> ActionResult:
    """Run the failed jobs of the PR's failing Actions runs again (`gh run rerun --failed`)."""
    pr = _my_open_pr(req)
    config = req.ctx.config
    runs = failed_runs(config.host, pr, config.policy_checks)
    if not runs:
        raise ActionError(f"{pr.repo}#{pr.number} has no failed Actions run that's over")
    refused: list[str] = []
    for run_id in runs:  # each, whatever another says
        try:
            client.rerun_failed(config.host, pr.repo, run_id)
        except CommandError as e:
            refused.append(f"run {run_id}: {e}")
    if len(refused) == len(runs):
        raise ActionError("; ".join(refused))
    notice = (
        f"running {pr.repo}#{pr.number}'s failed checks again ({len(runs) - len(refused)} run(s))"
    )
    return ActionResult(
        notice + "".join(f"; {r}" for r in refused), touched=[Ref(kind=PR, id=pr.url)]
    )


ACTIONS: dict[str, ActionHandler] = {
    "mark_ready": mark_ready,
    "primary_reviewer": primary_reviewer,
    "request_reviewer": request_reviewer,
    "rerun_failed": rerun_failed,
    "update_branch": update_branch,
}
