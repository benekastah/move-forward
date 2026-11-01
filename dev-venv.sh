#!/bin/sh
# Create .venv with the package (editable) and the dev dependencies from
# pyproject.toml, or bring it up to date with them: they're reinstalled
# whenever pyproject.toml has changed since they were last installed. check.sh,
# install.sh and the pre-commit hook run it; it's quick when there's
# nothing to do.
#
# Uses uv when it's installed, else python3 -m venv and pip (25.1+ for --group).

set -eu

cd "$(dirname "$0")"

if [ -f .venv/installed ] && [ ! pyproject.toml -nt .venv/installed ]; then
    exit 0
fi

echo "dev-venv: installing the package and dev dependencies into .venv" >&2
if command -v uv > /dev/null 2>&1; then
    uv sync --quiet
else
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
    fi
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet --editable . --group dev
fi
touch .venv/installed
