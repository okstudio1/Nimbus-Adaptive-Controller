# Nimbus Adaptive Controller — Directory Structure

> **Purpose**: Quick reference for developers and AI assistants to understand the codebase layout.  
> **Last updated**: March 2026 (v1.4.1)

---

## Root Files

| File | Purpose |
|------|---------|
| `run.py` | **Main entry point** — Run this to start the app in development |
| `run.bat` | Windows batch launcher (activates venv, runs run.py) |
| `README.md` | Project overview, features, installation, usage |
| `DIRECTORY.md` | This file — project structure guide |
| `CHANGELOG.md` | Version history with semantic versioning |
| `TODO.md` | Roadmap, completed features, backlog |
| `LICENSE` | MIT License |
| `requirements.txt` | Python dependencies |
| `controller_config.json` | Runtime config (generated, gitignored in production) |
| `logo.png` | Application logo (used in splash screen, about dialog) |

---

## Source Code: `src/`

Python backend — Qt/QML bridge, configuration, hardware interfaces.

| File | Purpose |
|------|---------|
| `__init__.py` | Package init, version string |
| `qt_qml_app.py` | **Main app entry** — QML engine setup, splash screen, window init |
| `bridge.py` | **QML↔Python bridge** — All `@Slot` methods callable from QML |
| `config.py` | Configuration manager — profiles, settings, JSON persistence |
| `vjoy_interface.py` | vJoy driver communication — axis/button output |
| `vigem_interface.py` | ViGEm Xbox controller emulation (optional) |
| `uinput_interface.py` | Linux `uinput` back ends: Xbox 360 pad + 8-axis joystick (stand-ins for ViGEm/vJoy) |
| `qt_dialogs.py` | Native Qt dialogs — Joystick Settings, Button Settings, Axis Mapping |
| `qt_widgets.py` | Custom Qt widget components |
| `borderless.py` | Borderless window mode + ClipCursor release (Windows) |
| `mouse_hider.py` | Controller Mode Enforcement — keep-alive pulse + mouse hook (Windows) |
| `window_utils.py` | Game Focus Mode — save/restore foreground window (Windows) |
| `telemetry.py` | Opt-in anonymous analytics + crash reporting (local buffer, batch flush) |
| `cloud_client.py` | User accounts (Email/Google/Facebook OAuth), token management, profile sync |
| `updater.py` | Lightweight auto-update checker with version manifest and update channels |
| `qt_main.py` | Legacy Qt Widgets main (not used in QML UI) |
| `legacy/` | Old pygame-based UI (deprecated, kept for reference) |

### Key Classes

- **`ControllerBridge`** (`bridge.py`) — Singleton exposed to QML as `controller`. All QML↔Python communication goes through here.
- **`ControllerConfig`** (`config.py`) — Profile management, settings persistence, sensitivity curve calculations.
- **`VJoyInterface`** (`vjoy_interface.py`) — Low-level vJoy API wrapper.
- **`UInputXboxInterface` / `UInputJoystickInterface`** (`uinput_interface.py`) — Linux equivalents of ViGEm/vJoy over `/dev/uinput`, same method names.
- **`TelemetryClient`** (`telemetry.py`) — Opt-in event tracking with local buffer and batch HTTP flush.
- **`CloudClient`** (`cloud_client.py`) — Supabase auth, OAuth, token vault, profile sync.
- **`UpdateChecker`** (`updater.py`) — Background version manifest fetch with QML signal integration.

---

## QML UI: `qml/`

Qt Quick (QML) frontend — all UI components and layouts.

```
qml/
├── Main.qml                 # Root ApplicationWindow, menu bar, layout loader
├── components/              # Reusable UI components
│   ├── DraggableWidget.qml  # Universal drag/resize/config wrapper for all widgets
│   ├── WidgetPalette.qml    # Floating toolbar for adding widgets (edit mode)
│   ├── BorderlessGamingDialog.qml  # Borderless gaming & cursor release UI
│   ├── MacroEditorDialog.qml       # Macro joystick zone editor
│   ├── AccountDialog.qml           # Sign-in / account management (Email, Google, Facebook)
│   ├── SettingsPrivacyDialog.qml   # Telemetry opt-in/out with data schema transparency
│   ├── UpdateNotification.qml      # Non-intrusive update ribbon
│   └── ...
└── layouts/                 # Layout implementations
    ├── CustomLayout.qml     # ★ Default — drag-and-drop canvas with widget system
    ├── FlightSimLayout.qml  # Fixed dual-joystick layout (legacy, not default)
    ├── XboxLayout.qml       # Xbox gamepad layout (legacy, not default)
    └── AdaptiveLayout.qml   # Accessibility fixed layout (legacy, not default)
```

### Key QML Components

- **`Main.qml`** — Entry point, menu bar, profile switching, layout loader
- **`CustomLayout.qml`** — Canvas with edit mode, widget repeater, joystick lock overlay, config dialog
- **`DraggableWidget.qml`** — Wraps all widget types with drag, resize, delete, and double-click config

### Widget Types (in CustomLayout)

| Type | Description |
|------|-------------|
| `joystick` | 2-axis analog with triple-click mouse lock, FPS-style delta tracking |
| `button` | Single press, toggle/momentary modes, color/shape options |
| `slider` | Horizontal/vertical, 3 snap modes (none, left, center) |
| `dpad` | 4-directional digital button cluster |
| `wheel` | Rotational single-axis steering wheel |

---

## Profiles: `profiles/`

JSON profile files — copied to `%APPDATA%\ProjectNimbus\profiles\` on first run.

| File | Layout Type |
|------|-------------|
| `adaptive_platform_2.json` | `custom` — Default drag-and-drop canvas (opens on first launch) |

### Profile JSON Structure

```json
{
  "name": "Display Name",
  "description": "Profile description",
  "layout_type": "custom",
  "custom_layout": {
    "widgets": [...],
    "grid_snap": 10,
    "show_grid": true
  },
  "joystick_settings": { "sensitivity": 50, "deadzone": 0, "extremity_deadzone": 5 },
  "rudder_settings": { ... },
  "buttons": { "button_1": { "label": "A", "toggle_mode": false }, ... }
}
```

---

## Build Tools: `build_tools/`

Everything needed to package and distribute the app.

| File | Purpose |
|------|---------|
| `Project-Nimbus.spec` | PyInstaller spec — defines bundling, paths, hidden imports |
| `launcher.py` | Entry point for frozen executable |
| `installer.nsi` | NSIS installer script — wizard, shortcuts, version detection |
| `sign_exe.bat` | Code signing script (EV certificate) |
| `Project-Nimbus.ico` | Application icon (multi-resolution) |
| `Project-Nimbus.manifest` | Windows manifest (UIAccess, DPI awareness) |
| `version_info.txt` | Windows version resource info |

### Build Commands

```powershell
# Build executable
venv\Scripts\pyinstaller.exe build_tools\Project-Nimbus.spec --noconfirm

# Build installer
& "C:\Program Files (x86)\NSIS\makensis.exe" build_tools\installer.nsi

# Sign both
cmd /c build_tools\sign_exe.bat
```

---

## Documentation: `docs/`

```
docs/
├── README.md                    # Docs index
├── GAME_COMPATIBILITY.md        # Borderless gaming game compatibility list
├── setup/                       # Installation & configuration
│   ├── INSTALLATION.md          # Install guide, vJoy setup
│   ├── PROFILES.md              # Profile system, save locations
│   ├── ACCOUNTS.md              # User accounts setup (Email, Google, Facebook OAuth)
│   ├── TELEMETRY.md             # Telemetry & privacy guide
│   ├── UPDATER.md               # Auto-updater setup & hosting
│   └── PACKAGING.md             # Build & distribute guide
├── architecture/                # Technical design
│   ├── architecture.md          # Codebase structure, data flow
│   └── VDROID_DRIVER_BRAINSTORM.md  # Future custom driver plans
├── development/                 # For contributors & AI
│   ├── LLM_NOTES.md             # Implementation details, conventions, bug fixes
│   ├── INTEGRATION_GUIDE.md     # How to add new widgets/features
│   ├── START_HERE.md            # Quick orientation
│   └── WIDGET_IDEAS.md          # Planned widget concepts
├── accessibility/               # Accessibility-related
│   └── ACCESSIBILITY_SPOTLIGHT_NOMINATION.md
├── screenshots/                 # UI screenshots
└── video/                       # Demo videos
```

---

## Other Directories

| Directory | Purpose |
|-----------|---------|
| `tests/` | Test files (unit tests, integration tests) |
| `research/` | Research notes, reference materials |
| `build/` | PyInstaller build cache (gitignored) |
| `dist/` | Built executables and installers (gitignored) |
| `venv/` | Python virtual environment (gitignored) |

---

## Data Flow

```
User Input (mouse/keyboard)
    ↓
QML UI (Main.qml → CustomLayout.qml → DraggableWidget.qml)
    ↓
ControllerBridge (@Slot methods in bridge.py)
    ↓
ControllerConfig (sensitivity curves, deadzones)
    ↓
VJoyInterface (vjoy_interface.py)
    ↓
vJoy Driver → Game/Application
```

---

## Key Entry Points for AI Assistants

| Task | Start Here |
|------|------------|
| Understanding the app | `README.md`, then `docs/architecture/architecture.md` |
| Making UI changes | `qml/Main.qml`, `qml/layouts/CustomLayout.qml` |
| Adding QML↔Python features | `src/bridge.py` (add @Slot methods) |
| Changing settings/profiles | `src/config.py` |
| Building releases | `docs/setup/PACKAGING.md` |
| User accounts / auth | `src/cloud_client.py`, `docs/setup/ACCOUNTS.md` |
| Telemetry / privacy | `src/telemetry.py`, `docs/setup/TELEMETRY.md` |
| Auto-updater | `src/updater.py`, `docs/setup/UPDATER.md` |
| Bug fixes / implementation details | `docs/development/LLM_NOTES.md` |
| Adding new widget types | `docs/development/INTEGRATION_GUIDE.md` |

---

## Conventions

- **Python**: PEP 8, type hints where practical
- **QML**: camelCase for properties/functions, PascalCase for component filenames
- **Commits**: Conventional commits (`feat:`, `fix:`, `docs:`, etc.)
- **Versioning**: Semantic versioning (MAJOR.MINOR.PATCH)
- **Profiles**: JSON files in `%APPDATA%\ProjectNimbus\profiles\`
- **Config**: `controller_config.json` in working directory (dev) or install directory (production)
