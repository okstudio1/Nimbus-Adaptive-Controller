# Nimbus Mouse Filter (Windows kernel driver)

A KMDF upper filter on the mouse device class. With no client attached it
passes every mouse packet through unchanged. While Nimbus holds the control
device open and turns isolation on, the driver withholds physical mouse packets
from `mouclass` (so the cursor, Raw Input, and every game stop seeing the mouse)
and delivers them to Nimbus instead, which drives its virtual stick and its own
on-screen cursor.

This is the Windows equivalent of the one-line `EVIOCGRAB` grab that
`src/mouse_isolation.py` uses on Linux. The reason it has to be a kernel driver,
and the measurements behind it, are in
[docs/vision/HOST_MODE_ISOLATION.md](../docs/vision/HOST_MODE_ISOLATION.md)
(sections 8 and 9). The design is in
[docs/vision/WINDOWS_MOUSE_FILTER_PLAN.md](../docs/vision/WINDOWS_MOUSE_FILTER_PLAN.md).

**Status:** builds and test-signs. Not yet loaded or validated on hardware, and
not yet attestation-signed. Do not ship it yet.

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
| `install-dev.ps1` / `uninstall-dev.ps1` | Register/unregister the class filter for development (elevated). |

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
driver\enable-testsigning.ps1   # once; then reboot
driver\install-dev.ps1          # copies the .sys, creates the service, adds the class UpperFilters, restarts the mice
```

`install-dev.ps1` inserts `nimbus_moufilter` **in front of** `mouclass` in the
mouse class `UpperFilters` list. Filters attach in list order, first listed
closest to the function driver, so this puts the filter between `mouhid` and
`mouclass`, which is where it has to be to receive `IOCTL_INTERNAL_MOUSE_CONNECT`.
On a stock machine the list reads `nimbus_moufilter, mouclass` afterwards.

Then, from the repo root:

```powershell
venv\Scripts\python -m src.mouse_isolation_win --status      # driver reachable? isolating?
venv\Scripts\python -m src.mouse_isolation_win --grab 5       # isolate for 5 s (the desktop cursor should freeze)
```

Remove it with `driver\uninstall-dev.ps1`.

**While test signing is on, anti-cheat games (EasyAntiCheat, BattlEye, Vanguard)
refuse to start.** Validate the filter against the fake-game probe and a
non-anti-cheat game under test signing; Elden Ring validation waits for an
attestation-signed build.

## Safety

- The control device is exclusive: one client at a time.
- Isolation is cleared when the client's handle closes (crash, kill, exit).
- A watchdog clears isolation if no read is pending for 2 s while isolating.
- The keyboard is never filtered, so `Ctrl+Alt+F12` (handled in
  `src/mouse_hider.py`) and `Ctrl+Alt+Del` always work, and every recovery
  below can be done from the keyboard.
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
| Cursor frozen while Nimbus is running | Isolation is on and the client is alive | Expected while Full Game Mode isolates. `Ctrl+Alt+F12`, or close Nimbus. The watchdog releases within 2 s if Nimbus stops reading; closing the handle releases immediately. |
| Cursor frozen and Nimbus is gone | Should not happen (handle cleanup clears isolation) | Replug the mouse (a fresh device instance), or reboot; the flag does not survive a driver reload. Then file the bug with the output of `--status`. |
| "Test Mode" watermark, anti-cheat games refuse to start | Test signing is on | `bcdedit /set testsigning off` from an elevated prompt, reboot. Do this before playing EAC/BattlEye/Vanguard titles; the unsigned dev driver cannot load without it. |
