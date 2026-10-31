"""`mfd init`: ask for each setting on the command line and write the config file.

Every question has a default guessed from the machine (the current repo's GitHub remote,
`gh`'s idea of my teams, git's user.email), so pressing Enter through the questions gives
a working config on a typical setup. The answers are validated as a `Config` before the
file is written.
"""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from . import config as config_module
from .config import Config
from .shell import CommandError, run

Prompt = Callable[[str, str], str]
"""Asks a question with a default and returns the answer ("" means take the default)."""

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")

OPTIONAL_SETTINGS = """\

# Optional settings, with their defaults. Uncomment to change.
# db_path = "~/.local/share/move-forward-dashboard/dashboard.db"
# refresh_seconds = 300                  # background refresh when nobody loads the page
# refresh_on_load_seconds = 60           # a page load refreshes data older than this
# history_days = 14                      # how far back merged and closed PRs are fetched
# commit_history_days = 180              # commit history for "paths I work on" and reviewers
# stale_review_hours = 24                # a review request this old can be nudged
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# review_checks = []                     # checks that report review status, not CI, e.g. ["review-policy"]
# required_owner_marker = ""             # CODEOWNERS comment marking required owners, e.g. "#required"
# ticket_link_prefix = ""                # PR body line that links the ticket, e.g. "Link"
# placeholder_labels = []                # template labels to replace, e.g. ["needs-team"]
# ping_first = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
# ping_follow_up = "Gentle bump on this review request"
# prune_days = 90                        # clear work finished this long ago from the notes
"""
TEAM_COMMENT = """\
# One [[teams]] table per GitHub team or review group you review for. Optional per team:
# slack_channel = "#team-a"              # where review pings for the team go
# jira_team_id = "..."                   # the team's id in Jira's team field (unused yet)
"""

OPTIONAL_JIRA_SETTINGS = """\
# token_env = "JIRA_API_TOKEN"            # else the macOS keychain item below
# keychain_service = "move-forward-dashboard-jira"
# status_in_progress = "In Progress"      # workflow names the transitions panel uses
# status_in_review = "In Review"
# status_closed = "Closed"
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]
"""


def console_prompt(question: str, default: str) -> str:
    """Ask on the terminal; the default (a guess from the machine) is shown in brackets."""
    suffix = f" [Enter keeps: {default}]" if default else ""
    return input(f"{question}{suffix}: ").strip()


def _quiet(args: list[str], cwd: Path | None = None) -> str:
    try:
        return run(args, cwd=cwd).strip()
    except (CommandError, FileNotFoundError):
        return ""


def guess_repo(cwd: Path) -> tuple[Path | None, str]:
    """The git checkout `cwd` is in and the GitHub org of its origin remote, if any."""
    top = _quiet(["git", "rev-parse", "--show-toplevel"], cwd)
    if not top:
        return None, ""
    match = GITHUB_REMOTE_RE.search(_quiet(["git", "remote", "get-url", "origin"], Path(top)))
    return Path(top), match.group(1) if match else ""


def guess_teams(org: str) -> list[str]:
    """My team slugs in `org`, from `gh` (needs the read:org scope; empty otherwise)."""
    if not org:
        return []
    jq = f'.[] | select(.organization.login == "{org}") | .slug'
    return _quiet(["gh", "api", "--paginate", "user/teams", "--jq", jq]).split()


def org_team_examples(org: str) -> list[str]:
    """A few of the org's team slugs, to show as examples; empty when they can't be read."""
    if not org:
        return []
    return _quiet(["gh", "api", f"orgs/{org}/teams?per_page=3", "--jq", ".[].slug"]).split()[:3]


def guess_email() -> str:
    return _quiet(["git", "config", "user.email"])


def _toml_value(value: object) -> str:
    """TOML for a string, int, or list of strings (JSON's quoting is valid TOML)."""
    return json.dumps(value)


def render_config(
    answers: dict[str, object], teams: list[dict[str, object]], jira: dict[str, object] | None
) -> str:
    """The config file: the answered settings, then the optional ones as comments."""
    lines = ["# move-forward-dashboard configuration. Paths may start with ~.", ""]
    lines += [f"{key} = {_toml_value(value)}" for key, value in answers.items()]
    text = "\n".join(lines) + "\n" + OPTIONAL_SETTINGS + "\n" + TEAM_COMMENT
    for team in teams:
        text += "\n[[teams]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in team.items())
    if jira:
        text += "\n[jira]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in jira.items())
        text += OPTIONAL_JIRA_SETTINGS
    else:
        text += (
            "\n# [jira]                                # uncomment to show tickets\n"
            '# site = "https://example.atlassian.net"\n# email = "me@example.com"\n'
            + OPTIONAL_JIRA_SETTINGS
        )
    return text


def _ask_list(prompt: Prompt, question: str, default: list[str]) -> list[str]:
    answer = prompt(question, ", ".join(default))
    if not answer:
        return default
    return [part.strip() for part in answer.split(",") if part.strip()]


def _ask(prompt: Prompt, question: str, default: str) -> str:
    return prompt(question, default) or default


def interview(prompt: Prompt, cwd: Path) -> str:
    """Ask every question and return the config file's text, validated."""
    repo, org_guess = guess_repo(cwd)
    print(
        "Examples are in parentheses. Where a value could be guessed from this machine"
        " (git, gh), it's in brackets and Enter keeps it.\n"
    )
    org = _ask(prompt, "GitHub organization, as in github.com/<org> (e.g. acme)", org_guess)
    slugs = _ask_list(
        prompt,
        "GitHub team slugs you review for, comma-separated; leave out org-wide teams"
        f" (e.g. {', '.join(org_team_examples(org) or ['team-a', 'team-b'])})",
        guess_teams(org),
    )
    teams: list[dict[str, object]] = []
    for slug in slugs:
        channel = _ask(
            prompt,
            f"Slack channel for review pings to {slug} (e.g. #team-{slug}; blank for none)",
            "",
        )
        teams.append({"github_slug": slug, **({"slack_channel": channel} if channel else {})})
    repos = _ask_list(
        prompt,
        "Main checkouts of the repos you work in, comma-separated (e.g. ~/src/app, ~/src/infra)",
        [str(repo)] if repo else [],
    )
    email = guess_email()
    domain = email.rsplit("@", 1)[1] if "@" in email else ""
    slack = _ask(
        prompt,
        "Slack workspace URL, for ping links (e.g. https://acme.slack.com; blank for none)",
        "",
    )
    email_domain = _ask(
        prompt, "Work email domain, to find reviewers' emails (e.g. acme.com)", domain
    )
    prefixes = _ask_list(
        prompt,
        "Prefixes of your branch names (e.g. jane/; blank: every local branch counts as yours)",
        [],
    )
    notes_dir = _ask(
        prompt,
        "Directory for the notes, i.e. the move-forward skill's work-notes.md and on-ice.md"
        " (e.g. ~/notes/move-forward; created if missing)",
        "",
    )
    if not notes_dir:
        raise ValueError("a notes directory is required")
    port = _ask(prompt, "Port for the local server (e.g. 8765)", str(config_module.DEFAULT_PORT))
    answers: dict[str, object] = {"org": org, "repos": repos, "notes_dir": notes_dir}
    if slack:
        answers["slack_workspace_url"] = slack
    if email_domain:
        answers["email_domain"] = email_domain
    if prefixes:
        answers["my_branch_prefixes"] = prefixes
    if port != str(config_module.DEFAULT_PORT):
        answers["port"] = int(port)
    jira: dict[str, object] | None = None
    site = _ask(prompt, "Jira site URL (e.g. https://acme.atlassian.net; blank to skip Jira)", "")
    if site:
        jira_email = _ask(prompt, "Jira account email (e.g. jane@acme.com)", email)
        jira = {"site": site, "email": jira_email}
    text = render_config(answers, teams, jira)
    Config.model_validate(tomllib.loads(text))  # a bad answer fails here, before writing
    return text


def init(path: Path, prompt: Prompt = console_prompt, cwd: Path | None = None) -> str:
    """Interview, write the file, and return what to do next."""
    text = interview(prompt, cwd or Path.cwd())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    next_steps = f"wrote {path}\nrun `mfd serve --open` to start"
    if "[jira]\nsite" in text:
        service = config_module.DEFAULT_JIRA_KEYCHAIN_SERVICE
        next_steps += (
            "\n\nFor Jira, create an API token at"
            " https://id.atlassian.com/manage-profile/security/api-tokens, then either"
            f" export {config_module.DEFAULT_JIRA_TOKEN_ENV} or store it in the keychain:\n"
            f"  security add-generic-password -s {service} -a <jira email> -w <token>"
        )
    return next_steps
