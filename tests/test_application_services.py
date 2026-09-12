"""Application-service lifetime tests with no network or driver access."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.application_services import ApplicationServices
from src.updater import UpdateChecker
from src.telemetry import TelemetryClient


class ApplicationServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_factories_receive_explicit_parent_and_shutdown_is_idempotent(self):
        config = Mock()
        factories = [Mock(), Mock(), Mock()]
        services = ApplicationServices(config, telemetry_factory=factories[0],
                                       cloud_factory=factories[1], updater_factory=factories[2])
        for factory in factories:
            factory.assert_called_once_with(config, services)
        services.shutdown()
        services.shutdown()
        factories[0].return_value.shutdown.assert_called_once()
        factories[1].return_value.shutdown.assert_called_once()
        factories[2].return_value.shutdown.assert_called_once()

    def test_updater_shutdown_blocks_new_checks(self):
        config = Mock()
        config.get.side_effect = lambda key, default=None: False if key == "updater.auto_check" else default
        network = Mock()
        updater = UpdateChecker(config, network_manager=network)
        updater.shutdown()
        updater.check()
        network.get.assert_not_called()
        notifications = []
        updater.updateAvailable.connect(lambda *args: notifications.append(args))
        updater._on_manifest_received({"latest": "999.0.0"})
        self.assertEqual(notifications, [])

    def test_live_crash_consent_controls_timer_and_sentry_gate(self):
        config = Mock()
        config.get.side_effect = lambda key, default=None: default
        with tempfile.TemporaryDirectory() as directory, \
                patch("src.telemetry._get_telemetry_dir", return_value=Path(directory)), \
                patch.object(TelemetryClient, "_ensure_user_id", return_value="test-only"), \
                patch.object(TelemetryClient, "_init_sentry"):
            telemetry = TelemetryClient(config)
            self.assertFalse(telemetry._flush_timer.isActive())
            telemetry.crash_reports_enabled = True
            self.assertTrue(telemetry._flush_timer.isActive())
            self.assertIsNone(telemetry._scrub_sentry_event({}, {}))
            telemetry.crash_reports_enabled = False
            self.assertFalse(telemetry._flush_timer.isActive())
            self.assertIsNone(telemetry._scrub_sentry_event({}, {}))
            telemetry.shutdown()

if __name__ == "__main__":
    unittest.main()