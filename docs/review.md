# Reviewing

What move-forward adds to how `AGENTS.md` (its Reviewing section) has PRs reviewed, and how the
whole code is reviewed after a run of merges; `PROJECT_AGENTS.md` and `AGENTS.md` have the rules
the code is reviewed against.

## Reviewing a PR

As `AGENTS.md` says, and:

- One fresh reviewer is enough for a PR, drafts included, as PRs are kept small; reviewing the
  whole code takes several (see [A full review](#a-full-review)).
- The review's findings are numbered in one sequence, those in its body first. Each is an
  inline comment on its line, starting with its number and severity; one with no line in the
  diff (something missing, unchanged code) goes in the body, since GitHub rejects an inline
  comment on a line outside the diff.
- The author pushes before answering, so the commits are there to link, and answers by the
  review's numbers:

  ```sh
  gh api repos/{owner}/{repo}/pulls/N/reviews/REVIEW_ID/comments --jq '.[] | {id, body}'
  gh api repos/{owner}/{repo}/pulls/N/comments/ID/replies -f body=...
  ```
- Stacked PRs merge in order, each safe on its own, squashed into one commit.
- **A Dependabot PR** (`.github/dependabot.yml`) is reviewed the same way. The agent asked to
  take it on is its author: it reads the review, and the release notes of each version it
  brings in, for what breaks or what the code relies on, fixes what the update needs in that
  PR, and answers the findings.

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
    tooling (`dev.py`, `dev_project.py`, `pyproject.toml`, `.github/`, `git_hooks/`, what
    python-template owns);
  - **the docs** (`docs`): the README, `docs/`, PROJECT_AGENTS.md, CHANGELOG and docstrings against
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
