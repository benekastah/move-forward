import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from move_forward.graph import Ref
from move_forward.kinds import PERSON
from move_forward.plugin import ChangedRequest, SourceError
from move_forward.plugins.vcard import client
from move_forward.plugins.vcard.config import VcardConfig
from move_forward.plugins.vcard.models import Contact, VcardSnapshot
from move_forward.plugins.vcard.plugin import VcardPlugin
from move_forward.plugins.vcard.repository import ContactRepository
from move_forward.testing import Harness

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


def card(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", *lines, "END:VCARD"]) + "\r\n"


def config(tmp_path: Path, **section: object) -> VcardConfig:
    return VcardConfig.model_validate({"paths": [str(tmp_path)], **section})


def test_a_card_gives_its_name_addresses_and_categories() -> None:
    [found] = client.parse(
        card(
            "FN:Alice\\, the Ops Lead",
            "item1.EMAIL;type=INTERNET;type=pref:Alice@Acme.com",
            "EMAIL:mailto:alice@home.example",
            "EMAIL:not an address",
            "EMAIL:a/b@acme.com",
            "CATEGORIES:Clients,Family\\, extended",
            "UID:urn:uuid:ABC",
        )
    )
    assert found.name == "Alice, the Ops Lead"
    assert found.emails == ["alice@acme.com", "alice@home.example"]
    assert found.categories == ["Clients", "Family, extended"]
    assert found.uid == "urn:uuid:ABC"


def test_folded_lines_are_joined_and_n_names_a_card_without_fn() -> None:
    [found] = client.parse(card("N:Doe;Jane;;Dr.;", "EMAIL:jane@acme", " .com"))
    assert (found.name, found.emails) == ("Dr. Jane Doe", ["jane@acme.com"])


def test_a_21_quoted_printable_value_is_decoded_by_its_charset() -> None:
    text = (
        "BEGIN:VCARD\nVERSION:2.1\n"
        "FN;CHARSET=ISO-8859-1;ENCODING=QUOTED-PRINTABLE:Jos=E9 =\nGarc=EDa\n"
        "EMAIL;INTERNET:jose@acme.com\nEND:VCARD\n"
    )
    [found] = client.parse(text)
    assert (found.name, found.emails) == ("José García", ["jose@acme.com"])


def test_lines_outside_a_card_are_ignored() -> None:
    assert client.parse("EMAIL:x@acme.com\n" + card("FN:A", "EMAIL:a@acme.com"))[0].emails == [
        "a@acme.com"
    ]


def test_everyone_with_an_address_in_the_files_is_a_contact_once(tmp_path: Path) -> None:
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "a.vcf").write_text(
        card("FN:Alice", "EMAIL:alice@acme.com", "CATEGORIES:Clients")
        + card("FN:Nobody")  # no address: no one to join
    )
    (tmp_path / "book" / "b.VCF").write_text(
        card("FN:Alice Again", "EMAIL:alice@acme.com", "CATEGORIES:Family")
    )
    (tmp_path / "notes.txt").write_text(card("FN:Not a vCard file", "EMAIL:x@acme.com"))
    assert client.collect(config(tmp_path)).contacts == [
        Contact(email="alice@acme.com", name="Alice", groups=["Clients", "Family"])
    ]


@pytest.mark.parametrize(
    ("kind", "member"),
    [("KIND:group", "MEMBER"), ("X-ADDRESSBOOKSERVER-KIND:group", "X-ADDRESSBOOKSERVER-MEMBER")],
)
def test_a_group_card_puts_its_members_in_it(tmp_path: Path, kind: str, member: str) -> None:
    (tmp_path / "people.vcf").write_text(
        card("FN:Bob", "EMAIL:bob@acme.com", "UID:B0B")
        + card("FN:Carol", "EMAIL:carol@acme.com", "UID:urn:uuid:c4r0l")
    )
    (tmp_path / "group.vcf").write_text(
        card("FN:Favorites", kind, f"{member}:urn:uuid:b0b", "UID:G1")
    )
    groups = {c.email: c.groups for c in client.collect(config(tmp_path)).contacts}
    assert groups == {"bob@acme.com": ["Favorites"], "carol@acme.com": []}


def test_a_path_that_isnt_there_fails_the_refresh(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="isn't there"):
        client.collect(config(tmp_path / "missing"))


def _harness(tmp_path: Path, **section: object) -> Harness:
    return Harness(VcardPlugin(), {"paths": [str(tmp_path)], **section}, now=NOW)


def test_its_people_matter_and_are_what_their_groups_say(tmp_path: Path) -> None:
    vcard = _harness(
        tmp_path,
        group_importance={"family": 2, "Favorites": 1},
        group_types={"Vendors": "vendor", "clients": "customer"},
    )
    vcard.store(
        VcardSnapshot(
            contacts=[
                Contact(email="mom@home.example", name="Mom", groups=["Favorites", "Family"]),
                Contact(email="alice@acme.com", name="Alice", groups=["Clients", "Vendors"]),
                Contact(email="bob@acme.com", name="Bob"),
            ]
        )
    )
    people = {e.ref.id: e for e in vcard.entities()}
    mom, alice, bob = (
        people[f"vcard/{e}"] for e in ("mom@home.example", "alice@acme.com", "bob@acme.com")
    )
    assert (mom.title, mom.attrs["email"], mom.attrs["importance"]) == (
        "Mom",
        "mom@home.example",
        2,
    )
    assert alice.attrs["contact_type"] == "vendor"  # the first listed in the config
    assert alice.attrs["groups"] == ["Clients", "Vendors"]
    assert (bob.attrs["importance"], bob.attrs["contact_type"]) == (None, None)
    assert [e.ref for e in vcard.graph().same_person("vcard/bob@acme.com")] == [
        Ref(kind=PERSON, id="vcard/bob@acme.com")
    ]


def test_what_it_read_is_kept_as_it_came(tmp_path: Path) -> None:
    vcard = _harness(tmp_path)
    snapshot = VcardSnapshot(
        contacts=[
            Contact(email="b@acme.com", name="B", groups=["Z", "A"]),
            Contact(email="a@acme.com", name="A"),
        ]
    )
    ContactRepository(vcard.db).replace(snapshot)
    assert ContactRepository(vcard.db).load() == snapshot


def test_a_written_file_refreshes_it(tmp_path: Path) -> None:
    book = tmp_path / "a.vcf"
    book.write_text(card("FN:A", "EMAIL:a@acme.com"))
    vcard = _harness(tmp_path)
    plugin = VcardPlugin()
    assert not plugin.changed(
        ChangedRequest(vcard.ctx, vcard.graph())
    ).changed  # nothing to compare yet
    assert not plugin.changed(ChangedRequest(vcard.ctx, vcard.graph())).changed
    book.write_text(card("FN:A", "EMAIL:a@acme.com", "CATEGORIES:Family"))
    os.utime(book, ns=(1, 1))  # a clock too coarse to tell the write apart
    assert plugin.changed(ChangedRequest(vcard.ctx, vcard.graph())).changed
