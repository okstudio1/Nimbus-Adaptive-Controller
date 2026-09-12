"""Consent and privacy regressions with temporary storage and mocked HTTP."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication

from src.telemetry import TelemetryClient


class TelemetryPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config = Mock()
        config.get.side_effect = lambda key, default=None: default
        with patch("src.telemetry._get_telemetry_dir", return_value=Path(temporary.name)), \
                patch.object(TelemetryClient, "_ensure_user_id", return_value="test-only"), \
                patch.object(TelemetryClient, "_init_sentry"):
            self.telemetry = TelemetryClient(config)
            self.telemetry.analytics_enabled = True
            self.telemetry.crash_reports_enabled = True
        self.addCleanup(self.telemetry._flush_timer.stop)

    def test_revoking_crash_consent_filters_memory_and_offline_uploads(self):
        mixed = [{"event": "session_start"}, {"event": "crash"}]
        self.telemetry._buffer = mixed.copy()
        self.telemetry._persist_offline(mixed)
        self.telemetry.crash_reports_enabled = False
        self.assertEqual(self.telemetry._buffer, [{"event": "session_start"}])
        with patch("httpx.post", return_value=Mock(status_code=200)) as post:
            self.telemetry.shutdown()
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["json"]["events"], [{"event": "session_start"}])

    def test_revoking_analytics_removes_queued_data_even_after_reenable(self):
        self.telemetry._buffer = [{"event": "session_start"}]
        self.telemetry._persist_offline(self.telemetry._buffer)
        self.telemetry.analytics_enabled = False
        self.assertFalse(self.telemetry._offline_path.exists())
        self.telemetry.analytics_enabled = True
        with patch("httpx.post") as post:
            self.telemetry._flush()
        post.assert_not_called()

    def test_retry_checks_current_consent_even_if_purge_failed(self):
        self.telemetry._persist_offline([{"event": "session_start"}, {"event": "crash"}])
        with patch.object(self.telemetry, "_purge_disallowed"):
            self.telemetry.analytics_enabled = False
        with patch("httpx.post", return_value=Mock(status_code=200)) as post:
            self.telemetry._retry_offline()
        self.assertEqual(post.call_args.kwargs["json"]["events"], [{"event": "crash"}])

    def test_malformed_and_unknown_queued_categories_are_dropped(self):
        events = [None, {"event": []}, {"event": "unknown"}, {"event": "_common_fields"},
                  {"event": "crash"}]
        self.assertEqual(self.telemetry._consented_events(events), [{"event": "crash"}])

    def test_sentry_allowlist_removes_messages_locals_and_all_extra_fields(self):
        marker = "synthetic-private-data"
        event = {
            "message": marker, "user": {"email": marker}, "extra": {"value": marker},
            "breadcrumbs": {"values": [marker]}, "contexts": {"private": marker},
            "request": {"url": marker}, "tags": {"private": marker},
            "exception": {"values": [{"type": "ValueError", "value": marker,
                "stacktrace": {"frames": [{"abs_path": marker, "vars": {"local": marker}}]}}]},
        }
        scrubbed = self.telemetry._scrub_sentry_event(event, {})
        self.assertNotIn(marker, json.dumps(scrubbed))
        self.assertEqual(set(scrubbed), {"level", "release", "exception", "fingerprint"})
        self.assertEqual(scrubbed["exception"]["values"][0]["type"], "ValueError")
        self.telemetry.crash_reports_enabled = False
        self.assertIsNone(self.telemetry._scrub_sentry_event(event, {}))

    def test_fingerprint_groups_the_same_crash_across_installs(self):
        """Two installs hitting one bug must land in one Sentry group.

        The fingerprint used to hash the whole exception payload, which carries
        abs_path, so it differed per machine and grouping was per user. It must
        depend on code position only, and still leak nothing.
        """
        def event_from(root, line):
            return {"exception": {"values": [{
                "type": "ValueError", "value": "secret detail",
                "stacktrace": {"frames": [{
                    "abs_path": root + "/src/bridge.py", "filename": root + "/src/bridge.py",
                    "module": "src.bridge", "function": "setStickInput", "lineno": line,
                    "vars": {"token": "secret"}}]}}]}}

        owen = self.telemetry._scrub_sentry_event(event_from("C:/Users/Owen", 700), {})
        other = self.telemetry._scrub_sentry_event(event_from("/home/someone-else", 700), {})
        self.assertEqual(owen["fingerprint"], other["fingerprint"])

        # A different bug in the same function must not collapse into it.
        moved = self.telemetry._scrub_sentry_event(event_from("C:/Users/Owen", 812), {})
        self.assertNotEqual(owen["fingerprint"], moved["fingerprint"])

        # And the fingerprint is still opaque.
        for marker in ("Owen", "secret", "bridge.py"):
            self.assertNotIn(marker, json.dumps(owen))


if __name__ == "__main__":
    unittest.main()