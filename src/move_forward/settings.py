"""The settings page, `/settings`: the config's settings as a form, saved back into the
config file.

The file stays the one source of truth. Saving changes only the lines of the keys that
changed (see `tomledit`), so comments and hand edits stay, and only once the result loads
as a config: an invalid one isn't written, and the page says why. `mfd serve`'s parent then
sees the file change and starts the server again with it (see `supervisor`).

The settings shown are the top level's, `[act_now]`'s and each enabled plugin's, of the
kinds a form can hold (numbers, true or false, text, lists); tables such as
`[github.teams]` stay for editing by hand. `db_path`, `port` and `https` are shown but not
changed here: changing them moves the server.
"""

import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError

from . import registry, render, tomledit
from .config import Config

PATH = "/settings"
Kind = Literal["int", "float", "bool", "text", "list"]
LOCKED = {"db_path", "port", "https"}
"""Top-level settings shown but not changed here."""
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

    @property
    def name(self) -> str:
        """The form field's name: `<section>:<key>`."""
        return f"{self.section or ''}:{self.key}"


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
        locked = section is None and key in LOCKED
        found.append(Setting(section, key, kind, getattr(model, key), help, locked))
    return found


def settings(config: Config) -> list[Setting]:
    """What the page shows: the top level's settings, `[act_now]`'s, then each enabled
    plugin's, in the order their models list them."""
    sections = {p.name for p in registry.PLUGINS} | {"act_now"}
    found = _fields(config, None, sections)
    act_now = config.act_now
    found += _fields(act_now, "act_now", {WEIGHTS})
    found += [
        Setting("act_now", f"{WEIGHTS}.{s.key}", s.kind, s.value, s.help)
        for s in _fields(act_now.weights, "act_now", set())
    ]
    for plugin in registry.PLUGINS:
        section = cast(BaseModel | None, getattr(config, plugin.name, None))
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
        return f"<textarea name='{e(setting.name)}' rows='3'{disabled}>{e(lines)}</textarea>"
    kind = "number' step='any" if setting.kind in ("int", "float") else "text"
    return f"<input type='{kind}' name='{e(setting.name)}' value='{e(setting.value)}'{disabled}>"


def page(config: Config, notice: str = "") -> str:
    """The form, a table of settings per section."""
    e = render.e
    rows: dict[str | None, list[str]] = {}
    for setting in settings(config):
        rows.setdefault(setting.section, []).append(
            f"<tr><th><label>{e(setting.key)}</label></th><td>{_input(setting)}"
            f"<p class='help'>{e(setting.help)}"
            + (" Not changed here: it moves the server." if setting.locked else "")
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
        f"<form method='post' action='{PATH}'>{tables}"
        "<p><button type='submit'>Save</button></p></form></article>"
    )
    return render.page("Settings", "settings", body)
