"""
Windows mouse isolation: talk to the Nimbus Mouse Filter kernel driver.

This is the Windows counterpart of :mod:`src.mouse_isolation` (the Linux
``EVIOCGRAB`` implementation), and it presents the **same** ``MouseIsolation``
class so :mod:`src.bridge` can use either behind the ``MOUSE_ISOLATION_AVAILABLE``
flag. Where Linux grabs the evdev node directly, here the kernel filter
(``driver/nimbus_moufilter``) withholds physical mouse packets from ``mouclass``
and hands them to this process through ``\\\\.\\NimbusMouseFilter``.

While isolation is on, Windows itself stops seeing the physical mouse (cursor,
Raw Input, and every application, including a game), so the bridge draws its own
cursor and synthesises Qt events from the deltas delivered here, exactly as on
Linux (see the F4 note in ``docs/vision/LINUX_PROBE_PLAN.md``).

Button codes reported to ``on_button`` match the Linux evdev codes
(``BTN_LEFT`` = 0x110, ...) so ``bridge._ISO_BUTTON_MAP`` is shared unchanged.

Safety
------
* The driver clears isolation when this process's handle closes (crash, kill,
  exit) and via its own 2 s read-inactivity watchdog.
* :func:`MouseIsolation.stop` is idempotent and registered with :mod:`atexit`.
* ``Ctrl+Alt+F12`` is handled by the existing hotkey thread in
  :mod:`src.mouse_hider`; the driver never touches the keyboard.

Requirements
------------
The Nimbus Mouse Filter driver installed and started (``driver/install-dev.ps1``
during development). If the device cannot be opened, :func:`MouseIsolation.start`
raises ``RuntimeError`` with a message that says the driver is missing, and the
bridge falls back to leaving isolation unavailable.
"""
from __future__ import annotations

import atexit
import struct
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

MOUSE_ISOLATION_AVAILABLE = sys.platform == "win32"

# Must match driver/nimbus_moufilter/nimbus_moufilter_ioctl.h
DEVICE_PATH = r"\\.\NimbusMouseFilter"
INTERFACE_VERSION = 1
IOCTL_NIMBUS_SET_ISOLATION = 0x00222000
IOCTL_NIMBUS_GET_STATUS = 0x00222004

# MOUSE_INPUT_DATA (ntddmou.h), x64 packing is natural with no padding here.
#   USHORT UnitId, Flags, ButtonFlags, ButtonData; ULONG RawButtons;
#   LONG LastX, LastY; ULONG ExtraInformation
_MOUSE_INPUT_DATA = struct.Struct("<HHHHIiiI")
assert _MOUSE_INPUT_DATA.size == 24

# MOUSE_INPUT_DATA.Flags
MOUSE_MOVE_RELATIVE = 0x0000
MOUSE_MOVE_ABSOLUTE = 0x0001

# MOUSE_INPUT_DATA.ButtonFlags
MOUSE_LEFT_BUTTON_DOWN = 0x0001
MOUSE_LEFT_BUTTON_UP = 0x0002
MOUSE_RIGHT_BUTTON_DOWN = 0x0004
MOUSE_RIGHT_BUTTON_UP = 0x0008
MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
MOUSE_MIDDLE_BUTTON_UP = 0x0020
MOUSE_BUTTON_4_DOWN = 0x0040
MOUSE_BUTTON_4_UP = 0x0080
MOUSE_BUTTON_5_DOWN = 0x0100
MOUSE_BUTTON_5_UP = 0x0200
MOUSE_WHEEL = 0x0400
MOUSE_HWHEEL = 0x0800

# evdev button codes, matching src/mouse_isolation.py and bridge._ISO_BUTTON_MAP
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

_BUTTON_EDGES = (
    (MOUSE_LEFT_BUTTON_DOWN, BTN_LEFT, True), (MOUSE_LEFT_BUTTON_UP, BTN_LEFT, False),
    (MOUSE_RIGHT_BUTTON_DOWN, BTN_RIGHT, True), (MOUSE_RIGHT_BUTTON_UP, BTN_RIGHT, False),
    (MOUSE_MIDDLE_BUTTON_DOWN, BTN_MIDDLE, True), (MOUSE_MIDDLE_BUTTON_UP, BTN_MIDDLE, False),
    (MOUSE_BUTTON_4_DOWN, BTN_SIDE, True), (MOUSE_BUTTON_4_UP, BTN_SIDE, False),
    (MOUSE_BUTTON_5_DOWN, BTN_EXTRA, True), (MOUSE_BUTTON_5_UP, BTN_EXTRA, False),
)

WHEEL_DELTA = 120

if MOUSE_ISOLATION_AVAILABLE:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    ERROR_IO_PENDING = 997
    ERROR_OPERATION_ABORTED = 995
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 0x102
    INFINITE = 0xFFFFFFFF

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.DeviceIoControl.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL


def _open_device() -> int:
    handle = _k32.CreateFileW(DEVICE_PATH, GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                              OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        err = ctypes.get_last_error()
        if err in (2, 3):  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
            raise RuntimeError("Nimbus Mouse Filter driver is not installed or not started "
                               "(run driver/install-dev.ps1 from an elevated prompt)")
        if err == 32:  # ERROR_SHARING_VIOLATION
            raise RuntimeError("another process already holds the Nimbus Mouse Filter open")
        raise RuntimeError(f"could not open {DEVICE_PATH}: Windows error {err}")
    return handle


def get_status() -> Dict[str, int]:
    """Open the control device, read its status struct, and close it.

    Raises ``RuntimeError`` if the driver is not reachable.
    """
    handle = _open_device()
    try:
        buf = ctypes.create_string_buffer(32)  # NIMBUS_MOUFILTER_STATUS is 8 ULONGs
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(handle, IOCTL_NIMBUS_GET_STATUS, None, 0,
                                  buf, ctypes.sizeof(buf), ctypes.byref(returned), None)
        if not ok:
            raise RuntimeError(f"IOCTL_NIMBUS_GET_STATUS failed: Windows error {ctypes.get_last_error()}")
        fields = ("version", "isolating", "connected_mice", "pending_reads",
                  "packets_captured", "packets_dropped", "packets_passed", "watchdog_releases")
        values = struct.unpack("<8I", buf.raw[:32])
        return dict(zip(fields, values))
    finally:
        _k32.CloseHandle(handle)


def is_available() -> bool:
    """True if the driver's control device can be opened right now."""
    if not MOUSE_ISOLATION_AVAILABLE:
        return False
    try:
        handle = _open_device()
    except RuntimeError:
        return False
    _k32.CloseHandle(handle)
    return True


def list_pointer_devices() -> List[Dict[str, Any]]:
    """Report the driver as a single grabbable 'device', to match the Linux API.

    The Windows filter is class-wide, so there are no per-node choices to make.
    Returns an empty list when the driver is unreachable.
    """
    try:
        status = get_status()
    except RuntimeError:
        return []
    return [{
        "name": "Nimbus Mouse Filter (all mice)",
        "node": DEVICE_PATH,
        "readable": True,
        "is_keyboard": False,
        "connected_mice": status.get("connected_mice", 0),
    }]


class MouseIsolation:
    """Isolate the physical mouse through the Nimbus Mouse Filter driver.

    Same constructor and lifecycle as :class:`src.mouse_isolation.MouseIsolation`
    on Linux. Callbacks run on the reader thread; marshal to the UI thread
    before touching Qt objects (the bridge does this with queued signals).

    Args:
        on_motion: ``(dx, dy)`` per input report with movement.
        on_button: ``(code, pressed)`` for mouse buttons (evdev codes).
        on_wheel: ``(horizontal, vertical)`` wheel notches.
        on_stopped: ``(reason)`` when isolation ends for any reason.
        hotkey: Accepted for API parity; the Ctrl+Alt+F12 hotkey lives in
            :mod:`src.mouse_hider` on Windows, so this class does not install one.
    """

    def __init__(
        self,
        on_motion: Callable[[int, int], None],
        on_button: Callable[[int, bool], None],
        on_wheel: Optional[Callable[[int, int], None]] = None,
        on_stopped: Optional[Callable[[str], None]] = None,
        hotkey: bool = True,
    ) -> None:
        self._on_motion = on_motion
        self._on_button = on_button
        self._on_wheel = on_wheel
        self._on_stopped = on_stopped
        self._lock = threading.Lock()
        self._active = False
        self._handle: Optional[int] = None
        self._read_event: Optional[int] = None
        self._stop_event: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self.stop_reason = ""

    @property
    def active(self) -> bool:
        return self._active

    @property
    def grabbed_devices(self) -> List[Dict[str, Any]]:
        return list_pointer_devices() if self._active else []

    def _set_isolation(self, enable: bool) -> None:
        value = wintypes.DWORD(1 if enable else 0)
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(self._handle, IOCTL_NIMBUS_SET_ISOLATION,
                                  ctypes.byref(value), ctypes.sizeof(value),
                                  None, 0, ctypes.byref(returned), None)
        if not ok:
            raise RuntimeError(f"IOCTL_NIMBUS_SET_ISOLATION failed: Windows error {ctypes.get_last_error()}")

    def start(self, nodes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Open the driver and turn isolation on. ``nodes`` is ignored (class-wide).

        Returns the grabbed 'devices'. Raises ``RuntimeError`` if the driver is
        missing or already in use.
        """
        if not MOUSE_ISOLATION_AVAILABLE:
            raise RuntimeError("mouse isolation is Windows-only in this module")
        with self._lock:
            if self._active:
                return self.grabbed_devices
            self._handle = _open_device()
            self._read_event = _k32.CreateEventW(None, True, False, None)
            self._stop_event = _k32.CreateEventW(None, True, False, None)
            try:
                self._set_isolation(True)
            except Exception:
                self._close_handles()
                raise
            self._active = True
            self.stop_reason = ""
        _register_instance(self)
        self._thread = threading.Thread(target=self._reader, daemon=True, name="MouseIsolationWin")
        self._thread.start()
        print("[mouse_isolation_win] isolation on (Ctrl+Alt+F12 to release)")
        return self.grabbed_devices

    def stop(self, reason: str = "requested") -> None:
        """Turn isolation off, stop the reader, and close the driver handle."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stop_reason = reason
        if self._handle is not None:
            try:
                self._set_isolation(False)
            except Exception:
                pass
            _k32.CancelIoEx(self._handle, None)
        if self._stop_event is not None:
            _k32.SetEvent(self._stop_event) if hasattr(_k32, "SetEvent") else None
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.0)
        self._close_handles()
        _unregister_instance(self)
        print(f"[mouse_isolation_win] released ({reason})")
        if self._on_stopped:
            try:
                self._on_stopped(reason)
            except Exception as exc:
                print(f"[mouse_isolation_win] on_stopped error: {exc}")

    def _close_handles(self) -> None:
        for attr in ("_handle", "_read_event", "_stop_event"):
            h = getattr(self, attr)
            if h:
                _k32.CloseHandle(h)
            setattr(self, attr, None)

    def _reader(self) -> None:
        reason = "reader exited"
        # 256 packets per read keeps a burst in one syscall (256 * 24 bytes).
        buf = ctypes.create_string_buffer(256 * _MOUSE_INPUT_DATA.size)
        ov = _OVERLAPPED()
        ov.hEvent = self._read_event
        try:
            while self._active:
                returned = wintypes.DWORD(0)
                ok = _k32.ReadFile(self._handle, buf, ctypes.sizeof(buf), ctypes.byref(returned), ctypes.byref(ov))
                if not ok:
                    err = ctypes.get_last_error()
                    if err == ERROR_IO_PENDING:
                        if not _k32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(returned), True):
                            gerr = ctypes.get_last_error()
                            if gerr == ERROR_OPERATION_ABORTED:
                                reason = "cancelled"
                                break
                            reason = f"read error {gerr}"
                            break
                    elif err == ERROR_OPERATION_ABORTED:
                        reason = "cancelled"
                        break
                    else:
                        reason = f"read error {err}"
                        break
                n = returned.value
                if n:
                    self._dispatch(buf.raw[:n])
        except Exception as exc:
            reason = f"error: {exc}"
        if self._active:
            threading.Thread(target=self.stop, args=(reason,), daemon=True).start()

    def _dispatch(self, data: bytes) -> None:
        count = len(data) // _MOUSE_INPUT_DATA.size
        for i in range(count):
            (_unit, _flags, button_flags, button_data, _raw,
             last_x, last_y, _extra) = _MOUSE_INPUT_DATA.unpack_from(data, i * _MOUSE_INPUT_DATA.size)
            if last_x or last_y:
                # The driver reports whatever the mouse sent; mice send relative.
                self._on_motion(last_x, last_y)
            if button_flags:
                for mask, code, pressed in _BUTTON_EDGES:
                    if button_flags & mask:
                        self._on_button(code, pressed)
                if self._on_wheel and (button_flags & (MOUSE_WHEEL | MOUSE_HWHEEL)):
                    notch = _signed16(button_data) // WHEEL_DELTA
                    if button_flags & MOUSE_WHEEL:
                        self._on_wheel(0, notch)
                    if button_flags & MOUSE_HWHEEL:
                        self._on_wheel(notch, 0)


def _signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


# ---- process-wide safety net (mirrors src/mouse_isolation.py) -------------
_instances: List[MouseIsolation] = []
_instances_lock = threading.Lock()


def _register_instance(inst: MouseIsolation) -> None:
    with _instances_lock:
        if inst not in _instances:
            _instances.append(inst)


def _unregister_instance(inst: MouseIsolation) -> None:
    with _instances_lock:
        if inst in _instances:
            _instances.remove(inst)


def stop_all(reason: str = "shutdown") -> None:
    """Release every active isolation (also runs at interpreter exit)."""
    with _instances_lock:
        pending = list(_instances)
    for inst in pending:
        try:
            inst.stop(reason)
        except Exception:
            pass


atexit.register(stop_all, "atexit")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Nimbus Mouse Filter diagnostics")
    parser.add_argument("--status", action="store_true", help="print the driver status and exit")
    parser.add_argument("--grab", type=float, metavar="SECONDS",
                        help="isolate the mouse for N seconds, printing deltas (mouse is captured)")
    args = parser.parse_args()

    if not MOUSE_ISOLATION_AVAILABLE:
        raise SystemExit("Windows only")

    if args.status or not args.grab:
        try:
            st = get_status()
        except RuntimeError as exc:
            raise SystemExit(str(exc))
        print(f"Nimbus Mouse Filter (interface v{st['version']}, expected v{INTERFACE_VERSION})")
        for key in ("isolating", "connected_mice", "pending_reads", "packets_captured",
                    "packets_dropped", "packets_passed", "watchdog_releases"):
            print(f"  {key:<18} {st[key]}")
        if not args.grab:
            raise SystemExit(0)

    import time

    totals = {"dx": 0, "dy": 0, "buttons": 0}

    def on_motion(dx, dy):
        totals["dx"] += dx
        totals["dy"] += dy

    def on_button(code, pressed):
        totals["buttons"] += 1
        print(f"  button 0x{code:x} {'down' if pressed else 'up'}")

    iso = MouseIsolation(on_motion, on_button)
    iso.start()
    print(f"isolating for {args.grab}s; the desktop cursor should be frozen")
    try:
        time.sleep(args.grab)
    finally:
        iso.stop("cli done")
    print(f"summed motion dx={totals['dx']} dy={totals['dy']} button edges={totals['buttons']}")
