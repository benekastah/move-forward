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
`status_closed` are lists of the workflow's status names, for Jira transitions: a ticket
matches a role if its status is any of them (without regard to case), and a move goes to the
first one the ticket can move to. A ticket in
`status_blocked` counts as blocked (in What next and Act now) even without blocker links.
A ticket in `status_rollout` (default `["Rollout"]`, empty: off) is waiting on monitoring: it is
never proposed for closing, and has its own panel, Rollouts: "Advance the rollout" while it has
open PRs, and once they have all merged "Finish the rollout", which offers to close it. Nothing
moves a ticket into rollout, and What next ranks it below unblocked work.
`blocked_by_links` (e.g. `["depends on"]`) names more links, as the blocked ticket sees
them, that mean "blocked by", besides "is blocked by".
`escalation_label_prefix` (e.g. `escalated-to-`) and `escalation_levels` (what follows it,
lowest first) rank escalated incidents and bugs in What next, and `incident_types` and
`bug_types` are the issue types (lower-case substrings) that count as each.
At equal escalation, incidents and bugs rank by `priorities` (Jira's priority names, lowest
first), and the top one raises their importance in Act now by a step.
`[jira.teams]` isn't used yet.

## Publishes

`ticket` for each of my open tickets, by its URL (`<site>/browse/<key>`), with its key as
its `handle`. Tickets are a linking kind: a PR or a work entry
that mentions a ticket's key is related to it. jira remembers every project it has seen a
ticket in (mine, or one linked from the notes), and only those projects' keys (`APP-12`)
count as tickets, so `UTF-8` doesn't. See [entities.md](../entities.md).

## Panels

- **Jira transitions**: status changes the PR states suggest, each with a "Move to …"
  button that makes it in Jira (by the workflow's transition into that status, so it fails,
  naming the statuses the ticket can go to, when there is none). A ticket in progress or
  in review whose only PRs were closed without merging is flagged too ("reopen it or move
  the ticket back"; in review, with a button back to in progress). So is a ticket not started
  that has PRs, with a button to in progress.
- **Rollouts**: tickets in a `status_rollout` status, each with its PRs as links saying where
  each is ("ready to merge #123", "draft #124", "merged #120"): "Advance the rollout" while some are
  open (low urgency, raised when one is ready to merge), "Finish the rollout" with a Move to Closed
  button once all have merged.
- **What next**: my tickets (with their due dates), incidents (by escalation label, if configured), then bugs,
  then unblocked work.

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/jira/transition` | `key`, `to` (a status name), optional `also` (other names for it, one per line) | Moves the ticket to the first of those statuses its workflow offers, with the API token (needs write access to the project). |

## Act now

jira nominates the status changes its PRs suggest, quick, closing the ticket when it goes
to Closed, and its rollouts ("Advance the rollout", "Finish the rollout"). From What next
it nominates incidents (important, critical once escalated), escalated bugs (important),
tickets due within `[act_now] due_warning_days` (the due date counting more as the end of its day nears) and
blocked tickets, which Act now ranks down until they've gone `[act_now] blocked_stale_days`
untouched.

## Notifications

Subscribe under Notifications on `/settings` (or `[notifications] subscribe` as
`jira.<kind>`). Each is said once, when the state changes; the first refresh says nothing.

| Kind | When |
| --- | --- |
| `assigned` | a ticket appears among my open tickets |
| `comment_added` | someone but me comments on one |
| `mentioned` | someone mentions me in a comment (said instead of `comment_added`) |
| `priority_raised` | its priority goes up in the order of `priorities` |
| `unblocked` | the last unfinished ticket blocking it is done |
| `closeable` | its PRs are merged, so the transitions panel proposes closing it |

`priorities` (`["Lowest", "Low", "Medium", "High", "Highest"]`) lists Jira's priority names
lowest first; one not listed is never counted as raised. Comments come with the search (the
latest ten per ticket are kept), and my account id from `/myself` tells mine from others'.
A flagged (impediment) ticket isn't told: Jira's flag is a custom field whose id differs by
site.
