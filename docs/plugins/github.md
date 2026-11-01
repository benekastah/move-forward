# github

Pull requests from GitHub, through the `gh` CLI: mine (open, merged and closed), review
requests, PRs I reviewed, and my teams' PRs. Enabled by a `[github]` section.

## Config

`[github]` needs `org`. `history_days` says how far back my merged and closed PRs are
fetched. Its conventions that differ between organisations are off by default:
`review_checks` (CI checks that really report review status), `required_owner_marker` (a
CODEOWNERS comment such as `#required`), `ticket_link_prefix` (the PR body line that links
the ticket, e.g. `Link`) and `placeholder_labels` (template labels to replace).
`email_domains` picks reviewers' emails from their commits (default: your email's domain),
`stale_review_hours` says when a review request can be nudged, and `reviewer_history_days`
how far back commits count for reviewer suggestions. `[github.teams]` gives a team's GitHub
slug where it isn't the team's name.

## Publishes

`pr` for every PR it fetched, mentioning its branch, title and body (so it points at the
tickets they name), and `person` for me and everyone it knows an email for; see
[entities.md](../entities.md).

## Panels

- **Reviews for me**: PRs requesting my review as a person, and PRs I reviewed that were
  pushed or re-requested since.
- **Feedback to address**: unresolved threads, changes requested, comments since my last
  push I haven't answered.
- **CI failures**: failing checks and merge-queue evictions in the last 2 days. Checks
  named in `review_checks` never count as failures.
- **Merge conflicts**.
- **Needs a reviewer**: no person requested and no approval. PRs requesting one of my
  `teams` get recent committers to the PR's directories as suggestions for whom to assign
  instead.
- **Ready to merge** and **Held by merge conditions** (from the work notes).
- **Requested from my teams**: PRs waiting on any of my `teams`, ranked by how many files
  CODEOWNERS gives those teams (required first) and how many are in directories I commit
  to.
- **PR housekeeping**: unknown labels, missing ticket links, drafts that look ready.

## Act now

github nominates reviews (quick when up to 100 lines, about an hour up to 500, with the
author waiting), feedback to reply to (quick, the thread authors waiting), CI failures and
merge conflicts (about an hour), and PRs ready to merge (quick, closing the PR).

## Commands

| Command | Does |
| --- | --- |
| `mfd github import <dir>` | Read reviewers' emails from the skill's `reviewer-map.md` in `<dir>`, for reviewer suggestions; they win over emails looked up from commits. Run it again whenever the map changes. |
