"""The dashboard's certificate, so `mfd serve` can answer on https://127.0.0.1.

`mfd cert` makes a local certificate authority and, with it, a server certificate for
`127.0.0.1`, `localhost` and `::1`. The authority can only vouch for those names (its name
constraints say so), and its private key is never written down: it signs the server
certificate and is dropped, so nothing on disk can make a certificate the browser would
trust for anything else. Renewing makes a new authority, to trust again.

The browser has to trust the authority (`ca.pem`) once. `mfd cert --trust` adds it to the
system's store for my user on macOS (the login keychain) and Windows (the user's root
store), and, where NSS's `certutil` is installed, to the stores Chrome on Linux
(`~/.pki/nssdb`) and every Firefox profile keep of their own. Anything else is trusted by
hand.
"""

import ipaddress
import os
import shutil
import ssl
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .shell import CommandError, run

VALID_DAYS = 825
"""How long a server certificate lasts: the longest Apple's platforms accept."""
RENEW_WITHIN_DAYS = 30
"""`mfd check` warns when the certificate ends sooner than this."""
NAMES = ["localhost"]
ADDRESSES = [ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")]


@dataclass(frozen=True)
class Paths:
    """Where the certificate lives: `tls/` next to the database (and its sign-in token)."""

    dir: Path

    @classmethod
    def beside(cls, db_path: Path) -> "Paths":
        return cls(db_path.expanduser().parent / "tls")

    @property
    def ca(self) -> Path:
        return self.dir / "ca.pem"

    @property
    def cert(self) -> Path:
        return self.dir / "cert.pem"

    @property
    def key(self) -> Path:
        return self.dir / "key.pem"

    def ready(self) -> bool:
        return self.cert.exists() and self.key.exists()


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def make(paths: Paths, now: datetime | None = None) -> None:
    """Make a new authority and server certificate in `paths`, replacing any there."""
    now = now or datetime.now(UTC)
    until = now + timedelta(days=VALID_DAYS)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = _name(f"move-forward local CA {now:%Y-%m-%d}")
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[
                    *(x509.DNSName(name) for name in NAMES),
                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("::1/128")),
                ],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("move-forward"))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(until)
        .add_extension(
            x509.SubjectAlternativeName(
                [*(x509.DNSName(name) for name in NAMES), *map(x509.IPAddress, ADDRESSES)]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # The server certificate carries its chain, so a browser that trusts the authority
    # needn't be given it.
    paths.cert.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM)
    )
    _write_private(
        paths.key,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def _write_private(path: Path, data: bytes) -> None:
    """Write `data` readable by me only, from the moment the file exists."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def expires(paths: Paths) -> datetime:
    """When the server certificate ends."""
    return x509.load_pem_x509_certificate(paths.cert.read_bytes()).not_valid_after_utc


def problem(paths: Paths, now: datetime | None = None) -> str | None:
    """What's wrong with the certificate, for `mfd check`: missing, ended, or ending soon."""
    if not paths.ready():
        return f"no certificate in {paths.dir}; `mfd cert` makes one (until then, plain HTTP)"
    left = expires(paths) - (now or datetime.now(UTC))
    if left <= timedelta(0):
        return "the certificate has ended; `mfd cert` makes a new one (trust it again)"
    if left < timedelta(days=RENEW_WITHIN_DAYS):
        return f"the certificate ends in {left.days} days; `mfd cert` makes a new one"
    return None


def context(paths: Paths) -> ssl.SSLContext:
    """A server context with the certificate, TLS 1.2 or newer."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(paths.cert, paths.key)
    return ctx


FIREFOX_PROFILES = [
    "~/.mozilla/firefox",
    "~/snap/firefox/common/.mozilla/firefox",
    "~/Library/Application Support/Firefox/Profiles",
    "~/AppData/Roaming/Mozilla/Firefox/Profiles",
]
"""Where Firefox keeps its profiles, each with its own certificate store."""
NSS_NICKNAME = "move-forward local CA"


def nss_stores(home: Path | None = None) -> list[Path]:
    """The NSS stores to add the authority to: Chrome's on Linux, and every Firefox
    profile's."""
    home = home or Path.home()
    stores = [home / ".pki" / "nssdb"] if sys.platform.startswith("linux") else []
    for profiles in FIREFOX_PROFILES:
        root = Path(profiles.replace("~", str(home), 1))
        if root.is_dir():
            stores += sorted(p for p in root.iterdir() if (p / "cert9.db").exists())
    return stores


def system_trust_command(paths: Paths) -> list[str] | None:
    """The command adding the authority to this system's store for my user: macOS's login
    keychain or Windows's user root store; None on Linux, whose browsers keep their own."""
    if sys.platform == "darwin":
        keychain = Path("~/Library/Keychains/login.keychain-db").expanduser()
        return [
            "security",
            "add-trusted-cert",
            "-r",
            "trustRoot",
            "-k",
            str(keychain),
            str(paths.ca),
        ]
    if sys.platform == "win32":
        return ["certutil", "-user", "-addstore", "Root", str(paths.ca)]
    return None


def trust(paths: Paths, home: Path | None = None) -> list[str]:
    """Add the authority to the system's store and the browsers' own, as far as this machine
    allows; what was done, and what's left to do by hand."""
    done: list[str] = []
    if command := system_trust_command(paths):
        run(command)
        done.append("trusted it in the system's store for your user")
    stores = nss_stores(home)
    if stores and shutil.which("certutil") is None:
        done.append(
            "for Firefox (and Chrome on Linux), install NSS's certutil (libnss3-tools or"
            f" nss-tools) and run `mfd cert --trust` again, or import {paths.ca} by hand"
        )
    for store in stores if shutil.which("certutil") else []:
        store.mkdir(parents=True, exist_ok=True)
        db = f"sql:{store}"
        with suppress(CommandError):  # an older authority of ours, if there is one
            run(["certutil", "-d", db, "-D", "-n", NSS_NICKNAME])
        run(["certutil", "-d", db, "-A", "-t", "C,,", "-n", NSS_NICKNAME, "-i", str(paths.ca)])
        done.append(f"trusted it in {store}")
    if not done:
        done.append(f"import {paths.ca} as an authority in your browser's certificate settings")
    return done
