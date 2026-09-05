# Host Mode: Running Games in an Isolated Environment

**Status:** Research, plus measurements on Windows (section 8, 2026-09-05), a survey of the driver landscape (section 9), and a prototype Windows filter driver in `driver/` that builds but is not yet validated on hardware. Nothing here is shipped.
**Question:** Could Nimbus run games inside a VM / sandbox ("host mode") the way the Steam Deck appears to, and what hardware would that need?

---

## 1. The problem this would actually solve

Nimbus needs two things to be true at the same time:

- **(A)** Nimbus receives the physical mouse (it drives the on-screen virtual stick).
- **(B)** The game does *not* receive the physical mouse.

On Windows, (B) is only partly solvable, and `docs/GAME_COMPATIBILITY.md` already documents where it breaks:

| Game capture mechanism | Nimbus counter-measure | Works? |
|---|---|---|
| `ClipCursor` confinement | Poll `ClipCursor(NULL)` | Yes |
| Exclusive fullscreen | Convert to borderless | Yes |
| `WM_MOUSEMOVE` | `WH_MOUSE_LL` suppression hook | Yes |
| **Raw Input (`WM_INPUT`)** | **None exists** | **No** |

`WH_MOUSE_LL` is a Win32 message-level hook. Raw Input is delivered from the HID stack and never passes through it, so there is no user-mode way to stop a raw-input game from seeing the physical mouse. That single gap is why Valorant, CS2, Fortnite, Apex, and Overwatch 2 sit in the "Incompatible" table, and why Elden Ring and Dark Souls III are only partial (the camera drifts as the user moves the mouse over the Nimbus UI).

The compatibility doc's own advice already terminates at *"use a second monitor, tablet input device, or game streaming instead."* So the instinct here is aimed at the right target: **isolation is the only complete fix for the raw-input tier.**

## 2. What the Steam Deck actually does (it is not a VM)

Worth correcting the premise, because the real mechanism is more useful than the imagined one. Three separate pieces, none of them virtualization:

1. **gamescope**, a nested "micro-compositor." The game runs in its own single-window XWayland session inside the normal desktop. It cannot interfere with the host desktop and the desktop cannot interfere with it, which is exactly why it fixes mouse-focus issues. Near-zero overhead; it is a compositor, not a hypervisor.
2. **Steam Input**, which creates a virtual gamepad via `uinput` and takes an exclusive `evdev` grab on the physical controller, so the game only ever sees the virtual device.
3. **Proton**, a Wine translation layer. Also not a VM.

The Deck's design leans on two things Linux gives away for free: kernel-supported input redirection (`evdev` grab plus `uinput`) and cheap compositor nesting. **Windows provides neither.** That asymmetry is the entire reason Nimbus has to fight with hooks and polling, and it's the real finding here.

(Minor caveat even on Linux: an `evdev` grab silences the physical device but can't make it disappear, so some games still see a phantom controller.)

## 3. The cheap Windows shortcut does not exist

The obvious candidate is **HidHide**, a kernel filter driver by the ViGEmBus author that acts as a per-application "device firewall," hiding a physical HID device from everything except an allowlist. It is the standard companion to ViGEm in the DS4Windows/reWASD world, and conceptually it is precisely the Windows equivalent of an `evdev` grab.

It does not help here. Per its own FAQ, mouse and keyboard input travels a different route through Windows (`mouclass`/`kbdclass`), and HidHide's blocking mechanism cannot interfere with those the way it does with joysticks and gamepads. It hides gamepads, not mice.

So there is no off-the-shelf software fix. The remaining options are isolation, or writing a driver.

## 4. Options, ranked by feasibility

### Option A: Two physical machines (most likely to actually work)

Game PC runs the game on bare metal. Nimbus PC runs Nimbus plus Moonlight. Nimbus creates its ViGEm pad locally, Moonlight forwards it as a standard gamepad, Sunshine on the game PC recreates it via ViGEm, and the game sees an ordinary Xbox 360 pad.

- The physical mouse lives on the Nimbus PC and **cannot** reach the game. The raw-input problem is solved by physics, with no drivers and no VM detection.
- **Anti-cheat: passes.** Bare metal, no hypervisor flags. This is the only option in this document where Valorant, Fortnite, Apex, and Elden Ring genuinely work.
- One important detail in Nimbus's favor: Sunshine's *mouse* injection uses `SendInput`, which goes through the Win32 cursor pipeline and is documented to break relative-mouse raw-input games (the FiveM/GTA V snap-to-corner bug; Parsec avoids it with a kernel-level virtual USB driver). Nimbus doesn't rely on that path. It outputs a **gamepad**, and gamepad forwarding goes through ViGEm at kernel level, which is the path that works correctly.
- **Hardware:** a second Windows PC able to run the game, plus wired Ethernet. Wi-Fi will work but adds jitter. Used mini-PC or an old gaming desktop, roughly $400+, or $0 if one is already sitting in a closet.
- **Latency:** roughly 10 to 25 ms added on a wired LAN at 1080p60. Very likely fine for the adaptive-input audience; noticeable in competitive FPS.

### Option B: Cloud gaming as the host (cheapest to test)

GeForce NOW or Xbox Cloud Gaming. The game runs in a datacenter, so hardware cost is zero.

- Nimbus creates a ViGEm pad; the GFN client sees a standard XInput device and forwards it upstream.
- **Open question:** the GFN client captures the mouse when focused, and it does use raw input for precision. Nimbus's existing `WS_EX_NOACTIVATE` focus mode plus `WH_MOUSE_LL` suppression over the GFN window *may* be sufficient, because the client is an ordinary Win32 app rather than a hardened game engine. This is unverified and is the one thing worth actually testing.
- **Cost:** roughly $10 to $20 per month, no hardware. Testable in an evening.

### Option C: Hyper-V GPU-PV (single machine, Windows host)

GPU paravirtualization shares one physical GPU with a VM. Officially GPU-P is Windows Server 2025 only and Microsoft states DDA and GPU-P are unsupported on client Windows, but community tooling (Easy-GPU-PV, Hyper-V GPU Paravirtualization Manager) makes it work on Windows 10/11 by copying host driver files into the guest. It's the same underlying tech as WSL2 and Windows Sandbox GPU sharing.

- **Advantage over full passthrough:** the host stays Windows, so Nimbus runs unchanged on the host with no port.
- **Hardware:** Windows 11 **Pro** or Enterprise (Hyper-V is not in Home), a GPU supporting GPU-PV, 32 GB RAM comfortable / 16 GB tight. No second GPU needed.
- **Performance:** roughly 87% of bare metal in one Cyberpunk 2077 comparison, versus 97% for full DDA passthrough.
- **Display:** the VM has no monitor, so you need Parsec, Sunshine, or RDP to view it in a host window. Easy-GPU-PV automates the Parsec side.
- **Blocker: anti-cheat.** EAC, BattlEye, and Vanguard all refuse to run in VMs. The games that motivate this whole exercise are exactly the ones that will not launch. Plus a second Windows license.

### Option D: Full VM with GPU passthrough (DDA / VFIO)

The "true" host mode. Guest sees only synthetic HID devices; the physical mouse never enters it.

- DDA is Windows Server only, so a Windows host is effectively out. The real-world version is a **Linux host** running KVM/QEMU with VFIO, plus Looking Glass to render the guest framebuffer into a host window over shared memory at sub-millisecond latency.
- That means **porting Nimbus to Linux**. It's a Windows app built on `ctypes`/`user32`, ViGEm, and vJoy. Substantial work.
- **Hardware:** VT-x/VT-d or AMD-V/AMD-Vi, IOMMU-capable motherboard with clean IOMMU groups, **two GPUs** (or iGPU for host plus dGPU for guest), 32 GB RAM, second Windows license.
- **Same anti-cheat blocker as Option C.**

### Option E: A gamescope equivalent on Windows

Does not exist. DWM is not nestable. Windows Sandbox is a throwaway container with weak game performance and is still VM-flagged; client Windows SKUs don't permit concurrent multi-session, and RDP sessions have poor GPU access. Not viable.

### Option F: Write a mouse class filter driver (the correct fix, highest effort)

What HidHide can't do for mice, a custom `moufiltr`-style upper filter on `mouclass` could: drop physical mouse input before it reaches anything except Nimbus. This is the genuine Windows equivalent of an `evdev` grab, and it keeps everything on one machine with zero added latency.

The mechanism is confirmed sound and the signing path is open, but the blocker turns out not to be either of those. **See [section 7](#7-option-f-in-depth) for the full analysis.**

## 5. The Linux host question

A Linux host is worth separating out, because it is interesting for a reason that has nothing to do with virtualization. **Linux doesn't isolate the game from the mouse. It lets Nimbus take the mouse away from everything else.** That is the same outcome by a much shorter route, and it means the VM was never the point.

### Two distinct Linux architectures

**L1: Linux native plus Proton (no VM at all).** The game runs under Proton on the same machine. Nimbus takes an exclusive `evdev` grab on the physical mouse, emits a virtual gamepad through `uinput`, and optionally wraps the game in gamescope. This is literally the Steam Deck architecture from section 2, running on ordinary hardware.

**L2: Linux host plus Windows VM (VFIO + Looking Glass).** Option D from the previous section, for games Proton cannot run.

L1 dominates, and L2 is close to pointless on inspection: the games that would force you into a Windows VM are overwhelmingly the kernel-anti-cheat titles, and those are exactly the ones that refuse to launch inside a VM. The intersection of "needs a Windows VM" and "tolerates a Windows VM" is nearly empty. If Linux is on the table, the native path is the one that matters.

### What Linux gives Nimbus for free

| Windows mechanism in Nimbus today | Linux equivalent |
|---|---|
| `WH_MOUSE_LL` suppression hook (fails on Raw Input) | `EVIOCGRAB` exclusive grab via python-evdev `InputDevice.grab()`. Only one process may hold it, and nothing else receives events until released. |
| `borderless.py`: ClipCursor polling, borderless conversion | Delete. gamescope and Wayland handle confinement and fullscreen. |
| `window_utils.py`: `WS_EX_NOACTIVATE` focus juggling | Largely delete. Wayland clients cannot steal focus the way Win32 windows can. |
| `mouse_hider.py` | Delete. |
| `vjoy_interface.py` (pyvjoy, requires vJoy driver) | `uinput` virtual device. Kernel module already present on every distro. |
| `vigem_interface.py` (vgamepad, requires ViGEmBus) | **vgamepad already ships a Linux backend** built on evdev/uinput, same Python API. |
| **Option F: signed kernel filter driver, EV cert, bricking risk** | **`InputDevice.grab()`. One line.** |

That last row is the finding. The single highest-effort item on the Windows roadmap, a kernel-mode mouse class filter driver with a $300 to $600/yr code-signing certificate, is a standard library call on Linux.

The port is also smaller than it looks. PySide6, QML, `bridge.py`, `config.py`, the profile system, and telemetry are all cross-platform and move unchanged. That is the bulk of the codebase. Three Windows-specific modules get **deleted** rather than ported, because the problems they exist to solve do not occur.

### Real friction, honestly

1. **Wayland's isolation cuts both ways.** The same strict client separation that kills the input leak also blocks an always-on-top overlay from positioning itself. `Qt.WindowStaysOnTopHint` does not work on KWin Wayland, and `move()`/`setGeometry()` are not honored. You need `layer-shell-qt` or KWindowSystem, and behavior varies by compositor (GNOME/Mutter notably does not implement layer-shell). For an app whose entire form factor is a floating panel beside a game, this is the main porting tax and it is not trivial.
2. **vgamepad's Linux backend is marked experimental.** Buttons and axes work with the same API; rumble and LEDs are Windows-only and not yet ported.
3. **Anti-cheat is opt-in, not solved.** EAC has supported Proton since 2021, but each developer must enable it. Enabled: **Elden Ring**, Halo MCC, Dead by Daylight, THE FINALS, Squad. Declined: Fortnite, Apex Legends. Vanguard (Valorant, League) is a hard no on Linux. So the competitive-shooter tier stays closed, just for a commercial reason rather than a technical one.
4. **Market fit.** Windows is where the users are, and "accessibility user running Arch" is a small intersection. This is the genuine strategic cost, and it is larger than any of the technical ones.

### Hardware required

**None.** This is the part worth underlining. The native Linux path needs no second PC, no second GPU, no IOMMU support, no 32 GB of RAM, and no second Windows license. Any machine that already runs the game will do.

- AMD GPU is the smoothest path (RADV/Mesa is in-tree, no proprietary driver).
- Intel Arc is fine.
- NVIDIA is workable in 2026 but has historically been the rough road on Wayland.

Compare that to the Windows VM path in Option C or D: two GPUs, IOMMU-clean motherboard, 32 GB, and another Windows license, to reach a worse outcome.

### The distribution angle

If Nimbus runs on Linux, it runs on a Steam Deck, and on Bazzite or ChimeraOS handhelds. For an accessibility-first project, "a preconfigured console you plug into your TV, with the adaptive control surface built in" is a stronger product story than anything achievable on Windows, and it inherits Steam Input on top of Nimbus's own layer.

## 6. Assessment

**Is it feasible?** Yes, but not as a VM, and not as something Nimbus should ship. The VM options (C and D) are the intuitive answer and the worst-performing ones, because kernel anti-cheat blocks VMs by design and the anti-cheat titles are precisely the ones in the incompatible tier. Building virtualization would be a lot of work to unlock nothing.

**The Linux native path (L1) is the technically correct answer** and requires no special hardware at all. It is also the worst market fit, since the Windows install base is the whole audience. That tension, not any engineering question, is the real decision.

**What is worth doing:**

1. **Probe Linux before committing to anything (one weekend, $0).** Boot Bazzite or a live USB on existing hardware, run Elden Ring under Proton, and write roughly 50 lines of python-evdev plus uinput that grabs the physical mouse and emits a virtual pad. That proves or kills the entire premise without porting Nimbus at all. Elden Ring is the ideal test subject: EAC-enabled for Proton, and currently only partially compatible on Windows because of exactly the camera drift this would eliminate. **Written up as a concrete test plan in [LINUX_PROBE_PLAN.md](LINUX_PROBE_PLAN.md).**
2. **Test cloud gaming (Option B).** One evening, about $20, no hardware. If the physical mouse doesn't leak into the GFN client, that's host mode for free on Windows.
3. **Document the two-PC setup (Option A)** as the supported path for the raw-input tier on Windows. It requires no Nimbus code at all, since Nimbus's ViGEm output already travels the Moonlight to Sunshine to ViGEm chain correctly. This is a docs change, not a feature.
4. **Option F stays the long-term Windows answer**, but note that its Linux equivalent is a single library call. If the Linux probe goes well, that reframes the cost/benefit on the driver work considerably.

**What not to do:** don't build VM management into Nimbus. Nimbus's value is the input surface. The cheap, useful move is making it *transport-aware*, meaning it understands that its virtual pad may be consumed by Moonlight or a cloud client rather than a local game, not shipping a hypervisor.

**Worth weighing honestly:** most of the compatibility list already works via ClipCursor. Isolation only unlocks the raw-input tier, which is dominated by twitch shooters that are the least accessible genre regardless of input method. The strongest argument for pursuing it is not Valorant, it's **Elden Ring and Dark Souls III**: EAC-protected, currently only partially compatible because of camera drift, and squarely the kind of game adaptive players ask for. A two-PC setup fully unlocks those today.

---

## 7. Option F in depth

Expanded because it is the only option that solves the problem on one machine, at zero latency, with no extra hardware. The conclusion is that it works technically and is blocked non-technically.

### 7.1 Why it works: the mechanism is confirmed

The Windows mouse stack is a single pipeline:

```
HID device -> mouhid.sys -> [FILTER SITS HERE] -> mouclass.sys -> win32k
```

`mouhid` converts HID reports into `MOUSE_INPUT_DATA` packets and hands them to `mouclass` through a class service callback. An upper-level filter driver may substitute its own callback and, per Microsoft's documentation, "filter the mouse data that is transferred to the class data queue, for example by deleting, transforming, or inserting data." The WDK ships `moufiltr` as exactly this template, and it works by intercepting `IOCTL_INTERNAL_MOUSE_CONNECT`, saving the real `CONNECT_DATA`, and substituting its own.

The decisive question was whether dropping packets there also kills Raw Input, or only the legacy cursor. **It kills both.** `win32k!ProcessMouseInput` applies movement to the desktop cursor *and* queues the same packets to `win32k!gMouseEventQueue`, which is what `win32k!RawInputThread` drains to generate `WM_INPUT`. One stream, two consumers, both downstream of the filter.

So a filter at this layer does what `WH_MOUSE_LL` cannot, and it does it for the entire Raw Input tier. The architecture is not in doubt.

### 7.2 The constraint nobody expects: no per-application filtering

This is the finding that changes the design. HidHide can hide a gamepad from one process and not another because applications open HID devices directly, so there is a process context at the moment of the open. **Mice have no such chokepoint.** Every application receives mouse input from `win32k`, not by opening the device, and `MouseClassServiceCallback` runs at DISPATCH_LEVEL in an arbitrary DPC context where "the current process" is meaningless.

You therefore cannot build "hide the mouse from the game but not from Nimbus." The only shape available is:

1. Suppress the physical mouse **globally**, for every application including the desktop shell.
2. Simultaneously publish those packets to Nimbus over a private channel (an inverted-call IOCTL on a control device object).
3. Nimbus consumes the deltas, drives its virtual stick, outputs to ViGEm, and **renders and moves its own cursor**, because the real one no longer moves.

That is a larger change to [bridge.py](../../src/bridge.py) than "add a driver." Nimbus stops being a Qt app that receives mouse events and becomes the system's mouse owner.

**Note the convergence:** this is precisely the F4 consequence documented in [LINUX_PROBE_PLAN.md](LINUX_PROBE_PLAN.md), where `EVIOCGRAB` also takes the device from the compositor and forces Nimbus to own the cursor. The two platforms arrive at an identical architecture from opposite directions. That is a strong argument for **running the Linux probe first**: it validates the "Nimbus owns the cursor" interaction model for a weekend and $0, before committing to kernel work that assumes it.

### 7.3 Signing, with an EV certificate already in hand

Having the EV cert removes the gating item, since attestation signing requires one to submit to Partner Center at all. What remains:

| Item | Status |
|---|---|
| EV code-signing certificate | **Already held.** Was the main gate. |
| Partner Center hardware dev account | Registration unblocked by the EV cert. Verify the current fee. |
| Attestation signing | Available for kernel-mode drivers, no HLK testing required |
| Runs on retail Windows 10/11 | **Yes.** Microsoft's signing matrix lists attestation dashboard signing as supported on Windows 10 and later. |
| Distribution via Windows Update | **No.** Retail Windows Update publication requires full WHCP/HLK. |
| Windows Server | Not supported for filter drivers; HLK only. Irrelevant here. |

One wrinkle to read correctly: Microsoft's documentation heads the attestation section "for testing scenarios" and says attestation-signed drivers "can't be published to Windows Update for retail audiences." That restriction is about **Windows Update as a distribution channel**, not about whether the driver loads. An attestation-signed driver loads normally on retail Windows 10/11 with Secure Boot on. This is exactly how ViGEmBus and HidHide ship, bundled in their own installers, which is also Nimbus's distribution model. So attestation signing is viable here despite the framing.

There is also **preproduction signing** for development, which permits testing with Secure Boot enabled on provisioned machines. Better than `bcdedit /set testsigning on`.

### 7.4 Time-sensitive: the April 2026 kernel trust change

Microsoft is removing trust for the legacy cross-signed driver program. The new kernel trust policy began rolling out in evaluation mode with the April 2026 servicing update to Windows 11 24H2, 25H2, 26H1, and Server 2025, after which the kernel accepts only drivers signed through the WHCP process, with an allow list preserved for older but reputable cross-signed drivers.

**This matters mainly for the buy-versus-build question in 7.5.** For a newly written, attestation-signed driver it is a non-issue, since that is the sanctioned path. For any existing third-party driver last validated in the Windows 10 era, it is potentially fatal.

### 7.5 Prior art, and why buying probably beats building

| Project | What it is | Verdict for Nimbus |
|---|---|---|
| **[Interception](https://github.com/oblitum/Interception)** | Kernel filter driver plus user-mode C library. Captures keyboard and mouse at driver level, can **block** input or transform it, per-device bindings. Functionally Option F, already built. LGPL library with binary distribution rights for open-source use. | **Checked 2026-09-05, do not build on it.** Last release 2017, licensor unreachable, signing undocumented, Windows 11 installer failures reported. Details in [section 9.1](#91-interception). |
| **[MouHidInputHook](https://github.com/changeofpace/MouHidInputHook)** | Filters, modifies, and injects `MOUSE_INPUT_DATA` without modifying the device stacks | Best available **reference** for the architecture. Research code, not shippable. |
| **[MouClassInputInjection](https://github.com/changeofpace/MouClassInputInjection)** | Kernel interface for injecting mouse packets into the stream | Reference for the injection half. |
| **`moufiltr` (WDK sample)** | Microsoft's official mouse filter template | **The correct starting point for building.** |
| **ViGEmBus / HidHide** | nefarius's signed, shipping input drivers | Proof a solo maintainer can ship and sign this class of driver. Also worth simply asking whether he would extend HidHide, given he already knows why it cannot currently cover mice. |

The last row is the cheapest lead in this document. Nimbus already depends on ViGEmBus. An upstream conversation costs an email.

### 7.6 The actual blocker: Nimbus looks exactly like a cheat device

This is the finding that should drive the decision, and it is not a technical one.

Converting mouse input into gamepad output is the defining signature of XIM and Cronus Zen. On console shooters the exploit is combining mouse precision with controller aim assist, and anti-cheat vendors hunt it specifically. Respawn classified XIM adapters as cheating devices in March 2026, permanent ban, no appeals, with 3,000+ Apex accounts banned in 2026. Activision has run parallel crackdowns in Call of Duty. **At the device layer, Nimbus is not distinguishable from a XIM.** Adding a kernel driver that globally suppresses physical mouse input while injecting synthetic gamepad input makes the resemblance stronger, not weaker.

Three things cut in Nimbus's favor:

1. **Detection has moved to behavior, not hardware.** Current systems "don't look at what hardware a player has connected. Instead, they study how inputs actually behave," tracking timing, consistency, and reaction patterns. This genuinely favors Nimbus: the XIM exploit produces superhuman consistency, while a disabled player driving a virtual stick with a mouse produces *slower and less consistent* input than an average player. Nimbus confers no advantage, and behavioral analysis is the kind of detection that can tell the difference.
2. **Vendors have stated accessibility carve-outs.** Respawn has said explicitly that adaptive controllers, custom button mapping devices, and accessibility peripherals will not be flagged, and that detection is being built to differentiate legitimate accessibility tools from cheat hardware.
3. **Nimbus is open source**, auditable, and can be identified positively rather than inferred.

And one that cuts hard against:

4. **False positives happen to exactly these users.** RICOCHET banned WheeledGamer, a paralyzed Call of Duty streamer, for using a QuadStick. The ban was rescinded on appeal only after it drew public attention. Adaptive input is non-standard by definition, and stated policy is not the same as a classifier that has seen your device.

**The path forward is disclosure and engagement, not engineering.** Publish the driver's identity and purpose, register it with anti-cheat vendors, pursue allow-listing, and work with organizations like AbleGamers that already advocate on this exact issue. Nimbus should never attempt to obscure what it is or how it appears to anti-cheat: for an accessibility tool, being clearly and openly identifiable is the asset. It is what makes an allow-list entry possible and what makes an appeal winnable. A tool that hid itself would forfeit both, and would deserve the ban it eventually got.

### 7.7 Honest cost summary

| Cost | Detail |
|---|---|
| EV certificate | $0, already held |
| Partner Center | One-time registration, fee to verify |
| Driver development | The real cost. KMDF filter from `moufiltr`, inverted-call IOCTL channel, installer, and a guaranteed-release watchdog. |
| Nimbus rework | Input intake moves off Qt events onto the driver channel; Nimbus renders its own cursor (7.2) |
| Ongoing | Cert renewal, re-submission per driver revision, and support for a kernel component whose failure mode is "user has no mouse" |
| Risk | Anti-cheat may block it regardless (7.6), and it is blocked in VMs anyway, so it never combines with Options C or D |

### 7.8 Recommendation for F

Do not start with the driver. In order:

1. **Email nefarius.** Ask whether HidHide could ever cover the mouse class, or whether he would advise on it. Effectively free, and he has already solved every adjacent problem.
2. **Check Interception's current signing status.** Done 2026-09-05, see [section 9.1](#91-interception): unmaintained, licensor unreachable, signing undocumented. It is not a prototype path. OpenInputBridge is the maintained successor to watch.
3. **Run the Linux probe** ([LINUX_PROBE_PLAN.md](LINUX_PROBE_PLAN.md)). It validates the "Nimbus owns the cursor" model from 7.2 for a weekend, and that model is the part most likely to be wrong.
4. **Open an accessibility dialogue with one anti-cheat vendor** before writing kernel code. If the answer is "we will never allow-list a software mouse-to-pad converter," that is worth knowing before the effort, not after. Respawn's stated position makes them a reasonable first contact.
5. **Only then** build, using `moufiltr` as the base and attestation signing for distribution.

---

## 8. Measured on Windows (2026-09-05)

Sections 1 to 7 rest on documentation and reasoning. This section is measurement, taken on the dev machine (Windows 11 Pro 25H2, build 26200, 2560x1440 at 100%, ViGEmBus 1.17, Secure Boot off) with two throwaway probes that mirror the Linux ones: `tests/probe_rawinput_windows.py` and `tests/probe_game_mouselook_windows.py`. Both inject motion with `SendInput`, which follows the same `win32k` routing as a physical mouse (foreground rules, `RIDEV_INPUTSINK`, `WH_MOUSE_LL`) but enters above any kernel filter, so these numbers say nothing about Option F itself. They say what user mode can and cannot do.

### 8.1 Raw Input routing, against a fake game

The probe spawns a window that registers for HID mouse Raw Input the way a game does, injects 40 relative moves (summed |delta| 720), and reads back what the game window and a Nimbus stand-in (registered with `RIDEV_INPUTSINK`) received.

| Scenario | Game `WM_INPUT` delta | Game `WM_MOUSEMOVE` | Game told it lost focus? | Nimbus stand-in `WM_INPUT` delta |
|---|---|---|---|---|
| Baseline, game foreground | 720 | 40 | no | 720 |
| `WH_MOUSE_LL` hook returning 1 for every event (hook saw 40, dropped 40) | **720** | 0 | no | 720 |
| Nimbus stand-in takes the foreground | **0** (720 if the game registered with `RIDEV_INPUTSINK`) | 40 | yes: `WM_ACTIVATE`, `WM_ACTIVATEAPP`, `WM_KILLFOCUS` | 720 |
| `AttachThreadInput` to the game's queue, then `SetFocus` on the stand-in | 720 with an `hwndTarget` registration, 0 with a NULL target | 40 | yes, same three messages | 720 |
| `BlockInput(TRUE)` from another process | not testable: `ERROR_ACCESS_DENIED` from a normal process, and it would blind Nimbus as well | | | |

Same result across all four registration styles (`hwndTarget` or NULL, none / `RIDEV_INPUTSINK` / `RIDEV_EXINPUTSINK`). Read it as:

- The hook in `mouse_hider.py` does exactly what the compatibility doc says: it stops `WM_MOUSEMOVE` and nothing else. Dropping 100% of events at `WH_MOUSE_LL` leaves `WM_INPUT` untouched.
- The **only** user-mode action that stops `WM_INPUT` is taking the foreground away from the game, and a game that registered with `RIDEV_INPUTSINK` keeps receiving even then. Every focus trick delivers the same activation and focus-loss messages engines use to pause or ignore input, so there is no "foreground for Raw Input but not for the game" state to exploit.
- Nothing else in user mode is left. Microsoft's HID architecture page states why: mouse and keyboard top-level collections are opened exclusively by the Raw Input Manager, so no process can read them, let alone block them, from user mode.

### 8.2 Real games

`tests/probe_game_mouselook_windows.py` captures the game's client area before and after a stimulus and counts changed samples (step 6, RGB delta over 60), the same rule as the Linux probe: moved if above `max(3*noise, 150)`, still if at or below `max(2*noise, 60)`.

**Elden Ring v1.17, EAC, online, windowed 1600x900, in the Stranded Graveyard cave:**

| Condition | Changed samples | Verdict |
|---|---|---|
| idle (noise floor) | 433 | still |
| 400 px mouse sweep, game foreground | 1,781 | moved |
| same sweep, `WH_MOUSE_LL` dropping 100% of events | **1,759** | **moved** |
| same sweep, Nimbus stand-in foreground | 312 | still |
| right stick held 0.6 s (ViGEm), game foreground | 12,412 | moved |
| right stick held, Nimbus stand-in foreground | **65** | **still** |
| mouse sweep again, game foreground | 8,797 | moved |

The compass in the saved frames swings from N toward E under the hook. So on Windows, Elden Ring is exactly the case the table in section 1 predicts: the hook is invisible to it, and the one user-mode trick that does stop the mouse also stops the pad, because the game ignores XInput the moment it loses the foreground. EAC did not react to the hook, the injected input, or the focus changes during this session, which is one data point and not a policy.

**Carrier Command 2 v1.5.18** is the opposite case. In a new campaign, first-person on the bridge (maximized window, noise floor 50): a 400 px sweep changed 11,945 samples with the game foreground, **25** with the hook dropping every event, 52 with the stand-in foreground, and 7,186 again afterwards. The menu cockpit behaved the same (41,419 to 1,228). So it reads the mouse through the cursor path, not Raw Input, and Nimbus's existing Full Game Mode already covers it on Windows. The right stick did not move the view in either place (0 changed samples), so the unfocused-pad question was not answerable there.

### 8.3 What this settles

1. **User mode is exhausted on Windows.** The Raw Input tier needs a kernel-mode filter, full stop. The design for it is in [WINDOWS_MOUSE_FILTER_PLAN.md](WINDOWS_MOUSE_FILTER_PLAN.md).
2. **The foreground trick is not a shortcut for Elden Ring.** It might still be a per-game toggle for games that keep polling XInput while unfocused, but the game that motivates this work is not one of them.
3. **Interception is not the shortcut either.** Last release 2017, licensor unreachable in 2026 issues, signing status undocumented. On this dev machine the April 2026 Windows Driver Policy is still in evaluation mode (Code Integrity audits `loopbe1.sys`, a cross-signed driver, at every boot, which resets the enforcement counter), so a cross-signed Interception would load here and then stop loading on users' machines once enforcement flips.
4. **Attestation signing still works** for a new driver, and two MIT projects show the shape of it: RawAccel (a shipping signed mouclass filter that rewrites deltas at the layer Option F would drop them, and works in Raw Input games, which confirms 7.1 empirically) and OpenInputBridge (a clean-room Interception-compatible filter for Windows 11, no signed release yet).
5. **The Linux branch's user-mode half carries over.** Once the driver delivers packets, `bridge.py`'s software cursor, synthetic Qt events, and the Isolate Mouse UI from `linux-uinput-support` are the Windows implementation too; only the source of deltas changes.

### 8.4 Measured with the filter (2026-09-05, dev build under test signing)

The Nimbus Mouse Filter from [WINDOWS_MOUSE_FILTER_PLAN.md](WINDOWS_MOUSE_FILTER_PLAN.md) was loaded on the same machine after the test-signing reboot and run against the fake Raw Input game with a person at the Logitech mouse (`tests/probe_mouse_filter_windows.py --attended`, 8 s per phase):

| Phase | Fake game `WM_INPUT` delta | Packets the driver passed to `mouclass` | Packets the driver captured for Nimbus |
|---|---|---|---|
| pass-through | 35,550 | 1,015 | 0 |
| isolated | **0** | 0 | **1,017** (103,653 px delivered to the client) |
| released | 54,163 | 1,012 | 0 |

Nothing dropped, the cursor froze during isolation and returned on release, and the six unattended safety checks (lifecycle, handle drop, watchdog, fail-fast read, exclusivity) passed first. This is the Windows counterpart of the Linux P2 result in section 5, with one gap: the game here is the probe's fake window, because Elden Ring's anti-cheat refuses to start while test signing is on. Closing that gap needs an attestation-signed build.

One operational fact came out of the first attempt: motion sent through TeamViewer never reached the driver at all (72 `WM_MOUSEMOVE` at the game, 0 packets at the filter), because remote-control tools inject with `SendInput`, above `mouclass`. Hands-on validation has to happen at the physical mouse.

---

## 9. Prior art and the signing landscape, checked 2026-09-05

Section 7 was written from memory of these projects. This is what they look like when checked, with links in the Sources list. Verified facts are stated as facts; inferences are marked.

### 9.1 Interception

- Latest release v1.0.1, 12 May 2017. Last push to the repository 9 Aug 2021. 73 open issues.
- 2026 issues asking how to buy a license (April) and reporting that "the creator disappeared" (July) are unanswered.
- The README says only "Tested from Windows XP to Windows 10." No primary source states how `keyboard.sys` and `mouse.sys` are signed. Inference: a 2017 closed-source driver that loads with Secure Boot on is cross-signed, which is the class the 2026 policy de-trusts.
- Windows 11 reports in 2026: installer failures writing to `system32\drivers` (January, March), and an EAC interference report (April) that implies the driver still loaded on that machine.
- License: LGPL library plus binary redistribution of the driver for non-commercial use "once communication with drivers happen solely by use of the library and its API"; commercial use needs a paid license from a licensor who appears unreachable.
- Successor: **OpenInputBridge** (Applet LLC, repository created July 2026), a clean-room, IOCTL-compatible `kbdclass`/`mouclass` upper filter for Windows 11 only. MIT source, self-built binaries need test signing, a paid WHQL-signed build is announced but not shipped, 20-device limit. Its README warns that installing on Windows 10 leaves keyboard and mouse unusable after restart, and that uninstalling with `pnputil -d` leaves remnants that do the same. Read it for the architecture; do not depend on it yet.

### 9.2 The Windows Driver Policy (cross-signed trust removal)

- Announced 26 March 2026. Ships in evaluation mode with the April 2026 security update on Windows 11 24H2, 25H2, 26H1 and Server 2025; "all future versions ... will enforce" it.
- Enforcement is **per device, not a calendar date**: a device switches to enforcement after 250 hours of active use and 3 reboots (2 on Server) with no audited violation. Any audited cross-signed load resets the counters. (Press coverage in late March quoted 100 hours; the support page says 250.)
- Detection: Code Integrity operational log event **3076** = audited by the audit policy `{784C4414-79F4-4C32-A6A5-F0FB42A51D0D}`, event **3077** = blocked by the enforce policy `{8F9CB695-5D48-48D6-A329-7202B44607E3}`. `citool -lp -json` lists active policies; `.cip` files live under `System32\CodeIntegrity\CiPolicies\Active\` with the enforce policy parked in `Reserved\` until activation. Microsoft documents `CiTool.exe --remove-policy` as the opt-out.
- The allow list of reputable cross-signed drivers is embedded in the signed policy; no public list exists.
- **Attestation-signed drivers are not affected today.** The announcement does not mention attestation; Microsoft's Zac Lockard on OSR: "There's nothing immediate for attestation drivers, although we are looking into how we could have everything require the HLK." The driver-signing page (updated 14 April 2026) now frames attestation as "for testing scenarios", but attestation-signed drivers still load on Windows 11 clients. Never on Server.
- On the dev machine: the audit policy is active, the enforce policy sits in `Reserved`, and at every boot `loopbe1.sys` is audited (3076) while `gdrv3.sys` and `ene.sys` are blocked (3077).

### 9.3 UsbDk

Red Hat's USB hub filter (Apache-2.0, v1.00-22 in March 2024, attestation-signed since 1.0.19 in 2017) can take any USB device, including a HID mouse, away from Windows and hand it to a user-mode app through libusb. It is a whole-device grab: Windows loses the mouse entirely, the app must parse raw HID reports, composite receivers take the keyboard with them, and Bluetooth mice are out of reach. One unanswered issue reports a 24H2 machine left unbootable, and the libusb wiki discourages the backend for stability. Not a path for Nimbus.

### 9.4 HidHide

Still cannot hide mice or keyboards. The FAQ (copyright 2020 to 2026) repeats that they "travel through different means and routes", and nefarius wrote in 2021 that blocking them "is impossible with the design of HidHide and that's intentional". No nefarius project filters `mouclass`.

### 9.5 What Microsoft's documentation says about user mode

- `RAWINPUTDEVICE`: raw input reaches the registered application "as long as it has the window focus"; with a NULL `hwndTarget` it "follows the keyboard focus"; `RIDEV_INPUTSINK` delivers "even when the caller is not in the foreground"; `RIDEV_EXINPUTSINK` delivers in the background only if the foreground application is not registered. Matches section 8.1 exactly.
- HID architecture: mouse and keyboard top-level collections are opened exclusively by the Raw Input Manager "for security reasons"; user mode can open them without read or write access. This is the direct denial of an `EVIOCGRAB` equivalent.
- `LowLevelMouseProc`: runs when an event "is about to be posted into a thread input queue" and only stops "the target window procedure". Raw Input is a separate path.
- `BlockInput`: blocks "keyboard and mouse input events from reaching applications"; never mentions `WM_INPUT`, needs elevation, and would blind Nimbus too.
- What is left are fragile tricks: take the foreground yourself (section 8 shows why that fails for Elden Ring), inject a `WH_GETMESSAGE` hook DLL into the game and rewrite `WM_INPUT` (anti-cheat blocks it and `GetRawInputBuffer` bypasses it), or disable the device node (removes the mouse for Nimbus as well).

### 9.6 Other drivers that touch mouse input

| Project | What it is | Usable? |
|---|---|---|
| reWASD `hidgamemap.sys` | Proprietary HID-class filter plus a virtual input device; can wedge the HID stack | No, not licensable |
| Keyran | Proprietary injector driver "for games where macros do not work" | No |
| Logitech G HUB, Razer | Vendor-specific virtual HID and bus drivers, no mouse suppression | No |
| QuadStick | A HID device plus a user-mode manager, no kernel driver | Not applicable |
| **RawAccel** | MIT, signed `mouclass` upper filter, v1.7.1 (July 2025). Rewrites `LastX`/`LastY` at exactly the layer Option F would drop them, and works in Raw Input games | Reference for build, installer, and signing; the filtering logic differs |
| **`moufiltr`** (WDK sample) | Microsoft's mouse filter template | The starting point |

### 9.7 Bottom line

There is no user-mode Windows equivalent of `EVIOCGRAB`; Microsoft opens mouse collections exclusively and routes Raw Input by foreground on purpose. Every working solution is a kernel filter. Interception is unmaintained with an unreachable licensor, UsbDk is a blunt USB-level grab, HidHide will not help. The realistic options are a Nimbus-owned `moufiltr`-style filter, attestation-signed now (with the risk that Microsoft later requires HLK), or waiting for OpenInputBridge's WHQL build. Either way, ship detection of Code Integrity events 3076 and 3077 so a user whose driver stops loading learns why. The plan is in [WINDOWS_MOUSE_FILTER_PLAN.md](WINDOWS_MOUSE_FILTER_PLAN.md).

---

## Sources

- [Gamescope, ArchWiki](https://wiki.archlinux.org/title/Gamescope)
- [ValveSoftware/gamescope](https://github.com/ValveSoftware/gamescope)
- [Steam Deck, HID, and libmanette adventures](https://blogs.gnome.org/alicem/2024/10/24/steam-deck-hid-and-libmanette-adventures/)
- [HidHide FAQ, mouse/keyboard limitation](https://docs.nefarius.at/projects/HidHide/FAQ/)
- [nefarius/HidHide](https://github.com/nefarius/HidHide)
- [Partition and share GPUs with virtual machines on Hyper-V, Microsoft Learn](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/gpu-partitioning)
- [Troubleshooting Hyper-V GPU assignment, partitioning, and passthrough, Microsoft Learn](https://learn.microsoft.com/en-us/troubleshoot/windows-server/virtualization/troubleshoot-hyper-v-gpu-assignment-partitioning-passthrough-issues)
- [jamesstringer90/Easy-GPU-PV](https://github.com/jamesstringer90/Easy-GPU-PV)
- [DanielChrobak/Hyper-V-GPU-Paravirtualization-Manager](https://github.com/DanielChrobak/Hyper-V-GPU-Paravirtualization-Manager)
- [Sunshine, Keyboard and Mouse Input (DeepWiki)](https://deepwiki.com/LizardByte/Sunshine/7.3-keyboard-and-mouse-input)
- [Apollo issue #1479, Mouse input broken with raw input games, SendInput vs kernel HID](https://github.com/ClassicOldSong/Apollo/issues/1479)
- [Things you really should know about Windows Input, Raw Mouse edition](https://ph3at.github.io/posts/Windows-Input/)
- [Self-Hosted Linux IOMMU/VFIO GPU Passthrough: VFIO, Looking Glass and vendor-reset](https://www.pistack.xyz/posts/2026-05-23-self-hosted-linux-iommu-vfio-gpu-passthrough-looking-glass-vendor-reset/)
- [Anti-Cheat Systems Explained 2026: Vanguard, BattlEye, EAC, VAC](https://tateware.com/blog/anti-cheat-comparison-2026)
- [python-evdev documentation (EVIOCGRAB via `InputDevice.grab()`)](https://manpages.ubuntu.com/manpages/questing/man7/python-evdev.7.html)
- [vgamepad on PyPI (Linux backend, marked experimental)](https://pypi.org/project/vgamepad/)
- [Emulating Xbox controllers on Linux](https://aweirdimagination.net/2015/04/06/emulating-xbox-controllers-on-linux/)
- [Easy Anti-Cheat on Linux: A Comprehensive Guide](https://linuxvox.com/blog/easy-anticheat-linux/)
- [How to Play Competitive Games on Linux in 2026: Anti-Cheat Guide](https://www.ingamenews.com/2026/05/how-to-play-competitive-games-on-linux.html)
- [Qt Forum: window stays on top under Plasma Wayland](https://forum.qt.io/topic/143381/how-to-get-window-stays-on-top-in-plasma-wayland)
- [Qt Forum: using layer-shell-qt with Qt windows](https://forum.qt.io/topic/153350/using-qwidget-as-qwindow-for-layer-shell-qt)

### Option F: driver architecture, signing, and anti-cheat

- [Configuration of Keyboard and Mouse Class Drivers, Microsoft Learn](https://learn.microsoft.com/en-us/previous-versions/windows/hardware/hid/keyboard-and-mouse-class-drivers)
- [MouseClassServiceCallback routine, Microsoft Learn](https://learn.microsoft.com/en-us/previous-versions/ff542394(v=vs.85))
- [IOCTL_INTERNAL_MOUSE_CONNECT, Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/kbdmou/ni-kbdmou-ioctl_internal_mouse_connect)
- [Mouse Input WDF Filter Driver (moufiltr) sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/mouse-input-wdf-filter-driver-moufiltr/)
- [changeofpace/MouHidInputHook (input stack architecture reference)](https://github.com/changeofpace/MouHidInputHook)
- [changeofpace/MouClassInputInjection](https://github.com/changeofpace/MouClassInputInjection)
- [oblitum/Interception](https://github.com/oblitum/Interception)
- [Driver Signing Options, Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings)
- [Advancing Windows driver security: removing trust for the cross-signed driver program](https://techcommunity.microsoft.com/blog/windows-itpro-blog/advancing-windows-driver-security-removing-trust-for-the-cross-signed-driver-pro/4504818)
- [April 2026 Windows update ends cross-signed kernel driver trust](https://windowsforum.com/threads/april-2026-windows-update-ends-cross-signed-kernel-driver-trust.410487/)
- [Apex Legends XIM and Cronus ban wave 2026](https://www.versaciboosts.com/blog/apex-legends-xim-cronus-titan-two-ban-2026-safe-boosting)
- [CoD cracks down on Cronus Zen and XIM, Dexerto](https://www.dexerto.com/call-of-duty/cod-cracks-down-on-cronus-zen-xim-in-major-anti-cheat-update-for-black-ops-7-season-2-3313252/)
- [CoD unbans disabled streamer after accessibility controller flagged as cheating device, Dexerto](https://www.dexerto.com/twitch/paralyzed-cod-warzone-streamer-begs-activision-for-help-after-accessibility-controller-ban-3367476/)
- [AbleGamers: don't lock out gamers with disabilities](https://ablegamers.org/ablegamers-plea-to-microsoft-dont-lock-out-gamers-with-disabilities/)

### Sections 8 and 9: Windows measurements, prior art, and signing (checked 2026-09-05)

- [RAWINPUTDEVICE structure, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-rawinputdevice)
- [WM_INPUT message, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-input)
- [HID Architecture (exclusive access to mouse and keyboard collections), Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hid-architecture)
- [LowLevelMouseProc callback, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)
- [BlockInput function, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-blockinput)
- [GetMsgProc callback, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/getmsgproc)
- [Interception releases](https://api.github.com/repos/oblitum/Interception/releases) and [issues](https://github.com/oblitum/Interception/issues)
- [Applet-LLC/OpenInputBridge](https://github.com/Applet-LLC/OpenInputBridge)
- [The Windows Driver Policy, Microsoft Support](https://support.microsoft.com/en-us/windows/hardware/drivers/the-windows-driver-policy)
- [OSR thread on the Windows Driver Policy, with Microsoft's comment on attestation](https://community.osr.com/t/advancing-windows-driver-security-the-windows-driver-policy/60086)
- [Driver blocked by the policy: openport.sys fix notes (event 3077)](https://www.recusoft.com/driverblocked/fix.html)
- [Microsoft Q&A: how do I remove the Windows Driver Policy](https://learn.microsoft.com/en-us/answers/questions/5921477/how-do-i-remove-windows-driver-policy)
- [daynix/UsbDk releases](https://github.com/daynix/usbdk/releases), [issue #134 (24H2 boot failure)](https://github.com/daynix/UsbDk/issues/134), [Red Hat bug 1434314 (attestation signing)](https://bugzilla.redhat.com/show_bug.cgi?id=1434314)
- [libusb wiki: Windows backends](https://github.com/libusb/libusb/wiki/Windows)
- [HidHide issue #4: keyboard and mouse hiding is impossible by design](https://github.com/nefarius/HidHide/issues/4)
- [nefarius project index](https://docs.nefarius.at/projects/)
- [reWASD forum: hidgamemap.sys](https://forum.rewasd.com/forum/rewasd/technical-questions-aa/219751-hidgamemap-sys-has-issues-that-needs-to-be-addressed-asap)
- [Keyran wiki](https://keyran.net/en/wiki/)
- [RawAccelOfficial/rawaccel](https://github.com/RawAccelOfficial/rawaccel)
- [moufiltr sample source](https://github.com/microsoft/Windows-driver-samples/tree/main/input/moufiltr)
- [Supported WDK downloads (26100.6584 for Visual Studio 2022)](https://learn.microsoft.com/en-us/windows-hardware/drivers/other-wdk-downloads)
- [Install the WDK using NuGet](https://learn.microsoft.com/en-us/windows-hardware/drivers/install-the-wdk-using-nuget)
