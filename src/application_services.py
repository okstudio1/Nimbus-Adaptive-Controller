"""Composition and lifetime of the application's non-controller services."""
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject

from .cloud_client import CloudClient
from .telemetry import TelemetryClient
from .updater import UpdateChecker


class ApplicationServices(QObject):
    """Create services once and inject them into the QML bridge.

    Parameters
    ----------
    config : ControllerConfig
        Shared application configuration.
    parent : QObject, optional
        Qt lifetime owner.
    telemetry_factory, cloud_factory, updater_factory : callable
        Constructors accepting configuration and a QObject parent.
    """

    def __init__(self, config: Any, parent: Optional[QObject] = None, *,
                 telemetry_factory: Callable = TelemetryClient,
                 cloud_factory: Callable = CloudClient,
                 updater_factory: Callable = UpdateChecker) -> None:
        super().__init__(parent)
        self.telemetry = telemetry_factory(config, self)
        self.cloud = cloud_factory(config, self)
        self.updater = updater_factory(config, self)
        self._closed = False

    def shutdown(self) -> None:
        """Stop owned background work exactly once before Qt destruction."""
        if self._closed:
            return
        self._closed = True
        try:
            self.updater.shutdown()
        finally:
            try:
                self.cloud.shutdown()
            finally:
                self.telemetry.shutdown()