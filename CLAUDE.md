# Nimbus Adaptive Controller

Free, open-source modular virtual controller for Windows. Turns mouse/GUI input into vJoy (DirectInput) or ViGEm (Xbox 360 XInput) joystick output. Accessibility-first software alternative to the Xbox Adaptive Controller. Python core plus a Qt Quick (PySide6/QML) UI.

## Key rules
- The QML UI talks to Python only through `ControllerBridge` (src/bridge.py), the single QObject exposed to QML as context property `controller` (config as `config`). QML to Python is `@Slot` methods; Python to QML is Signals. Apply sensitivity curves, deadzones, and smoothing inside the bridge before forwarding to a driver interface, never in QML or the interfaces.
- Two controller backends must stay behavior-compatible: `VJoyInterface` (8 axes X,Y,Z,RX,RY,RZ,SL0,SL1; 128 buttons) and `ViGEmInterface` (4 axes + 2 triggers; 14 buttons). The bridge selects one per profile `layout_type`. When adding or changing a `set_axis`/`set_button`-style method, mirror it in both and honor each backend's hard limits.
- Custom layouts are a three-way contract: profile JSON `custom_layout.widgets[]`, the rendering in `qml/layouts/CustomLayout.qml` + `qml/components/DraggableWidget.qml`, and the bridge mapping. Adding a widget `type` or field means updating all three (plus `profiles/adaptive_platform_2.json` if it is a default).
- Windows/driver-specific modules (vigem_interface, window_utils, borderless, mouse_hider) are imported under try/except into `*_AVAILABLE` flags. Keep that graceful-degradation pattern so the app still starts with the driver missing or on non-Windows. `src/mouse_isolation_win.py` is not imported by the bridge yet (its Linux twin lives on the `linux-uinput-support` branch); wire it in the same way when that branch merges.
- `driver/nimbus_moufilter/nimbus_moufilter_ioctl.h` and the constants at the top of `src/mouse_isolation_win.py` are one contract (device name, IOCTL codes, status struct, `MOUSE_INPUT_DATA` packing). Change them together and bump `NIMBUS_MOUFILTER_INTERFACE_VERSION`. The Windows and Linux `MouseIsolation` classes must keep the same constructor, `start`/`stop`, and callback signatures so `bridge.py` can use either once the Linux branch merges.
- Telemetry stays opt-in only, no PII, hashed identifiers, local-first (src/telemetry.py, src/cloud_client.py, keyring for secrets). Do not add default-on collection or log identifying data.
- `controller_config.json` is generated per-machine and is gitignored, so never commit it. Bundled default profiles live in `profiles/`; user profiles live in `%APPDATA%/ProjectNimbus/profiles/`. The internal app/data-dir name is `ProjectNimbus` (config.py `APP_NAME`), so do not rename it.
- Do not add features to non-primary shells: `src/legacy/` (pygame) and `src/qt_main.py` + `src/qt_widgets.py` (Qt Widgets) are reference-only and not at feature parity. The QML app is the one true UI.

## Stack & layout
- Python 3.8+ with PySide6 (Qt Quick/QML), pyvjoy, vgamepad (ViGEm), numpy, PyInstaller.
- `src/` Python core: bridge, config, vjoy_interface, vigem_interface, borderless, window_utils, mouse_hider, mouse_isolation_win, telemetry, cloud_client.
- `qml/` UI: `Main.qml`, `layouts/`, `components/`.
- `profiles/` bundled default profile JSON. `build_tools/` PyInstaller packaging. `tests/` vJoy hardware diagnostics and the Windows input probes. `docs/` architecture and dev notes.
- `driver/` the Nimbus Mouse Filter (KMDF, Windows only). Built with `driver\build.ps1`, which needs Visual Studio 2022 with the WDK component and WDK 10.0.26100; it is not part of `run.py` or the installer yet. Loading it needs test signing (see `driver/README.md`). Never install or load a kernel driver on someone's machine without asking.

## Build, run, test
- Run: `python run.py` (auto-creates `venv/`, installs requirements, launches `src.qt_qml_app`). Modules run as packages, so keep `src.`-qualified or relative imports.
- Deps: `pip install -r requirements.txt`.
- Package: follow `build_tools/BUILD_EXECUTABLE.md` (PyInstaller via `build_tools/Nimbus-Adaptive-Controller.spec`).
- Tests: the `tests/` files are vJoy/driver diagnostics that need a real vJoy install (for example `python tests/test_vjoy.py`), not an automated suite. pytest is not a dependency and some scripts use stale imports. There is no CI.

## Conventions
- PEP 8, type hints, and numpy-style docstrings on public classes and methods (match src/bridge.py). No linter or formatter is configured, so keep the existing style and avoid ruff/black reformatting churn.
- Keep vJoy axis names uppercase (X, Y, Z, RX, RY, RZ, SL0, SL1); button IDs are 1-based.

## When to ask
- Changing the profile / `custom_layout` JSON schema or the `controller_config.json` shape (needs migration plus all three layers updated).
- Changing telemetry scope, cloud endpoints, or credential handling.
- Touching a driver interface in a way that could inject unintended input, or changing failsafe / rate-limit behavior.