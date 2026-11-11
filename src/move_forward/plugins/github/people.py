"""The github plugin's table of people: GitHub logins and their commit emails, for mapping
commit authors to reviewers to suggest."""

from datetime import datetime

from ...api import Database, LookupCache


class PeopleRepository:
    """Logins' commit emails, looked up in the person's commits; a lookup never overwrites
    one known already."""

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
