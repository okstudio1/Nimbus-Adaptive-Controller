"""
Regression checks for the two P1 defects reviewed on the Linux input path.

Pure Python: no Qt, no uinput, no hardware, and no Linux. Both modules now
import on any platform (``fcntl`` is guarded the way ``uinput_interface``
guards it), so these run in CI on ``windows-latest`` alongside the rest of the
fast suite.

The two defects, and what is checked here:

**A keep-alive pulse could undo a stick release.** The pulse read the stick,
wrote an offset, then wrote the value it had read back. A release landing
between the read and the restore was overwritten, leaving the stick deflected
with no input from the user. The fix makes the pulse emit transiently: it never
records a value as commanded, so a restore always re-emits what the user
currently asks for. Checked below by releasing the stick mid-pulse and mid-burst
and asserting the device ends centred.

**An absolute pointer could be grabbed and then ignored.** Any device with a
``mouseN`` handler was grabbed exclusively, including touchpads, but only
``EV_REL`` motion is translated. For a touchpad-only user that froze the desktop
pointer and Nimbus's own cursor, with no way to reach the control that releases
it. The fix reads the ``B: REL=``/``B: ABS=`` capability masks and declines a
device it cannot translate. Checked below against real ``/proc`` bitmask shapes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import controller_pulse
from src.mouse_isolation import _low_bits, list_input_devices, pointer_support
from src.uinput_interface import UInputXboxInterface

PASSES = 0
FAILS = 0


def check(label, condition):
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"  [PASS] {label}")
    else:
        FAILS += 1
        print(f"  [FAIL] {label}")


class FakeXbox(UInputXboxInterface):
    """An Xbox interface whose device writes are recorded instead of emitted.

    Subclasses the real class so the locking and the transient/commanded split
    under test are the shipping ones, not a reimplementation.
    """

    def __init__(self):
        self.emitted = []
        self.current_values = {"left_x": 0.0, "left_y": 0.0,
                               "right_x": 0.0, "right_y": 0.0,
                               "left_trigger": 0.0, "right_trigger": 0.0}
        import threading
        self._lock = threading.RLock()
        self.is_connected = True
        self.device = object()
        # Present so the real class's atexit shutdown path stays quiet.
        self.button_states = {}
        self.failsafe_active = False

    def _emit(self, events):
        self.emitted.append(tuple(events))
        return True

    def last_stick(self):
        """The (x, y) raw values of the most recent write."""
        if not self.emitted:
            return None
        ev = self.emitted[-1]
        return (ev[0][2], ev[1][2])


def test_pulse_cannot_undo_a_release():
    print("\nA pulse must not resurrect a released stick")

    iface = FakeXbox()
    iface.set_left_stick(0.6, 0.2)
    check("a normal write records the commanded value",
          iface.current_values["left_x"] == 0.6)

    iface.pulse_left_stick(0.08, 0.0)
    check("a pulse leaves the commanded value untouched",
          iface.current_values["left_x"] == 0.6)
    check("a pulse ends back at the commanded position",
          iface.last_stick() == (iface._stick_raw(0.6), iface._stick_raw(0.2, invert=True)))

    # The failing interleaving from the review: release, then let the pulse
    # that was already in flight finish.
    iface.set_left_stick(0.0, 0.0)
    iface.pulse_left_stick(0.08, 0.0)
    check("after a release, a later pulse restores centre and not the old value",
          iface.last_stick() == (0, 0))
    check("the release is still the commanded value",
          iface.current_values["left_x"] == 0.0 and iface.current_values["left_y"] == 0.0)


def test_burst_cannot_undo_a_release():
    print("\nA startup burst must not resurrect a released stick")

    iface = FakeXbox()
    iface.set_left_stick(0.5, 0.5)
    controller_pulse._send_burst(iface, count=2, delay=0)
    check("a burst leaves the commanded value untouched",
          iface.current_values["left_x"] == 0.5)
    check("a burst ends at the commanded position",
          iface.last_stick() == (iface._stick_raw(0.5), iface._stick_raw(0.5, invert=True)))

    # Release happens while the burst is running: the burst's own writes must
    # not have claimed authorship, so the restore sees the release.
    iface2 = FakeXbox()
    iface2.set_left_stick(0.7, 0.0)
    iface2.set_left_stick(0.0, 0.0)          # the user lets go
    controller_pulse._send_burst(iface2, count=2, delay=0)
    check("a burst after a release re-centres rather than restoring the old value",
          iface2.last_stick() == (0, 0))


def test_absolute_pointers_are_declined():
    print("\nA device whose motion cannot be translated must not be grabbed")

    mouse = {"name": "Logitech Mouse", "has_rel": True, "has_abs": False}
    ok, why = pointer_support(mouse)
    check("a relative mouse is grabbable", ok and why == "")

    touchpad = {"name": "SynPS/2 Synaptics TouchPad", "has_rel": False, "has_abs": True}
    ok, why = pointer_support(touchpad)
    check("an absolute touchpad is declined", not ok)
    check("the refusal explains it would freeze the pointer", "freeze the pointer" in why)

    silent = {"name": "Odd device", "has_rel": False, "has_abs": False}
    ok, why = pointer_support(silent)
    check("a pointer with no motion axes is declined", not ok)

    # Devices synthesised for an explicit node list carry no capability keys.
    # Those must stay grabbable, or passing --grab an event node would break.
    ok, _ = pointer_support({"name": "explicit node", "node": "/dev/input/event9"})
    check("a device with unknown capabilities is still allowed", ok)


def test_capability_mask_parsing():
    print("\nCapability masks are read the way /proc writes them")

    check("REL_X|REL_Y set is recognised", _low_bits("103") & 0x3 == 0x3)
    check("a mask with only REL_Y is not enough", _low_bits("2") & 0x3 != 0x3)
    # Masks are space separated, most significant word first, so bits 0-63 are
    # in the last word. A touchpad's ABS mask looks like this.
    check("the low word is taken from a multi-word mask",
          _low_bits("1000000000000000 0") == 0)
    check("a multi-word mask keeps its low bits", _low_bits("7 3") == 3)
    check("a malformed mask is treated as empty", _low_bits("") == 0)
    check("a non-hex mask is treated as empty", _low_bits("zz") == 0)


def test_listing_is_safe_off_linux():
    print("\nThe module degrades instead of raising when /proc is absent")
    devices = list_input_devices()
    check("listing returns a list rather than raising", isinstance(devices, list))


def main():
    test_pulse_cannot_undo_a_release()
    test_burst_cannot_undo_a_release()
    test_absolute_pointers_are_declined()
    test_capability_mask_parsing()
    test_listing_is_safe_off_linux()
    print(f"\n{PASSES}/{PASSES + FAILS} checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
