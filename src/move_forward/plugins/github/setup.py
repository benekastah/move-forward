"""`mfd init`'s questions for `[github]`, with guesses from the current checkout and `gh`."""

import re
from pathlib import Path

from ...api import Asker, InitSetup, checkout, choices, quiet, word
from .client import api
from .config import DEFAULT_HOST

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")
TEMPLATE = """\
# host = "github.com"                   # a GitHub Enterprise Server's host instead
# refresh_seconds = 300                 # how often it refreshes (GitHub's API is rate-limited)
# search_scope = "org"                 # my PR searches: "org" (this org's repos) or "everywhere"
# history_days = 14                      # how far back merged and closed PRs are fetched
# policy_checks = []                     # checks that enforce PR policy, not CI, e.g. ["pr-policy"]
# ignored_reviewers = []                 # logins whose comments aren't feedback, e.g. a policy bot
# required_owner_marker = ""             # CODEOWNERS comment marking required owners, e.g. "#!required"
# ticket_link_prefixes = []             # PR body lines that link the ticket start with one, e.g. ["Link", "Closes"]
# placeholder_labels = []                # template labels to replace, e.g. ["needs-team"]
# default_team_reviewer_assignment = "team" # team requests count without an individual
# team_reviewer_assignment = {}          # by slug, e.g. { bacon = "individual" }
# primary_reviewer_repos = []            # repos whose PRs have a primary reviewer as an assignee, e.g. ["acme/widgets"]
# email_domains = []                     # reviewers' email domains, if not just your email's
# stale_review_hours = 24                # a review request this old can be nudged
# merge_queue_eviction_days = 2        # how long a merge-queue removal stays in CI failures
# stale_draft_days = 14                # my drafts untouched this long get a housekeeping line (0: off)
# quick_review_max_lines = 100           # a review of at most this many lines (and files below) takes minutes
# quick_review_max_files = 5
# hour_review_max_lines = 300            # within these, an hour; beyond, a day
# hour_review_max_files = 12
# split_pr_suggest_lines = 600           # my PRs this big "may need splitting"
# split_pr_lines = 800                   # ...and this big are "worth splitting"
# split_pr_labels = []                   # labels that mean worth splitting, e.g. ["size/XL"]
# [github.merge]
# method = "squash"                      # where a repo allows several; else GitHub's pick for me
# [github.teams]                         # a team's slug, where it isn't the team's name
# web = "web-reviewers"
"""
EXAMPLE = '# org = "example-org"\n'


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
