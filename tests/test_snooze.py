from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from conftest import NOW, pr, snapshot, stored

from move_forward import cli, localtime, refresh, snooze
from move_forward.act_now import ActNowConfig, Feedback
from move_forward.db import Database
from move_forward.repositories import FeedbackRepository


@pytest.mark.parametrize(
    ("text", "span"),
    [("7d", timedelta(days=7)), ("12h", timedelta(hours=12))],
)
def test_durations_are_days_or_hours(text: str, span: timedelta) -> None:
    assert snooze.parse_duration(text) == span


@pytest.mark.parametrize("text", ["", "7", "d", "0d", "1w", "-1d", "1.5h"])
def test_other_durations_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="isn't a duration"):
        snooze.parse_duration(text)


def test_a_snoozed_review_waits_dimmed_in_reviews_for_me_until_it_is_woken() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice", head_oid="a1")]))
    url = pr(1).url
    graph, _ = refresh.stored_graph(db, refresh.enabled(config, db, NOW))
    feedback = FeedbackRepository(db)

    until = snooze.snooze_review(feedback, graph, ActNowConfig(), url, NOW, timedelta(days=7))

    assert until == NOW + timedelta(days=7)
    [item] = refresh.build_dashboard(config, db, NOW).panel("github.reviews_for_me").items
    assert item.set_aside == "snooze"
    assert "Unsnooze" in [a.label for a in item.actions]
    assert feedback.all()[("pull_request", url, "review")].fingerprint == "a1"
    assert not any(
        i.subjects[0].id == url for i in refresh.build_dashboard(config, db, NOW).act_now()
    )


def test_unsnooze_brings_back_anything_set_aside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n')
    assert cli.main(["-c", str(config), "notes", "add-entry", "Chores"]) == 0
    feedback = FeedbackRepository(Database(db_path))
    said: list[tuple[str, Literal["dismiss", "snooze", "pin"]]] = [
        ("close", "dismiss"),
        ("plan", "snooze"),
        ("other", "pin"),
    ]
    for task, action in said:
        until = NOW + timedelta(days=1) if action == "snooze" else None
        feedback.say(
            ("notes.entry", "Chores", task),
            Feedback(action=action, fingerprint="f", until=until),
            NOW,
        )
    assert cli.main(["-c", str(config), "unsnooze", "Chores", "--task", "plan"]) == 0
    assert set(FeedbackRepository(Database(db_path)).all()) == {
        ("notes.entry", "Chores", "close"),
        ("notes.entry", "Chores", "other"),
    }
    assert cli.main(["-c", str(config), "unsnooze", "Chores"]) == 0
    assert set(FeedbackRepository(Database(db_path)).all()) == {("notes.entry", "Chores", "other")}
    assert cli.main(["-c", str(config), "unsnooze", "Nothing"]) == 1
    assert "brought back 1 item(s)" in capsys.readouterr().out


def test_without_a_duration_the_config_for_prs_says_how_long() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, head_oid="a1")]))
    graph, _ = refresh.stored_graph(db, refresh.enabled(config, db, NOW))
    until = snooze.snooze_review(
        FeedbackRepository(db), graph, ActNowConfig(snooze_days={"pull_request": 3}), pr(1).url, NOW
    )
    assert timedelta(days=2) < until - NOW <= timedelta(days=3)


def test_an_unknown_pr_cannot_be_snoozed() -> None:
    config, db = stored(github=snapshot())
    graph, _ = refresh.stored_graph(db, refresh.enabled(config, db, NOW))
    with pytest.raises(LookupError, match="mfd refresh"):
        snooze.snooze_review(FeedbackRepository(db), graph, ActNowConfig(), "https://x/pull/9", NOW)


def test_a_snooze_ends_at_the_configured_local_hour_of_its_last_day() -> None:
    config = ActNowConfig(snooze_until_hour=9)
    local = localtime.local(config.snooze_until("pull_request", NOW))
    tomorrow = localtime.local(NOW).date() + timedelta(days=1)
    assert (local.date(), local.hour, local.minute) == (tomorrow, 9, 0)
    midnight = localtime.local(ActNowConfig().snooze_until("pull_request", NOW))
    assert (midnight.date(), midnight.hour) == (tomorrow, 0)
