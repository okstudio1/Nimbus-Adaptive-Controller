"""
Nimbus cursor-relay probe: the real app, Full Game Mode, and a fake Raw Input game.

This runs the actual Nimbus QML application in-process (the same objects
``src.qt_qml_app.main`` builds), spawns the fake Raw Input game from
``probe_rawinput_windows.py``, and puts the bridge into Full Game Mode against
it. Everything the driver feeds in production is then exercised for real: the
bridge's relay policy, ``SetCursorPos`` on the reader thread, the queued
signals, the synthesised Qt events, the QML joystick, the ViGEm output,
``WS_EX_NOACTIVATE``, and mouse_hider's controller mode.

Unattended (default): the isolation class is replaced by a stand-in whose
packets come from this script instead of the kernel driver (``FakeIsolation``
feeds ``MOUSE_INPUT_DATA`` through the module's own parser). Checks:

  N1  Full Game Mode starts with isolation active and the game in the foreground
  N2  captured motion moves the real cursor onto Nimbus's joystick; the game
      receives no WM_INPUT and keeps the foreground
  N3  a captured left press and drag on the joystick drives the virtual stick;
      nothing is injected, so the game (registered RIDEV_INPUTSINK, which sees
      Raw Input even unfocused) gets no button and no motion
  N4  the release recentres the stick
  N5  motion aimed at the game window is refused: the cursor never enters it
  N5b a cursor already sitting on the game is parked onto Nimbus by the next
      packet, so a physical mouse is never stuck over the game
  N6  a click on the desktop is replayed with SendInput: the sink-registered game
      sees the button events and zero motion (the documented leak)
  N7  stopping Full Game Mode ends isolation and re-enables activation

Attended (``--attended``): the real driver, a hand on the physical mouse. A
dialog asks for a drag on the joystick, then for Ctrl+Alt+F12.

Run::

    venv\\Scripts\\python tests\\probe_nimbus_relay_windows.py
    venv\\Scripts\\python tests\\probe_nimbus_relay_windows.py --attended
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("Windows only")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from PySide6.QtCore import QObject, QPointF, QUrl, Qt, Signal, Slot, QTimer  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import src.bridge as bridge_mod  # noqa: E402
from src import mouse_isolation_win as iso  # noqa: E402
from src.bridge import ControllerBridge  # noqa: E402
from src.cloud_client import CloudClient  # noqa: E402
from src.config import ControllerConfig  # noqa: E402
from src.qt_qml_app import qml_path  # noqa: E402
from src.telemetry import TelemetryClient  # noqa: E402
from src.updater import UpdateChecker  # noqa: E402
from probe_rawinput_windows import GameProcess, bring_to_front, user32, window_center  # noqa: E402

RESULTS: List[Dict[str, object]] = []
MB_OK, MB_ICONINFORMATION, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x40, 0x10000, 0x40000
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "note": note})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {note}", flush=True)


def cursor_pos() -> Tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground() -> int:
    h = user32.GetForegroundWindow()
    return int(h) if h else 0


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


# ---- the stand-in for the driver (unattended mode) ----------------------------
class FakeIsolation(iso.MouseIsolation):
    """``MouseIsolation`` without the kernel driver: packets come from :meth:`feed`.

    ``start`` and ``stop`` keep the module's bookkeeping; ``feed`` pushes raw
    ``MOUSE_INPUT_DATA`` through the same ``_dispatch`` the reader thread uses,
    so the relay (``SetCursorPos`` with the bridge's policy) and the callbacks
    behave exactly as with the driver. Pointer speed is pinned to 1.0 so a
    count is a pixel and the script can aim.
    """

    def start(self, nodes=None):  # noqa: D401
        with self._lock:
            if self._active:
                return list(self._devices)
            self._devices = [{"name": "fake mouse (probe)", "node": "fake", "readable": True,
                              "is_keyboard": False, "connected_mice": 1}]
            self._abs_last.clear()
            self._wheel_rem = [0, 0]
            self._relay_rem = [0.0, 0.0]
            self._speed = 1.0
            self.ticks = 0
            self.stop_reason = ""
            self._active = True
            iso._register_instance(self)
        print("[probe] fake isolation on")
        return list(self._devices)

    def stop(self, reason: str = "requested") -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stop_reason = reason
        iso._unregister_instance(self)
        print(f"[probe] fake isolation released ({reason})")
        if self._on_stopped:
            self._on_stopped(reason)

    def status(self) -> Dict[str, int]:
        return {"version": iso.INTERFACE_VERSION, "isolating": 1 if self._active else 0, "connected_mice": 1,
                "pending_reads": 1, "packets_captured": 0, "packets_dropped": 0, "packets_passed": 0,
                "watchdog_releases": 0}

    def feed(self, packets: List[Tuple[int, int, int, int, int]]) -> None:
        """Deliver packets as ``(flags, button_flags, button_data, dx, dy)``."""
        raw = b"".join(iso._MOUSE_INPUT_DATA.pack(0, f, bf, bd, 0, dx, dy, 0) for f, bf, bd, dx, dy in packets)
        buf = ctypes.create_string_buffer(raw, len(raw))
        self._dispatch(buf, len(raw))

    def move(self, dx: int, dy: int) -> None:
        self.feed([(0, 0, 0, dx, dy)])

    def button(self, down: bool) -> None:
        self.feed([(0, iso.MOUSE_LEFT_BUTTON_DOWN if down else iso.MOUSE_LEFT_BUTTON_UP, 0, 0, 0)])


# ---- helpers on the app --------------------------------------------------------
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


def layout_widgets(engine: QQmlApplicationEngine) -> List[QQuickItem]:
    return [i for i in visual_items(engine) if i.objectName().startswith("widget_")]


def find_joystick(engine: QQmlApplicationEngine) -> Optional[QQuickItem]:
    """The first joystick widget of the loaded layout (by objectName)."""
    for item in layout_widgets(engine):
        if item.property("widgetType") == "joystick":
            return item
    return None


def item_center(item: QQuickItem) -> Tuple[int, int]:
    p = item.mapToGlobal(QPointF(item.width() / 2.0, item.height() / 2.0))
    return int(p.x()), int(p.y())


def stick_values(bridge: ControllerBridge) -> Tuple[float, float, str]:
    text = bridge.getControllerStateText()
    mx = re.search(r"LX:([+-]?\d+\.\d+)", text) or re.search(r"\bX:([+-]?\d+\.\d+)", text)
    my = re.search(r"LY:([+-]?\d+\.\d+)", text) or re.search(r"\bY:([+-]?\d+\.\d+)", text)
    return (float(mx.group(1)) if mx else 0.0, float(my.group(1)) if my else 0.0, text)


def game_delta(game: GameProcess, before: Dict[str, Any]) -> Dict[str, int]:
    now = game.settle()
    return {k: now.get(k, 0) - before.get(k, 0) for k in ("input_abs", "input_events", "mousemove")}


# ---- scenarios -------------------------------------------------------------------
def unattended(app: QApplication, bridge: ControllerBridge, engine: QQmlApplicationEngine,
               game: GameProcess) -> None:
    win = bridge._window
    fake: Optional[FakeIsolation] = None

    # N1
    gx, gy = window_center(game.hwnd)
    bring_to_front(game.hwnd)
    user32.SetCursorPos(gx, gy)
    time.sleep(0.3)
    started = on_qt(lambda: bridge.startFullGameMode(game.hwnd, 30))
    time.sleep(0.6)
    fake = bridge._iso if isinstance(bridge._iso, FakeIsolation) else None
    fg_game = foreground() == game.hwnd
    if not fg_game:
        bring_to_front(game.hwnd)
        time.sleep(0.3)
        fg_game = foreground() == game.hwnd
    record("N1 Full Game Mode starts with isolation", bool(started) and fake is not None and bridge._iso_active
           and bridge._no_focus_mode and fg_game,
           f"started={started} isolation_active={bridge._iso_active} no_focus_mode={bridge._no_focus_mode} "
           f"game_foreground={fg_game} nimbus_hwnd={bridge._iso_nimbus_hwnd} game_hwnd={bridge._iso_game_hwnd}")
    if fake is None:
        return

    # N2: captured motion carries the real cursor onto the joystick
    joy = on_qt(lambda: find_joystick(engine))
    if joy is None:
        names = on_qt(lambda: [i.objectName() for i in layout_widgets(engine)])
        record("N2 motion moves the real cursor onto the joystick", False,
               f"no joystick widget in the current layout (widgets: {names})")
        return
    tx, ty = on_qt(lambda: item_center(joy))
    g0 = game.settle()
    cx, cy = cursor_pos()
    fake.move(tx - cx, ty - cy)
    time.sleep(0.4)
    nx, ny = cursor_pos()
    d = game_delta(game, g0)
    record("N2 motion moves the real cursor onto the joystick",
           abs(nx - tx) <= 2 and abs(ny - ty) <= 2 and d["input_abs"] == 0 and d["input_events"] == 0
           and foreground() == game.hwnd,
           f"cursor ({cx},{cy}) -> ({nx},{ny}), target ({tx},{ty}); game WM_INPUT abs={d['input_abs']} "
           f"events={d['input_events']}; game_foreground={foreground() == game.hwnd}")

    # N3: press and drag drives the stick, nothing reaches the game
    g0 = game.settle()
    base_x, _base_y, _ = on_qt(lambda: stick_values(bridge))
    fake.button(True)
    time.sleep(0.25)
    held = bridge._iso_buttons != Qt.MouseButton.NoButton
    for _ in range(20):
        fake.move(4, 0)
        time.sleep(0.01)
    time.sleep(0.4)
    lx, ly, text = on_qt(lambda: stick_values(bridge))
    d = game_delta(game, g0)
    dragged_x, dragged_y = cursor_pos()
    record("N3 press and drag on the joystick drives the virtual stick, game blind",
           held and lx > 0.5 and d["input_abs"] == 0 and d["input_events"] == 0 and foreground() == game.hwnd,
           f"synthetic button held={held}; cursor now ({dragged_x},{dragged_y}); {text}; "
           f"game WM_INPUT abs={d['input_abs']} events={d['input_events']}; game_foreground={foreground() == game.hwnd}")

    # N4: release recentres
    fake.button(False)
    time.sleep(0.4)
    lx2, ly2, text2 = on_qt(lambda: stick_values(bridge))
    record("N4 release recentres the stick", abs(lx2) < 0.05 and abs(ly2) < 0.05
           and bridge._iso_buttons == Qt.MouseButton.NoButton, f"{text2}; buttons held={bridge._iso_buttons}")

    # N5: motion aimed at the game is refused
    g0 = game.settle()
    cx, cy = cursor_pos()
    fake.move(gx - cx, gy - cy)
    time.sleep(0.3)
    nx, ny = cursor_pos()
    d = game_delta(game, g0)
    inside_game = iso.point_in_window(game.hwnd, nx, ny) and not iso.point_in_window(bridge._iso_nimbus_hwnd, nx, ny)
    record("N5 motion aimed at the game window is refused", (nx, ny) == (cx, cy) and not inside_game
           and d["input_abs"] == 0,
           f"cursor stayed at ({cx},{cy}) -> ({nx},{ny}), game centre ({gx},{gy}); game WM_INPUT abs={d['input_abs']}")

    # N5b: a cursor that already sits on the game (the game was clicked) is
    # parked onto Nimbus by the next packet instead of being stuck there.
    user32.SetCursorPos(gx, gy)
    time.sleep(0.15)
    g0 = game.settle()
    fake.move(5, 0)
    time.sleep(0.3)
    px, py = cursor_pos()
    ncx, ncy = iso.window_center(bridge._iso_nimbus_hwnd)
    d = game_delta(game, g0)
    # Two mechanisms park it: the relay policy on refusal, and mouse_hider's
    # controller-mode loop, which may get there first and then let the small
    # move through. Either way it must end up on Nimbus, never on the game.
    on_nimbus = iso.point_in_window(bridge._iso_nimbus_hwnd, px, py)
    on_game = iso.point_in_window(game.hwnd, px, py)
    record("N5b a cursor stuck on the game is parked onto Nimbus",
           on_nimbus and not on_game and abs(px - ncx) <= 8 and abs(py - ncy) <= 8 and d["input_abs"] == 0,
           f"placed at game centre ({gx},{gy}) -> ({px},{py}); Nimbus centre ({ncx},{ncy}); "
           f"on_nimbus={on_nimbus} on_game={on_game}; game WM_INPUT abs={d['input_abs']}")

    # N6: a click on the desktop is replayed (documented leak: the sink sees the button, never motion)
    screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    dx_, dy_ = screen_w // 2, min(screen_h - 150, 1300)
    cx, cy = cursor_pos()
    fake.move(dx_ - cx, dy_ - cy)
    time.sleep(0.2)
    over = iso.hwnd_at_cursor()
    g0 = game.settle()
    fake.button(True)
    time.sleep(0.05)
    fake.button(False)
    time.sleep(0.4)
    d = game_delta(game, g0)
    record("N6 click outside Nimbus is replayed with SendInput (sink sees the button, no motion)",
           over not in (game.hwnd, bridge._iso_nimbus_hwnd) and d["input_events"] == 2 and d["input_abs"] == 0,
           f"clicked at ({dx_},{dy_}) over hwnd {over}; sink game WM_INPUT events={d['input_events']} "
           f"abs={d['input_abs']}")
    bring_to_front(game.hwnd)

    # N7: stop
    on_qt(lambda: bridge.stopFullGameMode(game.hwnd))
    time.sleep(0.4)
    record("N7 stopping Full Game Mode ends isolation", not bridge._iso_active and not bridge._no_focus_mode
           and fake.stop_reason == "requested",
           f"isolation_active={bridge._iso_active} no_focus_mode={bridge._no_focus_mode} "
           f"stop_reason={fake.stop_reason!r}")


def attended(app: QApplication, bridge: ControllerBridge, engine: QQmlApplicationEngine,
             game: GameProcess, seconds: float) -> None:
    gx, gy = window_center(game.hwnd)
    bring_to_front(game.hwnd)
    user32.SetCursorPos(gx, gy)
    started = on_qt(lambda: bridge.startFullGameMode(game.hwnd, 30))
    time.sleep(0.6)
    record("A1 Full Game Mode starts with the driver isolating", bool(started) and bridge._iso_active
           and bridge._no_focus_mode,
           f"started={started} isolation_active={bridge._iso_active} no_focus_mode={bridge._no_focus_mode} "
           f"driver={bridge._iso.status() if bridge._iso else None}")
    if not bridge._iso_active:
        return

    user32.MessageBoxW(None,
                       f"Nimbus is in Full Game Mode with the mouse filter on.\n\n"
                       f"Click OK, then use the mouse to press and DRAG the left stick on the Nimbus window "
                       f"for {seconds:.0f} seconds. The cursor should move normally. "
                       f"The 'Probe game' window must not react.",
                       "Nimbus relay probe", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)
    bring_to_front(game.hwnd)
    g0 = game.settle()
    st0 = on_qt(lambda: bridge._iso.status()) if bridge._iso else {}
    peak = 0.0
    travel = 0
    last = cursor_pos()
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        time.sleep(0.1)
        now_pos = cursor_pos()
        travel += abs(now_pos[0] - last[0]) + abs(now_pos[1] - last[1])
        last = now_pos
        lx, ly, _ = on_qt(lambda: stick_values(bridge))
        peak = max(peak, abs(lx), abs(ly))
    d = game_delta(game, g0)
    st1 = on_qt(lambda: bridge._iso.status()) if bridge._iso else {}
    captured = st1.get("packets_captured", 0) - st0.get("packets_captured", 0)
    record("A2 hand on the mouse: stick moves, cursor moves, game blind",
           peak > 0.3 and travel > 0 and d["input_abs"] == 0 and d["input_events"] == 0 and captured > 0,
           f"peak stick={peak:.2f} cursor_travel={travel} px driver_captured={captured} "
           f"game WM_INPUT abs={d['input_abs']} events={d['input_events']}")

    user32.MessageBoxW(None, "Now press Ctrl+Alt+F12 on the keyboard (within 15 s of clicking OK).",
                       "Nimbus relay probe", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)
    t0 = time.monotonic()
    while bridge._iso_active and time.monotonic() - t0 < 15.0:
        time.sleep(0.1)
    record("A3 Ctrl+Alt+F12 releases isolation inside Nimbus", not bridge._iso_active,
           f"isolation_active={bridge._iso_active} after {time.monotonic() - t0:.1f} s")
    on_qt(lambda: bridge.stopFullGameMode(game.hwnd))
    lines = [f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}" for r in RESULTS]
    user32.MessageBoxW(None, "\n".join(lines) + "\n\nAll done. You can close this.",
                       "Nimbus relay probe: result", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)


# ---- main ---------------------------------------------------------------------------
def main() -> int:
    global _QT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attended", action="store_true", help="use the real driver and a hand on the mouse")
    ap.add_argument("--seconds", type=float, default=8.0, help="length of the attended drag phase")
    ap.add_argument("--profile", default="adaptive_platform_2",
                    help="profile to load for the run (needs a joystick widget); '' keeps the current one. "
                         "controller_config.json is restored afterwards either way")
    args = ap.parse_args()

    # The run switches profiles and Game Mode touches settings; put the
    # per-machine config back exactly as it was when we leave.
    config_path = os.path.join(REPO, "controller_config.json")
    config_backup = open(config_path, "rb").read() if os.path.exists(config_path) else None

    if not args.attended:
        # The driver stays out of it: packets come from FakeIsolation.
        iso.MouseIsolation = FakeIsolation          # type: ignore[misc]
        bridge_mod.MOUSE_ISOLATION_AVAILABLE = True

    app = QApplication(sys.argv)
    app.setApplicationName("Nimbus Adaptive Controller")
    _QT = _QtCall()
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

    scratch = os.path.join(REPO, "tests", "probe_frames")
    os.makedirs(scratch, exist_ok=True)
    exit_code = {"code": 1}

    def scenario() -> None:
        game: Optional[GameProcess] = None
        try:
            for _ in range(100):                     # Main.qml calls controller.setWindow(root)
                if bridge._window is not None:
                    break
                time.sleep(0.1)
            if bridge._window is None:
                record("N0 app window", False, "the QML window never registered with the bridge")
                return
            on_qt(lambda: (bridge._window.setPosition(1100, 80), bridge._window.raise_()))
            if args.profile and config.get_current_profile() != args.profile:
                on_qt(lambda: bridge.switchProfile(args.profile))
                print(f"[probe] switched to profile {args.profile!r} for the run", flush=True)
                time.sleep(1.0)
            time.sleep(0.8)
            game = GameProcess("hwnd", "inputsink", os.path.join(scratch, "nimbus_relay_game_stats.json"))
            print(f"[probe] game hwnd={game.hwnd}, nimbus hwnd={int(on_qt(lambda: bridge._window.winId()))}",
                  flush=True)
            if args.attended:
                attended(app, bridge, engine, game, args.seconds)
            else:
                unattended(app, bridge, engine, game)
        except Exception as exc:   # noqa: BLE001
            record("scenario crashed", False, f"{type(exc).__name__}: {exc}")
        finally:
            try:
                on_qt(lambda: bridge.stopFullGameMode(game.hwnd if game else 0))
            except Exception:
                pass
            if game:
                game.close()
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
