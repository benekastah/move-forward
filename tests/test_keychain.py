"""Tokens in the system's own secret store, and `mfd token`."""

import subprocess
import sys
from pathlib import Path

import pytest

from move_forward import cli, keychain


def test_on_macos_and_linux_the_store_is_asked_through_its_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[str]] = []

    def run(args: list[str], input: str | None = None) -> str:
        asked.append(args)
        return "s3cret\n"

    monkeypatch.setattr(keychain, "run", run)
    for platform, command in (("darwin", "security"), ("linux", "secret-tool")):
        monkeypatch.setattr(keychain.sys, "platform", platform)
        assert keychain.read("svc", "me@x") == "s3cret"
        assert asked[-1][0] == command and "svc" in asked[-1] and "me@x" in asked[-1]


def test_a_secret_is_written_on_stdin_never_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[tuple[list[str], str | None]] = []

    def run(args: list[str], input: str | None = None) -> str:
        written.append((args, input))
        return "s3cret\n"  # what reading it back gives

    monkeypatch.setattr(keychain, "run", run)
    for platform, command in (("darwin", "security"), ("linux", "secret-tool")):
        monkeypatch.setattr(keychain.sys, "platform", platform)
        keychain.write("svc", "me@x", "s3cret")
        args, stdin = written[-2]
        assert args[0] == command and not any("s3cret" in a for a in args)
        assert stdin is not None and "s3cret" in stdin and "svc" in stdin + " ".join(args)
    for bad in ("", 'a"b', "a\\b", "a\nb"):
        with pytest.raises(ValueError):
            keychain.write("svc", "me@x", bad)
    with pytest.raises(ValueError):
        keychain.write("svc", 'me@x" -w "other', "s3cret")
    with pytest.raises(keychain.CommandError, match="didn't keep"):
        keychain.write("svc", "me@x", "other")  # reads back "s3cret"


def test_no_store_or_no_secret_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str], input: str | None = None) -> str:
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(keychain.sys, "platform", "linux")
    monkeypatch.setattr(keychain, "run", missing)
    assert keychain.read("svc", "me@x") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Credential Manager")
def test_a_secret_stored_with_cmdkey_is_read_from_credential_manager() -> None:
    target = "move-forward-test"
    subprocess.run(
        ["cmdkey", f"/generic:{target}", "/user:me@x", "/pass:s3cret-token"],
        check=True,
        capture_output=True,
    )
    try:
        assert keychain.read(target, "me@x") == "s3cret-token"
    finally:
        subprocess.run(["cmdkey", f"/delete:{target}"], check=False, capture_output=True)
    assert keychain.read(target, "me@x") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Credential Manager")
def test_a_secret_written_to_credential_manager_reads_back() -> None:
    target = "move-forward-test-write"
    try:
        keychain.write(target, "me@x", "first")
        keychain.write(target, "me@x", "s3cret-token")  # replaces
        assert keychain.read(target, "me@x") == "s3cret-token"
    finally:
        subprocess.run(["cmdkey", f"/delete:{target}"], check=False, capture_output=True)


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    db = (tmp_path / "dashboard.db").as_posix()
    config.write_text(f'email = "me@x"\ndb_path = "{db}"\n[jira]\nsite = "https://j"\n')
    return config


def test_mfd_token_set_stores_what_it_asks_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stored: list[tuple[str, str, str]] = []

    def write(service: str, account: str, secret: str) -> None:
        stored.append((service, account, secret))

    def getpass(prompt: str = "") -> str:
        return " s3cret\n"

    monkeypatch.setattr(cli.getpass, "getpass", getpass)
    monkeypatch.setattr(keychain, "write", write)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    config = _config(tmp_path)
    assert cli.main(["-c", str(config), "token", "set", "jira"]) == 0
    assert stored == [("move-forward-jira", "me@x", "s3cret")]
    assert cli.main(["-c", str(config), "token", "set", "slack"]) == 1
    assert "[slack] isn't in" in capsys.readouterr().err


def test_mfd_token_check_says_where_each_token_is_without_showing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    found: list[str | None] = ["s3cret"]

    def read(service: str, account: str) -> str | None:
        return found[0]

    monkeypatch.setattr(keychain, "read", read)
    monkeypatch.setenv("JIRA_API_TOKEN", "from-env")
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    monkeypatch.delenv("JIRA_API_TOKEN")
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    found[0] = None
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "jira: from $JIRA_API_TOKEN",
        "jira: from the secret store (move-forward-jira)",
        "jira: missing; run `mfd token set jira`",
    ]
    assert "s3cret" not in out and "from-env" not in out
