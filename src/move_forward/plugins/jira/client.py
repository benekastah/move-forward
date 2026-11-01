"""Jira collector: my open issues, through the Jira Cloud REST API."""

import base64
import json
import os
import re
import urllib.request
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ... import https
from ...shell import CommandError, run
from .config import JiraConfig
from .models import JiraIssue, JiraSnapshot

SEARCH_PATH = "/rest/api/3/search/jql"
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


class RawIssue(_Raw):
    key: str
    fields: _Fields


class RawSearchResponse(_Raw):
    issues: list[RawIssue] = []
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


def token(config: JiraConfig, email: str) -> str:
    """The API token from the configured env var, else the macOS keychain item for `email`."""
    if value := os.environ.get(config.token_env):
        return value
    try:
        return run(
            [
                "security",
                "find-generic-password",
                "-s",
                config.keychain_service,
                "-a",
                email,
                "-w",
            ]
        ).strip()
    except (CommandError, FileNotFoundError) as e:
        raise RuntimeError(
            f"no Jira token: set ${config.token_env} or run `security add-generic-password"
            f" -s {config.keychain_service} -a {email} -w <token>`"
        ) from e


def to_issue(raw: RawIssue, site: str) -> JiraIssue:
    f = raw.fields
    open_blockers = [
        link.inward_issue.key
        for link in f.issuelinks
        if link.type.inward == BLOCKED_BY_LINK
        and link.inward_issue
        and link.inward_issue.fields.status.status_category.key != "done"
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
        url=f"{site.rstrip('/')}/browse/{raw.key}",
    )


def search(config: JiraConfig, email: str, jql: str, api_token: str) -> list[JiraIssue]:
    url = config.site.rstrip("/") + SEARCH_PATH
    https.require_https(url, "the Jira token")
    auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    issues: list[JiraIssue] = []
    next_token: str | None = None
    while True:
        body: dict[str, object] = {"jql": jql, "fields": FIELDS, "maxResults": PAGE_SIZE}
        if next_token:
            body["nextPageToken"] = next_token
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with https.opener.open(request, timeout=60) as response:
            page = RawSearchResponse.model_validate_json(response.read())
        issues.extend(to_issue(raw, config.site) for raw in page.issues)
        if not page.next_page_token:
            return issues
        next_token = page.next_page_token


KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
"""A ticket key, e.g. `APP-12`: the only thing let into a query by key."""


def is_key(value: str) -> bool:
    return KEY_RE.fullmatch(value) is not None


def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
    """The tickets with `keys`, open or resolved. Anything that isn't a ticket key is left
    out, so nothing but keys reaches the query."""
    keys = [k for k in keys if is_key(k)]
    if not keys:
        return []
    return search(config, email, f"key in ({', '.join(keys)})", api_token)


def collect(config: JiraConfig, email: str) -> JiraSnapshot:
    return JiraSnapshot(issues=search(config, email, MY_OPEN_ISSUES_JQL, token(config, email)))
