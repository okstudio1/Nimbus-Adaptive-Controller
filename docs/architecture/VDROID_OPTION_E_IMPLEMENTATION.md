# VDroid Option E: Multi-Device vJoy Implementation Guide

> **Status**: Implementation Plan  
> **Priority**: High — Zero driver development, works with existing vJoy  
> **Estimated Effort**: Medium (Python + QML changes, installer updates)

> **Note, 2026-09-09.** The capacity arithmetic here is right (the axes come
> from having 16 devices, not from a wider one), but it rests on vJoy, which is
> a frozen third-party driver whose only usable build is from 2019; see
> `docs/setup/PACKAGING.md`. Phase 4 of
> [docs/vision/PAD_BUS_FORK_PLAN.md](../vision/PAD_BUS_FORK_PLAN.md) would
> enumerate the same kind of device from a driver we own and control, which is
> the same capacity without the dependency. That plan's section 10 also records
> why a single wider device does not help: 8 axes, 4 hats and 128 buttons is
> `DIJOYSTATE2`'s shape, so it is DirectInput's ceiling and not vJoy's.

---

## Overview

Option E leverages **existing vJoy infrastructure** to provide up to 128 axes (64 two-axis joysticks) and 2048 buttons without writing a custom driver. This is achieved by opening multiple vJoy devices simultaneously.

### Capacity

| Configuration | Axes | Buttons | Joysticks |
|---------------|------|---------|-----------|
| 1 device (current) | 8 | 128 | 4 |
| 4 devices | 32 | 512 | 16 |
| 8 devices | 64 | 1024 | 32 |
| 16 devices (max) | 128 | 2048 | 64 |

---

## Architecture

### Current Architecture (Single Device)

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  QML Widgets    │ ──► │ControllerBridge│ ──► │ VJoyInterface│ ──► vJoy Device 1
│ (joystick/btn)  │     │  (bridge.py)   │     │(vjoy_interface)│
└─────────────────┘     └──────────────┘     └─────────────┘
```

### Multi-Device Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│  QML Widgets    │ ──► │ControllerBridge│ ──► │ VJoyMultiDevice │
│  (with device   │     │  (bridge.py)   │     │  Manager        │
│   assignment)   │     └──────────────┘     │                 │
└─────────────────┘                           │  ┌─────────────┐│
                                              │  │ Device 1    ││ ──► vJoy #1
                                              │  │ (axes 1-8)  ││
                                              │  ├─────────────┤│
                                              │  │ Device 2    ││ ──► vJoy #2
                                              │  │ (axes 9-16) ││
                                              │  ├─────────────┤│
                                              │  │ Device N... ││ ──► vJoy #N
                                              │  └─────────────┘│
                                              └─────────────────┘
```

---

## Implementation Steps

### Phase 1: Multi-Device VJoy Interface

#### 1.1 Create `VJoyMultiDevice` Class

**File**: `src/vjoy_multi_device.py`

```python
from typing import Dict, Optional, List
import pyvjoy

class VJoyMultiDevice:
    """
    Manages multiple vJoy devices for expanded axis/button capacity.
    
    Device allocation:
    - Device 1: Axes X, Y, Z, RX, RY, RZ, SL0, SL1 (standard)
    - Device 2-16: Additional axis banks
    
    Axis naming convention:
    - "x", "y", "z", "rx", "ry", "rz", "sl0", "sl1" (device 1)
    - "x2", "y2", ... "sl1_2" (device 2)
    - "x3", "y3", ... "sl1_3" (device 3)
    - etc.
    """
    
    MAX_DEVICES = 16
    AXES_PER_DEVICE = 8
    BUTTONS_PER_DEVICE = 128
    
    AXIS_NAMES = ["x", "y", "z", "rx", "ry", "rz", "sl0", "sl1"]
    AXIS_HID_MAP = {
        "x": pyvjoy.HID_USAGE_X,
        "y": pyvjoy.HID_USAGE_Y,
        "z": pyvjoy.HID_USAGE_Z,
        "rx": pyvjoy.HID_USAGE_RX,
        "ry": pyvjoy.HID_USAGE_RY,
        "rz": pyvjoy.HID_USAGE_RZ,
        "sl0": pyvjoy.HID_USAGE_SL0,
        "sl1": pyvjoy.HID_USAGE_SL1,
    }
    
    def __init__(self, device_count: int = 1):
        """
        Initialize multi-device manager.
        
        Args:
            device_count: Number of vJoy devices to open (1-16)
        """
        self.device_count = min(max(1, device_count), self.MAX_DEVICES)
        self.devices: Dict[int, pyvjoy.VJoyDevice] = {}
        self._init_devices()
    
    def _init_devices(self) -> None:
        """Initialize all requested vJoy devices."""
        for device_id in range(1, self.device_count + 1):
            try:
                device = pyvjoy.VJoyDevice(device_id)
                self.devices[device_id] = device
                print(f"[VJoyMulti] Device {device_id} initialized")
            except Exception as e:
                print(f"[VJoyMulti] Failed to init device {device_id}: {e}")
    
    def _parse_axis_name(self, axis: str) -> tuple[int, str]:
        """
        Parse extended axis name to (device_id, base_axis).
        
        Examples:
            "x" -> (1, "x")
            "x2" -> (2, "x")
            "ry3" -> (3, "ry")
            "sl0_4" -> (4, "sl0")
        """
        axis = axis.lower()
        
        # Check for underscore format (sl0_2, sl1_3)
        if "_" in axis:
            parts = axis.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return int(parts[1]), parts[0]
        
        # Check for suffix digit (x2, ry3)
        for base in sorted(self.AXIS_NAMES, key=len, reverse=True):
            if axis.startswith(base):
                suffix = axis[len(base):]
                if suffix == "":
                    return 1, base
                if suffix.isdigit():
                    return int(suffix), base
        
        # Default to device 1
        return 1, axis
    
    def update_axis(self, axis: str, value: float) -> None:
        """
        Update an axis on the appropriate device.
        
        Args:
            axis: Axis name (e.g., "x", "y2", "rz3", "sl0_4")
            value: Normalized value (-1.0 to 1.0)
        """
        device_id, base_axis = self._parse_axis_name(axis)
        
        if device_id not in self.devices:
            return
        
        if base_axis not in self.AXIS_HID_MAP:
            return
        
        # Convert -1..1 to 0..32767 (vJoy range)
        vjoy_value = int((value + 1.0) * 0.5 * 32767)
        vjoy_value = max(0, min(32767, vjoy_value))
        
        hid_usage = self.AXIS_HID_MAP[base_axis]
        self.devices[device_id].set_axis(hid_usage, vjoy_value)
    
    def set_button(self, button_id: int, pressed: bool) -> None:
        """
        Set a button state on the appropriate device.
        
        Button IDs 1-128 go to device 1, 129-256 to device 2, etc.
        """
        device_id = ((button_id - 1) // self.BUTTONS_PER_DEVICE) + 1
        local_button = ((button_id - 1) % self.BUTTONS_PER_DEVICE) + 1
        
        if device_id not in self.devices:
            return
        
        self.devices[device_id].set_button(local_button, pressed)
    
    def get_available_axes(self) -> List[str]:
        """Return list of all available axis names."""
        axes = []
        for device_id in range(1, self.device_count + 1):
            suffix = "" if device_id == 1 else str(device_id)
            for base in self.AXIS_NAMES:
                if base.startswith("sl"):
                    axes.append(f"{base}_{device_id}" if device_id > 1 else base)
                else:
                    axes.append(f"{base}{suffix}")
        return axes
    
    def get_button_range(self) -> tuple[int, int]:
        """Return (min_button_id, max_button_id) available."""
        return (1, self.device_count * self.BUTTONS_PER_DEVICE)
    
    def shutdown(self) -> None:
        """Release all vJoy devices."""
        for device_id, device in self.devices.items():
            try:
                device.reset()
            except Exception:
                pass
        self.devices.clear()
    
    @property
    def is_connected(self) -> bool:
        return len(self.devices) > 0
```

#### 1.2 Update `VJoyInterface` to Support Multi-Device

Modify `src/vjoy_interface.py` to optionally use `VJoyMultiDevice`:

```python
# Add to VJoyInterface.__init__():
self._multi_device = None
if config.get("vjoy.multi_device_count", 1) > 1:
    from .vjoy_multi_device import VJoyMultiDevice
    count = config.get("vjoy.multi_device_count", 1)
    self._multi_device = VJoyMultiDevice(count)
```

### Phase 2: Configuration & UI

#### 2.1 Add Multi-Device Settings

**File**: `src/config.py` — Add defaults:

```python
"vjoy": {
    "device_id": 1,
    "multi_device_count": 1,  # 1-16
    "update_rate": 60
}
```

#### 2.2 Settings Dialog for Device Count

Add a "Virtual Controller" section in Settings menu:
- **Device Count** slider (1-16)
- **Available Axes** display (updates dynamically)
- **Restart Required** warning when changing

#### 2.3 Widget Axis Dropdown Update

Update axis dropdowns in widget config dialog to show extended axes:
- Device 1: X, Y, Z, RX, RY, RZ, SL0, SL1
- Device 2+: X2, Y2, Z2, RX2, RY2, RZ2, SL0_2, SL1_2, etc.

### Phase 3: Widget Device Assignment

#### 3.1 Profile Schema Update

Add `device` field to widget mapping:

```json
{
  "id": "throttle_2",
  "type": "slider",
  "mapping": {
    "axis": "z2",
    "device": 2
  }
}
```

#### 3.2 Smart Device Auto-Assignment

When adding widgets, auto-assign to devices to balance load:
1. Count axes used per device
2. Assign new widget to least-used device
3. User can override in config dialog

---

## Automatic Driver Installation

### vJoy Installation Detection

**Check if vJoy is installed:**

```python
import winreg
import os

def is_vjoy_installed() -> bool:
    """Check if vJoy driver is installed."""
    # Check registry
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8E31F76F-74C3-47F1-9550-E041EEDC5FBB}_is1"
        )
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        pass
    
    # Check for vJoy DLL
    vjoy_paths = [
        r"C:\Program Files\vJoy\x64\vJoyInterface.dll",
        r"C:\Program Files\vJoy\x86\vJoyInterface.dll",
        r"C:\Program Files (x86)\vJoy\x64\vJoyInterface.dll",
    ]
    return any(os.path.exists(p) for p in vjoy_paths)

def get_vjoy_device_count() -> int:
    """Get number of configured vJoy devices."""
    # Use vJoyInterface.dll to query
    # Returns 0 if vJoy not installed
    pass
```

### Installer Integration (NSIS)

**File**: `build_tools/installer.nsi` — Add vJoy bundling:

```nsis
; ==================== VJOY INSTALLATION ====================
Section "vJoy Driver" SEC_VJOY
    SetOutPath "$INSTDIR\drivers"
    
    ; Extract bundled vJoy installer
    File "drivers\vJoySetup.exe"
    
    ; Check if vJoy already installed
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8E31F76F-74C3-47F1-9550-E041EEDC5FBB}_is1" "DisplayVersion"
    StrCmp $0 "" install_vjoy check_version
    
check_version:
    ; vJoy is installed, check version
    ${VersionCompare} $0 "2.1.9" $1
    IntCmp $1 2 install_vjoy skip_vjoy skip_vjoy
    
install_vjoy:
    ; Run vJoy installer silently
    DetailPrint "Installing vJoy driver..."
    nsExec::ExecToLog '"$INSTDIR\drivers\vJoySetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
    Pop $0
    
    ; Configure vJoy devices (enable 4 devices by default)
    DetailPrint "Configuring vJoy devices..."
    nsExec::ExecToLog '"$INSTDIR\drivers\vJoyConfig.exe" /D:1 /A:8 /B:128'
    nsExec::ExecToLog '"$INSTDIR\drivers\vJoyConfig.exe" /D:2 /A:8 /B:128'
    nsExec::ExecToLog '"$INSTDIR\drivers\vJoyConfig.exe" /D:3 /A:8 /B:128'
    nsExec::ExecToLog '"$INSTDIR\drivers\vJoyConfig.exe" /D:4 /A:8 /B:128'
    
skip_vjoy:
    DetailPrint "vJoy driver ready"
SectionEnd

; Make vJoy installation optional but recommended
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_VJOY} "Virtual joystick driver (required for controller emulation)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END
```

### First-Run Driver Check

**File**: `src/qt_qml_app.py` — Add startup check:

```python
def check_vjoy_installation(self) -> bool:
    """Check vJoy installation and prompt user if missing."""
    if is_vjoy_installed():
        return True
    
    # Show dialog
    from PySide6.QtWidgets import QMessageBox
    result = QMessageBox.question(
        None,
        "vJoy Driver Required",
        "Project Nimbus requires the vJoy driver for virtual controller emulation.\n\n"
        "Would you like to install it now?",
        QMessageBox.Yes | QMessageBox.No
    )
    
    if result == QMessageBox.Yes:
        self._install_vjoy()
        return True
    
    return False

def _install_vjoy(self) -> None:
    """Launch vJoy installer."""
    import subprocess
    import sys
    
    installer_path = os.path.join(
        os.path.dirname(sys.executable),
        "drivers", "vJoySetup.exe"
    )
    
    if os.path.exists(installer_path):
        subprocess.Popen([installer_path], shell=True)
    else:
        # Open download page
        import webbrowser
        webbrowser.open("https://github.com/njz3/vJoy/releases")
```

---

## vJoy Device Configuration

### Programmatic Device Setup

vJoy devices must be configured before use. The `vJoyConfig.exe` tool (included with vJoy) can configure devices:

```batch
:: Enable device 1 with 8 axes and 128 buttons
vJoyConfig.exe /D:1 /A:8 /B:128

:: Enable devices 1-4
for /L %%i in (1,1,4) do vJoyConfig.exe /D:%%i /A:8 /B:128
```

### Python Configuration Script

**File**: `src/vjoy_config.py`

```python
import subprocess
import os

def configure_vjoy_devices(count: int = 4) -> bool:
    """
    Configure vJoy devices programmatically.
    
    Args:
        count: Number of devices to enable (1-16)
    
    Returns:
        True if successful
    """
    vjoy_config = find_vjoy_config()
    if not vjoy_config:
        return False
    
    for device_id in range(1, count + 1):
        cmd = [vjoy_config, f"/D:{device_id}", "/A:8", "/B:128"]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"[vJoy] Device {device_id} configured")
        except subprocess.CalledProcessError as e:
            print(f"[vJoy] Failed to configure device {device_id}: {e}")
            return False
    
    return True

def find_vjoy_config() -> str | None:
    """Find vJoyConfig.exe path."""
    paths = [
        r"C:\Program Files\vJoy\x64\vJoyConfig.exe",
        r"C:\Program Files\vJoy\x86\vJoyConfig.exe",
        r"C:\Program Files (x86)\vJoy\x64\vJoyConfig.exe",
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None
```

---

## ViGEm Integration (Optional)

For XInput compatibility, also bundle ViGEmBus:

### ViGEmBus Installation

```nsis
Section "ViGEmBus Driver" SEC_VIGEM
    SetOutPath "$INSTDIR\drivers"
    File "drivers\ViGEmBusSetup_x64.msi"
    
    ; Check if already installed
    ReadRegStr $0 HKLM "SOFTWARE\Nefarius Software Solutions e.U.\ViGEm Bus Driver" "Version"
    StrCmp $0 "" install_vigem skip_vigem
    
install_vigem:
    DetailPrint "Installing ViGEmBus driver..."
    nsExec::ExecToLog 'msiexec /i "$INSTDIR\drivers\ViGEmBusSetup_x64.msi" /quiet /norestart'
    
skip_vigem:
    DetailPrint "ViGEmBus driver ready"
SectionEnd
```

---

## Migration Path

### From Single to Multi-Device

1. **Existing profiles work unchanged** — Device 1 is the default
2. **New "Advanced" section** in profile settings for multi-device
3. **Automatic widget migration** — Option to spread widgets across devices

### Profile Compatibility

```json
{
  "vjoy_settings": {
    "device_count": 4,
    "device_assignment": "auto"  // or "manual"
  }
}
```

---

## Testing Checklist

- [ ] Single device mode still works (regression test)
- [ ] Multi-device opens 2, 4, 8, 16 devices successfully
- [ ] Axis naming convention works (x, x2, x3, sl0_4)
- [ ] Button IDs map to correct devices (1-128 → device 1, 129-256 → device 2)
- [ ] vJoy installer runs silently from NSIS
- [ ] First-run detection prompts user correctly
- [ ] Device configuration persists across restarts
- [ ] Games detect multiple vJoy devices

---

## Resources

- [vJoy GitHub (njz3 fork)](https://github.com/njz3/vJoy) — Community-maintained vJoy
- [vJoy SDK Documentation](https://github.com/njz3/vJoy/tree/master/SDK) — API reference
- [ViGEmBus Releases](https://github.com/ViGEm/ViGEmBus/releases) — Xbox emulation driver
- [NSIS Documentation](https://nsis.sourceforge.io/Docs/) — Installer scripting

---

## Summary

**Option E (Multi-Device vJoy)** provides massive capacity expansion with zero driver development:

| Benefit | Details |
|---------|---------|
| **No custom driver** | Uses existing vJoy infrastructure |
| **128 axes** | 16 devices × 8 axes each |
| **2048 buttons** | 16 devices × 128 buttons each |
| **Backward compatible** | Single device mode unchanged |
| **Easy bundling** | vJoy installer can be included |

This approach gets us 80% of the benefit of a custom driver with 10% of the effort. Only if games consistently fail to recognize multiple vJoy devices would we need to pursue custom driver development (Phase 2 in the brainstorm document).
