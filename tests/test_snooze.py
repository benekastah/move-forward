import sys
import time
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

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
        until = datetime.now(UTC) + timedelta(days=1) if action == "snooze" else None
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


PACIFIC = timezone(timedelta(hours=-8))


def test_only_snoozes_still_running_come_back_or_stay_whatever_their_offset() -> None:
    feedback = FeedbackRepository(Database(":memory:"))
    now = datetime(2026, 11, 10, 10, tzinfo=UTC)
    running = datetime(2026, 11, 10, 9, tzinfo=PACIFIC)  # 17:00 UTC
    over = datetime(2026, 11, 10, 1, tzinfo=PACIFIC)  # 09:00 UTC
    for task, until in (("running", running), ("over", over)):
        feedback.say(
            ("x.a", "1", task), Feedback(action="snooze", fingerprint="f", until=until), now
        )
    assert feedback.bring_back("x.a", "1", None, now) == 1  # not the snooze that's over
    assert set(feedback.all()) == {("x.a", "1", "over")}
    feedback.say(
        ("x.a", "1", "running"), Feedback(action="snooze", fingerprint="f", until=running), now
    )
    feedback.prune(now, now - timedelta(days=30))
    assert set(feedback.all()) == {("x.a", "1", "running")}  # the prune ends only what's over


def test_unsnooze_from_a_stale_page_leaves_a_pin_made_since() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    feedback = FeedbackRepository(db)
    key = ("pull_request", pr(1).url, "review")
    feedback.say(key, Feedback(action="pin", fingerprint="f"), NOW)
    fields = {"kind": key[0], "id": key[1], "task": key[2], "fingerprint": "f"}
    assert refresh.run_action(config, db, "act_now/unsnooze", fields) == "back"
    assert feedback.all()[key].action == "pin"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no time.tzset to set the zone")
def test_a_snooze_across_a_clock_change_ends_at_its_hour_in_the_systems_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(localtime, "zone", None)  # the system's, as `mfd serve` runs
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        before = datetime(2026, 10, 31, 16, tzinfo=UTC)  # Saturday, summer time
        ends = ActNowConfig(snooze_until_hour=9).snooze_until("pull_request", before)
        assert ends == datetime(2026, 11, 1, 14, tzinfo=UTC)  # 09:00 EST, the day it ends
    finally:
        monkeypatch.undo()
        time.tzset()


def test_a_local_time_on_a_day_takes_that_days_offset_through_a_gap_and_a_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(localtime, "zone", ZoneInfo("America/New_York"))
    assert localtime.at(date(2026, 11, 1), dt_time(9)).astimezone(UTC) == datetime(
        2026, 11, 1, 14, tzinfo=UTC
    )
    assert localtime.at(date(2026, 10, 31), dt_time(9)).astimezone(UTC) == datetime(
        2026, 10, 31, 13, tzinfo=UTC
    )
    # 01:30 happens twice on the day clocks go back: the first, still summer time.
    assert localtime.at(date(2026, 11, 1), dt_time(1, 30)).astimezone(UTC) == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    # 02:30 doesn't happen on the day they go forward: read as an hour on, not refused.
    assert localtime.at(date(2026, 3, 8), dt_time(2, 30)).astimezone(UTC) == datetime(
        2026, 3, 8, 7, 30, tzinfo=UTC
    )
