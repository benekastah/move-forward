"""What the notes read in the text I write them."""

from move_forward.plugins.notes.models import Condition
from move_forward.plugins.notes.text import attach_conditions, sized


def test_a_condition_is_given_the_pr_it_names_else_each_of_its_entrys() -> None:
    pr_1, pr_2 = "https://github.com/acme/widgets/pull/1", "https://ghe.acme.com/acme/api/pull/2"
    conditions, kept = attach_conditions(
        [f"{pr_2}: after the migration", "after the release"], [pr_1, pr_2]
    )
    assert conditions == [
        Condition(pr=pr_2, text="after the migration"),
        Condition(pr=pr_1, text="after the release"),
        Condition(pr=pr_2, text="after the release"),
    ]
    assert kept == []


def test_a_condition_without_any_pr_is_kept_as_a_line() -> None:
    assert attach_conditions(["ask Sam first"], []) == ([], ["- Merge condition: ask Sam first"])


def test_a_todos_size_is_read_from_its_start() -> None:
    assert sized("[Quick] fix it") == ("fix it", "quick")
    assert sized("[big] rewrite") == ("rewrite", "big")
    assert sized("fix [quick] it") == ("fix [quick] it", None)
