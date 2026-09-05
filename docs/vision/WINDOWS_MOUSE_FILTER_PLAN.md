# Windows Mouse Filter Plan

**Status:** Prototype built and test-signed (section 6), not yet loaded on hardware. Matches `driver/README.md`.
**Parent doc:** [HOST_MODE_ISOLATION.md](HOST_MODE_ISOLATION.md) (Option F). Measurements that motivate this plan are recorded there under "Measured on Windows".
**Linux counterpart:** [LINUX_PROBE_PLAN.md](LINUX_PROBE_PLAN.md) and `src/mouse_isolation.py` on the `linux-uinput-support` branch, which this plan reuses.

## 1. Why a driver, in one paragraph

On Linux, Nimbus takes the physical mouse away from the game with one `EVIOCGRAB` ioctl and draws its own cursor. On Windows the same outcome needs a kernel-mode filter, because user mode has no equivalent call. That is not a guess any more: `tests/probe_rawinput_windows.py` shows that a `WH_MOUSE_LL` hook that drops 100% of mouse events leaves `WM_INPUT` untouched, that the only user-mode action that stops `WM_INPUT` is taking the foreground away from the game, and `tests/probe_game_mouselook_windows.py` shows that Elden Ring stops reading the gamepad the moment it loses the foreground. Microsoft's HID architecture page states the reason: mouse and keyboard top-level collections are opened exclusively by the Raw Input Manager, so no process can read or block them from user mode.

## 2. What to build

A small KMDF upper filter on the mouse class (class GUID `{4D36E96F-E325-11CE-BFC1-08002BE10318}`), started from Microsoft's `moufiltr` sample, that does exactly two things:

1. **Pass-through by default.** With no client attached, every `MOUSE_INPUT_DATA` packet goes to `mouclass` unchanged. Installing the driver changes nothing for anyone.
2. **Isolation on request.** While a user-mode client holds the control device open and has switched isolation on, packets from the physical mice are **not** forwarded to `mouclass` (so neither the cursor nor Raw Input nor any game sees them) and are instead handed to the client.

Everything else Nimbus needs (software cursor, synthetic Qt events, deadzones, curves, the virtual pad) already exists in user mode.

### 2.1 Kernel side

| Item | Decision |
|---|---|
| Base | WDK `moufiltr` sample (KMDF). `MouFilter_ServiceCallback` is the one function that matters: it is where packets are dropped or forwarded. |
| Control device | `\\.\NimbusMouseFilter`, one client at a time. |
| Packet delivery | Inverted call: the client keeps a `ReadFile` pending on the control device; the driver completes it with an array of `MOUSE_INPUT_DATA` (verbatim: `UnitId`, `Flags`, `ButtonFlags`, `ButtonData`, `RawButtons`, `LastX`, `LastY`, `ExtraInformation`). If no read is pending, packets go to a bounded ring (drop oldest) so a slow client never stalls the input stack. A read issued or pending while isolation is off fails with `STATUS_DEVICE_NOT_READY`, which is how the client learns about a watchdog release (interface v2). |
| Control | `IOCTL_NIMBUS_SET_ISOLATION {enabled: ULONG}` and `IOCTL_NIMBUS_GET_STATUS` (version and counters). |
| Device selection | First version: all mice. Second version: per-`UnitId` mask, since combo devices and multi-node mice exist on Windows too. |
| Release guarantees | Isolation is tied to the client handle: `IRP_MJ_CLEANUP` (crash, kill, exit) restores pass-through. Plus a watchdog: if no read is pending for 2 s while isolated, restore pass-through. Keyboard is never touched, so `Ctrl+Alt+Del` always works and `Ctrl+Alt+F12` can be wired as the release hotkey (section 2.2). |
| Injection | None. The driver never inserts packets. When isolation is off, the mouse simply flows again. |
| Size | Roughly 500 to 700 lines of C on top of the sample. |

### 2.2 User side

`src/mouse_isolation_win.py` exposing the **same** class as the Linux module:

```python
MouseIsolation(on_motion, on_button, on_wheel=None, on_stopped=None, hotkey=True)
    .start(nodes=None) -> list[dict]
    .stop(reason="requested")
    .active, .grabbed_devices, .stop_reason
```

Internally it opens the control device, pends reads on a thread, converts `MOUSE_INPUT_DATA` to `(dx, dy)`, buttons, and wheel notches, and fires the same callbacks. Absolute-position packets (`MOUSE_MOVE_ABSOLUTE`: RDP, VM pointers, tablets in mouse mode) are turned into pixel deltas against the previous position. `src/bridge.py` then needs only the platform switch the Linux branch already has (`MOUSE_ISOLATION_AVAILABLE`), and the software cursor, `_IsolationRelay`, `_iso_send_mouse`, and the `Isolate Mouse` menu item carry over unchanged. `Ctrl+Alt+F12` uses the existing hotkey thread in `mouse_hider.py`; it must call `stop()` on the isolation object as well as the pulse, and until that wiring lands the module installs no hotkey (release is `stop()`, closing Nimbus, or the watchdog).

Two things to get right in that merge:

- `MOUSE_ISOLATION_AVAILABLE` in the Windows module is True only when the driver's control device opened at import time, so a Windows machine without the driver keeps today's `mouse_hider` Game Mode. The Linux flag is platform-only; do not "simplify" the Windows one back to that, or Game Mode would skip `mouse_hider` on every Windows machine.
- In the Linux branch's `startFullGameMode`, the `mouse_hider` step is an `elif` on the isolation step. On Windows both should run: isolation takes the packets away, and `mouse_hider` still owns the `ClipCursor` release and the `Ctrl+Alt+F12` hotkey. Make them independent `if`s.

`window_utils.py` (WS_EX_NOACTIVATE) stays exactly as it is. The game keeps the foreground, which is the whole point: the pad keeps working and the mouse is gone.

### 2.3 What changes for the user

- One extra driver in the installer, alongside ViGEmBus. Class filters need the mouse devices restarted once (the installer can do that with `pnputil /restart-device`, no reboot).
- Registration detail that is easy to get wrong: on HID-mouse machines the mouse class `UpperFilters` value already contains `mouclass` (mouhid is the function driver; mouclass rides above it as a class filter). Filters attach in list order, first listed closest to the function driver, so `nimbus_moufilter` must be inserted **before** `mouclass` to sit between mouhid and mouclass and receive `IOCTL_INTERNAL_MOUSE_CONNECT`. And `mouclass` must never be removed from that list; the dev scripts refuse to write a list without it.
- "Isolate Mouse" becomes available in the View menu and is on by default in Full Game Mode, as on Linux.
- Failure modes to design for: if Nimbus dies while isolated, the mouse comes back within a frame (handle cleanup). If the driver itself fails to load, the mouse devices do **not** start, because a class upper filter is mandatory once it is listed in `UpperFilters` (Device Manager Code 39/19; this is the "keyboard and mouse unusable after restart" failure other filter projects warn about). The installer must therefore register the filter, restart the mice, verify the driver is running and every mouse is healthy, and roll the registry entry back automatically if not, with a restore point taken first. The keyboard is never filtered, so manual recovery is always possible from the keyboard; `driver/README.md` lists the recovery steps.

## 3. Signing and the 2026 policy change

- Attestation signing through Partner Center needs the EV certificate that is already held. Attestation-signed drivers load on retail Windows 10 and 11 with Secure Boot on. That is how ViGEmBus ships.
- The April 2026 "Windows Driver Policy" removes trust for cross-signed drivers. It does not affect attestation-signed drivers today; Microsoft has said only that it is "looking into" requiring HLK for everything. Track the OSR thread linked from the parent doc.
- Ship detection of Code Integrity events 3076 (audited) and 3077 (blocked) in the diagnostics dialog, so a user whose driver stopped loading gets a real explanation. The dev machine used for the measurements is itself still in the policy's evaluation mode because another cross-signed driver (`loopbe1.sys`) is audited at every boot; test on a clean Windows 11 VM too.
- Development can use test signing (`bcdedit /set testsigning on`) on the dev machine, where Secure Boot is already off. Preproduction signing from Partner Center is the option when Secure Boot must stay on.

## 4. Prior art to borrow from

| Project | Use it for |
|---|---|
| WDK `moufiltr` | The starting point. Official, KMDF, tiny. |
| [RawAccel](https://github.com/RawAccelOfficial/rawaccel) (MIT) | A shipping, signed mouclass upper filter with an installer and a user-mode settings IOCTL. It rewrites `LastX`/`LastY` at exactly the layer this plan drops them, and it works in Raw Input games, which is independent confirmation of section 7.1 in the parent doc. Copy the installer and packaging approach. |
| [OpenInputBridge](https://github.com/Applet-LLC/OpenInputBridge) (MIT) | Clean-room, Interception-compatible mouse and keyboard filters for Windows 11 with an inverted-call design. No signed release yet (WHQL build "coming", paid), a 20-device limit, and an explicit warning that Windows 10 installs leave the machine without keyboard and mouse. Worth reading; not yet worth depending on. |
| [Interception](https://github.com/oblitum/Interception) | Do not build on it: last release 2017, licensor unreachable since 2026, signing status unknown, Windows 11 installer reports open. |
| HidHide / ViGEmBus | Not a mouse solution (nefarius: "impossible ... by design"), but the reference for how a solo maintainer ships attestation-signed input drivers. Still worth the email suggested in the parent doc. |

## 5. Test plan

1. `tests/probe_mouse_filter_windows.py` (unattended): status readable, start/stop lifecycle, handle-drop release, watchdog release, exclusive open. Safe over a remote session; the mouse is isolated for a few seconds at a time with nobody moving it.
2. `tests/probe_mouse_filter_windows.py --attended`: three timed phases with a hand on the physical mouse. Pass-through: the fake game receives `WM_INPUT`. Isolated: the game receives nothing and the driver captures the motion. Released: the game receives it again. **This needs a physical mouse or a HID mouse emulator.** `SendInput` enters above the filter and will still reach the game, which is correct behaviour but makes injected motion useless here.
3. `tests/probe_game_mouselook_windows.py` against a Raw Input game with the filter on, game foreground, physical sweep: `STILL`; pad: `MOVED`; after release: `MOVED`. Same pass rule as the Linux probe (`hi = max(3*noise, 150)`, `lo = max(2*noise, 60)`).
4. Kill Nimbus while isolated: mouse returns within one second. Pull the mouse's USB cable while isolated and plug it back: no BSOD, mouse works.
5. Reboot with the driver installed and Nimbus not running: nothing observable.

**Test signing blocks anti-cheat.** EasyAntiCheat, BattlEye and Vanguard refuse to start while `testsigning` is on, so item 3 cannot use Elden Ring on the dev machine. Run it against a non-anti-cheat Raw Input game under test signing, and repeat it with Elden Ring only once the driver is attestation-signed (or on a second machine that loads the signed build).

## 6. Order of work

1. **Done 2026-09-05 (build half).** The dev machine now has WDK 10.0.26100.6584, the Visual Studio 2022 "Windows Driver Kit" component (10.0.26100.16), the Windows 11 SDK 26100, and the Spectre-mitigated libraries for MSVC 14.44 and 14.38. The unmodified `moufiltr` sample builds, test-signs, and produces a catalog. Two quirks to know before building:
   - The kernel-mode driver platform pins MSVC **14.38.33130**, not the newest 14.44, so the Spectre component that matters is `Microsoft.VisualStudio.Component.VC.14.38.17.8.x86.x64.Spectre` (the "Latest" one alone gives `MSB8040`).
   - `WDKContentRoot` is unset in the registry and the 64-bit view of `KitsRoot10` points at `C:\Program Files\Windows Kits\10\`, which is not where the kit is. Build with the 64-bit MSBuild and pass the kit root explicitly:

     ```powershell
     & "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\amd64\MSBuild.exe" driver.vcxproj /p:Configuration=Release /p:Platform=x64 "/p:WDKContentRoot=C:\Program Files (x86)\Windows Kits\10\\"
     ```

     The 32-bit `MSBuild.exe` resolves the kit on its own but then fails to load `InfVerif.dll`, so INF verification silently does not run there.

   Still to do for this step: enable test signing (`bcdedit /set testsigning on`, needs the Admin account and a reboot) and load the sample once to prove the install path.
2. **Written 2026-09-05, not yet loaded.** `driver/nimbus_moufilter/` holds the filter (control device, `IOCTL_NIMBUS_SET_ISOLATION`, `IOCTL_NIMBUS_GET_STATUS`, inverted-call reads through `ReadFile`, a 1024-packet ring, handle-cleanup and watchdog release), `src/mouse_isolation_win.py` is the client with the Linux class API, and `driver/build.ps1`, `enable-testsigning.ps1`, `install-dev.ps1`, `uninstall-dev.ps1` cover the dev loop. It builds, test-signs and passes InfVerif. The INF installs only the service; the class `UpperFilters` entry is added by `install-dev.ps1` because a primitive INF may not write outside `HKR` (InfVerif 1321). Review fixes the same day (interface v2): reads fail with `STATUS_DEVICE_NOT_READY` while isolation is off so the client sees a watchdog release; the client reads status through its own handle (the device is exclusive), maps `ERROR_ACCESS_DENIED` on a second open, handles absolute-motion packets, and reports the driver as available only when the device opens; `install-dev.ps1` detaches a loaded build before replacing the file. Still to do: enable test signing, reboot, install, and pass test-plan items 1, 2 and 4 with the real mouse.
3. Merge or rebase onto `linux-uinput-support` so the bridge's isolation plumbing is shared, then wire the Windows module in and pass item 2 against Elden Ring.
4. Partner Center registration, attestation signing, installer changes, item 4.
5. Disclosure: publish the driver's name and purpose, and open the anti-cheat conversation described in the parent doc before the first release that ships it.

## 7. Explicitly out of scope

- Keyboard filtering of any kind.
- Injecting mouse motion (the desktop cursor is driven by the real mouse whenever isolation is off).
- Per-application filtering. It is not possible at this layer; the parent doc explains why.
- Anything that hides the driver from anti-cheat. Nimbus must be openly identifiable.
