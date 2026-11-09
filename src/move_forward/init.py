"""`mfd init`: ask for each setting on the command line and write the config file.

Core asks who I am, then which plugins to set up, then each chosen plugin asks for its own
section (`Plugin.init_questions`), then core asks the port. Every question has a default
guessed from the machine (the current repo's GitHub remote, `gh`'s idea of my teams, git's
user.email), so pressing Enter through the questions gives a working config on a typical
setup. Plugins left out are written as commented-out sections. The answers are validated
as a `Config` before the file is written. `mfd init --example` writes the same for example
answers without asking (`example`).
"""

import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from . import asking, registry, tomledit
from . import config as config_module
from .asking import Asker, Complete, InitSetup, Prompt, choices, quiet
from .config import Config
from .plugin import Plugin

OPTIONAL_SETTINGS = """\
# Optional settings, with their defaults. Uncomment to change.
# db_path = "~/.local/share/move-forward/dashboard.db"
# port = 8765                            # where the local server listens
# db_backups = 10                        # copies kept in backups/, taken before migrations
# prune_days = 90                        # clear work finished this long ago from the notes
"""
OPTIONAL_ACT_NOW_SETTINGS = """\
# [people]                              # what people are to me
# contact_types = { customer = 1, partner = 1, vendor = 0 }  # the types offered, each with the importance it gives (0 to 2)

# [act_now]                             # how Act now ranks what the plugins nominate
# size = 7                               # items shown
# per_plugin = 3                         # so one busy tool can't fill the list
# weights = { closes = 3, minutes = 2, hour = 1, waiting = 2, importance = 3, due = 1, blocked = -4 }  # what each signal adds
# blocked_stale_days = 3                 # a blocked item untouched this long stops being ranked down (0: never)
# due_warning_days = 7                  # a due date starts raising an item this many days ahead
# default_snooze_days = 1                # days a snooze lasts (1 = until tomorrow)
# snooze_until_hour = 0                # the local hour a snooze ends at on its last day (9: 9am)
# snooze_days = { pull_request = 7 }     # ...for a kind of subject, e.g. a PR's review for a week
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


def _table(name: str, values: dict[str, object], optional: str = "") -> str:
    """A section: its keys, its optional settings commented out, then its own tables (a dict
    value, e.g. `teams` for `[slack.teams]`)."""
    body = "".join(
        f"{k} = {tomledit.value(v)}\n" for k, v in values.items() if not isinstance(v, dict)
    )
    text = f"\n[{name}]\n{body}{optional}"
    for key, table in values.items():
        if isinstance(table, dict):
            text += _table(f"{name}.{key}", cast(dict[str, object], table))
    return text


OPTIONAL_NOTIFICATION_SETTINGS = """\
# [notifications]                       # browser notifications; none until you subscribe
# subscribe = []                         # kinds, e.g. "dashboard.source_failing"; see the settings page
# failing_minutes = 15                   # how long a plugin fails before `source_failing` says so
# keep_hours = 24                        # events older than this are dropped untold
"""


def render_config(
    answers: dict[str, object],
    sections: dict[str, dict[str, object]],
    subscribe: list[str] | None = None,
) -> str:
    """The config file: the top-level `answers`, then a section per offered plugin, the
    chosen ones' with their `sections` values and the others commented out, each with its
    optional settings as comments."""
    lines = ["# move-forward configuration. Paths may start with ~.", ""]
    lines += [f"{key} = {tomledit.value(value)}" for key, value in answers.items()]
    text = "\n".join(lines) + "\n" + OPTIONAL_SETTINGS
    for plugin in offered():
        if plugin.name in sections:
            text += _table(plugin.name, sections[plugin.name], plugin.init_template)
        else:
            text += f"\n# [{plugin.name}]                      # uncomment to turn it on\n"
            text += plugin.init_example + plugin.init_template
    text += "\n" + OPTIONAL_ACT_NOW_SETTINGS + "\n"
    if subscribe:
        return text + _table("notifications", {"subscribe": subscribe})
    return text + OPTIONAL_NOTIFICATION_SETTINGS


EXAMPLE_ANSWERS: dict[str, object] = {"email": "me@example.com", "teams": ["my-team"]}
"""The top-level answers of `mfd init --example`."""


def example() -> str:
    """`mfd init --example`: the config `mfd init` would write for `EXAMPLE_ANSWERS`, with
    the section of each offered plugin `init_example_on` on, set to its `init_example`'s
    values, and the others commented out."""
    sections = {
        plugin.name: tomllib.loads(_uncommented(plugin.init_example))
        for plugin in offered()
        if plugin.init_example_on
    }
    return render_config(EXAMPLE_ANSWERS, sections)


def _uncommented(text: str) -> str:
    """`text`'s lines without the `# ` that comments each out."""
    return "".join(line.removeprefix("# ") for line in text.splitlines(keepends=True))


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


def _kind(answer: str, kinds: dict[str, str]) -> str:
    """A kind of event to subscribe to, or why it isn't one."""
    if answer not in kinds:
        raise ValueError(f"no notification {answer!r}; pick from {', '.join(kinds)}")
    return answer


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
    kinds = registry.notification_kinds_of([p.name for p in chosen])
    subscribe = ask.list(
        "Notifications to turn on, comma-separated (blank for none; the settings page lists them)",
        [],
        lambda kind: _kind(kind, kinds),
        choices(list(kinds)),
    )
    answers: dict[str, object] = {"email": setup.email, "teams": setup.teams}
    if port != str(config_module.DEFAULT_PORT):
        answers["port"] = int(port)
    text = render_config(answers, sections, subscribe)
    Config.model_validate(tomllib.loads(text))  # a bad answer fails here, before writing
    return text


def init(path: Path, prompt: Prompt = console_prompt, cwd: Path | None = None) -> str:
    """Interview, write the file, and return what to do next."""
    text = interview(prompt, cwd or Path.cwd())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    next_steps = f"wrote {path}\nrun `mfd serve --open` to start"
    config = Config.model_validate(tomllib.loads(text))
    for plugin in registry.PLUGINS:
        section = config.section(plugin.name)
        if section is None or not (tokens := plugin.tokens(section)):
            continue
        names = [f"{plugin.name}.{a}" if a else plugin.name for a in tokens]
        stores = " and ".join(
            f"`mfd token set {name}` (or export {t.token_env})"
            for name, t in zip(names, tokens.values(), strict=True)
        )
        if plugin.token_help:
            next_steps += f"\n\nFor {plugin.title}: {plugin.token_help}"
        else:
            verb = "stores its token" if len(names) == 1 else "store its tokens"
            next_steps += f"\n\nFor {plugin.title}: {stores} {verb}"
    return next_steps
