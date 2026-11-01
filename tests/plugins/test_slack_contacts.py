"""Where review pings go: the /slack/contacts page, its buttons, and `mfd slack contact(s)`."""

import json
from pathlib import Path

import pytest
from conftest import Reviewer, pr, snapshot, stored

from move_forward import cli, refresh
from move_forward.db import Database
from move_forward.plugin import ActionError
from move_forward.plugins.slack.models import Contact
from move_forward.plugins.slack.repository import SlackRepository


def test_the_page_lists_requested_reviewers_and_where_their_pings_go() -> None:
    mine = pr(1, requested_users=["alice", "<b>"], requested_teams=["web"])
    config, db = stored(
        github=snapshot(mine=[mine]), users={"alice": Reviewer(login="alice", slack_id="U111111")}
    )
    SlackRepository(db).set_team_channel("web", "#team-web", None)
    page = refresh.plugin_page(config, db, "/slack/contacts", {})
    assert page is not None
    assert "alice" in page and "value='U111111'" in page and "imported" in page
    assert "&lt;b&gt;" in page and "<b>" not in page  # escaped; and not known yet
    assert "not known" in page and "set by hand" in page and "#team-web" in page
    assert "action='/actions/slack/set_contact?back=/slack/contacts'" in page


def test_setting_by_hand_checks_the_ids_and_wins_over_imports() -> None:
    config, db = stored(slack=True)
    notice = refresh.run_action(
        config, db, "slack/set_contact", {"login": "bob", "slack_id": " U222222 "}
    )
    assert notice == "bob's pings go to U222222"
    with pytest.raises(ActionError, match="isn't a Slack member id"):
        refresh.run_action(config, db, "slack/set_contact", {"login": "bob", "slack_id": "x"})
    with pytest.raises(ActionError, match="a channel name or id is needed"):
        refresh.run_action(config, db, "slack/set_team_channel", {"team": "web"})
    slack = SlackRepository(db)
    slack.replace_imported([Contact(login="bob", slack_id="U999999")], [], [])
    assert slack.contacts()["bob"].slack_id == "U222222"
    assert slack.sources()["user:bob"] == "dashboard"


def test_mfd_slack_contacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n\n[slack]\n')

    def mfd(*args: str) -> int:
        return cli.main(["-c", str(config), "slack", *args])

    assert mfd("contact", "set", "alice", "U111111") == 0
    assert mfd("team", "set", "web", "#team-web", "--id", "C123456") == 0
    capsys.readouterr()
    assert mfd("contacts", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["people"] == [{"login": "alice", "slack_id": "U111111", "source": "dashboard"}]
    assert listed["teams"][0]["channel_id"] == "C123456"
    assert mfd("contact", "set", "alice", "nope") == 1
    assert mfd("contact", "unset", "alice") == 0 and mfd("contact", "unset", "alice") == 1
    assert mfd("team", "unset", "web") == 0
    assert SlackRepository(Database(tmp_path / "d.db")).contacts() == {}
