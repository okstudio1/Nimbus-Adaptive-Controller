"""
Windows in-game mouse-look probe (throwaway, not Nimbus code).

Windows counterpart of ``tests/probe_game_mouselook.py`` on the Linux branch.
It finds a running game window, captures its client area, and measures how
much the picture changes under a series of conditions.  A camera rotation
changes most of the frame; an idle scene changes only its animated parts,
which is the noise floor.

Conditions
----------
idle                Two frames with no input at all (noise floor).
mouse               Relative mouse sweep via ``SendInput``, game foreground.
                    Control: the camera must move.
mouse+llhook        Same sweep while a ``WH_MOUSE_LL`` hook drops every mouse
                    event (the strongest form of Full Game Mode's hook).
                    Raw Input games keep moving here; that is the gap.
mouse+panel_fg      Same sweep while a Nimbus stand-in window is the
                    foreground window.  Only a foreground change stops
                    ``WM_INPUT`` in user mode, so this should be still.
pad                 ViGEm right stick held, game foreground.  Control: the
                    pad must move the camera.
pad+panel_fg        Right stick held while the stand-in is foreground.  This
                    is the question that decides whether "Nimbus takes the
                    foreground" is a usable Windows workaround for the game.
mouse (after)       Sweep again with the game foreground, to prove recovery.

Injected motion is a stand-in for the physical mouse only above ``win32k``;
it says nothing about a kernel filter.  See ``probe_rawinput_windows.py``.

Run (from the repo root, venv with PySide6 and vgamepad)::

    venv\\Scripts\\python tests\\probe_game_mouselook_windows.py --title "Carrier Command"

The game must already be in a state where the mouse rotates the view.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional

if sys.platform != "win32":
    sys.exit("Windows only")

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

import numpy as np  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_rawinput_windows import (  # noqa: E402
    LLHook, bring_to_front, create_window, make_wndproc, _Counters, _hwnd_int,
    INPUT, MOUSEINPUT, INPUT_MOUSE, MOUSEEVENTF_MOVE, WS_EX_TOPMOST, WM_CLOSE, user32, kernel32,
)

try:
    import vgamepad as vg
    VGAMEPAD_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    VGAMEPAD_AVAILABLE = False
    print(f"vgamepad unavailable: {exc}")

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]


# ---- window lookup and capture -------------------------------------------
def find_window(title_substring: str) -> Optional[int]:
    needle = title_substring.lower()
    found: List[int] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle in buf.value.lower():
            r = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 200 and r.bottom - r.top > 200:
                found.append(_hwnd_int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


def client_rect_on_screen(hwnd: int) -> tuple:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def grab(hwnd: int) -> np.ndarray:
    x, y, w, h = client_rect_on_screen(hwnd)
    screen = QGuiApplication.primaryScreen()
    pm = screen.grabWindow(0, x, y, w, h)
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def frame_diff(a: np.ndarray, b: np.ndarray, step: int = 6, threshold: int = 60, skip_top: int = 0) -> int:
    """Count sampled pixels whose summed RGB delta exceeds ``threshold``."""
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    sa = a[skip_top:h:step, 0:w:step].astype(np.int16)
    sb = b[skip_top:h:step, 0:w:step].astype(np.int16)
    return int((np.abs(sa - sb).sum(axis=2) > threshold).sum())


def save_frame(arr: np.ndarray, path: str) -> None:
    h, w, _ = arr.shape
    img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888)
    img.save(path)


# ---- stimuli ---------------------------------------------------------------
def sweep(px: int, steps: int = 50, spacing_s: float = 0.01) -> None:
    """Move the mouse ``px`` pixels to the right in ``steps`` relative moves."""
    d = max(1, px // steps)
    for _ in range(steps):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(d, 0, 0, MOUSEEVENTF_MOVE, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(spacing_s)


class Pad:
    def __init__(self) -> None:
        self.pad = vg.VX360Gamepad() if VGAMEPAD_AVAILABLE else None
        if self.pad:
            self.pad.update()

    def right_stick(self, x: float, y: float) -> None:
        if self.pad:
            self.pad.right_joystick_float(x_value_float=x, y_value_float=y)
            self.pad.update()

    def hold_right(self, seconds: float, x: float = 1.0) -> None:
        self.right_stick(x, 0.0)
        time.sleep(seconds)
        self.right_stick(0.0, 0.0)

    def close(self) -> None:
        if self.pad:
            self.right_stick(0.0, 0.0)
            self.pad = None


class Panel:
    """Nimbus stand-in on its own thread, topmost, not registered for anything."""

    def __init__(self, x: int, y: int) -> None:
        self.hwnd = 0
        self._ready = threading.Event()
        self._counters = _Counters()
        self._x, self._y = x, y
        self._thread = threading.Thread(target=self._run, daemon=True, name="ProbePanel")
        self._thread.start()
        self._ready.wait(5.0)

    def _run(self) -> None:
        self._proc = make_wndproc(self._counters)
        self.hwnd = create_window("NimbusProbePanel", "Probe panel (Nimbus stand-in)",
                                  self._x, self._y, 360, 520, self._proc, WS_EX_TOPMOST)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


# ---- the run ----------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    hwnd = find_window(args.title)
    if not hwnd:
        print(f"no visible window with '{args.title}' in its title")
        return 2
    x, y, w, h = client_rect_on_screen(hwnd)
    print(f"game hwnd={hwnd} client=({x},{y}) {w}x{h}")
    os.makedirs(args.frames, exist_ok=True)

    panel = Panel(x + w - 380 if x + w - 380 > 0 else 20, y + 40)
    hook = LLHook()
    pad = Pad()
    print(f"panel hwnd={panel.hwnd}; pad={'ok' if pad.pad else 'missing'}")
    time.sleep(1.0)  # let the game notice the pad

    rows: List[Dict[str, Any]] = []

    def game_front() -> None:
        bring_to_front(hwnd)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.4)

    def measure(name: str, stimulus, setup=None, teardown=None) -> int:
        game_front()
        note = ""
        if setup:
            note = setup() or ""
        time.sleep(args.settle)
        fg_before = _hwnd_int(user32.GetForegroundWindow())
        a = grab(hwnd)
        stimulus()
        time.sleep(args.settle)
        b = grab(hwnd)
        fg_after = _hwnd_int(user32.GetForegroundWindow())
        if teardown:
            teardown()
        d = frame_diff(a, b, skip_top=args.skip_top)
        tag = name.replace("+", "_").replace(" ", "_").replace("(", "").replace(")", "")
        save_frame(a, os.path.join(args.frames, f"{tag}_before.png"))
        save_frame(b, os.path.join(args.frames, f"{tag}_after.png"))
        rows.append({"condition": name, "changed": d, "game_fg_before": fg_before == hwnd,
                     "game_fg_after": fg_after == hwnd, "note": note})
        print(f"  {name:<16} changed={d:>6}  game_fg={int(fg_before == hwnd)}/{int(fg_after == hwnd)}  {note}",
              flush=True)
        return d

    def hook_on():
        hook.seen = hook.blocked = 0
        hook.block = True

    def hook_off():
        hook.block = False
        rows[-1]["note"] = f"hook dropped {hook.blocked}/{hook.seen}"

    def panel_front():
        ok = bring_to_front(panel.hwnd)
        return f"panel fg={ok}"

    try:
        noise = measure("idle", lambda: time.sleep(args.hold))
        measure("mouse", lambda: sweep(args.sweep))
        measure("mouse+llhook", lambda: sweep(args.sweep), hook_on, hook_off)
        measure("mouse+panel_fg", lambda: sweep(args.sweep), panel_front)
        if pad.pad:
            measure("pad", lambda: pad.hold_right(args.hold))
            measure("pad+panel_fg", lambda: pad.hold_right(args.hold), panel_front)
        measure("mouse (after)", lambda: sweep(args.sweep))
    finally:
        hook.close()
        pad.close()
        panel.close()
        bring_to_front(hwnd)

    hi = max(3 * noise, 150)
    lo = max(2 * noise, 60)
    print(f"\nnoise floor={noise}  moved if > {hi}, still if <= {lo}")
    for r in rows:
        v = "MOVED" if r["changed"] > hi else ("STILL" if r["changed"] <= lo else "INCONCLUSIVE")
        r["verdict"] = v
        print(f"  {r['condition']:<16} {r['changed']:>6}  {v}")
    out = os.path.join(args.frames, "mouselook_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"title": args.title, "hwnd": hwnd, "client": [x, y, w, h], "noise": noise,
                   "when": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, fh, indent=2)
    print(f"wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True, help="substring of the game window title")
    ap.add_argument("--sweep", type=int, default=400, help="pixels of relative mouse motion")
    ap.add_argument("--hold", type=float, default=0.6, help="seconds to hold the right stick / idle")
    ap.add_argument("--settle", type=float, default=0.5, help="seconds to wait around each capture")
    ap.add_argument("--skip-top", type=int, default=0)
    ap.add_argument("--frames", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_frames"))
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
