"""The github plugin's table of people: GitHub logins and their commit emails, for mapping
commit authors to reviewers to suggest."""

from datetime import datetime

from ...api import Database, LookupCache, cell, tables

REVIEWER_MAP_FILE = "reviewer-map.md"


def read_reviewer_map(text: str) -> dict[str, str]:
    """Emails by login, from the Users table of `reviewer-map.md` (login, email, Slack id)."""
    return {
        login: email
        for row in tables(text).get("Users", [])
        if (login := cell(row, 0)) and (email := cell(row, 1))
    }


class PeopleRepository:
    """Logins' commit emails: imported from the reviewer map (`mfd github import`), or looked
    up in the person's commits; an import wins over a lookup, which never overwrites."""

    def __init__(self, db: Database):
        self._people = LookupCache(db, "github_people", "login", "email")

    def emails(self) -> dict[str, str | None]:
        """Every known login's email; None where a lookup found none."""
        return self._people.values()

    def save_looked_up(self, login: str, email: str | None, now: datetime) -> None:
        """Remember what a lookup found (or that it found nothing), unless the login is known."""
        self._people.save_looked_up(login, email, now)

    def forget_misses_before(self, cutoff: datetime) -> int:
        """Forget lookups that found no email before `cutoff`, so they're tried again (the
        person may have committed with a work email since); how many. Emails found stay:
        people outlast the PRs they were found for, and suggestions need them."""
        return self._people.forget_misses_before(cutoff)

    def replace_imported(self, emails: dict[str, str]) -> None:
        """Replace what an earlier import read with these, over any lookup of the same login."""
        self._people.replace_imported(emails)
