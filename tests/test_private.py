"""Files only I can read, on every system."""

from pathlib import Path

from move_forward import private


def test_a_file_made_private_is_mine_alone(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_text("x")
    path.chmod(0o644)
    private.make_private(path)
    assert private.is_private(path)
    folder = tmp_path / "keys"
    folder.mkdir()
    private.make_private(folder)
    assert private.is_private(folder)


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
