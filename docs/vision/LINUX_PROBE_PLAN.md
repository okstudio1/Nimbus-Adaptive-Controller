# Linux Probe Plan

**Status:** Proposed experiment. Nothing here is part of Nimbus; the scripts below are throwaway.
**Parent doc:** [HOST_MODE_ISOLATION.md](HOST_MODE_ISOLATION.md)
**Cost:** One weekend, $0, existing hardware.

The purpose is to settle the Linux question empirically before any porting decision. Two independent probes, each with a clear pass/fail. Probe 1 tests whether Linux actually solves the input problem. Probe 2 tests whether the Nimbus UI can exist on Wayland at all. **Both must pass for a port to be viable**, and they can fail independently, so run both.

---

## Background: the one thing being tested

On Windows, Nimbus cannot stop a Raw Input game from seeing the physical mouse (see [GAME_COMPATIBILITY.md](../GAME_COMPATIBILITY.md)). The Linux claim is that `EVIOCGRAB` takes exclusive ownership of the mouse device so nothing else, including the game, receives its events, while Nimbus forwards synthesized stick motion through a `uinput` virtual gamepad.

Everything else in the Linux argument follows from that claim being true. So test it first and test it directly.

---

## Probe 1: Exclusive grab plus virtual pad, against a real EAC game

### Setup

| Item | Choice | Why |
|---|---|---|
| Distro | **Bazzite** (live USB or spare drive) | Fedora Atomic, ships Steam, Proton, and gamescope preconfigured. CachyOS or Nobara are fine alternatives. Avoid a bare Arch install for a time-boxed probe. |
| GPU | AMD preferred | RADV/Mesa is in-tree. NVIDIA works in 2026 but adds variables you don't want in a probe. |
| Game | **Elden Ring** | EAC is enabled for Proton, it is Deck Verified, and it is the exact camera-drift case that is only partially compatible on Windows today. |
| Session | KDE Plasma Wayland | Matches what Probe 2 needs. |

### Safety first

`EVIOCGRAB` is all-or-nothing. Once you grab the mouse, **the desktop loses the pointer too.** Before running anything:

- Run the probe from a terminal you can reach with the keyboard, so `Ctrl+C` always works.
- Build an unconditional auto-ungrab timeout into the script (included below).
- Know your VT switch keys (`Ctrl+Alt+F3`) as a last resort.

This mirrors the existing `Ctrl+Alt+F12` emergency stop in Full Game Mode, and the same reasoning applies: never ship or run an input grab without a guaranteed release path.

### Steps

1. Boot Bazzite, install Steam, install Elden Ring, and confirm it launches under Proton with the EAC handshake completing. **Stop here if this fails**; everything downstream depends on it.
2. Identify the mouse event node:
   ```bash
   ls -l /dev/input/by-id/ | grep -i mouse
   sudo evtest    # confirm which node emits REL_X / REL_Y as you move
   ```
   Note: many mice expose **multiple** event nodes. Note them all; see failure mode F1.
3. Install probe deps: `pip install evdev vgamepad`
4. Run the probe script (below) and confirm the desktop pointer freezes.
5. Launch Elden Ring, ideally wrapped in gamescope:
   ```bash
   gamescope -f -- %command%      # as Steam launch options
   ```
6. Move the physical mouse and observe the in-game camera.

### Probe script

Use **vgamepad** first, not raw uinput. It is the library [vigem_interface.py](../../src/vigem_interface.py) already depends on, so this tests the real Nimbus code path on Linux rather than a parallel implementation. Fall back to raw `evdev.UInput` only if vgamepad's experimental Linux backend misbehaves.

```python
"""Throwaway probe. Not Nimbus code. Grabs the mouse, emits right-stick motion."""
import time
import evdev
import vgamepad as vg

MOUSE = "/dev/input/eventN"      # from step 2
SENSITIVITY = 0.004              # tune by feel
TIMEOUT_S = 120                  # hard safety release

dev = evdev.InputDevice(MOUSE)
pad = vg.VX360Gamepad()
x = y = 0.0
deadline = time.monotonic() + TIMEOUT_S

dev.grab()                       # EVIOCGRAB: this is the whole experiment
try:
    for event in dev.read_loop():
        if time.monotonic() > deadline:
            break
        if event.type == evdev.ecodes.EV_REL:
            if event.code == evdev.ecodes.REL_X:
                x = max(-1.0, min(1.0, x + event.value * SENSITIVITY))
            elif event.code == evdev.ecodes.REL_Y:
                y = max(-1.0, min(1.0, y - event.value * SENSITIVITY))
            pad.right_joystick_float(x_value_float=x, y_value_float=y)
            pad.update()
finally:
    dev.ungrab()                 # must always run
```

Self-centering, smoothing, and deadzones are deliberately omitted. The probe is not trying to feel good, it is trying to answer one question.

### Pass criteria

All four must hold:

- [ ] **P1.** Moving the physical mouse moves the in-game camera via the right stick.
- [ ] **P2.** The game's own mouse-look does **not** respond. No double input, no drift. *This is the entire test.* On Windows this is the step that fails.
- [ ] **P3.** Elden Ring shows Xbox button glyphs, confirming the uinput device is detected as a standard pad.
- [ ] **P4.** EAC does not complain, refuse to launch, or flag the session.

### Failure modes and what they mean

| # | Symptom | Meaning | Next step |
|---|---|---|---|
| **F1** | Camera responds to both stick and mouse-look | The grab is not covering every event node for that physical mouse | Grab all of them, then re-test. Common and usually benign. |
| **F2** | Game ignores the pad entirely | uinput device shape or VID/PID not matching what SDL expects | Try raw `evdev.UInput` with explicit Xbox 360 VID/PID, or check `SDL_JOYSTICK_HIDAPI` |
| **F3** | EAC refuses or flags the session | uinput devices treated as suspicious | Serious, but unlikely: Steam Input itself is uinput-based and coexists with EAC on Deck |
| **F4** | Grab works, but the Nimbus-equivalent UI cannot read the mouse either | **Expected, and a real design consequence, not a bug** | See "Architectural consequence" below |

### Architectural consequence to record either way

F4 is worth calling out in advance because it is certain to happen and it changes the design. On Windows, Nimbus's Qt window receives mouse events for free from the OS while the hooks fight the game for them. Under `EVIOCGRAB`, Nimbus steals the device from the **compositor as well**, so its own QML window stops receiving Qt mouse events.

The consequence: Nimbus would read raw deltas from evdev directly and drive its own on-screen cursor and stick position internally, rather than relying on Qt's mouse handling. That is arguably cleaner, since it is what the virtual stick conceptually wants anyway, but it is a real change to how [bridge.py](../../src/bridge.py) receives input and it should be scoped, not discovered late.

---

## Probe 2: Can the Nimbus UI live on Wayland?

Independent of Probe 1, and the likelier of the two to fail. Nimbus is a floating always-on-top panel positioned beside a game. Wayland's client isolation, the same property that makes Probe 1 work, restricts exactly that.

Known constraints going in: `Qt.WindowStaysOnTopHint` does not work under KWin Wayland, and `move()` / `setGeometry()` are not honored. The workaround is `layer-shell-qt` or KWindowSystem, and GNOME/Mutter does not implement layer-shell at all.

### Steps

1. Run the existing Nimbus QML app on Bazzite under KDE Plasma Wayland. Most of it should start unchanged; PySide6 and QML are cross-platform. Expect the Windows-specific modules to fall back through their `*_AVAILABLE` flags, which is the graceful-degradation pattern working as intended.
2. Attempt to keep the window above a fullscreen gamescope game.
3. Attempt to position it at a specific screen coordinate.
4. Repeat steps 2 and 3 with `layer-shell-qt`.
5. Note behavior under GNOME as well, to size the portability tax.

### Pass criteria

- [ ] **P5.** The QML UI renders and is interactive on Wayland.
- [ ] **P6.** It can be kept above a fullscreen game, by any means.
- [ ] **P7.** It can be positioned deliberately, by any means.
- [ ] **P8.** The approach that achieves P6 and P7 works on more than one compositor, or the KDE-only restriction is judged acceptable.

P8 is the one to think hard about. A solution that only works on KDE means shipping Linux support that is really "KDE support," which is a legitimate choice but should be a deliberate one.

---

## Interpreting the results

| Probe 1 | Probe 2 | Conclusion |
|---|---|---|
| Pass | Pass | The Linux path is real. Scope the port properly: QML UI and bridge move mostly unchanged, three Windows modules get deleted, input intake gets rewritten around evdev. |
| Pass | Fail | The input model works but the form factor does not. Consider a different UI shape on Linux (gamescope overlay, separate device, or Steam Input integration) rather than porting the panel as-is. |
| Fail | Either | The core premise is wrong and the whole Linux argument in the parent doc collapses. Fall back to the Windows options: cloud gaming, two-PC streaming, or the filter driver. |

Whatever the outcome, record it back into [HOST_MODE_ISOLATION.md](HOST_MODE_ISOLATION.md) section 5, since that section currently rests on reasoning rather than measurement.

## Explicitly out of scope

- Porting Nimbus. No `src/` changes should result from these probes.
- Profile schema, telemetry, or packaging concerns.
- Performance tuning, latency measurement, or curve/deadzone feel.
- Any game other than Elden Ring. One decisive case beats five ambiguous ones.
