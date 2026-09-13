"""Own controller instances and output selection independently of Qt."""
from typing import Any, Callable, Optional


class ControllerOutput:
    """Manage lazy backend creation without changing controller input state.

    Parameters
    ----------
    config : ControllerConfig
        Application configuration.
    vjoy_factory, vigem_factory : callable
        Backend constructors supplied by the application boundary.
    vigem_available : bool
        Whether the optional ViGEm implementation can be constructed.
    """

    def __init__(self, config: Any, vjoy_factory: Callable,
                 vigem_factory: Optional[Callable], vigem_available: bool) -> None:
        self._config = config
        self._vjoy_factory = vjoy_factory
        self._vigem_factory = vigem_factory
        self.vigem_available = vigem_available
        self.vjoy = None
        self.vigem = None
        self.use_vigem = False

    @property
    def active(self) -> Any:
        """Return the selected backend, if one has been created."""
        return self.vigem if self.use_vigem and self.vigem else self.vjoy

    @property
    def connected(self) -> bool:
        """Return the selected backend's connection state."""
        return bool(self.active and self.active.is_connected)

    @property
    def mode(self) -> str:
        """Return the selected output mode."""
        return "vigem" if self.use_vigem else "vjoy"

    def initialize(self) -> None:
        """Create the backends selected by the existing profile policy."""
        layout_type = self._config.get_layout_type()
        prefer_vigem = self._config.get("controller.prefer_vigem", True)
        self.use_vigem = bool(layout_type in ("xbox", "adaptive", "custom")
                              and self.vigem_available and prefer_vigem)
        if self.use_vigem and self.vigem is None and self.ensure_vigem() is None:
            # mode must never name a backend that active does not return, or
            # the status bar and getOutputMode() report ViGEm while input goes
            # to vJoy.
            self.use_vigem = False
        if self.vjoy is None:
            self.vjoy = self._vjoy_factory(self._config)

    def select(self, mode: str) -> bool:
        """Select an output, returning whether selection changed.

        This does not reset either device, persist preferences, or emit UI
        events. Input transition policy remains the caller's responsibility.
        """
        mode = mode.lower().strip()
        if mode not in ("vjoy", "vigem") or mode == self.mode:
            return False
        if mode == "vigem":
            if not self.vigem_available:
                return False
            if self.vigem is None and self.ensure_vigem() is None:
                return False   # see initialize(): do not select what was not built
        elif self.vjoy is None:
            self.vjoy = self._vjoy_factory(self._config)
        self.use_vigem = mode == "vigem"
        return True

    def ensure_vigem(self) -> Any:
        """Create or reuse ViGEm for Game Mode without selecting its output."""
        if not self.vigem_available or self._vigem_factory is None:
            return None
        if self.vigem is None:
            self.vigem = self._vigem_factory(self._config)
        return self.vigem