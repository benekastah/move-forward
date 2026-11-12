"""Tokens in the system's own secret store, and `mfd token`."""

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import NOW
from pydantic import ValidationError

from move_forward import cli, keychain, registry
from move_forward import config as config_module
from move_forward.db import Database
from move_forward.plugin import Context, SourceError
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.repositories import KvRepository
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


class _CredentialManager:
    """advapi32's Credential Manager functions over a dict of each target's secret, which,
    as Windows does, ignores the target's case: what `keychain._advapi32` gives in its tests,
    so they don't reach the real store."""

    def __init__(self) -> None:
        self.secrets: dict[str, bytes] = {}
        self._handed: list[object] = []
        """What `CredReadW` pointed the caller at, kept alive as Windows keeps its own."""

    def CredWriteW(self, credential: Any, flags: int) -> int:
        given = credential._obj  # the CREDENTIALW `byref` points at
        self.secrets[given.TargetName.lower()] = bytes(
            given.CredentialBlob[: given.CredentialBlobSize]
        )
        return 1

    def CredReadW(self, target: str, type: int, flags: int, found: Any) -> int:
        blob = self.secrets.get(target.lower())
        if blob is None:
            return 0
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = getattr(keychain, "_Credential")(  # noqa: B009  # defined on Windows only
            TargetName=target,
            CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        self._handed.append((buffer, credential))
        found._obj.contents = credential  # the caller's pointer, which `byref` points at
        return 1

    def CredDeleteW(self, target: str, type: int, flags: int) -> int:
        return int(self.secrets.pop(target.lower(), None) is not None)

    def CredFree(self, credential: object) -> None:
        pass


@pytest.fixture
def credential_manager(monkeypatch: pytest.MonkeyPatch) -> _CredentialManager:
    fake = _CredentialManager()
    monkeypatch.setattr(keychain, "_advapi32", lambda: fake)
    return fake


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Credential Manager")
def test_a_secret_stored_as_cmdkey_does_is_read_from_credential_manager(
    credential_manager: _CredentialManager,
) -> None:
    credential_manager.secrets["move-forward-test"] = "s3cret-token".encode("utf-16-le")
    assert keychain.read("Move-Forward-Test", "me@x") == "s3cret-token"
    credential_manager.secrets["move-forward-test"] = b"utf-8 token"  # another tool's
    assert keychain.read("move-forward-test", "me@x") == "utf-8 token"
    assert keychain.read("move-forward-other", "me@x") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Credential Manager")
def test_a_secret_written_to_credential_manager_reads_back_and_deletes(
    credential_manager: _CredentialManager,
) -> None:
    target = "move-forward-test-write"
    keychain.write(target, "me@x", "first")
    keychain.write(target, "me@x", "s3cret-token")  # replaces
    assert credential_manager.secrets == {target: "s3cret-token".encode("utf-16-le")}
    assert keychain.read(target, "me@x") == "s3cret-token"
    keychain.delete(target, "me@x")
    assert keychain.read(target, "me@x") is None
    keychain.delete(target, "me@x")  # nothing there: fine


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

    def tokens(self, config: GitHubConfig) -> dict[str, keychain.TokenSettings]:
        return {"": _ElsewhereToken()}


def test_mfd_token_finds_a_plugins_token_where_it_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    swap = {"github": _GitHubWithAToken()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setenv("GITHUB_PAT", "x")
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    looked_up: list[str] = []

    def read(service: str, account: str) -> None:
        looked_up.append(service)

    monkeypatch.setattr(keychain, "read", read)
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    assert cli.main(["-c", str(config), "token", "check"]) == 0
    assert "github: from $GITHUB_PAT" in capsys.readouterr().out
    assert looked_up == ["move-forward-jira"]  # github's was in its variable


def test_a_config_keeping_two_tokens_in_one_place_is_refused(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.write_text(
        config.read_text().replace(
            'site = "https://j"', 'site = "https://j"\nkeychain_service = "shared"'
        )
        + '[slack]\nkeychain_service = "shared"\n'
    )
    with pytest.raises(
        ValidationError,
        match=r"jira \('shared'\) and slack \('shared'\) keep their tokens in one place",
    ):
        config_module.load(config)


class _GitHubKeepingJirasService(GitHubPlugin):
    def tokens(self, config: GitHubConfig) -> dict[str, keychain.TokenSettings]:
        return {"": _JiraService()}


class _JiraService:
    token_env: str = "GITHUB_PAT"
    keychain_service: str = "move-forward-jira"


def test_where_a_plugin_says_it_keeps_its_token_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubKeepingJirasService()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = _config(tmp_path)
    config.write_text(config.read_text() + '[github]\norg = "acme"\n')
    with pytest.raises(ValidationError, match=r"github \('move-forward-jira'\) and jira"):
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
    with pytest.raises(ValidationError, match=r"github.work \('move-forward-jira'\) and jira"):
        config_module.load(config)


def test_services_differing_only_in_case_are_one(tmp_path: Path) -> None:
    config = _config(tmp_path)  # Windows' Credential Manager ignores case
    config.write_text(
        config.read_text().replace(
            'site = "https://j"', 'site = "https://j"\nkeychain_service = "Shared"'
        )
        + '[slack]\nkeychain_service = "shared"\n'
    )
    with pytest.raises(ValidationError, match=r"jira \('Shared'\) and slack \('shared'\) keep"):
        config_module.load(config)


class _SiteConfig(keychain.TokenFields):
    default_token_env: ClassVar[str] = "SITE_TOKEN"
    default_keychain_service: ClassVar[str] = "move-forward-site"


def test_a_section_keeping_a_token_has_its_own_defaults_for_where() -> None:
    assert (_SiteConfig().token_env, _SiteConfig().keychain_service) == (
        "SITE_TOKEN",
        "move-forward-site",
    )
    mine = _SiteConfig.model_validate({"token_env": "MY_TOKEN"})
    assert (mine.token_env, mine.keychain_service) == ("MY_TOKEN", "move-forward-site")
    with pytest.raises(ValidationError):  # still a config model: no unknown keys
        _SiteConfig.model_validate({"token_envv": "MY_TOKEN"})


def _token_ctx(tokens: dict[str, keychain.TokenSettings] | None = None) -> Context[_SiteConfig]:
    db = Database(":memory:")
    return Context(
        config=_SiteConfig(),
        email="me@x",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "site"),
        name="site",
        tokens=tokens,
    )


def test_a_plugins_token_is_read_from_its_variable_else_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = {("move-forward-site", "me@x"): "from-store"}

    def read(service: str, account: str) -> str | None:
        return stored.get((service, account))

    monkeypatch.setattr(keychain, "read", read)
    monkeypatch.delenv("SITE_TOKEN", raising=False)
    assert _token_ctx().token() == "from-store"  # the section's own, by default
    monkeypatch.setenv("SITE_TOKEN", "from-env")
    assert _token_ctx().token() == "from-env"


def test_no_token_says_how_to_give_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def read(service: str, account: str) -> None:
        return None

    monkeypatch.setattr(keychain, "read", read)
    monkeypatch.delenv("SITE_TOKEN", raising=False)
    monkeypatch.delenv("WORK_TOKEN", raising=False)
    work = _ElsewhereToken(token_env="WORK_TOKEN", keychain_service="move-forward-site-work")
    ctx = _token_ctx({"": _SiteConfig(), "work": work})
    with pytest.raises(
        SourceError, match=r"^no token: run `mfd token set site`, or set \$SITE_TOKEN$"
    ):
        ctx.token()
    with pytest.raises(
        SourceError, match=r"^no token: run `mfd token set site\.work`, or set \$WORK_TOKEN$"
    ):
        ctx.token("work")
    with pytest.raises(LookupError, match="keeps no token for 'home'"):
        ctx.token("home")
    with pytest.raises(SourceError, match=r"^no password: run `mfd token set site\.work`"):
        ctx.token("work", secret="password")  # what the plugin calls it


@pytest.mark.parametrize("missing", ["default_token_env", "default_keychain_service"])
def test_a_section_keeping_a_token_must_say_where_by_default(missing: str) -> None:
    defaults = {"default_token_env": "HALF_TOKEN", "default_keychain_service": "half"}
    del defaults[missing]
    with pytest.raises(TypeError, match=f"_Half must set {missing}"):
        type("_Half", (keychain.TokenFields,), defaults)


def test_the_context_core_gives_holds_the_plugins_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWithAToken()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setenv("GITHUB_PAT", "pat")
    path = _config(tmp_path)
    path.write_text(path.read_text() + '[github]\norg = "acme"\n')
    config = config_module.load(path)
    enabled = registry.enabled(config, Database(":memory:"), NOW)
    [github] = [e for e in enabled if e.name == "github"]
    assert github.ctx.token() == "pat"  # its own, though its section names none
