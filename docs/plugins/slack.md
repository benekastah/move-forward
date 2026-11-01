# slack

Review pings: who to ask about my PRs, and the messages to send them. It fetches nothing
from Slack but people's ids; it reads my PRs from the graph. Enabled by a `[slack]`
section.

## Config

`workspace_url` makes the ping page link to channels and people, `ping_first` and
`ping_follow_up` are the messages, `[slack.teams]` the channel each team's pings go to
(winning over the reviewer map), and `team_channel` (e.g. `"#team-{team}"`) a guess at the
channel of a team nothing else names one for.

People's Slack ids and teams' channels come from the skill's `reviewer-map.md`, read with
`mfd slack import <dir>` (which also picks up the nudges the skill logged in
`work-notes.md`), or are set by hand on `/slack/contacts` or with
`mfd slack contact|team set`; what's set by hand wins over a later import. With a Slack
token (`token_env`, default `SLACK_API_TOKEN`, else the system's secret store under
`keychain_service` for your email, stored with `mfd token set slack`), each refresh also looks up the Slack id of every
reviewer my open PRs ask for whom nothing names one for, by the email github knows for
them. The token needs the `users:read.email` scope; a lookup that finds nobody is tried again
once it's `prune_days` old (they may have joined Slack since), and an import or a value
set by hand wins over a lookup.

## Panels

- **Nudge reviewers**: PRs in order (green, no conflicts, no open threads) with no review
  activity for `[github] stale_review_hours`. The Ping link on any of my PRs opens a page
  with one message per requested reviewer or team, a link to their Slack DM or channel,
  and "mark as sent", which makes the next ping a follow-up. Nudges the skill logged in
  `work-notes.md` count too, once imported.

slack adds a Ping link (Bump once a target was pinged) to every item about one of my open
PRs.

## Act now

slack nominates the reviews to nudge: a quick ping, or a bump.

## Pages

| Path | Shows |
| --- | --- |
| `/ping?pr=<PR URL>` | The ping page for one of my PRs. |
| `/slack/contacts` | Where people's and teams' pings go, imported or set by hand, with a form to change each; the ping page links to it. |

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/slack/mark_sent` | `pr_url`, `target` (`user:<login>` or `team:<name>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent, so the next one to that target is a follow-up. |
| `/actions/slack/set_contact` | `login`, `slack_id` (blank for none) | Sets where a person's pings go, by hand; it wins over imports. |
| `/actions/slack/set_team_channel` | `team`, `channel_name`, `channel_id` (one of them at least) | Sets where a team's pings go, by hand; `[slack.teams]` still wins. |

## Commands

| Command | Does |
| --- | --- |
| `mfd slack contacts [--json]` | Where people's and teams' pings go, and whether each was imported or set by hand. |
| `mfd slack contact set <login> <slack id>` / `unset <login>` | Set or forget a person's Slack member id. |
| `mfd slack team set <team> <#channel> [--id C…]` / `unset <team>` | Set or forget a team's channel. |
| `mfd slack import <dir>` | Read people's Slack ids and teams' channels from the skill's `reviewer-map.md` in `<dir>`, and the nudges it logged in `work-notes.md`, replacing what the last import read. |
