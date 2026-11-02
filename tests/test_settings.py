"""The settings page: the config as a form, saved back into the file as written."""

import sys
import tomllib
from pathlib import Path
from typing import cast

from move_forward import config as config_module
from move_forward import settings

TEXT = """\
# my config
email = "me@x"   # who I am
port = 8765

[act_now]
size = 7

[github]
org = "acme"
# refresh_seconds = 300                 # how often it refreshes
review_checks = []  # checks that report review status
"""


def _saved(tmp_path: Path, form: dict[str, str]) -> tuple[str, str]:
    path = tmp_path / "config.toml"
    path.write_text(TEXT)
    path.chmod(0o600)
    notice = settings.save(path, config_module.load(path), form)
    return notice, path.read_text()


def _form(path_text: str = TEXT, **changes: str) -> dict[str, str]:
    """The form as the page fills it from `TEXT`'s config, with `changes`."""
    config = config_module.Config.model_validate(tomllib.loads(path_text))
    form: dict[str, str] = {}
    for setting in settings.settings(config):
        if setting.kind == "bool":
            form[setting.name] = "on" if setting.value else "off"  # as the page's form sends
        elif setting.kind == "list":
            form[setting.name] = "\n".join(str(v) for v in cast(list[object], setting.value))
        else:
            form[setting.name] = str(setting.value)
    return {**form, **changes}


def test_saving_changes_only_the_lines_of_what_changed(tmp_path: Path) -> None:
    notice, text = _saved(
        tmp_path,
        _form(
            **{
                "github:refresh_seconds": "600",
                "github:review_checks": "policy\nci",
                "act_now:weights.closes": "4",
                ":prune_days": "30",
            }
        ),
    )
    assert notice.startswith("saved ") and "github.refresh_seconds" in notice
    assert "refresh_seconds = 600                 # how often it refreshes" in text
    assert 'review_checks = ["policy", "ci"]  # checks that report review status' in text
    assert 'email = "me@x"   # who I am' in text and text.startswith("# my config\n")
    loaded = config_module.Config.model_validate(tomllib.loads(text))
    assert loaded.act_now.weights.closes == 4 and loaded.prune_days == 30
    if sys.platform != "win32":  # its mode is kept (Windows has none)
        assert (tmp_path / "config.toml").stat().st_mode & 0o777 == 0o600


def test_nothing_changed_writes_nothing(tmp_path: Path) -> None:
    notice, text = _saved(tmp_path, _form())
    assert notice == "nothing changed" and text == TEXT


def test_an_invalid_change_isnt_written(tmp_path: Path) -> None:
    notice, text = _saved(tmp_path, _form(**{"github:refresh_seconds": "1"}))  # under 10
    assert notice.startswith("not saved:") and text == TEXT
    notice, text = _saved(tmp_path, _form(**{"act_now:size": "seven"}))
    assert notice == "not saved: size must be a number" and text == TEXT


def test_the_port_and_where_the_database_is_arent_changed_here(tmp_path: Path) -> None:
    notice, text = _saved(tmp_path, _form(**{":port": "9999"}))
    assert notice == "nothing changed" and "port = 8765" in text
    shown = settings.page(config_module.Config.model_validate({"email": "me@x"}))
    assert "name=':port' value='8765' disabled" in shown and "moves the server" in shown


def test_a_form_without_a_setting_leaves_it_alone(tmp_path: Path) -> None:
    """A script posting some fields doesn't turn off the true-or-false settings it left out."""
    path = tmp_path / "config.toml"
    path.write_text('email = "me@x"\nhttps = true\n\n[act_now]\nsize = 7\n')
    notice = settings.save(path, config_module.load(path), {"act_now:size": "5"})
    assert notice.startswith("saved act_now.size") and "https = true" in path.read_text()
