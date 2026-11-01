"""`mfd init`'s questions for `[github]`, with guesses from the current checkout and `gh`."""

import re
from pathlib import Path

from ...asking import Asker, InitSetup, checkout, quiet, word

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")
TEMPLATE = """\
# history_days = 14                      # how far back merged and closed PRs are fetched
# review_checks = []                     # checks that report review status, not CI, e.g. ["review-policy"]
# required_owner_marker = ""             # CODEOWNERS comment marking required owners, e.g. "#required"
# ticket_link_prefix = ""                # PR body line that links the ticket, e.g. "Link"
# placeholder_labels = []                # template labels to replace, e.g. ["needs-team"]
# email_domains = []                     # reviewers' email domains, if not just your email's
# stale_review_hours = 24                # a review request this old can be nudged
# reviewer_history_days = 180            # commit history for reviewer suggestions
# [github.teams]                         # a team's slug, where it isn't the team's name
# web = "web-reviewers"
"""


def guess_org(cwd: Path) -> str:
    """The GitHub org of the origin remote of the checkout `cwd` is in, if any."""
    top = checkout(cwd)
    if top is None:
        return ""
    match = GITHUB_REMOTE_RE.search(quiet(["git", "remote", "get-url", "origin"], top))
    return match.group(1) if match else ""


def guess_teams(org: str) -> list[str]:
    """My team slugs in `org`, from `gh` (needs the read:org scope; empty otherwise)."""
    if not org:
        return []
    jq = f'.[] | select(.organization.login == "{org}") | .slug'
    return quiet(["gh", "api", "--paginate", "user/teams", "--jq", jq]).split()


def org_team_examples(org: str) -> list[str]:
    """A few of the org's team slugs, to show as examples; empty when they can't be read."""
    if not org:
        return []
    return quiet(["gh", "api", f"orgs/{org}/teams?per_page=3", "--jq", ".[].slug"]).split()[:3]


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    """The org, and the teams I review for (which become the config's top-level teams)."""
    org = ask(
        "GitHub organization, as in github.com/<org> (e.g. acme)",
        guess_org(setup.cwd),
        word("GitHub organization"),
    )
    examples = org_team_examples(org) or ["team-a", "team-b"]
    setup.teams = ask.list(
        "GitHub team slugs you review for, comma-separated; leave out org-wide teams"
        f" (e.g. {', '.join(examples)})",
        guess_teams(org),
        word("team slug"),
    )
    return {"org": org}
