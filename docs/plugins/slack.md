# slack

Review pings: who to ask about my PRs, and the messages to send them. It reads my PRs from
the graph, and from Slack, people's ids, through the workspace's Slack app when it's
connected. Enabled by a `[slack]`
section.

## The Slack app

slack can connect to a Slack app of the workspace's, signing in for a user token of your
own. Without it, slack works as below with the token you give it, or none. One app serves
the whole workspace, so someone makes it once. Slack's page (`/slack`) shows how:
1. Create the app at api.slack.com/apps from the manifest checked in as
   `src/move_forward/plugins/slack/manifest.json` (the page shows it to paste). It asks for
   user scopes only, signs in with PKCE (so it has no client secret to share) and rotates
   its tokens.
2. Install it to the workspace (an admin may need to approve it; on Enterprise Grid,
   install it to the organization).
3. Share its client id.

Then each person sets `client_id` in `[slack]` and presses Connect Slack on `/slack`.
Slack sends the browser back to `http://localhost:<port>/slack/callback` (`https://` when
the dashboard serves HTTPS). The manifest lists that URL for the default port; on another
port, add yours to the app's redirect URLs. The sign-in's tokens go in the system's secret
store under `keychain_service` (the access token, where `mfd token set slack` puts one)
and `keychain_service`-refresh. The access token lasts 12 hours and is refreshed before it
runs out; the refresh token lasts 30 days, so after a month away, connect again. When
Slack refuses a refresh, slack forgets the connection, says why on its page, and goes back
to the token given by hand, if any. Disconnect revokes the token and forgets it.

## Config

`client_id` is the Slack app's (see above). `workspace_url` makes the ping page link to channels and people, `ping_first` and
`ping_follow_up` are the messages, `[slack.teams]` the channel each team's pings go to
(winning over the reviewer map), and `team_channel` (e.g. `"#team-{team}"`) a guess at the
channel of a team nothing else names one for.

People's Slack ids and teams' channels come from the skill's `reviewer-map.md`, read with
`mfd slack import <dir>` (which also picks up the nudges the skill logged in
`work-notes.md`), or are set by hand on `/slack/contacts` or with
`mfd slack contact|team set`; what's set by hand wins over a later import. With the app
connected, or else a Slack token (`token_env`, default `SLACK_API_TOKEN`, else the
system's secret store under
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
| `/slack` | Setting up the Slack app, connecting to it, and what it's connected as, with the scopes each feature lacks. |
| `/slack/connect` | Starts signing in to the app and sends the browser to Slack (not found without `client_id`). |
| `/slack/callback` | Where Slack sends the browser back. Served without the dashboard's cookie, which browsers don't send on a redirect from Slack; it's refused unless it carries the single-use `state` of the sign-in started here, within 10 minutes. |

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/slack/mark_sent` | `pr_url`, `target` (`user:<login>` or `team:<name>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent, so the next one to that target is a follow-up. |
| `/actions/slack/set_contact` | `login`, `slack_id` (blank for none) | Sets where a person's pings go, by hand; it wins over imports. |
| `/actions/slack/set_team_channel` | `team`, `channel_name`, `channel_id` (one of them at least) | Sets where a team's pings go, by hand; `[slack.teams]` still wins. |
| `/actions/slack/disconnect` | | Revokes the app's token with Slack and forgets it. |

## Commands

| Command | Does |
| --- | --- |
| `mfd slack contacts [--json]` | Where people's and teams' pings go, and whether each was imported or set by hand. |
| `mfd slack contact set <login> <slack id>` / `unset <login>` | Set or forget a person's Slack member id. |
| `mfd slack team set <team> <#channel> [--id C…]` / `unset <team>` | Set or forget a team's channel. |
| `mfd slack import <dir>` | Read people's Slack ids and teams' channels from the skill's `reviewer-map.md` in `<dir>`, and the nudges it logged in `work-notes.md`, replacing what the last import read. |
