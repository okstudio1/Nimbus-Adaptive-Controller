"""
Guard against a method being defined twice in ``ControllerBridge``.

Python binds the *later* definition, silently, so a duplicate turns the
earlier one into dead code with no error, no warning and no test failure.
That is not hypothetical: merging the Windows and Linux branches put both
mouse-isolation implementations into the same class body, and on Windows the
Linux software-cursor methods overrode the cursor relay. Every test still
passed, because nothing in the fast suite exercises that path.

This parses ``src/bridge.py`` rather than importing it, so it sees every
definition rather than only the ones that survived binding. Property and
setter pairs are legitimate and excluded.

``KNOWN`` is the tolerated-duplicates list. It is empty. Adding to it should
be a deliberate act with a reason, not the fix for a failing run: a duplicate
means one of the two definitions is dead code.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BRIDGE = Path(__file__).resolve().parent.parent / "src" / "bridge.py"

#: Duplicates that are tolerated. Empty, and meant to stay that way: every
#: entry is a method whose earlier definition never runs. The ten that were
#: here when this test was written have since been removed, after checking in
#: each case that the surviving definition was the correct one.
KNOWN: set = set()

PASSES = 0
FAILS = 0


def check(label, condition):
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"  [PASS] {label}")
    else:
        FAILS += 1
        print(f"  [FAIL] {label}")


def _is_property_pair(nodes):
    """True if these definitions are a property and its setter/deleter.

    ``@property`` plus ``@x.setter`` legitimately share a name.
    """
    kinds = set()
    for node in nodes:
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "property":
                kinds.add("get")
            elif isinstance(dec, ast.Attribute) and dec.attr in ("setter", "deleter"):
                kinds.add(dec.attr)
    return "get" in kinds and bool(kinds - {"get"})


def duplicate_methods():
    """Method names defined more than once in ControllerBridge."""
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "ControllerBridge"), None)
    assert cls is not None, "ControllerBridge not found in src/bridge.py"

    by_name = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            by_name.setdefault(node.name, []).append(node)
    return {name: [n.lineno for n in nodes]
            for name, nodes in by_name.items()
            if len(nodes) > 1 and not _is_property_pair(nodes)}


def main():
    print("\nNo method may be defined twice in ControllerBridge")
    dupes = duplicate_methods()

    unexpected = {n: ls for n, ls in dupes.items() if n not in KNOWN}
    for name, linenos in sorted(unexpected.items()):
        print(f"         {name} defined at lines {linenos}; only the last one runs")
    check("no new duplicate method definitions", not unexpected)

    # The isolation split is the one this test was written for. Both
    # implementations must exist under distinct names, with a dispatcher.
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for stem in ("_on_iso_motion", "_on_iso_button", "_on_iso_wheel",
                 "_on_iso_stopped", "_iso_send_mouse"):
        check(f"{stem} has a relay and a software implementation",
              f"{stem}_relay" in names and f"{stem}_sw" in names)
        check(f"{stem} has a single dispatcher", stem in names and stem not in dupes)

    # A stale entry means someone fixed a duplicate: tighten the list.
    stale = KNOWN - set(dupes)
    for name in sorted(stale):
        print(f"         {name} is no longer duplicated; remove it from KNOWN")
    check("KNOWN lists no duplicates that have since been fixed", not stale)

    print(f"\n  {len(dupes)} duplicate(s) present, {len(KNOWN)} of them known and tracked")
    print(f"\n{PASSES}/{PASSES + FAILS} checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
