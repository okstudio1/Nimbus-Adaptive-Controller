"""Production startup and service UI with only external effects replaced."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_bridge_services import ControllerBridge, QApplication
from tests.test_updater_requests import FakeReply
from PySide6.QtCore import QObject, QTimer, QMetaObject, Qt, QPointF
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest

from src import qt_qml_app
from src.application_services import ApplicationServices
from src.cloud_client import CloudClient
from src.config import ControllerConfig
from src.profile_repository import ProfileRepository


class FakeOutput:
    def __init__(self, config):
        self.is_connected = True
        self.current_values = {}
        self.button_states = {}
        self.gamepad = None


class ApplicationStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_production_menu_privacy_update_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root_dir = Path(directory)
            bundled = root_dir / "bundled"
            bundled.mkdir()
            ProfileRepository(bundled, root_dir / "unused").save("adaptive_platform_2",
                {"name": "Default", "layout_type": "custom", "custom_layout": {"widgets": []}})
            config_path = root_dir / "settings.json"
            reply = FakeReply()
            network = Mock()
            network.get.return_value = reply
            failures = []
            observed = {}

            def exercise():
                try:
                    services = self.app.findChild(ApplicationServices)
                    bridge = self.app.findChild(ControllerBridge)
                    observed.update(services=services, bridge=bridge)
                    self.assertIsNotNone(services)
                    self.assertIsInstance(bridge._config, ControllerConfig)
                    window = bridge._window
                    window.setWidth(700)
                    window.setHeight(450)
                    privacy = window.findChild(QObject, "privacyDialog")
                    account = window.findChild(QObject, "accountDialog")
                    self.assertIsNotNone(privacy)
                    self.assertIsNotNone(account)
                    opened = QSignalSpy(privacy.opened)
                    menu = window.findChild(QObject, "privacyMenuItem")
                    QMetaObject.invokeMethod(menu, "triggered", Qt.ConnectionType.DirectConnection)
                    self.assertTrue(opened.count() or opened.wait(1000))
                    self.assertLessEqual(privacy.property("height"), window.height())
                    toggle = window.findChild(QQuickItem, "analyticsSwitch")
                    position = toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint()
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
                    self.assertTrue(services.telemetry.analytics_enabled)
                    self.assertTrue(bridge._config.get("telemetry.analytics_enabled"))
                    bridge.setAnalyticsEnabled(False)
                    self.assertFalse(toggle.property("checked"))
                    QMetaObject.invokeMethod(privacy, "close")
                    opened = QSignalSpy(account.opened)
                    QMetaObject.invokeMethod(window.findChild(QObject, "accountMenuItem"), "triggered")
                    self.assertTrue(opened.count() or opened.wait(1000))
                    self.assertLessEqual(account.property("height"), window.height())
                    QMetaObject.invokeMethod(account, "close")
                    network.get.assert_not_called()
                    updates = QSignalSpy(bridge.updateAvailable)
                    QMetaObject.invokeMethod(window.findChild(QObject, "checkUpdatesMenuItem"), "triggered")
                    def complete_request():
                        reply.data = b'{"latest":"999.0.0"}'
                        reply.finished.emit()
                    QTimer.singleShot(10, complete_request)
                    self.assertTrue(updates.count() or updates.wait(1000))
                    network.get.assert_called_once()
                    notice = window.findChild(QObject, "updateNotification")
                    self.assertTrue(notice.property("visible"))
                    self.assertEqual(notice.property("latestVersion"), "999.0.0")
                    window.close()
                except Exception as exc:
                    failures.append(exc)
                finally:
                    self.app.quit()

            config_constructor = ControllerConfig
            def create_config():
                return config_constructor(str(config_path))
            with patch.object(ControllerConfig, "_get_user_data_dir", return_value=root_dir / "data"), \
                    patch.object(ControllerConfig, "_get_bundled_profiles_dir", return_value=bundled), \
                    patch.object(qt_qml_app, "ControllerConfig", side_effect=create_config), \
                    patch.object(qt_qml_app, "_create_splash", return_value=None), \
                    patch.object(CloudClient, "_restore_session"), \
                    patch("src.cloud_client._get_cache_dir", return_value=root_dir / "cache"), \
                    patch("src.telemetry._get_telemetry_dir", return_value=root_dir), \
                    patch("src.bridge.VJoyInterface", FakeOutput), \
                    patch("src.bridge.ViGEmInterface", FakeOutput), \
                    patch("src.updater.QNetworkAccessManager", return_value=network), \
                    patch("httpx.post") as http_post, patch("httpx.get") as http_get:
                QTimer.singleShot(0, exercise)
                self.assertEqual(qt_qml_app.main(), 0)
                http_post.assert_not_called()
                http_get.assert_not_called()
            if "bridge" in observed:
                observed["bridge"]._smooth_timer.stop()
                observed["bridge"].deleteLater()
            if "services" in observed:
                self.assertTrue(observed["services"]._closed)
                self.assertTrue(observed["services"].updater._closed)
                observed["services"].deleteLater()
            if failures:
                raise failures[0]


if __name__ == "__main__":
    unittest.main()