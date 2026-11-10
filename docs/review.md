# Reviewing

How a PR is reviewed before it merges, and how the whole code is reviewed after a run of
merges; [AGENTS.md](../AGENTS.md) has the rules the code is reviewed against.

## Reviewing a PR

Every PR is reviewed before it merges, drafts included, by one fresh agent with no context
but the PR: not a fork of the session that wrote it, so it judges what the code says rather
than what was meant. One reviewer is enough for a PR, as PRs are kept small; reviewing the
whole code takes several (see [A full review](#a-full-review)).

- **The reviewer** reads the diff against the PR's base, its commit messages and AGENTS.md,
  and changes nothing. It looks for, most important first: bugs (inputs or state that give a
  wrong result or a crash); surprises, and what the change makes inconsistent outside
  itself; tests that are missing, can't fail without the change, or enshrine a wrong
  behavior; docs, docstrings and comments that are missing or untrue (or only describe
  what the code used to do); dead code; readability; security. It checks each finding
  against the code before reporting it, and gives it a severity (bug, should-fix, minor),
  a failure scenario and a fix.
- **It posts one GitHub review of type COMMENT**, never approving or requesting changes,
  saying who wrote it (an AI agent) in its first line. Its findings are numbered in one
  sequence, those in the body first. Each is an inline comment on the line it's about,
  starting with its number and severity; one with no line in the diff (something missing,
  unchanged code) goes in the review's body. The JSON goes in a temporary directory, never
  the repo, where a commit could pick it up:

  ```sh
  gh pr view N --json headRefOid -q .headRefOid   # the head GitHub has, not a local one
  gh api repos/{owner}/{repo}/pulls/N/reviews --method POST --input "$TMP/review.json"
  # {"commit_id": "<that sha>", "event": "COMMENT", "body": "...",
  #  "comments": [{"path": "src/...", "line": 42, "side": "RIGHT", "body": "..."}]}
  ```

  GitHub rejects an inline comment on a line outside the diff.
- **The PR's author** (the agent that wrote it and started the reviewer) then decides what
  to fix, fixes it, and pushes. Then it answers every finding, none skipped: inline ones in
  their own thread, the body's in one PR comment, by the review's numbers. Each answer
  says who wrote it (an AI agent) as the review does, since it's posted from a person's
  account; whether it was fixed and why, or why not (a finding that's wrong, out of scope,
  or not worth its cost says which); and for a fix, the commit that made it, as GitHub
  links it (its short hash). Push before answering, so the commits are there to link.

  ```sh
  gh api repos/{owner}/{repo}/pulls/N/reviews/REVIEW_ID/comments --jq '.[] | {id, body}'
  gh api repos/{owner}/{repo}/pulls/N/comments/ID/replies -f body=...
  ```
- **Fixes are reviewed too**, unless all they change is wording (docs, comments, messages):
  a fresh reviewer, the same way, on what changed since the reviewed head
  (`<reviewed sha>..HEAD`), so a fix can't bring in a bug no one looked at.
- A PR merges once its reviews are answered and CI passes, in stack order, squashed into
  one commit; `git stack sync` then drops its commits from the branches above.

## A full review

A review of the whole code, rather than of one PR, after a run of merges or when asked: several
fresh agents at once (each started anew, not a fork of the session that starts them), each with
no context but the code at one commit of `main` and its part, so none reviews what it was told
to expect, and each reads all of its part.

- **The parts**, each with the prefix its findings are numbered by:
  - **every plugin** (its name: `mail`, `github`): `src/move_forward/plugins/<name>/`, its tests
    in `tests/plugins/` and its page in `docs/plugins/`: its bugs, what it publishes against
    `docs/entities.md`, its config keys in their two places, its use of `move_forward.api`
    alone;
  - **core** (`core`): `src/move_forward/` but the plugins, and core's tests (`tests/` but
    `tests/plugins/`): the graph, Act now, panels, rendering, the server and the CLI;
  - **the database and migrations** (`db`): every migration, core's and the plugins'
    (append-only, prefixed, not depending on another plugin's), what's kept or fetched, pruning,
    rekeying, SQL that filters in SQL, and data a crash or a concurrent refresh could leave half
    written;
  - **what cuts across** (`cross`): what one change made inconsistent elsewhere, the same item
    offering the same wherever it's shown, security (the token, what a sender or
    another tool controls), Linux, macOS and Windows, the plugin API's contract, and the
    tooling (`dev.py`, `pyproject.toml`, `.github/workflows/`, `git_hooks/`);
  - **the docs** (`docs`): the README, `docs/`, AGENTS.md, CHANGELOG and docstrings against
    what the code does, and dead or stale text.
- **The author** (the agent running the review) opens the issue first, saying who wrote it (an
  AI agent), and starts the reviewers, giving each only the issue's number, the commit's sha,
  its part and its prefix. Files go in a temporary directory, never the repo:

  ```sh
  gh issue create --title "Full review of <short sha>" --body-file "$TMP/issue.md"
  gh issue comment N --body-file "$TMP/<prefix>.md"   # each reviewer, markdown
  ```
- **Each reviewer** reads the code at the commit (`git show <sha>:<path>`, a detached worktree),
  changes nothing, and checks each finding against the code, running it where it can, before
  reporting it, with its severity (bug, should-fix, minor), file and line, a failure scenario
  and a fix, as a PR's reviewer does. It posts one comment, saying who wrote it (an AI agent)
  and which part it reviewed, its findings numbered with its prefix (`core 3`, `mail 2`); one
  over GitHub's 65,536 characters goes in several comments, numbered on.
- **The author** then fixes what it decides to, in PRs as usual: one kind of change each,
  reviewed as any PR, naming the findings they fix. It answers every finding in one comment on
  the issue, saying who wrote it (an AI agent), by the finding's name: fixed, with the PR; not,
  and why; or the same as another (`same as db 2`), since parts overlap. The issue closes once
  every finding is answered and its fixes merged.
