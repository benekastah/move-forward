"""Jira collector: my open issues, through the Jira Cloud REST API."""

import base64
import json
import re
import urllib.error
import urllib.request
from collections.abc import Collection
from datetime import date, datetime
from http import HTTPStatus
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from ...api import SourceError, http, keychain
from .config import JiraConfig
from .models import MAX_COMMENTS, JiraComment, JiraIssue, JiraSnapshot

SEARCH_PATH = "/rest/api/3/search/jql"
MYSELF_PATH = "/rest/api/3/myself"
PAGE_SIZE = 100
MY_OPEN_ISSUES_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
BLOCKED_BY_LINK = "is blocked by"
FIELDS = [
    "summary",
    "status",
    "issuetype",
    "labels",
    "priority",
    "created",
    "updated",
    "parent",
    "issuelinks",
    "resolutiondate",
    "duedate",
    "comment",
]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Named(_Raw):
    name: str | None = None
    key: str | None = None


class _StatusCategory(_Raw):
    key: str = "undefined"


class _Status(_Raw):
    name: str = ""
    status_category: _StatusCategory = Field(default=_StatusCategory(), alias="statusCategory")


class _LinkedFields(_Raw):
    status: _Status = _Status()


class _LinkedIssue(_Raw):
    key: str
    fields: _LinkedFields = _LinkedFields()


class _LinkType(_Raw):
    inward: str = ""


class _IssueLink(_Raw):
    type: _LinkType = _LinkType()
    inward_issue: _LinkedIssue | None = Field(default=None, alias="inwardIssue")


class _Author(_Raw):
    account_id: str | None = Field(default=None, alias="accountId")
    display_name: str = Field(default="someone", alias="displayName")


class _RawComment(_Raw):
    id: str
    author: _Author = _Author()
    created: datetime
    body: object = None


class _RawComments(_Raw):
    comments: list[_RawComment] = []


class _Fields(_Raw):
    summary: str = ""
    status: _Status = _Status()
    issuetype: _Named = _Named()
    labels: list[str] = []
    priority: _Named | None = None
    created: datetime
    updated: datetime
    parent: _Named | None = None
    issuelinks: list[_IssueLink] = []
    resolutiondate: datetime | None = None
    duedate: date | None = None
    comment: _RawComments = _RawComments()


class RawIssue(_Raw):
    key: str
    fields: _Fields


class RawSearchResponse(_Raw):
    issues: list[RawIssue] = []
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


def token(config: JiraConfig, email: str) -> str:
    """The API token from the configured env var, else the system's secret store (the macOS
    keychain, Windows' Credential Manager, the desktop keyring) under `keychain_service` for
    `email`."""
    if value := keychain.token(config, email):
        return value
    raise RuntimeError(f"no Jira token: run `mfd token set jira`, or set ${config.token_env}")


def mentions(body: object, account_id: str) -> bool:
    """Whether a comment's body (Atlassian document format: nested nodes) mentions the account."""
    if isinstance(body, dict):
        node = cast(dict[str, object], body)
        attrs = node.get("attrs")
        if (
            node.get("type") == "mention"
            and isinstance(attrs, dict)
            and cast(dict[str, object], attrs).get("id") == account_id
        ):
            return True
        return any(mentions(child, account_id) for child in node.values())
    if isinstance(body, list):
        return any(mentions(child, account_id) for child in cast(list[object], body))
    return False


def to_issue(
    raw: RawIssue,
    site: str,
    account_id: str | None = None,
    blocked_by_links: Collection[str] = (),
) -> JiraIssue:
    """The issue, with its unfinished blockers: linked issues through "is blocked by" or one
    of `blocked_by_links` (the links' inward names)."""
    f = raw.fields
    blocking = {BLOCKED_BY_LINK, *blocked_by_links}
    open_blockers = [
        link.inward_issue.key
        for link in f.issuelinks
        if link.type.inward in blocking
        and link.inward_issue
        and link.inward_issue.fields.status.status_category.key != "done"
    ]
    comments = [
        JiraComment(
            id=c.id,
            author=c.author.display_name,
            created=c.created,
            mine=account_id is not None and c.author.account_id == account_id,
            mentions_me=account_id is not None and mentions(c.body, account_id),
        )
        for c in f.comment.comments[-MAX_COMMENTS:]
    ]
    return JiraIssue(
        key=raw.key,
        summary=f.summary,
        status=f.status.name,
        status_category=f.status.status_category.key,
        type=f.issuetype.name or "",
        labels=f.labels,
        priority=f.priority.name if f.priority else None,
        created=f.created,
        updated=f.updated,
        parent=f.parent.key if f.parent else None,
        open_blockers=open_blockers,
        resolved=f.resolutiondate,
        due=f.duedate,
        url=ticket_url(site, raw.key),
        comments=comments,
    )


def _search_page(config: JiraConfig, email: str, api_token: str, body: dict[str, object]) -> bytes:
    """One page of a JQL search: the response to `body`."""
    url = config.site.rstrip("/") + SEARCH_PATH
    http.require_https(url, "the Jira token")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": _basic_auth(email, api_token),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with http.opener.open(request, timeout=60) as response:
        return response.read()


def _request(
    config: JiraConfig,
    email: str,
    api_token: str,
    path: str,
    body: dict[str, object] | None = None,
) -> bytes:
    """A GET of `path`, or a POST of `body` to it, answered with Jira's response."""
    url = config.site.rstrip("/") + path
    http.require_https(url, "the Jira token")
    headers = {"Authorization": _basic_auth(email, api_token), "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with http.opener.open(request, timeout=60) as response:
        return response.read()


def _basic_auth(email: str, api_token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{api_token}".encode()).decode()


def verify_token(config: JiraConfig, email: str, api_token: str) -> None:
    """Raise `SourceError` if Jira doesn't accept the token. A search with a rejected token
    isn't refused: Jira answers it as an anonymous user, with no issues, so an empty search
    can't tell a bad token from no tickets."""
    url = config.site.rstrip("/") + MYSELF_PATH
    http.require_https(url, "the Jira token")
    request = urllib.request.Request(
        url, headers={"Authorization": _basic_auth(email, api_token), "Accept": "application/json"}
    )
    try:
        http.opener.open(request, timeout=60).close()
    except urllib.error.HTTPError as e:
        if e.code == HTTPStatus.UNAUTHORIZED:
            raise SourceError(
                f"Jira rejected the API token for {email} (HTTP 401); it may have expired or "
                "been revoked: make a new one and run `mfd token set jira`"
            ) from e
        raise


class _Myself(_Raw):
    account_id: str | None = Field(default=None, alias="accountId")


def account_id(config: JiraConfig, email: str, api_token: str) -> str | None:
    """My account id in Jira, which tells my comments from others' and finds mentions of me."""
    found = _Myself.model_validate_json(_request(config, email, api_token, MYSELF_PATH))
    return found.account_id


def search(
    config: JiraConfig, email: str, jql: str, api_token: str, me: str | None = None
) -> list[JiraIssue]:
    issues: list[JiraIssue] = []
    next_token: str | None = None
    while True:
        body: dict[str, object] = {"jql": jql, "fields": FIELDS, "maxResults": PAGE_SIZE}
        if next_token:
            body["nextPageToken"] = next_token
        page = RawSearchResponse.model_validate_json(_search_page(config, email, api_token, body))
        if not issues and not page.issues:
            verify_token(config, email, api_token)
        issues.extend(
            to_issue(raw, config.site, me, config.blocked_by_links) for raw in page.issues
        )
        if not page.next_page_token:
            return issues
        next_token = page.next_page_token


KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
"""A ticket key, e.g. `APP-12`: the only thing let into a query by key."""


def is_key(value: str) -> bool:
    return KEY_RE.fullmatch(value) is not None


def ticket_url(site: str, key: str) -> str:
    """A ticket's URL, which is its id in the graph: `<site>/browse/<key>`."""
    return f"{site.rstrip('/')}/browse/{key}"


def key_of(site: str, url: str) -> str | None:
    """The key of the ticket on `site` that `url` is, from `ticket_url`; None for another."""
    prefix = ticket_url(site, "")
    key = url.removeprefix(prefix)
    return key if url.startswith(prefix) and is_key(key) else None


def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
    """The tickets with `keys`, open or resolved. Anything that isn't a ticket key is left
    out, so nothing but keys reaches the query."""
    keys = [k for k in keys if is_key(k)]
    if not keys:
        return []
    try:
        return search(config, email, f"key in ({', '.join(keys)})", api_token)
    except urllib.error.HTTPError as e:
        # Jira refuses a whole `key in` search naming one ticket it doesn't know (mistyped,
        # deleted, or in a project I can't see): ask key by key, and leave those out.
        if e.code != HTTPStatus.BAD_REQUEST:
            raise
        if len(keys) == 1:
            return []
        return [issue for key in keys for issue in tickets(config, email, [key], api_token)]


class _Keys(_Raw):
    issues: list[dict[str, object]] = []


def updated_within(config: JiraConfig, email: str, minutes: int) -> bool:
    """Whether a ticket assigned to me, open or not, was updated in the last `minutes`:
    a search for one ticket's key, cheap enough to run every minute. Jira takes a relative
    time (`-5m`), so no time zone is involved."""
    body: dict[str, object] = {
        "jql": f"assignee = currentUser() AND updated >= -{minutes}m",
        "fields": ["key"],
        "maxResults": 1,
    }
    page = _Keys.model_validate_json(_search_page(config, email, token(config, email), body))
    return bool(page.issues)


class _Transition(_Raw):
    id: str
    to: _Status


class _Transitions(_Raw):
    transitions: list[_Transition] = []


def transition(config: JiraConfig, email: str, key: str, statuses: list[str]) -> str:
    """Move the ticket `key` to the first of `statuses` (matched without regard to case) that
    its workflow offers a transition into, and say which, as Jira names it; raises
    `LookupError` naming the statuses it can go to when there is none."""
    api_token = token(config, email)
    path = f"/rest/api/3/issue/{key}/transitions"
    offered = _Transitions.model_validate_json(_request(config, email, api_token, path))
    wanted = next(
        (
            t
            for status in statuses
            for t in offered.transitions
            if t.to.name.casefold() == status.casefold()
        ),
        None,
    )
    if wanted is None:
        options = ", ".join(sorted(t.to.name for t in offered.transitions)) or "none"
        raise LookupError(f"{key} can't move to {' or '.join(statuses)}; it can go to: {options}")
    _request(config, email, api_token, path, {"transition": {"id": wanted.id}})
    return wanted.to.name


def collect(config: JiraConfig, email: str) -> JiraSnapshot:
    api_token = token(config, email)
    me = account_id(config, email, api_token)
    return JiraSnapshot(issues=search(config, email, MY_OPEN_ISSUES_JQL, api_token, me))
