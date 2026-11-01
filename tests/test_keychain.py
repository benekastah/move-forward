"""Tokens from the system's own secret store."""

import subprocess
import sys

import pytest

from move_forward import keychain


def test_on_macos_and_linux_the_store_is_asked_through_its_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[str]] = []

    def run(args: list[str]) -> str:
        asked.append(args)
        return "s3cret\n"

    monkeypatch.setattr(keychain, "run", run)
    for platform, command in (("darwin", "security"), ("linux", "secret-tool")):
        monkeypatch.setattr(keychain.sys, "platform", platform)
        assert keychain.read("svc", "me@x") == "s3cret"
        assert asked[-1][0] == command and "svc" in asked[-1] and "me@x" in asked[-1]
        assert keychain.how_to_store("svc", "me@x").startswith(command)


def test_no_store_or_no_secret_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str]) -> str:
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
