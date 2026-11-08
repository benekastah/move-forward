"""The families of entity kinds, core's and what a plugin needs to declare its own, for
plugins to publish and read."""

from ..kinds import (
    FAMILIES,
    PERSON,
    PULL_REQUEST,
    REVIEW,
    TICKET,
    Family,
    Person,
    PullRequest,
    PullRequestFetched,
    Ticket,
    TicketFetched,
    is_host_login,
    is_url,
)

__all__ = [
    "FAMILIES",
    "PERSON",
    "PULL_REQUEST",
    "REVIEW",
    "TICKET",
    "Family",
    "Person",
    "PullRequest",
    "PullRequestFetched",
    "Ticket",
    "TicketFetched",
    "is_host_login",
    "is_url",
]
