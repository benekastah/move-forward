"""Dashboard configuration, read from a TOML file.

Top level: who I am, the teams I'm on, and the dashboard's own settings. Each plugin has a
section, e.g. `[github]`; a plugin is enabled when its section is there.
"""

import tomllib
from pathlib import Path
from typing import cast, overload

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import InitErrorDetails

from . import registry
from .act_now import ActNowConfig
from .db import DEFAULT_KEEP_BACKUPS
from .notifications import NotificationsConfig
from .people import PeopleConfig
from .strict import Strict

BASE_DIR = Path("~/.config/move-forward")
"""Where the config lives."""
DEFAULT_CONFIG_PATH = (BASE_DIR / "config.toml").expanduser()
DEFAULT_DB_PATH = Path("~/.local/share/move-forward/dashboard.db")
DEFAULT_PORT = 8765
DEFAULT_PRUNE_DAYS = 90

MOVED_SETTINGS = {
    "repos": "git.repos",
    "my_branch_prefixes": "git.my_branch_prefixes",
    "ignored_worktree_globs": "git.ignored_worktree_globs",
    "commit_history_days": "git.commit_history_days",
    "email_domains": "github.email_domains",
    "stale_review_hours": "github.stale_review_hours",
    "github.reviewer_history_days": "git.commit_history_days",
}
"""Settings that moved, from where they were (a top-level key, or a plugin's as
`<section>.<key>`) to where they are now (`<section>.<key>`), to say so instead of just "extra
input"."""
REMOVED_PLUGINS = {
    "vcard": (
        "the [vcard] plugin was removed: delete the section; contact types (Set type, or"
        " `mfd contact <address|@domain> <type>`) say who matters"
    ),
}
"""Built-in plugins that are gone, by name, and what to say to a config still naming one,
instead of "no plugin is named". An installed plugin of that name is read as any other."""

GONE_NOTIFICATIONS = {
    "dashboard.certificate_ending": "with HTTPS mode",
    "jira.comment_added": "(Jira tells of comments itself, e.g. through Slack)",
    "jira.mentioned": "(Jira tells of mentions itself, e.g. through Slack)",
}
"""Notification kinds that no longer exist, with why, to say so to a config still subscribed
to one instead of just "no such kind"."""


def _moved_settings(settings: dict[object, object]) -> list[str]:
    """What `MOVED_SETTINGS` says of the settings in the config file that moved."""
    found: list[str] = []
    for old, new in MOVED_SETTINGS.items():
        section, _, key = old.rpartition(".")
        table = settings.get(section) if section else settings
        if not isinstance(table, dict) or key not in table:
            continue
        new_section, new_key = new.split(".")
        where = f"`{key}` in [{section}]" if section else f"`{key}`"
        renamed = f" `{new_key}`" if new_key != key else ""
        found.append(f"{where} is now{renamed} in [{new_section}]")
    return found


class Config(Strict):
    """Everything the collectors and panels need to know about me and my machine."""

    email: str
    """My work email: Jira logs in with it, and its domain picks reviewers' emails from
    their commits."""
    teams: list[str] = Field(default_factory=list)
    """The teams I'm on or review for, by name. Plugins map a name to what their tool calls
    the team in their own `teams` table, e.g. `[github.teams]`."""
    db_path: Path = DEFAULT_DB_PATH
    """The SQLite database holding the plugins' tables (what they fetched, and the notes), and
    core's: when each plugin last fetched, priorities, tags, sessions and the like."""
    db_backups: int = DEFAULT_KEEP_BACKUPS
    """How many copies of the database to keep in `backups/` next to it. One is taken before
    migrations change the database; the oldest go first. 0 takes none."""
    port: int = DEFAULT_PORT
    """The localhost port `mfd serve` listens on."""
    prune_days: int = DEFAULT_PRUNE_DAYS
    """Work finished this long ago (merged PRs, closed tickets, done todos) is cleared from
    the notes by a daily job, as are pings, Act now's dismissals and pins, and lookups that
    found nothing (tried again) this old."""
    act_now: ActNowConfig = Field(default_factory=ActNowConfig)
    """`[act_now]`: how core ranks what the plugins nominate for Act now."""
    people: PeopleConfig = Field(default_factory=PeopleConfig)
    """`[people]`: the contact types offered, and the importance each gives what's about them."""
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    """`[notifications]`: which events to be told of in the browser; none until subscribed."""
    sections: dict[str, BaseModel] = Field(default_factory=dict[str, BaseModel])
    """The plugins' sections, by plugin name, each its plugin's `config_model`: a plugin is
    enabled when its section is there. An `always_on` plugin's is there without one in the
    file. Read with `section`."""

    @overload
    def section(self, name: str) -> BaseModel | None: ...
    @overload
    def section[M: BaseModel](self, name: str, model: type[M]) -> M | None: ...
    def section[M: BaseModel](self, name: str, model: type[M] | None = None) -> BaseModel | None:
        """The plugin's section, e.g. `[github]`; None when the plugin isn't enabled. With
        `model`, as that model: raises TypeError if it's another."""
        found = self.sections.get(name)
        if found is not None and model is not None and not isinstance(found, model):
            raise TypeError(f"[{name}] is a {type(found).__name__}, not a {model.__name__}")
        return found

    def with_section(self, name: str, section: BaseModel) -> "Config":
        """This config with the plugin's section set (or replaced)."""
        return self.model_copy(update={"sections": {**self.sections, name: section}})

    @model_validator(mode="before")
    @classmethod
    def _plugin_sections(cls, data: object) -> object:
        """Each key that isn't one of the top level's is a plugin's section, checked against
        its plugin's `config_model`; one no plugin has is an error naming the plugins."""
        if not isinstance(data, dict):
            return data
        settings = dict(cast(dict[str, object], data))
        if "sections" in settings:
            raise ValueError("`sections` isn't a setting: give each plugin its own table")
        plugins = {p.name: p for p in registry.PLUGINS}
        sections: dict[str, BaseModel] = {}
        errors: list[InitErrorDetails] = []
        for key in [k for k in settings if k not in cls.model_fields]:
            plugin = plugins.get(key)
            if plugin is None:
                continue  # not a plugin's: `_moved` names it, or it's refused as extra
            raw = settings.pop(key)
            try:
                sections[key] = plugin.config_model.model_validate(raw)
            except ValidationError as e:
                errors += [
                    InitErrorDetails(
                        type=err["type"],
                        loc=(key, *err["loc"]),
                        input=err["input"],
                        ctx=err.get("ctx", {}),
                    )
                    for err in e.errors()
                ]
        if errors:
            raise ValidationError.from_exception_data(cls.__name__, errors)
        for plugin in registry.PLUGINS:
            if plugin.always_on and plugin.name not in sections:
                sections[plugin.name] = plugin.config_model()
        settings["sections"] = sections
        return settings

    @model_validator(mode="before")
    @classmethod
    def _moved(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        settings = cast(dict[object, object], data)
        moved = _moved_settings(settings)
        if "notes_dir" in settings:
            moved.append(
                "`notes_dir` is gone; the notes live in the database now: import them once with"
                " `mfd notes import <dir>`"
            )
        if "https" in settings:
            moved.append(
                "`https` is gone; the dashboard serves plain HTTP on localhost only: delete the"
                " line"
            )
        if "refresh_on_load_seconds" in settings:
            moved.append(
                "`refresh_on_load_seconds` is gone; a page load no longer refreshes: each plugin"
                " refreshes on its own schedule, sooner when its tool has something new, and ↻"
                " and Refresh refresh now: delete the line"
            )
        if "refresh_seconds" in settings:
            moved.append("`refresh_seconds` is now per plugin: set it in [github], [git] or [jira]")
        plugins = [p.name for p in registry.PLUGINS]
        moved += [
            REMOVED_PLUGINS.get(key)
            or f"no plugin is named [{key}]; the plugins are {', '.join(plugins)}"
            for key in map(str, settings)
            if isinstance(settings[key], dict)
            and key not in cls.model_fields
            and key not in plugins
        ]
        if moved:
            raise ValueError("; ".join(moved))
        return settings

    @model_validator(mode="after")
    def _known_teams(self) -> "Config":
        """Every plugin section's teams table (`Plugin.teams_field`) names only top-level
        teams."""
        for plugin in registry.PLUGINS:
            if plugin.teams_field is None:
                continue
            teams: object = getattr(self.section(plugin.name), plugin.teams_field, None)
            if not isinstance(teams, dict):
                continue
            name = plugin.name
            unknown = sorted(set(cast(dict[str, object], teams)) - set(self.teams))
            if unknown:
                raise ValueError(
                    f"[{name}.{plugin.teams_field}] has {', '.join(unknown)}, which the"
                    " top-level teams don't"
                )
        return self

    @model_validator(mode="after")
    def _known_notifications(self) -> "Config":
        """`[notifications] subscribe` names only kinds that exist; one in `GONE_NOTIFICATIONS`
        says it's gone, and why, not that it's misspelled."""
        known = registry.notification_kinds()
        gone = [kind for kind in self.notifications.subscribe if kind in GONE_NOTIFICATIONS]
        if gone:
            raise ValueError(
                "; ".join(
                    f"[notifications] subscribe has {kind}, which is gone {GONE_NOTIFICATIONS[kind]}:"
                    " take it out"
                    for kind in gone
                )
            )
        bad = [kind for kind in self.notifications.subscribe if kind not in known]
        if bad:
            raise ValueError(f"[notifications] subscribe has {', '.join(bad)}: no such kind")
        return self

    @model_validator(mode="after")
    def _requirements(self) -> "Config":
        unmet = registry.unmet_requirements(self)
        if unmet:
            raise ValueError("; ".join(unmet))
        return self

    @model_validator(mode="after")
    def _token_services(self) -> "Config":
        """No two tokens (two plugins', or two of one plugin's accounts) are kept in one place,
        where storing one (`mfd token set`, Slack's Connect) would replace the other's."""
        if shared := registry.shared_token_services(self):
            raise ValueError("; ".join(shared))
        return self

    @field_validator("db_path")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()


def load(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load the config file, which may start with a byte-order mark (Notepad's "UTF-8 with
    BOM"), as TOML itself may not."""
    return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8-sig")))
