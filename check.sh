#!/bin/sh
# Run every check: formatting, lint, types (strict), then the tests; arguments go to
# pytest, e.g. `./check.sh -k panels`. See dev.py, which does the work on every system.
exec python3 "$(dirname "$0")/dev.py" check "$@"
