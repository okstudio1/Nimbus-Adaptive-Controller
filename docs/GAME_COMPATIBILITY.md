# Game Compatibility — Borderless Gaming & Mouse Capture

Project Nimbus includes built-in borderless gaming support that lets you free your cursor from games that lock it, so you can interact with the Nimbus controller interface while gaming.

## How It Works

Games capture the mouse using one of three mechanisms:

| Mechanism | How Nimbus Handles It |
|---|---|
| **ClipCursor** — game confines cursor to its window rect | Nimbus continuously releases the clip via polling (`ClipCursor(NULL)`) |
| **Exclusive Fullscreen** — game takes over the display | Nimbus converts the window to borderless windowed mode |
| **Raw Input** — game reads mouse delta directly from HID | Use **Controller Mode** (see below) — requires Raw Input: OFF in game settings |

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

**On Linux** no driver is needed: the Xbox pad is a kernel `uinput` device, and the same Game Mode button runs controller-mode enforcement, an X11 Game Focus Mode equivalent, and **Mouse Isolation**, an exclusive `EVIOCGRAB` of the physical mouse. With the mouse isolated, the Raw Input mechanism behind the Incompatible tier below has nothing to read; anti-cheat support under Proton is the remaining per-game question. Borderless conversion and cursor release are Windows-only. See [Linux Setup](setup/LINUX.md).

### Emergency stop
Press **Ctrl+Alt+F12** at any time to instantly kill Controller Mode and restore normal mouse behavior. This hotkey works even if Nimbus doesn't have focus.

### Which games benefit from Controller Mode?
Any game that:
- Uses a standard Windows message loop for mouse input (most games)
- Has a "Raw Input" or "DirectInput" toggle in settings (switch it OFF)
- Supports XInput controllers natively (detects the virtual Xbox 360 pad)

**Games that will NOT work** even with Controller Mode:
- Games with kernel-level anti-cheat (Vanguard, EAC) — the hook gets blocked
- Games that use Raw Input exclusively with no toggle option

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

## Verified Games

These games have been tested and confirmed working with Project Nimbus.

| Game | Method | Notes |
|---|---|---|
| **Minecraft (Java Edition)** | Controller Mode | Set Raw Input: OFF in Options → Controls → Mouse Settings. Use Full Game Mode. |
| **Stardew Valley** | ClipCursor | XNA/MonoGame engine. Very accessible with controller support. |
| **Terraria** | ClipCursor | XNA/MonoGame engine. Great accessibility with controller support. |
| **The Elder Scrolls V: Skyrim** | ClipCursor | Set to windowed mode in launcher. Full controller support via ViGEm/vJoy. |

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
| **Elden Ring** | ClipCursor + Raw Input | Use controller mode (ViGEm) — mouse camera won't work. Set to windowed in settings. |
| **Dark Souls III** | ClipCursor + Raw Input | Similar to Elden Ring — use controller mode. Set to windowed in settings first. |

## Incompatible Games

These games use Raw Input exclusively. External cursor release has no effect. Use a second monitor, tablet input device, or game streaming instead.

> **Why this tier can't be fixed in user mode:** `WH_MOUSE_LL` is a Win32 message-level hook, but Raw Input is delivered from the HID stack and never passes through it. Closing this gap requires either isolating the game from the physical mouse or filtering the mouse below `win32k`. See [Host Mode & Input Isolation](vision/HOST_MODE_ISOLATION.md) for research on both.

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

### Reporting New Games
If you test a game that works (or doesn't), please open an issue on our GitHub repository with:
- Game name and version
- Whether borderless mode worked
- Whether cursor release was needed
- Any notes about controller compatibility

---

*This list is maintained as part of the Project Nimbus source code in `src/borderless.py` and is displayed in-app via View → Borderless Gaming → Compatibility tab.*
