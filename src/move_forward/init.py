"""`mfd init`: ask for each setting on the command line and write the config file.

Core asks who I am, then which plugins to set up, then each chosen plugin asks for its own
section (`Plugin.init_questions`), then core asks the port. Every question has a default
guessed from the machine (the current repo's GitHub remote, `gh`'s idea of my teams, git's
user.email), so pressing Enter through the questions gives a working config on a typical
setup. Plugins left out are written as commented-out sections, and every optional key not
answered is written commented out, from its config model: its default, and its docstring's
first sentence as its comment (`commented_defaults`). The answers are validated
as a `Config` before the file is written. `mfd init --example` writes the same for example
answers without asking (`example`).
"""

import re
import textwrap
import tomllib
from collections.abc import Collection, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo

from . import asking, registry, tomledit
from . import config as config_module
from .asking import Asker, Complete, InitSetup, Prompt, choices, quiet
from .config import Config
from .keychain import TokenFields
from .plugin import Plugin

LINE_WIDTH = 100
"""The widest line `mfd init` writes: a key's comment, above it, is wrapped to this. A key
whose value is wider still is written whole."""
_CODE_SPAN = re.compile(r"`[^`]*`")
_NO_BREAK = "\0"
"""What stands for a space inside a code span while wrapping: not whitespace to `textwrap`,
and in no docstring."""
_SENTENCE = re.compile(r"(.*?(?<![eE]\.g)(?<![iI]\.e)(?<!\betc)[.?!][)\]\"'`]*)(?=\s|$)")
"""A docstring's first sentence: up to a `.`, `?` or `!` (and a closing bracket or quote)
before a space, but not the `.` of `e.g.`, `i.e.` or `etc.`."""
_TABLE_NAMED = re.compile(r"^`\[[^\]]+\]`: ")
"""A docstring's opening `[act_now]`: naming the table it's about, which its header says."""
HAND_WRITTEN = frozenset({"notifications"})
"""Core's tables whose commented defaults `render_config` writes by hand: `[notifications]`,
which it writes uncommented instead when `mfd init` was asked to subscribe."""


def summary(description: str | None) -> str:
    """A key's comment: its docstring's first sentence on one line (see `_SENTENCE`), less an
    opening `[table]`: naming the table; the whole docstring when it has no sentence."""
    text = " ".join((description or "").split())
    if match := _SENTENCE.match(text):
        text = match[1]
    if named := _TABLE_NAMED.match(text):
        text = text[named.end() :]
        text = text[:1].upper() + text[1:]
    return text


def _comment(text: str) -> str:
    """`text` as comment lines, wrapped to `LINE_WIDTH`, but never inside a `code span`
    (see `_NO_BREAK`)."""
    kept = _CODE_SPAN.sub(lambda span: span[0].replace(" ", _NO_BREAK), text)
    lines = textwrap.wrap(
        kept,
        LINE_WIDTH,
        initial_indent="# ",
        subsequent_indent="# ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "".join(f"{line.replace(_NO_BREAK, ' ')}\n" for line in lines)


def _commented(line: str, description: str | None) -> str:
    """`line` with its docstring's `summary` above it as its comment, after a blank line so
    each key with one reads as an entry of its own; `line` alone if it has none."""
    if not (comment := summary(description)):
        return f"{line}\n"
    return f"\n{_comment(comment)}{line}\n"


def _toml_ready(value: object) -> object:
    """A value pydantic dumped as JSON, as TOML writes it: a whole float as an int (a weight
    of 3, not 3.0), and None left out of tables and lists, as TOML has none."""
    if isinstance(value, dict):
        items = cast(dict[str, object], value).items()
        return {key: _toml_ready(v) for key, v in items if v is not None}
    if isinstance(value, list):
        return [_toml_ready(v) for v in cast(list[object], value) if v is not None]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _toml_value(field: FieldInfo, value: object) -> str:
    """`value`, of `field`'s type, as a TOML value: through pydantic's JSON mode, so a model,
    an enum or a date is written as its section would read it; a path in `/`s, on Windows
    too."""
    if isinstance(value, Path):
        return tomledit.value(value.as_posix())
    adapter: TypeAdapter[object] = TypeAdapter(field.annotation or Any)
    dumped = adapter.dump_python(adapter.validate_python(value), mode="json", exclude_none=True)
    return tomledit.value(_toml_ready(dumped))


def _default(model: type[BaseModel], key: str, field: FieldInfo) -> object:
    """`key`'s default as the section would read it: for a token's place (`TokenFields`), the
    section's own, which its empty default stands for."""
    if issubclass(model, TokenFields) and key == "token_env":
        return model.default_token_env
    if issubclass(model, TokenFields) and key == "keychain_service":
        return model.default_keychain_service
    return field.get_default(call_default_factory=True)


def commented_defaults(
    model: type[BaseModel], skip: Collection[str] = (), shown: Mapping[str, object] | None = None
) -> str:
    """`model`'s optional keys but `skip`, commented out, each with its default (or its value in
    `shown`) and its docstring's `summary`. A table of tables by name (a field marked
    `tables`, mail's accounts) and a key whose default is None, not in `shown`, are left out."""
    shown = shown or {}
    text = ""
    for key, field in model.model_fields.items():
        if key in skip or field.is_required() or field.json_schema_extra == {"tables": True}:
            continue
        value = shown[key] if key in shown else _default(model, key, field)
        if value is not None:
            text += _commented(f"# {key} = {_toml_value(field, value)}", field.description)
    return text


def plugin_defaults(plugin: Plugin[Any, Any], values: Mapping[str, object]) -> str:
    """What `mfd init` writes, commented out, under the plugin's section of these `values`:
    its optional keys but those in `values`, as `init_shown` shows them, then its
    `init_tables` after a blank line, less the keys those write, and none when `values` has
    one of those tables (uncommented, it would be written twice)."""
    tables = {
        key
        for key in plugin.config_model.model_fields
        if f"[{plugin.name}.{key}]" in plugin.init_tables
    }
    shown = plugin.init_shown(values)
    keys = commented_defaults(plugin.config_model, {*values, *tables}, shown)
    if not plugin.init_tables or tables & values.keys():
        return keys
    return f"{keys}\n{plugin.init_tables}"


def _core_table_names() -> list[str]:
    """Core's own tables in `Config` (`[act_now]`, `[people]`): each a field whose type is a
    model, but those `HAND_WRITTEN`."""
    return [
        key
        for key, field in Config.model_fields.items()
        if key not in HAND_WRITTEN
        and isinstance(field.annotation, type)
        and issubclass(field.annotation, BaseModel)
    ]


def core_defaults(skip: Collection[str]) -> str:
    """Core's optional top-level keys but `skip` (those written already), commented out."""
    hidden = {*skip, *_core_table_names(), *HAND_WRITTEN, "sections"}
    return "\n# Optional settings, with their defaults. Uncomment to change.\n" + (
        commented_defaults(Config, hidden)
    )


def core_tables() -> str:
    """Core's own tables (`[act_now]` and the like), commented out, with their defaults."""
    text = ""
    for key in _core_table_names():
        field = Config.model_fields[key]
        model = cast(type[BaseModel], field.annotation)
        comment = _comment(summary(field.description))
        text += f"\n{comment}# [{key}]\n{commented_defaults(model)}"
    return text


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
# Browser notifications; none until you subscribe.
# [notifications]

# Kinds of event to be told of, e.g. "dashboard.source_failing"; the settings page lists them.
# subscribe = []

# How long a plugin fails before `source_failing` says so, in minutes.
# failing_minutes = 15

# Events older than this, in hours, are dropped untold.
# keep_hours = 24
"""


def render_config(
    answers: dict[str, object],
    sections: dict[str, dict[str, object]],
    subscribe: list[str] | None = None,
) -> str:
    """The config file: the top-level `answers`, then a section per offered plugin, the
    chosen ones' with their `sections` values and the others commented out, then core's own
    tables; each key not given commented out with its default (`commented_defaults`)."""
    lines = ["# move-forward configuration. Paths may start with ~.", ""]
    lines += [f"{key} = {tomledit.value(value)}" for key, value in answers.items()]
    text = "\n".join(lines) + "\n" + core_defaults(answers)
    for plugin in offered():
        if plugin.name in sections:
            values = sections[plugin.name]
            text += _table(plugin.name, values, plugin_defaults(plugin, values))
        else:
            text += f"\n# Uncomment to turn {plugin.title} on.\n# [{plugin.name}]\n"
            example = tomllib.loads(_uncommented(plugin.init_example))
            text += plugin.init_example + plugin_defaults(plugin, example)
    text += core_tables() + "\n"
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
