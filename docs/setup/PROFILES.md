# Profile System

## Overview

Nimbus Adaptive Controller uses a profile system to store different controller configurations. Each profile defines:
- **Layout type** — which UI layout to use (flight_sim, xbox, adaptive, custom)
- **Custom layout** — widget positions, sizes, and per-widget configuration
- **Axis mapping** — which vJoy axes map to which controls
- **Button settings** — toggle/momentary modes per button
- **Sensitivity settings** — joystick and rudder sensitivity curves, deadzones

## Storage Locations

### User Profiles (Persistent)

```
%APPDATA%\ProjectNimbus\profiles\
├── flight_simulator.json
├── xbox.json
├── adaptive_platform.json
├── adaptive_platform_2.json    ← default custom layout
└── my_custom_layout.json       ← user-created profiles
```

**Path on disk:** `C:\Users\<username>\AppData\Roaming\ProjectNimbus\profiles\`

This directory is:
- Created automatically on first launch
- **Never deleted** by the installer or uninstaller
- Safe across upgrades, reinstalls, and version changes

### Bundled Default Profiles

Default profiles are embedded inside the PyInstaller executable. On startup, `ControllerConfig._ensure_user_profiles()` copies any missing bundled profiles to the user directory. **Existing user profiles are never overwritten** — only missing ones are added.

In development mode, bundled profiles are read from `<project_root>/profiles/`.

### Global Config File

```
controller_config.json
```

This file is saved in the **working directory** (install dir for installed app, project root for dev). It stores:
- `current_profile` — which profile is active
- `joystick_settings` — global sensitivity/deadzone values
- `rudder_settings` — global rudder sensitivity values
- `ui` — window dimensions, scale factor, debug borders
- `axis_mapping` — current axis assignments
- `vjoy` — device ID, update rate

**Note:** This file is deleted on uninstall. It is regenerated with defaults on next launch. Profile-specific settings (custom layouts, per-widget configs) are stored in the profile JSON files and are preserved.

## Profile JSON Structure

### Built-in Profile Example (flight_simulator.json)

```json
{
    "name": "Flight Simulator",
    "description": "Dual joystick flight sim layout",
    "layout_type": "flight_sim",
    "axis_mapping": {
        "left_x": "x",
        "left_y": "y",
        "right_x": "rx",
        "right_y": "ry",
        "throttle": "z",
        "rudder": "rz"
    },
    "buttons": {
        "button_1": { "label": "1", "toggle_mode": false },
        "button_9": { "label": "ARM", "toggle_mode": true }
    },
    "joystick_settings": {
        "sensitivity": 35.0,
        "deadzone": 0.0,
        "extremity_deadzone": 38.0
    },
    "rudder_settings": {
        "sensitivity": 50.0,
        "deadzone": 10.0,
        "extremity_deadzone": 5.0
    }
}
```

### Custom Layout Profile Example (adaptive_platform_2.json)

```json
{
    "name": "Adaptive Platform 2",
    "description": "Custom drag-and-drop layout",
    "layout_type": "custom",
    "custom_layout": {
        "grid_snap": 10,
        "show_grid": true,
        "widgets": [
            {
                "id": "joystick_1",
                "type": "joystick",
                "label": "Left Stick",
                "x": 50, "y": 200,
                "width": 200, "height": 200,
                "mapping": { "axis_x": "x", "axis_y": "y" },
                "triple_click_enabled": true,
                "auto_center": true,
                "auto_center_delay": 5,
                "lock_sensitivity": 4.0,
                "tremor_filter": 0.0,
                "sensitivity": 50.0,
                "dead_zone": 0.0,
                "extremity_dead_zone": 5.0
            },
            {
                "id": "button_1",
                "type": "button",
                "label": "A",
                "x": 400, "y": 350,
                "width": 60, "height": 60,
                "button_id": 1,
                "color": "#4CAF50",
                "shape": "circle",
                "toggle_mode": false
            }
        ]
    }
}
```

## Profile Operations

| Operation | Menu Location | What It Does |
|-----------|--------------|--------------|
| Switch profile | File → Profile → [name] | Loads a different profile and applies its settings |
| Save profile | File → Save Profile | Saves current settings to the active profile file |
| Save As | File → Save Profile As... | Creates a new profile from current settings with a custom name |
| Reset profile | File → Profile → Reset [name] | Restores a built-in profile to its original defaults |
| Delete profile | File → Profile → Delete [name] | Removes a user-created profile (built-in profiles cannot be deleted) |
| Open folder | File → Open Profiles Folder | Opens `%APPDATA%\ProjectNimbus\profiles\` in Explorer |

## Custom Layout Widget Properties

Each widget in a custom layout profile stores:

### Common Properties
- `id`, `type`, `label`, `x`, `y`, `width`, `height`

### Joystick-Specific
- `mapping.axis_x`, `mapping.axis_y` — vJoy axis pair (x/y, rx/ry, z/rz, sl0/sl1)
- `triple_click_enabled` — allow triple-click mouse lock
- `auto_center` — auto-return to center when locked and idle
- `auto_center_delay` — delay before return (1-10ms)
- `lock_sensitivity` — FPS-style delta multiplier (1-10, actual = value × 2)
- `tremor_filter` — EMA smoothing for jittery input (0-10), applied in the bridge on the drag path and the lock path
- `travel_px`: mouse pixels from the press point to full deflection; 0 or absent follows the drawn radius (81 px for a 200 px stick)
- `precision_gain`: fraction of the stick's range left while a Precision aim button is held (default 0.25)
- `sensitivity`, `dead_zone`, `extremity_dead_zone` — axis response curve, applied radially to the vector's magnitude so the dead region is a circle
- `anti_deadzone`, `anti_deadzone_buffer`: output floor as fractions of the range. Absent means the documented XInput constant for the mapped stick (0.24 left, 0.265 right) when the output is ViGEm and 0 under vJoy; the buffer (default 0.02) only counts above a non-zero floor

All shaping runs in the bridge (`src/bridge.py`), which reads these by widget id; the QML sends raw geometry.

### Button-Specific
- `button_id` — vJoy button number (1-128)
- `color`, `shape` — visual appearance
- `toggle_mode` — toggle vs momentary
- `modifier`: `"none"` (default) sends the gamepad button; `"precision"` holds the precision aim modifier instead, momentary or latched by `toggle_mode`

### Slider-Specific
- `mapping.axis` — single vJoy axis
- `snap_mode` — "none" (hold), "left" (return to zero), "center" (spring to center)
- `click_mode` — "jump" (teleport) or "relative" (drag)
- `orientation` — "horizontal" or "vertical"
- `sensitivity`, `dead_zone`, `extremity_dead_zone`, `anti_deadzone`, `anti_deadzone_buffer`: as for joysticks, except the extremity cap defaults to 0. Hold and return-to-zero sliders are shaped from their bottom end (deadzone at 0, cap at full); centre-sprung sliders and wheels are shaped around the centre

### D-Pad-Specific
- `mapping.up`, `mapping.down`, `mapping.left`, `mapping.right` — vJoy button IDs

### Wheel-Specific
- `mapping.axis` — single vJoy axis

## Development vs Installed Paths

| Context | Profiles Dir | Bundled Profiles | Config File |
|---------|-------------|-----------------|-------------|
| Development | `%APPDATA%\ProjectNimbus\profiles\` | `<repo>/profiles/` | `<repo>/controller_config.json` |
| Installed | `%APPDATA%\ProjectNimbus\profiles\` | Inside `Nimbus-Adaptive-Controller.exe` (PyInstaller) | `%LOCALAPPDATA%\Project Nimbus\controller_config.json` |

Both modes use the same `%APPDATA%` profiles directory, so **switching between dev and installed versions shares the same profiles**.
