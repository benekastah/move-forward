"""The dashboard's certificate, and serving over HTTPS with it."""

import ssl
import stat
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.client import HTTPSConnection
from pathlib import Path

import pytest
from cryptography import x509

from move_forward import server, tls
from move_forward.config import Config
from move_forward.db import Database
from move_forward.plugins.github.config import GitHubConfig
from move_forward.registry import migrate

TOKEN = "s3cret"


def test_the_certificate_names_only_this_machine_and_its_authority_only_vouches_for_it(
    tmp_path: Path,
) -> None:
    paths = tls.Paths(tmp_path / "tls")
    tls.make(paths)
    [(first, first_key), *_] = paths.certs()
    assert all(stat.S_IMODE(key.stat().st_mode) == 0o600 for _, key in paths.certs())
    cert = x509.load_pem_x509_certificate(first.read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["localhost"]
    assert [str(a) for a in names.get_values_for_type(x509.IPAddress)] == ["127.0.0.1", "::1"]
    ca = x509.load_pem_x509_certificate(paths.ca.read_bytes())
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical and constraints.value.permitted_subtrees is not None
    assert not list(paths.dir.glob("*ca*key*"))  # the authority's key isn't kept
    assert first_key.exists()


def test_check_says_when_the_certificate_is_missing_or_ending(tmp_path: Path) -> None:
    paths = tls.Paths(tmp_path / "tls")
    assert tls.problem(paths) is not None and "mfd cert" in (tls.problem(paths) or "")
    tls.make(paths)
    assert tls.problem(paths) is None
    last = tls.expires(paths)
    assert tls.problem(paths, datetime.now(UTC) + timedelta(days=tls.VALID_DAYS + 1)) is None
    assert "ends in" in (tls.problem(paths, last - timedelta(days=10)) or "")
    assert "have ended" in (tls.problem(paths, last + timedelta(days=1)) or "")


@pytest.fixture
def https(tmp_path: Path) -> Iterator[tuple[HTTPSConnection, tls.Paths]]:
    config = Config(
        email="me@x", db_path=tmp_path / "dashboard.db", github=GitHubConfig(org="acme")
    )
    db = Database(config.db_path)
    migrate(config, db)
    paths = tls.Paths.beside(config.db_path)
    tls.make(paths)
    assert server.scheme(config) == "https"
    app = server.App(config, db, TOKEN, "https")
    httpd = server._TLSServer(  # pyright: ignore[reportPrivateUsage]
        ("127.0.0.1", 0), server.make_handler(app), paths
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    trusting = ssl.create_default_context(cafile=paths.ca)  # verifies the name, too
    yield HTTPSConnection("127.0.0.1", httpd.server_port, context=trusting, timeout=5), paths
    httpd.shutdown()
    httpd.server_close()


def test_the_dashboard_answers_over_https_with_a_secure_sign_in_cookie(
    https: tuple[HTTPSConnection, tls.Paths],
) -> None:
    client, _ = https
    client.request("GET", f"/login?token={TOKEN}")
    response = client.getresponse()
    response.read()
    assert response.status == 303
    assert "Secure" in (response.getheader("Set-Cookie") or "")
    origin = f"https://127.0.0.1:{client.port}"
    headers = {"Authorization": f"Bearer {TOKEN}", "Origin": origin}
    client.request("POST", "/refresh", headers=headers)
    response = client.getresponse()
    response.read()
    assert response.status == 303
    client.request(
        "POST", "/refresh", headers={**headers, "Origin": origin.replace("https", "http")}
    )
    response = client.getresponse()
    response.read()
    assert response.status == 403  # the plain-HTTP origin isn't this dashboard any more


def test_without_a_certificate_the_dashboard_stays_on_plain_http(tmp_path: Path) -> None:
    config = Config(email="me@x", db_path=tmp_path / "dashboard.db")
    assert server.scheme(config) == "http"
    tls.make(tls.Paths.beside(config.db_path))
    assert server.scheme(config) == "https"
    assert server.scheme(config.model_copy(update={"https": False})) == "http"


def test_trusting_adds_the_authority_to_firefoxs_and_chromes_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = tls.Paths(tmp_path / "tls")
    tls.make(paths)
    profile = tmp_path / "home" / ".mozilla" / "firefox" / "abc.default"
    profile.mkdir(parents=True)
    (profile / "cert9.db").write_text("")
    ran: list[list[str]] = []

    def run(args: list[str]) -> str:
        ran.append(args)
        return ""

    monkeypatch.setattr(tls.sys, "platform", "linux")

    def which(_name: str) -> str:
        return "/usr/bin/certutil"

    monkeypatch.setattr(tls.shutil, "which", which)
    monkeypatch.setattr(tls, "run", run)
    done = tls.trust(paths, tmp_path / "home")
    added = [a for a in ran if "-A" in a]
    assert [a[2] for a in added] == [
        f"sql:{tmp_path / 'home' / '.pki' / 'nssdb'}",
        f"sql:{profile}",
    ]
    assert all(a[-1] == str(paths.ca) for a in added)
    assert len(done) == 2


def test_without_certutil_it_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = tls.Paths(tmp_path / "tls")
    tls.make(paths)
    monkeypatch.setattr(tls.sys, "platform", "linux")

    def which(_name: str) -> None:
        return None

    monkeypatch.setattr(tls.shutil, "which", which)
    [said] = tls.trust(paths, tmp_path / "home")
    assert "certutil" in said and str(paths.ca) in said


def test_on_macos_the_authority_is_trusted_for_tls_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tls.sys, "platform", "darwin")
    command = tls.system_trust_command(tls.Paths(tmp_path))
    assert command is not None and command[command.index("-p") + 1] == "ssl"


def test_the_chain_covers_years_and_each_certificate_takes_over_before_the_last_ends(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    paths = tls.Paths(tmp_path / "tls")
    tls.make(paths, now)
    first, second, third = paths.certs()
    step = timedelta(days=tls.VALID_DAYS - tls.OVERLAP_DAYS)
    assert tls.current(paths, now) == first
    assert tls.current(paths, now + step - timedelta(days=1)) == first
    assert tls.current(paths, now + step + timedelta(days=1)) == second  # during the overlap
    assert tls.current(paths, now + 2 * step + timedelta(days=1)) == third
    end = tls.expires(paths)
    assert end - now > timedelta(days=6 * 365)
    assert tls.current(paths, end + timedelta(days=1)) is None


def test_the_server_moves_to_the_next_certificate_by_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    paths = tls.Paths(tmp_path / "tls")
    tls.make(paths, now)
    moments = [now]
    chosen: list[tuple[Path, Path] | None] = []
    real_context = tls.context

    def context(paths: tls.Paths, when: datetime | None = None) -> ssl.SSLContext:
        chosen.append(tls.current(paths, when))
        return real_context(paths, when)

    monkeypatch.setattr(server.tls, "context", context)
    monkeypatch.setattr(server, "TLS_RECHECK_SECONDS", 0)
    app = server.App(Config(email="me@x", db_path=tmp_path / "d.db"), Database(":memory:"), TOKEN)
    httpd = server._TLSServer(  # pyright: ignore[reportPrivateUsage]
        ("127.0.0.1", 0), server.make_handler(app), paths, clock=lambda: moments[-1]
    )
    try:
        moments.append(now + timedelta(days=tls.VALID_DAYS - tls.OVERLAP_DAYS + 1))
        httpd.context()
        assert chosen == [paths.certs()[0], paths.certs()[1]]
    finally:
        httpd.server_close()


def test_the_page_says_when_there_is_no_certificate_or_it_ends_soon(tmp_path: Path) -> None:
    config = Config(email="me@x", db_path=tmp_path / "dashboard.db")
    db = Database(config.db_path)
    migrate(config, db)
    app = server.App(config, db, TOKEN)
    assert "<span class='notice warn'>no certificate" in app.dashboard_page()
    tls.make(tls.Paths.beside(config.db_path))
    assert "notice warn" not in app.dashboard_page()
    elsewhere = config.model_copy(update={"https": False, "db_path": tmp_path / "x" / "d.db"})
    assert "notice warn" not in server.App(elsewhere, db, TOKEN).dashboard_page()  # off


def test_renewing_gets_louder_as_the_last_certificate_nears_its_end(tmp_path: Path) -> None:
    paths = tls.Paths(tmp_path / "tls")
    assert tls.urgency(paths) is None  # none yet: the header says how to make them
    tls.make(paths)
    last = tls.expires(paths)
    assert tls.urgency(paths, last - timedelta(days=20)) is None  # just the header
    assert tls.urgency(paths, last - timedelta(days=10)) == "soon"
    assert tls.urgency(paths, last - timedelta(days=3)) == "now"
    assert tls.urgency(paths, last + timedelta(days=1)) == "now"


def test_the_banner_is_on_the_dashboard_and_focus_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(email="me@x", db_path=tmp_path / "dashboard.db")
    db = Database(config.db_path)
    migrate(config, db)
    tls.make(tls.Paths.beside(config.db_path))
    app = server.App(config, db, TOKEN)
    assert "class='banner" not in app.dashboard_page()
    soon = tls.expires(tls.Paths.beside(config.db_path)) - timedelta(days=3)
    real_urgency, real_problem = tls.urgency, tls.problem

    def urgency(paths: tls.Paths) -> str | None:
        return real_urgency(paths, soon)

    def problem(paths: tls.Paths) -> str | None:
        return real_problem(paths, soon)

    monkeypatch.setattr(server.tls, "urgency", urgency)
    monkeypatch.setattr(server.tls, "problem", problem)
    banner = "<div class='banner now' role='alert'>the last certificate ends in 3 days"
    assert banner in app.dashboard_page()
    assert banner in (app.focus_page("", 0) or "")
    assert banner in (app.focus_page("all", 0) or "")
