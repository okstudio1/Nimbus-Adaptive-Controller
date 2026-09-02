"""
Linux ``uinput`` virtual-controller back ends.

Linux has neither vJoy nor ViGEmBus. What it has instead is the kernel's
``uinput`` module, which lets any process with write access to
``/dev/uinput`` create a virtual input device that every consumer (SDL,
Steam, Proton, browsers, ``evtest``) sees as ordinary hardware. This module
provides two such devices, and :mod:`src.bridge` uses them exactly where it
would use the Windows drivers:

* :class:`UInputXboxInterface`: an Xbox 360 gamepad (same vendor/product IDs
  the kernel ``xpad`` driver reports, so SDL/Steam apply their built-in
  mapping) with the same public API as
  :class:`~src.vigem_interface.ViGEmInterface`. Selected for ``"vigem"``
  output mode.
* :class:`UInputJoystickInterface`: a generic 8-axis / 56-button joystick
  with the same public API as :class:`~src.vjoy_interface.VJoyInterface`.
  Selected for ``"vjoy"`` output mode.

The implementation talks to ``/dev/uinput`` directly through ``ioctl`` and
``struct`` so there is no compiled dependency (``python-evdev`` needs a C
toolchain to install). It needs kernel 4.5 or newer for ``UI_DEV_SETUP`` /
``UI_ABS_SETUP``, which every supported distribution has had since 2016.

Permissions
-----------
``/dev/uinput`` is ``root:root 0600`` on a stock system. Steam installs a udev
rule that grants the logged-in user access; without Steam, install the rule
shipped in ``build_tools/linux/60-nimbus-uinput.rules`` (see
``docs/setup/LINUX.md``). On failure the interfaces stay in simulation mode
(``is_connected == False``) and print what to do, matching the Windows
back ends' graceful degradation.

Axis conventions
----------------
* Xbox device: sticks are ``-32768..32767`` and ``+Y`` is **down** in evdev,
  so the XInput-style ``+Y == up`` values the bridge sends are negated.
  Triggers are ``0..255``. The D-pad is the ``ABS_HAT0X/Y`` hat.
* Joystick device: all eight axes are ``0..axis_range`` (``vjoy.axis_range``,
  default 32767) with the same ``(value + 1) / 2`` scaling as vJoy, so a
  profile behaves identically on both platforms.
"""
from __future__ import annotations

import os
import struct
import sys
import threading
import time
from typing import Any, Dict, Iterable, Optional, Tuple

from .config import ControllerConfig

try:  # fcntl does not exist on Windows; the bridge treats ImportError as "not available"
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

UINPUT_DEVICE_PATH = "/dev/uinput"
UINPUT_AVAILABLE = sys.platform.startswith("linux") and fcntl is not None

# ---------------------------------------------------------------------------
# linux/input.h and linux/uinput.h constants (asm-generic ioctl layout)
# ---------------------------------------------------------------------------
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, type_char: str, nr: int, size: int) -> int:
    """Build an ioctl request number the way ``_IOC()`` does in C."""
    return (
        (direction << _IOC_DIRSHIFT)
        | (ord(type_char) << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
_INPUT_EVENT = struct.Struct("llHHi")
# struct uinput_setup { struct input_id id; char name[80]; __u32 ff_effects_max; }
_UINPUT_SETUP = struct.Struct("HHHH80sI")
# struct uinput_abs_setup { __u16 code; struct input_absinfo absinfo; }  (absinfo is 4-aligned)
_UINPUT_ABS_SETUP = struct.Struct("H2xiiiiii")
UINPUT_MAX_NAME_SIZE = 80

UI_DEV_CREATE = _ioc(_IOC_NONE, "U", 1, 0)
UI_DEV_DESTROY = _ioc(_IOC_NONE, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, _UINPUT_SETUP.size)
UI_ABS_SETUP = _ioc(_IOC_WRITE, "U", 4, _UINPUT_ABS_SETUP.size)
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, "U", 101, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, "U", 103, 4)


def _ui_get_sysname(length: int) -> int:
    return _ioc(_IOC_READ, "U", 44, length)


EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00

BUS_USB = 0x03
BUS_VIRTUAL = 0x06

ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_THROTTLE = 0x06
ABS_RUDDER = 0x07
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

BTN_JOYSTICK = 0x120  # BTN_TRIGGER .. BTN_DEAD: 16 codes
BTN_A = 0x130  # a.k.a. BTN_SOUTH / BTN_GAMEPAD
BTN_B = 0x131  # BTN_EAST
BTN_X = 0x133  # BTN_NORTH
BTN_Y = 0x134  # BTN_WEST
BTN_TL = 0x136
BTN_TR = 0x137
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_THUMBL = 0x13D
BTN_THUMBR = 0x13E
BTN_TRIGGER_HAPPY = 0x2C0  # BTN_TRIGGER_HAPPY1 .. BTN_TRIGGER_HAPPY40

# Identity the kernel xpad driver reports for a wired Xbox 360 pad. SDL's
# built-in gamecontrollerdb keys its mapping on bus/vendor/product/version.
XBOX360_NAME = "Microsoft X-Box 360 pad"
XBOX360_VENDOR = 0x045E
XBOX360_PRODUCT = 0x028E
XBOX360_VERSION = 0x0114

JOYSTICK_NAME = "Nimbus Virtual Joystick"
JOYSTICK_VENDOR = 0x1209  # pid.codes open-source VID space
JOYSTICK_PRODUCT = 0x4E49  # "NI"
JOYSTICK_VERSION = 0x0001
JOYSTICK_MAX_BUTTONS = 56  # 16 in the BTN_JOYSTICK block + 40 BTN_TRIGGER_HAPPY

# code -> (minimum, maximum, fuzz, flat)
AxisSpec = Tuple[int, int, int, int]


class _UInputDevice:
    """Thin owner of one ``/dev/uinput`` file descriptor.

    Creates the virtual device in the constructor and destroys it in
    :meth:`close`. All event emission goes through :meth:`emit`, which
    appends the ``SYN_REPORT`` that consumers need to see a coherent frame.

    Args:
        name: Device name as shown by ``evtest`` / SDL (max 79 bytes).
        vendor: USB vendor ID.
        product: USB product ID.
        version: Device version.
        bustype: ``BUS_USB`` or ``BUS_VIRTUAL``.
        keys: Button codes (``BTN_*``) to register.
        axes: Mapping of ``ABS_*`` code to ``(min, max, fuzz, flat)``.
        initial: Mapping of ``ABS_*`` code to the value reported at creation.
    """

    def __init__(
        self,
        name: str,
        vendor: int,
        product: int,
        version: int,
        bustype: int,
        keys: Iterable[int],
        axes: Dict[int, AxisSpec],
        initial: Optional[Dict[int, int]] = None,
    ) -> None:
        if fcntl is None:
            raise OSError("uinput is only available on Linux")
        initial = initial or {}
        self.name = name
        self.fd = os.open(UINPUT_DEVICE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
            for code in keys:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            for code, (lo, hi, fuzz, flat) in axes.items():
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                fcntl.ioctl(
                    self.fd,
                    UI_ABS_SETUP,
                    _UINPUT_ABS_SETUP.pack(code, initial.get(code, 0), lo, hi, fuzz, flat, 0),
                )
            setup = _UINPUT_SETUP.pack(
                bustype, vendor, product, version,
                name.encode("utf-8")[: UINPUT_MAX_NAME_SIZE - 1], 0,
            )
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            self.fd = -1
            raise
        self.sysname: str = self._query_sysname()
        self.event_node: str = self._find_event_node(self.sysname)
        # Give udev a moment to publish the node before the first frame.
        time.sleep(0.05)

    def _query_sysname(self) -> str:
        try:
            buf = bytearray(64)
            fcntl.ioctl(self.fd, _ui_get_sysname(len(buf)), buf)
            return bytes(buf).split(b"\0", 1)[0].decode("ascii", "replace")
        except Exception:
            return ""

    @staticmethod
    def _find_event_node(sysname: str) -> str:
        if not sysname:
            return ""
        try:
            for entry in os.listdir(f"/sys/devices/virtual/input/{sysname}"):
                if entry.startswith("event"):
                    return f"/dev/input/{entry}"
        except OSError:
            pass
        return ""

    def emit(self, events: Iterable[Tuple[int, int, int]]) -> None:
        """Write ``(type, code, value)`` events followed by one ``SYN_REPORT``.

        The kernel stamps the events itself, so the ``timeval`` is left zero.
        """
        if self.fd < 0:
            raise OSError("uinput device is closed")
        frame = b"".join(_INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events)
        frame += _INPUT_EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(self.fd, frame)

    def close(self) -> None:
        """Destroy the virtual device and release the descriptor (idempotent)."""
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

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _explain_open_failure(exc: BaseException) -> None:
    """Print actionable troubleshooting for a failed ``/dev/uinput`` open."""
    print(f"[ERROR] Could not create uinput device: {exc}")
    if isinstance(exc, PermissionError):
        print("No write access to /dev/uinput. Grant it with a udev rule:")
        print("  sudo cp build_tools/linux/60-nimbus-uinput.rules /etc/udev/rules.d/")
        print("  sudo udevadm control --reload && sudo udevadm trigger /dev/uinput")
        print("then log out and back in. (Steam installs an equivalent rule.)")
    elif isinstance(exc, FileNotFoundError):
        print("/dev/uinput is missing. Load the kernel module:")
        print("  sudo modprobe uinput")
        print("and add 'uinput' to /etc/modules-load.d/ to make it permanent.")
    print("See docs/setup/LINUX.md for details. Running in simulation mode.")


class _UInputBase:
    """State and lifecycle shared by both uinput back ends.

    Subclasses define the device shape via :meth:`_device_spec` and the
    reset behaviour via :meth:`_reset_axes`.

    Args:
        config: Configuration manager instance.
    """

    #: Human-readable device type used in ``get_status()`` and logs.
    device_type: str = "uinput device"

    def __init__(self, config: ControllerConfig) -> None:
        self.config = config
        self.device: Optional[_UInputDevice] = None
        self.is_connected = False
        self.last_update_time = 0.0
        self.last_command_time = time.time()
        self.failsafe_active = False
        self.button_states: Dict[int, bool] = {}
        self._lock = threading.Lock()
        self._warned_buttons: set = set()
        self._initialize()

    # ---- lifecycle -------------------------------------------------------
    def _device_spec(self) -> Tuple[str, int, int, int, int, Iterable[int], Dict[int, AxisSpec], Dict[int, int]]:
        """Return ``(name, vendor, product, version, bustype, keys, axes, initial)``."""
        raise NotImplementedError

    def _initialize(self) -> None:
        if not UINPUT_AVAILABLE:
            print(f"{self.device_type}: uinput is only available on Linux - running in simulation mode")
            return
        try:
            name, vendor, product, version, bustype, keys, axes, initial = self._device_spec()
            self.device = _UInputDevice(name, vendor, product, version, bustype, keys, axes, initial)
            self.is_connected = True
            node = self.device.event_node or "(node not resolved)"
            print(f"[OK] uinput device created: '{name}' at {node}")
            self._reset_axes()
        except Exception as exc:
            _explain_open_failure(exc)
            self.device = None
            self.is_connected = False

    def _emit(self, events: Iterable[Tuple[int, int, int]]) -> bool:
        if not self.is_connected or not self.device:
            return False
        try:
            with self._lock:
                self.device.emit(events)
            now = time.time()
            self.last_update_time = now
            self.last_command_time = now
            return True
        except Exception as exc:
            print(f"Error writing to uinput device: {exc}")
            return False

    def _reset_axes(self) -> None:
        raise NotImplementedError

    def _release_all_buttons(self) -> None:
        for button_id, pressed in list(self.button_states.items()):
            if pressed:
                self.set_button(button_id, False)

    def emergency_stop(self) -> None:
        """Emergency stop: immediately center all axes and release all buttons."""
        print("EMERGENCY STOP ACTIVATED")
        if self.device:
            try:
                self._reset_axes()
                self._release_all_buttons()
            except Exception as exc:
                print(f"Error during emergency stop: {exc}")

    def shutdown(self) -> None:
        """Center the device, release every button, and destroy it."""
        if self.device:
            print(f"Shutting down {self.device_type}...")
            try:
                self._reset_axes()
                self._release_all_buttons()
            except Exception as exc:
                print(f"Error during shutdown: {exc}")
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
        self.is_connected = False

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass

    # ---- shared API ------------------------------------------------------
    def set_button(self, button_id: int, pressed: bool) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def get_status(self) -> Dict[str, Any]:
        """Return a status dictionary (shape shared with the Windows back ends)."""
        return {
            "connected": self.is_connected,
            "device_type": self.device_type,
            "event_node": self.device.event_node if self.device else "",
            "failsafe_active": self.failsafe_active,
            "last_update": self.last_update_time,
            "button_states": self.button_states.copy(),
            "uinput_available": UINPUT_AVAILABLE,
        }


class UInputXboxInterface(_UInputBase):
    """Virtual Xbox 360 gamepad via ``uinput``.

    Drop-in replacement for :class:`~src.vigem_interface.ViGEmInterface` on
    Linux: same method names, same value ranges (sticks ``-1..1`` with
    ``+Y == up``, triggers ``0..1``), same 14-button numbering.

    Button mapping (matches ViGEmInterface):
        1: A, 2: B, 3: X, 4: Y, 5: LB, 6: RB, 7: Back, 8: Start,
        9: LS click, 10: RS click, 11-14: D-pad Up/Down/Left/Right

    Args:
        config: Configuration manager instance.
    """

    device_type = "Xbox 360 Controller (uinput)"

    STICK_MAX = 32767
    TRIGGER_MAX = 255

    BUTTON_MAP: Dict[int, int] = {
        1: BTN_A, 2: BTN_B, 3: BTN_X, 4: BTN_Y,
        5: BTN_TL, 6: BTN_TR, 7: BTN_SELECT, 8: BTN_START,
        9: BTN_THUMBL, 10: BTN_THUMBR,
    }
    # D-pad buttons drive the hat axes: id -> (axis code, direction)
    DPAD_MAP: Dict[int, Tuple[int, int]] = {
        11: (ABS_HAT0Y, -1), 12: (ABS_HAT0Y, 1),
        13: (ABS_HAT0X, -1), 14: (ABS_HAT0X, 1),
    }

    def __init__(self, config: ControllerConfig) -> None:
        # Normalized state, same keys as ViGEmInterface.current_values
        self.current_values: Dict[str, float] = {
            "left_x": 0.0, "left_y": 0.0,
            "right_x": 0.0, "right_y": 0.0,
            "left_trigger": 0.0, "right_trigger": 0.0,
        }
        super().__init__(config)

    @property
    def gamepad(self) -> Optional[_UInputDevice]:
        """The underlying device (``None`` in simulation mode).

        Mirrors ``ViGEmInterface.gamepad`` so bridge truthiness checks work.
        """
        return self.device

    def _device_spec(self):
        stick: AxisSpec = (-32768, 32767, 16, 128)
        trigger: AxisSpec = (0, 255, 0, 0)
        hat: AxisSpec = (-1, 1, 0, 0)
        axes: Dict[int, AxisSpec] = {
            ABS_X: stick, ABS_Y: stick, ABS_RX: stick, ABS_RY: stick,
            ABS_Z: trigger, ABS_RZ: trigger,
            ABS_HAT0X: hat, ABS_HAT0Y: hat,
        }
        keys = list(self.BUTTON_MAP.values()) + [BTN_MODE]
        return (
            XBOX360_NAME, XBOX360_VENDOR, XBOX360_PRODUCT, XBOX360_VERSION,
            BUS_USB, keys, axes, {},
        )

    # ---- helpers ---------------------------------------------------------
    def _stick_raw(self, value: float, invert: bool = False) -> int:
        v = _clamp(value, -1.0, 1.0)
        if invert:
            v = -v
        return int(round(v * self.STICK_MAX))

    def _trigger_raw(self, value: float) -> int:
        return int(round(_clamp(value, 0.0, 1.0) * self.TRIGGER_MAX))

    def _reset_axes(self) -> None:
        ok = self._emit([
            (EV_ABS, ABS_X, 0), (EV_ABS, ABS_Y, 0),
            (EV_ABS, ABS_RX, 0), (EV_ABS, ABS_RY, 0),
            (EV_ABS, ABS_Z, 0), (EV_ABS, ABS_RZ, 0),
            (EV_ABS, ABS_HAT0X, 0), (EV_ABS, ABS_HAT0Y, 0),
        ])
        if ok:
            for key in self.current_values:
                self.current_values[key] = 0.0

    # ---- ViGEm-compatible API -------------------------------------------
    def set_left_stick(self, x: float, y: float) -> bool:
        """Set the left stick (``-1..1`` each, ``+y`` is up)."""
        x, y = _clamp(x, -1, 1), _clamp(y, -1, 1)
        if self._emit([(EV_ABS, ABS_X, self._stick_raw(x)),
                       (EV_ABS, ABS_Y, self._stick_raw(y, invert=True))]):
            self.current_values["left_x"], self.current_values["left_y"] = x, y
            return True
        return False

    def set_right_stick(self, x: float, y: float) -> bool:
        """Set the right stick (``-1..1`` each, ``+y`` is up)."""
        x, y = _clamp(x, -1, 1), _clamp(y, -1, 1)
        if self._emit([(EV_ABS, ABS_RX, self._stick_raw(x)),
                       (EV_ABS, ABS_RY, self._stick_raw(y, invert=True))]):
            self.current_values["right_x"], self.current_values["right_y"] = x, y
            return True
        return False

    def set_left_trigger(self, value: float) -> bool:
        """Set the left trigger (``0..1``)."""
        value = _clamp(value, 0.0, 1.0)
        if self._emit([(EV_ABS, ABS_Z, self._trigger_raw(value))]):
            self.current_values["left_trigger"] = value
            return True
        return False

    def set_right_trigger(self, value: float) -> bool:
        """Set the right trigger (``0..1``)."""
        value = _clamp(value, 0.0, 1.0)
        if self._emit([(EV_ABS, ABS_RZ, self._trigger_raw(value))]):
            self.current_values["right_trigger"] = value
            return True
        return False

    def _hat_value(self, axis: int) -> int:
        neg = pos = 0
        for bid, (code, direction) in self.DPAD_MAP.items():
            if code == axis and self.button_states.get(bid, False):
                if direction < 0:
                    neg = -1
                else:
                    pos = 1
        return neg + pos

    def set_button(self, button_id: int, pressed: bool) -> bool:
        """Press or release a button by Nimbus ID (``1..14``)."""
        button_id = int(button_id)
        pressed = bool(pressed)
        if button_id in self.BUTTON_MAP:
            if self._emit([(EV_KEY, self.BUTTON_MAP[button_id], 1 if pressed else 0)]):
                self.button_states[button_id] = pressed
                return True
            return False
        if button_id in self.DPAD_MAP:
            code, _direction = self.DPAD_MAP[button_id]
            self.button_states[button_id] = pressed
            if self._emit([(EV_ABS, code, self._hat_value(code))]):
                return True
            return False
        if button_id not in self._warned_buttons:
            self._warned_buttons.add(button_id)
            print(f"Unknown button ID: {button_id} (Xbox device supports 1-14)")
        return False

    def update_axis(self, axis: str, value: float) -> bool:
        """vJoy-style single-axis update (``x/y/rx/ry`` sticks, ``z/rz`` triggers)."""
        axis = axis.lower()
        value = _clamp(value, -1.0, 1.0)
        if axis == "x":
            return self.set_left_stick(value, self.current_values["left_y"])
        if axis == "y":
            return self.set_left_stick(self.current_values["left_x"], value)
        if axis == "rx":
            return self.set_right_stick(value, self.current_values["right_y"])
        if axis == "ry":
            return self.set_right_stick(self.current_values["right_x"], value)
        if axis == "z":
            return self.set_left_trigger((value + 1.0) / 2.0)
        if axis == "rz":
            return self.set_right_trigger((value + 1.0) / 2.0)
        print(f"Unknown axis: {axis}")
        return False

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status["current_values"] = self.current_values.copy()
        status["vigem_available"] = False
        return status


class UInputJoystickInterface(_UInputBase):
    """Generic 8-axis virtual joystick via ``uinput``.

    Drop-in replacement for :class:`~src.vjoy_interface.VJoyInterface` on
    Linux. Axes ``x, y, z, rx, ry, rz, sl0, sl1`` map to
    ``ABS_X .. ABS_RZ, ABS_THROTTLE, ABS_RUDDER`` with the vJoy value range
    (``0..vjoy.axis_range``). Buttons ``1..56`` are supported (vJoy allows
    128, but evdev only has 56 generic joystick button codes).

    Args:
        config: Configuration manager instance.
    """

    device_type = "Virtual Joystick (uinput)"

    AXIS_MAP: Dict[str, int] = {
        "x": ABS_X, "y": ABS_Y, "z": ABS_Z,
        "rx": ABS_RX, "ry": ABS_RY, "rz": ABS_RZ,
        "sl0": ABS_THROTTLE, "sl1": ABS_RUDDER,
    }

    def __init__(self, config: ControllerConfig) -> None:
        self.device_id = config.get("vjoy.device_id", 1)
        self.axis_range = int(config.get("vjoy.axis_range", 32767))
        # 0.0..1.0 with 0.5 at center, same as VJoyInterface.current_values
        self.current_values: Dict[str, float] = {axis: 0.5 for axis in self.AXIS_MAP}
        super().__init__(config)

    def _device_spec(self):
        center = self.axis_range // 2
        axis: AxisSpec = (0, self.axis_range, 0, 0)
        axes: Dict[int, AxisSpec] = {code: axis for code in self.AXIS_MAP.values()}
        initial = {code: center for code in self.AXIS_MAP.values()}
        keys = [self._button_code(i) for i in range(1, JOYSTICK_MAX_BUTTONS + 1)]
        return (
            JOYSTICK_NAME, JOYSTICK_VENDOR, JOYSTICK_PRODUCT, JOYSTICK_VERSION,
            BUS_VIRTUAL, keys, axes, initial,
        )

    @staticmethod
    def _button_code(button_id: int) -> int:
        """Map a 1-based Nimbus button ID to an evdev key code."""
        if 1 <= button_id <= 16:
            return BTN_JOYSTICK + (button_id - 1)
        if 17 <= button_id <= JOYSTICK_MAX_BUTTONS:
            return BTN_TRIGGER_HAPPY + (button_id - 17)
        raise ValueError(f"button {button_id} out of range 1..{JOYSTICK_MAX_BUTTONS}")

    def _reset_axes(self) -> None:
        center = self.axis_range // 2
        if self._emit([(EV_ABS, code, center) for code in self.AXIS_MAP.values()]):
            for axis in self.current_values:
                self.current_values[axis] = 0.5

    # ---- vJoy-compatible API --------------------------------------------
    def update_axis(self, axis: str, value: float) -> bool:
        """Update one axis from a normalized ``-1..1`` value."""
        axis = axis.lower()
        code = self.AXIS_MAP.get(axis)
        if code is None:
            print(f"Unknown axis: {axis}")
            return False
        value = _clamp(value, -1.0, 1.0)
        raw = int(_clamp(self.config.get_vjoy_value(value), 0, self.axis_range))
        if self._emit([(EV_ABS, code, raw)]):
            self.current_values[axis] = (value + 1.0) / 2.0
            return True
        return False

    def update_joystick(self, left_x: float, left_y: float,
                        right_x: float, right_y: float) -> bool:
        """Update both stick pairs (``x/y`` and ``rx/ry``) in one frame."""
        events = []
        for axis, value in (("x", left_x), ("y", left_y), ("rx", right_x), ("ry", right_y)):
            value = _clamp(value, -1.0, 1.0)
            raw = int(_clamp(self.config.get_vjoy_value(value), 0, self.axis_range))
            events.append((EV_ABS, self.AXIS_MAP[axis], raw))
        if self._emit(events):
            for axis, value in (("x", left_x), ("y", left_y), ("rx", right_x), ("ry", right_y)):
                self.current_values[axis] = (_clamp(value, -1.0, 1.0) + 1.0) / 2.0
            return True
        return False

    def set_button(self, button_id: int, pressed: bool) -> bool:
        """Press or release a button by 1-based ID (``1..56``)."""
        button_id = int(button_id)
        pressed = bool(pressed)
        try:
            code = self._button_code(button_id)
        except ValueError:
            if button_id not in self._warned_buttons:
                self._warned_buttons.add(button_id)
                print(f"Button {button_id} unavailable: uinput joystick supports 1-{JOYSTICK_MAX_BUTTONS}")
            return False
        if self._emit([(EV_KEY, code, 1 if pressed else 0)]):
            self.button_states[button_id] = pressed
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status["device_id"] = self.device_id
        status["current_values"] = self.current_values.copy()
        status["vjoy_available"] = False
        return status
