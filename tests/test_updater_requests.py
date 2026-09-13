"""Update deadlines and cancellation using Qt signals without network I/O."""
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QObject, Signal, QByteArray, QCoreApplication, QEventLoop, QTimer
from PySide6.QtNetwork import QNetworkReply

from src.updater import UpdateChecker, MAX_MANIFEST_BYTES


class FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.data = b""
        self.aborted = False
        self.setReadBufferSize = Mock()

    def readAll(self):
        data, self.data = self.data, b""
        return QByteArray(data)

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def attribute(self, key):
        return 200

    def abort(self):
        self.aborted = True
        self.finished.emit()


class UpdaterRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        config = Mock()
        config.get.side_effect = lambda key, default=None: False if key == "updater.auto_check" else default
        self.reply = FakeReply()
        self.network = Mock()
        self.network.get.return_value = self.reply
        self.updater = UpdateChecker(config, network_manager=self.network)
        self.addCleanup(self.updater.shutdown)

    def test_shutdown_aborts_stalled_request_without_waiting(self):
        self.updater.check()
        self.updater.shutdown()
        self.assertTrue(self.reply.aborted)
        self.assertIsNone(self.updater._reply)
        self.assertFalse(self.updater._deadline.isActive())
        self.updater.check()
        self.network.get.assert_called_once()

    def test_trickling_response_cannot_extend_total_deadline(self):
        errors = []
        loop = QEventLoop()
        self.updater.checkFailed.connect(lambda message: (errors.append(message), loop.quit()))
        self.updater._deadline.setInterval(20)
        self.updater.check()
        trickle = QTimer()
        trickle.setInterval(1)
        def send_chunk():
            self.reply.data = b" "
            self.reply.readyRead.emit()
        trickle.timeout.connect(send_chunk)
        trickle.start()
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(1000)
        loop.exec()
        trickle.stop()
        guard.stop()
        self.assertTrue(self.reply.aborted)
        self.assertEqual(len(errors), 1)
        self.assertIn("deadline", errors[0])

    def test_success_parses_manifest_and_releases_reply(self):
        updates = []
        self.updater.updateAvailable.connect(lambda *args: updates.append(args))
        self.updater.check()
        self.reply.data = b'{"latest":"999.0.0"}'
        self.reply.finished.emit()
        self.assertEqual(self.updater.latest_version, "999.0.0")
        self.assertEqual(len(updates), 1)
        self.assertIsNone(self.updater._reply)
        self.assertFalse(self.updater._deadline.isActive())

    def test_oversized_and_invalid_manifests_fail(self):
        errors = []
        self.updater.checkFailed.connect(errors.append)
        self.updater.check()
        self.reply.data = b"x" * (MAX_MANIFEST_BYTES + 1)
        self.reply.readyRead.emit()
        self.assertTrue(self.reply.aborted)
        self.assertEqual(len(errors), 1)

    def test_late_completion_after_shutdown_is_ignored(self):
        updates = []
        self.updater.updateAvailable.connect(lambda *args: updates.append(args))
        self.updater.check()
        self.updater.shutdown()
        self.reply.data = b'{"latest":"999.0.0"}'
        self.reply.finished.emit()
        self.assertEqual(updates, [])


if __name__ == "__main__":
    unittest.main()