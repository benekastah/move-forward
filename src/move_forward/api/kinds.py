"""The families of entity kinds, core's and what a plugin needs to declare its own, for
plugins to publish and read."""

from ..kinds import (
    FAMILIES,
    ORGANISATION,
    PERSON,
    PULL_REQUEST,
    REVIEW,
    TAG,
    TICKET,
    Family,
    Organisation,
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
    "ORGANISATION",
    "PERSON",
    "PULL_REQUEST",
    "REVIEW",
    "TAG",
    "TICKET",
    "Family",
    "Organisation",
    "Person",
    "PullRequest",
    "PullRequestFetched",
    "Ticket",
    "TicketFetched",
    "is_host_login",
    "is_url",
]
