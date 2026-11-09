"""Tokens in the system's own secret store, and `mfd token`."""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from move_forward import cli, keychain, registry
from move_forward import config as config_module
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.shell import CommandError


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
def test_a_secret_written_to_credential_manager_reads_back_and_deletes() -> None:
    target = "move-forward-test-write"
    try:
        keychain.write(target, "me@x", "first")
        keychain.write(target, "me@x", "s3cret-token")  # replaces
        assert keychain.read(target, "me@x") == "s3cret-token"
        keychain.delete(target, "me@x")
        assert keychain.read(target, "me@x") is None
        keychain.delete(target, "me@x")  # nothing there: fine
    finally:
        subprocess.run(["cmdkey", f"/delete:{target}"], check=False, capture_output=True)


def test_on_macos_and_linux_a_secret_is_deleted_through_the_stores_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[str]] = []
    stored: list[str] = []

    def run(args: list[str], input: str | None = None) -> str:
        asked.append(args)
        if "find-generic-password" in args or "lookup" in args:
            if stored:
                return stored[0]
            raise CommandError("not found")
        if "delete-generic-password" in args or "clear" in args:
            stored.clear()
        return ""

    monkeypatch.setattr(keychain, "run", run)
    for platform, command in (("darwin", "security"), ("linux", "secret-tool")):
        monkeypatch.setattr(keychain.sys, "platform", platform)
        stored[:] = ["s3cret"]
        keychain.delete("svc", "me@x")
        assert asked[-2][0] == command and "svc" in asked[-2] and not stored
    stored[:] = ["s3cret"]

    def stubborn(args: list[str], input: str | None = None) -> str:
        return "s3cret" if "lookup" in args else ""

    monkeypatch.setattr(keychain, "run", stubborn)
    with pytest.raises(CommandError, match="kept the token"):
        keychain.delete("svc", "me@x")


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
    assert "slack isn't a token" in (err := capsys.readouterr().err) and err.endswith(": jira\n")


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


def test_a_store_that_refuses_the_secret_doesnt_say_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def refusing(args: list[str], input: str | None = None) -> str:
        raise CommandError(f"security -i: failed on: {input}")

    monkeypatch.setattr(keychain, "run", refusing)
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    with pytest.raises(CommandError) as refused:
        keychain.write("svc", "me@x", "s3cret-token")
    assert "s3cret-token" not in str(refused.value) and "failed on" in str(refused.value)


@dataclass
class _ElsewhereToken:
    token_env: str = "GITHUB_PAT"
    keychain_service: str = "move-forward-github"


class _GitHubWithAToken(GitHubPlugin):
    """github, keeping a token its section doesn't name as `token_env`."""

    def token_settings(self, config: GitHubConfig) -> keychain.TokenSettings | None:
        return _ElsewhereToken()


def test_mfd_token_finds_a_plugins_token_by_its_token_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    swap = {"github": _GitHubWithAToken()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setenv("GITHUB_PAT", "x")
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    assert "github: from $GITHUB_PAT" in capsys.readouterr().out


def test_a_config_keeping_two_tokens_in_one_place_is_refused(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.write_text(
        config.read_text().replace(
            'site = "https://j"', 'site = "https://j"\nkeychain_service = "shared"'
        )
        + '[slack]\nkeychain_service = "shared"\n'
    )
    with pytest.raises(ValidationError, match="jira and slack keep their tokens under 'shared'"):
        config_module.load(config)


class _GitHubKeepingJirasService(GitHubPlugin):
    def token_settings(self, config: GitHubConfig) -> keychain.TokenSettings | None:
        return _JiraService()


class _JiraService:
    token_env: str = "GITHUB_PAT"
    keychain_service: str = "move-forward-jira"


def test_a_plugins_token_settings_count_where_it_keeps_its_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubKeepingJirasService()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    with pytest.raises(ValidationError, match="github and jira keep their tokens"):
        config_module.load(config)


class _WorkToken:
    token_env: str = "WORK_PAT"
    keychain_service: str = "move-forward-github-work"


class _GitHubWithAccounts(GitHubPlugin):
    """github, keeping a token of its own and one for a work account."""

    def tokens(self, config: GitHubConfig) -> dict[str, keychain.TokenSettings]:
        return {"": _ElsewhereToken(), "work": _WorkToken()}


def test_mfd_token_sets_and_checks_each_of_a_plugins_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    swap = {"github": _GitHubWithAccounts()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    stored: list[tuple[str, str, str]] = []

    def write(service: str, account: str, secret: str) -> None:
        stored.append((service, account, secret))

    def nothing(service: str, account: str) -> str | None:
        return None

    monkeypatch.setattr(keychain, "write", write)
    monkeypatch.setattr(keychain, "read", nothing)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "s3cret")  # pyright: ignore[reportUnknownLambdaType]
    monkeypatch.delenv("WORK_PAT", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_PAT", "x")
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    assert cli.main(["-c", str(config), "token", "set", "github.work"]) == 0
    assert stored == [("move-forward-github-work", "me@x", "s3cret")]
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    out = capsys.readouterr().out
    assert "github: from $GITHUB_PAT" in out
    assert "github.work: missing; run `mfd token set github.work`" in out


class _GitHubWithAnAccountOnJirasService(GitHubPlugin):
    def tokens(self, config: GitHubConfig) -> dict[str, keychain.TokenSettings]:
        return {"work": _JiraService()}


def test_an_accounts_token_counts_where_it_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWithAnAccountOnJirasService()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    with pytest.raises(ValidationError, match="github.work and jira keep their tokens"):
        config_module.load(config)


def test_services_differing_only_in_case_are_one(tmp_path: Path) -> None:
    config = _config(tmp_path)  # Windows' Credential Manager ignores case
    config.write_text(
        config.read_text().replace(
            'site = "https://j"', 'site = "https://j"\nkeychain_service = "Shared"'
        )
        + '[slack]\nkeychain_service = "shared"\n'
    )
    with pytest.raises(ValidationError, match="jira and slack keep their tokens"):
        config_module.load(config)
