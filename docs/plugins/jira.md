# jira

My open Jira tickets. Enabled by a `[jira]` section.

## Setup

Add a `[jira]` section with `site`; requests log in as the top-level `email` with an API
token. Create one at https://id.atlassian.com/manage-profile/security/api-tokens, then
store it with

```sh
mfd token set jira
```

which asks for it without showing it and keeps it in your system's secret store (the macOS
keychain, Windows' Credential Manager, or on Linux the desktop keyring through
`secret-tool`) under `keychain_service`, with your email as the account. Or export
`JIRA_API_TOKEN` (or the variable `token_env` names), which wins over the store.
`mfd token check` says where it was found.

## Config

`refresh_seconds` (900) is how often it refreshes in the background: tickets change slowly.
It also refreshes within a minute or two of a change to a ticket assigned to me: every
minute it searches for one of my tickets updated since it last looked, a search that
returns at most one key.

`[jira]` needs `site`, an `https://` URL (the requests carry your token). `token_env` and
`keychain_service` say where its token is. `status_in_progress`, `status_in_review` and
`status_closed` are the workflow's status names, for Jira transitions.
`escalation_label_prefix` (e.g. `escalated-to-`) and `escalation_levels` (what follows it,
lowest first) rank escalated incidents and bugs in What next, and `incident_types` and
`bug_types` are the issue types (lower-case substrings) that count as each.
`[jira.teams]` isn't used yet.

## Publishes

`ticket` for each of my open tickets. Tickets are a linking kind: a PR or a work entry
that mentions a ticket's key is related to it. jira remembers every project it has seen a
ticket in (mine, or one linked from the notes), and only those projects' keys (`APP-12`)
count as tickets, so `UTF-8` doesn't. See [entities.md](../entities.md).

## Panels

- **Jira transitions**: status changes the PR states suggest.
- **What next**: my tickets (with their due dates), incidents (by escalation label, if configured), then bugs,
  then unblocked work.

## Act now

jira nominates the status changes its PRs suggest, quick, closing the ticket when it goes
to Closed. From What next it nominates incidents (important, critical once escalated),
escalated bugs (important) and tickets due within the week (the due date counting more as
the end of its day nears); a ticket with open blockers is left out.
