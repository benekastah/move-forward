#!/bin/sh
# Create or update .venv with the package and the dev dependencies. See dev.py.
exec python3 "$(dirname "$0")/dev.py" venv
