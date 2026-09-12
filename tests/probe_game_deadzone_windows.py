"""
In-game stick deadzone probe (throwaway, not Nimbus code).

Finds the right-stick magnitude at which a running game starts to move its
camera: the game's own inner deadzone, the number the output anti-deadzone
in Nimbus has to clear (``docs/vision/AIM_ASSISTANCE.md`` sections 2.2, 4.2
and 12.3). Reuses the window capture and frame differencing of
``probe_game_mouselook_windows.py``: a camera rotation changes most of the
frame, an idle scene only its animated parts.

Modes
-----
sweep (default)
    A ViGEm pad of its own. Holds the right stick at each magnitude in
    ``--magnitudes`` for ``--hold`` seconds with the game in the foreground,
    captures before and after, and counts changed samples against the idle
    noise floor. A full-deflection control comes first: if that does not
    move the camera the game is not reading the stick and the sweep is
    meaningless. The first magnitude that moves the camera is the deadzone.
nimbus
    The real Nimbus app in-process (as ``probe_nimbus_relay_windows.py``
    runs it), placed beside the game, current profile. Finds the first
    joystick mapped to rx/ry and checks that a full drag moves the camera
    (control) and that a ``--nudge`` px drag (default 1) does too: the
    anti-deadzone floor clears the game's threshold. Run this in a separate
    invocation so the sweep's pad is gone and Nimbus's pad is player one.

The game must be in a map with its view following the right stick, and the
pad must exist before the game starts (Source decides at launch whether an
XInput controller is present; one plugged in later is never read). So start
the probe with ``--wait-for-window``, then launch the game:

    venv\\Scripts\\python tests\\probe_game_deadzone_windows.py --title "Left 4 Dead 2" --wait-for-window 300 --warmup 75
    "C:\\Program Files (x86)\\Steam\\steam.exe" -applaunch 550 -novid -windowed -noborder -w 1280 -h 720 +map c1m2_streets +exec 360controller

and the same with ``--mode nimbus`` (quit the game in between so the sweep's
pad is gone and Nimbus's pad is player one). Without the flags the probe
expects the game to be running already.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

if sys.platform != "win32":
    sys.exit("Windows only")

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from PySide6.QtCore import QObject, QPointF, QTimer, QUrl, Qt, Signal, Slot  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from probe_game_mouselook_windows import (  # noqa: E402
    Pad, client_rect_on_screen, find_window, frame_diff, grab, save_frame,
)
from probe_rawinput_windows import bring_to_front, user32  # noqa: E402

DEFAULT_MAGNITUDES = "0.10,0.15,0.20,0.22,0.24,0.26,0.28,0.30,0.35,0.40"


# ---- Qt thread hop (nimbus mode) ------------------------------------------------
class _QtCall(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        fn()


_QT: Optional[_QtCall] = None


def on_qt(fn: Callable[[], Any], timeout: float = 10.0) -> Any:
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


# ---- shared measurement --------------------------------------------------------
class Scene:
    """The game window: capture, differencing, foreground, and the result rows."""

    def __init__(self, args: argparse.Namespace, capture: Callable[[Callable[[], Any]], Any],
                 hwnd: Optional[int] = None) -> None:
        self.args = args
        self.hwnd = hwnd or find_window(args.title)
        if not self.hwnd:
            raise SystemExit(f"no visible window with '{args.title}' in its title")
        self.x, self.y, self.w, self.h = client_rect_on_screen(self.hwnd)
        self.rows: List[Dict[str, Any]] = []
        self.noise = 0
        self._capture = capture
        os.makedirs(args.frames, exist_ok=True)
        print(f"game hwnd={self.hwnd} client=({self.x},{self.y}) {self.w}x{self.h}", flush=True)

    def front(self) -> None:
        bring_to_front(self.hwnd)
        user32.SetCursorPos(self.x + self.w // 2, self.y + self.h // 2)
        time.sleep(0.4)

    def grab(self):
        return self._capture(lambda: grab(self.hwnd))

    def measure(self, name: str, start: Callable[[], None], stop: Callable[[], None], hold: float) -> int:
        """Capture, start the stimulus, hold, capture again, stop, difference."""
        self.front()
        time.sleep(self.args.settle)
        a = self.grab()
        start()
        time.sleep(hold)
        b = self.grab()
        stop()
        time.sleep(self.args.settle)
        d = frame_diff(a, b, skip_top=self.args.skip_top)
        tag = name.replace(" ", "_").replace(".", "p")
        save_frame(a, os.path.join(self.args.frames, f"deadzone_{tag}_before.png"))
        save_frame(b, os.path.join(self.args.frames, f"deadzone_{tag}_after.png"))
        self.rows.append({"condition": name, "changed": d})
        print(f"  {name:<18} changed={d:>6}", flush=True)
        return d

    def thresholds(self):
        return max(3 * self.noise, 150), max(2 * self.noise, 60)

    def verdicts(self) -> None:
        hi, lo = self.thresholds()
        print(f"\nnoise floor={self.noise}  moved if > {hi}, still if <= {lo}")
        for r in self.rows:
            v = "MOVED" if r["changed"] > hi else ("STILL" if r["changed"] <= lo else "INCONCLUSIVE")
            r["verdict"] = v
            print(f"  {r['condition']:<18} {r['changed']:>6}  {v}")

    def write(self, name: str, extra: Dict[str, Any]) -> None:
        out = os.path.join(self.args.frames, name)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"title": self.args.title, "hwnd": self.hwnd, "client": [self.x, self.y, self.w, self.h],
                       "noise": self.noise, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": self.rows,
                       **extra}, fh, indent=2)
        print(f"wrote {out}")


def wait_for_game(args: argparse.Namespace) -> Optional[int]:
    """Find the game window, waiting up to ``--wait-for-window`` seconds for it
    to appear and then ``--warmup`` seconds for the map to load.

    The pad (or Nimbus) must exist before the game starts: Source decides at
    start-up whether an XInput controller is present, and a pad created after
    launch is never read (measured on Left 4 Dead 2 on 2026-09-06: the mouse
    moved the camera, the pad did not, and the HUD showed the generic JOY3
    glyph). So start the probe first, then launch the game.
    """
    t0 = time.time()
    hwnd = find_window(args.title)
    while not hwnd and time.time() - t0 < args.wait_for_window:
        time.sleep(2.0)
        hwnd = find_window(args.title)
    if not hwnd:
        return None
    if args.warmup > 0:
        print(f"game window found after {time.time() - t0:.0f}s; waiting {args.warmup:.0f}s for the map", flush=True)
        time.sleep(args.warmup)
    return hwnd


# ---- sweep mode ------------------------------------------------------------------
def run_sweep(args: argparse.Namespace) -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841  (grab needs a GUI app)
    magnitudes = [float(m) for m in args.magnitudes.split(",") if m.strip()]
    pad = Pad()   # before the game starts; see wait_for_game
    if not pad.pad:
        print("vgamepad unavailable; nothing to sweep")
        return 2
    hwnd = wait_for_game(args)
    if not hwnd:
        pad.close()
        print(f"no visible window with '{args.title}' in its title")
        return 2
    scene = Scene(args, lambda fn: fn(), hwnd)
    time.sleep(1.5)
    try:
        scene.noise = scene.measure("idle", lambda: None, lambda: None, args.hold)
        hi, _lo = scene.thresholds()
        control = scene.measure("pad 1.00 control", lambda: pad.right_stick(1.0, 0.0),
                                lambda: pad.right_stick(0.0, 0.0), args.hold)
        if control <= hi:
            print(f"\nthe camera did not move at full deflection (changed={control}, need > {hi}); "
                  "the game is not reading the right stick, so the sweep would measure nothing. "
                  "Enable the controller in the game and put it in a map first.")
            scene.write("deadzone_results.json", {"mode": "sweep", "aborted": "no control motion"})
            return 3
        for m in magnitudes:
            scene.measure(f"pad {m:.2f}", lambda m=m: pad.right_stick(m, 0.0),
                          lambda: pad.right_stick(0.0, 0.0), args.hold)
    finally:
        pad.close()
        bring_to_front(scene.hwnd)

    scene.verdicts()
    hi, lo = scene.thresholds()
    moved = [float(r["condition"].split()[1]) for r in scene.rows
             if r["condition"].startswith("pad ") and "control" not in r["condition"] and r["changed"] > hi]
    still = [float(r["condition"].split()[1]) for r in scene.rows
             if r["condition"].startswith("pad ") and "control" not in r["condition"] and r["changed"] <= lo]
    first = min(moved) if moved else None
    last_still = max([s for s in still if first is None or s < first], default=None)
    print()
    if first is None:
        print("the camera never moved below full deflection: the deadzone is above the highest magnitude swept")
    else:
        print(f"deadzone: the camera first moved at {first:.2f}"
              + (f", still at {last_still:.2f}" if last_still is not None else "")
              + f"  (XInput right thumb constant 0.265)")
    scene.write("deadzone_results.json", {"mode": "sweep", "first_moved": first, "last_still": last_still,
                                          "magnitudes": magnitudes})
    return 0


# ---- nimbus mode --------------------------------------------------------------------
def run_nimbus(args: argparse.Namespace) -> int:
    global _QT
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow  # noqa: F401  (without it the root object wraps as a bare QWindow)
    from src.bridge import ControllerBridge
    from src.cloud_client import CloudClient
    from src.config import ControllerConfig
    from src.qt_qml_app import qml_path
    from src.telemetry import TelemetryClient
    from src.updater import UpdateChecker

    config_path = os.path.join(REPO, "controller_config.json")
    config_backup = open(config_path, "rb").read() if os.path.exists(config_path) else None

    app = QApplication(sys.argv)
    app.setApplicationName("Nimbus Adaptive Controller")
    _QT = _QtCall()
    scene_box: Dict[str, Scene] = {}
    config = ControllerConfig()
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

    def items():
        out = []
        stack = [engine.rootObjects()[0].contentItem()]
        while stack:
            it = stack.pop()
            out.append(it)
            stack.extend(it.childItems())
        return out

    def aim_widget():
        for it in items():
            if it.objectName().startswith("widget_") and it.property("widgetType") == "joystick":
                m = it.property("mapping") or {}
                if str(m.get("axis_x", "")).lower() == "rx":
                    return it
        return None

    def send(ev_type, pos: QPointF, button, buttons) -> None:
        win = bridge._window
        g = QPointF(win.mapToGlobal(pos.toPoint()))
        QCoreApplication.sendEvent(win, QMouseEvent(ev_type, pos, pos, g, button, buttons,
                                                    Qt.KeyboardModifier.NoModifier))

    def scenario() -> None:
        try:
            for _ in range(100):
                if bridge._window is not None:
                    break
                time.sleep(0.1)
            if bridge._window is None:
                print("the QML window never registered with the bridge")
                return
            # Nimbus's pad exists now; the game may start (see wait_for_game)
            hwnd = wait_for_game(args)
            if not hwnd:
                print(f"no visible window with '{args.title}' in its title")
                return
            scene = scene_box["s"] = Scene(args, lambda fn: on_qt(fn), hwnd)
            # Beside the game, never over its client area (that would freeze the
            # capture). If there is no room to the right, move the game to the
            # top-left of the screen first.
            screen_w = user32.GetSystemMetrics(0)
            if scene.x + scene.w + 10 + 1024 > screen_w:
                user32.SetWindowPos(scene.hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0004)   # SWP_NOSIZE | SWP_NOZORDER
                time.sleep(0.5)
                scene.x, scene.y, scene.w, scene.h = client_rect_on_screen(scene.hwnd)
                print(f"moved the game to ({scene.x},{scene.y})", flush=True)
            nx = scene.x + scene.w + 10
            on_qt(lambda: (bridge._window.setPosition(nx, max(0, scene.y)), bridge._window.raise_()))
            time.sleep(1.0)
            widget = on_qt(aim_widget)
            if widget is None:
                print("the current profile has no joystick mapped to rx/ry; switch to one that does (Adaptive Platform 2)")
                return
            params = on_qt(lambda: bridge._widget_params(bridge._widget_shaping[widget.objectName()[7:]]))
            c = on_qt(lambda: widget.mapToScene(QPointF(widget.width() / 2.0, widget.height() / 2.0)))
            print(f"aim stick {widget.objectName()} params={params} mode={bridge.getOutputMode()}", flush=True)

            def start_drag(px: float):
                def _s():
                    on_qt(lambda: send(QEvent.Type.MouseButtonPress, c, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
                    time.sleep(0.02)
                    on_qt(lambda: send(QEvent.Type.MouseMove, QPointF(c.x() + px, c.y()), Qt.MouseButton.NoButton,
                                       Qt.MouseButton.LeftButton))
                    time.sleep(0.05)
                    v = on_qt(lambda: dict(bridge._vigem.current_values)) if bridge._vigem else {}
                    print(f"      Nimbus sent RX={v.get('right_x', 0.0):+.3f}", flush=True)
                return _s

            def stop_drag():
                on_qt(lambda: send(QEvent.Type.MouseButtonRelease, c, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))

            time.sleep(1.0)  # let the game notice Nimbus's pad
            scene.noise = scene.measure("idle", lambda: None, lambda: None, args.hold)
            scene.measure("nimbus full drag", start_drag(400), stop_drag, args.hold)
            scene.measure(f"nimbus {args.nudge}px drag", start_drag(args.nudge), stop_drag, args.hold)
            scene.verdicts()
            hi, _lo = scene.thresholds()
            full_ok = scene.rows[1]["changed"] > hi
            nudge_ok = scene.rows[2]["changed"] > hi
            print()
            if not full_ok:
                print("a full drag did not move the camera: the game is not reading Nimbus's pad (is it player one?)")
            elif nudge_ok:
                print(f"a {args.nudge} px drag moved the camera: the anti-deadzone floor "
                      f"({params['anti_deadzone']:.3f} + {params['anti_deadzone_buffer']:.3f}) clears the game's threshold")
            else:
                print(f"a {args.nudge} px drag did NOT move the camera: raise Anti-DZ above "
                      f"{params['anti_deadzone']:.3f} for this game")
            scene.write("deadzone_nimbus_results.json", {"mode": "nimbus", "params": params, "nudge_px": args.nudge,
                                                          "full_moved": full_ok, "nudge_moved": nudge_ok})
            exit_code["code"] = 0 if (full_ok and nudge_ok) else 1
        except Exception as exc:   # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"scenario crashed: {type(exc).__name__}: {exc}")
        finally:
            try:
                if bridge._vigem:
                    on_qt(lambda: bridge._vigem._reset_axes())
            except Exception:
                pass
            if "s" in scene_box:
                bring_to_front(scene_box["s"].hwnd)
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
    return exit_code["code"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True, help="substring of the game window title")
    ap.add_argument("--mode", choices=("sweep", "nimbus"), default="sweep")
    ap.add_argument("--magnitudes", default=DEFAULT_MAGNITUDES, help="comma-separated right-stick magnitudes to hold")
    ap.add_argument("--hold", type=float, default=0.8, help="seconds to hold each stimulus")
    ap.add_argument("--settle", type=float, default=0.5, help="seconds to wait around each capture")
    ap.add_argument("--nudge", type=float, default=1.0, help="nimbus mode: pixels of drag for the floor check")
    ap.add_argument("--wait-for-window", type=float, default=0.0,
                    help="seconds to wait for the game window to appear (start the probe, then launch the game)")
    ap.add_argument("--warmup", type=float, default=0.0, help="seconds to wait after the window appears, for the map")
    ap.add_argument("--skip-top", type=int, default=0)
    ap.add_argument("--frames", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_frames"))
    args = ap.parse_args()
    return run_nimbus(args) if args.mode == "nimbus" else run_sweep(args)


if __name__ == "__main__":
    sys.exit(main())
