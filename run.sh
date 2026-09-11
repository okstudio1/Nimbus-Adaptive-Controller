#!/usr/bin/env bash
# Launcher for Nimbus Adaptive Controller on Linux / macOS.
# Mirrors run.bat: delegates to run.py, which creates venv/ and installs
# requirements.txt on first launch.
set -euo pipefail

cd "$(dirname "$0")"

echo "Starting Nimbus Adaptive Controller - Virtual Controller Interface..."
echo

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python is not installed or not in PATH"
    echo "Please install Python 3.8+ and try again"
    exit 1
fi

exec "$PYTHON" run.py "$@"
