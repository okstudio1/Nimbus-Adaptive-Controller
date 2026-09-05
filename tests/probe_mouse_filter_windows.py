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


def raw_set_isolation(handle: int, enable: bool) -> None:
    value = wintypes.DWORD(1 if enable else 0)
    returned = wintypes.DWORD(0)
    ok = iso._k32.DeviceIoControl(handle, iso.IOCTL_NIMBUS_SET_ISOLATION, ctypes.byref(value),
                                  ctypes.sizeof(value), None, 0, ctypes.byref(returned), None)
    if not ok:
        raise RuntimeError(f"SET_ISOLATION failed: {ctypes.get_last_error()}")


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
        raw_set_isolation(h, True)
        time.sleep(0.3)
        iso._k32.CloseHandle(h)
        time.sleep(0.3)
        st3 = iso.get_status()
        record("U3 handle drop clears isolation", st3["isolating"] == 0, f"isolating={st3['isolating']}")
    except Exception as exc:
        record("U3 handle drop clears isolation", False, str(exc))

    # U4: watchdog clears isolation when nobody reads
    try:
        before = iso.get_status()["watchdog_releases"]
        h = iso._open_device()
        raw_set_isolation(h, True)
        print("      (mouse isolated with no reader; the watchdog should release it in about 2 s)", flush=True)
        time.sleep(3.2)
        # Status through the same handle: the device is exclusive.
        st4 = iso._query_status(h)
        isolating, releases = st4["isolating"], st4["watchdog_releases"]
        record("U4 watchdog releases isolation", isolating == 0 and releases == before + 1,
               f"isolating={isolating} watchdog_releases {before} -> {releases}")

        # U6: a read after the release must fail fast with ERROR_NOT_READY, which
        # is how MouseIsolation's reader learns the mouse was given back.
        buf = ctypes.create_string_buffer(iso._MOUSE_INPUT_DATA.size)
        returned = wintypes.DWORD(0)
        ov = iso._OVERLAPPED()
        ov.hEvent = iso._k32.CreateEventW(None, True, False, None)
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
        elapsed = time.monotonic() - t0
        iso._k32.CloseHandle(ov.hEvent)
        iso._k32.CloseHandle(h)
        record("U6 read after release fails with ERROR_NOT_READY", err == iso.ERROR_NOT_READY,
               f"error={err} after {elapsed:.3f}s (expected {iso.ERROR_NOT_READY})")
    except Exception as exc:
        record("U4 watchdog releases isolation", False, str(exc))

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

    def phase(name: str, setup, teardown) -> Dict[str, int]:
        bring_to_front(game.hwnd)
        cx, cy = window_center(game.hwnd)
        user32.SetCursorPos(cx, cy)
        setup()
        time.sleep(0.4)
        g0 = game.settle()
        m0 = dict(motion)
        print(f"\n>>> {name}: MOVE THE PHYSICAL MOUSE for {seconds:.0f} s", flush=True)
        for remaining in range(int(seconds), 0, -1):
            print(f"    {remaining}...", end="\r", flush=True)
            time.sleep(1.0)
        g1 = game.settle()
        m1 = dict(motion)
        teardown()
        time.sleep(0.4)
        out = {
            "game_input_abs": g1["input_abs"] - g0["input_abs"],
            "game_mousemove": g1["mousemove"] - g0["mousemove"],
            "captured_abs": m1["abs"] - m0["abs"],
            "captured_events": m1["events"] - m0["events"],
        }
        print(f"    {name:<13} game WM_INPUT abs={out['game_input_abs']:>7} MOUSEMOVE={out['game_mousemove']:>5} "
              f"| driver captured abs={out['captured_abs']:>7} events={out['captured_events']}", flush=True)
        return out

    try:
        p1 = phase("pass-through", lambda: None, lambda: None)
        p2 = phase("isolated", lambda: m.start(), lambda: m.stop("phase done"))
        p3 = phase("released", lambda: None, lambda: None)
    finally:
        try:
            m.stop("probe end")
        except Exception:
            pass
        panel.close()
        game.close()

    record("A1 pass-through reaches the game", p1["game_input_abs"] > 0 and p1["captured_abs"] == 0,
           f"game={p1['game_input_abs']} captured={p1['captured_abs']}")
    record("A2 isolated: game blind, driver sees motion", p2["game_input_abs"] == 0 and p2["captured_abs"] > 0,
           f"game={p2['game_input_abs']} captured={p2['captured_abs']}")
    record("A3 released: game sees the mouse again", p3["game_input_abs"] > 0,
           f"game={p3['game_input_abs']}")


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
