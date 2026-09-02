# Linux Setup

Nimbus Adaptive Controller runs on Linux. The Qt Quick UI is unchanged; the
output layer uses the kernel's `uinput` module instead of the vJoy and
ViGEmBus drivers, so games, Steam, Proton, and browsers see an ordinary
controller. No driver install and no compiled Python packages are needed.

## Requirements

- Any recent distribution with kernel 4.5 or newer (2016+)
- Python 3.8+ with the `venv` module (`sudo apt install python3-venv` on Debian/Ubuntu)
- Write access to `/dev/uinput` (see [Permissions](#permissions))
- A desktop session. X11 and Wayland both work for the UI; see [Wayland notes](#wayland-notes)

Qt needs the usual X11/xcb libraries. Desktop installs already have them; on a
minimal Debian/Ubuntu system:

```bash
sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxkbcommon-x11-0 libegl1 libgl1
```

## Install and run

```bash
git clone https://github.com/owenpkent/Nimbus-Adaptive-Controller.git
cd Nimbus-Adaptive-Controller
./run.sh
```

`run.sh` calls `run.py`, which creates `venv/`, installs `requirements.txt`
(the Windows-only `pyvjoy` and `vgamepad` packages are skipped automatically
via environment markers), and starts the app. To run it by hand:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python run.py
```

Profiles live in `~/.local/share/ProjectNimbus/profiles/` (or
`$XDG_DATA_HOME/ProjectNimbus/profiles/`).

## Permissions

`/dev/uinput` is `root:root 0600` by default. **If Steam is installed you are
already set**: Steam ships `60-steam-input.rules`, which grants the logged-in
user access. Otherwise install the rule from this repository once:

```bash
sudo cp build_tools/linux/60-nimbus-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --name-match=uinput
```

Then log out and back in. The rule uses systemd-logind's `uaccess` tag for
the active seat and also opens the node to the `input` group as a fallback
for systems without logind (`sudo usermod -aG input $USER`).

If `/dev/uinput` does not exist at all, load the module and make it permanent:

```bash
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
```

When access fails the app still starts, shows "not connected" in the status
bar, and prints the fix in the terminal. Nothing else is affected.

## Verify

```bash
./venv/bin/python tests/test_uinput.py            # creates both devices, checks every axis/button
./venv/bin/python tests/test_uinput.py --hold 60  # keep them alive so you can inspect them
```

While a device is held (or the app is running) you can see it with
`evtest` (`sudo apt install evtest`), `jstest-gtk`, or Steam > Settings >
Controller. The Xbox device appears as **Microsoft X-Box 360 pad**; the
generic one as **Nimbus Virtual Joystick**.

## Output modes on Linux

The **Output Device** menu (Settings, or the status-bar chip) keeps the same
two choices as Windows; the table shows what each becomes on Linux.

| Menu label on Linux | Windows equivalent | Device created | Use it for |
|---|---|---|---|
| **Xbox 360 gamepad (uinput)** | ViGEm Xbox 360 (XInput) | `Microsoft X-Box 360 pad`, 2 sticks, 2 triggers, D-pad, 10 buttons | Steam, Proton, and any game that expects a gamepad (default for `xbox`, `adaptive`, and `custom` profiles) |
| **Generic joystick (uinput)** | vJoy (DirectInput) | `Nimbus Virtual Joystick`, 8 axes (X, Y, Z, RX, RY, RZ, SL0, SL1), 56 buttons | Flight sims, emulators, and anything that binds raw joystick axes |

Details that differ from Windows:

- **Button limit**: the generic joystick exposes 56 buttons (evdev has 56
  generic joystick codes) where vJoy allows 128. Button IDs above 56 are
  ignored with a one-time warning in the terminal.
- **One device at a time**: switching output mode or profile destroys the
  device that is no longer in use, so games never see two Nimbus controllers.
  On Windows both drivers stay attached.
- **Axis ranges** match the Windows back ends exactly: profile settings,
  sensitivity curves, and the INV toggles behave the same on both platforms.
- Profiles are portable between Windows and Linux without changes; the
  `vigem`/`vjoy` mode names are kept as platform-neutral identifiers.

## Game Mode and focus on Linux

- **Game Focus Mode** (View menu) works on X11. It sets Qt's
  `WindowDoesNotAcceptFocus` flag on the Nimbus window, so clicking or
  dragging on Nimbus never takes keyboard focus away from the game while
  pointer input still reaches Nimbus. Under Wayland the compositor decides
  focus, so the item is disabled there.
- **Game Mode** (the ▶ button in the status bar) runs controller-mode
  enforcement: an initial burst of stick deflections plus an A press, then a
  30 Hz sub-deadzone left-stick oscillation that keeps a game with dual input
  detection in gamepad mode (Xbox prompts, no mouse chasing). Your real stick
  values are restored every tick. On X11 it also turns on Game Focus Mode for
  the session. Click the button again to stop. There is no `Ctrl+Alt+F12`
  emergency hotkey on Linux; the button and quitting the app are the stop
  paths, and both re-centre the stick.
- If the current profile outputs to the generic joystick, Game Mode creates
  the Xbox pad on demand and removes it again when you stop.

## What is Windows-only

Borderless window conversion, ClipCursor release polling, and the low-level
mouse hook rely on Win32 APIs and are not available. Under Wine/Proton a
game's `ClipCursor` becomes an X pointer grab, so a game that confines the
pointer while in mouse mode behaves as it does on Windows; controller mode
is the counter-measure. The design that removes the physical mouse from the
game entirely (an `EVIOCGRAB` exclusive grab) is discussed in
[docs/vision/HOST_MODE_ISOLATION.md](../vision/HOST_MODE_ISOLATION.md) and
[docs/vision/LINUX_PROBE_PLAN.md](../vision/LINUX_PROBE_PLAN.md). A safe
probe for it lives in `tests/probe_evdev_grab.py`; it grabs a virtual mouse
rather than yours and needs your user in the `input` group:

```bash
sudo usermod -aG input $USER    # then log out and back in
./venv/bin/python tests/probe_evdev_grab.py
```

## Steam and Proton

Steam Input picks the virtual pad up like any wired Xbox 360 controller, so
Steam games, Proton titles, and Big Picture all work without configuration.
Steam may take an exclusive grab on the device while a game runs; that is
normal and is how it feeds the game. If a game shows keyboard prompts instead
of Xbox glyphs, check Steam > Settings > Controller and make sure the pad is
listed.

## Wayland notes

The UI renders and works on Wayland (KDE, GNOME, Sway). Two window behaviours
are compositor-controlled there and may not apply:

- "Always on top" is not honoured by every compositor.
- Programmatic window positioning is ignored by most compositors.
- Game Focus Mode is disabled: a Wayland client cannot opt out of keyboard
  focus on its own. Game Mode still runs the controller pulse.

If you need the panel to float over a fullscreen game, an X11 session or
running the game under `gamescope` is the reliable path today. Set
`QT_QPA_PLATFORM=xcb` to force the X11 backend (XWayland) if a Wayland
session misbehaves.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not create uinput device: [Errno 13] Permission denied` | Install the udev rule above (or Steam) and log back in |
| `[Errno 2] No such file or directory: '/dev/uinput'` | `sudo modprobe uinput` |
| Qt prints `Could not load the Qt platform plugin "xcb"` | Install the xcb libraries listed under [Requirements](#requirements) |
| Device shows up but the game ignores it | Try the other output mode; some engines only scan for gamepads (Xbox mode) or only for joysticks (generic mode) |
| Two Nimbus controllers listed | An older app instance is still running; close it |
| Game Focus Mode is greyed out | You are on Wayland; use an X11 session or run the game under `gamescope` |
| Game shows keyboard prompts again after a while | Some games drop controller mode on any mouse click; keep Game Mode running and avoid clicking inside the game window |
| Cursor moves the game camera as well as the on-screen stick | Expected today; see the host-mode research doc for the evdev grab plan |
