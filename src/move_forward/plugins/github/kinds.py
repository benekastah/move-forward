"""The entity kinds github publishes, with their attributes; see `docs/entities.md`."""

from move_forward.api.kinds import Person, PullRequest, Team


class GitHubPullRequest(PullRequest):
    """A `pull_request` (the pull request family), with what only github has."""

    number: int
    """Its number in its repo; `handle` is `#<number>`."""
    base: str
    author: str | None


class GitHubPerson(Person):
    """A `person`: me, or anyone github knows an email for; `handle` is their login."""


class GitHubTeam(Team):
    """A `team`: one of mine (`[github] teams`), or one a PR requests; `handle` is its slug."""
