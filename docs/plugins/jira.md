# jira

My open Jira tickets. Enabled by a `[jira]` section.

## Setup

Add a `[jira]` section with `site`; requests log in as the top-level `email` with an API
token. Either export `JIRA_API_TOKEN` (or the variable `token_env` names) or store a token
in your system's secret store under `keychain_service`, with your email as the account:

```sh
security add-generic-password -s move-forward-jira -a me@example.com -w <token>   # macOS
cmdkey /generic:move-forward-jira /user:me@example.com /pass:<token>              # Windows
secret-tool store --label=move-forward-jira service move-forward-jira account me@example.com  # Linux
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens.

## Config

`refresh_seconds` (900) is how often it refreshes in the background: tickets change slowly.

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
- **What next**: my tickets, incidents (by escalation label, if configured), then bugs,
  then unblocked work.

## Act now

jira nominates the status changes its PRs suggest, quick, closing the ticket when it goes
to Closed.
