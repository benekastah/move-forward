"""Where review pings go: the /slack/contacts page, its buttons, and `mfd slack contact` and
`mfd slack destinations`."""

import json
from pathlib import Path

import pytest
from conftest import NOW, Reviewer, pr, snapshot, stored

from move_forward import cli, refresh
from move_forward.db import Database
from move_forward.plugin import ActionError
from move_forward.plugins.slack.repository import SlackRepository


def test_the_page_lists_requested_reviewers_and_where_their_pings_go() -> None:
    mine = pr(1, requested_users=["alice", "<b>"], requested_teams=["web"])
    config, db = stored(
        github=snapshot(mine=[mine]), users={"alice": Reviewer(login="alice", slack_id="U111111")}
    )
    SlackRepository(db).set_team_channel("web", "#team-web", None)
    page = refresh.plugin_page(config, db, "/slack/contacts", {})
    assert isinstance(page, str)
    assert "alice" in page and "value='U111111'" in page and "imported" in page
    assert "&lt;b&gt;" in page and "<b>" not in page  # escaped; and not known yet
    assert "not known" in page and "set by hand" in page and "#team-web" in page
    assert "action='/actions/slack/set_contact?back=/slack/contacts'" in page


def test_setting_by_hand_checks_the_ids_and_wins_over_a_lookup() -> None:
    config, db = stored(slack=True)
    notice = refresh.run_action(
        config, db, "slack/set_contact", {"login": "bob", "slack_id": " U222222 "}
    )
    assert notice == "bob's pings go to U222222"
    with pytest.raises(ActionError, match="isn't a Slack member id"):
        refresh.run_action(config, db, "slack/set_contact", {"login": "bob", "slack_id": "x"})
    slack = SlackRepository(db)
    slack.save_looked_up("bob", "U999999", NOW)
    assert slack.contacts()["bob"].slack_id == "U222222"
    assert slack.sources()["user:bob"] == "dashboard"


def test_mfd_slack_destinations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n\n[slack]\n')

    def mfd(*args: str) -> int:
        return cli.main(["-c", str(config), "slack", *args])

    assert mfd("contact", "set", "alice", "U111111") == 0
    assert mfd("team", "set", "web", "#team-web", "--id", "C123456") == 0
    capsys.readouterr()
    assert mfd("destinations", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["people"] == [{"login": "alice", "slack_id": "U111111", "source": "dashboard"}]
    assert listed["teams"][0]["channel_id"] == "C123456"
    assert mfd("contact", "set", "alice", "nope") == 1
    assert mfd("contact", "unset", "alice") == 0 and mfd("contact", "unset", "alice") == 1
    assert mfd("team", "unset", "web") == 0
    assert SlackRepository(Database(tmp_path / "d.db")).contacts() == {}


def test_clear_forgets_a_teams_channel_but_a_blank_form_does_not() -> None:
    config, db = stored(slack=True, github=snapshot())
    slack = SlackRepository(db)
    slack.set_team_channel("web", "#team-web", None)
    with pytest.raises(ActionError, match="a channel name or id is needed"):
        refresh.perform_action(config, db, "slack/set_team_channel", {"team": "web"})
    assert "web" in slack.team_channels()
    notice = refresh.run_action(config, db, "slack/clear_team_channel", {"team": "web"})
    assert notice == "forgot web's channel"
    assert "web" not in slack.team_channels()
    with pytest.raises(ActionError, match="has no channel set"):
        refresh.perform_action(config, db, "slack/clear_team_channel", {"team": "web"})


def test_the_contacts_page_offers_clear_with_a_confirm_for_a_team_with_a_channel() -> None:
    config, db = stored(slack=True, github=snapshot())
    SlackRepository(db).set_team_channel("web", "#team-web", None)
    page = str(refresh.plugin_page(config, db, "/slack/contacts", {}))
    assert "/actions/slack/clear_team_channel" in page and "Forget web" in page
    SlackRepository(db).unset_team_channel("web")
    assert "clear_team_channel" not in str(refresh.plugin_page(config, db, "/slack/contacts", {}))


def test_clear_forgets_a_persons_slack_id_so_its_looked_up_again() -> None:
    config, db = stored(slack=True, github=snapshot())
    slack = SlackRepository(db)
    slack.set_contact("bob", None)  # a blank form: kept unlinked
    page = str(refresh.plugin_page(config, db, "/slack/contacts", {}))
    assert "/actions/slack/clear_contact" in page and "Forget what" in page
    assert refresh.run_action(config, db, "slack/clear_contact", {"login": "bob"}) == (
        "forgot what was kept for bob; they're looked up again"
    )
    assert "bob" not in slack.contacts()
    with pytest.raises(ActionError, match="nothing is kept for bob"):
        refresh.perform_action(config, db, "slack/clear_contact", {"login": "bob"})
