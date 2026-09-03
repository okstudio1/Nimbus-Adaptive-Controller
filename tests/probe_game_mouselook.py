#!/usr/bin/env python3
"""
In-game Mouse Isolation check: is a running game blind to a grabbed mouse?

Points at a game window, creates a VIRTUAL uinput mouse (your real mouse is
never touched), and compares whole-window frames under three conditions:

  1. no input                      -> noise floor
  2. sweep, mouse not grabbed      -> the camera / cursor should move
  3. same sweep while Nimbus holds EVIOCGRAB on that mouse -> nothing should move
  4. same sweep after release      -> moves again

Works best when the game is in a mouse-look view (free camera). Menus with
hover highlights also work but produce smaller numbers.

    ./venv/bin/python tests/probe_game_mouselook.py --window "ELDEN RING"
    ./venv/bin/python tests/probe_game_mouselook.py --window "Carrier Command" --sweep 300

Requirements: X11 session with xdotool, write access to /dev/uinput, and read
access to mouse-class event nodes (``sudo usermod -aG input $USER``; before
re-login, run through ``sg input -c "..."``). Frames are written next to the
script's ``--out`` directory for inspection.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import mouse_isolation as mi  # noqa: E402
from src.uinput_interface import (  # noqa: E402
    _ioc, _IOC_WRITE, _UINPUT_SETUP, _INPUT_EVENT, _ui_get_sysname,
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY,
    EV_KEY, EV_SYN, SYN_REPORT, BUS_USB,
)

EV_REL, REL_X, REL_Y, BTN_LEFT = 0x02, 0x00, 0x01, 0x110
UI_SET_RELBIT = _ioc(_IOC_WRITE, "U", 102, 4)


def xdo(*args: str) -> str:
    return subprocess.run(["xdotool", *args], capture_output=True, text=True).stdout.strip()


def pointer() -> tuple:
    d = dict(line.split("=") for line in xdo("getmouselocation", "--shell").split())
    return int(d["X"]), int(d["Y"])


def find_window(name: str) -> int:
    for _ in range(5):
        ids = xdo("search", "--onlyvisible", "--name", name).split()
        if ids:
            return int(ids[-1])
        time.sleep(1)
    raise SystemExit(f"no visible window matching {name!r}")


def create_virtual_mouse() -> tuple:
    ufd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(ufd, UI_SET_EVBIT, EV_REL)
    fcntl.ioctl(ufd, UI_SET_EVBIT, EV_KEY)
    for code in (REL_X, REL_Y):
        fcntl.ioctl(ufd, UI_SET_RELBIT, code)
    fcntl.ioctl(ufd, UI_SET_KEYBIT, BTN_LEFT)
    fcntl.ioctl(ufd, UI_DEV_SETUP, _UINPUT_SETUP.pack(BUS_USB, 0x1209, 0x4E53, 1, b"Nimbus Probe Game Mouse", 0))
    fcntl.ioctl(ufd, UI_DEV_CREATE)
    buf = bytearray(64)
    fcntl.ioctl(ufd, _ui_get_sysname(64), buf)
    sysname = bytes(buf).split(b"\0")[0].decode()
    node = next(f"/dev/input/{e}" for e in os.listdir(f"/sys/devices/virtual/input/{sysname}") if e.startswith("event"))
    return ufd, node


def inject_sweep(ufd: int, dx: int, dy: int) -> None:
    while dx or dy:
        sx = max(-12, min(12, dx))
        sy = max(-12, min(12, dy))
        dx -= sx
        dy -= sy
        os.write(ufd, _INPUT_EVENT.pack(0, 0, EV_REL, REL_X, sx) + _INPUT_EVENT.pack(0, 0, EV_REL, REL_Y, sy)
                 + _INPUT_EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
        time.sleep(0.008)


def frame_diff(a: QImage, b: QImage, step: int, threshold: int, skip_top: int) -> int:
    n = 0
    for y in range(skip_top, a.height(), step):
        for x in range(0, a.width(), step):
            pa, pb = a.pixel(x, y), b.pixel(x, y)
            if (abs(((pa >> 16) & 255) - ((pb >> 16) & 255)) + abs(((pa >> 8) & 255) - ((pb >> 8) & 255))
                    + abs((pa & 255) - (pb & 255))) > threshold:
                n += 1
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--window", required=True, help="substring of the game window title")
    parser.add_argument("--sweep", type=int, default=400, help="horizontal sweep in mouse counts (default 400)")
    parser.add_argument("--settle", type=float, default=0.8, help="seconds to wait after each sweep")
    parser.add_argument("--step", type=int, default=6, help="pixel sampling step for the frame diff")
    parser.add_argument("--threshold", type=int, default=60, help="per-sample RGB delta counted as changed")
    parser.add_argument("--skip-top", type=int, default=40, help="rows to ignore at the top (title bar)")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "probe_frames"),
                        help="directory for the captured frames")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        print("Linux only."); return 1
    if not os.environ.get("DISPLAY"):
        print("Needs an X11 DISPLAY (xdotool and window grabs)."); return 1

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    screen = app.primaryScreen()
    wid = find_window(args.window)
    print(f"game window: {wid} {xdo('getwindowname', str(wid))!r}")

    def frame(tag: str) -> QImage:
        img = screen.grabWindow(wid).toImage().convertToFormat(QImage.Format.Format_RGB32)
        img.save(str(out / f"{tag}.png"))
        return img

    def diff(a: QImage, b: QImage) -> int:
        return frame_diff(a, b, args.step, args.threshold, args.skip_top)

    ufd, node = create_virtual_mouse()
    time.sleep(1.5)
    if not os.access(node, os.R_OK):
        print(f"cannot read {node}: add your user to the 'input' group (sudo usermod -aG input $USER)")
        fcntl.ioctl(ufd, UI_DEV_DESTROY); os.close(ufd)
        return 2
    geo = dict(line.split("=") for line in xdo("getwindowgeometry", "--shell", str(wid)).split())
    cx = int(geo["X"]) + int(geo["WIDTH"]) // 2
    cy = int(geo["Y"]) + int(geo["HEIGHT"]) // 2
    orig = pointer()
    iso = None
    verdict = False
    try:
        xdo("windowactivate", "--sync", str(wid)); time.sleep(0.4)
        xdo("mousemove", "--sync", str(cx), str(cy)); time.sleep(1.0)
        f0 = frame("0_idle_a"); time.sleep(0.7); f1 = frame("0_idle_b")
        noise = diff(f0, f1)
        inject_sweep(ufd, args.sweep, 0); time.sleep(args.settle); f2 = frame("1_ungrabbed")
        d_un = diff(f1, f2)
        inject_sweep(ufd, -args.sweep, 0); time.sleep(args.settle); f3 = frame("2_back")
        iso = mi.MouseIsolation(on_motion=lambda dx, dy: None, on_button=lambda c, p: None, hotkey=False)
        iso.start([node]); time.sleep(0.3)
        p_before = pointer()
        inject_sweep(ufd, args.sweep, 0); time.sleep(args.settle); f4 = frame("3_grabbed")
        d_gr = diff(f3, f4); p_after = pointer()
        iso.stop("done"); iso = None; time.sleep(0.3)
        inject_sweep(ufd, args.sweep, 0); time.sleep(args.settle); f5 = frame("4_released")
        d_re = diff(f4, f5)
        inject_sweep(ufd, -args.sweep, 0); time.sleep(0.3)
        print(f"{'condition':<34}{'changed samples':>16}")
        print(f"{'no input (noise floor)':<34}{noise:>16}")
        print(f"{'sweep, not grabbed':<34}{d_un:>16}")
        print(f"{'sweep, grabbed by Nimbus':<34}{d_gr:>16}   pointer {p_before} -> {p_after}")
        print(f"{'sweep after release':<34}{d_re:>16}")
        hi = max(3 * noise, 150); lo = max(2 * noise, 60)
        verdict = d_un > hi and d_gr <= lo and d_re > hi
        print("VERDICT:", "PASS: the game reacts to the mouse only while it is not grabbed" if verdict
              else "INCONCLUSIVE: inspect the frames (is the game in a mouse-look view and focused?)")
        print(f"frames: {out}")
    finally:
        if iso is not None:
            try:
                iso.stop("cleanup")
            except Exception:
                pass
        fcntl.ioctl(ufd, UI_DEV_DESTROY); os.close(ufd)
        xdo("mousemove", str(orig[0]), str(orig[1]))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
