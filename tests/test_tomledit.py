"""Changing a config key in place, keeping the rest of the file as written."""

import tomllib
from pathlib import Path

from move_forward import tomledit

CONFIG = """\
# move-forward configuration.

email = "me@x"  # who I am
# port = 8765

[github]
org = "acme"
# refresh_seconds = 300                 # how often it refreshes
review_checks = ["policy"]  # a "#" in a comment

# [jira]
# site = "https://example.atlassian.net"
# refresh_seconds = 900
"""


def test_a_set_key_keeps_its_comment() -> None:
    text = tomledit.set_key(CONFIG, "github", "review_checks", ["policy", "ci # 2"])
    assert 'review_checks = ["policy", "ci # 2"]  # a "#" in a comment' in text
    assert tomllib.loads(text)["github"]["review_checks"] == ["policy", "ci # 2"]


def test_a_commented_default_is_set_in_its_own_section_not_a_left_out_one() -> None:
    text = tomledit.set_key(CONFIG, "github", "refresh_seconds", 600)
    assert "refresh_seconds = 600                 # how often it refreshes" in text
    assert "# refresh_seconds = 900" in text  # jira's, commented out, untouched
    top = tomledit.set_key(CONFIG, None, "port", 9000)
    assert tomllib.loads(top)["port"] == 9000 and "\nport = 9000\n" in top


def test_a_key_or_section_not_there_is_added() -> None:
    text = tomledit.set_key(CONFIG, "github", "history_days", 30)
    assert tomllib.loads(text)["github"]["history_days"] == 30
    text = tomledit.set_key(text, "act_now", "weights", {"closes": 4, "due": 1.5})
    assert tomllib.loads(text)["act_now"] == {"weights": {"closes": 4, "due": 1.5}}
    text = tomledit.set_key(text, None, "prune_days", 30)
    parsed = tomllib.loads(text)
    assert parsed["prune_days"] == 30 and parsed["email"] == "me@x"
    assert text.index("prune_days") < text.index("[github]")
    path = Path("/a b")  # `\\a b` on Windows, its backslash escaped in TOML as in JSON
    assert tomllib.loads(f"p = {tomledit.value(path)}")["p"] == str(path)
    assert tomledit.value(True) == "true"
