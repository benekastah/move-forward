# slack

Review pings: who to ask about my PRs, and the messages to send them. It reads my PRs from
the graph, and from Slack, people's ids, with the token you give it. Enabled by a `[slack]`
section.

## Config

`workspace_url` makes the ping page link to channels and people, `ping_first` and
`ping_follow_up` are the messages, and `[slack.teams]` the channel each team's pings go to.

A team's channel comes from `[slack.teams]`, or is set by hand on `/slack/contacts` or with
`mfd slack team set`; `[slack.teams]` wins. A team named in neither has no channel, and its
ping says only `team <name>`. `mfd import <dir>` sets the channels of the teams in the skill's
`reviewer-map.md` as if by hand, but for teams that have one set already, and says which it
left.

People's Slack ids come from the skill's `reviewer-map.md`, read with `mfd import <dir>`
(which also picks up the nudges the skill logged in `work-notes.md`), or are set by hand on
`/slack/contacts` or with `mfd slack contact set`; what's set by hand wins over a later
import. With a Slack token
(`token_env`, default `SLACK_API_TOKEN`, else the system's secret store under
`keychain_service` for your email, stored with `mfd token set slack`), each refresh also looks
up the Slack id of every reviewer my open PRs ask for whom nothing names one for, by the email
github knows for them. The token needs the `users:read.email` scope; a lookup that finds nobody
is tried again once it's `prune_days` old (they may have joined Slack since), and an import or
a value set by hand wins over a lookup. A lookup that fails (a token without the scope, a rate
limit, no network) stops that refresh's, and `/slack/contacts` says why until the next.

## Panels

- **Nudge reviewers**: PRs in order (green, no conflicts, no open threads), with a reviewer
  asked and the PR not yet approved, and no review activity for `[github]
  stale_review_hours`. The Ping link opens a page
  with one message per requested reviewer or team, a link to their Slack profile (where a DM is a click away) or their team's channel,
  and "mark as sent", which makes the next ping a follow-up. Nudges the skill logged in
  `work-notes.md` count too, once imported.

slack adds a Ping link (Bump once a target was pinged) to every item about one of my open
PRs with a reviewer requested and without the approval it needs.

## Act now

slack nominates the reviews to nudge: a quick ping, or a bump.

## Pages

| Path | Shows |
| --- | --- |
| `/slack/ping?pr=<PR URL>` | The ping page for one of my PRs. |
| `/slack/contacts` | Where people's and teams' pings go, and whether a person's was imported, set by hand or looked up, with a form to change each and a Clear button for what's kept (asking first), and why the last lookup by email failed, if it did; the ping page links to it. |

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/slack/mark_sent` | `pr_url`, `target` (`user:<login>` or `team:<name>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent, so the next one to that target is a follow-up. |
| `/actions/slack/set_contact` | `login`, `slack_id` (blank for none) | Sets where a person's pings go, by hand; it wins over imports, and blank keeps them unlinked. |
| `/actions/slack/clear_contact` | `login` | Forgets where a person's pings go, asking first on the page, so they're imported or looked up again. |
| `/actions/slack/set_team_channel` | `team`, `channel_name`, `channel_id` (one of them at least) | Sets where a team's pings go, by hand; `[slack.teams]` still wins. |
| `/actions/slack/clear_team_channel` | `team` | Forgets a team's channel, asking first on the page; `[slack.teams]` still wins, and a later `mfd import` can set it again. |

## Commands

| Command | Does |
| --- | --- |
| `mfd slack destinations [--json]` | Where people's and teams' pings go, and whether each was imported, set by hand or looked up by email (a team's is always set by hand, `dashboard`). |
| `mfd slack contact set <login> <slack id>` / `unset <login>` | Set or forget a person's Slack member id. |
| `mfd slack team set <team> <#channel> [--id C…]` / `unset <team>` | Set or forget a team's channel. |
| `mfd import <dir>` | Core's command; slack reads people's Slack ids from the skill's `reviewer-map.md` in `<dir>`, and the nudges it logged in `work-notes.md`, replacing what the last import read; and sets its teams' channels as if by hand, but for teams that have one set already. |
