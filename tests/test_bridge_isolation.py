"""
Functional tests for the bridge side of mouse isolation.

The isolation *devices* need a kernel filter on Windows or an evdev grab on
Linux, so they cannot run here. Everything above them can: the bridge decides
which implementation runs, where synthetic events are delivered, and what the
software cursor does. That is the layer every isolation defect found so far
has actually lived in, and until now nothing exercised it. A bug that made the
Windows cursor relay dead code passed the whole suite.

Covered here:

- the platform dispatch picks the relay or the software cursor, checked by
  running it rather than by reading the source
- synthetic events reach the window that should receive them
- a modal dialog receives them instead of the main window, which is what was
  broken for Axis, Joystick and Button Settings
- the cursor position is carried across when the target changes, instead of
  snapping to a corner
- the software cursor stays inside its window
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt, QPoint
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtWidgets import QApplication

from src.controller_output import ControllerOutput

# Same shim the other bridge tests use: the native modules are absent or
# useless off their platform, and this file is about the bridge, not them.
native_modules = {}
for module_name in ("src.vjoy_interface", "src.vigem_interface", "src.window_utils",
                    "src.borderless", "src.mouse_hider", "src.mouse_isolation_win",
                    "src.mouse_isolation"):
    native_modules[module_name] = types.ModuleType(module_name)
native_modules["src.vjoy_interface"].VJoyInterface = Mock
native_modules["src.vigem_interface"].ViGEmInterface = Mock
native_modules["src.vigem_interface"].VIGEM_AVAILABLE = True
native_modules["src.mouse_isolation_win"].MOUSE_ISOLATION_AVAILABLE = False
native_modules["src.mouse_isolation"].MOUSE_ISOLATION_AVAILABLE = False
previous_modules = {name: sys.modules.get(name) for name in native_modules}
sys.modules.update(native_modules)
try:
    from src import bridge as bridge_module
    from src.bridge import ControllerBridge
finally:
    for module_name, previous in previous_modules.items():
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


class MouseEventRecorder(QObject):
    """Counts the mouse events a window receives."""

    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
                            QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            self.events.append((obj, event.type()))
        return False

    def types(self):
        return [t for _, t in self.events]

    def windows(self):
        return [o for o, _ in self.events]


class BridgeIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = Mock()
        self.config.get.side_effect = lambda key, default=None: default
        self.config.get_layout_type.return_value = "custom"
        self.config.get_current_profile.return_value = "test"
        self.config.get_current_profile_data.return_value = {"custom_layout": {"widgets": []}}
        self.config.get_available_profiles.return_value = []
        self.config.is_builtin_profile.return_value = False
        self.services = types.SimpleNamespace(
            cloud=Mock(), updater=Mock(),
            telemetry=types.SimpleNamespace(analytics_enabled=False, crash_reports_enabled=False))
        self.output = ControllerOutput(self.config, Mock(return_value=Mock(is_connected=True)),
                                       Mock(return_value=Mock(is_connected=True)), True)
        self.bridge = ControllerBridge(self.config, output=self.output, services=self.services)
        self.addCleanup(self.bridge._smooth_timer.stop)

        self.window = QWindow()
        self.window.resize(400, 300)
        self.window.show()
        self.addCleanup(self.window.close)
        self.bridge._window = self.window
        self.bridge._iso_target = None

    # ---- the dispatch, checked by running it ----------------------------

    def test_dispatch_runs_the_relay_on_windows(self):
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", True), \
             patch.object(ControllerBridge, "_on_iso_motion_relay") as relay, \
             patch.object(ControllerBridge, "_on_iso_motion_sw") as software:
            self.bridge._on_iso_motion(3, 4)
        relay.assert_called_once_with(3, 4)
        software.assert_not_called()

    def test_dispatch_runs_the_software_cursor_elsewhere(self):
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False), \
             patch.object(ControllerBridge, "_on_iso_motion_relay") as relay, \
             patch.object(ControllerBridge, "_on_iso_motion_sw") as software:
            self.bridge._on_iso_motion(3, 4)
        software.assert_called_once_with(3, 4)
        relay.assert_not_called()

    def test_every_isolation_entry_point_dispatches(self):
        """No entry point may call one implementation unconditionally."""
        for name, args in (("_on_iso_motion", (1, 1)),
                           ("_on_iso_button", (0x110, True)),
                           ("_on_iso_wheel", (0, 1)),
                           ("_on_iso_stopped", ("requested",)),
                           ("_iso_send_mouse", (QEvent.Type.MouseMove, Qt.MouseButton.NoButton))):
            for relay_active in (True, False):
                wanted = f"{name}_relay" if relay_active else f"{name}_sw"
                other = f"{name}_sw" if relay_active else f"{name}_relay"
                with self.subTest(entry=name, relay=relay_active):
                    with patch.object(bridge_module, "_ISO_CURSOR_RELAY", relay_active), \
                         patch.object(ControllerBridge, wanted) as chosen, \
                         patch.object(ControllerBridge, other) as skipped:
                        getattr(self.bridge, name)(*args)
                    self.assertEqual(chosen.call_count, 1, f"{name} did not call {wanted}")
                    self.assertEqual(skipped.call_count, 0, f"{name} also called {other}")

    # ---- delivery -------------------------------------------------------

    def _isolate_software(self):
        """Put the bridge in the software-cursor isolation state."""
        self.bridge._iso_active = True
        self.bridge._iso_x = 10.0
        self.bridge._iso_y = 10.0
        self.bridge._iso_target = self.window

    def test_synthetic_events_reach_the_main_window(self):
        recorder = MouseEventRecorder()
        self.window.installEventFilter(recorder)
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False):
            self._isolate_software()
            self.bridge._on_iso_motion(5, 5)
        self.assertIn(QEvent.Type.MouseMove, recorder.types())
        self.assertIn(self.window, recorder.windows())

    def test_a_modal_dialog_receives_the_events_instead(self):
        """The defect: settings dialogs could not be clicked while isolated."""
        dialog = QWindow()
        dialog.resize(200, 150)
        dialog.setModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        self.addCleanup(dialog.close)
        if QGuiApplication.modalWindow() is not dialog:
            self.skipTest("this platform plugin does not report a modal QWindow")

        recorder = MouseEventRecorder()
        dialog.installEventFilter(recorder)
        main_recorder = MouseEventRecorder()
        self.window.installEventFilter(main_recorder)

        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False):
            self._isolate_software()
            self.bridge._on_iso_motion(5, 5)

        self.assertEqual(self.bridge._iso_event_target(), dialog)
        self.assertIn(dialog, recorder.windows())
        self.assertNotIn(self.window, main_recorder.windows())

    def test_the_cursor_is_carried_across_a_target_change(self):
        """Opening a dialog must not snap the cursor to a corner."""
        dialog = QWindow()
        dialog.resize(200, 150)
        dialog.setModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        self.addCleanup(dialog.close)
        if QGuiApplication.modalWindow() is not dialog:
            self.skipTest("this platform plugin does not report a modal QWindow")

        self._isolate_software()
        self.bridge._iso_x, self.bridge._iso_y = 40.0, 30.0
        before_global = self.window.mapToGlobal(QPoint(40, 30))

        target = self.bridge._iso_retarget()
        self.assertEqual(target, dialog)
        after_global = dialog.mapToGlobal(QPoint(int(self.bridge._iso_x), int(self.bridge._iso_y)))
        self.assertEqual(before_global, after_global,
                         "the cursor moved on screen when the target changed")

    # ---- the software cursor -------------------------------------------

    def test_the_software_cursor_stays_inside_its_window(self):
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False):
            self._isolate_software()
            self.bridge._iso_set_cursor(10_000.0, 10_000.0)
            self.assertLessEqual(self.bridge._iso_x, self.window.width() - 1)
            self.assertLessEqual(self.bridge._iso_y, self.window.height() - 1)
            self.bridge._iso_set_cursor(-500.0, -500.0)
            self.assertGreaterEqual(self.bridge._iso_x, 0.0)
            self.assertGreaterEqual(self.bridge._iso_y, 0.0)

    def test_motion_moves_the_software_cursor(self):
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False):
            self._isolate_software()
            start = (self.bridge._iso_x, self.bridge._iso_y)
            self.bridge._on_iso_motion(7, 3)
            self.assertEqual((self.bridge._iso_x, self.bridge._iso_y),
                             (start[0] + 7, start[1] + 3))

    def test_motion_is_ignored_when_isolation_is_off(self):
        with patch.object(bridge_module, "_ISO_CURSOR_RELAY", False):
            self.bridge._iso_active = False
            self.bridge._iso_x = self.bridge._iso_y = 5.0
            self.bridge._on_iso_motion(20, 20)
            self.assertEqual((self.bridge._iso_x, self.bridge._iso_y), (5.0, 5.0))


if __name__ == "__main__":
    unittest.main()
