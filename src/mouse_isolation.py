"""
Linux mouse isolation: exclusive ``EVIOCGRAB`` of the physical mouse.

This is the input half of the Linux design in
``docs/vision/HOST_MODE_ISOLATION.md``. Holding ``EVIOCGRAB`` on a mouse's
evdev node means nothing else on the machine, not the X server, not a
Wayland compositor, not a game under Proton, receives that device's events.
Nimbus reads them here and :mod:`src.bridge` turns the deltas into a
software cursor plus synthetic Qt mouse events, so the on-screen widgets
keep working while the game is genuinely blind to the mouse.

Devices
-------
Pointer devices are discovered from ``/proc/bus/input/devices`` (entries
with a ``mouseN`` handler). Many mice expose several nodes; all of them are
grabbed. Combined keyboard-and-touchpad devices (Logitech K400, laptops)
are common: when a grabbed device also carries keyboard keys, those keys are
forwarded to a uinput "pass-through" keyboard so typing keeps working while
only the pointer is isolated.

Safety
------
* The kernel drops a grab when the file descriptor closes, so a crash or
  ``kill`` always releases the mouse.
* :func:`stop` is idempotent and is also registered with :mod:`atexit`.
* ``Ctrl+Alt+F12`` releases the grab from anywhere: keys from grabbed combo
  devices are seen directly, and every other keyboard is watched read-only
  (never grabbed).
* Any error in the reader thread stops the grab.

Requirements
------------
Read access to ``/dev/input/event*`` for mouse and keyboard devices (the
``input`` group) and write access to ``/dev/uinput`` for pass-through
keyboards. Everything is pure Python (``ioctl`` + ``struct``).
"""
from __future__ import annotations

import atexit
import fcntl
import os
import select
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .uinput_interface import (
    _ioc, _IOC_READ, _IOC_WRITE, _INPUT_EVENT, _UINPUT_SETUP,
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY,
    UINPUT_DEVICE_PATH, EV_SYN, EV_KEY, SYN_REPORT, BUS_VIRTUAL,
)

MOUSE_ISOLATION_AVAILABLE = sys.platform.startswith("linux")

EV_REL = 0x02
EV_MSC = 0x04
REL_X, REL_Y = 0x00, 0x01
REL_HWHEEL, REL_WHEEL = 0x06, 0x08
BTN_MOUSE, BTN_TASK = 0x110, 0x117          # mouse button code range (inclusive)
KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
KEY_LEFTALT, KEY_RIGHTALT = 56, 100
KEY_F12 = 88
KEY_MAX = 0x2FF
MSC_SCAN = 0x04

EVIOCGRAB = _ioc(_IOC_WRITE, "E", 0x90, 4)
UI_SET_MSCBIT = _ioc(_IOC_WRITE, "U", 104, 4)


def _eviocgbit(ev_type: int, length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x20 + ev_type, length)


def _key_capabilities(fd: int) -> List[int]:
    """Return the EV_KEY codes a device reports (via ``EVIOCGBIT``)."""
    buf = bytearray((KEY_MAX + 1) // 8)
    fcntl.ioctl(fd, _eviocgbit(EV_KEY, len(buf)), buf)
    return [code for code in range(KEY_MAX + 1) if buf[code // 8] & (1 << (code % 8))]


def _is_mouse_button(code: int) -> bool:
    return BTN_MOUSE <= code <= BTN_TASK


def list_input_devices() -> List[Dict[str, Any]]:
    """Parse ``/proc/bus/input/devices`` into dicts with name, node, and handlers."""
    devices: List[Dict[str, Any]] = []
    block: Dict[str, Any] = {}

    def flush() -> None:
        handlers = block.get("handlers", [])
        event = next((h for h in handlers if h.startswith("event")), None)
        if event:
            devices.append({
                "name": block.get("name", ""),
                "node": f"/dev/input/{event}",
                "handlers": handlers,
                "is_pointer": any(h.startswith("mouse") for h in handlers),
                "is_keyboard": "kbd" in handlers,
            })

    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    if block:
                        flush()
                    block = {}
                elif line.startswith("N: Name="):
                    block["name"] = line.split('"')[1] if '"' in line else line[8:]
                elif line.startswith("H: Handlers="):
                    block["handlers"] = line[len("H: Handlers="):].split()
        if block:
            flush()
    except OSError:
        pass
    return devices


def list_pointer_devices() -> List[Dict[str, Any]]:
    """Pointer-class devices Nimbus would grab, excluding its own virtual ones."""
    out = []
    for dev in list_input_devices():
        if not dev["is_pointer"] or dev["name"].startswith("Nimbus"):
            continue
        dev["readable"] = os.access(dev["node"], os.R_OK)
        out.append(dev)
    return out


class _PassthroughKeyboard:
    """uinput keyboard that re-emits the keys of a grabbed combo device."""

    def __init__(self, source_name: str, key_codes: List[int]) -> None:
        self.fd = os.open(UINPUT_DEVICE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for code in key_codes:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_MSC)
            fcntl.ioctl(self.fd, UI_SET_MSCBIT, MSC_SCAN)
            name = f"{source_name} (Nimbus passthrough)".encode("utf-8")[:79]
            fcntl.ioctl(self.fd, UI_DEV_SETUP, _UINPUT_SETUP.pack(BUS_VIRTUAL, 0x1209, 0x4E4B, 1, name, 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise
        self.name = name.decode("utf-8", "replace")

    def write(self, ev_type: int, code: int, value: int) -> None:
        os.write(self.fd, _INPUT_EVENT.pack(0, 0, ev_type, code, value))

    def close(self) -> None:
        if self.fd < 0:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except Exception:
            pass
        self.fd = -1


class MouseIsolation:
    """Grab pointer devices and stream their events to callbacks.

    All callbacks run on the reader thread; marshal to the UI thread before
    touching Qt objects (the bridge does this with queued signals).

    Args:
        on_motion: ``(dx, dy)`` per input report with movement.
        on_button: ``(code, pressed)`` for mouse buttons (``BTN_LEFT`` ...).
        on_wheel: ``(horizontal, vertical)`` wheel notches.
        on_stopped: ``(reason)`` when the grab ends for any reason.
        hotkey: Release on ``Ctrl+Alt+F12`` when True.
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
        self._hotkey = hotkey
        self._lock = threading.Lock()
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._grabbed: Dict[int, Dict[str, Any]] = {}      # fd -> device info
        self._watched: Dict[int, Dict[str, Any]] = {}      # read-only keyboards (hotkey)
        self._passthrough: Dict[int, _PassthroughKeyboard] = {}
        self._held: Dict[int, set] = {}                    # fd -> held hotkey keys
        self.stop_reason = ""

    # ---- lifecycle -------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def grabbed_devices(self) -> List[Dict[str, Any]]:
        return [dict(info) for info in self._grabbed.values()]

    def start(self, nodes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Grab pointer devices (all of them, or the given event nodes).

        Returns:
            The grabbed devices. Raises ``RuntimeError`` if none could be
            grabbed, with a message that says why.
        """
        with self._lock:
            if self._active:
                return self.grabbed_devices
            candidates = list_pointer_devices()
            if nodes:
                wanted = {os.path.realpath(n) for n in nodes}
                candidates = [d for d in candidates if os.path.realpath(d["node"]) in wanted]
                known = {os.path.realpath(d["node"]) for d in candidates}
                for n in wanted - known:                      # nodes not in /proc listing (tests)
                    candidates.append({"name": os.path.basename(n), "node": n, "handlers": [],
                                       "is_pointer": True, "is_keyboard": False, "readable": os.access(n, os.R_OK)})
            if not candidates:
                raise RuntimeError("no pointer devices found")
            problems = []
            for dev in candidates:
                try:
                    self._grab_one(dev)
                except Exception as exc:
                    problems.append(f"{dev['name'] or dev['node']}: {exc}")
            if not self._grabbed:
                self._cleanup_fds()
                if any("Permission denied" in p for p in problems):
                    raise RuntimeError(
                        "no read access to /dev/input/event* (add your user to the "
                        "'input' group: sudo usermod -aG input $USER, then log back in)")
                raise RuntimeError("; ".join(problems) or "could not grab any pointer device")
            for problem in problems:
                print(f"[mouse_isolation] skipped {problem}")
            if self._hotkey:
                self._open_keyboard_watchers()
            self._active = True
            self.stop_reason = ""
        _register_instance(self)
        self._thread = threading.Thread(target=self._reader, daemon=True, name="MouseIsolation")
        self._thread.start()
        names = ", ".join(d["name"] or d["node"] for d in self._grabbed.values())
        print(f"[mouse_isolation] grabbed: {names}")
        if self._passthrough:
            print(f"[mouse_isolation] keyboard pass-through for {len(self._passthrough)} combo device(s)")
        if self._hotkey:
            print("[mouse_isolation] emergency release: Ctrl+Alt+F12")
        return self.grabbed_devices

    def _grab_one(self, dev: Dict[str, Any]) -> None:
        fd = os.open(dev["node"], os.O_RDONLY | os.O_NONBLOCK)
        try:
            keys = _key_capabilities(fd)
            # Keyboard keys: everything below the mouse-button block, plus the
            # extra keys above it but below the joystick/trigger-happy range.
            kb_keys = [k for k in keys if k < BTN_MOUSE or (BTN_TASK < k < 0x2C0)]
            passthrough = None
            if dev.get("is_keyboard") or any(k < BTN_MOUSE for k in kb_keys):
                # Refuse to silence a keyboard: only grab if we can re-emit its keys.
                passthrough = _PassthroughKeyboard(dev["name"] or "Keyboard", kb_keys)
            fcntl.ioctl(fd, EVIOCGRAB, 1)
        except Exception:
            os.close(fd)
            raise
        self._grabbed[fd] = dict(dev)
        self._held[fd] = set()
        if passthrough is not None:
            self._passthrough[fd] = passthrough

    def _open_keyboard_watchers(self) -> None:
        grabbed_nodes = {os.path.realpath(d["node"]) for d in self._grabbed.values()}
        for dev in list_input_devices():
            if not dev["is_keyboard"] or dev["name"].startswith("Nimbus") or "(Nimbus passthrough)" in dev["name"]:
                continue
            if os.path.realpath(dev["node"]) in grabbed_nodes:
                continue
            try:
                fd = os.open(dev["node"], os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._watched[fd] = dev
            self._held[fd] = set()

    def stop(self, reason: str = "requested") -> None:
        """Release every grab, destroy pass-through keyboards, stop the thread."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stop_reason = reason
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.0)
        self._cleanup_fds()
        _unregister_instance(self)
        print(f"[mouse_isolation] released ({reason})")
        if self._on_stopped:
            try:
                self._on_stopped(reason)
            except Exception as exc:
                print(f"[mouse_isolation] on_stopped error: {exc}")

    def _cleanup_fds(self) -> None:
        for fd in list(self._grabbed):
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        self._grabbed.clear()
        for fd in list(self._watched):
            try:
                os.close(fd)
            except OSError:
                pass
        self._watched.clear()
        for pt in self._passthrough.values():
            pt.close()
        self._passthrough.clear()
        self._held.clear()

    # ---- reader ----------------------------------------------------------
    def _hotkey_hit(self, fd: int, code: int, value: int) -> bool:
        held = self._held.setdefault(fd, set())
        if code in (KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTALT, KEY_RIGHTALT, KEY_F12):
            if value:
                held.add(code)
            else:
                held.discard(code)
        ctrl = KEY_LEFTCTRL in held or KEY_RIGHTCTRL in held
        alt = KEY_LEFTALT in held or KEY_RIGHTALT in held
        return ctrl and alt and KEY_F12 in held

    def _reader(self) -> None:
        reason = "reader exited"
        try:
            while self._active:
                fds = list(self._grabbed) + list(self._watched)
                if not fds:
                    reason = "no devices"
                    break
                ready, _, _ = select.select(fds, [], [], 0.25)
                for fd in ready:
                    try:
                        data = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    except OSError as exc:
                        if fd in self._grabbed:
                            reason = f"device disconnected ({exc.strerror})"
                            raise
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        self._watched.pop(fd, None)
                        continue
                    if fd in self._grabbed:
                        self._handle_grabbed(fd, data)
                    else:
                        self._handle_watched(fd, data)
            else:
                return  # stopped by stop()
        except Exception as exc:
            if self._active:
                print(f"[mouse_isolation] reader error: {exc}")
                reason = reason if reason != "reader exited" else f"error: {exc}"
        if self._active:
            threading.Thread(target=self.stop, args=(reason,), daemon=True).start()

    def _handle_grabbed(self, fd: int, data: bytes) -> None:
        passthrough = self._passthrough.get(fd)
        dx = dy = wheel = hwheel = 0
        for off in range(0, len(data) - _INPUT_EVENT.size + 1, _INPUT_EVENT.size):
            _s, _u, ev_type, code, value = _INPUT_EVENT.unpack_from(data, off)
            if ev_type == EV_REL:
                if code == REL_X:
                    dx += value
                elif code == REL_Y:
                    dy += value
                elif code == REL_WHEEL:
                    wheel += value
                elif code == REL_HWHEEL:
                    hwheel += value
            elif ev_type == EV_KEY:
                if _is_mouse_button(code):
                    if value in (0, 1):
                        self._on_button(code, bool(value))
                else:
                    if self._hotkey and self._hotkey_hit(fd, code, value):
                        threading.Thread(target=self.stop, args=("emergency hotkey",), daemon=True).start()
                        return
                    if passthrough is not None and value in (0, 1):
                        passthrough.write(EV_KEY, code, value)
            elif ev_type == EV_MSC:
                if passthrough is not None:
                    passthrough.write(EV_MSC, code, value)
            elif ev_type == EV_SYN and code == SYN_REPORT:
                if dx or dy:
                    self._on_motion(dx, dy)
                    dx = dy = 0
                if (wheel or hwheel) and self._on_wheel:
                    self._on_wheel(hwheel, wheel)
                    wheel = hwheel = 0
                if passthrough is not None:
                    passthrough.write(EV_SYN, SYN_REPORT, 0)
        if dx or dy:
            self._on_motion(dx, dy)
        if (wheel or hwheel) and self._on_wheel:
            self._on_wheel(hwheel, wheel)

    def _handle_watched(self, fd: int, data: bytes) -> None:
        for off in range(0, len(data) - _INPUT_EVENT.size + 1, _INPUT_EVENT.size):
            _s, _u, ev_type, code, value = _INPUT_EVENT.unpack_from(data, off)
            if ev_type == EV_KEY and self._hotkey_hit(fd, code, value):
                threading.Thread(target=self.stop, args=("emergency hotkey",), daemon=True).start()
                return


# ---- process-wide safety net --------------------------------------------
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
    """Release every active grab (also runs at interpreter exit)."""
    with _instances_lock:
        pending = list(_instances)
    for inst in pending:
        try:
            inst.stop(reason)
        except Exception:
            pass


atexit.register(stop_all, "atexit")
