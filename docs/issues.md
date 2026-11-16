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

- **Set aside from anywhere.** An item only Act now shows (nominated with no panel item) has
  nowhere to come back from but `mfd unsnooze`.
- **Focus mode's missing buttons.** Panel-wide buttons (Delete all, Clean) don't show there,
  nor do those of an item that focus mode splits into parts (an entry's Put on ice).
- **Snooze a PR waiting on review.** slack's Nudge reviewers nominates a PR of mine (task
  `ping`), with Snooze, but the PR's item in github's Ready for review has no task, so it
  has neither Snooze nor Unsnooze. A snooze is kept by its subject's kind and id and its
  task, so snoozing the nudge sets aside only the nudge: give github's item a task, and
  decide whether snoozing it sets aside the nudge too.
- **Importance where it means something.** Say whether every panel sorts by it: Ready for me
  to review, Fix before review and Mail do, What next and Feedback to address don't.
- **Restack on a conflict.** Stacks' `needs-restack` items have Restack; github's merge
  conflicts that say restacking fixes them don't yet. It stays local: `sync` and `push`
  force-push (with lease), which the rule against force-pushing from a button forbids unless
  that's changed.
- **Size a todo, and say why something's on ice, from the CLI.** `todo add --size` sizes a
  new one; nothing sizes an existing one but the page's Quick and Big, and nothing changes
  an ice note but its Replace note, so the work-notes skill can't do either.
- **Edit an entry.** What the CLI can do to one: `blocked-on` (its badge can't be cleared from
  the page), status and where (not shown at all), `link`, and `remove`.
- **Merge conditions.** Held's "held: …" has no way out but `mfd notes condition remove`. Add
  and remove them on the PR's items.

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
