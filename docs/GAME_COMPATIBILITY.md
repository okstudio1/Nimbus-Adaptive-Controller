# Game Compatibility — Borderless Gaming & Mouse Capture

Project Nimbus includes built-in borderless gaming support that lets you free your cursor from games that lock it, so you can interact with the Nimbus controller interface while gaming.

## How It Works

Games capture the mouse using one of three mechanisms:

| Mechanism | How Nimbus Handles It |
|---|---|
| **ClipCursor** — game confines cursor to its window rect | Nimbus continuously releases the clip via polling (`ClipCursor(NULL)`) |
| **Exclusive Fullscreen** — game takes over the display | Nimbus converts the window to borderless windowed mode |
| **Raw Input**: game reads mouse deltas from the HID stack (`WM_INPUT`) | Turn Raw Input **off** in the game's settings if it has the option, then use **Controller Mode** (see below). If it has no option, nothing in user mode helps (measured, see the Incompatible section); the Nimbus Mouse Filter kernel driver in `driver/` is the fix in development: with it, Full Game Mode takes the physical mouse away from the game while the real cursor keeps working on Nimbus and the desktop. Not in any release yet. |

## Access

**View → Borderless Gaming...** opens the control panel where you can:
- Auto-detect running games from our compatibility database
- Select any window and convert it to borderless fullscreen
- Toggle continuous cursor release on/off
- Adjust the release polling speed

---

## Controller Mode Enforcement (Full Game Mode)

**Full Game Mode** is a more powerful alternative to simple cursor release. Use it when the game camera still moves with the mouse even after cursor release is enabled.

### How it works

1. **ViGEm keep-alive pulse**: Sends a continuous stream of tiny sub-deadzone Xbox 360 controller inputs so the game stays in "controller mode" and voluntarily releases the mouse
2. **WH_MOUSE_LL hook**: Installs a system-wide low-level mouse hook that suppresses `WM_MOUSEMOVE` events over the game window — the camera receives zero mouse delta
3. **ClipCursor release**: Fights the game's cursor confinement at 2ms polling so you can still reach the Nimbus window

### Requirements
- **ViGEmBus driver** must be installed (Project Nimbus prompts for this automatically)
- **Raw Input: OFF** in the game's mouse/video settings — `WH_MOUSE_LL` intercepts `WM_MOUSEMOVE` but cannot intercept `WM_INPUT` (raw device events)
- Game must be in **windowed or borderless** mode (not exclusive fullscreen)

**On Linux** no driver is needed: the Xbox pad is a kernel `uinput` device, and the same Game Mode button runs controller-mode enforcement, an X11 Game Focus Mode equivalent, and **Mouse Isolation**, an exclusive `EVIOCGRAB` of the physical mouse. With the mouse isolated, the Raw Input mechanism behind the Incompatible tier below has nothing to read; anti-cheat support under Proton is the remaining per-game question. Verified with **Carrier Command 2** (Proton): the cockpit free-look stopped responding to a grabbed mouse entirely (1 changed frame sample versus 23,845 ungrabbed), and the game recognised the uinput pad as an Xbox 360 controller. Borderless conversion and cursor release are Windows-only. See [Linux Setup](setup/LINUX.md).

### Emergency stop
Press **Ctrl+Alt+F12** at any time to instantly kill Controller Mode and restore normal mouse behavior. This hotkey works even if Nimbus doesn't have focus.

### Which games benefit from Controller Mode?
Any game that:
- Uses a standard Windows message loop for mouse input (most games)
- Has a "Raw Input" or "DirectInput" toggle in settings (switch it OFF)
- Supports XInput controllers natively (detects the virtual Xbox 360 pad)

**Games that will NOT work** even with Controller Mode:
- Games that read the mouse through Raw Input (`WM_INPUT`) with no option to turn it off. The `WH_MOUSE_LL` hook is never consulted for `WM_INPUT`, so it cannot block them. Measured on Elden Ring under EAC on 2026-09-05: the hook dropped every mouse event and the camera still turned, and EAC did not react to the hook in that one session. That is one data point, not a policy: other kernel anti-cheat (Vanguard in particular) was not measured and may block or flag the hook, so treat those titles as unsupported. See [Host Mode & Input Isolation, section 8](vision/HOST_MODE_ISOLATION.md#8-measured-on-windows-2026-09-05).

### How it generalizes across game engines

| Engine | Notes |
|---|---|
| **Java (Minecraft)** | Raw Input toggle in Mouse Settings. Set Raw Input: OFF. Works perfectly. |
| **Unity** | Most Unity games use `WM_MOUSEMOVE`. No setting change needed. |
| **Unreal Engine** | Has a "Raw Input" or "Use Mouse for Touch" option — disable raw input. |
| **Source Engine** | `m_rawinput 0` in console disables raw input. Works with Controller Mode. |
| **GameMaker** | Uses standard WM_MOUSEMOVE. No setting change needed. |
| **Godot** | Uses standard WM_MOUSEMOVE. No setting change needed. |
| **XNA / MonoGame / FNA** | Uses standard WM_MOUSEMOVE. No setting change needed. |

---

## Measured With the Game Test Harness

`tests/probe_game_harness_windows.py` launches a game from a recipe in `tests/games/`, owns the virtual pad, and measures what stick input does to it, from the game's own console where it has one and from frame differencing otherwise. What it has measured so far (dev machine, 2026-09-07; the full log is in [Game Test Harness, section 8](vision/GAME_TEST_HARNESS.md)):

| Game | Oracle | Right-stick deadzone | Notes |
|---|---|---|---|
| Left 4 Dead 2 | console | between 0.26 and 0.28 (the XInput constant the anti-deadzone default assumes) | turn linear in time below the stop, about 480 degrees a second at full deflection (Source's stick acceleration); a 1 px Nimbus drag turns the camera 2.4 degrees a second |
| Elden Ring | frame differencing | at or below 0.20 | camera moves at every magnitude swept; the default anti-deadzone floor is above this game's threshold, so the smallest Nimbus movement is a little faster than it needs to be here |
| Half-Life 2 | console | **between 0.30 and 0.40** | above the default floor: a 1 px Nimbus drag sends 0.289 and the camera does not move at all, so **raise Anti-DZ to about 40 for this game**. It also acts on the pad's axes and never on its buttons (see below). Turn rates are exact at 0.40 and 0.60 (about 48 and 178 degrees a second) and too fast to measure above that |
| PowerWash Simulator | frame differencing | below 0.26 (0.20 is the marginal one) | camera moves at every magnitude from 0.26 up, and the default floor clears it. Its menus are the thing to know about: they are driven by a virtual pointer the left stick moves, and A clicks whatever is under it, so a layout for this game needs a left stick and an A button to get from the title screen into a job |

Four games, four thresholds, one of them above the default: the XInput constant is a sound starting point and cannot be right everywhere, which is why Anti-DZ is per widget and the widget dialog has a test pad to calibrate it against the game in front of you. A game whose deadzone sits below the default floor can lower Anti-DZ; one above it, as Half-Life 2 is, needs it raised or the smallest movement does nothing. Adding a game is a recipe file; a Source engine game gets numbers, anything else gets moved-or-still verdicts.

## Verified Games

These games have been tested and confirmed working with Project Nimbus.

| Game | Method | Notes |
|---|---|---|
| **Minecraft (Java Edition)** | Controller Mode | Set Raw Input: OFF in Options → Controls → Mouse Settings. Use Full Game Mode. |
| **Stardew Valley** | ClipCursor | XNA/MonoGame engine. Very accessible with controller support. |
| **Terraria** | ClipCursor | XNA/MonoGame engine. Great accessibility with controller support. |
| **The Elder Scrolls V: Skyrim** | ClipCursor | Set to windowed mode in launcher. Full controller support via ViGEm/vJoy. |
| **Carrier Command 2** | Controller Mode | Measured on Windows 2026-09-05 (v1.5.18): the `WH_MOUSE_LL` hook stops in-game mouse-look completely (11,945 changed frame samples unhooked, 25 hooked, noise floor 50), so it is not a Raw Input game. Its first-person view does not respond to the gamepad right stick, so drive the carrier through its screens with the mouse rather than expecting stick camera control. |
| **Left 4 Dead 2** | Controller Mode | Source engine. Measured on Windows 2026-09-06 (windowed, `+exec 360controller`): the right stick turns the camera through ViGEm, and the game's own inner deadzone sits at the XInput constant (still at 0.26, moving at 0.28), which Nimbus's default anti-deadzone clears (a 1 px drag on the aim stick moved the camera). **Start Nimbus before the game**: Source decides at launch whether an Xbox controller is present and never sees one that appears later. Raw Input mouse-look stays on unless `m_rawinput 0`; use the stick for the camera. |
| **Half-Life 2** (and the episodes) | Controller Mode, sticks only | Source 2013. Measured on Windows 2026-09-07 (20th-anniversary build, windowed): both sticks work through ViGEm, but **the game never acts on the pad's buttons**, so jump, use and reload have to come from the keyboard. That was confirmed by binding all fourteen, reading the binds back from the game with `key_listboundkeys`, and watching the stick still turn the view between one ignored press and the next. Its camera deadzone is higher than most, between 0.30 and 0.40, so **raise Anti-DZ to about 40** or the smallest movement does nothing. **Start Nimbus before the game**, as with any Source title. |
| **PowerWash Simulator** | Controller Mode | Unity, no anti-cheat, so it starts under test signing. Measured on Windows 2026-09-08 with the game test harness (11/11 on the pad and 11/11 through Nimbus): both sticks work through ViGEm, the camera deadzone is below 0.26 so the default Anti-DZ needs no change, and a 1 px Nimbus drag moves the camera. **Its menus need a stick, not just buttons**: they are driven by an on-screen pointer that only the left stick moves, and A clicks whatever the pointer is over, so a button-only layout can reach nothing on the main menu. The game also ignores its own `-screen-width` / `-screen-height` launch arguments and applies its saved display preference a few seconds after launch. |

## Likely Compatible Games

High confidence based on engine analysis. These games use ClipCursor and should work well.

| Game | Input Method | Notes |
|---|---|---|
| **No Man's Sky** | ClipCursor | Has native borderless option. Excellent controller support — use ViGEm Xbox profile. |
| **Celeste** | ClipCursor | XNA/FNA engine. Strong controller support. Excellent built-in accessibility features. |
| **Hollow Knight** | ClipCursor | Unity engine. Good controller support. |
| **Hades** | ClipCursor | Custom engine with standard windowing. Excellent controller support. |
| **Slay the Spire** | ClipCursor | Java/LibGDX. Turn-based — very accessible for adaptive controllers. |
| **Civilization VI** | ClipCursor | Turn-based strategy — ideal for adaptive controllers. Has native borderless option. |
| **Factorio** | ClipCursor | Pausable — great for players who need extra time. |
| **Among Us** | ClipCursor | Unity engine. Simple controls — very accessible. |
| **Undertale / Deltarune** | ClipCursor | GameMaker engine. Simple controls. Excellent for adaptive play. |
| **Cuphead** | ClipCursor | Unity engine. Excellent controller support. |
| **Rocket League** | ClipCursor | Unreal Engine. Has built-in borderless option. Use ViGEm Xbox profile. |
| **Fall Guys** | ClipCursor | Unity engine. Good controller support. |
| **The Binding of Isaac: Rebirth** | ClipCursor | Roguelike — no penalty for taking time. |
| **Portal / Portal 2** | ClipCursor | Source engine. Puzzler — can be paused freely. |

## Partially Compatible Games

Some features work, with limitations.

| Game | Input Method | Notes |
|---|---|---|
| **Elden Ring** | ClipCursor + Raw Input | Use controller mode (ViGEm); the mouse camera won't work. Set to windowed in settings. **Measured on Windows 2026-09-05 (v1.17, EAC online):** with a `WH_MOUSE_LL` hook dropping 100% of mouse events the camera still turned (1,759 changed frame samples vs 1,781 unhooked, noise floor 433). Making a Nimbus-like window the foreground window stopped the mouse (312) but the game then ignored the ViGEm pad too (65 vs 12,412 when focused), so the focus trick is not a workaround. Needs the kernel filter in [Windows Mouse Filter Plan](vision/WINDOWS_MOUSE_FILTER_PLAN.md). Fully compatible on Linux: Mouse Isolation makes the raw-input camera blind to the mouse, the Nimbus pad drives it, and EAC accepted the session (verified 2026-09-03). |
| **Dark Souls III** | ClipCursor + Raw Input | Similar to Elden Ring — use controller mode. Set to windowed in settings first. |

## Incompatible Games

These games use Raw Input exclusively. External cursor release has no effect. Use a second monitor, tablet input device, or game streaming instead.

**On Linux**, Mouse Isolation removes the mouse from the equation entirely, so what decides a title in this tier there is only its anti-cheat under Proton: EAC titles that enable Proton support work (Elden Ring verified), while Vanguard titles do not run on Linux at all.

> **Why this tier can't be fixed in user mode:** `WH_MOUSE_LL` is a Win32 message-level hook, but Raw Input is delivered from the HID stack and never passes through it. Closing this gap requires either isolating the game from the physical mouse or filtering the mouse below `win32k`. The Nimbus Mouse Filter in `driver/` does the latter and is validated in development (Left 4 Dead 2 with raw input on stayed still while the real cursor kept working), but it is not in any release, and anti-cheat titles cannot be tested until it is attestation-signed. See [Host Mode & Input Isolation](vision/HOST_MODE_ISOLATION.md) for the research and measurements.

| Game | Why |
|---|---|
| **Valorant** | Raw Input + Vanguard anti-cheat |
| **Counter-Strike 2** | Raw Input + VAC anti-cheat |
| **Fortnite** | Raw Input + EasyAntiCheat |
| **Apex Legends** | Raw Input + EasyAntiCheat |
| **Overwatch 2** | Raw Input exclusively |

## General Guidelines

### Best Game Genres for Adaptive Controllers
- **Turn-based games** (Civilization, Slay the Spire) — no time pressure
- **Sandbox/creative** (Minecraft, Stardew Valley, Terraria) — relaxed pace
- **Puzzle games** (Portal) — pausable, thoughtful gameplay
- **Platformers with controller support** (Celeste, Hollow Knight) — designed for gamepad
- **Racing** (Rocket League) — joystick maps naturally

### Tips
1. **Always set the game to windowed mode first** before applying borderless
2. **Use Game Focus Mode** (View → Game Focus Mode) alongside borderless for best results
3. **Lower the cursor release interval** (16–30ms) if the game keeps re-locking your cursor
4. **Use ViGEm Xbox profile** for games with native XInput support (most modern games)
5. **Start Nimbus before the game.** Some engines (Source, measured on Left 4 Dead 2) decide at launch whether an Xbox controller is present and never see one that appears later. If the stick does nothing, quit the game, make sure Nimbus is running, and launch again.
6. **Calibrate the anti-deadzone per game.** Edit Layout, double-click the aim stick, turn on "Drive the controller" on the test pad, and raise or lower Anti-DZ until the smallest nudge moves the camera. The default matches the Xbox constants most games use.

### Reporting New Games
If you test a game that works (or doesn't), please open an issue on our GitHub repository with:
- Game name and version
- Whether borderless mode worked
- Whether cursor release was needed
- Any notes about controller compatibility

---

*This list is maintained as part of the Project Nimbus source code in `src/borderless.py` and is displayed in-app via View → Borderless Gaming → Compatibility tab.*
