"""
Nimbus stick-shaping probe: the real app, synthesized input, ViGEm output.

Runs the QML application in-process, the way ``probe_nimbus_relay_windows.py``
does, with a throwaway profile whose widgets have known settings. Each check
drives a widget with synthesized Qt mouse events (what a pointer at the
console produces) and reads what the bridge sent to the ViGEm pad. It is
layer 2 of the verification plan in ``docs/vision/AIM_ASSISTANCE.md``
section 12: the end-to-end check that raw geometry in the QML becomes the
right driver output. Expected values come from the bridge's own resolved
parameters through ``shape_magnitude``, so these checks are about wiring;
the formula itself is covered by ``test_stick_shaping.py``.

  S0  a plain button still presses and releases a gamepad button
  S1  floor: a 2 px drag on the aim stick reads the anti-deadzone plus buffer
  S2  ceiling: a full drag reads the widget's own cap (0.95), not 0.90
  S3  travel: 80 px on the 160 px-travel stick is well under 80 px on the
      auto stick (the ruler test, in code)
  S4  radial: a diagonal drag has the magnitude of a cardinal one; screen
      down is controller down
  S5  release recentres the stick exactly
  S6  precision: the modifier button sets the bridge modifier, and holding
      the modifier re-shapes a held stick without a new pointer event
  S7  tremor: a filtered stick lags its input, rises with more samples, and
      a release still reads exactly 0
  S8  triggers: the RT slider reaches 1.00 pulled and 0.00 released
  S9  vJoy output has no anti-deadzone by default; switching back restores it
  S10 lock mode drives the stick and unlock centres it
  S11 the config dialog: defaults, the bridge-drawn preview, the test pad
      driving the real stick, Apply writing the profile (screenshot saved to
      tests/probe_frames/shaping_dialog.png)
  S12 the value applied in S11 is what the bridge shapes with next

The probe writes ``nimbus_probe_shaping.json`` into the user profiles folder,
switches to it, and deletes it afterwards; ``controller_config.json`` is
restored either way. ViGEmBus must be installed (vJoy too, for S9). Safe to
run over TeamViewer: nothing needs the physical mouse. Lock mode (S10) moves
the real cursor onto the Nimbus window for a moment.

Run::

    venv\\Scripts\\python tests\\probe_stick_shaping_windows.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("Windows only")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QTimer, QUrl, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.bridge import ControllerBridge  # noqa: E402
from src.cloud_client import CloudClient  # noqa: E402
from src.config import ControllerConfig, shape_magnitude  # noqa: E402
from src.qt_qml_app import qml_path  # noqa: E402
from src.telemetry import TelemetryClient  # noqa: E402
from src.updater import UpdateChecker  # noqa: E402

PROFILE_ID = "nimbus_probe_shaping"
FRAMES = os.path.join(REPO, "tests", "probe_frames")
RESULTS: List[Dict[str, object]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "note": note})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {note}", flush=True)


def probe_profile() -> Dict[str, Any]:
    """The throwaway profile: every widget the checks need, with known settings."""
    return {
        "name": "Shaping probe",
        "description": "Throwaway profile written by tests/probe_stick_shaping_windows.py; safe to delete",
        "layout_type": "custom",
        "custom_layout": {
            "canvas_width": 1024, "canvas_height": 600, "grid_snap": 10, "show_grid": False,
            "widgets": [
                {"id": "auto_stick", "type": "joystick", "x": 30, "y": 110, "width": 200, "height": 200,
                 "label": "Auto", "mapping": {"axis_x": "x", "axis_y": "y"}},
                {"id": "aim_stick", "type": "joystick", "x": 270, "y": 110, "width": 200, "height": 200,
                 "label": "Aim", "mapping": {"axis_x": "rx", "axis_y": "ry"}, "travel_px": 160},
                {"id": "tremor_stick", "type": "joystick", "x": 510, "y": 110, "width": 200, "height": 200,
                 "label": "Tremor", "mapping": {"axis_x": "rx", "axis_y": "ry"}, "tremor_filter": 8},
                {"id": "rt", "type": "slider", "x": 270, "y": 30, "width": 200, "height": 50, "label": "RT",
                 "orientation": "horizontal", "mapping": {"axis": "rz"}},
                {"id": "btn_a", "type": "button", "x": 30, "y": 340, "width": 70, "height": 60, "label": "A",
                 "button_id": 1, "color": "#16a34a", "shape": "circle"},
                {"id": "btn_precision", "type": "button", "x": 130, "y": 340, "width": 100, "height": 60,
                 "label": "PREC", "button_id": 2, "modifier": "precision", "color": "#7c3aed", "shape": "rounded"},
            ],
        },
        "axis_mapping": {"left_x": "x", "left_y": "y", "right_x": "rx", "right_y": "ry",
                         "left_trigger": "z", "right_trigger": "rz"},
        "buttons": {"button_1": {"label": "A", "toggle_mode": False}, "button_2": {"label": "PREC", "toggle_mode": False}},
        "joystick_settings": {"sensitivity": 50.0, "deadzone": 10.0, "extremity_deadzone": 5.0},
        "rudder_settings": {"sensitivity": 50.0, "deadzone": 10.0, "extremity_deadzone": 5.0},
    }


# ---- running things on the Qt thread from the scenario thread ----------------
class _QtCall(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        fn()


_QT: Optional[_QtCall] = None


def on_qt(fn: Callable[[], Any], timeout: float = 5.0) -> Any:
    """Run ``fn`` on the Qt thread and return its result (or re-raise)."""
    assert _QT is not None
    done = threading.Event()
    box: Dict[str, Any] = {}

    def wrapper() -> None:
        try:
            box["r"] = fn()
        except Exception as exc:   # noqa: BLE001
            box["e"] = exc
        finally:
            done.set()

    _QT.call.emit(wrapper)
    if not done.wait(timeout):
        raise TimeoutError("the Qt thread did not answer in time")
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ---- items and synthesized input ------------------------------------------------
def visual_items(engine: QQmlApplicationEngine) -> List[QQuickItem]:
    """Every item in the window's visual tree (Repeater delegates are not QObject children)."""
    roots = engine.rootObjects()
    if not roots:
        return []
    out: List[QQuickItem] = []
    stack = [roots[0].contentItem()]
    while stack:
        item = stack.pop()
        out.append(item)
        stack.extend(item.childItems())
    return out


def find_item(engine: QQmlApplicationEngine, object_name: str) -> Optional[QQuickItem]:
    for item in visual_items(engine):
        if item.objectName() == object_name:
            return item
    return None


def centre(item: QQuickItem) -> QPointF:
    """The item's centre in window (scene) coordinates."""
    return item.mapToScene(QPointF(item.width() / 2.0, item.height() / 2.0))


def _send(win, ev_type, pos: QPointF, button, buttons) -> None:
    g = QPointF(win.mapToGlobal(pos.toPoint()))
    ev = QMouseEvent(ev_type, pos, pos, g, button, buttons, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(win, ev)


def press(win, pos: QPointF) -> None:
    _send(win, QEvent.Type.MouseButtonPress, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)


def move(win, pos: QPointF, held: bool = True) -> None:
    _send(win, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton,
          Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton)


def release(win, pos: QPointF) -> None:
    _send(win, QEvent.Type.MouseButtonRelease, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


def dblclick(win, pos: QPointF) -> None:
    _send(win, QEvent.Type.MouseButtonDblClick, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)


class Driver:
    """Synthesized pointer on the Nimbus window, called from the scenario thread."""

    def __init__(self, win, engine: QQmlApplicationEngine, bridge: ControllerBridge) -> None:
        self.win = win
        self.engine = engine
        self.bridge = bridge
        self._last_press: Dict[str, float] = {}

    def press_on(self, wid: str, pos: QPointF) -> None:
        """Press a widget, keeping successive presses on the same joystick more
        than the 400 ms triple-click window apart, so the probe never locks a
        stick by accident (a real user pressing that fast would, and should)."""
        gap = 0.45 - (time.monotonic() - self._last_press.get(wid, 0.0))
        if gap > 0:
            time.sleep(gap)
        on_qt(lambda: press(self.win, pos))
        self._last_press[wid] = time.monotonic()

    def item(self, name: str) -> QQuickItem:
        it = on_qt(lambda: find_item(self.engine, name))
        if it is None:
            raise RuntimeError(f"no item named {name!r} in the window")
        return it

    def widget(self, wid: str) -> QQuickItem:
        return self.item("widget_" + wid)

    # These helpers hop to the Qt thread exactly once. Nesting on_qt calls
    # deadlocks (the outer call holds the Qt thread while waiting for the
    # inner one), so the checks below use these instead of composing item()
    # with another hop.
    def centre_of(self, name: str, widget: bool = True) -> QPointF:
        key = ("widget_" + name) if widget else name
        return on_qt(lambda: centre(find_item(self.engine, key)))

    def prop(self, name: str, prop: str) -> Any:
        return on_qt(lambda: find_item(self.engine, name).property(prop))

    def set_prop(self, name: str, prop: str, value: Any) -> None:
        on_qt(lambda: find_item(self.engine, name).setProperty(prop, value))

    def click(self, pos: QPointF, gap: float = 0.03) -> None:
        on_qt(lambda: press(self.win, pos))
        time.sleep(gap)
        on_qt(lambda: release(self.win, pos))

    def drag(self, wid: str, dx: float, dy: float, steps: int = 4, hold: bool = False) -> QPointF:
        """Press at the widget's centre, move by (dx, dy) in steps, optionally release."""
        c = self.centre_of(wid)
        self.press_on(wid, c)
        time.sleep(0.02)
        for i in range(1, steps + 1):
            p = QPointF(c.x() + dx * i / steps, c.y() + dy * i / steps)
            on_qt(lambda p=p: move(self.win, p))
            time.sleep(0.02)
        if not hold:
            self.release_at(QPointF(c.x() + dx, c.y() + dy))
        return c

    def release_at(self, pos: QPointF) -> None:
        on_qt(lambda: release(self.win, pos))
        time.sleep(0.05)

    def pad(self) -> Dict[str, float]:
        return on_qt(lambda: dict(self.bridge._vigem.current_values))

    def buttons(self) -> Dict[int, bool]:
        return on_qt(lambda: dict(getattr(self.bridge._vigem, "button_states", {})))

    def params(self, wid: str) -> Dict[str, float]:
        return on_qt(lambda: self.bridge._widget_params(self.bridge._widget_shaping[wid]))

    def expect(self, wid: str, magnitude: float, gain: float = 1.0) -> float:
        return shape_magnitude(magnitude, gain=gain, **self.params(wid))


def auto_radius(item: QQuickItem) -> float:
    """DraggableWidget's effectiveRadius for a joystick: drawn radius less the thumb."""
    size = min(item.width(), item.height())
    return max(1.0, (size / 2.0 - 1.0) - size * 0.09)


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


# ---- the checks ------------------------------------------------------------------
def run_checks(d: Driver, config: ControllerConfig, profile_path: str) -> None:
    aim, auto, trem = "aim_stick", "auto_stick", "tremor_stick"

    # S0 plain button
    c = d.centre_of("btn_a")
    on_qt(lambda: press(d.win, c))
    time.sleep(0.05)
    down = d.buttons().get(1, False)
    on_qt(lambda: release(d.win, c))
    time.sleep(0.05)
    up = d.buttons().get(1, False)
    record("S0 plain button presses and releases gamepad button 1", down and not up, f"down={down} up={up}")

    # S1 floor
    d.drag(aim, 2, 0, steps=1, hold=True)
    rx = d.pad()["right_x"]
    exp = d.expect(aim, 2.0 / 160.0)
    d.release_at(d.centre_of(aim))
    record("S1 a 2 px drag on the aim stick lands on the anti-deadzone floor",
           near(rx, exp, 0.01) and rx >= 0.28, f"RX={rx:.3f} expected={exp:.3f} params={d.params(aim)}")

    # S2 ceiling
    d.drag(auto, 130, 0, hold=True)
    lx = d.pad()["left_x"]
    exp = d.expect(auto, 1.0)
    d.release_at(d.centre_of(auto))
    record("S2 a full drag reads the widget's own 0.95 cap", near(lx, exp, 0.01) and near(lx, 0.95, 0.01),
           f"LX={lx:.3f} expected={exp:.3f}")

    # S3 travel
    d.drag(aim, 80, 0, hold=True)
    rx80 = d.pad()["right_x"]
    d.release_at(d.centre_of(aim))
    d.drag(auto, 80, 0, hold=True)
    lx80 = d.pad()["left_x"]
    d.release_at(d.centre_of(auto))
    auto_item = d.widget(auto)
    r_auto = on_qt(lambda: auto_radius(auto_item))
    exp_aim = d.expect(aim, 80.0 / 160.0)
    exp_auto = d.expect(auto, min(1.0, 80.0 / r_auto))
    record("S3 80 px on the 160 px stick is half the deflection of 80 px on the auto stick",
           near(rx80, exp_aim, 0.02) and near(lx80, exp_auto, 0.02) and rx80 < lx80 - 0.2,
           f"aim RX={rx80:.3f} (expected {exp_aim:.3f}) auto LX={lx80:.3f} (expected {exp_auto:.3f}, "
           f"auto radius {r_auto:.0f} px)")

    # S4 radial
    d.drag(aim, 60, 60, hold=True)
    v = d.pad()
    rx_d, ry_d = v["right_x"], v["right_y"]
    d.release_at(d.centre_of(aim))
    mag_d = math.hypot(rx_d, ry_d)
    exp_d = d.expect(aim, math.hypot(60, 60) / 160.0)
    d.drag(aim, math.hypot(60, 60), 0, hold=True)
    rx_c = d.pad()["right_x"]
    d.release_at(d.centre_of(aim))
    record("S4 a diagonal drag has the magnitude of a cardinal one and screen-down is controller-down",
           near(mag_d, exp_d, 0.02) and near(mag_d, rx_c, 0.02) and ry_d < 0 and mag_d <= 1.0,
           f"diagonal=({rx_d:+.3f},{ry_d:+.3f}) |v|={mag_d:.3f} cardinal RX={rx_c:.3f} expected={exp_d:.3f}")

    # S5 release
    v = d.pad()
    record("S5 release recentres exactly", v["right_x"] == 0.0 and v["right_y"] == 0.0 and v["left_x"] == 0.0,
           f"RX={v['right_x']} RY={v['right_y']} LX={v['left_x']}")

    # S6 precision
    c = d.centre_of("btn_precision")
    on_qt(lambda: press(d.win, c))
    time.sleep(0.05)
    mod_down = on_qt(lambda: d.bridge.isModifierActive("precision"))
    on_qt(lambda: release(d.win, c))
    time.sleep(0.05)
    mod_up = on_qt(lambda: d.bridge.isModifierActive("precision"))
    d.drag(aim, 80, 0, hold=True)
    rx0 = d.pad()["right_x"]
    on_qt(lambda: d.bridge.setModifier("precision", True))
    rx1 = d.pad()["right_x"]
    on_qt(lambda: d.bridge.setModifier("precision", False))
    rx2 = d.pad()["right_x"]
    d.release_at(d.centre_of(aim))
    gain = float(on_qt(lambda: d.bridge._widget_shaping[aim].get("precision_gain", 0.25)))
    exp1 = d.expect(aim, 0.5, gain=gain)
    record("S6 the precision button sets the modifier, and holding it re-shapes a held stick at once",
           mod_down and not mod_up and near(rx1, exp1, 0.01) and near(rx2, rx0, 0.001) and rx1 < rx0 - 0.1,
           f"button held={mod_down} released={mod_up}; RX {rx0:.3f} -> {rx1:.3f} (expected {exp1:.3f}) -> {rx2:.3f}")

    # S7 tremor
    c = d.centre_of(trem)
    d.press_on(trem, c)
    time.sleep(0.02)
    seq: List[float] = []
    for i, px in enumerate((80, 81, 80, 81, 80)):
        p = QPointF(c.x() + px, c.y())
        on_qt(lambda p=p: move(d.win, p))
        time.sleep(0.02)
        seq.append(d.pad()["right_x"])
    d.release_at(QPointF(c.x() + 80, c.y()))
    after = d.pad()["right_x"]
    full = d.expect(trem, 1.0)
    record("S7 a tremor-filtered stick lags, rises with more samples, and a release still reads 0",
           seq[0] < full - 0.2 and all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1))
           and seq[-1] > seq[0] and after == 0.0,
           f"RX per sample={[round(s, 3) for s in seq]} unfiltered full={full:.3f} after release={after}")

    # S7b a modifier toggle rescales a filtered stick without moving it.
    # Regression for the review finding that replaying _last_raw through
    # _drive_stick stepped the EMA again: with precision_gain 1.0 the toggle
    # should change nothing at all, and the filter state should not advance.
    c = d.centre_of(trem)
    d.press_on(trem, c)
    time.sleep(0.02)
    on_qt(lambda: move(d.win, QPointF(c.x() + 80, c.y())))
    time.sleep(0.02)
    saved_gain = on_qt(lambda: d.bridge._widget_shaping[trem].get("precision_gain"))
    on_qt(lambda: d.bridge._widget_shaping[trem].__setitem__("precision_gain", 1.0))
    before = d.pad()["right_x"]
    ema_before = on_qt(lambda: d.bridge._ema.get(trem))
    on_qt(lambda: d.bridge.setModifier("precision", True))
    during = d.pad()["right_x"]
    on_qt(lambda: d.bridge.setModifier("precision", False))
    after_toggle = d.pad()["right_x"]
    ema_after = on_qt(lambda: d.bridge._ema.get(trem))
    on_qt(lambda: d.bridge._widget_shaping[trem].__setitem__("precision_gain", saved_gain))
    d.release_at(QPointF(c.x() + 80, c.y()))
    record("S7b toggling a gain-1.0 modifier leaves a filtered stick and its filter state untouched",
           near(during, before, 1e-6) and near(after_toggle, before, 1e-6)
           and ema_before is not None and ema_after is not None
           and near(ema_before[0], ema_after[0], 1e-9) and near(ema_before[1], ema_after[1], 1e-9),
           f"RX {before:.4f} -> {during:.4f} -> {after_toggle:.4f}; "
           f"EMA {tuple(round(v, 6) for v in ema_before)} -> {tuple(round(v, 6) for v in ema_after)}")

    # S7c reloading the widget cache clears latched modifiers. Regression for
    # the review finding that a latched precision survived a profile switch
    # into a layout with no button to unlatch it.
    on_qt(lambda: d.bridge.setModifier("precision", True))
    latched = on_qt(lambda: d.bridge.isModifierActive("precision"))
    on_qt(lambda: d.bridge._reload_widget_shaping())
    still_latched = on_qt(lambda: d.bridge.isModifierActive("precision"))
    record("S7c a widget-cache reload clears a latched modifier",
           latched and not still_latched,
           f"latched before reload={latched} after={still_latched}")

    # S8 RT slider (hold mode: presses jump the value; the widget keeps it)
    rt = d.widget("rt")
    right = on_qt(lambda: rt.mapToScene(QPointF(rt.width() - 9, rt.height() - 18)))
    left = on_qt(lambda: rt.mapToScene(QPointF(9, rt.height() - 18)))
    d.click(right)
    pulled = d.pad()["right_trigger"]
    d.click(left)
    released = d.pad()["right_trigger"]
    record("S8 the RT slider reads 1.00 pulled and 0.00 released", pulled >= 0.98 and released <= 0.02,
           f"pulled={pulled:.3f} released={released:.3f}")

    # S9 vJoy: no anti-deadzone by default
    mode_before = on_qt(lambda: d.bridge.getOutputMode())
    on_qt(lambda: d.bridge.setOutputMode("vjoy"))
    time.sleep(0.5)
    mode_vjoy = on_qt(lambda: d.bridge.getOutputMode())
    vjoy_ok = mode_vjoy == "vjoy" and on_qt(lambda: bool(d.bridge._vjoy and d.bridge._vjoy.is_connected))
    if vjoy_ok:
        d.drag(aim, 2, 0, steps=1, hold=True)
        time.sleep(0.9)                          # the vJoy path is smoothed at 60 Hz
        vrx = float(on_qt(lambda: d.bridge._vjoy.current_values.get("rx", 0.5)))
        adz_vjoy = d.params(aim)["anti_deadzone"]
        d.release_at(d.centre_of(aim))
        time.sleep(0.9)
        on_qt(lambda: d.bridge.setOutputMode("vigem"))
        time.sleep(0.5)
        adz_back = d.params(aim)["anti_deadzone"]
        record("S9 vJoy output has no anti-deadzone by default and switching back restores it",
               near(vrx, 0.5, 0.03) and adz_vjoy == 0.0 and adz_back > 0.2
               and on_qt(lambda: d.bridge.getOutputMode()) == "vigem",
               f"vJoy RX (0.5 = centre)={vrx:.3f} default adz vjoy={adz_vjoy} vigem={adz_back:.3f}")
    else:
        on_qt(lambda: d.bridge.setOutputMode(mode_before))
        record("S9 vJoy output has no anti-deadzone by default", False,
               f"could not switch to vJoy (mode={mode_vjoy}); is vJoy installed and device 1 free?")

    # S10 lock mode (three fast clicks are the point here)
    time.sleep(0.5)
    aim_item = d.widget(aim)
    c = on_qt(lambda: centre(aim_item))
    for _ in range(3):
        d.click(c, gap=0.02)
        time.sleep(0.04)
    time.sleep(0.35)                              # the warp safety timer clears _warping
    locked = bool(on_qt(lambda: aim_item.property("joystickLocked")))
    on_qt(lambda: move(d.win, QPointF(c.x() + 20, c.y()), held=False))
    time.sleep(0.1)
    rx_lock = d.pad()["right_x"]
    for _ in range(3):
        d.click(c, gap=0.02)
        time.sleep(0.04)
    time.sleep(0.2)
    unlocked = not bool(on_qt(lambda: aim_item.property("joystickLocked")))
    rx_unlock = d.pad()["right_x"]
    record("S10 lock mode drives the stick from a hover offset and unlock centres it",
           locked and rx_lock > 0.9 and unlocked and rx_unlock == 0.0,
           f"locked={locked} RX while locked={rx_lock:.3f} unlocked={unlocked} RX after={rx_unlock}")

    # S11 the config dialog
    layout = d.item("customLayout")
    on_qt(lambda: layout.setProperty("editMode", True))
    time.sleep(0.5)
    edit_on = bool(d.prop("widget_" + aim, "editMode"))
    dialog = d.item("widgetConfigDialog")
    c = d.centre_of(aim)
    # QTest's double-click goes in at the platform layer, the path real input
    # takes. (A MouseButtonDblClick sent straight to the window does not
    # reach QML's doubleClicked: measured 2026-09-06.)
    opened_by = "QTest double-click"
    try:
        from PySide6.QtTest import QTest
        on_qt(lambda: QTest.mouseDClick(d.win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                        c.toPoint()))
        time.sleep(0.6)
    except Exception as exc:   # noqa: BLE001
        opened_by = f"QTest failed: {exc}"
    if not bool(on_qt(lambda: dialog.property("visible"))):
        # Fallback: call the dialog's own QML function, so its contents are
        # still tested when the double-click path is broken
        try:
            from PySide6.QtCore import QMetaObject, Q_ARG
            on_qt(lambda: QMetaObject.invokeMethod(dialog, "openForWidget", Qt.ConnectionType.DirectConnection,
                                                   Q_ARG("QVariant", aim)))
            time.sleep(0.6)
            opened_by = "direct call (the double-click did not open it)"
        except Exception as exc:   # noqa: BLE001
            opened_by = f"nothing worked: {exc}"
    visible = bool(on_qt(lambda: dialog.property("visible")))
    target = on_qt(lambda: dialog.property("targetWidgetId"))
    adz = float(d.prop("antiDzSlider", "value"))
    travel = float(d.prop("travelSlider", "value"))
    prec = float(d.prop("precisionSlider", "value"))
    curve = d.item("responseCurve")
    on_qt(lambda: curve.requestPaint())
    time.sleep(0.5)
    floor_v = float(on_qt(lambda: curve.property("floorValue")))
    ceil_v = float(on_qt(lambda: curve.property("ceilingValue")))
    exp_floor = d.expect(aim, 0.01)
    exp_adz = d.params(aim)["anti_deadzone"] * 100.0
    record("S11a double-click in edit mode opens the dialog with the resolved defaults",
           visible and target == aim and near(adz, exp_adz, 0.1) and travel == 160 and prec == 25
           and "double-click" in opened_by and "did not" not in opened_by,
           f"edit mode on widget={edit_on}; opened by {opened_by}; visible={visible} target={target} "
           f"Anti-DZ={adz:.1f} (expected {exp_adz:.1f}) travel={travel:.0f} precision={prec:.0f}")
    record("S11b the bridge-drawn preview reports the floor and the ceiling",
           near(floor_v, exp_floor, 0.01) and near(ceil_v, 0.95, 0.001),
           f"floor={floor_v:.3f} (expected {exp_floor:.3f}) ceiling={ceil_v:.3f}")
    # scroll the dialog to its bottom so the test pad is inside the Flickable's clip
    flick = d.item("configFlickable")
    on_qt(lambda: flick.setProperty("contentY", max(0.0, float(flick.property("contentHeight")) - float(flick.property("height")))))
    time.sleep(0.3)
    os.makedirs(FRAMES, exist_ok=True)
    shot = os.path.join(FRAMES, "shaping_dialog.png")
    on_qt(lambda: d.win.grabWindow().save(shot))
    pad = d.item("testPad")
    pc = on_qt(lambda: centre(pad))
    pr = float(on_qt(lambda: pad.property("padRadius")))
    on_qt(lambda: press(d.win, QPointF(pc.x() + 30, pc.y())))
    time.sleep(0.05)
    out_mag = float(on_qt(lambda: pad.property("outMag")))
    rx_pad_off = d.pad()["right_x"]
    on_qt(lambda: release(d.win, QPointF(pc.x() + 30, pc.y())))
    time.sleep(0.05)
    exp_pad = shape_magnitude(min(1.0, 30.0 / pr), **{k: v for k, v in d.params(aim).items()})
    sw = d.item("testDriveSwitch")
    d.click(on_qt(lambda: centre(sw)))
    time.sleep(0.05)
    drive_on = bool(on_qt(lambda: sw.property("checked")))
    on_qt(lambda: press(d.win, QPointF(pc.x() + 30, pc.y())))
    time.sleep(0.05)
    rx_pad_on = d.pad()["right_x"]
    on_qt(lambda: release(d.win, QPointF(pc.x() + 30, pc.y())))
    time.sleep(0.05)
    rx_pad_rel = d.pad()["right_x"]
    record("S11c the test pad reports the shaped vector and, with Drive on, moves the real stick",
           near(out_mag, exp_pad, 0.01) and rx_pad_off == 0.0 and drive_on and near(rx_pad_on, exp_pad, 0.01)
           and rx_pad_rel == 0.0,
           f"pad out={out_mag:.3f} (expected {exp_pad:.3f}) RX drive off={rx_pad_off} on={rx_pad_on:.3f} "
           f"released={rx_pad_rel}")
    # Apply a new anti-deadzone of 10% and check it reached the profile on disk
    d.set_prop("antiDzSlider", "value", 10.0)
    time.sleep(0.1)
    d.click(d.centre_of("applyConfigButton", widget=False))
    time.sleep(0.6)
    hidden = not bool(on_qt(lambda: dialog.property("visible")))
    with open(profile_path, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    saved_w = next((w for w in saved["custom_layout"]["widgets"] if w["id"] == aim), {})
    saved_adz = saved_w.get("anti_deadzone")
    record("S11d Apply closes the dialog and writes the profile",
           hidden and saved_adz is not None and near(float(saved_adz), 0.10, 1e-6)
           and saved_w.get("travel_px") == 160,
           f"dialog hidden={hidden} saved anti_deadzone={saved_adz} travel_px={saved_w.get('travel_px')}")
    on_qt(lambda: layout.setProperty("editMode", False))
    time.sleep(0.3)

    # S12 the applied value is what the bridge shapes with
    d.drag(aim, 2, 0, steps=1, hold=True)
    rx = d.pad()["right_x"]
    params_now = d.params(aim)
    exp = shape_magnitude(2.0 / 160.0, **params_now)
    d.release_at(d.centre_of(aim))
    record("S12 the applied anti-deadzone is what the bridge shapes with next",
           near(params_now["anti_deadzone"], 0.10, 1e-6) and near(rx, exp, 0.01) and rx < 0.2,
           f"RX={rx:.3f} expected={exp:.3f} bridge adz={params_now['anti_deadzone']:.3f}")


# ---- main ---------------------------------------------------------------------
def main() -> int:
    global _QT
    config_path = os.path.join(REPO, "controller_config.json")
    config_backup = open(config_path, "rb").read() if os.path.exists(config_path) else None

    app = QApplication(sys.argv)
    app.setApplicationName("Nimbus Adaptive Controller")
    _QT = _QtCall()
    config = ControllerConfig()
    original_profile = config.get_current_profile()
    profile_path = os.path.join(config.get_user_profiles_path(), f"{PROFILE_ID}.json")
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(probe_profile(), fh, indent=4)
    print(f"[probe] wrote {profile_path}", flush=True)

    bridge = ControllerBridge(config)
    telemetry = TelemetryClient(config)
    cloud = CloudClient(config)
    updater = UpdateChecker(config)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", bridge)
    engine.rootContext().setContextProperty("config", config)
    engine.rootContext().setContextProperty("telemetry", telemetry)
    engine.rootContext().setContextProperty("cloud", cloud)
    engine.rootContext().setContextProperty("updater", updater)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        print("QML failed to load")
        return 2

    exit_code = {"code": 1}

    def scenario() -> None:
        try:
            for _ in range(100):                     # Main.qml calls controller.setWindow(root)
                if bridge._window is not None:
                    break
                time.sleep(0.1)
            if bridge._window is None:
                record("S00 app window", False, "the QML window never registered with the bridge")
                return
            on_qt(lambda: (bridge._window.setPosition(1100, 80), bridge._window.raise_()))
            if not on_qt(lambda: bridge._is_controller_connected()) or bridge.getOutputMode() != "vigem":
                record("S00 ViGEm output", False,
                       f"connected={bridge._is_controller_connected()} mode={bridge.getOutputMode()}; "
                       "the probe needs a ViGEm pad")
                return
            on_qt(lambda: bridge.switchProfile(PROFILE_ID))
            time.sleep(1.2)
            d = Driver(bridge._window, engine, bridge)
            win_w, win_h = on_qt(lambda: (bridge._window.width(), bridge._window.height()))
            missing = [w["id"] for w in probe_profile()["custom_layout"]["widgets"]
                       if on_qt(lambda w=w: find_item(engine, "widget_" + w["id"])) is None]
            record("S00 the probe profile is loaded with every widget on screen", not missing,
                   f"window {win_w}x{win_h}; missing={missing}")
            if missing:
                return
            run_checks(d, config, profile_path)
        except Exception as exc:   # noqa: BLE001
            import traceback
            traceback.print_exc()
            record("scenario crashed", False, f"{type(exc).__name__}: {exc}")
        finally:
            try:
                on_qt(lambda: bridge.setModifier("precision", False))
                if bridge._vigem:
                    on_qt(lambda: bridge._vigem._reset_axes())
                on_qt(lambda: bridge.switchProfile(original_profile))
            except Exception:
                pass
            try:
                os.remove(profile_path)
                print(f"[probe] removed {profile_path}", flush=True)
            except OSError:
                pass
            failed = [r for r in RESULTS if not r["ok"]]
            print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
            exit_code["code"] = 1 if failed or not RESULTS else 0
            on_qt(app.quit)

    QTimer.singleShot(1500, lambda: threading.Thread(target=scenario, daemon=True, name="ProbeScenario").start())
    try:
        app.exec()
    finally:
        try:
            telemetry.shutdown()
        except Exception:
            pass
        if config_backup is not None:
            with open(config_path, "wb") as fh:
                fh.write(config_backup)
            print("[probe] controller_config.json restored", flush=True)
    return exit_code["code"]


if __name__ == "__main__":
    sys.exit(main())
