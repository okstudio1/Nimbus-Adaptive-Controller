#!/usr/bin/env python3
"""
Linux uinput diagnostic for Nimbus Adaptive Controller.

Creates the two virtual devices the app uses on Linux (an Xbox 360 gamepad
and a generic 8-axis joystick), drives every axis and button, and reads the
events back from the kernel to prove the round trip works. Run from the
repository root:

    python tests/test_uinput.py            # quick self-check
    python tests/test_uinput.py --hold 30  # keep both devices alive 30 s so
                                           # you can look at them in evtest,
                                           # jstest-gtk, or Steam's controller
                                           # settings

Reading events back needs read access to the created ``/dev/input/eventN``
node. systemd grants that to the active seat for joystick-class devices; if
it is missing the test still reports whether device creation succeeded.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import ControllerConfig  # noqa: E402
from src.uinput_interface import (  # noqa: E402
    UINPUT_AVAILABLE,
    UINPUT_DEVICE_PATH,
    UInputJoystickInterface,
    UInputXboxInterface,
    _INPUT_EVENT,
    EV_ABS,
    EV_KEY,
)


def _drain(fd: int, timeout: float = 0.3) -> list:
    """Read every pending input_event from ``fd`` as ``(type, code, value)``."""
    events = []
    while select.select([fd], [], [], timeout)[0]:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            break
        for off in range(0, len(data) - _INPUT_EVENT.size + 1, _INPUT_EVENT.size):
            _sec, _usec, ev_type, code, value = _INPUT_EVENT.unpack_from(data, off)
            if ev_type in (EV_ABS, EV_KEY):
                events.append((ev_type, code, value))
        timeout = 0.05
    return events


def _open_reader(node: str):
    if not node or not os.access(node, os.R_OK):
        return None
    return os.open(node, os.O_RDONLY | os.O_NONBLOCK)


def _check(label: str, ok: bool) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def test_xbox(config: ControllerConfig) -> tuple:
    print("\nXbox 360 gamepad")
    iface = UInputXboxInterface(config)
    if not _check("device created", iface.is_connected):
        return iface, False
    node = iface.device.event_node
    print(f"  node: {node or '(unresolved)'}")
    time.sleep(0.3)
    rfd = _open_reader(node)
    if rfd is None:
        print("  (no read access to the event node; skipping read-back checks)")
        return iface, True

    ok = True
    _drain(rfd, 0.1)
    iface.set_left_stick(1.0, 1.0)
    ev = _drain(rfd)
    ok &= _check("left stick right/up -> ABS_X=+32767, ABS_Y=-32767",
                 (EV_ABS, 0x00, 32767) in ev and (EV_ABS, 0x01, -32767) in ev)
    iface.set_right_stick(-1.0, -1.0)
    ev = _drain(rfd)
    ok &= _check("right stick left/down -> ABS_RX=-32767, ABS_RY=+32767",
                 (EV_ABS, 0x03, -32767) in ev and (EV_ABS, 0x04, 32767) in ev)
    iface.set_left_trigger(1.0)
    iface.set_right_trigger(0.5)
    ev = _drain(rfd)
    ok &= _check("triggers -> ABS_Z=255, ABS_RZ=128",
                 (EV_ABS, 0x02, 255) in ev and (EV_ABS, 0x05, 128) in ev)
    for bid in range(1, 11):
        iface.set_button(bid, True)
    ev = _drain(rfd)
    pressed = {c for t, c, v in ev if t == EV_KEY and v == 1}
    ok &= _check("buttons 1-10 press -> 10 distinct BTN_* codes", len(pressed) == 10)
    for bid in range(1, 11):
        iface.set_button(bid, False)
    ev = _drain(rfd)
    released = {c for t, c, v in ev if t == EV_KEY and v == 0}
    ok &= _check("buttons 1-10 release", released == pressed)
    iface.set_button(11, True)  # up
    iface.set_button(14, True)  # right
    ev = _drain(rfd)
    ok &= _check("D-pad up+right -> HAT0Y=-1, HAT0X=+1",
                 (EV_ABS, 0x11, -1) in ev and (EV_ABS, 0x10, 1) in ev)
    iface.set_button(11, False)
    iface.set_button(14, False)
    ev = _drain(rfd)
    ok &= _check("D-pad release -> hats back to 0",
                 (EV_ABS, 0x11, 0) in ev and (EV_ABS, 0x10, 0) in ev)
    iface.emergency_stop()
    _drain(rfd)
    os.close(rfd)
    return iface, ok


def test_joystick(config: ControllerConfig) -> tuple:
    print("\nGeneric joystick")
    iface = UInputJoystickInterface(config)
    if not _check("device created", iface.is_connected):
        return iface, False
    node = iface.device.event_node
    print(f"  node: {node or '(unresolved)'}")
    time.sleep(0.3)
    rfd = _open_reader(node)
    if rfd is None:
        print("  (no read access to the event node; skipping read-back checks)")
        return iface, True

    ok = True
    _drain(rfd, 0.1)
    top = iface.axis_range
    seen = {}
    for axis in ("x", "y", "z", "rx", "ry", "rz", "sl0", "sl1"):
        iface.update_axis(axis, 1.0)
        for t, c, v in _drain(rfd):
            if t == EV_ABS:
                seen[c] = v
    ok &= _check(f"all 8 axes reach max ({top})",
                 len(seen) == 8 and all(v == top for v in seen.values()))
    iface.update_axis("x", -1.0)
    ev = _drain(rfd)
    ok &= _check("x at -1.0 -> ABS_X=0", (EV_ABS, 0x00, 0) in ev)
    iface.update_axis("y", 0.0)
    ev = _drain(rfd)
    ok &= _check(f"y at 0.0 -> ABS_Y={top // 2}", (EV_ABS, 0x01, top // 2) in ev)
    codes = set()
    for bid in (1, 16, 17, 56):
        iface.set_button(bid, True)
        codes |= {c for t, c, v in _drain(rfd) if t == EV_KEY and v == 1}
    ok &= _check("buttons 1, 16, 17, 56 map to distinct codes", len(codes) == 4)
    for bid in (1, 16, 17, 56):
        iface.set_button(bid, False)
    _drain(rfd)
    ok &= _check("button 57 is rejected without raising", iface.set_button(57, True) is False)
    iface.emergency_stop()
    _drain(rfd)
    os.close(rfd)
    return iface, ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hold", type=float, default=0.0, metavar="SECONDS",
                        help="keep both devices alive this long before destroying them")
    args = parser.parse_args()

    print("Nimbus Adaptive Controller: Linux uinput diagnostic")
    print(f"  platform: {sys.platform}, uinput available: {UINPUT_AVAILABLE}")
    if not UINPUT_AVAILABLE:
        print("This diagnostic only runs on Linux.")
        return 1
    exists = os.path.exists(UINPUT_DEVICE_PATH)
    writable = os.access(UINPUT_DEVICE_PATH, os.W_OK)
    print(f"  {UINPUT_DEVICE_PATH}: exists={exists} writable={writable}")
    if not exists:
        print("  -> sudo modprobe uinput")
    elif not writable:
        print("  -> install build_tools/linux/60-nimbus-uinput.rules (see docs/setup/LINUX.md)")

    config = ControllerConfig()
    xbox, xbox_ok = test_xbox(config)
    joy, joy_ok = test_joystick(config)

    if args.hold > 0 and (xbox.is_connected or joy.is_connected):
        print(f"\nHolding devices for {args.hold:.0f} s (Ctrl+C to stop early)...")
        try:
            time.sleep(args.hold)
        except KeyboardInterrupt:
            pass

    xbox.shutdown()
    joy.shutdown()
    print()
    print("RESULT:", "PASS" if (xbox_ok and joy_ok) else "FAIL")
    return 0 if (xbox_ok and joy_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
