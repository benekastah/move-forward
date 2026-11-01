#!/bin/sh
# Set up this clone: .venv, `mfd` on PATH, the git hooks, then `mfd init`. See dev.py
# (on Windows, run `py dev.py install`).
exec python3 "$(dirname "$0")/dev.py" install
