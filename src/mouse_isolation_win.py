"""
Windows mouse isolation: talk to the Nimbus Mouse Filter kernel driver.

This is the Windows counterpart of the Linux ``EVIOCGRAB`` module
(``src/mouse_isolation.py`` on the ``linux-uinput-support`` branch, not yet
merged into ``main``), and it presents the **same** ``MouseIsolation`` class so
that, once that branch lands, :mod:`src.bridge` can use either behind a
``MOUSE_ISOLATION_AVAILABLE`` flag. Nothing on ``main`` imports this module
yet. Where Linux grabs the evdev node directly, here the kernel filter
(``driver/nimbus_moufilter``) withholds physical mouse packets from ``mouclass``
and hands them to this process through ``\\\\.\\NimbusMouseFilter``.

While isolation is on, Windows itself stops seeing the physical mouse (cursor,
Raw Input, and every application, including a game), so the bridge draws its own
cursor and synthesises Qt events from the deltas delivered here, exactly as on
Linux (see the F4 note in ``docs/vision/LINUX_PROBE_PLAN.md``).

Button codes reported to ``on_button`` are the Linux evdev codes
(``BTN_LEFT`` = 0x110, ...) so the bridge's button map from the Linux branch
applies unchanged.

What the filter covers
----------------------
Every pointer that reports through ``mouclass``: USB and Bluetooth mice, PS/2,
and HID touchpads in legacy mouse mode. Precision Touchpads report through the
HID digitizer path straight to ``win32k`` and are expected to bypass the filter
(not yet measured on hardware). The driver's ``connected_mice`` counter says how
many devices it is attached to, and :meth:`MouseIsolation.start` refuses to
report success when that count is zero.

Availability
------------
``MOUSE_ISOLATION_AVAILABLE`` is True only when this is Windows **and** the
driver's control device existed when this module was imported (it may have
been held by another process at that moment; that still counts as installed).
A Windows machine without the driver therefore looks to the bridge exactly like
a Linux machine without evdev access, and the existing ``mouse_hider`` path
stays in charge of Game Mode. After installing the driver, restart Nimbus.
:meth:`MouseIsolation.start` does not depend on the import-time result: it
opens the device again and raises ``RuntimeError`` with a specific message if
the driver is missing or another process holds it.

Safety
------
* The driver clears isolation when this process's handle closes (crash, kill,
  exit) and via its own 2 s read-inactivity watchdog. Once isolation is off the
  driver fails every read with ``ERROR_NOT_READY``, so after a watchdog release
  the reader thread exits and ``on_stopped`` fires instead of the software
  cursor going dead while the real cursor moves.
* :meth:`MouseIsolation.stop` is idempotent and registered with :mod:`atexit`.
* This module installs no hotkey. Until the bridge wires ``Ctrl+Alt+F12`` to
  :func:`stop_all`, isolation ends through :meth:`MouseIsolation.stop`, by
  closing Nimbus (handle cleanup), or by the watchdog once reads stop. The
  driver never touches the keyboard.

Requirements
------------
The Nimbus Mouse Filter driver installed and started (``driver/install-dev.ps1``
during development). The driver's interface version must match
``INTERFACE_VERSION``; :meth:`MouseIsolation.start` refuses a mismatch.
"""
from __future__ import annotations

import atexit
import struct
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

_IS_WINDOWS = sys.platform == "win32"

# Must match driver/nimbus_moufilter/nimbus_moufilter_ioctl.h
DEVICE_PATH = r"\\.\NimbusMouseFilter"
INTERFACE_VERSION = 2
IOCTL_NIMBUS_SET_ISOLATION = 0x00222000
IOCTL_NIMBUS_GET_STATUS = 0x00222004

# MOUSE_INPUT_DATA (ntddmou.h), x64 packing is natural with no padding here.
#   USHORT UnitId, Flags, ButtonFlags, ButtonData; ULONG RawButtons;
#   LONG LastX, LastY; ULONG ExtraInformation
_MOUSE_INPUT_DATA = struct.Struct("<HHHHIiiI")
assert _MOUSE_INPUT_DATA.size == 24

# NIMBUS_MOUFILTER_STATUS: eight ULONGs in this order.
_STATUS_STRUCT = struct.Struct("<8I")
_STATUS_FIELDS = ("version", "isolating", "connected_mice", "pending_reads",
                  "packets_captured", "packets_dropped", "packets_passed", "watchdog_releases")

# MOUSE_INPUT_DATA.Flags
MOUSE_MOVE_RELATIVE = 0x0000
MOUSE_MOVE_ABSOLUTE = 0x0001
MOUSE_VIRTUAL_DESKTOP = 0x0002   # absolute coordinates span the virtual desktop

# Absolute positions are scaled by the port driver to this range.
_ABSOLUTE_RANGE = 65536

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

# evdev button codes, matching the Linux module on the linux-uinput-support branch
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

_BUTTON_EDGES = (
    (MOUSE_LEFT_BUTTON_DOWN, BTN_LEFT, True), (MOUSE_LEFT_BUTTON_UP, BTN_LEFT, False),
    (MOUSE_RIGHT_BUTTON_DOWN, BTN_RIGHT, True), (MOUSE_RIGHT_BUTTON_UP, BTN_RIGHT, False),
    (MOUSE_MIDDLE_BUTTON_DOWN, BTN_MIDDLE, True), (MOUSE_MIDDLE_BUTTON_UP, BTN_MIDDLE, False),
    (MOUSE_BUTTON_4_DOWN, BTN_SIDE, True), (MOUSE_BUTTON_4_UP, BTN_SIDE, False),
    (MOUSE_BUTTON_5_DOWN, BTN_EXTRA, True), (MOUSE_BUTTON_5_UP, BTN_EXTRA, False),
)

WHEEL_DELTA = 120

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    ERROR_FILE_NOT_FOUND = 2
    ERROR_PATH_NOT_FOUND = 3
    ERROR_ACCESS_DENIED = 5
    ERROR_NOT_READY = 21            # STATUS_DEVICE_NOT_READY: read while isolation is off
    ERROR_SHARING_VIOLATION = 32
    ERROR_OPERATION_ABORTED = 995
    ERROR_IO_PENDING = 997

    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

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
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _u32.GetSystemMetrics.restype = ctypes.c_int


class DriverMissingError(RuntimeError):
    """The control device does not exist: driver not installed or not started."""


class DriverBusyError(RuntimeError):
    """Another handle holds the exclusive control device."""


def _open_device() -> int:
    """Open the control device for overlapped I/O. Raises ``RuntimeError``."""
    if not _IS_WINDOWS:
        raise RuntimeError("mouse isolation is Windows-only in this module")
    handle = _k32.CreateFileW(DEVICE_PATH, GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                              OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        err = ctypes.get_last_error()
        if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
            raise DriverMissingError("Nimbus Mouse Filter driver is not installed or not started "
                                     "(run driver/install-dev.ps1 from an elevated prompt)")
        if err in (ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION):
            # The control device is exclusive (WdfDeviceInitSetExclusive). A
            # second open is refused with STATUS_ACCESS_DENIED, which Win32
            # reports as ERROR_ACCESS_DENIED, not ERROR_SHARING_VIOLATION.
            raise DriverBusyError("another process already holds the Nimbus Mouse Filter open "
                                  "(the device is exclusive), or this account may not open it")
        raise RuntimeError(f"could not open {DEVICE_PATH}: Windows error {err}")
    return handle


def _ioctl(handle: int, code: int, name: str, in_buf: Any = None, out_buf: Any = None) -> int:
    """``DeviceIoControl`` on the control handle; returns the byte count returned.

    The handle is opened with ``FILE_FLAG_OVERLAPPED`` and the reader thread
    keeps a ``ReadFile`` pending on it, so every control call carries its own
    ``OVERLAPPED`` and event. Passing ``NULL`` there is undefined on an
    overlapped handle and only worked because the driver completes these
    IOCTLs inline.
    """
    event = _k32.CreateEventW(None, True, False, None)
    if not event:
        raise RuntimeError(f"CreateEvent failed: Windows error {ctypes.get_last_error()}")
    try:
        ov = _OVERLAPPED()
        ov.hEvent = event
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(handle, code,
                                  ctypes.byref(in_buf) if in_buf is not None else None,
                                  ctypes.sizeof(in_buf) if in_buf is not None else 0,
                                  out_buf, ctypes.sizeof(out_buf) if out_buf is not None else 0,
                                  ctypes.byref(returned), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise RuntimeError(f"{name} failed: Windows error {err}")
            if not _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), True):
                raise RuntimeError(f"{name} failed: Windows error {ctypes.get_last_error()}")
        return returned.value
    finally:
        _k32.CloseHandle(event)


def _set_isolation(handle: int, enable: bool) -> None:
    """Issue ``IOCTL_NIMBUS_SET_ISOLATION`` on an already open handle."""
    _ioctl(handle, IOCTL_NIMBUS_SET_ISOLATION, "IOCTL_NIMBUS_SET_ISOLATION",
           in_buf=wintypes.DWORD(1 if enable else 0))


def _query_status(handle: int) -> Dict[str, int]:
    """Issue ``IOCTL_NIMBUS_GET_STATUS`` on an already open handle."""
    buf = ctypes.create_string_buffer(_STATUS_STRUCT.size)
    returned = _ioctl(handle, IOCTL_NIMBUS_GET_STATUS, "IOCTL_NIMBUS_GET_STATUS", out_buf=buf)
    if returned < _STATUS_STRUCT.size:
        raise RuntimeError(f"IOCTL_NIMBUS_GET_STATUS returned {returned} bytes, "
                           f"expected {_STATUS_STRUCT.size}")
    return dict(zip(_STATUS_FIELDS, _STATUS_STRUCT.unpack(buf.raw)))


# Instances that currently hold the device (see the safety net at the bottom).
_instances: List["MouseIsolation"] = []
_instances_lock = threading.Lock()


def _active_instance() -> Optional["MouseIsolation"]:
    """The instance in this process that currently holds the device, if any."""
    with _instances_lock:
        for inst in _instances:
            if inst.active:
                return inst
    return None


def get_status() -> Dict[str, int]:
    """Read the driver's status struct.

    While an instance in this process is isolating, the exclusive device
    cannot be opened a second time, so the answer comes through that
    instance's own handle (:meth:`MouseIsolation.status`). Otherwise the
    device is opened, read, and closed. Raises ``RuntimeError`` if the driver
    is not reachable, including when another process holds it.
    """
    inst = _active_instance()
    if inst is not None:
        return inst.status()
    handle = _open_device()
    try:
        return _query_status(handle)
    finally:
        _k32.CloseHandle(handle)


def is_available() -> bool:
    """True if the driver's control device exists, whether or not it is free.

    A device held by another process, or by this process's own active
    instance, still counts as installed; :meth:`MouseIsolation.start` reports
    the busy case with its own message.
    """
    if not _IS_WINDOWS:
        return False
    if _active_instance() is not None:
        return True
    try:
        handle = _open_device()
    except DriverBusyError:
        return True
    except RuntimeError:
        return False
    _k32.CloseHandle(handle)
    return True


def _device_entry(status: Dict[str, int]) -> Dict[str, Any]:
    return {
        "name": "Nimbus Mouse Filter (all mice)",
        "node": DEVICE_PATH,
        "readable": True,
        "is_keyboard": False,
        "connected_mice": status.get("connected_mice", 0),
    }


def list_pointer_devices() -> List[Dict[str, Any]]:
    """Report the driver as a single grabbable 'device', to match the Linux API.

    The Windows filter is class-wide, so there are no per-node choices to make.
    Works while an instance in this process is isolating (the status comes
    through its handle). Returns an empty list when the driver is missing or
    another process holds it, in which case :meth:`MouseIsolation.start` would
    fail too.
    """
    try:
        status = get_status()
    except RuntimeError:
        return []
    return [_device_entry(status)]


# True only when the driver's device existed at import time. The bridge treats
# this the way it treats the Linux flag: False means Game Mode uses mouse_hider.
MOUSE_ISOLATION_AVAILABLE = is_available()


class MouseIsolation:
    """Isolate the physical mouse through the Nimbus Mouse Filter driver.

    Same constructor and lifecycle as the Linux ``MouseIsolation``
    (``src/mouse_isolation.py`` on the ``linux-uinput-support`` branch).
    Callbacks run on the reader thread; marshal to the UI thread before
    touching Qt objects (the bridge does this with queued signals).

    Args:
        on_motion: ``(dx, dy)`` per input report with movement, in pixels.
            Absolute-position devices (RDP, VM pointers, tablets in mouse
            mode) are converted to deltas against their previous position.
        on_button: ``(code, pressed)`` for mouse buttons (evdev codes).
        on_wheel: ``(horizontal, vertical)`` whole wheel notches; high
            resolution wheels accumulate until a notch is complete.
        on_stopped: ``(reason)`` when isolation ends for any reason, including
            ``"released by driver watchdog"`` when the driver gave the mouse
            back because this process stopped reading.
        hotkey: Accepted for API parity and ignored. This class installs no
            hotkey; the bridge is responsible for wiring ``Ctrl+Alt+F12`` to
            :meth:`stop` or :func:`stop_all`.
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
        self._lock = threading.RLock()
        self._active = False
        self._handle: Optional[int] = None
        self._read_event: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._devices: List[Dict[str, Any]] = []
        # Per UnitId: last absolute position, already scaled to pixels.
        self._abs_last: Dict[int, Tuple[float, float]] = {}
        # Wheel travel below one notch, carried to the next packet: [horizontal, vertical].
        self._wheel_rem = [0, 0]
        self.stop_reason = ""

    @property
    def active(self) -> bool:
        """True between a successful :meth:`start` and the matching :meth:`stop`."""
        return self._active

    @property
    def grabbed_devices(self) -> List[Dict[str, Any]]:
        """The device list captured at :meth:`start`; empty when inactive."""
        return list(self._devices) if self._active else []

    def status(self) -> Dict[str, int]:
        """Read the driver's counters through this instance's own handle.

        Works while isolating, which :func:`get_status` cannot because the
        device is exclusive. Raises ``RuntimeError`` when not active.
        """
        with self._lock:
            if not self._active or self._handle is None:
                raise RuntimeError("mouse isolation is not active")
            return _query_status(self._handle)

    def start(self, nodes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Open the driver and turn isolation on. ``nodes`` is ignored (class-wide).

        Returns the grabbed 'devices'. Raises ``RuntimeError`` if the driver is
        missing, already in use, built against another interface version, or
        attached to no mouse (nothing would be captured while the real cursor
        kept moving).
        """
        with self._lock:
            if self._active:
                return list(self._devices)
            self._handle = _open_device()
            try:
                self._read_event = _k32.CreateEventW(None, True, False, None)
                if not self._read_event:
                    raise RuntimeError(f"CreateEvent failed: Windows error {ctypes.get_last_error()}")
                status = _query_status(self._handle)
                if status["version"] != INTERFACE_VERSION:
                    raise RuntimeError(f"Nimbus Mouse Filter reports interface v{status['version']}, "
                                       f"this client needs v{INTERFACE_VERSION}; rebuild and reinstall the driver")
                _set_isolation(self._handle, True)
                try:
                    # Re-read after enabling: this count is what the driver is
                    # attached to right now. Zero means the real cursor would
                    # keep moving (a Precision Touchpad, or a mouse that is
                    # present but not started) while this reader saw nothing.
                    status = _query_status(self._handle)
                    if status["connected_mice"] == 0:
                        raise RuntimeError("the Nimbus Mouse Filter is attached to no mouse "
                                           "(connected_mice = 0), so nothing would be isolated; "
                                           "plug in a mouse that reports through mouclass "
                                           "(Precision Touchpads bypass the filter)")
                except Exception:
                    try:
                        _set_isolation(self._handle, False)
                    except Exception:
                        pass
                    raise
            except Exception:
                self._close_handles()
                raise
            self._devices = [_device_entry(status)]
            self._abs_last.clear()
            self._wheel_rem = [0, 0]
            self.stop_reason = ""
            # Everything below stays under the lock so a stop() from another
            # thread cannot run between "_active" and the reader existing.
            self._thread = threading.Thread(target=self._reader, daemon=True, name="MouseIsolationWin")
            self._active = True
            _register_instance(self)
            self._thread.start()
        print("[mouse_isolation_win] isolation on (released by stop(), by closing Nimbus, "
              "or by the driver watchdog if reads stop)")
        return list(self._devices)

    def stop(self, reason: str = "requested") -> None:
        """Turn isolation off, stop the reader, and close the driver handle."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stop_reason = reason
            handle = self._handle
        if handle is not None:
            try:
                _set_isolation(handle, False)   # the driver fails the pending read
            except Exception:
                pass
            _k32.CancelIoEx(handle, None)       # and this wakes it if the IOCTL itself failed
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.0)
        with self._lock:
            self._close_handles()
        _unregister_instance(self)
        print(f"[mouse_isolation_win] released ({reason})")
        if self._on_stopped:
            try:
                self._on_stopped(reason)
            except Exception as exc:
                print(f"[mouse_isolation_win] on_stopped error: {exc}")

    def _close_handles(self) -> None:
        for attr in ("_handle", "_read_event"):
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
        handle = self._handle
        try:
            while self._active:
                returned = wintypes.DWORD(0)
                ok = _k32.ReadFile(handle, buf, ctypes.sizeof(buf), ctypes.byref(returned), ctypes.byref(ov))
                if not ok:
                    err = ctypes.get_last_error()
                    if err == ERROR_IO_PENDING:
                        # Blocks until the driver completes the read or CancelIoEx aborts it.
                        if _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), True):
                            err = 0
                        else:
                            err = ctypes.get_last_error()
                    if err:
                        reason = _read_failure_reason(err)
                        break
                n = returned.value
                if n:
                    self._dispatch(buf, n)
        except Exception as exc:
            reason = f"error: {exc}"
        if self._active:
            threading.Thread(target=self.stop, args=(reason,), daemon=True).start()

    def _dispatch(self, data: Any, length: int) -> None:
        # ``data`` is the ctypes read buffer itself; unpacking in place avoids
        # copying the whole 6 KB buffer for what is usually one 24-byte packet.
        count = length // _MOUSE_INPUT_DATA.size
        for i in range(count):
            (unit, flags, button_flags, button_data, _raw,
             last_x, last_y, _extra) = _MOUSE_INPUT_DATA.unpack_from(data, i * _MOUSE_INPUT_DATA.size)
            if flags & MOUSE_MOVE_ABSOLUTE:
                dx, dy = self._absolute_to_delta(unit, flags, last_x, last_y)
            else:
                dx, dy = last_x, last_y
            if dx or dy:
                self._on_motion(dx, dy)
            if button_flags:
                for mask, code, pressed in _BUTTON_EDGES:
                    if button_flags & mask:
                        self._on_button(code, pressed)
                if self._on_wheel:
                    if button_flags & MOUSE_WHEEL:
                        notch = self._wheel_notches(1, _signed16(button_data))
                        if notch:
                            self._on_wheel(0, notch)
                    if button_flags & MOUSE_HWHEEL:
                        notch = self._wheel_notches(0, _signed16(button_data))
                        if notch:
                            self._on_wheel(notch, 0)

    def _wheel_notches(self, axis: int, delta: int) -> int:
        """Add wheel travel and return the whole notches it completes.

        ``int(x / WHEEL_DELTA)`` truncates toward zero, so -60 and +60 both give
        0 and the remainder waits for the next packet. Floor division would
        turn -60 into -1 and +60 into 0.
        """
        total = self._wheel_rem[axis] + delta
        notch = int(total / WHEEL_DELTA)
        self._wheel_rem[axis] = total - notch * WHEEL_DELTA
        return notch

    def _absolute_to_delta(self, unit: int, flags: int, x: int, y: int) -> Tuple[int, int]:
        """Convert an absolute position packet to whole pixels of motion.

        Absolute devices report 0..65535 across the primary monitor, or across
        the virtual desktop when ``MOUSE_VIRTUAL_DESKTOP`` is set. The first
        packet from a unit only records where it is; the sub-pixel remainder
        is carried in the stored position so slow motion is not lost.
        """
        if flags & MOUSE_VIRTUAL_DESKTOP:
            width, height = _u32.GetSystemMetrics(SM_CXVIRTUALSCREEN), _u32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        else:
            width, height = _u32.GetSystemMetrics(SM_CXSCREEN), _u32.GetSystemMetrics(SM_CYSCREEN)
        sx = x * (width or _ABSOLUTE_RANGE) / _ABSOLUTE_RANGE
        sy = y * (height or _ABSOLUTE_RANGE) / _ABSOLUTE_RANGE
        prev = self._abs_last.get(unit)
        if prev is None:
            self._abs_last[unit] = (sx, sy)
            return 0, 0
        dx = int(sx - prev[0])
        dy = int(sy - prev[1])
        self._abs_last[unit] = (prev[0] + dx, prev[1] + dy)
        return dx, dy


def _signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def _read_failure_reason(err: int) -> str:
    if err == ERROR_OPERATION_ABORTED:
        return "cancelled"
    if err == ERROR_NOT_READY:
        # The driver fails reads while isolation is off. Reaching this with
        # _active still True means its watchdog released the mouse because
        # this process stopped reading for 2 s.
        return "released by driver watchdog"
    return f"read error {err}"


# ---- process-wide safety net (mirrors the Linux module) -------------------
# _instances and _instances_lock are defined next to _active_instance() above.

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

    if not _IS_WINDOWS:
        raise SystemExit("Windows only")

    def print_status(st: Dict[str, int]) -> None:
        print(f"Nimbus Mouse Filter (interface v{st['version']}, expected v{INTERFACE_VERSION})")
        for key in _STATUS_FIELDS[1:]:
            print(f"  {key:<18} {st[key]}")

    if args.status or not args.grab:
        try:
            print_status(get_status())
        except RuntimeError as exc:
            raise SystemExit(str(exc))
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

    def on_stopped(reason):
        if reason != "cli done":
            print(f"  isolation ended early: {reason}")

    iso = MouseIsolation(on_motion, on_button, on_stopped=on_stopped)
    iso.start()
    print(f"isolating for {args.grab}s; the desktop cursor should be frozen")
    try:
        deadline = time.monotonic() + args.grab
        while iso.active and time.monotonic() < deadline:
            time.sleep(0.1)
        if iso.active:
            print_status(iso.status())
    finally:
        iso.stop("cli done")
    print(f"summed motion dx={totals['dx']} dy={totals['dy']} button edges={totals['buttons']}")
