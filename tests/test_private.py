"""Files only I can read, on every system."""

from pathlib import Path

import pytest

from move_forward import private


def _explain(path: Path) -> str:
    """What the system says of `path`'s access, for a failure's message."""
    if not private.WINDOWS:
        return oct(path.stat().st_mode)
    return f"{private._me()!r}: {private.run(['icacls', str(path)])!r}"  # pyright: ignore[reportPrivateUsage]


def test_a_file_made_private_is_mine_alone(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_text("x")
    path.chmod(0o644)
    private.make_private(path)
    assert private.is_private(path), _explain(path)
    folder = tmp_path / "keys"
    folder.mkdir()
    private.make_private(folder)
    assert private.is_private(folder), _explain(folder)


def test_icacls_listings_are_read_for_whom_they_grant() -> None:
    path = r"C:\data\dashboard.db"
    listing = (
        f"{path} ACME\\jane:(F)\n"
        "                     NT AUTHORITY\\SYSTEM:(I)(F)\n"
        "                     BUILTIN\\Users:(I)(RX)\n"
        "\n"
        "Successfully processed 1 files; Failed processing 0 files\n"
    )
    holders = private._holders(listing, path)  # pyright: ignore[reportPrivateUsage]
    assert holders == ["acme\\jane", "nt authority\\system", "builtin\\users"]


def test_on_windows_only_me_and_the_system_may_hold_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def listing(path: str, *holders: str) -> str:
        first, *rest = holders
        lines = [f"{path} {first}:(F)", *(f"            {h}:(F)" for h in rest)]
        return "\n".join([*lines, "", "Successfully processed 1 files"]) + "\n"

    answers = {
        "mine": listing(
            "mine", "ACME\\jane", "NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators", "OWNER RIGHTS"
        ),
        "shared": listing("shared", "ACME\\jane", "BUILTIN\\Users"),
    }

    def icacls(args: list[str]) -> str:
        return answers[args[1]]

    def me() -> str:
        return "ACME\\jane"

    monkeypatch.setattr(private, "WINDOWS", True)
    monkeypatch.setattr(private, "_me", me)
    monkeypatch.setattr(private, "run", icacls)
    assert private.is_private(Path("mine"))
    assert not private.is_private(Path("shared"))  # the Users group can read it
