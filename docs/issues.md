# Issues

What's wrong with what's built: bugs, gaps against the rules in `PROJECT_AGENTS.md`, and what hasn't
been tried where it runs. New work is in [roadmap.md](roadmap.md). When a change fixes one, remove
it in the same change, with any roadmap step that points at it; when a change leaves something
wrong, add it.

## Bugs

- **A newer system pandoc fails the pre-push hook.** `dev.py man --check` (run by `dev.py
  static`, so by the pre-push hook) fails with "man: man/mfd.1 is out of date with README.md"
  when the committed page is right. It makes the page with pypandoc, which uses the
  highest-versioned pandoc it finds: one on PATH (or `PYPANDOC_PANDOC`, which overrides the
  search) before the one `pypandoc_binary` bundles. Here the system pandoc is 3.11 and the
  pinned binary bundles 3.9, so the page comes out different. Workaround, never skipping the
  hook: set `PYPANDOC_PANDOC` to the bundled binary, as in
  `PYPANDOC_PANDOC=<repo>/.venv/lib/python3.15/site-packages/pypandoc/files/pandoc git push`.
  The fix belongs in python-template's `dev.py` (python-template has no issues file), not in
  each project: make `man` point pypandoc at its own bundled pandoc, or fail naming both
  versions, so a newer system pandoc can't change the page.
- **Gmail links don't open the message.** The `message_url` `mfd init` suggests for Gmail,
  `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}`, doesn't
  open the message (seen on a personal Gmail). Find what does: the Message-ID's encoding in
  the fragment (`message_url` percent-encodes all but `@`, so `+`, `=` and `/` change), its
  angle brackets, `u/<address>` against `u/0`, or a link by Gmail's own ids (`X-GM-MSGID`,
  `X-GM-THRID`, which IMAP gives), then fix the suggestion and `GMAIL_MESSAGE_URL`.
- **Another org's team takes my team's reviewer policy.** github's
  `team_reviewer_assignment` is keyed by slug, so with `search_scope = "everywhere"` a policy
  set for my org's `bacon` applies to another org's `bacon` requested on a PR of mine too. Key
  it by the team's org as well (or only apply it in `org`).

- **The default config path is resolved at import.** `config.DEFAULT_CONFIG_PATH` is expanded
  when `move_forward.config` is imported, and `config.load`'s and `--config`'s defaults are bound
  to it, so a test's home of its own (`no_real_home`) doesn't redirect them: a test calling
  `cli.main` with no `-c`, or `config.load()` with no path, would read my real config. No test
  does. Resolve it where it's used, as the database path is, in a core PR.

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
