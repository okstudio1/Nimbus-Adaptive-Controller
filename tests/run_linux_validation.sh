#!/usr/bin/env bash
# Validate the Linux stack on this machine, in one command.
#
#   ./tests/run_linux_validation.sh
#
# Sets up the venv if it is missing, runs the hardware-free suite and the
# Linux probes, and writes everything to one log you can paste back.
#
# Nothing here grabs the mouse you are holding. Pass --grab to add the
# grab-failure cleanup check, which briefly takes a real pointer device: do
# that only while sitting at the machine, with a keyboard to recover with.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
GRAB=""
[ "${1:-}" = "--grab" ] && GRAB="--grab"

LOG="linux-validation-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1

echo "Nimbus Linux validation"
echo "======================="
echo "date    : $(date -Is)"
echo "host    : $(uname -srm)"
echo "distro  : $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo unknown)"
echo "session : ${XDG_SESSION_TYPE:-unknown}  desktop: ${XDG_CURRENT_DESKTOP:-unknown}"
echo "branch  : $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null)"
echo "groups  : $(id -nG)"
echo -n "uinput  : "
if [ -w /dev/uinput ]; then echo "writable"
elif [ -e /dev/uinput ]; then echo "PRESENT BUT NOT WRITABLE -> see the note at the end"
else echo "MISSING -> try: sudo modprobe uinput"; fi
echo

# ---- venv ----------------------------------------------------------------
if [ ! -x venv/bin/python ]; then
    echo "Creating venv..."
    python3 -m venv venv || { echo "could not create venv; install python3-venv"; exit 1; }
    ./venv/bin/pip -q install --upgrade pip
    ./venv/bin/pip -q install -r requirements.txt || echo "(some requirements failed; continuing)"
fi
PY=./venv/bin/python
echo "python  : $($PY --version 2>&1)"

FAILED=""
GATES=""
run() {                      # run <label> <command...>
    local label="$1"; shift
    echo
    echo "=================================================================="
    echo ">>> $label"
    echo "=================================================================="
    if "$@"; then
        echo "<<< $label: PASSED"
    else
        echo "<<< $label: FAILED (exit $?)"
        FAILED="$FAILED $label"
    fi
}

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

run "fast suite"            $PY tests/run_fast_tests.py
run "linux stack probe"     $PY -m tests.probe_linux_stack $GRAB
run "uinput round-trip"     $PY -m tests.test_uinput

# Both of these need a real display and xdotool. A missing tool is a gate, not
# a finding, so they are skipped with a reason rather than run and failed.
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    echo; echo ">>> evdev grab and always-on-top: GATE (no display; run from a desktop session)"
    GATES="$GATES
  - no display: run from a desktop session, X11 preferred"
elif ! command -v xdotool >/dev/null 2>&1; then
    echo; echo ">>> evdev grab and always-on-top: GATE (xdotool not installed)"
    GATES="$GATES
  - xdotool missing: sudo apt install xdotool"
else
    run "evdev grab probe"  $PY -m tests.probe_evdev_grab
    run "always on top"     $PY -m tests.probe_always_on_top
fi

echo
echo "=================================================================="
if [ -n "$GATES" ]; then
    echo "Environment gates (properties of this machine, not of the code):"
    printf "$GATES
"
    echo
fi
if [ -n "$FAILED" ]; then
    echo "FAILED:$FAILED"
    echo
    echo "A failed stage is a real finding: these are claims about the code, checked"
    echo "against a real kernel. Paste this whole log back rather than trying to fix"
    echo "it, and do not confuse it with the gates above, which only mean less ran."
else
    echo "Everything that could run, passed."
    [ -n "$GATES" ] && echo "Close the gates above and re-run to cover the rest."
fi
[ -w /dev/uinput ] || cat <<'NOTE'

/dev/uinput was not writable, so the uinput checks could not mean much. Fix:
    sudo cp build_tools/linux/60-nimbus-uinput.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo udevadm trigger --name-match=uinput
    sudo udevadm trigger --subsystem-match=input
    sudo usermod -aG input "$USER"
Then log out of the graphical session entirely and back in. A new shell is not
enough, and neither is newgrp for the desktop session; confirm with: id -nG
Do not work around it by running this script as root: that tests something
other than what a user will actually run.
NOTE
echo
echo "Log written to: $LOG"
[ -n "$FAILED" ] && exit 1
exit 0
