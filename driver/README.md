# Nimbus Mouse Filter (Windows kernel driver)

A KMDF upper filter on the mouse device class. With no client attached it
passes every mouse packet through unchanged. While Nimbus holds the control
device open and turns isolation on, the driver withholds physical mouse packets
from `mouclass` (so the cursor, Raw Input, and every game stop seeing the mouse)
and delivers them to Nimbus instead. Nimbus drives its virtual stick from them
and, on Windows, moves the real cursor itself with `SetCursorPos` (the cursor
relay in `src/mouse_isolation_win.py`), which moves the cursor without creating
an input event: the mouse keeps working everywhere, the game keeps the
foreground, and the game's Raw Input sees nothing.

This is the Windows equivalent of the one-line `EVIOCGRAB` grab that
`src/mouse_isolation.py` uses on Linux (on the `linux-uinput-support` branch,
not yet merged). The reason it has to be a kernel driver,
and the measurements behind it, are in
[docs/vision/HOST_MODE_ISOLATION.md](../docs/vision/HOST_MODE_ISOLATION.md)
(sections 8 and 9). The design is in
[docs/vision/WINDOWS_MOUSE_FILTER_PLAN.md](../docs/vision/WINDOWS_MOUSE_FILTER_PLAN.md).

**Status:** dev build loaded and validated on hardware on 2026-09-05 (Windows 11
25H2, Logitech USB mouse): the attended probe passed 9/9, with the fake Raw Input
game receiving zero `WM_INPUT` while the driver captured 1,017 packets and none
were dropped, and the unattended robustness set (client kill and suspension,
open races, parked-read draining, malformed requests, a 30 s idle soak) passed
16/16, then 16/16 again under Driver Verifier (standard checks plus WDF
verification) after a reboot that loaded the filter cleanly, and the filter
survived mouse restarts both with a client isolating and with none. Interface
v3 (heartbeat ticks, so a frozen Nimbus loses the mouse within 2 s) and the
cursor relay in the client were written the same evening; v3 is installed and
passed the probe 17/17 under Driver Verifier, including the frozen-client
release. The relay is wired into the bridge's Full Game Mode, and the real
Nimbus app passed the relay harness 8/8 against the fake Raw Input game
(`tests/probe_nimbus_relay_windows.py`), and a first hands-on run of Game
Mode through TeamViewer worked once two usability gaps were closed (the game
comes to the foreground by itself; a cursor found over the game is parked
onto Nimbus). Not attestation-signed, not
validated against an anti-cheat game, not in any release. Do not ship it yet.

## Layout

| File | What it is |
|---|---|
| `nimbus_moufilter/nimbus_moufilter.c` | The driver. `NimbusFilter_ServiceCallback` is where packets are dropped or passed. |
| `nimbus_moufilter/nimbus_moufilter.h` | Private declarations and driver-wide state. |
| `nimbus_moufilter/nimbus_moufilter_ioctl.h` | The user/kernel contract. `src/mouse_isolation_win.py` mirrors these values. |
| `nimbus_moufilter/nimbus_moufilter.inx` | INF template (service + file only; the class filter entry is added by `install-dev.ps1`). |
| `nimbus_moufilter/nimbus_moufilter.vcxproj` | KMDF driver project, `WindowsKernelModeDriver10.0` toolset. |
| `build.ps1` | Build and collect outputs into `out/`. |
| `enable-testsigning.ps1` | Install the test cert and turn on test signing (elevated, one reboot). |
| `install-dev.ps1` / `uninstall-dev.ps1` | Register/unregister the class filter for development (elevated). `install-dev.ps1` also updates a loaded build: it detaches the filter, replaces the file, and re-attaches. |
| `pnp-common.ps1` | Shared by the two scripts above: `Restart-Mice`, which restarts every mouse with `pnputil /restart-device` so the filter attaches or detaches without a reboot. |

## Build

Needs Visual Studio 2022 with the "Windows Driver Kit" component, the
Spectre-mitigated libraries for the pinned MSVC toolset (14.38 as of WDK
10.0.26100.6584, **not** the "Latest" one), and WDK 10.0.26100.

```powershell
driver\build.ps1              # Release x64 -> driver\out\
```

`build.ps1` passes the kit root explicitly and uses the 64-bit MSBuild, because
the 64-bit `KitsRoot10` registry value can point at the wrong folder and the
32-bit MSBuild cannot load `InfVerif`. See the plan doc, section 6.

## Install for development (elevated, at the machine)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& "C:\path\to\Nimbus-Adaptive-Controller\driver\enable-testsigning.ps1"   # once; then reboot
& "C:\path\to\Nimbus-Adaptive-Controller\driver\install-dev.ps1"          # copies the .sys, creates the service, adds the class UpperFilters, restarts the mice
```

Use the full path: an elevated PowerShell starts in `C:\WINDOWS\system32`, where
`driver\install-dev.ps1` is taken for a module name and fails with "The module
'driver' could not be loaded".

The reboot is not optional: `bcdedit` stores the setting for the *next* boot,
and a self-signed driver attached before that reboot fails to load with
`0xC0000428` (Code Integrity event 3004, "invalid root certificate"), which the
script's rollback then undoes. After the reboot the desktop shows a "Test Mode"
watermark, and `install-dev.ps1` prints the running boot's Code Integrity
options (bit `0x2` is test signing) and refuses to attach the filter unless it
is set or the driver is Microsoft-signed.

Run `install-dev.ps1` again after every rebuild. If the previous build is
loaded it detaches it first (the mice restart twice), because a loaded driver
holds its `.sys` open and the copy would otherwise fail.

`install-dev.ps1` inserts `nimbus_moufilter` **in front of** `mouclass` in the
mouse class `UpperFilters` list. Filters attach in list order, first listed
closest to the function driver, so this puts the filter between `mouhid` and
`mouclass`, which is where it has to be to receive `IOCTL_INTERNAL_MOUSE_CONNECT`.
On a stock machine the list reads `nimbus_moufilter, mouclass` afterwards.

Then, from the repo root:

```powershell
venv\Scripts\python -m src.mouse_isolation_win --status      # driver reachable? isolating?
venv\Scripts\python -m src.mouse_isolation_win --grab 5       # isolate for 5 s (the desktop cursor should freeze)
venv\Scripts\python -m src.mouse_isolation_win --grab 5 --relay   # same, but the cursor keeps working (cursor relay)
```

Remove it with `driver\uninstall-dev.ps1`. Do not also install the INF with
`pnputil /add-driver`: that points the service at a Driver Store copy that
`install-dev.ps1` does not update, so rebuilds would keep loading the old
driver. `install-dev.ps1` refuses to continue if it finds such a service, and
`uninstall-dev.ps1` removes the Driver Store package.

**While test signing is on, anti-cheat games (EasyAntiCheat, BattlEye, Vanguard)
refuse to start.** Validate the filter against the fake-game probe and a
non-anti-cheat game under test signing; Elden Ring validation waits for an
attestation-signed build.

## Safety

- The control device is exclusive: one client at a time. A second open fails
  with `ERROR_ACCESS_DENIED`.
- Isolation is cleared when the client's handle closes (crash, kill, exit).
- A watchdog clears isolation if no read has arrived for 2 s while isolating.
  It runs only while isolating. Since interface v3 a read that has been
  parked for 1 s with nothing to deliver is completed empty (a heartbeat
  tick) and the client issues the next one; a client that is frozen or
  suspended cannot, so it loses the mouse within 2 s of its last read (on v2
  a parked read kept isolation alive by design; on v3 a client frozen with
  `NtSuspendProcess` was released 2 s after its last read, probe check U11).
  Once isolation is off, every read fails with `ERROR_NOT_READY`, so
  the client notices a watchdog release at its next read and reports the
  stop. Every release path (IOCTL, handle cleanup, watchdog) drains reads
  that were already parked, so a read cannot outlive a release. Measured on
  v2: release 2.1 s after the last read activity; a killed client frees the
  device in about 30 ms.
- Coverage: the filter sees every pointer that reports through `mouclass`
  (USB, Bluetooth and PS/2 mice, touchpads in legacy mouse mode). Precision
  Touchpads report through the HID digitizer path straight to `win32k` and
  are expected to bypass it (not yet measured on hardware). `--status` shows
  `connected_mice`, and `MouseIsolation.start()` refuses to report success
  when it is 0, since the real cursor would keep moving with nothing captured.
- The keyboard is never filtered, so `Ctrl+Alt+Del` always works and every
  recovery below can be done from the keyboard. `Ctrl+Alt+F12` releases
  isolation: the client polls it with `GetAsyncKeyState` on its reader thread
  every 100 ms while a read is parked, so it needs no focus and works when
  Nimbus's UI thread is stuck. The `Ctrl+Alt+F12` listener in
  `src/mouse_hider.py` still stops the Controller Mode pulse.
- A class upper filter is **mandatory once listed**: if the driver fails to
  load, Windows does not start the mouse devices (Device Manager Code 39 or
  Code 19) until the `UpperFilters` entry is removed. `install-dev.ps1`
  therefore creates a restore point first, verifies after attaching, and
  rolls the registry entry back automatically if the driver is not running
  or any mouse reports a problem.

## If it gets stuck

| Symptom | What happened | Recovery |
|---|---|---|
| Mouse dead right after `install-dev.ps1`, script reported a rollback | Driver did not load (signature, test signing, or a load-time bug) | Nothing to do; the rollback already removed the entry. Replug the mouse if it has not come back. Read the reason the script printed. |
| Mouse dead, no rollback (script interrupted, or `-NoRollback`) | `UpperFilters` still names a driver that will not start | Keyboard: `Win+X`, `A` for an elevated PowerShell, run `driver\uninstall-dev.ps1`, replug the mouse. Or in `regedit`, under `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}`, edit `UpperFilters` so it reads only `mouclass` (the list normally holds `nimbus_moufilter` above `mouclass`; **`mouclass` must stay**, it is the mouse class driver itself). |
| Blue screen when a mouse starts (possibly at every boot) | A bug in the filter | Windows opens the recovery environment after two failed boots (or hold Shift while clicking Restart). Troubleshoot, Advanced options, System Restore, pick the "Before Nimbus Mouse Filter dev install" point. Alternative from the recovery Command Prompt: `reg load HKLM\sys C:\Windows\System32\config\SYSTEM`, then `reg add "HKLM\sys\ControlSet001\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}" /v UpperFilters /t REG_MULTI_SZ /d mouclass /f`, then `reg unload HKLM\sys`. Never delete the value outright: `mouclass` has to remain in it. |
| Cursor frozen while Nimbus is running | Isolation is on without the cursor relay, or the relay's reader thread is stuck | With the relay the cursor should keep moving; frozen means the reader is not running. `Ctrl+Alt+F12` releases (polled by the client). Or close Nimbus (Alt+F4 or Task Manager from the keyboard); closing the handle releases immediately. The watchdog releases within 2 s if Nimbus stops issuing reads, including when it is frozen (interface v3). |
| Cursor frozen and Nimbus is gone | Should not happen (handle cleanup clears isolation) | Replug the mouse (a fresh device instance), or reboot; the flag does not survive a driver reload. Then file the bug with the output of `--status`. |
| "Test Mode" watermark, anti-cheat games refuse to start | Test signing is on | `bcdedit /set testsigning off` from an elevated prompt, reboot. Do this before playing EAC/BattlEye/Vanguard titles; the unsigned dev driver cannot load without it. |
