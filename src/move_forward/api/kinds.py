"""The families of entity kinds, core's and what a plugin needs to declare its own, for
plugins to publish and read."""

from move_forward.kinds import (
    FAMILIES,
    PERSON,
    PULL_REQUEST,
    REVIEW,
    TAG,
    TEAM,
    TICKET,
    Family,
    Person,
    PullRequest,
    PullRequestFetched,
    Team,
    Ticket,
    TicketFetched,
    is_host_login,
    is_host_path,
    is_url,
)

__all__ = [
    "FAMILIES",
    "PERSON",
    "PULL_REQUEST",
    "REVIEW",
    "TAG",
    "TEAM",
    "TICKET",
    "Family",
    "Person",
    "PullRequest",
    "PullRequestFetched",
    "Team",
    "Ticket",
    "TicketFetched",
    "is_host_login",
    "is_host_path",
    "is_url",
]
