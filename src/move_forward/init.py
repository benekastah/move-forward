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
from .plugins.jira.config import DEFAULT_JIRA_KEYCHAIN_SERVICE, DEFAULT_JIRA_TOKEN_ENV
from .shell import CommandError, run

Prompt = Callable[[str, str], str]
"""Asks a question with a default and returns the answer ("" means take the default)."""

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")

OPTIONAL_SETTINGS = """\
# Optional settings, with their defaults. Uncomment to change.
# notes_dir = "~/.config/move-forward/notes"  # work-notes.md, on-ice.md, reviewer-map.md
# db_path = "~/.local/share/move-forward/dashboard.db"
# refresh_seconds = 300                  # background refresh when nobody loads the page
# refresh_on_load_seconds = 60           # a page load refreshes data older than this
# prune_days = 90                        # clear work finished this long ago from the notes
"""
OPTIONAL_GITHUB_SETTINGS = """\
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
OPTIONAL_GIT_SETTINGS = """\
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# commit_history_days = 180              # commit history for "directories I work in"
"""
OPTIONAL_SLACK_SETTINGS = """\
# ping_first = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
# ping_follow_up = "Gentle bump on this review request"
"""
OPTIONAL_JIRA_SETTINGS = """\
# token_env = "JIRA_API_TOKEN"            # else the macOS keychain item for your email
# keychain_service = "move-forward-jira"
# status_in_progress = "In Progress"      # workflow names the transitions panel uses
# status_in_review = "In Review"
# status_closed = "Closed"
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]
# [jira.teams]                            # a team's id in Jira's team field (unused yet)
# core = "12345"
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


def _table(name: str, values: dict[str, object], optional: str = "") -> str:
    body = "".join(f"{k} = {_toml_value(v)}\n" for k, v in values.items())
    return f"\n[{name}]\n{body}{optional}"


def render_config(
    answers: dict[str, object],
    github: dict[str, object],
    git: dict[str, object],
    slack: dict[str, object] | None,
    slack_teams: dict[str, object],
    jira: dict[str, object] | None,
) -> str:
    """The config file: a section per plugin, with the optional settings as comments."""
    lines = ["# move-forward configuration. Paths may start with ~.", ""]
    lines += [f"{key} = {_toml_value(value)}" for key, value in answers.items()]
    text = "\n".join(lines) + "\n" + OPTIONAL_SETTINGS
    text += _table("github", github, OPTIONAL_GITHUB_SETTINGS)
    text += _table("git", git, OPTIONAL_GIT_SETTINGS)
    if slack:
        text += _table("slack", slack, OPTIONAL_SLACK_SETTINGS)
    else:
        text += (
            "\n# [slack]                               # uncomment to link review pings\n"
            '# workspace_url = "https://example.slack.com"\n' + OPTIONAL_SLACK_SETTINGS
        )
    if slack_teams:
        text += _table("slack.teams", slack_teams)
    if jira:
        text += _table("jira", jira, OPTIONAL_JIRA_SETTINGS)
    else:
        text += (
            "\n# [jira]                                # uncomment to show tickets\n"
            '# site = "https://example.atlassian.net"\n' + OPTIONAL_JIRA_SETTINGS
        )
    return text


Check = Callable[[str], str]
"""Normalises an answer, or raises ValueError saying what's wrong with it."""

MAX_RETRIES = 5


def _ask(prompt: Prompt, question: str, default: str, check: Check | None = None) -> str:
    """Ask until the answer passes `check`; a bad answer prints why and asks again."""
    for _ in range(MAX_RETRIES):
        answer = prompt(question, default) or default
        if check is None:
            return answer
        try:
            return check(answer)
        except ValueError as e:
            print(f"  {e}; try again")
    raise ValueError(f"no usable answer for: {question}")


def _ask_list(
    prompt: Prompt, question: str, default: list[str], check: Check | None = None
) -> list[str]:
    """A comma-separated answer; each item goes through `check`."""

    def check_all(answer: str) -> str:
        parts = [part.strip() for part in answer.split(",") if part.strip()]
        return ", ".join(check(part) if check else part for part in parts)

    answer = _ask(prompt, question, ", ".join(default), check_all)
    return [part.strip() for part in answer.split(",") if part.strip()]


def _required(what: str) -> Check:
    def check(answer: str) -> str:
        if not answer.strip():
            raise ValueError(f"{what} is required")
        return answer.strip()

    return check


def _word(what: str) -> Check:
    """A GitHub org or team slug: letters, digits, `-`, `_`, `.`."""

    def check(answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError(f"{what} is required")
        if not re.fullmatch(r"[\w.-]+", answer):
            raise ValueError(f"{answer!r} isn't a {what}: use letters, digits, - and _")
        return answer

    return check


def _url(answer: str) -> str:
    answer = answer.strip().rstrip("/")
    if answer and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", answer):
        raise ValueError(f"{answer!r} isn't a URL starting with https://")
    return answer


def _email(answer: str) -> str:
    answer = answer.strip()
    if "@" not in answer or " " in answer:
        raise ValueError(f"{answer!r} isn't an email address")
    return answer


def _channel(answer: str) -> str:
    answer = answer.strip()
    if answer and " " in answer:
        raise ValueError(f"{answer!r} isn't a channel name (e.g. #team-a)")
    return answer


def _path(answer: str) -> str:
    answer = answer.strip()
    if not answer:
        raise ValueError("a path is required")
    if not Path(answer).expanduser().is_dir():
        raise ValueError(f"{answer} isn't a directory")
    return answer


def _branch_prefix(answer: str) -> str:
    if " " in answer.strip():
        raise ValueError(f"{answer!r} has a space; a branch prefix is like jane/")
    return answer.strip()


def _port(answer: str) -> str:
    if not answer.isdigit() or not 1 <= int(answer) <= 65535:
        raise ValueError(f"{answer!r} isn't a port number (1-65535)")
    return answer


def interview(prompt: Prompt, cwd: Path) -> str:
    """Ask every question and return the config file's text, validated."""
    repo, org_guess = guess_repo(cwd)
    print(
        "Examples are in parentheses. Where a value could be guessed from this machine"
        " (git, gh), it's in brackets and Enter keeps it.\n"
    )
    email = _ask(prompt, "Your work email (e.g. jane@acme.com)", guess_email(), _email)
    org = _ask(
        prompt,
        "GitHub organization, as in github.com/<org> (e.g. acme)",
        org_guess,
        _word("GitHub organization"),
    )
    slugs = _ask_list(
        prompt,
        "GitHub team slugs you review for, comma-separated; leave out org-wide teams"
        f" (e.g. {', '.join(org_team_examples(org) or ['team-a', 'team-b'])})",
        guess_teams(org),
        _word("team slug"),
    )
    channels: dict[str, object] = {}
    for slug in slugs:
        channel = _ask(
            prompt,
            f"Slack channel for review pings to {slug} (e.g. #team-{slug}; blank for none)",
            "",
            _channel,
        )
        if channel:
            channels[slug] = channel
    repos = _ask_list(
        prompt,
        "Main checkout of each repo you work in, comma-separated; other worktrees are found"
        " from it (e.g. ~/src/app, ~/src/infra)",
        [str(repo)] if repo else [],
        _path,
    )
    slack = _ask(
        prompt,
        "Slack workspace URL, for ping links; it's under the workspace name in Slack's menu"
        " (e.g. https://acme.slack.com; blank for none)",
        "",
        _url,
    )
    prefixes = _ask_list(
        prompt,
        "Prefixes of your branch names (e.g. jane/; blank: every local branch counts as yours)",
        [],
        _branch_prefix,
    )
    notes_dir = _ask(
        prompt,
        "Directory for the notes, i.e. the move-forward skill's work-notes.md and on-ice.md"
        " (created if missing)",
        str(config_module.DEFAULT_NOTES_DIR),
        _required("a notes directory"),
    )
    port = _ask(
        prompt,
        "Port for the local server (e.g. 8765)",
        str(config_module.DEFAULT_PORT),
        _port,
    )
    answers: dict[str, object] = {"email": email, "teams": slugs}
    if notes_dir != str(config_module.DEFAULT_NOTES_DIR):
        answers["notes_dir"] = notes_dir
    git: dict[str, object] = {"repos": repos}
    if prefixes:
        git["my_branch_prefixes"] = prefixes
    if port != str(config_module.DEFAULT_PORT):
        answers["port"] = int(port)
    site = _ask(
        prompt, "Jira site URL (e.g. https://acme.atlassian.net; blank to skip Jira)", "", _url
    )
    text = render_config(
        answers,
        {"org": org},
        git,
        {"workspace_url": slack} if slack else None,
        channels,
        {"site": site} if site else None,
    )
    Config.model_validate(tomllib.loads(text))  # a bad answer fails here, before writing
    return text


def init(path: Path, prompt: Prompt = console_prompt, cwd: Path | None = None) -> str:
    """Interview, write the file, and return what to do next."""
    text = interview(prompt, cwd or Path.cwd())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    next_steps = f"wrote {path}\nrun `mfd serve --open` to start"
    if "\n[jira]\n" in text:
        service = DEFAULT_JIRA_KEYCHAIN_SERVICE
        next_steps += (
            "\n\nFor Jira, create an API token at"
            " https://id.atlassian.com/manage-profile/security/api-tokens, then either"
            f" export {DEFAULT_JIRA_TOKEN_ENV} or store it in the keychain:\n"
            f"  security add-generic-password -s {service} -a <jira email> -w <token>"
        )
    return next_steps
