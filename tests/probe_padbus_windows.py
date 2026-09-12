"""
Probe: the pure-Python bus client (``src/padbus_client.py``) against the real
ViGEmBus, read back through XInput the way a game reads it.

Phase 0 of ``docs/vision/PAD_BUS_FORK_PLAN.md``. Twelve numbered checks,
unattended, about 30 seconds, no game, safe over TeamViewer. Nothing here
touches the mouse or keyboard.

  P1  a bus device interface is present, opens, and CHECK_VERSION agrees
  P2  X360Pad() plugs in and the ready wait returns (time recorded)
  P3  XInput sees the pad: a signature report shows up at some user index,
      every field equal to what was submitted
  P4  user_index(): agrees with the XInput slot, or is None (reported, not
      failed: ViGEmBus 1.21 accepts the query and writes nothing back)
  P5  reset() + update() centres everything as XInput sees it
  P6  unplug(): the XInput slot disconnects (time recorded)
  P7  handle death: a child process plugs a pad, deflects a stick and is
      killed; the pad is gone from XInput (time recorded). This is the
      failsafe the plan relies on: no handle, no pad
  P8  two pads from one process take two serials and two XInput slots
  P9  replug storm: 20 x (plug, update, close) all succeed (times recorded)
  P10 update() timing over 2000 reports (median and p99, microseconds)
  P11 notifications: how many queued output reports the bus hands over on a
      fresh pad, whether the call pends once drained, and that XInputSetState
      rumble arrives scaled to a byte
  P12 the app path: ViGEmInterface connects through the client and
      set_left_stick(0.5, -0.25) reads back as round(x * 32767)

Run (from the repo root, ViGEmBus installed)::

    venv\\Scripts\\python tests\\probe_padbus_windows.py

The results log is PAD_BUS_FORK_PLAN.md, section 17.
"""
from __future__ import annotations

import ctypes
import os
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("Windows only")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src import padbus_client as pb  # noqa: E402

ERROR_DEVICE_NOT_CONNECTED = 1167
RESULTS: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {note}" if note else ""), flush=True)


# ---- XInput, the way a game reads the pad ----------------------------------------
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [("wLeftMotorSpeed", wintypes.WORD), ("wRightMotorSpeed", wintypes.WORD)]


xinput = ctypes.WinDLL("xinput1_4")
xinput.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
xinput.XInputGetState.restype = wintypes.DWORD
xinput.XInputSetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_VIBRATION)]
xinput.XInputSetState.restype = wintypes.DWORD

Fields = Tuple[int, int, int, int, int, int, int]


def xinput_states() -> Dict[int, Fields]:
    """Connected slots as {index: (buttons, lt, rt, lx, ly, rx, ry)}."""
    out: Dict[int, Fields] = {}
    for i in range(4):
        st = XINPUT_STATE()
        if xinput.XInputGetState(i, ctypes.byref(st)) == 0:
            g = st.Gamepad
            out[i] = (g.wButtons, g.bLeftTrigger, g.bRightTrigger, g.sThumbLX, g.sThumbLY, g.sThumbRX, g.sThumbRY)
    return out


def wait_for(predicate, timeout_s: float, step_s: float = 0.02):
    """Poll until predicate() is truthy; returns (value, seconds) or (None, timeout)."""
    t0 = time.perf_counter()
    while True:
        v = predicate()
        if v:
            return v, time.perf_counter() - t0
        if time.perf_counter() - t0 > timeout_s:
            return None, time.perf_counter() - t0
        time.sleep(step_s)


def slot_with(signature: Fields) -> Optional[int]:
    for i, f in xinput_states().items():
        if f == signature:
            return i
    return None


def slot_gone(index: int) -> bool:
    st = XINPUT_STATE()
    return xinput.XInputGetState(index, ctypes.byref(st)) == ERROR_DEVICE_NOT_CONNECTED


def driver_version(path: str) -> str:
    try:
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return "unknown"
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buf):
            return "unknown"
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return "unknown"
        ffi = (ctypes.c_uint32 * (length.value // 4)).from_address(ptr.value)
        ms, ls = ffi[2], ffi[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return "unknown"


def main() -> int:
    print("Pad bus client probe against the real bus")
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    print(f"  ViGEmBus.sys {driver_version(os.path.join(sysroot, 'System32', 'drivers', 'ViGEmBus.sys'))}, "
          f"python {sys.version.split()[0]}")
    others = xinput_states()
    if others:
        print(f"  note: XInput slots already connected before the probe: {sorted(others)}")

    # P1 bus present and speaks our version
    found = pb.find_bus()
    if not found:
        record("P1 bus present", False, "no bus device interface; is ViGEmBus installed and started?")
        return summary()
    try:
        bus = pb.open_bus()
        record("P1 bus present and CHECK_VERSION agrees", True, f"{bus.name} at {bus.path}")
        bus.close()
    except pb.PadBusError as exc:
        record("P1 bus present and CHECK_VERSION agrees", False, str(exc))
        return summary()

    # P2 plug
    t0 = time.perf_counter()
    try:
        pad = pb.X360Pad()
    except pb.PadBusError as exc:
        record("P2 plug", False, str(exc))
        return summary()
    plug_s = time.perf_counter() - t0
    record("P2 X360Pad() plugs in and the ready wait returns", pad.plugged and pad.serial > 0,
           f"serial {pad.serial} in {plug_s * 1000:.0f} ms, zombies skipped {pad.skipped_serials}")

    # P3 signature through XInput
    pad.left_joystick_float(12345 / 32767, -23456 / 32767)
    pad.right_joystick_float(3000 / 32767, -3000 / 32767)
    pad.left_trigger_float(77 / 255)
    pad.right_trigger_float(200 / 255)
    pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_A | pb.XUSB_BUTTON.XUSB_GAMEPAD_X)
    pad.update()
    signature: Fields = (0x5000, 77, 200, 12345, -23456, 3000, -3000)
    slot, dt = wait_for(lambda: (slot_with(signature) is not None) and (slot_with(signature),), 3.0)
    index = slot[0] if slot else None
    record("P3 XInput sees the submitted report field for field", index is not None,
           f"slot {index} after {dt * 1000:.0f} ms" if index is not None else f"not seen; slots={xinput_states()}")

    # P4 user index
    ui = pad.user_index()
    if ui is None:
        record("P4 user_index()", True, "None: this bus does not answer the query (expected on ViGEmBus 1.21)")
    else:
        record("P4 user_index() agrees with XInput", ui == index, f"user_index {ui}, XInput slot {index}")

    # P5 reset
    pad.reset()
    pad.update()
    if index is not None:
        centred, dt = wait_for(lambda: xinput_states().get(index) == (0, 0, 0, 0, 0, 0, 0), 2.0)
        record("P5 reset() + update() centres the pad in XInput", bool(centred), f"{dt * 1000:.0f} ms")
    else:
        record("P5 reset() + update() centres the pad in XInput", False, "no slot to read")

    # P6 unplug
    pad.unplug()
    if index is not None:
        gone, dt = wait_for(lambda: slot_gone(index), 3.0)
        record("P6 unplug() removes the pad from XInput", bool(gone), f"{dt * 1000:.0f} ms")
    else:
        record("P6 unplug() removes the pad from XInput", False, "no slot to watch")
    pad.close()

    # P7 handle death in another process
    child_code = (
        "import sys, time\n"
        f"sys.path.insert(0, {REPO!r})\n"
        "from src.padbus_client import X360Pad\n"
        "p = X360Pad(); p.left_joystick_float(0.75, 0.0); p.right_joystick_float(0.0, -0.75); p.update()\n"
        "print('READY', p.serial, 'skipped', p.skipped_serials, 'recovered', p.recovered_reports, "
        "'replugs', p.replugs, flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code], cwd=REPO, stdout=subprocess.PIPE, text=True)
    line = child.stdout.readline().strip() if child.stdout else ""
    child_sig: Fields = (0, 0, 0, 24575, 0, 0, -24575)
    cslot, dt = wait_for(lambda: (slot_with(child_sig) is not None) and (slot_with(child_sig),), 3.0)
    cindex = cslot[0] if cslot else None
    if cindex is None:
        record("P7 handle death unplugs the pad", False, f"child said {line!r}; its pad never showed in XInput")
        child.kill()
    else:
        child.kill()
        child.wait(timeout=5)
        gone, dt = wait_for(lambda: slot_gone(cindex), 5.0)
        record("P7 handle death unplugs the pad", bool(gone),
               f"child {line!r} at slot {cindex}; gone {dt * 1000:.0f} ms after kill")

    # P8 two pads at once
    try:
        a = pb.X360Pad()
        b = pb.X360Pad()
        a.left_joystick_float(0.25, 0.0)
        a.update()
        b.left_joystick_float(-0.25, 0.0)
        b.update()
        sa: Fields = (0, 0, 0, 8192, 0, 0, 0)
        sb: Fields = (0, 0, 0, -8192, 0, 0, 0)
        both, dt = wait_for(lambda: slot_with(sa) is not None and slot_with(sb) is not None, 3.0)
        record("P8 two pads from one process get two serials and two XInput slots",
               bool(both) and a.serial != b.serial,
               f"serials {a.serial} and {b.serial}, slots {slot_with(sa)} and {slot_with(sb)}, "
               f"zombies skipped {a.skipped_serials + b.skipped_serials}")
        a.close()
        b.close()
    except pb.PadBusError as exc:
        record("P8 two pads from one process get two serials and two XInput slots", False, str(exc))

    # P9 replug storm
    times: List[float] = []
    failures = 0
    zombies: List[int] = []
    serials: List[int] = []
    recovered = 0
    replugs: List[Tuple[int, int]] = []
    for _ in range(20):
        t0 = time.perf_counter()
        try:
            p = pb.X360Pad()
            zombies += p.skipped_serials
            p.left_joystick_float(0.5, 0.5)
            p.update()
            serials.append(p.serial)
            recovered += p.recovered_reports
            replugs += p.replugs
            p.close()
        except pb.PadBusError as exc:
            failures += 1
            print(f"    storm failure: {exc}")
        times.append(time.perf_counter() - t0)
    record("P9 replug storm: 20 x (plug, update, close)", failures == 0,
           f"{failures} failures; mean {statistics.mean(times) * 1000:.0f} ms, max {max(times) * 1000:.0f} ms; "
           f"skipped serials {zombies}; reports recovered by retry {recovered}; re-plugs {replugs}; "
           f"serials used {serials}")
    settled, dt = wait_for(lambda: len(xinput_states()) == len(others), 5.0)
    record("P9b every storm pad is gone from XInput afterwards", bool(settled), f"{dt * 1000:.0f} ms")

    # P10 update timing
    pad = pb.X360Pad()
    laps: List[float] = []
    for i in range(2000):
        pad.left_joystick_float((i % 100) / 100.0, 0.0)
        t0 = time.perf_counter()
        pad.update()
        laps.append(time.perf_counter() - t0)
    laps.sort()
    med = laps[len(laps) // 2] * 1e6
    p99 = laps[int(len(laps) * 0.99)] * 1e6
    record("P10 update() timing over 2000 reports", med < 1000 and p99 < 5000,
           f"median {med:.0f} us, p99 {p99:.0f} us, max {laps[-1] * 1e6:.0f} us")

    # P11 notifications
    pad.reset()
    pad.update()
    sig0: Fields = (0, 0, 0, 0, 0, 0, 0)
    myslot, _ = wait_for(lambda: (slot_with(sig0) is not None) and (slot_with(sig0),), 3.0)
    myindex = myslot[0] if myslot else None
    queued = 0
    pended = False
    for _ in range(50):
        n = pad.request_notification(timeout_ms=150)
        if n is None:
            pended = True
            break
        queued += 1
    print(f"  note: fresh pad handed over {queued} queued output reports; "
          f"{'then pended' if pended else 'never pended in 50 reads'}")
    if myindex is None:
        record("P11 rumble from XInputSetState arrives in request_notification", False, "no slot to rumble")
    else:
        xinput.XInputSetState(myindex, XINPUT_VIBRATION(30000, 10000))
        got = None
        for _ in range(10):
            n = pad.request_notification(timeout_ms=500)
            if n and n[0] == 117 and n[1] == 39:
                got = n
                break
        xinput.XInputSetState(myindex, XINPUT_VIBRATION(0, 0))
        record("P11 rumble from XInputSetState arrives in request_notification", got is not None,
               f"{got} for 30000/10000 (expected 117/39 scaled to a byte)")
    pad.close()

    # P12 the app path
    try:
        from src.config import ControllerConfig
        from src.vigem_interface import ViGEmInterface
        iface = ViGEmInterface(ControllerConfig())
        ok = iface.is_connected and iface.set_left_stick(0.5, -0.25) and iface.set_button(1, True)
        app_sig: Fields = (0x1000, 0, 0, round(0.5 * 32767), round(-0.25 * 32767), 0, 0)
        seen, dt = wait_for(lambda: slot_with(app_sig) is not None, 3.0)
        record("P12 ViGEmInterface drives the pad through the client", bool(ok and seen),
               f"status bus={iface.get_status().get('bus')} serial={iface.get_status().get('serial')}, "
               f"XInput matched in {dt * 1000:.0f} ms")
        iface.shutdown()
        gone, dt = wait_for(lambda: len(xinput_states()) == len(others), 3.0)
        record("P12b shutdown() unplugs it", bool(gone), f"{dt * 1000:.0f} ms")
    except Exception as exc:
        record("P12 ViGEmInterface drives the pad through the client", False, repr(exc))

    return summary()


def summary() -> int:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    for name, ok, note in RESULTS:
        if not ok:
            print(f"  FAILED {name}: {note}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
