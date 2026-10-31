#!/bin/sh
# Run every check: that the Python code is formatted (fix it with
# `.venv/bin/ruff format`), Ruff's linter (`.venv/bin/ruff check --fix` fixes
# what it can), basedpyright in strict mode, then the tests. Arguments go to
# pytest, e.g. `./check.sh -k panels`.
#
# Runs dev-venv.sh first, so .venv has the dev dependencies they need.

set -eu

cd "$(dirname "$0")"

./dev-venv.sh

.venv/bin/ruff format --check --quiet
.venv/bin/ruff check --quiet
.venv/bin/basedpyright
.venv/bin/python -m pytest "$@"
