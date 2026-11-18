# Working on this project

Read `PROJECT_AGENTS.md` too: it has this project's own shape of the code, rules and
decisions. Where the two disagree, `PROJECT_AGENTS.md` wins.

This file holds the rules every project made from
[python-template](https://github.com/benekastah/python-template) shares. It's the template's:
`uvx copier update --defaults` keeps it current, so change it in the template, and put what's
this project's own in `PROJECT_AGENTS.md`. The README is the user documentation; its
Development section says how to set up a clone and run the checks.

## Making a change

- **One kind of change per PR**: a refactor, a feature and a fix are separate PRs, each small
  enough to review whole. When one needs another first, that one lands first, on its own,
  with its own tests.
- **Except in rapid development**: while `.copier-answers.yml` has `rapid_development: true`,
  waiting on CI costs more than a longer review, so bundle related changes (a fix, the
  refactor it needs, a small feature) into one PR worth a CI run. Past about 800 changed
  lines, split it into PRs that each get their CI in turn. Once others depend on a release and
  most changes are fixes, leave rapid development, which also makes CI strict (see "Tests and
  checks"):

  ```sh
  uvx copier update --defaults -d rapid_development=false \
      --vcs-ref "$(sed -n "s/^_commit: //p" .copier-answers.yml | tr -d "'")"
  ```

  then check that `git diff` shows the new answer and `ci.yml`'s stricter `CI passed`, and
  commit them.
- **One commit per change**, in every project, so a reviewer can go commit by commit: each
  with its own tests and docs, and passing the checks on its own. Fixes from a review can be
  commits of their own.
- **Every change to behavior comes with tests**, in the same commit, that would fail without
  it, and changes the tests that cover the old behavior. Tests say what's correct, not what
  the code happens to do: don't add a test that pins down a bug.
- **Work test-first**: write the tests for a change (or a bug) first, run them and see them
  fail for the reason you expect, then make the change and see them pass. If the code has to
  be reshaped first for the tests to run (e.g. to inject what they replace), do only that
  before seeing them fail, so the failure still shows the old behavior.
- **Docs change with the code**, in the same commit: the README for behavior,
  `PROJECT_AGENTS.md` for the shape of the code and its rules, and the docstrings and
  comments on the code you touch. Before committing, search the docs for the names you
  renamed, moved or removed.
- **Keep `docs/roadmap.md` (what's left to build) and `docs/issues.md` (what's wrong with
  what's built) current**: a change removes what it finishes or fixes, and adds what it
  leaves wrong.
- **No GitHub issues without the maintainer's approval**: don't open or comment on one unless
  the maintainer says so (PRs and their reviews are fine). What's wrong goes in
  `docs/issues.md`.
- **Commit messages say why**, not just what.
- **No new runtime dependency** without a good reason: prefer the standard library.

## Tooling

- `dev.py` sets up a clone and runs the checks, the same on every system (on Windows, run
  it with `py`); `python3 dev.py` lists its commands. It's the template's: this project's
  own commands and install steps go in `dev_project.py` (see `dev.py`'s docstring), and
  any links it makes in `install_links()`, so `install` checks them with its own.
- `dev.py install` points the user's commands at this clone: run it only in the clone they
  use. It refuses to take a command over from another clone (unless given `--replace`), and
  to install a clone in a temporary directory (the system's, /tmp or /var/tmp). In a
  worktree or a scratch clone, use `dev.py venv` and the commands in `.venv` (a scratch clone
  also needs `git config core.hooksPath git_hooks` for the hooks; a worktree shares its
  clone's).
- **The man pages are made from the README**: `dev.py man` writes `man/<command>.1` for each
  of the package's commands, with pandoc, and `dev.py static` fails if one is out of date.
  Never edit them by hand: change the README, and commit the pages with it (the pre-commit
  hook makes them again and stages them, but stops a `git commit PATH`, which it can't stage
  them in). Which `##` sections they leave out, and their
  SYNOPSIS, are set in `dev_project.py` (see `dev.py`'s docstring).
- `dev` is a reserved module name: `dev.py tests` loads `dev.py` into pytest as `dev`, which
  would hide a test helper of that name.
- Dev tooling goes in `dev.py` or `dev_project.py`, not shell scripts; the git hooks are
  the only ones. The pre-commit hook formats and lint-fixes staged files; the pre-push hook
  runs the static checks before every push; CI runs the tests.
- `pyproject.toml` is partly the template's. Add dev dependencies to `dev` with
  `uv add --dev`, never to `template-dev`, and add to Ruff's `extend-select` and
  basedpyright's `include` below their `# The project's own below:` line, which stays as it
  is, so the template's changes merge without conflicts.
- Commit `uv.lock` with the `pyproject.toml` change that updates it: in CI, `dev.py` runs
  `uv sync --locked`, which fails on a stale lock. `dev.py venv` says when it changed.

## Tests and checks

- **Before committing, run `python3 dev.py static`** (Ruff's formatter and linter,
  basedpyright in strict mode; each runs even if another fails) **and the tests near your
  change** (`python3 dev.py tests PATH`). CI runs the full suite on each PR into `main` and
  on `main`, and a PR merges only once it passes. A stacked PR (into another PR's branch)
  gets no CI until it targets `main`, so these local checks are all it gets till then. In
  a GitHub stack, merge from the bottom, one PR at a time, each once its own CI has passed:
  a PR above the bottom never gets "CI passed", so merging it waits forever.
  `python3 dev.py coverage` shows what the tests don't run.
- **PRs are squash-merged**, so the checks must pass on a PR's last commit, and CI must pass
  on it too (the "CI passed" check, which main's ruleset should require). In rapid
  development, a PR's test jobs on macOS or Windows that never got a runner (GitHub's are
  sometimes unavailable for long stretches) don't fail "CI passed" if Linux's passed: it
  names them in a warning and in its summary. Those systems' tests didn't run, so mind the
  code that differs there. On `main` they still fail it, and out of rapid development they
  always do.
- **Tests keep to themselves**: no network, and not the user's real config, home directory
  or other state; use `tmp_path`, and give programs the tests start a config that reaches
  nothing.
- **Keep the tests fast.** Starting a process costs about 24ms on GitHub's Windows runners
  and 4–7ms on macOS, against under 1ms on Linux, and how Python starts it makes no
  difference, so on those systems the number of processes is what makes a suite slow. Call
  the code in the test's process rather than through its command line, except in the few
  tests of the command line itself; build expensive fixtures (e.g. git repositories) once per
  session and copy them for each test; and prefer one long-lived process (e.g.
  `git cat-file --batch`) over one per request.
- **Each CI job has a time limit** (`timeout-minutes`), about 20% over how long it takes in
  finished runs, rounded up to a whole minute, and never over 5 minutes, so no job keeps a
  run waiting long. If a job runs into its limit, speed it up if you can (the tests, or the
  code itself); if you can't, raise the limit, up to 5, saying why in the commit; past that,
  split the job.
- **Tests too slow for one job are split.** If a system's tests take over about 4 minutes in
  CI even once sped up, run them in several jobs there (shards), each with a share of the
  tests: set how many in the project's Copier answers, `test_shards_linux`,
  `test_shards_macos` or `test_shards_windows` (1 to 8, 1 by default). If
  `.copier-answers.yml` lists them already, this changes the answer alone, without the
  template's later changes:

  ```sh
  uvx copier update --defaults -d test_shards_windows=3 \
      --vcs-ref "$(sed -n "s/^_commit: //p" .copier-answers.yml | tr -d "'")"
  ```

  If it doesn't, that template commit has no such question, and the command quietly changes
  nothing: run `uvx copier update --defaults -d test_shards_windows=3`, which takes in the
  template's later changes too. Either way, check that `git diff` shows the new answer and
  `ci.yml`'s new jobs, then commit them. Each of a system's jobs, with each Python, runs
  `python3 dev.py tests --shard I/N` (see `dev.py`), which you can run locally too. A shard
  left with no tests fails its job, so keep to fewer shards than tests; and each job sets up
  again, so split no more than you need. Coverage runs in one job, on Linux, whatever the
  answers say.
- **CI is the template's `ci.yml` and `cancel.yml`, and the project's `project.yml`.**
  `.github/workflows/ci.yml` is the template's (`copier update` keeps it current): the static
  checks, the tests, coverage, and the `CI passed` job a ruleset requires. The project's own
  jobs go in `.github/workflows/project.yml`, which `ci.yml` runs and `CI passed` waits for;
  they can set up uv and Python with `.github/actions/setup`, also the template's.
  `.github/workflows/cancel.yml`, the template's too, cancels a pull request's `ci.yml` run
  when it merges or closes, so its concurrency group must stay the same as `ci.yml`'s. Both
  run only for PRs into `main`. A retarget to `main` alone starts no `ci.yml` run: git stack
  and GitHub's stacks push after one, but GitHub's own retarget when the branch below is
  deleted (outside a GitHub stack) doesn't. If none started, push again, or close and reopen
  the PR (re-running an earlier run keeps its old base).

## Linux, macOS and Windows

The project runs on all three, and CI runs the tests on each. Keep it that way, with every
change:

- Use `pathlib.Path` for paths, not strings joined with `/`, and compare them as `Path`s.
  Write paths into TOML or URLs with `as_posix()`.
- Read and write text as UTF-8 (`encoding="utf-8"`, not `text=True`): Windows' default
  encoding is its code page. Don't assume `\n` line endings in files you didn't write, or a
  case-sensitive filesystem (macOS and Windows usually aren't).
- Run programs with their arguments as a list, never through a shell, and don't call Unix
  tools (`sed`, `grep`, ...) from Python. The project itself mustn't need a shell.
- Branch on `sys.platform` rather than assuming POSIX (`fcntl`, `os.fork`, signals, modes).
  When a system needs its own way, give it one rather than skipping it there.
- In tests, make a fake command as a Python script with a launcher, not a shell script or a
  symlink, which Windows can't run or usually can't make. A test that can only run on one
  system says why in its `skipif`, and the other systems get a test of their own.

## Reviewing

Every PR is reviewed by a fresh agent before it leaves draft: one with no context but the PR
(not a fork of the session that wrote it), so it judges what the code says rather than what
was meant. An agent says it's an agent wherever it writes as a person would: in the first line
of a PR's description, a review, and each answer to a finding.

- **The reviewer** reads the diff against the PR's base, its commit messages, this file and
  `PROJECT_AGENTS.md`, and changes nothing. It runs nothing that changes the user's
  environment: no `dev.py install`, no global installs (`uv tool install`, `pipx install`,
  `pip install --user`). It looks for, most important first:
  - correctness: obviously correct is much better than just correct;
  - security: use the right secure library rather than writing our own;
  - surprises: anything surprising that's necessary has a comment and a test, only necessary
    changes are accepted, and nothing outside the change is left inconsistent with it;
  - tests: missing ones (new features fully covered), and low-value ones (would it fail if the
    code were wrong in a meaningful way? is it no bigger than it needs to be?), or ones that
    pin down a wrong behavior;
  - docs, docstrings and comments that are missing or untrue, or that only describe a prior
    state of the code or an idea that was rejected;
  - dead code; readability; terseness;
  - fully qualified imports rather than relative ones;
  - size (a large PR should be split, where it can be; in rapid development, past about 800
    lines), risk, importance (does it move the needle?) and cost.

  It checks each finding against the code, and gives it a severity (bug, should-fix, minor),
  a failure scenario and a fix.
- **It posts one GitHub review of type COMMENT** (never approving or requesting changes),
  its findings numbered, each inline on its line where it has one. The JSON goes in a
  temporary directory, not the repo:

  ```sh
  dir=$(mktemp -d)
  gh pr view N --json headRefOid -q .headRefOid   # the head GitHub has, not a local one
  gh api repos/{owner}/{repo}/pulls/N/reviews --method POST --input "$dir/review.json"
  # {"commit_id": "<that sha>", "event": "COMMENT", "body": "...",
  #  "comments": [{"path": "src/...", "line": 42, "side": "RIGHT", "body": "1. bug: ..."}]}
  ```
- **The PR's author** fixes what it decides to, pushes, then answers every finding (inline
  ones in their thread, the body's in one PR comment): fixed, with the commit's short hash,
  or why not (wrong, out of scope, or not worth its cost).
- **Fixes are reviewed too**, the same way, on `<reviewed sha>..HEAD`, unless all they
  change is wording. Fix and review again until a review finds no bugs or should-fixes.
- **A PR merges** once every finding is answered, CI passes and the maintainer says go:
  squashed, and stacked PRs in order, each safe to merge on its own.

After a run of merges, several fresh agents review the whole code at once, each one part (a
module, the tests, the tooling and CI, the docs against the code) at one commit of `main`,
each reporting its findings to the agent that started them, which records what isn't fixed
yet in `docs/issues.md`; the fixes are PRs as above.
