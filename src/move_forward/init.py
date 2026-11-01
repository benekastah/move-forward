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
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from . import asking, registry
from . import config as config_module
from .asking import Asker, Complete, InitSetup, Prompt, choices, quiet
from .config import Config
from .plugin import Plugin
from .plugins.jira.config import DEFAULT_JIRA_KEYCHAIN_SERVICE, DEFAULT_JIRA_TOKEN_ENV

OPTIONAL_SETTINGS = """\
# Optional settings, with their defaults. Uncomment to change.
# db_path = "~/.local/share/move-forward/dashboard.db"
# db_backups = 10                        # copies kept in backups/, taken before migrations
# https = true                           # serve over HTTPS with `mfd cert`'s certificate
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


def console_prompt(question: str, default: str, complete: Complete | None = None) -> str:
    """Ask on the terminal; the default (a guess from the machine) is shown in brackets, and
    tab offers what `complete` gives, where readline is there to offer it."""
    suffix = f" [Enter keeps: {default}]" if default else ""
    with _completing(complete):
        return input(f"{question}{suffix}: ").strip()


@contextmanager
def _completing(complete: Complete | None) -> Generator[None]:
    """Tab completes with `complete` while the block runs: word by word, a word being what's
    between spaces and commas, so paths and team slugs complete whole."""
    try:
        import readline
    except ImportError:  # not on every platform; then no completion, just typing
        readline = None
    if complete is None or readline is None:
        yield
        return
    matches: list[str] = []

    def completer(text: str, state: int) -> str | None:
        if state == 0:
            matches[:] = complete(text)
        return matches[state] if state < len(matches) else None

    before, delims = readline.get_completer(), readline.get_completer_delims()
    readline.set_completer(completer)
    readline.set_completer_delims(" ,\t\n")
    binding = "bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete"
    readline.parse_and_bind(binding)
    try:
        yield
    finally:
        readline.set_completer(before)
        readline.set_completer_delims(delims)


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
    """Plugins `mfd init` offers, by name, comma-separated, each with those it requires."""
    by_name = {p.name: p for p in offered()}
    chosen = [part.strip() for part in answer.split(",") if part.strip()]
    for name in chosen:
        if name not in by_name:
            raise ValueError(f"no plugin named {name!r}; pick from {', '.join(by_name)}")
        if missing := [r for r in by_name[name].requires if r not in chosen]:
            raise ValueError(f"{name} needs {', '.join(missing)} too")
    return ", ".join(chosen)


def interview(prompt: Prompt, cwd: Path) -> str:
    """Ask every question and return the config file's text, validated."""
    ask = Asker(prompt)
    print(
        "Examples are in parentheses. Where a value could be guessed from this machine"
        " (git, gh), it's in brackets and Enter keeps it.\n"
    )
    email = ask("Your work email (e.g. jane@acme.com)", guess_email(), asking.email)
    setup = InitSetup(email=email, cwd=cwd)
    answer = ask(
        f"Plugins to set up, comma-separated ({', '.join(p.name for p in offered())})",
        ", ".join(p.name for p in offered() if p.init_suggested()),
        _plugins,
        choices([p.name for p in offered()]),
    )
    chosen = [p for p in offered() if p.name in answer.split(", ")]
    if not any(p.init_finds_teams for p in chosen):  # else that plugin asks for them
        setup.teams = ask.list(
            "Teams you're on or review for, by name, comma-separated (blank for none)",
            [],
            asking.word("team name"),
        )
    sections = {plugin.name: plugin.init_questions(ask, setup) for plugin in chosen}
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
