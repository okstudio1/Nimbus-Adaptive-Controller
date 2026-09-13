#!/usr/bin/env sh
# Linux/macOS launcher (mirrors run.bat): creates venv/, installs the
# requirements, and starts the Qt Quick app via run.py.
cd "$(dirname "$0")" && exec python3 run.py "$@"
