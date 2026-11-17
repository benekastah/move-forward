# Issues

What's wrong with what's built: bugs, gaps against the rules in `PROJECT_AGENTS.md`, and what hasn't
been tried where it runs. New work is in [roadmap.md](roadmap.md). When a change fixes one, remove
it in the same change, with any roadmap step that points at it; when a change leaves something
wrong, add it.

## Bugs

- **Gmail links don't open the message.** The `message_url` `mfd init` suggests for Gmail,
  `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}`, doesn't
  open the message (seen on a personal Gmail). Find what does: the Message-ID's encoding in
  the fragment (`message_url` percent-encodes all but `@`, so `+`, `=` and `/` change), its
  angle brackets, `u/<address>` against `u/0`, or a link by Gmail's own ids (`X-GM-MSGID`,
  `X-GM-THRID`, which IMAP gives), then fix the suggestion and `GMAIL_MESSAGE_URL`.
- **A base merge isn't an answer.** github counts comments as answered once they're older than my
  last push, by the head commit's date, so merging the base into a branch (Update branch, or by
  hand) clears them with nothing said. Read past merge commits: it needs their parents fetched.
  Then Update branch's confirm no longer needs to warn of it.
- **An empty panel with a field is hidden.** A panel with no items is drawn collapsed, so a
  panel-wide field (notes' New todo) is hidden just when it's wanted: draw such a panel open.
- **Another org's team pings my team's channel.** slack keys a team's channel and pings by the
  slug a PR requests, but github publishes a team for each org whose PRs request one. With
  `search_scope = "everywhere"`, a PR of mine in another org requesting `web` is nudged to my
  org's `web` channel, and its pings count as theirs. Key them by the team's id; `Team.handle`'s
  docstring ("unique only within its publisher") is wrong too, as one publisher has several
  orgs' teams.
- **Windows' accounts in another language.** On a Windows that isn't in English, `icacls` names
  SYSTEM and Administrators in its language, so `private.is_private` wouldn't recognize them
  and the token file would be warned about on every start: look their names up from their
  well-known SIDs (`LookupAccountSidW`) rather than matching English ones.

## What one surface offers and another doesn't

Where `PROJECT_AGENTS.md`'s "An item offers the same wherever it's shown" doesn't hold yet (Act now,
the panels, focus mode), and where the CLI can't do what the page does, which the work-notes skill
needs. Each stands alone; a plugin fix that needs a core one says so, and lands on top of it.

- **Restack on a conflict.** Stacks' `needs-restack` items have Restack; github's merge
  conflicts that say restacking fixes them don't yet. It stays local: `sync` and `push`
  force-push (with lease), which the rule against force-pushing from a button forbids unless
  that's changed.

## The skill and the dashboard disagree

The work-notes skill won't clean while something's on ice, but the dashboard cleans regardless
(`git stack clean` never restacks or pushes), so the skill's rule no longer matches. The CLI's
JSON output is the contract the skill is written against: change it deliberately, with the
skill. (Moving the skill onto the notes is on the [roadmap](roadmap.md#notes-and-the-skill).)

## Windows, on a real machine

CI passes on Windows, but these need trying by hand: `py dev.py install` (the PATH
change, the `mfd.cmd` shim, the hooks through `py -3`);
`mfd serve` replacing its server on a config change, and ctrl-c stopping it;
`mfd token set` with Credential Manager; Slack's Connect; git stack itself; git watching its
repos while you work (the watcher holds handles on the watched directories, and Windows can
refuse to delete or rename a directory with handles open in it: `git worktree remove`, a
checkout that deletes a top-level directory, renaming a repo's folder); and repo paths
compared as `Path`s (case, `~`, separators), including those stored in `notes_ice`.
