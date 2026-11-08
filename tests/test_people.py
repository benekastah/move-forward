"""Who people are to me: one human across tools, by email, and their contact type."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from move_forward import act_now, cli, panels, people, refresh
from move_forward.act_now import Nomination
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import KindModel
from move_forward.graph import Entity, Graph, KindSpec, Ref, attrs
from move_forward.kinds import ORGANISATION, PERSON, Organisation, Person, fits, personal_email
from move_forward.page import Item, Panel, Signals
from move_forward.people import Contacts, PeopleConfig
from move_forward.plugin import (
    API_VERSION,
    ActionError,
    EntitiesRequest,
    PanelsRequest,
    Plugin,
)
from move_forward.repositories import ContactRepository
from move_forward.strict import Strict
from move_forward.testing import Harness

NOW = datetime(2026, 11, 7, tzinfo=UTC)
THREAD = Ref(kind="mail.thread", id="t1")


def _person(
    id: str,
    email: str | None = None,
    contact_type: str | None = None,
    importance: int | None = None,
) -> Entity:
    return Entity(
        ref=Ref(kind=PERSON, id=id),
        title=id,
        attrs=attrs(
            Person(handle=id, email=email, contact_type=contact_type, importance=importance)
        ),
    )


def _organisation(
    id: str, contact_type: str | None = None, importance: int | None = None
) -> Entity:
    return Entity(
        ref=Ref(kind=ORGANISATION, id=id),
        title=id,
        attrs=attrs(Organisation(handle=id, contact_type=contact_type, importance=importance)),
    )


def _at(person: Entity, *orgs: Entity) -> Entity:
    return person.model_copy(update={"refs": [*person.refs, *(o.ref for o in orgs)]})


def _thread(*about: Entity) -> Entity:
    return Entity(ref=THREAD, title="Hello", refs=[p.ref for p in about])


def _contacts(*entities: Entity, labels: dict[str, str] | None = None) -> Contacts:
    return Contacts(labels or {}, Graph(list(entities)), PeopleConfig())


SENDER = _person("acme.com/alice", "Alice@Acme.com")


def test_people_with_the_same_email_are_one_person_whichever_tool_publishes_them() -> None:
    crm = _person("crm.example.com/42", "alice@acme.com")
    other = _person("acme.com/bob", "bob@acme.com")
    graph = Graph([SENDER, crm, other])
    assert [e.ref.id for e in graph.same_person(SENDER.ref.id)] == [SENDER.ref.id, crm.ref.id]
    assert [e.ref.id for e in graph.same_person(other.ref.id)] == [other.ref.id]
    graph.same_person(SENDER.ref.id).clear()  # a caller's own copy
    assert len(graph.same_person(SENDER.ref.id)) == 2


@pytest.mark.parametrize("email", [None, "noreply@github.com", "no-reply@x.com", "not an address"])
def test_no_email_or_a_shared_one_joins_no_one(email: str | None) -> None:
    one, two = _person("github.com/a", email), _person("github.com/b", email)
    assert [e.ref.id for e in Graph([one, two]).same_person("github.com/a")] == ["github.com/a"]


def test_someone_the_graph_does_not_have_is_no_one() -> None:
    assert Graph([]).same_person("github.com/a") == []


def test_a_personal_email_is_lower_cased() -> None:
    assert personal_email(" Alice@Acme.COM ") == "alice@acme.com"


@pytest.mark.parametrize(
    ("text", "key"), [("Alice@Acme.com", "alice@acme.com"), ("@Acme.com", "@acme.com")]
)
def test_a_type_is_kept_on_an_address_or_a_domain(text: str, key: str) -> None:
    assert people.contact_key(text) == key


@pytest.mark.parametrize(
    "text",
    ["@gmail.com", "@users.noreply.github.com", "noreply@acme.com", "acme.com", "@acme", ""],
)
def test_what_says_nothing_about_who_someone_is_takes_no_type(text: str) -> None:
    with pytest.raises(ValueError, match=r"anyone can have|isn't one person"):
        people.contact_key(text)


@pytest.mark.parametrize("text", ["", "1st", "a" * 41, "<b>"])
def test_a_contact_type_has_a_form(text: str) -> None:
    with pytest.raises(ValueError, match="isn't a contact type"):
        people.contact_type(text)


def test_config_refuses_an_importance_out_of_range() -> None:
    with pytest.raises(ValidationError):
        PeopleConfig(contact_types={"customer": 3})


def test_the_type_from_my_label_beats_a_tools_and_an_address_beats_a_domain() -> None:
    crm = _person("crm.example.com/42", "alice@acme.com", "vendor")
    thread = _thread(SENDER)
    tool = _contacts(SENDER, crm, thread).of(THREAD)
    assert tool is not None and (tool.contact_type, tool.labelled) == ("vendor", None)
    domain = _contacts(SENDER, crm, thread, labels={"@acme.com": "partner"}).of(THREAD)
    assert domain is not None and (domain.contact_type, domain.labelled) == (
        "partner",
        "@acme.com",
    )
    address = _contacts(
        SENDER, thread, labels={"@acme.com": "partner", "alice@acme.com": "customer"}
    ).of(THREAD)
    assert address is not None and address.contact_type == "customer"


def test_a_crm_saying_who_someone_is_counts_for_their_mail() -> None:
    crm = _person("crm.example.com/42", "alice@acme.com", "customer")
    assert _contacts(SENDER, crm, _thread(SENDER)).importance(THREAD) == 1


def test_where_tools_disagree_the_first_plugins_type_wins() -> None:
    first = _person("crm.example.com/42", "alice@acme.com", "vendor")
    second = _person("support.example.com/7", "alice@acme.com", "partner")
    found = _contacts(SENDER, first, second, _thread(SENDER)).of(THREAD)
    assert found is not None and found.contact_type == "vendor"


def test_a_label_that_cant_be_set_any_more_can_still_be_cleared() -> None:
    kept = {"@users.noreply.github.com": "customer"}
    assert people.clear_label(" @Users.Noreply.GitHub.com", lambda k: bool(kept.pop(k, None))) == (
        "@users.noreply.github.com",
        True,
    )
    assert kept == {}
    with pytest.raises(ValueError, match="anyone can have"):
        people.clear_label("@users.noreply.github.com", lambda k: False)
    assert people.clear_label("Bob@Acme.com", lambda k: False) == ("bob@acme.com", False)


def test_a_tools_type_is_read_as_mine_would_be() -> None:
    crm = _person("crm.example.com/42", "alice@acme.com", "Customer")
    contact = _contacts(SENDER, crm, _thread(SENDER)).of(THREAD)
    assert contact is not None and (contact.contact_type, contact.importance) == ("customer", 1)
    odd = _person("crm.example.com/42", "alice@acme.com", "<b>")
    assert (
        _contacts(SENDER, odd, _thread(SENDER))
        .additions(Item(title="Hello", subjects=[THREAD]))
        .badges
        == []
    )


def test_a_tool_can_say_how_much_someone_matters_for_all_their_mail() -> None:
    crm = _person("crm.example.com/42", "alice@acme.com", importance=2)  # a VIP deal's contact
    assert _contacts(SENDER, crm, _thread(SENDER)).importance(THREAD) == 2


def test_the_highest_of_a_tools_type_and_what_a_tool_says_counts() -> None:
    starred = _person("contacts/alice", "alice@acme.com", "vendor", importance=1)  # vendor: 0
    contact = _contacts(SENDER, starred, _thread(SENDER)).of(THREAD)
    assert contact is not None and (contact.importance, contact.stated) == (1, True)
    customer = _person("crm/alice", "alice@acme.com", "customer", importance=0)  # customer: 1
    contact = _contacts(SENDER, customer, _thread(SENDER)).of(THREAD)
    assert contact is not None and (contact.importance, contact.stated) == (1, False)


def test_a_type_i_set_wins_over_what_a_tool_says() -> None:
    vip = _person("crm/bob", "alice@acme.com", importance=2)
    labels = {"alice@acme.com": "vendor"}  # I say: 0
    assert _contacts(SENDER, vip, _thread(SENDER), labels=labels).importance(THREAD) == 0


def test_a_type_on_their_domain_doesnt_lower_what_a_tool_says() -> None:
    vip = _person("crm/ceo", "alice@acme.com", importance=2)
    labels = {"@acme.com": "customer"}  # 1, for everyone there
    assert _contacts(SENDER, vip, _thread(SENDER), labels=labels).importance(THREAD) == 2


def test_a_stated_importance_above_the_type_is_badged() -> None:
    vip = _person("crm/alice", "alice@acme.com", importance=2)
    added = _contacts(SENDER, vip, _thread(SENDER)).additions(Item(title="x", subjects=[THREAD]))
    assert added.badges == ["VIP"]


def test_a_stated_importance_must_be_one_of_the_three() -> None:
    with pytest.raises(ValidationError):
        Person(handle="x", importance=3)


def test_a_type_not_in_the_config_gives_no_importance() -> None:
    assert (
        _contacts(SENDER, _thread(SENDER), labels={"@acme.com": "friend"}).importance(THREAD) == 0
    )


def test_an_item_about_no_one_gets_nothing() -> None:
    contacts = _contacts(Entity(ref=THREAD, title="Hello"))
    assert contacts.of(THREAD) is None
    added = contacts.additions(Item(title="Hello", subjects=[THREAD]))
    assert added.badges == [] and added.actions == []


def test_an_item_shows_its_type_and_offers_to_set_it_for_the_person_or_their_domain() -> None:
    contacts = _contacts(SENDER, _thread(SENDER), labels={"@acme.com": "customer"})
    added = contacts.additions(Item(title="Hello", subjects=[THREAD]))
    assert added.badges == ["customer"]
    set_type, clear = added.actions
    assert set_type.name == people.CONTACT_ACTION
    assert set_type.choices == {
        "alice@acme.com": "alice@acme.com",
        "everyone at acme.com": "@acme.com",
    }
    assert set_type.suggestions == ["customer", "partner", "vendor"]
    assert clear.name == people.CLEAR_CONTACT_ACTION
    assert clear.fields == {"who": "@acme.com"}
    # It clears everyone there, so it says so and asks.
    assert clear.label == "Clear type for everyone at acme.com" and clear.confirm


def test_clearing_one_address_asks_first_too() -> None:
    contacts = _contacts(SENDER, _thread(SENDER), labels={"alice@acme.com": "customer"})
    _, clear = contacts.additions(Item(title="Hello", subjects=[THREAD])).actions
    assert (clear.label, clear.fields, clear.confirm) == (
        "Clear type",
        {"who": "alice@acme.com"},
        "Clear the type of alice@acme.com?",
    )


def test_a_public_domain_is_not_offered_and_nothing_is_cleared_without_a_label() -> None:
    sender = _person("gmail.com/alice", "alice@gmail.com")
    added = _contacts(sender, _thread(sender)).additions(Item(title="Hello", subjects=[THREAD]))
    assert [a.label for a in added.actions] == ["Set type"]
    assert added.actions[0].choices == {"alice@gmail.com": "alice@gmail.com"}


def test_someone_without_an_address_can_only_have_a_tools_type() -> None:
    sender = _person("github.com/alice", None, "customer")
    added = _contacts(sender, _thread(sender)).additions(Item(title="Hello", subjects=[THREAD]))
    assert added.badges == ["customer"] and added.actions == []


def _ranked(importance: act_now.ContactImportance, priority: act_now.PriorityOf) -> list[Item]:
    def nomination(title: str, subject: Ref, **signals: Any) -> Nomination:
        item = Item(
            title=title,
            subjects=[subject],
            signals=Signals(task="reply", **signals),
        )
        return Nomination("mail", item)

    nominated = [
        nomination("someone else", Ref(kind="mail.thread", id="t2")),
        nomination("customer", THREAD),
    ]
    shown = act_now.panels(nominated, now=NOW, priority_of=priority, contact_importance=importance)
    return sorted((i for p in shown for i in p.items), key=lambda i: -i.rank)


def test_act_now_ranks_a_customer_higher_unless_i_said_otherwise() -> None:
    contacts = _contacts(SENDER, _thread(SENDER), labels={"@acme.com": "customer"})
    shown = _ranked(contacts.importance, act_now.no_priority)
    assert [i.title for i in shown] == ["customer", "someone else"]
    assert shown[0].details[0] == "why: important"

    def not_important(ref: Ref) -> int | None:
        return 0 if ref == THREAD else None

    assert [i.title for i in _ranked(contacts.importance, not_important)] == [
        "someone else",
        "customer",
    ]


def test_a_contact_never_lowers_what_a_plugin_said() -> None:
    nomination = Nomination(
        "mail", Item(title="x", subjects=[THREAD], signals=Signals(task="reply", importance=2))
    )
    shown = act_now.panels([nomination], now=NOW, contact_importance=lambda ref: 1)
    assert shown[0].items[0].details[0] == "why: critical"


def test_a_panel_ordered_by_importance_counts_the_contact() -> None:
    panel = Panel(
        key="threads",
        priority=1,
        title="Threads",
        description="Threads, most important first.",
        sort_by_importance=True,
        items=[
            Item(title="other", rank=1, subjects=[Ref(kind="mail.thread", id="t2")]),
            Item(title="customer", subjects=[THREAD]),
        ],
    )

    def importance(ref: Ref) -> int:
        return 1 if ref == THREAD else 0

    ordered = panels._sorted(panel, contact_importance=importance)  # pyright: ignore[reportPrivateUsage]
    assert [i.title for i in ordered.items] == ["customer", "other"]


def test_the_commands_set_list_and_clear_a_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db.as_posix()}"\n')
    assert cli.main(["-c", str(config), "contact", "@Acme.com", "Customer"]) == 0
    capsys.readouterr()
    assert cli.main(["-c", str(config), "contacts"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"who": "@acme.com", "contact_type": "customer"}]
    assert cli.main(["-c", str(config), "contact", "@acme.com", "clear"]) == 0
    capsys.readouterr()
    assert cli.main(["-c", str(config), "contacts"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["-c", str(config), "contact", "@gmail.com", "customer"]) == 1
    assert "anyone can have" in capsys.readouterr().err
    assert cli.main(["-c", str(config), "contact", "@acme.com", "clear"]) == 0
    assert capsys.readouterr().out == "@acme.com had no type\n"
    # One kept on what can't be labelled any more still clears.
    ContactRepository(Database(db)).set("@users.noreply.github.com", "customer", NOW)
    assert cli.main(["-c", str(config), "contact", "@users.noreply.github.com", "clear"]) == 0
    assert capsys.readouterr().out == "@users.noreply.github.com: type cleared\n"


class _NoSettings(Strict):
    pass


class _InboxPlugin(Plugin[BaseModel, BaseModel]):
    """A plugin publishing a thread from SENDER, and an item about it to act on."""

    name = "inbox"
    title = "Inbox"
    api_version = API_VERSION
    config_model = _NoSettings
    always_on = True
    kinds: ClassVar[dict[str, KindSpec]] = {"inbox.thread": KindSpec(KindModel)}

    def entities(self, req: EntitiesRequest[BaseModel, BaseModel]) -> list[Entity]:
        return [Entity(ref=INBOX_THREAD, title="Hello", refs=[SENDER.ref])]

    def panels(self, req: PanelsRequest[BaseModel, BaseModel]) -> list[Panel]:
        item = Item(title="Hello", subjects=[INBOX_THREAD])
        return [
            Panel(
                key="threads",
                priority=1,
                title="Threads",
                description="Mail to answer.",
                items=[item],
                act_now=True,
                signals=Signals(verb="Reply", task="reply"),
            )
        ]


INBOX_THREAD = Ref(kind="inbox.thread", id="t1")


def test_the_page_badges_ranks_and_offers_to_set_the_type_of_who_an_item_is_about() -> None:
    inbox = Harness(_InboxPlugin()).with_entities([SENDER])
    ContactRepository(inbox.db).set("@acme.com", "customer", NOW)
    page = inbox.dashboard()
    [shown] = [i for p in page.panels if p.key == "inbox.threads" for i in p.items]
    [ranked] = inbox.act_now()
    for item in (shown, ranked):
        assert "customer" in item.badges
        assert {a.label for a in item.actions} >= {
            "Set type",
            "Clear type for everyone at acme.com",
        }
    assert "why: important" in ranked.details
    html = inbox.render_item(shown)
    assert "<select name='choice'" in html and "everyone at acme.com" in html


def test_nothing_is_added_while_no_one_is_set() -> None:
    inbox = Harness(_InboxPlugin()).with_entities([SENDER])
    [ranked] = inbox.act_now()
    assert "customer" not in ranked.badges
    assert "Clear type" not in {a.label for a in ranked.actions}
    assert "why: important" not in ranked.details


def _config() -> tuple[Config, Database]:
    return Config(email="me@example.com"), Database(":memory:")


def test_the_buttons_set_and_clear_a_type() -> None:
    config, db = _config()
    set_it = {"choice": "@Acme.com", "input": " Customer "}
    assert refresh.perform_action(config, db, people.CONTACT_ACTION, set_it).notice == (
        "@acme.com: customer"
    )
    assert ContactRepository(db).all() == {"@acme.com": "customer"}
    cleared = refresh.perform_action(config, db, people.CLEAR_CONTACT_ACTION, {"who": "@Acme.com"})
    assert cleared.notice == "@acme.com: type cleared"
    assert ContactRepository(db).all() == {}
    again = refresh.perform_action(config, db, people.CLEAR_CONTACT_ACTION, {"who": "@acme.com"})
    assert again.notice == "@acme.com had no type"


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"choice": "@gmail.com", "input": "customer"}, "anyone can have"),
        ({"choice": "@acme.com", "input": "<b>"}, "isn't a contact type"),
        ({"choice": "@acme.com"}, "missing field"),
    ],
)
def test_the_button_checks_what_it_is_given(fields: dict[str, str], says: str) -> None:
    config, db = _config()
    with pytest.raises(ActionError, match=says):
        refresh.perform_action(config, db, people.CONTACT_ACTION, fields)
    assert ContactRepository(db).all() == {}


def test_what_their_organisation_is_counts_for_its_peoples_mail() -> None:
    acme = _organisation("crm.example.com/acme", "customer")
    crm = _at(_person("crm.example.com/42", "alice@acme.com"), acme)
    contact = _contacts(SENDER, crm, acme, _thread(SENDER)).of(THREAD)
    assert contact is not None and (contact.contact_type, contact.importance) == ("customer", 1)
    key = _organisation("crm.example.com/acme", importance=2)  # a key account
    crm = _at(_person("crm.example.com/42", "alice@acme.com"), key)
    assert _contacts(SENDER, crm, key, _thread(SENDER)).importance(THREAD) == 2


def test_what_a_tool_or_i_say_of_someone_beats_what_their_organisation_is() -> None:
    acme = _organisation("crm.example.com/acme", "customer")
    crm = _at(_person("crm.example.com/42", "alice@acme.com", "vendor"), acme)
    tool = _contacts(SENDER, crm, acme, _thread(SENDER)).of(THREAD)
    assert tool is not None and tool.contact_type == "vendor"
    crm = _at(_person("crm.example.com/42", "alice@acme.com"), acme)
    labels = {"@acme.com": "partner"}
    domain = _contacts(SENDER, crm, acme, _thread(SENDER), labels=labels).of(THREAD)
    assert domain is not None and domain.contact_type == "partner"


def test_only_the_organisation_a_person_points_at_is_theirs() -> None:
    acme = _organisation("crm.example.com/acme", "customer")
    pointing = acme.model_copy(update={"refs": [SENDER.ref]})  # it says nothing of who's at it
    contact = _contacts(SENDER, pointing, _thread(SENDER)).of(THREAD)
    assert contact is not None and contact.contact_type is None


def test_an_organisations_id_is_its_hosts() -> None:
    assert fits(ORGANISATION, "crm.example.com/acme")
    assert not fits(ORGANISATION, "Acme")
