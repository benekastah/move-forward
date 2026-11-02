"""`mfd init`'s questions for `[github]`, with guesses from the current checkout and `gh`."""

import re
from pathlib import Path

from ...asking import Asker, InitSetup, checkout, choices, quiet, word
from .client import api
from .config import DEFAULT_HOST

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")
TEMPLATE = """\
# host = "github.com"                   # a GitHub Enterprise Server's host instead
# refresh_seconds = 300                 # how often it refreshes (GitHub's API is rate-limited)
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
    """The GitHub org of the checkout `cwd` is in, if any: from the remote its current
    branch tracks (`upstream` in a fork, often), else from the first remote on GitHub."""
    top = checkout(cwd)
    if top is None:
        return ""
    tracked = quiet(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], top).split("/")[0]
    remotes = quiet(["git", "remote"], top).split()
    for remote in sorted(remotes, key=lambda name: name != tracked):
        if match := GITHUB_REMOTE_RE.search(quiet(["git", "remote", "get-url", remote], top)):
            return match.group(1)
    return ""


def guess_teams(org: str, host: str = DEFAULT_HOST) -> list[str]:
    """My team slugs in `org`, from `gh` (needs the read:org scope; empty otherwise)."""
    if not org:
        return []
    jq = f'.[] | select(.organization.login == "{org}") | .slug'
    return quiet(api(host, "--paginate", "user/teams", "--jq", jq)).split()


def org_team_examples(org: str, host: str = DEFAULT_HOST) -> list[str]:
    """A few of the org's team slugs, to show as examples; empty when they can't be read."""
    if not org:
        return []
    return quiet(api(host, f"orgs/{org}/teams?per_page=3", "--jq", ".[].slug")).split()[:3]


ORG_URL_RE = re.compile(r"^(?:https?://)?([^/\s]+)/([^/\s]+)/?$")


def org(answer: str) -> str:
    """An org's name, or its URL pasted whole (`https://github.com/acme`, or an Enterprise
    server's), kept as `<host>/<org>` for a host other than github.com; see `host_and_org`."""
    if match := ORG_URL_RE.match(answer.strip()):
        host, name = match.groups()
        name = word("GitHub organization")(name)
        if host.lower() in ("github.com", "www.github.com"):
            return name
        return f"{host.lower()}/{name}"  # `gh` says so when it can't reach it
    return word("GitHub organization")(answer)


def host_and_org(answer: str) -> tuple[str, str]:
    """The host and org `org` gave back."""
    host, _, name = answer.rpartition("/")
    return (host or DEFAULT_HOST), name


def org_teams(org: str, host: str = DEFAULT_HOST) -> list[str]:
    """Every team slug in `org`, for tab completion; empty when they can't be read."""
    return quiet(api(host, "--paginate", f"orgs/{org}/teams", "--jq", ".[].slug")).split()


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    """The org (and, from an Enterprise org's URL, the host), and the teams I review for
    (which become the config's top-level teams)."""
    host, name = host_and_org(
        ask(
            "GitHub organization, or its URL (e.g. acme, https://github.com/acme, or an"
            " Enterprise server's https://github.acme.com/acme)",
            guess_org(setup.cwd),
            org,
        )
    )
    examples = org_team_examples(name, host) or ["team-a", "team-b"]
    setup.teams = ask.list(
        "GitHub team slugs you review for, comma-separated (tab completes); leave out"
        f" org-wide teams (e.g. {', '.join(examples)})",
        guess_teams(name, host),
        word("team slug"),
        choices(lambda: org_teams(name, host)),
    )
    return {"org": name, **({"host": host} if host != DEFAULT_HOST else {})}
