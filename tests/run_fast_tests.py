"""
Run the fast, hardware-free tests, the way they have to be run.

The files under ``tests/test_*.py`` import the app as ``src.<module>`` and
each other as ``tests.<module>``, so they only work as modules from the
repo root (``python -m tests.test_bridge_services``); run by path they fail
on ``No module named 'src'`` before executing a line. This runner does the
right thing for every one of them, one subprocess per file so that one
file's ``QApplication`` or ``sys.exit`` cannot take the others with it,
prints one line per file and a total, and exits non-zero on any failure.
It is what CI runs (``.github/workflows/fast-tests.yml``).

Two files that match the pattern are not tests and are skipped by name:
``test_vjoy.py`` is a vJoy driver diagnostic that needs a real vJoy
install, and ``test_dialog.py`` is an interactive pygame script for the
legacy shell. The hardware probes are ``probe_*_windows.py`` and do not
match the pattern.

Run (from anywhere, with the project's venv)::

    venv\\Scripts\\python tests\\run_fast_tests.py
    venv\\Scripts\\python tests\\run_fast_tests.py --match shaping --verbose
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from typing import Dict, List

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO, "tests")

SKIP: Dict[str, str] = {
    "test_vjoy.py": "vJoy driver diagnostic, needs a real vJoy install",
    "test_dialog.py": "interactive pygame script for the legacy shell, not a test",
}
PER_FILE_TIMEOUT_S = 600


def discover(match: str = "") -> List[str]:
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(TESTS, "test_*.py")))
    return [f for f in files if match in f]


def run_one(name: str, verbose: bool, env: Dict[str, str]) -> bool:
    module = "tests." + name[:-3]
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-m", module], cwd=REPO, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=PER_FILE_TIMEOUT_S)
        ok = p.returncode == 0
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        ok = False
        out = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")) \
            + f"\n[runner] timed out after {PER_FILE_TIMEOUT_S}s"
    dt = time.monotonic() - t0
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {dt:6.1f}s", flush=True)
    if verbose or not ok:
        tail = out.strip().splitlines()
        if not verbose:
            tail = tail[-40:]
        for line in tail:
            print("        " + line, flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", default="", help="only files whose name contains this")
    ap.add_argument("--verbose", action="store_true", help="print every file's output, not just failures")
    ap.add_argument("--list", action="store_true", help="list the files that would run and exit")
    args = ap.parse_args()

    files = discover(args.match)
    if args.list:
        for f in files:
            print(f"{f}" + (f"  (skipped: {SKIP[f]})" if f in SKIP else ""))
        return 0

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")     # three files build a QApplication
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    print(f"fast tests from {TESTS} with {sys.executable}", flush=True)
    passed: List[str] = []
    failed: List[str] = []
    for name in files:
        if name in SKIP:
            print(f"  [SKIP] {name:<36}  {SKIP[name]}", flush=True)
            continue
        (passed if run_one(name, args.verbose, env) else failed).append(name)
    ran = len(passed) + len(failed)
    print(f"\n{len(passed)}/{ran} files passed" + (f"; failed: {', '.join(failed)}" if failed else ""), flush=True)
    if ran == 0:
        print("nothing ran", flush=True)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
