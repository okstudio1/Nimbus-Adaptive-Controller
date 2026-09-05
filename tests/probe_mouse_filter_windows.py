"""
Nimbus Mouse Filter probe (throwaway, not Nimbus code).

Exercises the kernel filter in ``driver/`` through ``src/mouse_isolation_win.py``.
Needs the driver installed and started (``driver/install-dev.ps1``).

Unattended checks (no mouse movement required, safe to run over a remote
session; the mouse is isolated for at most a few seconds at a time):

  U1  status readable and the interface version matches
  U2  MouseIsolation.start() turns isolation on with a read pending; stop() turns it off
  U3  handle drop: isolation set on a raw handle is cleared when the handle closes
  U4  watchdog: isolation with no read pending is cleared within ~3 s
  U5  exclusivity: a second open fails while the first handle is open
  U6  a read after the watchdog release fails at once with ERROR_NOT_READY

Attended phases (``--attended``): a fake Raw Input game and a Nimbus stand-in
from ``probe_rawinput_windows.py`` are spawned, and you move the physical mouse
during three timed phases:

  pass-through   game receives WM_INPUT, driver captures nothing
  isolated       game receives NOTHING, driver captures the motion, cursor frozen
  released       game receives WM_INPUT again

Injected motion cannot test the filter (SendInput enters above it), which is
why this one needs a hand on the mouse.

Run::

    venv\\Scripts\\python tests\\probe_mouse_filter_windows.py            # unattended checks
    venv\\Scripts\\python tests\\probe_mouse_filter_windows.py --attended # plus the three phases
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from typing import Dict, List

if sys.platform != "win32":
    sys.exit("Windows only")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from src import mouse_isolation_win as iso  # noqa: E402

RESULTS: List[Dict[str, object]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "note": note})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {note}", flush=True)


# ---- unattended ---------------------------------------------------------------
def unattended() -> None:
    print("Unattended checks")
    try:
        st = iso.get_status()
    except RuntimeError as exc:
        record("U1 status readable", False, str(exc))
        return
    record("U1 status readable", st["version"] == iso.INTERFACE_VERSION,
           f"version={st['version']} mice={st['connected_mice']} isolating={st['isolating']} "
           f"passed={st['packets_passed']} captured={st['packets_captured']}")

    # U2: the module's own lifecycle
    motion = {"n": 0}
    stops: List[str] = []
    m = iso.MouseIsolation(lambda dx, dy: motion.__setitem__("n", motion["n"] + 1), lambda c, p: None,
                           on_stopped=stops.append)
    try:
        m.start()
        time.sleep(0.5)
        # The device is exclusive, so status while active must go through the
        # instance's own handle; get_status() would fail here.
        live = m.status()
        active_ok = m.active and live["isolating"] == 1
        grabbed = m.grabbed_devices
        m.stop("probe")
        time.sleep(0.3)
        st2 = iso.get_status()
        record("U2 start/stop lifecycle",
               active_ok and st2["isolating"] == 0 and len(grabbed) == 1 and stops == ["probe"],
               f"active={active_ok} isolating_live={live['isolating']} pending_reads_live={live['pending_reads']} "
               f"grabbed={len(grabbed)} isolating_after_stop={st2['isolating']} stop_reasons={stops} motion_events={motion['n']}")
    except Exception as exc:
        record("U2 start/stop lifecycle", False, str(exc))
        try:
            m.stop("probe error")
        except Exception:
            pass

    # U3: handle drop clears isolation
    try:
        h = iso._open_device()
        iso._set_isolation(h, True)
        time.sleep(0.3)
        iso._k32.CloseHandle(h)
        time.sleep(0.3)
        st3 = iso.get_status()
        record("U3 handle drop clears isolation", st3["isolating"] == 0, f"isolating={st3['isolating']}")
    except Exception as exc:
        record("U3 handle drop clears isolation", False, str(exc))

    # U4: watchdog clears isolation when nobody reads. U6 reuses the handle, so
    # both close in a finally: a leaked handle would make U5 fail for the wrong
    # reason (the device is exclusive) and hold isolation on.
    h = None
    event = None
    u4_done = u6_done = False
    try:
        before = iso.get_status()["watchdog_releases"]
        h = iso._open_device()
        iso._set_isolation(h, True)
        print("      (mouse isolated with no reader; the watchdog should release it in about 2 s)", flush=True)
        time.sleep(3.2)
        # Status through the same handle: the device is exclusive.
        st4 = iso._query_status(h)
        isolating, releases = st4["isolating"], st4["watchdog_releases"]
        record("U4 watchdog releases isolation", isolating == 0 and releases == before + 1,
               f"isolating={isolating} watchdog_releases {before} -> {releases}")
        u4_done = True

        # U6: a read after the release must fail fast with ERROR_NOT_READY, which
        # is how MouseIsolation's reader learns the mouse was given back.
        buf = ctypes.create_string_buffer(iso._MOUSE_INPUT_DATA.size)
        returned = wintypes.DWORD(0)
        ov = iso._OVERLAPPED()
        event = iso._k32.CreateEventW(None, True, False, None)
        ov.hEvent = event
        t0 = time.monotonic()
        ok = iso._k32.ReadFile(h, buf, ctypes.sizeof(buf), ctypes.byref(returned), ctypes.byref(ov))
        err = 0 if ok else ctypes.get_last_error()
        if err == iso.ERROR_IO_PENDING:
            # Give a slow completion a moment, then treat a still-pending read as the old hang.
            time.sleep(0.5)
            if iso._k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(returned), False):
                err = 0
            else:
                err = ctypes.get_last_error()
            if err == 996:  # ERROR_IO_INCOMPLETE: still pending, would wait forever
                iso._k32.CancelIoEx(h, None)
                iso._k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(returned), True)
        elapsed = time.monotonic() - t0
        record("U6 read after release fails with ERROR_NOT_READY", err == iso.ERROR_NOT_READY,
               f"error={err} after {elapsed:.3f}s (expected {iso.ERROR_NOT_READY})")
        u6_done = True
    except Exception as exc:
        if not u4_done:
            record("U4 watchdog releases isolation", False, str(exc))
        else:
            record("U6 read after release fails with ERROR_NOT_READY", False, str(exc))
    finally:
        if event:
            iso._k32.CloseHandle(event)
        if h:
            iso._k32.CloseHandle(h)
        if not u6_done and not u4_done:
            record("U6 read after release fails with ERROR_NOT_READY", False, "skipped (U4 did not complete)")

    # U5: exclusivity
    try:
        h1 = iso._open_device()
        try:
            h2 = iso._open_device()
            iso._k32.CloseHandle(h2)
            record("U5 exclusive open", False, "second open succeeded")
        except RuntimeError as exc:
            record("U5 exclusive open", True, str(exc))
        finally:
            iso._k32.CloseHandle(h1)
    except Exception as exc:
        record("U5 exclusive open", False, str(exc))


# ---- attended -----------------------------------------------------------------
def attended(seconds: float) -> None:
    from probe_rawinput_windows import GameProcess, Panel, bring_to_front, window_center, user32

    scratch = os.path.join(REPO, "tests", "probe_frames")
    os.makedirs(scratch, exist_ok=True)
    game = GameProcess("hwnd", "none", os.path.join(scratch, "filter_game_stats.json"))
    panel = Panel()
    motion = {"abs": 0, "events": 0}

    def on_motion(dx, dy):
        motion["abs"] += abs(dx) + abs(dy)
        motion["events"] += 1

    m = iso.MouseIsolation(on_motion, lambda c, p: None)

    MB_OK, MB_ICONINFORMATION, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x40, 0x10000, 0x40000
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]

    def driver_counters() -> Dict[str, int]:
        # While this process is isolating the device is exclusive, so read
        # through the instance's own handle; otherwise open, read, close.
        st = m.status() if m.active else iso.get_status()
        return {"passed": st["packets_passed"], "captured": st["packets_captured"]}

    def phase(index: int, name: str, setup, teardown) -> Dict[str, int]:
        user32.MessageBoxW(None,
                           f"Phase {index} of 3: {name}\n\n"
                           f"Click OK (or press Enter), then move the mouse continuously "
                           f"for {seconds:.0f} seconds, until the next box appears.\n\n"
                           + ("The cursor will FREEZE during this phase. Keep moving anyway."
                              if name == "isolated" else "The cursor should move normally."),
                           "Nimbus Mouse Filter probe", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)
        bring_to_front(game.hwnd)
        cx, cy = window_center(game.hwnd)
        user32.SetCursorPos(cx, cy)
        setup()
        time.sleep(0.4)
        d0 = driver_counters()
        g0 = game.settle()
        m0 = dict(motion)
        print(f"\n>>> {name}: MOVE THE PHYSICAL MOUSE for {seconds:.0f} s", flush=True)
        for remaining in range(int(seconds), 0, -1):
            user32.SetWindowTextW(game.hwnd, f"{name}: MOVE THE MOUSE ({remaining} s left)")
            print(f"    {remaining}...", end="\r", flush=True)
            time.sleep(1.0)
        user32.SetWindowTextW(game.hwnd, f"{name}: done")
        g1 = game.settle()
        m1 = dict(motion)
        d1 = driver_counters()
        teardown()
        time.sleep(0.4)
        out = {
            "game_input_abs": g1["input_abs"] - g0["input_abs"],
            "game_mousemove": g1["mousemove"] - g0["mousemove"],
            "captured_abs": m1["abs"] - m0["abs"],
            "captured_events": m1["events"] - m0["events"],
            "driver_passed": d1["passed"] - d0["passed"],
            "driver_captured": d1["captured"] - d0["captured"],
        }
        print(f"    {name:<13} game WM_INPUT abs={out['game_input_abs']:>7} MOUSEMOVE={out['game_mousemove']:>5} "
              f"| client captured abs={out['captured_abs']:>7} events={out['captured_events']:>5} "
              f"| driver passed={out['driver_passed']:>5} captured={out['driver_captured']:>5}", flush=True)
        return out

    try:
        p1 = phase(1, "pass-through", lambda: None, lambda: None)
        p2 = phase(2, "isolated", lambda: m.start(), lambda: m.stop("phase done"))
        p3 = phase(3, "released", lambda: None, lambda: None)
    finally:
        try:
            m.stop("probe end")
        except Exception:
            pass
        panel.close()
        game.close()

    if p1["driver_passed"] == 0 and p1["game_input_abs"] == 0:
        print("  (no packets passed the filter and none reached the game: the mouse did not move in phase 1)")
    record("A1 pass-through reaches the game", p1["game_input_abs"] > 0 and p1["captured_abs"] == 0,
           f"game={p1['game_input_abs']} client_captured={p1['captured_abs']} driver_passed={p1['driver_passed']}")
    record("A2 isolated: game blind, driver sees motion", p2["game_input_abs"] == 0 and p2["captured_abs"] > 0,
           f"game={p2['game_input_abs']} client_captured={p2['captured_abs']} driver_captured={p2['driver_captured']} "
           f"driver_passed={p2['driver_passed']}")
    record("A3 released: game sees the mouse again", p3["game_input_abs"] > 0,
           f"game={p3['game_input_abs']} driver_passed={p3['driver_passed']}")

    # Show the verdict on screen too, for whoever is at the mouse.
    lines = [f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}" for r in RESULTS if str(r["check"]).startswith("A")]
    summary = "\n".join(lines) + (
        f"\n\nphase 1: game saw {p1['game_input_abs']} px, filter passed {p1['driver_passed']} packets"
        f"\nphase 2: game saw {p2['game_input_abs']} px, filter captured {p2['driver_captured']} packets"
        f"\nphase 3: game saw {p3['game_input_abs']} px, filter passed {p3['driver_passed']} packets"
        "\n\nAll done. You can close this.")
    user32.MessageBoxW(None, summary, "Nimbus Mouse Filter probe: result",
                       MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attended", action="store_true", help="also run the three hands-on phases")
    ap.add_argument("--seconds", type=float, default=6.0, help="length of each attended phase")
    args = ap.parse_args()

    unattended()
    if args.attended:
        print("\nAttended phases (a fake game window and a panel will appear)")
        attended(args.seconds)

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
