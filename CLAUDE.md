# Nimbus Adaptive Controller

Free, open-source modular virtual controller for Windows. Turns mouse/GUI input into vJoy (DirectInput) or ViGEm (Xbox 360 XInput) joystick output. Accessibility-first software alternative to the Xbox Adaptive Controller. Python core plus a Qt Quick (PySide6/QML) UI.

## Key rules
- The QML UI talks to Python only through `ControllerBridge` (src/bridge.py), the single QObject exposed to QML as context property `controller` (config as `config`). QML to Python is `@Slot` methods; Python to QML is Signals. Apply sensitivity curves, deadzones, and smoothing inside the bridge before forwarding to a driver interface, never in QML or the interfaces.
- Two controller backends must stay behavior-compatible: `VJoyInterface` (8 axes X,Y,Z,RX,RY,RZ,SL0,SL1; 128 buttons) and `ViGEmInterface` (4 axes + 2 triggers; 14 buttons). The bridge selects one per profile `layout_type`. When adding or changing a `set_axis`/`set_button`-style method, mirror it in both and honor each backend's hard limits.
- On Linux, `UInputJoystickInterface` and `UInputXboxInterface` (src/uinput_interface.py, pure-Python `/dev/uinput` ioctls) stand in for vJoy and ViGEm with the same method names; the bridge picks them via `_create_joystick_interface()` / `_create_xbox_interface()` and `XBOX_OUTPUT_AVAILABLE`. Any interface method change must be mirrored there too. The joystick device is capped at 56 buttons (evdev limit) and only the active device is kept alive on Linux.
- Custom layouts are a three-way contract: profile JSON `custom_layout.widgets[]`, the rendering in `qml/layouts/CustomLayout.qml` + `qml/components/DraggableWidget.qml`, and the bridge mapping. Adding a widget `type` or field means updating all three (plus `profiles/adaptive_platform_2.json` if it is a default).
- Windows/driver-specific modules (vigem_interface, window_utils, borderless, mouse_hider) and the Linux-only uinput_interface are imported under try/except into `*_AVAILABLE` flags. Keep that graceful-degradation pattern so the app still starts with the driver missing or on another OS.
- Controller mode has two implementations: `mouse_hider.py` on Windows (pulse + Win32 hooks) and `controller_pulse.py` everywhere else (pulse only, driven through the interface API). The bridge switches on `_USE_MOUSE_HIDER`; keep the burst/pulse parameters in sync if you tune one. Game Focus Mode is `WS_EX_NOACTIVATE` on Windows and `Qt.WindowDoesNotAcceptFocus` on X11 (`_apply_no_focus_flag`).
- Mouse Isolation (Linux, `src/mouse_isolation.py`) grabs pointer devices with `EVIOCGRAB`. Its callbacks run on a reader thread; only the bridge's `_IsolationRelay` queued signals may touch Qt. Never grab a device that carries keyboard keys unless the uinput pass-through keyboard was created, and keep every stop path (toggle, hotkey, `stop_all`, atexit) releasing the grab. Test with a virtual device, not the user's mouse.
- Telemetry stays opt-in only, no PII, hashed identifiers, local-first (src/telemetry.py, src/cloud_client.py, keyring for secrets). Do not add default-on collection or log identifying data.
- `controller_config.json` is generated per-machine and is gitignored, so never commit it. Bundled default profiles live in `profiles/`; user profiles live in `%APPDATA%/ProjectNimbus/profiles/`. The internal app/data-dir name is `ProjectNimbus` (config.py `APP_NAME`), so do not rename it.
- Do not add features to non-primary shells: `src/legacy/` (pygame) and `src/qt_main.py` + `src/qt_widgets.py` (Qt Widgets) are reference-only and not at feature parity. The QML app is the one true UI.

## Stack & layout
- Python 3.8+ with PySide6 (Qt Quick/QML), pyvjoy, vgamepad (ViGEm), numpy, PyInstaller.
- `src/` Python core: bridge, config, vjoy_interface, vigem_interface, borderless, window_utils, telemetry, cloud_client.
- `qml/` UI: `Main.qml`, `layouts/`, `components/`.
- `profiles/` bundled default profile JSON. `build_tools/` PyInstaller packaging. `tests/` vJoy hardware diagnostics. `docs/` architecture and dev notes.

## Build, run, test
- Run: `python run.py` on Windows or `./run.sh` on Linux (auto-creates `venv/`, installs requirements, launches `src.qt_qml_app`). Modules run as packages, so keep `src.`-qualified or relative imports. Linux needs write access to `/dev/uinput` (docs/setup/LINUX.md).
- Deps: `pip install -r requirements.txt`.
- Package: follow `build_tools/BUILD_EXECUTABLE.md` (PyInstaller via `build_tools/Nimbus-Adaptive-Controller.spec`).
- Tests: the `tests/` files are driver diagnostics, not an automated suite: `python tests/test_vjoy.py` needs a real vJoy install; `python tests/test_uinput.py` runs on Linux and round-trips every axis/button through the kernel; `python tests/probe_evdev_grab.py` checks the EVIOCGRAB mouse-isolation mechanism (needs the `input` group and an X11 display); `python tests/probe_game_mouselook.py --window "ELDEN RING"` measures whether a running game still sees a grabbed mouse (frame diff). pytest is not a dependency and some scripts use stale imports. There is no CI.

## Conventions
- PEP 8, type hints, and numpy-style docstrings on public classes and methods (match src/bridge.py). No linter or formatter is configured, so keep the existing style and avoid ruff/black reformatting churn.
- Keep vJoy axis names uppercase (X, Y, Z, RX, RY, RZ, SL0, SL1); button IDs are 1-based.

## When to ask
- Changing the profile / `custom_layout` JSON schema or the `controller_config.json` shape (needs migration plus all three layers updated).
- Changing telemetry scope, cloud endpoints, or credential handling.
- Touching a driver interface in a way that could inject unintended input, or changing failsafe / rate-limit behavior.