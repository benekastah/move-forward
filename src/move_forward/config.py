"""Dashboard configuration, read from a TOML file.

Top level: who I am, the teams I'm on, and the dashboard's own settings. Each plugin has a
section, e.g. `[github]`; a plugin is enabled when its section is there.
"""

import tomllib
from pathlib import Path
from typing import cast

from pydantic import Field, field_validator, model_validator

from .db import DEFAULT_KEEP_BACKUPS
from .plugins.git.config import GitConfig
from .plugins.github.config import GitHubConfig
from .plugins.jira.config import JiraConfig
from .strict import Strict

BASE_DIR = Path("~/.config/move-forward")
"""Where the config and, by default, the notes live."""
DEFAULT_CONFIG_PATH = (BASE_DIR / "config.toml").expanduser()
DEFAULT_NOTES_DIR = BASE_DIR / "notes"
DEFAULT_DB_PATH = Path("~/.local/share/move-forward/dashboard.db")
DEFAULT_PORT = 8765
DEFAULT_REFRESH_SECONDS = 300
DEFAULT_REFRESH_ON_LOAD_SECONDS = 60
DEFAULT_PING_FIRST = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
DEFAULT_PING_FOLLOW_UP = "Gentle bump on this review request"
DEFAULT_PRUNE_DAYS = 90

MOVED_SETTINGS = {
    "repos": "git",
    "my_branch_prefixes": "git",
    "ignored_worktree_globs": "git",
    "commit_history_days": "git",
    "email_domains": "github",
    "stale_review_hours": "github",
}
"""Top-level settings that moved into a plugin's section, to say so instead of just "extra
input"."""


class SlackConfig(Strict):
    """`[slack]`: where review pings go. Optional; without it the ping page has no links."""

    workspace_url: str = ""
    """Base URL for Slack links, e.g. https://example.slack.com. Empty means no links."""
    ping_first: str = DEFAULT_PING_FIRST
    """The first review reminder; `{url}` is the PR."""
    ping_follow_up: str = DEFAULT_PING_FOLLOW_UP
    teams: dict[str, str] = Field(default_factory=dict)
    """Where review pings to a team go, e.g. `#team-a`, by its name in the top-level `teams`.
    Overrides the reviewer map."""


class Config(Strict):
    """Everything the collectors and panels need to know about me and my machine."""

    email: str
    """My work email: Jira logs in with it, and its domain picks reviewers' emails from
    their commits."""
    teams: list[str] = Field(default_factory=list)
    """The teams I'm on or review for, by name. Plugins map a name to what their tool calls
    the team in their own `teams` table, e.g. `[github.teams]`."""
    notes_dir: Path = DEFAULT_NOTES_DIR
    """The directory of work-notes.md, on-ice.md and reviewer-map.md: the move-forward skill's
    notes, which the dashboard reads on every refresh and writes todos and icing to."""
    db_path: Path = DEFAULT_DB_PATH
    db_backups: int = DEFAULT_KEEP_BACKUPS
    """How many copies of the database to keep in `backups/` next to it. One is taken before
    migrations change the database; the oldest go first. 0 takes none."""
    port: int = DEFAULT_PORT
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    """How often the background loop refreshes when nobody loads the page."""
    refresh_on_load_seconds: int = DEFAULT_REFRESH_ON_LOAD_SECONDS
    """A page load starts a background refresh if the last one began longer ago than this."""
    prune_days: int = DEFAULT_PRUNE_DAYS
    """Work finished this long ago (merged PRs, closed tickets, done todos) is cleared from
    the notes, and old pings from the database, by a daily job."""
    github: GitHubConfig | None = None
    git: GitConfig | None = None
    jira: JiraConfig | None = None
    slack: SlackConfig = Field(default_factory=SlackConfig)

    @model_validator(mode="before")
    @classmethod
    def _moved(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        settings = cast(dict[object, object], data)
        moved = [
            f"`{key}` is now in [{MOVED_SETTINGS[key]}]"
            for key in map(str, settings)
            if key in MOVED_SETTINGS
        ]
        if moved:
            raise ValueError("; ".join(moved))
        return settings

    @model_validator(mode="after")
    def _known_teams(self) -> "Config":
        sections: dict[str, dict[str, str]] = {"slack": self.slack.teams}
        if self.github:
            sections["github"] = self.github.teams
        if self.jira:
            sections["jira"] = self.jira.teams
        for name, teams in sections.items():
            unknown = sorted(set(teams) - set(self.teams))
            if unknown:
                raise ValueError(
                    f"[{name}.teams] has {', '.join(unknown)}, which the top-level teams don't"
                )
        return self

    @field_validator("notes_dir", "db_path")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()


def load(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load the config file."""
    with open(path, "rb") as f:
        return Config.model_validate(tomllib.load(f))
