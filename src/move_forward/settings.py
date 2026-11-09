"""The settings page, `/settings`: the config's settings as a form, saved back into the
config file.

The file stays the one source of truth. Saving changes only the lines of the keys that
changed (see `tomledit`), so comments and hand edits stay, and only once the result loads
as a config: an invalid one isn't written, and the page says why. `mfd serve`'s parent then
sees the file change and starts the server again with it (see `supervisor`).

The settings shown are the top level's, `[act_now]`'s and each enabled plugin's, of the
kinds a form can hold (numbers, true or false, text, lists); tables such as
`[github.teams]` stay for editing by hand. `db_path`, `port` and `https` are shown but not
changed here: changing them moves the server. Nor are the settings that choose where a
credential is sent or which one is read, or what runs where (see `SECRET_LOCKED`): the page
is reached with the dashboard's cookie, and a page that could point the Jira site, say, at
another server could send it the API token.
"""

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

from . import registry, render, tomledit
from .config import Config

PATH = "/settings"
Kind = Literal["int", "float", "bool", "text", "list"]
LOCKED = {"db_path", "port", "https"}
"""Top-level settings shown but not changed here: changing them moves the server."""
SECRET_LOCKED: dict[str | None, set[str]] = {
    None: {"email"},
    "github": {"host"},
    "jira": {"site", "token_env", "keychain_service"},
    "mail": {"host", "port", "token_env", "keychain_service"},
    "slack": {"client_id", "workspace_url", "token_env", "keychain_service"},
    "git": {"repos", "github_hosts"},
}
"""Settings, by section, only the file changes: where a credential goes (a host, a site, a
client id, the account a token is keyed by) or comes from (an environment variable, a
keychain service), and which checkouts `git` runs commands in."""
NOTIFICATIONS = "notifications"
SUBSCRIBE = "subscribe"
"""`[notifications]`'s list of kinds, shown as a checkbox each."""
SUBSCRIBED_FORM = "notify_form"
"""A hidden field that says the form has the checkboxes, so one left unticked means off."""
NOTIFY_PREFIX = "notify:"
"""A checkbox's name is this and the kind."""
WEIGHTS = "weights"
"""`[act_now]`'s table of weights, shown a field per weight."""


@dataclass(frozen=True)
class Setting:
    """One field of the form."""

    section: str | None
    """Its TOML section, e.g. "github"; None for the top level."""
    key: str
    """Its key there; a weight's is `weights.<name>`."""
    kind: Kind
    value: object
    help: str
    locked: bool = False
    default: object = None
    """The field's default in code; None when it has none."""

    @property
    def name(self) -> str:
        """The form field's name: `<section>:<key>`."""
        return f"{self.section or ''}:{self.key}"


EXAMPLE_RE = re.compile(r"e\.g\. (?:`([^`]+)`|((?:https?://|#)[^\s;,]*[^\s.;,]))")
"""Finds a setting's example in its help: the backticked text after `e.g.`, or an unquoted
URL or channel."""


def example(setting: Setting) -> str | None:
    """What an empty field could hold, from the `e.g.` in the setting's help (config
    docstrings carry them): a list example, written as a TOML list, is one item per line,
    the way the form takes lists. None when the help has none."""
    match = EXAMPLE_RE.search(setting.help)
    if match is None:
        return None
    text = match.group(1) or match.group(2)
    if setting.kind != "list" or not text.startswith("["):
        return text
    try:
        items = cast(object, tomllib.loads(f"x = {text}")["x"])
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(items, list):
        return None
    return "\n".join(str(item) for item in cast(list[object], items))


def _kind(annotation: object) -> Kind | None:
    """The form field for a model field's type; None for one a form can't hold (a table, an
    optional value)."""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str or annotation is Path:
        return "text"
    if get_origin(annotation) is list and get_args(annotation) in ((str,), (Path,)):
        return "list"
    return None


def _fields(model: BaseModel, section: str | None, skip: set[str]) -> list[Setting]:
    found: list[Setting] = []
    for key, field in type(model).model_fields.items():
        if key in skip or (kind := _kind(field.annotation)) is None:
            continue
        help = " ".join((field.description or "").split())
        locked = (section is None and key in LOCKED) or key in SECRET_LOCKED.get(section, set())
        default = field.get_default(call_default_factory=True)
        found.append(Setting(section, key, kind, getattr(model, key), help, locked, default))
    return found


def settings(config: Config) -> list[Setting]:
    """What the page shows: the top level's settings, `[act_now]`'s, then each enabled
    plugin's, in the order their models list them."""
    sections = {p.name for p in registry.PLUGINS} | {"act_now", NOTIFICATIONS}
    found = _fields(config, None, sections)
    act_now = config.act_now
    found += _fields(act_now, "act_now", {WEIGHTS})
    found += [
        replace(s, key=f"{WEIGHTS}.{s.key}") for s in _fields(act_now.weights, "act_now", set())
    ]
    found += _fields(config.notifications, NOTIFICATIONS, {SUBSCRIBE})
    for plugin in registry.PLUGINS:
        section = config.section(plugin.name)
        if section is not None:
            found += _fields(section, plugin.name, set())
    return found


def _parse(setting: Setting, form: dict[str, str]) -> object:
    """The value the form gives `setting`; raises ValueError for one that isn't one."""
    raw = form.get(setting.name)
    if setting.kind == "bool":
        # A ticked box sends "on" before its hidden "off"; an unticked one only the "off";
        # a form without the field at all leaves it as it is.
        return setting.value if raw is None else raw == "on"
    if raw is None:
        return setting.value
    if setting.kind == "int":
        return int(raw.strip())
    if setting.kind == "float":
        number = float(raw.strip())
        return int(number) if number.is_integer() else number
    if setting.kind == "list":
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return raw.strip()


def _comparable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_comparable(v) for v in cast(list[object], value)]
    return value


def save(path: Path, config: Config, form: dict[str, str]) -> str:
    """Write the settings `form` changed into the config file at `path`, if the result
    loads; what was saved, or why not. Nothing is written unless all of it can be."""
    changes: dict[tuple[str | None, str], object] = {}
    weights: dict[str, object] = {}
    for setting in settings(config):
        if setting.locked:
            continue
        try:
            new = _parse(setting, form)
        except ValueError:
            return f"not saved: {setting.key} must be a number"
        if setting.key.startswith(f"{WEIGHTS}."):
            weights[setting.key.removeprefix(f"{WEIGHTS}.")] = new
        if _comparable(new) != _comparable(setting.value):
            changes[(setting.section, setting.key)] = new
    subscribed = _subscribed(config, form)
    if subscribed != config.notifications.subscribe:
        changes[(NOTIFICATIONS, SUBSCRIBE)] = subscribed
    if not changes:
        return "nothing changed"
    text = path.read_text()
    for (section, key), new in changes.items():
        if key.startswith(f"{WEIGHTS}."):
            text = tomledit.set_key(text, section, WEIGHTS, weights)
        else:
            text = tomledit.set_key(text, section, key, new)
    try:
        Config.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as e:
        return f"not saved: couldn't change the file in place ({e}); edit it by hand"
    except ValidationError as e:
        problems = "; ".join(err["msg"] for err in e.errors()[:3])
        return f"not saved: {problems}"
    _write(path, text)
    names = ", ".join(f"{s}.{k}" if s else k for s, k in changes)
    return f"saved {names}; the dashboard restarts with them"


def _subscribed(config: Config, form: dict[str, str]) -> list[str]:
    """The kinds ticked on the form, in the order the page lists them; a form without the
    checkboxes leaves the subscriptions as they are."""
    current = config.notifications.subscribe
    if SUBSCRIBED_FORM not in form:
        return current
    kinds = registry.notification_kinds(config)
    ticked = [kind for kind in kinds if form.get(f"{NOTIFY_PREFIX}{kind}") == "on"]
    # A kind of a plugin that isn't enabled isn't on the form, and stays as it was.
    return ticked + [kind for kind in current if kind not in kinds]


def _write(path: Path, text: str) -> None:
    """Replace the file in one step, keeping its permissions."""
    mode = path.stat().st_mode
    handle, draft = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w") as f:
            f.write(text)
        os.chmod(draft, mode)
        os.replace(draft, path)
    except BaseException:
        Path(draft).unlink(missing_ok=True)
        raise


def _input(setting: Setting) -> str:
    e = render.e
    disabled = " disabled" if setting.locked else ""
    if setting.kind == "bool":
        checked = " checked" if setting.value else ""
        return (
            f"<input type='checkbox' name='{e(setting.name)}'{checked}{disabled}>"
            f"<input type='hidden' name='{e(setting.name)}' value='off'{disabled}>"
        )
    if setting.kind == "list":
        lines = "\n".join(str(v) for v in cast(list[object], setting.value))
        hint = _placeholder(setting)
        return f"<textarea name='{e(setting.name)}' rows='3'{hint}{disabled}>{e(lines)}</textarea>"
    kind = "number' step='any" if setting.kind in ("int", "float") else "text"
    return (
        f"<input type='{kind}' name='{e(setting.name)}' value='{e(setting.value)}'"
        f"{_placeholder(setting)}{disabled}>"
    )


def _default_text(setting: Setting) -> str | None:
    """The setting's default as it is entered (a list one item per line); None when empty."""
    default = setting.default
    if default is None or default == "" or default == [] or default is PydanticUndefined:
        return None
    if isinstance(default, list):
        return "\n".join(str(v) for v in cast(list[object], default))
    return str(default)


def _placeholder(setting: Setting) -> str:
    """The attribute showing the setting's example in its empty field."""
    if setting.kind == "bool" or setting.locked:
        return ""
    text = _default_text(setting) or example(setting)
    if not text:
        return ""
    return f" placeholder='{render.e(text).replace(chr(10), '&#10;')}'"


ENABLE_SCRIPT = (
    "<script>(() => {"
    " const button = document.getElementById('allow-notifications');"
    " const say = document.getElementById('notification-permission');"
    " const show = () => { say.textContent = 'Notification' in window"
    "  ? `The browser says: ${Notification.permission}.` : 'This browser has no notifications.'; };"
    " button.addEventListener('click', async () => {"
    "  if ('Notification' in window) await Notification.requestPermission(); show(); });"
    " show(); })();</script>"
)


def _notifications(config: Config) -> str:
    """The notifications section: the browser's permission, and a checkbox per kind of event,
    grouped by where it comes from."""
    e = render.e
    subscribed = set(config.notifications.subscribe)
    groups: dict[str, list[str]] = {}
    for kind, says in registry.notification_kinds(config).items():
        group, _, name = kind.partition(".")
        checked = " checked" if kind in subscribed else ""
        groups.setdefault(group, []).append(
            f"<li><label><input type='checkbox' name='{e(NOTIFY_PREFIX + kind)}'{checked}>"
            f" {e(name)}</label> <span class='help'>{e(says)}</span></li>"
        )
    lists = "".join(
        f"<h3>{e(group)}</h3><ul>{''.join(rows)}</ul>" for group, rows in groups.items()
    )
    return (
        "<h2>Notifications</h2><p>Shown by the browser while a dashboard tab is open, and"
        " when you open one, for what happened while none was. None are on until ticked."
        " <button type='button' id='allow-notifications'>Allow in this browser</button>"
        " <span id='notification-permission' class='help'></span></p>"
        f"<input type='hidden' name='{SUBSCRIBED_FORM}' value='1'>{lists}"
    )


def _why_locked(setting: Setting) -> str:
    """The note on a setting this page doesn't change."""
    if setting.section is None and setting.key in LOCKED:
        return " Not changed here: it moves the server."
    return " Not changed here: it says where a credential goes or comes from, or where commands run, so only the config file sets it."


def page(config: Config, notice: str = "") -> str:
    """The form, a table of settings per section."""
    e = render.e
    rows: dict[str | None, list[str]] = {}
    for setting in settings(config):
        rows.setdefault(setting.section, []).append(
            f"<tr><th><label>{e(setting.key)}</label></th><td>{_input(setting)}"
            f"<p class='help'>{e(setting.help)}"
            + (_why_locked(setting) if setting.locked else "")
            + "</p></td></tr>"
        )
    tables = "".join(
        f"<h2>{e(f'[{section}]' if section else 'Top level')}</h2><table>{''.join(r)}</table>"
        for section, r in rows.items()
    )
    body = (
        (f"<p class='notice'>{e(notice)}</p>" if notice else "")
        + "<article class='panel padded settings'>"
        "<p>Saving writes the config file, keeping its comments, and the dashboard restarts"
        " with it. Tables like <code>[github.teams]</code> are edited in the file.</p>"
        f"<form method='post' action='{PATH}'>{tables}{_notifications(config)}"
        f"<p><button type='submit'>Save</button></p></form>{ENABLE_SCRIPT}</article>"
    )
    return render.page("Settings", "settings", body)
