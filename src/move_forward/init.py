"""`mfd init`: ask for each setting on the command line and write the config file.

Core asks who I am, then which plugins to set up, then each chosen plugin asks for its own
section (`Plugin.init_questions`), then core asks the port. Every question has a default
guessed from the machine (the current repo's GitHub remote, `gh`'s idea of my teams, git's
user.email), so pressing Enter through the questions gives a working config on a typical
setup. Plugins left out are written as commented-out sections. The answers are validated
as a `Config` before the file is written.
"""

import json
import tomllib
from pathlib import Path
from typing import Any, cast

from . import asking, registry
from . import config as config_module
from .asking import Asker, InitSetup, Prompt, quiet
from .config import Config
from .plugin import Plugin
from .plugins.jira.config import DEFAULT_JIRA_KEYCHAIN_SERVICE, DEFAULT_JIRA_TOKEN_ENV

OPTIONAL_SETTINGS = """\
# Optional settings, with their defaults. Uncomment to change.
# db_path = "~/.local/share/move-forward/dashboard.db"
# db_backups = 10                        # copies kept in backups/, taken before migrations
# refresh_seconds = 300                  # background refresh when nobody loads the page
# refresh_on_load_seconds = 60           # a page load refreshes data older than this
# prune_days = 90                        # clear work finished this long ago from the notes
"""
OPTIONAL_ACT_NOW_SETTINGS = """\
# [act_now]                             # how Act now ranks what the plugins nominate
# size = 7                               # items shown
# per_plugin = 3                         # so one busy tool can't fill the list
# weights = { closes = 3, minutes = 2, hour = 1, waiting = 2 }  # what each signal adds
"""


def console_prompt(question: str, default: str) -> str:
    """Ask on the terminal; the default (a guess from the machine) is shown in brackets."""
    suffix = f" [Enter keeps: {default}]" if default else ""
    return input(f"{question}{suffix}: ").strip()


def guess_email() -> str:
    return quiet(["git", "config", "user.email"])


def offered() -> list[Plugin[Any, Any]]:
    """The plugins `mfd init` offers to set up: all but those always on (notes)."""
    return [p for p in registry.PLUGINS if not p.always_on]


def _toml_value(value: object) -> str:
    """TOML for a string, int, or list of strings (JSON's quoting is valid TOML)."""
    return json.dumps(value)


def _table(name: str, values: dict[str, object], optional: str = "") -> str:
    """A section: its keys, its optional settings commented out, then its own tables (a dict
    value, e.g. `teams` for `[slack.teams]`)."""
    body = "".join(
        f"{k} = {_toml_value(v)}\n" for k, v in values.items() if not isinstance(v, dict)
    )
    text = f"\n[{name}]\n{body}{optional}"
    for key, table in values.items():
        if isinstance(table, dict):
            text += _table(f"{name}.{key}", cast(dict[str, object], table))
    return text


def render_config(answers: dict[str, object], sections: dict[str, dict[str, object]]) -> str:
    """The config file: the top-level `answers`, then a section per offered plugin, the
    chosen ones' with their `sections` values and the others commented out, each with its
    optional settings as comments."""
    lines = ["# move-forward configuration. Paths may start with ~.", ""]
    lines += [f"{key} = {_toml_value(value)}" for key, value in answers.items()]
    text = "\n".join(lines) + "\n" + OPTIONAL_SETTINGS
    for plugin in offered():
        if plugin.name in sections:
            text += _table(plugin.name, sections[plugin.name], plugin.init_template)
        else:
            text += f"\n# [{plugin.name}]                      # uncomment to turn it on\n"
            text += plugin.init_example + plugin.init_template
    return text + "\n" + OPTIONAL_ACT_NOW_SETTINGS


def _plugins(answer: str) -> str:
    """A plugin `mfd init` offers, by name."""
    names = [p.name for p in offered()]
    if answer.strip() not in names:
        raise ValueError(f"no plugin named {answer.strip()!r}; pick from {', '.join(names)}")
    return answer.strip()


def interview(prompt: Prompt, cwd: Path) -> str:
    """Ask every question and return the config file's text, validated."""
    ask = Asker(prompt)
    print(
        "Examples are in parentheses. Where a value could be guessed from this machine"
        " (git, gh), it's in brackets and Enter keeps it.\n"
    )
    email = ask("Your work email (e.g. jane@acme.com)", guess_email(), asking.email)
    setup = InitSetup(email=email, cwd=cwd)
    chosen = ask.list(
        f"Plugins to set up, comma-separated ({', '.join(p.name for p in offered())})",
        [p.name for p in offered() if p.init_suggested()],
        _plugins,
    )
    sections = {
        plugin.name: plugin.init_questions(ask, setup)
        for plugin in offered()
        if plugin.name in chosen
    }
    if not setup.teams:
        setup.teams = ask.list(
            "Teams you're on or review for, by name, comma-separated (blank for none)",
            [],
            asking.word("team name"),
        )
    port = ask(
        "Port for the local server (e.g. 8765)", str(config_module.DEFAULT_PORT), asking.port
    )
    answers: dict[str, object] = {"email": setup.email, "teams": setup.teams}
    if port != str(config_module.DEFAULT_PORT):
        answers["port"] = int(port)
    text = render_config(answers, sections)
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
