# github

Pull requests from GitHub, through the `gh` CLI: mine (open, merged and closed), review
requests, PRs I reviewed, and my teams' PRs. Enabled by a `[github]` section.

## Config

`host` (`github.com`) is GitHub's host; for a GitHub Enterprise Server, its host (e.g.
`github.acme.com`), which `gh` must be logged in to (`gh auth login --hostname <host>`).
Every `gh api` call goes there. `mfd init` fills it in from an Enterprise org's URL. Set
git's `github_hosts` too, so git knows the server's remotes.

`refresh_seconds` (300) is how often it refreshes in the background: GitHub's API is rate-limited.
It also refreshes soon after I get a notification about something in the org (a review
requested, a review, a comment, CI): it polls my notifications as often as GitHub allows
(every 60 seconds, usually), and GitHub answers "not modified" cheaply when there's nothing
new. And it refreshes within seconds of a commit or push on the local branch of one of my
open PRs: GitHub notifies nobody of their own pushes, so github watches git, and after each
refresh of git it compares where `git.branch` entities for those PRs' heads stand (their
commit, and how far ahead of or behind their upstream) with what it saw last. Other changes that notify nobody, like a PR falling into
conflict, wait for `refresh_seconds`.

An action that touched a PR (a ping, a reaction) and `mfd refresh --subject pull_request
<URL>` fetch just that PR the same way (`refresh_subjects`).

Such a refresh fetches only the PRs that changed, in one request, and places them in the
lists the searches fill (mine, merged or closed; requesting my review or my team's;
reviewed by me), keeping the rest as they were. A CI run's notification names its repo,
not its PR, so it fetches my open PRs in that repo; one about what no search shows (an
issue, a discussion, a release, a commit comment, a security alert) fetches nothing. Every
search runs instead when `refresh_seconds` have passed since they last did, for a
notification about anything else, when more PRs changed than a search page holds (25), and
when fetching the PRs fails. A PR that newly concerns me without a notification (one I
just opened, say) waits for that.

`[github]` needs `org`. `search_scope` (`"org"` by default, or `"everywhere"`) says whether my PR searches stay in that organization's repositories. `history_days` says how far back my merged and closed PRs are
fetched. Its conventions that differ between organisations are off by default:
`policy_checks` (checks that enforce PR policy, such as labels or assignees, rather than test the code), `ignored_reviewers` (logins whose comments and
reviews, like a policy bot's, aren't feedback; other bots' are), `required_owner_marker` (a
CODEOWNERS comment such as `#!required`), `ticket_link_prefixes` (the words a PR body line linking
the ticket can start with, e.g. `["Link", "Closes"]`, followed by a key or a Jira URL) and `placeholder_labels` (template labels to replace).
`email_domains` picks reviewers' emails from their commits (default: your email's domain;
an email found is kept, and a lookup that found none is tried again after `prune_days`),
`stale_review_hours` says when a review request can be nudged,
`quick_review_max_lines`/`_files` and `hour_review_max_lines`/`_files` size a review (minutes
within the quick limits, an hour within the hour ones, else a day; they grow slowly because
complexity grows faster than size), `stale_draft_days` (my drafts untouched that long get a "stale draft" line; 0: off),
`split_pr_suggest_lines` and `split_pr_lines` (my PRs at least that many lines "may need
splitting" or are "worth splitting", in PR housekeeping) and `split_pr_labels` (labels that
mean worth splitting whatever the lines), and `reviewer_history_days`
how far back commits count for reviewer suggestions. `[github.teams]` gives a team's GitHub
slug where it isn't the team's name.
`default_team_reviewer_assignment` is `"team"` by default: a team request is enough.
`team_reviewer_assignment` overrides it by GitHub team slug, for example
`{ bacon = "individual" }`. For an `"individual"` team, the PR must request at least one
member of that team as a person too. A person in several requested teams can satisfy each
of them. Membership is fetched per team; if it cannot be verified, the PR stays in
Fix before review with that reason (and for one of my `teams`, the log says why). A PR
requiring review with no reviewer requested still needs one.
`primary_reviewer_repos` lists the repos (`owner/name`) where a PR has a primary reviewer: an
assignee besides its author, who must approve. GitHub enforces that; it only adds the field
described under [Buttons](#buttons).

## Publishes

`pull_request` (as `github.pull_request`) for every PR it fetched, mentioning its branch, title and body (so it points at the
tickets they name, and a `#tag` in them, out of code, tags it: see the README's Tags), with its
labels as its tags but `placeholder_labels` and `split_pr_labels`, which are about its shape
(only those listed: a size labeler's other sizes are tags), and `person` (`<host>/<login>`) for me and everyone it knows an email for; see
[entities.md](../entities.md).

## Panels

- **Fix before review**: my open, non-draft PRs with failing CI or policy checks, conflicts,
  unresolved or unanswered feedback, changes requested, or reviewer assignments missing
  under the teams' rules. A changes request counts even when another reviewer approved.
  After a push and a renewed request to the same reviewer, the old changes request is
  treated as waiting for review (a reviewer who has since approved isn't waited on). Failing policy checks whose status descriptions say they
  need an approval are waiting too, on a first review as on a later one; other policy
  failures remain here.
  The reasons appear together on one item; assigned importance sorts before age.
- **Ready for review**: my open, non-draft PRs without those blockers, waiting for approval.
  Pending CI is allowed. Each item says who it's waiting on.
- **Ready for me to review**: non-draft PRs requesting me as an individual, plus PRs
  pushed after my approval when GitHub says `REVIEW_REQUIRED`. Team-only requests stay in
  Requested from my teams; a new push alone does not bring an approved PR back. Like
  other entity items, each has the core
  importance choices. Assigned importance sorts before recent updates.
- **Replies for me**: open non-draft PRs of others that mention me (updated within
  `history_days`) without asking for a review, with Act now's "Reply".
- **Feedback to address**: unresolved threads, changes requested, comments since my last
  push I haven't answered. This and the next diagnostic panels remain below the three
  review stages, so their detailed links and Act now nominations remain available.
- **CI failures**: failing checks and merge-queue evictions in the last
  `merge_queue_eviction_days` (2), unless the PR is back in the queue, was queued again, or
  is green now. Checks
  named in `policy_checks` never count as failures.
- **PR policy**: failing checks named in `policy_checks`, linked to their details, which
  say what's missing (a label, an assignee); not one only waiting for a review, until the PR
  is approved and it still fails.
- **Merge conflicts**.
- **Ready for me to review** and **Requested from my teams** size a review by the files that
  CODEOWNERS gives me or my teams, when there are some: "my part: 12 lines in 2 of 119
  files". The PR's badges still show the whole PR. Without owned files, the whole PR sizes it.
- **Needs a reviewer**: the assignment reasons from Fix before review, on PRs not in the merge
  queue, with recent committers suggested for PRs requesting a team. GitHub's `reviewDecision` is null where
  the base branch requires no review; Ready to merge asks for no approval there. These PRs,
  here and in Fix before review, have a field to request a reviewer, suggesting the requested
  teams' members as far as they're known, then recent committers; any login can be typed.
- **Ready to merge** and **Held by merge conditions** (from the work notes: a PR is held by the conditions on it, not by those on its entry's other PRs). A PR that GitHub's merge state calls BEHIND ("behind base: update the branch", with an Update branch button that asks first) or BLOCKED with nothing else we can see ("blocked by branch protection") isn't ready either: it's held, with that reason.
- **In the merge queue**: my PRs waiting in it. Nothing to act on, so Act now ignores it.
- **Requested from my teams**: PRs waiting on any of my `teams`, ranked by how many files
  CODEOWNERS gives those teams (required first) and how many are in directories I commit
  to.
- **PR housekeeping**: all my drafts, plus PRs with unknown labels, missing ticket links,
  or other housekeeping flags. Each PR says which branch it targets. PRs based on another
  PR's head branch nest beneath that PR, including a parent with no housekeeping flags.
  A branch tree starts expanded if it contains a non-draft PR, and collapsed if all its
  PRs are drafts; the page remembers toggles. A draft with green CI, no open threads and no open
  todos has a Ready for review button, which asks first.

In a repo in `primary_reviewer_repos`, every item about one of my open PRs, unless it's in the
merge queue, shows whoever's assigned besides the author ("primary: alice") and has a field to
pick the primary reviewer. It suggests the requested reviewers, then the requested teams'
members as far as they're known, then recent committers; any login can be typed.

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/github/update_branch` | `pr` (the URL of one of my open PRs that's behind its base) | Merges its base into its branch on GitHub (`gh pr update-branch`), not rebasing, so what's stacked on it isn't rewritten. |
| `/actions/github/mark_ready` | `pr` (the URL of one of my draft PRs) | Takes it out of draft. |
| `/actions/github/request_reviewer` | `pr` (the URL of one of my open PRs), `input` (a login) | Asks them to review it. |
| `/actions/github/primary_reviewer` | `pr` (the URL of one of my open PRs, in a repo in `primary_reviewer_repos`), `input` (a login) | Assigns them in place of whoever else is assigned besides the author, and asks for their review unless they're already asked or have approved it. |

## Act now

github nominates direct reviews (sized by the `quick_review_max_*` and `hour_review_max_*`
limits, with the author waiting since my review was last asked for), mentions to reply to
(quick), feedback to reply to (quick, the thread authors waiting since the oldest unresolved
thread), CI failures (about an hour), PR policy failures (quick), merge conflicts (about an
hour, or quick when restacking fixes them), PRs needing a reviewer (quick, but below anything else to do on the PR, where Act now
shows it as an "also" line on that item; not when already approved or all that's missing is a
team's members being readable) and PRs ready to merge (quick, closing the PR). Fix before review, Ready for review, Held, the merge queue,
Requested from my teams and PR housekeeping aren't nominated.

## Commands

| Command | Does |
| --- | --- |
| `mfd github import <dir>` | Read reviewers' emails from the skill's `reviewer-map.md` in `<dir>`, for reviewer suggestions; they win over emails looked up from commits. Run it again whenever the map changes. |

## Notifications

Subscribe to these kinds under Notifications on `/settings` (or `[notifications] subscribe`
as `github.<kind>`); each is said once, when the state changes between two refreshes, and
a PR the last refresh didn't hold says nothing of its state.
GitHub can briefly report mergeability as `UNKNOWN` while recalculating it. Returning
to a ready or conflicting result after that temporary state does not repeat the
notification unless a review, check, or commit changed.

| Kind | When |
| --- | --- |
| `pr_mergeable` | my PR is approved, green, conflict-free, not a draft and not queued |
| `ci_failed`, `ci_recovered` | CI fails on my PR (naming the checks), or passes again |
| `review_received` | someone but me approves, requests changes on or comments in a review of my PR |
| `conversation_comment` | someone but me comments on my PR's conversation |
| `merge_conflict` | my PR gets a conflict |
| `pr_merged`, `pr_closed` | my PR is merged, or closed without merging, by anyone |
| `merge_queue_added` | someone else queues my PR (unsaid when GitHub's recent timeline doesn't say who) |
| `merge_queue_removed` | my PR leaves the queue without merging, with GitHub's reason |
| `thread_reply` | a review thread on my PR, or one I reviewed, gains comments |
| `review_requested_me` | a review is requested of me, including asked again |
| `review_requested_team` | a review is requested of my team |
| `new_push` | new commits on a PR I reviewed |
| `author_replied` | the author comments on a PR I reviewed |

Events about one PR come as one notification, which opens the dashboard at the PR.
