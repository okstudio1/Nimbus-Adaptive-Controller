# Installation Guide

Windows is the primary platform. Linux is supported from source — jump to
[Linux Installation](#linux-installation).

## Prerequisites

- **Windows 10/11** (64-bit)
- **vJoy driver** — must be installed separately from [vJoy on GitHub](https://github.com/njz3/vJoy) or [SourceForge](https://sourceforge.net/projects/vjoystick/)
- Optional: **ViGEmBus driver** for Xbox 360 controller emulation (`pip install vgamepad` installs it)

## Installing Nimbus Adaptive Controller

### From Installer (Recommended)

1. Download `Nimbus-Adaptive-Controller-Setup-X.Y.Z.exe` from the [Releases page](https://github.com/owenpkent/Nimbus-Adaptive-Controller/releases)
2. Run the installer
3. **UAC admin prompt** — Enter your Windows admin password (required for proper installation)
4. The installer wizard will:
   - Check if Nimbus Adaptive Controller is already running and offer to close it
   - Detect any previous installation (per-user or system-wide) and offer to remove it
   - Let you choose the installation directory (default: `%LOCALAPPDATA%\Programs\Project Nimbus\`)
   - **Shortcut options page** — Choose whether to create Desktop and/or Start Menu shortcuts
   - Install the executable and create uninstaller
   - Register in Windows Add/Remove Programs
5. Click "Launch Nimbus Adaptive Controller" on the final page to start

### From Source (Development)

```bash
git clone https://github.com/owenpkent/Nimbus-Adaptive-Controller.git
cd Nimbus-Adaptive-Controller
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## Installer Behavior

### Previous Version Detection

The installer checks **both** registry locations for existing installations:
- `HKCU\...\Uninstall\project-nimbus-virtual-controller` (per-user installs)
- `HKLM\...\Uninstall\project-nimbus-virtual-controller` (system-wide installs)

If found:
- Prompts you to remove the old version first (recommended)
- The old version's silent uninstaller (`/S` flag) is run if you choose to remove it
- If you skip removal, the new version installs to your chosen directory

### Running Instance Detection

If `Nimbus-Adaptive-Controller.exe` is already running, the installer offers to close it via `taskkill` before proceeding.

### Settings Preservation

**User settings are preserved across installs and upgrades.** Here's why:

| Data | Location | Survives Reinstall? |
|------|----------|-------------------|
| User profiles | `%APPDATA%\ProjectNimbus\profiles\` | ✅ Yes |
| App config | `controller_config.json` (in install dir) | ⚠️ Deleted on uninstall |
| Bundled profiles | Inside the executable (PyInstaller) | Regenerated on first run |

- **Profiles** (custom layouts, widget configurations, sensitivity settings) are stored in `%APPDATA%\ProjectNimbus\profiles\` which is **never touched by the installer or uninstaller**
- **Bundled default profiles** (flight_simulator, xbox, adaptive_platform, etc.) are embedded in the executable. On first run, any missing profiles are copied to the user directory. Existing user profiles are never overwritten.
- **`controller_config.json`** stores global settings (current profile, window size, vjoy device ID). This file is in the install directory and **is deleted on full uninstall**. It is regenerated with defaults on next launch.

### Uninstall

The uninstaller removes:
- The executable and uninstaller from the install directory
- `controller_config.json` from the install directory
- Desktop and Start Menu shortcuts (if created)
- Windows registry entries (both HKCU and HKLM)

**Optional user data removal**: At the end of uninstall, you'll be asked if you want to remove your saved profiles and settings from `%APPDATA%\ProjectNimbus\`. Choose "No" to keep them for future installations.

## Building the Installer

Requirements:
- [NSIS](https://nsis.sourceforge.io/) (Nullsoft Scriptable Install System)
- PyInstaller-built executable in `dist/Nimbus-Adaptive-Controller.exe`
- Icon file in `build_tools/Nimbus-Adaptive-Controller.ico`

```bash
# Build the executable
pyinstaller Nimbus-Adaptive-Controller.spec

# Build the installer
cd build_tools
makensis installer.nsi
```

The installer is output to `dist/Nimbus-Adaptive-Controller-Setup-X.Y.Z.exe`.

### Code Signing

The executable and installer should be signed with an EV code certificate for SmartScreen trust:

```bash
build_tools\sign_exe.bat dist\Nimbus-Adaptive-Controller.exe
build_tools\sign_exe.bat dist\Nimbus-Adaptive-Controller-Setup-X.Y.Z.exe
```

## vJoy Setup

1. Download vJoy from [GitHub releases](https://github.com/njz3/vJoy/releases)
2. Run the installer (requires admin rights)
3. Open **vJoyConf** (vJoy Configuration) and ensure:
   - Device 1 is enabled
   - At least 8 axes are configured (X, Y, Z, RX, RY, RZ, Slider 0, Slider 1)
   - At least 128 buttons are enabled
4. Launch Nimbus Adaptive Controller — it should detect vJoy automatically

## Linux Installation

There is no vJoy on Linux. Nimbus outputs an Xbox 360-compatible gamepad through
the kernel `uinput` device instead, which Steam, Proton, SDL, and native games
see as a standard pad on `/dev/input/js*`. The Qt Quick UI, profiles, layouts,
curves, deadzones, and smoothing are unchanged.

### Prerequisites

- Python 3.8+
- `libevdev` (`sudo apt install libevdev2`, `sudo dnf install libevdev`, `sudo pacman -S libevdev`)
- A writable `/dev/uinput`

### Setup

```bash
git clone https://github.com/owenpkent/Nimbus-Adaptive-Controller.git
cd Nimbus-Adaptive-Controller
./run.sh          # equivalent to: python3 run.py
```

`run.py` creates `venv/`, installs `requirements.txt` (skipping the Windows-only
`pyvjoy`), checks `/dev/uinput`, and launches the QML UI.

### uinput Setup

`/dev/uinput` is the Linux equivalent of the ViGEmBus driver. Without write
access, Nimbus starts but the status dot stays red and nothing reaches a game.

```bash
# 1. Load the kernel module (and make it load at boot)
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf

# 2. Grant your user write access
sudo groupadd -f uinput
sudo usermod -aG uinput "$USER"
echo 'KERNEL=="uinput", GROUP="uinput", MODE="0660", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/99-nimbus-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# 3. Log out and back in, then verify
ls -l /dev/uinput
```

On desktops where systemd-logind applies a `uaccess` ACL, the logged-in user
already has access and step 2 is unnecessary.

### Verifying Output

With Nimbus running:

```bash
ls /dev/input/js*     # a new joystick node appears
sudo evtest           # should list "Xbox 360 Controller"
```

The control surface matches the Windows ViGEm backend exactly: 4 axes,
2 triggers, 14 buttons (D-pad on `ABS_HAT0X/Y`).

### Unsupported on Linux

Borderless Gaming, Game Focus Mode, and Full Game Mode are Win32-only — they
depend on `ClipCursor`, `WS_EX_NOACTIVATE`, and low-level mouse hooks. The UI
hides or disables those controls on Linux rather than offering dead buttons.

Verified on Ubuntu 22.04 / X11. Under Wayland the UI renders but the compositor
restricts always-on-top and programmatic positioning; see
[`docs/vision/LINUX_PROBE_PLAN.md`](../vision/LINUX_PROBE_PLAN.md).

## Troubleshooting

- **"Failed to initialize VJoy device 1"** — vJoy driver not installed, or another application is using device 1. Close other vJoy apps and retry.
- **"Cannot acquire vJoy Device because it is not in VJD_STAT_FREE"** — Previous instance of Nimbus Adaptive Controller didn't shut down cleanly. Wait a few seconds and try again, or restart vJoy via Device Manager.
- **SmartScreen warning** — The executable must be signed with an EV code certificate. Unsigned builds will trigger SmartScreen warnings.
- **Linux: "/dev/uinput is not writable by this user"** — Follow [uinput Setup](#uinput-setup). The group change needs a fresh login to take effect.
- **Linux: "/dev/uinput does not exist"** — The kernel module is not loaded. Run `sudo modprobe uinput`.
- **Linux: "vgamepad not available"** — `libevdev` is missing. Install `libevdev2` (Debian/Ubuntu) or `libevdev` (Fedora/Arch).
- **Linux: vJoy menu entry is greyed out** — Expected. vJoy is a Windows kernel driver; use the Xbox 360 (ViGEm) output mode.
