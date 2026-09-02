# Project Nimbus Architecture

## High-Level Overview

Project Nimbus is a Python-based modular virtual controller that converts mouse/GUI input into joystick commands via **vJoy** (DirectInput) or **ViGEm** (Xbox 360 XInput emulation). The UI is implemented with **Qt Quick (PySide6 + QML)**, backed by a Python core that manages configuration, sensitivity curves, profiles, and controller I/O.

The project's goal is to be a **free, open-source software Xbox Adaptive Controller** — enabling users with physical disabilities to build fully custom controller layouts without expensive hardware.

### Data Flow

```
User (mouse) → QML UI → ControllerBridge (Python) → ControllerConfig (curves/mapping)
                                                   → VJoyInterface or ViGEmInterface
                                                   → Virtual Controller Driver
                                                   → Game / Application
```

## Launch Flow

### 1. Top-Level Launcher (`run.py`)

- Performs Python version checks and virtual environment setup.
- Ensures dependencies (PySide6, pyvjoy, numpy, etc.) are installed.
- Launches the QML application module: `python run.py` → runs `src.qt_qml_app` in the venv.
- For bundled executables, `build_tools/launcher.py` is used instead.

### 2. QML Application Entry (`src/qt_qml_app.py`)

- Creates `QApplication` and `QQmlApplicationEngine`.
- Instantiates `ControllerConfig` and `ControllerBridge`.
- Exposes to QML as context properties: `controller` and `config`.
- Loads `qml/Main.qml` and starts the Qt event loop.

### 3. QML UI (`qml/`)

`Main.qml` defines the main window, menu bar, profile system, and a **layout loader** that switches between layout components based on profile `layout_type`:

| Layout Type | QML Component | Description |
|-------------|---------------|-------------|
| `custom` | `CustomLayout.qml` | **★ Default — modular drag-and-drop canvas** |
| `flight_sim` | `FlightSimLayout.qml` | Fixed dual joysticks + throttle/rudder (legacy) |
| `xbox` | `XboxLayout.qml` | Fixed Xbox gamepad layout (legacy) |
| `adaptive` | `AdaptiveLayout.qml` | Fixed accessibility-focused layout (legacy) |

### 4. Controller Interface Selection

The bridge auto-selects the controller backend based on profile layout type:
- **ViGEm** (preferred for `custom`) — Xbox 360 emulation for XInput games
- **vJoy** (fallback) — DirectInput with 8 axes + 128 buttons

---

## Custom Layout System (Adaptive Platform 2)

The `custom` layout type is the architectural centerpiece of the modular controller builder. Instead of hardcoded widget positions, everything is driven by a JSON widget array stored in the profile.

### Architecture

```
Profile JSON                    QML Rendering
─────────────                   ─────────────
custom_layout.widgets[] ──→ CustomLayout.qml (canvas)
  ├── {type: "joystick", ...}       ├── Repeater over widget model
  ├── {type: "button", ...}         ├── DraggableWidget.qml (per widget)
  ├── {type: "slider", ...}         │   ├── Edit mode: drag/resize/delete/config
  └── ...                           │   └── Play mode: interactive control
                                    ├── WidgetPalette.qml (sidebar, edit mode only)
                                    └── Widget Config Dialog (double-click)
```

### Widget Schema

Each widget in `custom_layout.widgets[]` has:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (e.g. `"left_stick"`, `"btn_a"`) |
| `type` | string | `"joystick"`, `"button"`, `"slider"`, `"dpad"`, `"wheel"` |
| `x`, `y` | number | Position on canvas (pixels, grid-snapped) |
| `width`, `height` | number | Size (pixels, grid-snapped) |
| `label` | string | Display label |

**Type-specific fields:**

- **Joystick**: `mapping.axis_x`, `mapping.axis_y` (e.g. `"x"/"y"`, `"rx"/"ry"`, `"sl0"/"sl1"`)
- **Button**: `button_id` (1–128), `color` (hex), `shape` (`"circle"/"rounded"/"square"`), `toggle_mode`
- **Slider**: `mapping.axis` (e.g. `"z"`, `"rz"`, `"sl0"`), `orientation` (`"horizontal"`/`"vertical"`), `snap_mode` (`"none"`/`"left"`/`"center"`), `click_mode` (`"jump"`/`"relative"`)
- **D-Pad**: `mapping` with `up`/`down`/`left`/`right` button IDs
- **Wheel**: `mapping.axis` (single rotational axis)

### Edit Mode vs. Play Mode

| Feature | Edit Mode | Play Mode |
|---------|-----------|-----------|
| Drag widgets | ✅ | ❌ |
| Resize widgets | ✅ | ❌ |
| Delete widgets | ✅ | ❌ |
| Config dialog (double-click) | ✅ | ❌ |
| Widget Palette (pop-out window) | ✅ visible | ❌ hidden |
| Grid overlay | ✅ optional | ❌ hidden |
| Interactive controls | ❌ blocked | ✅ active |
| Selection border | ✅ blue outline | ❌ none |

### DraggableWidget.qml

The universal wrapper component. It:
1. Receives widget data as properties (`widgetType`, `widgetId`, `mapping`, etc.)
2. In edit mode: renders drag handle, resize handle, delete button, type label
3. In play mode: renders the interactive control via a `Loader` that selects the correct `Component`:
   - `joystickContent` — full joystick with thumb, base circle, axis output, triple-click mouse lock
   - `buttonContent` — colored button with toggle/momentary support
   - `sliderContent` — horizontal/vertical slider with 3 snap modes, jump/relative click, smooth snap-back easing, and fill bar
   - `dpadContent` — 4-directional button cluster with arrow symbols
   - `wheelContent` — rotational single-axis with spoke indicator
4. Exposes `joystickLocked`, `updateJoystickPosition()`, `triggerTripleClick()` for the parent overlay
5. `_applyCurve()` applies sensitivity using the same formula as `config.py:apply_joystick_dialog_curve()`

### Widget Palette (Pop-Out Window)

The palette is rendered as a separate `Window` with `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`:
- Custom draggable title bar (dark blue, drag to reposition)
- Stays on top so it's always accessible
- Never overlaps the canvas since it's a separate OS window
- Auto-shows/hides with edit mode
- Contains: add widget buttons, axis limits info, toggle grid, save as, done editing

### Mouse Lock Overlay

When a joystick is triple-click locked, `CustomLayout.qml` shows a full-canvas `MouseArea` overlay:
- `hoverEnabled: true` — tracks mouse position without clicking
- Maps global coordinates to the locked joystick's local space via `mapToItem()`
- Calls `updateJoystickPosition(nx, ny)` on the locked widget
- Handles triple-click for unlock
- Joystick returns to center (0,0) when unlocked
- Fixes the edge-sticking bug from the old hover-based approach

### Sensitivity Curve (Per-Widget)

Each axis widget stores sensitivity settings as percentages (0–100), matching the Settings menu:
- `sensitivity` (50% = linear, <50% = exponential, >50% = responsive)
- `dead_zone` (0–100%, maps to 0–0.25 of axis range internally)
- `extremity_dead_zone` (0–100%, scales max output)

The `_applyCurve()` function in `DraggableWidget.qml` uses the identical formula as `apply_joystick_dialog_curve()` in `config.py`.

The config dialog includes a **Response Curve Preview** canvas that draws the curve in real-time as sliders change.

### Persistence

- Widget positions/sizes/config saved to profile JSON under `custom_layout`
- `ControllerConfig.save_custom_layout(widgets, grid_snap, show_grid)` writes to disk
- Bridge slots: `getCustomLayout()`, `saveCustomLayout()`, `saveCustomLayoutAs()`, `getCustomLayoutGridSnap()`, `getCustomLayoutShowGrid()`
- Auto-saves on every widget move/resize/config change and when exiting edit mode
- Save As dialog for creating named layout profiles

---

## Core Python Components

### `ControllerConfig` (`src/config.py`)

Configuration manager responsible for:
- Loading defaults and merging with `controller_config.json`
- `get`/`set` helpers with dot-notation paths (e.g. `"joystick_settings.sensitivity"`)
- Persisting settings to disk (`save_config`)
- **Profile system**: load/save/switch/duplicate/delete/reset profiles
- **Custom layout persistence**: `save_custom_layout()` for the modular canvas
- UI scaling utilities: `get_scaled_value`, `get_scaled_int`, `set_scale_factor`
- Input processing: sensitivity curves, deadzone/extremity handling, normalized-to-vJoy conversion

### `VJoyInterface` (`src/vjoy_interface.py`)

Wrapper around the vJoy driver (via `pyvjoy`):
- Supports **8 axes**: X, Y, Z, RX, RY, RZ, SL0, SL1
- Supports up to **128 buttons**
- APIs: `update_axis(axis, value)`, `set_button(button_id, pressed)`, `get_status()`
- Maps logical axis names to vJoy HID constants
- Handles initialization, validation, centering, and failsafe

### `ViGEmInterface` (`src/vigem_interface.py`)

Xbox 360 controller emulation via ViGEm/vgamepad:
- **2 analog sticks** (left/right), **2 triggers** (LT/RT), **14 buttons**
- Compatible API with VJoyInterface: `update_axis()`, `set_button()`
- Auto-selected for `xbox`, `adaptive`, and `custom` layout types when available
- Provides XInput compatibility for games like No Man's Sky

### `UInputXboxInterface` / `UInputJoystickInterface` (`src/uinput_interface.py`)

Linux back ends over the kernel `uinput` module (pure Python: `ioctl` + `struct`, no python-evdev):
- `UInputXboxInterface` mirrors `ViGEmInterface`'s API and creates a `Microsoft X-Box 360 pad` (xpad VID/PID, so SDL/Steam apply their built-in mapping). Sticks are negated on Y to convert XInput `+Y == up` to evdev `+Y == down`; the D-pad drives `ABS_HAT0X/Y`.
- `UInputJoystickInterface` mirrors `VJoyInterface`'s API: 8 axes (`ABS_X..ABS_RZ`, `ABS_THROTTLE`, `ABS_RUDDER`) with the vJoy `0..axis_range` scaling, 56 buttons (`BTN_JOYSTICK` block + `BTN_TRIGGER_HAPPY1..40`).
- The bridge chooses them through `_create_xbox_interface()` / `_create_joystick_interface()` when `UINPUT_AVAILABLE`; `XBOX_OUTPUT_AVAILABLE = VIGEM_AVAILABLE or UINPUT_AVAILABLE` drives the "vigem" mode availability. Only the active device is kept alive on Linux (`_retire_inactive_interface()`).
- Setup, permissions, and verification: [docs/setup/LINUX.md](../setup/LINUX.md)

### `ControllerBridge` (`src/bridge.py`)

Qt `QObject` exposed to QML as `controller`:
- Owns `ControllerConfig` + controller interface (VJoy or ViGEm on Windows, uinput on Linux)
- **Properties**: `scaleFactor`, `debugBorders`, `buttonsVersion`, `noFocusMode`
- **Axis slots**: `setLeftStick`, `setRightStick`, `setThrottle`, `setRudder`, `setAxis`
- **Button slot**: `setButton(id, pressed)`
- **Profile slots**: `switchProfile`, `saveCurrentProfile`, `createProfileAs`, `deleteProfile`
- **Custom layout slots**: `getCustomLayout`, `saveCustomLayout`, `getCustomLayoutGridSnap`, `getCustomLayoutShowGrid`
- **Settings dialog openers**: `openAxisSettings`, `openButtonSettings`, `openSliderSettings`
- **Borderless gaming slots**: `isBorderlessAvailable`, `getRunningWindows`, `getGameCompatibility`, `autoDetectGame`, `makeGameBorderless`, `restoreGameWindow`, `startCursorRelease`, `stopCursorRelease`, `applyBorderlessAndRelease`, `restoreAndStopRelease`
- **Axis smoothing**: QTimer-based interpolation toward target values before sending to vJoy

### `WindowUtils` (`src/window_utils.py`)

Windows-specific utilities for Game Focus Mode:
- Saves foreground window on mouse press, restores on release
- Uses Windows API: `GetForegroundWindow`, `SetForegroundWindow`, `AttachThreadInput`
- Only available on Windows; gracefully disabled on other platforms

### `Borderless` (`src/borderless.py`)

Borderless gaming and mouse capture (Windows only, pure ctypes):
- **`enumerate_windows()`** — lists all visible top-level windows with size/position/state
- **`make_borderless(hwnd)`** — strips WS_CAPTION/WS_THICKFRAME, resizes to fill monitor
- **`restore_window(hwnd)`** — restores original window style and size
- **`start_cursor_release(interval_ms)`** — background thread polling `ClipCursor(NULL)` to fight games that re-apply cursor confinement every frame
- **`stop_cursor_release()`** — stops polling
- **`auto_detect_game()`** — matches running windows against 30+ game compatibility entries
- **`GAME_COMPATIBILITY`** — built-in database of verified/likely/partial/incompatible games
- Exposed via 12 `ControllerBridge` slots (see bridge section above)
- UI: `qml/components/BorderlessGamingDialog.qml` — game picker, auto-detect, one-click apply, compatibility browser

---

## Settings & Dialogs (`src/qt_dialogs.py`)

Qt Widgets-based dialogs invoked from the bridge:

| Dialog | Class | Purpose |
|--------|-------|---------|
| Axis Configuration | `AxisSettingsQt` | Per-axis sensitivity, deadzone, VJoy mapping |
| Slider/Trigger Settings | `SliderSettingsQt` | Throttle/rudder or trigger sensitivity |
| Button Settings | `ButtonSettingsQt` | Toggle vs. momentary mode per button |

All dialogs read/write through `ControllerConfig` and persist to the current profile.

---

## Profile System

Profiles are JSON files stored in the user data directory:

| Platform | Location |
|----------|----------|
| Windows | `%APPDATA%\ProjectNimbus\profiles\` |
| macOS | `~/Library/Application Support/ProjectNimbus/profiles/` |
| Linux | `~/.local/share/ProjectNimbus/profiles/` |

### Bundled Profile

| Profile | Layout Type | Description |
|---------|-------------|-------------|
| `adaptive_platform_2` | `custom` | **Default — modular drag-and-drop canvas** |

This is the only bundled profile. It opens automatically on first launch. Users create additional profiles via **File > Save Profile As...** or the Widget Palette's Save As button.

### Profile Lifecycle

1. `adaptive_platform_2` copied to user directory on first run
2. User can modify, save, duplicate, or create new profiles
3. The bundled profile can be reset to defaults
4. Switching profiles reloads settings and swaps the QML layout

---

## Hardware Capabilities

### vJoy (DirectInput)

| Resource | Limit |
|----------|-------|
| Axes | 8 (X, Y, Z, RX, RY, RZ, SL0, SL1) |
| Joysticks | Up to 4 (each uses 2 axes) |
| Buttons | Up to 128 |
| POV hats | Supported |

### ViGEm (Xbox 360 XInput)

| Resource | Limit |
|----------|-------|
| Analog sticks | 2 (left, right) |
| Triggers | 2 (LT, RT) |
| Buttons | 14 (A, B, X, Y, LB, RB, Back, Start, L3, R3, D-Pad ×4) |

---

## Alternative Shells & Legacy Code

### Qt Widgets Shell (`src/qt_main.py`)
- Alternative (non-QML) interface for experimentation. Not the default entry point.

### Legacy Pygame UI (`src/legacy/`)
- Original pygame-based UI. Kept for reference only; not used by `run.py`.

---

## Build & Packaging

- **Source**: `python run.py` (sets up venv, installs deps, launches QML app)
- **Executable**: PyInstaller one-file build via `build_tools/build_exe.bat`
- **Installer**: NSIS installer via `build_tools/installer.nsi` (built automatically if NSIS is in PATH)
- **EV Signing**: `build_tools/sign_exe.bat` signs both exe and installer with SHA-256 + DigiCert timestamp
- **UIAccess**: `build_tools/Project-Nimbus.manifest` embeds `uiAccess="true"` for on-screen keyboard parity
- **Icon**: `build_tools/Project-Nimbus.ico` (multi-size: 16–256px)
- **Version**: Defined in `src/__init__.py`, `build_tools/version_info.txt`, and `build_tools/Project-Nimbus.spec`

### Build Workflow
```
build_exe.bat  →  PyInstaller (Project-Nimbus.exe)  →  NSIS (Project-Nimbus-Setup-X.Y.Z.exe)
sign_exe.bat   →  signtool signs both .exe files with EV cert + timestamp
```

See `build_tools/BUILD_EXECUTABLE.md` and `build_tools/CODE_SIGNING.md` for detailed steps.
