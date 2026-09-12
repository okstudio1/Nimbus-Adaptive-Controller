"""
Protocol checks for ``src/padbus_client.py``, the pure-Python client for the
virtual gamepad bus behind Xbox 360 emulation.

Pure Python: no driver, no hardware, and the module imports on any OS. Every
constant the driver validates is checked here, against values confirmed on a
real ViGEmBus (1.21.442.0, 2026-09-09) by ``tests/probe_padbus_windows.py``:

- the IOCTL codes derived from ``CTL_CODE`` are the ones the bus dispatches on
- the request structures pack to the sizes the driver checks ``Size`` against
- ``XUSB_REPORT`` is ``XINPUT_GAMEPAD`` byte for byte
- the float conversions are vgamepad's (``round(x * 32767)``, ``round(t * 255)``,
  clamped at the type limits), so the shaping probe's expected values hold
- press and release touch only their bit, and the 14 Nimbus button ids map to
  distinct single-bit masks equal to XInput's
- GUID packing matches ``uuid.UUID.bytes_le``
- graceful degradation: ``PADBUS_AVAILABLE`` is a bool, ``find_bus`` never
  raises, and a pad on a bus that does not exist fails with ``PadBusNotFound``

Run (from the repo root)::

    venv\\Scripts\\python -m tests.test_padbus_client
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src import padbus_client as pb  # noqa: E402
from src.vigem_interface import XUSB_BY_ID  # noqa: E402

FAILS = 0
PASSES = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS, PASSES
    if cond:
        PASSES += 1
    else:
        FAILS += 1
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  ({detail})" if detail else ""))


def main() -> int:
    print("Pad bus client protocol checks")

    # IOCTL codes: CTL_CODE(FILE_DEVICE_BUS_EXTENDER, 0x801 + n, METHOD_BUFFERED, access)
    expected_codes = {
        "IOCTL_VIGEM_PLUGIN_TARGET": 0x002AA004,
        "IOCTL_VIGEM_UNPLUG_TARGET": 0x002AA008,
        "IOCTL_VIGEM_CHECK_VERSION": 0x002AA00C,
        "IOCTL_VIGEM_WAIT_DEVICE_READY": 0x002AA010,
        "IOCTL_XUSB_REQUEST_NOTIFICATION": 0x002AE804,
        "IOCTL_XUSB_SUBMIT_REPORT": 0x002AA808,
        "IOCTL_XUSB_GET_USER_INDEX": 0x002AE81C,
    }
    for name, value in expected_codes.items():
        got = getattr(pb, name)
        check(f"{name} = 0x{value:08X}", got == value, f"got 0x{got:08X}")
    check("ctl_code is the WDK macro", pb.ctl_code(0x801, 2) == (0x2A << 16) | (2 << 14) | (0x801 << 2))
    check("interface version is 1", pb.INTERFACE_VERSION == 0x0001)

    # Structure sizes, which the driver compares Size against
    expected_sizes = {
        "VIGEM_CHECK_VERSION": 8, "VIGEM_PLUGIN_TARGET": 16, "VIGEM_UNPLUG_TARGET": 8,
        "VIGEM_WAIT_DEVICE_READY": 8, "XUSB_REPORT": 12, "XUSB_SUBMIT_REPORT": 20,
        "XUSB_REQUEST_NOTIFICATION": 12, "XUSB_GET_USER_INDEX": 12,
    }
    for name, size in expected_sizes.items():
        got = ctypes.sizeof(getattr(pb, name))
        check(f"sizeof({name}) = {size}", got == size, f"got {got}")

    # XUSB_REPORT is XINPUT_GAMEPAD byte for byte
    r = pb.XUSB_REPORT(wButtons=0x5010, bLeftTrigger=7, bRightTrigger=255,
                       sThumbLX=-32768, sThumbLY=32767, sThumbRX=-1, sThumbRY=1234)
    check("XUSB_REPORT packs like XINPUT_GAMEPAD",
          bytes(r) == struct.pack("<HBBhhhh", 0x5010, 7, 255, -32768, 32767, -1, 1234))
    sub = pb.XUSB_SUBMIT_REPORT(Size=20, SerialNo=3)
    sub.Report = r
    check("XUSB_SUBMIT_REPORT carries the report after Size and SerialNo",
          bytes(sub) == struct.pack("<II", 20, 3) + bytes(r))
    plug = pb.VIGEM_PLUGIN_TARGET(Size=16, SerialNo=1, TargetType=pb.TARGET_XBOX360_WIRED,
                                  VendorId=pb.XBOX360_VENDOR_ID, ProductId=pb.XBOX360_PRODUCT_ID)
    check("VIGEM_PLUGIN_TARGET packs Xbox360Wired with Microsoft's VID/PID",
          bytes(plug) == struct.pack("<IIIHH", 16, 1, 0, 0x045E, 0x028E))

    # Float conversions: vgamepad's, so the shaping probe's expected values hold
    state = pb.X360ReportState()
    for x in (-1.0, -0.95, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 0.95, 1.0):
        state.left_joystick_float(x, -x)
        state.right_joystick_float(-x, x)
        ok = (state.report.sThumbLX == round(x * 32767) and state.report.sThumbLY == round(-x * 32767)
              and state.report.sThumbRX == round(-x * 32767) and state.report.sThumbRY == round(x * 32767))
        check(f"stick {x:+.2f} -> round(x * 32767) = {round(x * 32767)}", ok,
              f"LX={state.report.sThumbLX} LY={state.report.sThumbLY}")
    for t in (0.0, 0.1, 0.5, 0.95, 1.0):
        state.left_trigger_float(t)
        state.right_trigger_float(1.0 - t)
        check(f"trigger {t:.2f} -> round(t * 255) = {round(t * 255)}",
              state.report.bLeftTrigger == round(t * 255) and state.report.bRightTrigger == round((1.0 - t) * 255),
              f"LT={state.report.bLeftTrigger} RT={state.report.bRightTrigger}")
    state.left_joystick_float(2.0, -2.0)
    check("over-range sticks clamp at the int16 limits",
          state.report.sThumbLX == 32767 and state.report.sThumbLY == -32768)
    state.left_trigger_float(1.5)
    state.right_trigger_float(-0.5)
    check("over-range triggers clamp at 0 and 255",
          state.report.bLeftTrigger == 255 and state.report.bRightTrigger == 0)

    # Buttons
    state.reset()
    check("reset zeroes the report", bytes(state.report) == bytes(12))
    A, X, Y = pb.XUSB_BUTTON.XUSB_GAMEPAD_A, pb.XUSB_BUTTON.XUSB_GAMEPAD_X, pb.XUSB_BUTTON.XUSB_GAMEPAD_Y
    state.press_button(A)
    state.press_button(X)
    check("press sets only its bits", state.report.wButtons == 0x5000, hex(state.report.wButtons))
    state.release_button(A)
    check("release clears only its bit", state.report.wButtons == 0x4000, hex(state.report.wButtons))
    state.release_button(Y)
    check("releasing an unpressed button changes nothing", state.report.wButtons == 0x4000)
    state.press_button(A | Y)
    check("a combined mask presses both", state.report.wButtons == 0xD000, hex(state.report.wButtons))
    xinput_masks = {
        "XUSB_GAMEPAD_DPAD_UP": 0x0001, "XUSB_GAMEPAD_DPAD_DOWN": 0x0002, "XUSB_GAMEPAD_DPAD_LEFT": 0x0004,
        "XUSB_GAMEPAD_DPAD_RIGHT": 0x0008, "XUSB_GAMEPAD_START": 0x0010, "XUSB_GAMEPAD_BACK": 0x0020,
        "XUSB_GAMEPAD_LEFT_THUMB": 0x0040, "XUSB_GAMEPAD_RIGHT_THUMB": 0x0080,
        "XUSB_GAMEPAD_LEFT_SHOULDER": 0x0100, "XUSB_GAMEPAD_RIGHT_SHOULDER": 0x0200,
        "XUSB_GAMEPAD_GUIDE": 0x0400, "XUSB_GAMEPAD_A": 0x1000, "XUSB_GAMEPAD_B": 0x2000,
        "XUSB_GAMEPAD_X": 0x4000, "XUSB_GAMEPAD_Y": 0x8000,
    }
    check("XUSB_BUTTON has XInput's masks", all(int(pb.XUSB_BUTTON[k]) == v for k, v in xinput_masks.items()))

    # The app's 14 ids
    expected_ids = {1: 0x1000, 2: 0x2000, 3: 0x4000, 4: 0x8000, 5: 0x0100, 6: 0x0200, 7: 0x0020, 8: 0x0010,
                    9: 0x0040, 10: 0x0080, 11: 0x0001, 12: 0x0002, 13: 0x0004, 14: 0x0008}
    check("XUSB_BY_ID covers ids 1 to 14", sorted(XUSB_BY_ID) == list(range(1, 15)))
    check("XUSB_BY_ID maps each id to the same mask as before",
          {k: int(v) for k, v in XUSB_BY_ID.items()} == expected_ids)
    check("XUSB_BY_ID masks are distinct single bits",
          len({int(v) for v in XUSB_BY_ID.values()}) == 14 and all(bin(int(v)).count("1") == 1 for v in XUSB_BY_ID.values()))

    # GUID packing
    vigem = pb.BUS_INTERFACES[0][1]
    check("GUID.from_string packs like uuid.bytes_le",
          bytes(pb.GUID.from_string(vigem)) == uuid.UUID(vigem).bytes_le)
    check("ViGEmBus is the first bus probed and its GUID is the published one",
          pb.BUS_INTERFACES[0][0] == "ViGEmBus" and vigem.upper() == "{96E42B22-F5E9-42F8-B043-ED0F932F014F}")

    # Graceful degradation
    check("PADBUS_AVAILABLE is a bool", isinstance(pb.PADBUS_AVAILABLE, bool), str(pb.PADBUS_AVAILABLE))
    bogus = [("nothing", "{00000000-0000-0000-0000-000000000001}")]
    check("find_bus on an absent interface is None without raising", pb.find_bus(bogus) is None)
    check("interface_paths on an absent interface is empty", pb.interface_paths(bogus[0][1]) == [])
    try:
        pb.X360Pad(interfaces=bogus)
        check("X360Pad on an absent bus raises PadBusNotFound", False, "no exception")
    except pb.PadBusNotFound:
        check("X360Pad on an absent bus raises PadBusNotFound", True)
    except Exception as exc:  # pragma: no cover
        check("X360Pad on an absent bus raises PadBusNotFound", False, repr(exc))

    print(f"\n{PASSES} passed, {FAILS} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
