"""
Spectator+ primitives: timed axis and button sequences on the Qt thread.

A primitive is a small plan (set an axis, wait, release it) run by a
``QTimer`` so nothing blocks the UI thread and every plan ends with the
axes it touched back at zero. The runner writes through whatever the bridge
gives it, normally ``ControllerBridge.setAxis`` and ``setButton``, so the
output goes through the active driver interface and its limits, and bypasses
the widget shaping on purpose: a turn of 90 degrees has to be the same turn
whatever curve the user has on their own stick.

One primitive runs at a time; ``stop()`` cancels it and zeroes the output.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from .calibration import GameCalibration

AxisSink = Callable[[str, float], None]
ButtonSink = Callable[[int, bool], None]


class PrimitiveRunner(QObject):
    """Executes Spectator+ primitives against an axis and a button sink.

    Parameters
    ----------
    set_axis : callable
        ``set_axis(axis_name, value)`` with ``value`` in -1 to 1 (0 to 1 for
        triggers), for example ``ControllerBridge.setAxis``.
    set_button : callable
        ``set_button(button_id, pressed)``, for example ``ControllerBridge.setButton``.
    parent : QObject, optional
        Qt parent; the runner's timer lives on the parent's thread.

    Signals
    -------
    started(str)
        A primitive began; the argument names it.
    finished(str, bool)
        A primitive ended; ``True`` when it ran to completion, ``False`` when
        ``stop()`` cut it short.
    """

    started = Signal(str)
    finished = Signal(str, bool)

    def __init__(self, set_axis: AxisSink, set_button: ButtonSink, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._set_axis = set_axis
        self._set_button = set_button
        self._calibration: Optional[GameCalibration] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self._steps: List[Tuple[float, Callable[[], None]]] = []
        self._index = 0
        self._t0 = 0.0
        self._name = ""
        self._touched_axes: set = set()
        self._touched_buttons: set = set()
        self.last_plan: Dict[str, float] = {}

    # ----- configuration -----
    @property
    def busy(self) -> bool:
        return bool(self._name)

    @property
    def calibration(self) -> Optional[GameCalibration]:
        return self._calibration

    def use_calibration(self, calibration: GameCalibration) -> None:
        self._calibration = calibration

    def use_game(self, game: str) -> bool:
        """Load the bundled calibration for ``game``; ``False`` when there is none."""
        try:
            self._calibration = GameCalibration.load(game)
            return True
        except (OSError, ValueError):
            return False

    # ----- primitives -----
    def turn(self, degrees: float) -> Optional[Dict[str, float]]:
        """Turn the view by ``degrees`` (positive right). Returns the plan, or
        ``None`` when there is no calibration, the runner is busy, or the
        angle is zero."""
        if self._calibration is None or self.busy or abs(degrees) < 1e-6:
            return None
        plan = self._calibration.plan_turn(degrees)
        if plan is None:
            return None
        magnitude, hold = plan
        self.last_plan = {"axis_value": magnitude, "hold": hold, "degrees": float(degrees)}
        self._run("turn", [(0.0, lambda: self._axis("rx", magnitude)),
                           (hold, lambda: self._axis("rx", 0.0))])
        return dict(self.last_plan)

    def walk(self, units: float) -> Optional[Dict[str, float]]:
        """Walk ``units`` forward (negative walks backward). Returns the plan or ``None``."""
        if self._calibration is None or self.busy or abs(units) < 1e-6:
            return None
        plan = self._calibration.plan_walk(units)
        if plan is None:
            return None
        magnitude, hold = plan
        self.last_plan = {"axis_value": magnitude, "hold": hold, "units": float(units)}
        self._run("walk", [(0.0, lambda: self._axis("y", magnitude)),
                           (hold, lambda: self._axis("y", 0.0))])
        return dict(self.last_plan)

    def press(self, button_id: int, hold: float = 0.12) -> bool:
        """Press and release a button. Needs no calibration."""
        if self.busy:
            return False
        bid = int(button_id)
        self.last_plan = {"button": float(bid), "hold": float(hold)}
        self._run("press", [(0.0, lambda: self._button(bid, True)),
                            (max(0.02, float(hold)), lambda: self._button(bid, False))])
        return True

    def stop(self) -> None:
        """Cancel the running primitive and zero everything it touched."""
        if not self.busy:
            return
        name = self._name
        self._timer.stop()
        self._steps = []
        self._release_all()
        self._name = ""
        self.finished.emit(name, False)

    # ----- machinery -----
    def _axis(self, axis: str, value: float) -> None:
        self._touched_axes.add(axis)
        self._set_axis(axis, float(value))

    def _button(self, button_id: int, pressed: bool) -> None:
        if pressed:
            self._touched_buttons.add(button_id)
        else:
            self._touched_buttons.discard(button_id)
        self._set_button(button_id, bool(pressed))

    def _release_all(self) -> None:
        for axis in list(self._touched_axes):
            try:
                self._set_axis(axis, 0.0)
            except Exception:
                pass
        for bid in list(self._touched_buttons):
            try:
                self._set_button(bid, False)
            except Exception:
                pass
        self._touched_axes.clear()
        self._touched_buttons.clear()

    def _run(self, name: str, steps: List[Tuple[float, Callable[[], None]]]) -> None:
        self._name = name
        self._steps = sorted(steps, key=lambda s: s[0])
        self._index = 0
        self._t0 = time.monotonic()
        self.started.emit(name)
        self._advance()

    def _advance(self) -> None:
        # Run every step whose time has come, then arm the timer for the next
        # one. Steps are relative to the start, so timer drift does not add up.
        while self._index < len(self._steps):
            at, action = self._steps[self._index]
            remaining = at - (time.monotonic() - self._t0)
            if remaining > 0.0005:
                self._timer.start(max(1, int(remaining * 1000)))
                return
            self._index += 1
            try:
                action()
            except Exception:
                self._release_all()
                name, self._name = self._name, ""
                self.finished.emit(name, False)
                return
        self._release_all()
        name, self._name = self._name, ""
        self.finished.emit(name, True)
