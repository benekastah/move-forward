import json
import shlex
import sys
import time
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pytest
from conftest import NOW, entry, pr, snapshot, stored, todo

from move_forward import cli, localtime, refresh, snooze
from move_forward.act_now import ActNowConfig, Feedback
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.plugins.github.models import Check
from move_forward.repositories import FeedbackRepository


@pytest.mark.parametrize(
    ("text", "span"),
    [("7d", timedelta(days=7)), ("12h", timedelta(hours=12))],
)
def test_durations_are_days_or_hours(text: str, span: timedelta) -> None:
    assert snooze.parse_duration(text) == span


@pytest.mark.parametrize("text", ["", "7", "d", "0d", "1w", "-1d", "1.5h"])
def test_other_durations_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="isn't a duration like 7d or 12h, nor changed"):
        snooze.parse_duration(text)


def _snooze(
    config: Config, db: Database, ref: Ref, task: str | None = None, **duration: float
) -> list[datetime | None]:
    """What `mfd snooze` does: run the Snooze buttons on the items about `ref`."""
    span = timedelta(**duration) if duration else None
    buttons = snooze.snooze_buttons(refresh.build_dashboard(config, db, NOW), ref, task)
    return [refresh.snooze_item(config.act_now, db, f, NOW, span) for f in buttons]


def test_a_snoozed_review_waits_dimmed_in_reviews_for_me_until_it_is_woken() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice", head_oid="a1")]))
    url = pr(1).url

    assert _snooze(config, db, Ref(kind="pull_request", id=url), days=7) == [
        NOW + timedelta(days=7)
    ]
    [item] = refresh.build_dashboard(config, db, NOW).panel("github.reviews_for_me").items
    assert item.set_aside
    assert "Unsnooze" in [a.label for a in item.actions]
    assert FeedbackRepository(db).all()[("pull_request", url, "review")].fingerprint == "a1"
    assert not any(
        i.subjects[0].id == url for i in refresh.build_dashboard(config, db, NOW).act_now()
    )


def test_the_command_snoozes_as_the_button_does_until_the_item_changes() -> None:
    work = [entry("APP-1 finish", [todo("reply", "quick")])]
    config, db = stored(work_items=work)
    ref = Ref(kind="notes.entry", id="APP-1 finish")
    [shown] = [
        i for i in refresh.build_dashboard(config, db, NOW).act_now() if i.subjects[0] == ref
    ]
    button = next(a for a in shown.actions if a.label == "Snooze")

    assert _snooze(config, db, ref)
    said = FeedbackRepository(db).all()[(ref.kind, ref.id, button.fields["task"])]
    assert said.fingerprint == button.fields["fingerprint"]  # the button's, so it ends as one
    assert ref not in [i.subjects[0] for i in refresh.build_dashboard(config, db, NOW).act_now()]

    changed = [entry("APP-1 finish", [todo("reply", "quick"), todo("ship", "quick")])]
    config, later = stored(work_items=changed)
    for key, one in FeedbackRepository(db).all().items():
        FeedbackRepository(later).say(key, one, NOW)
    back = refresh.build_dashboard(config, later, NOW).act_now()
    assert ref in [i.subjects[0] for i in back]


def test_only_the_task_given_is_snoozed_and_nothing_without_a_button() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    ref = Ref(kind="pull_request", id=pr(1).url)
    assert _snooze(config, db, ref, "merge") == []
    assert _snooze(config, db, Ref(kind="pull_request", id="https://x/pull/9")) == []
    assert FeedbackRepository(db).all() == {}


def test_a_task_given_is_snoozed_alone_among_the_entitys_others() -> None:
    failing = [Check(name="tests", state="FAILURE", url="https://ci/1")]
    mine = pr(1, mergeable="CONFLICTING", checks=failing)  # conflicts, and CI failing
    config, db = stored(github=snapshot(mine=[mine]))
    ref = Ref(kind="pull_request", id=mine.url)
    assert _snooze(config, db, ref, "resolve_conflicts")
    snoozed = {task for _, _, task in FeedbackRepository(db).all()}
    # With what Act now shows alongside it, as the button snoozes; but not the CI failure.
    assert "resolve_conflicts" in snoozed and "fix_ci" not in snoozed


def test_unsnooze_brings_back_anything_set_aside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n')
    assert cli.main(["-c", str(config), "notes", "add-entry", "Chores"]) == 0
    feedback = FeedbackRepository(Database(db_path))
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    said: list[tuple[str, Literal["snooze", "pin"], datetime | None]] = [
        ("close", "snooze", None),  # until it changes
        ("plan", "snooze", tomorrow),
        ("other", "pin", None),
    ]
    for task, action, until in said:
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


def test_without_a_duration_the_config_for_the_kind_says_how_long() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, head_oid="a1")]))
    config = config.model_copy(update={"act_now": ActNowConfig(snooze_days={"pull_request": 3})})
    [until] = _snooze(config, db, Ref(kind="pull_request", id=pr(1).url))
    assert until is not None and timedelta(days=2) < until - NOW <= timedelta(days=3)


def test_mfd_snooze_takes_any_entity_and_refuses_one_it_cannot_snooze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n', encoding="utf-8")
    assert cli.main(["-c", str(config), "notes", "add-entry", "Bob's chores"]) == 0
    assert (
        cli.main(
            ["-c", str(config), "notes", "todo", "add", "Bob's chores", "sweep", "--size", "quick"]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["-c", str(config), "snooze", "Bob's chores", "2d"]) == 0
    assert "snoozed 1 item(s) until " in capsys.readouterr().out
    [(kind, id, _)] = FeedbackRepository(Database(db_path)).all()
    assert (kind, id) == ("notes.entry", "Bob's chores")
    assert cli.main(["-c", str(config), "snooze", "Bob's chores"]) == 1  # snoozed: no Snooze button
    said = capsys.readouterr().err
    assert "nothing about Bob's chores" in said
    again = cli.command_line(["mfd", "unsnooze", "Bob's chores", "--kind", "notes.entry"])
    assert f"`{again}` first" in said
    assert cli.main(["-c", str(config), "snooze", "Nothing"]) == 1
    assert cli.main(["-c", str(config), "snooze", "Bob's chores", "1w"]) == 1


def test_mfd_snooze_changed_snoozes_until_the_item_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n', encoding="utf-8")
    assert cli.main(["-c", str(config), "notes", "add-entry", "Chores"]) == 0
    todo = ["notes", "todo", "add", "Chores", "sweep", "--size", "quick"]
    assert cli.main(["-c", str(config), *todo]) == 0
    capsys.readouterr()
    assert cli.main(["-c", str(config), "snooze", "Chores", "changed"]) == 0
    assert capsys.readouterr().out == "snoozed 1 item(s) until each changes\n"
    [said] = FeedbackRepository(Database(db_path)).all().values()
    assert (said.action, said.until) == ("snooze", None)
    assert cli.main(["-c", str(config), "snoozes"]) == 0
    [listed] = json.loads(capsys.readouterr().out)
    assert (listed["id"], listed["until"]) == ("Chores", None)

    another = ["notes", "todo", "add", "Chores", "mop", "--size", "quick"]
    assert cli.main(["-c", str(config), *another]) == 0  # the item changed: the snooze is over
    capsys.readouterr()
    assert cli.main(["-c", str(config), "snoozes"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_mfd_snoozes_lists_only_the_snoozes_whose_time_isnt_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n', encoding="utf-8")
    assert cli.main(["-c", str(config), "snoozes"]) == 0  # creates and migrates the database
    assert json.loads(capsys.readouterr().out) == []
    for title in ("Chores", "Errands"):
        assert cli.main(["-c", str(config), "notes", "add-entry", title]) == 0
        todo = ["notes", "todo", "add", title, "sweep", "--size", "quick"]
        assert cli.main(["-c", str(config), *todo]) == 0
        assert cli.main(["-c", str(config), "snooze", title, "2d"]) == 0
    feedback = FeedbackRepository(Database(db_path))
    now = datetime.now(UTC)
    [(key, said)] = [(k, f) for k, f in feedback.all().items() if k[1] == "Errands"]
    feedback.say(key, said.model_copy(update={"until": now - timedelta(hours=1)}), now)
    capsys.readouterr()
    assert cli.main(["-c", str(config), "snoozes"]) == 0
    [listed] = json.loads(capsys.readouterr().out)
    assert (listed["kind"], listed["id"]) == ("notes.entry", "Chores")
    assert listed["until"] is not None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows splits a command line its own way")
def test_a_command_line_reads_back_as_its_words_in_a_posix_shell() -> None:
    words = ["mfd", "unsnooze", 'Bob\'s "chores" & $HOME `x`', "--kind", "notes.entry"]
    assert shlex.split(cli.command_line(words)) == words


@pytest.mark.skipif(
    sys.platform != "win32", reason="POSIX shells split a command line their own way"
)
def test_a_command_line_quotes_windows_arguments_with_spaces_or_quotes() -> None:
    words = ["mfd", "unsnooze", 'Bob\'s "chores"', "--kind", "notes.entry"]
    assert cli.command_line(words) == 'mfd unsnooze "Bob\'s \\"chores\\"" --kind notes.entry'


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
    ends = ActNowConfig(snooze_until_hour=9).snooze_until(
        "pull_request", datetime(2026, 10, 31, 16, tzinfo=UTC)
    )
    assert ends.astimezone(UTC) == datetime(2026, 11, 1, 14, tzinfo=UTC)  # a snooze across it
    # 02:30 doesn't happen on the day they go forward: read as an hour on, not refused.
    assert localtime.at(date(2026, 3, 8), dt_time(2, 30)).astimezone(UTC) == datetime(
        2026, 3, 8, 7, 30, tzinfo=UTC
    )
