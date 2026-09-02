# Host Mode: Running Games in an Isolated Environment

**Status:** Research only. No implementation, no commitment.
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
| `WH_MOUSE_LL` suppression hook (fails on Raw Input) | `EVIOCGRAB` exclusive grab. Only one process may hold it, and nothing else receives events until released. **Shipped (2026-09):** `src/mouse_isolation.py`, pure-Python ioctl, no python-evdev, with keyboard pass-through for combo devices and a `Ctrl+Alt+F12` release. |
| `borderless.py`: ClipCursor polling, borderless conversion | Delete. gamescope and Wayland handle confinement and fullscreen. |
| `window_utils.py`: `WS_EX_NOACTIVATE` focus juggling | Largely delete. Wayland clients cannot steal focus the way Win32 windows can. |
| `mouse_hider.py` | Delete. |
| `vjoy_interface.py` (pyvjoy, requires vJoy driver) | **Shipped (2026-09):** `UInputJoystickInterface` in `src/uinput_interface.py`, a pure-Python `uinput` device with 8 axes and 56 buttons. |
| `vigem_interface.py` (vgamepad, requires ViGEmBus) | **Shipped (2026-09):** `UInputXboxInterface`, a `uinput` Xbox 360 pad with the xpad VID/PID. (vgamepad 0.1.0 does have an experimental Linux backend, but it needs libevdev; Nimbus does not use it.) |
| **Option F: signed kernel filter driver, EV cert, bricking risk** | **`InputDevice.grab()`. One line.** |

That last row is the finding. The single highest-effort item on the Windows roadmap, a kernel-mode mouse class filter driver with a $300 to $600/yr code-signing certificate, is a standard library call on Linux.

The port is also smaller than it looks. PySide6, QML, `bridge.py`, `config.py`, the profile system, and telemetry are all cross-platform and move unchanged. That is the bulk of the codebase. Three Windows-specific modules get **deleted** rather than ported, because the problems they exist to solve do not occur.

### Real friction, honestly

1. **Wayland's isolation cuts both ways.** The same strict client separation that kills the input leak also blocks an always-on-top overlay from positioning itself. `Qt.WindowStaysOnTopHint` does not work on KWin Wayland, and `move()`/`setGeometry()` are not honored. You need `layer-shell-qt` or KWindowSystem, and behavior varies by compositor (GNOME/Mutter notably does not implement layer-shell). For an app whose entire form factor is a floating panel beside a game, this is the main porting tax and it is not trivial.
2. **No rumble or LEDs.** Nimbus's own uinput back end covers sticks, triggers, D-pad, and buttons; force feedback is not implemented (it is not on Windows either).
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

### Measured, 2026-09-02

The output half of L1 is no longer a plan. Nimbus runs on Linux (`docs/setup/LINUX.md`) and the following were measured on an Ubuntu 24.04 X11 desktop with Steam installed:

| Claim in this section | Result |
|---|---|
| A uinput pad is seen as a standard gamepad | **Confirmed.** SDL reports `Xbox 360 Controller`, `SDL_IsGameController() == true`, with its built-in X360 mapping; every stick, trigger, D-pad direction, and button lands on the expected SDL control. Steam's `controller.txt` logged the pad and loaded its `configset_controller_xbox360.vdf` the moment the app started. Proton uses the same SDL path. |
| `window_utils.py` can "largely" be deleted | **Confirmed for X11.** A Qt window with `Qt.WindowDoesNotAcceptFocus` keeps receiving pointer events while the previously active window keeps keyboard focus, which is Game Focus Mode in one flag. The bridge now uses it off Windows. Wayland is untested; compositors decide focus there. |
| `EVIOCGRAB` isolates the mouse from everything else | **Confirmed.** `tests/probe_evdev_grab.py` grabbed a mouse's evdev node and synthesised motion: the X11 desktop pointer did not move at all while the grabbing process received every `REL_X/REL_Y` event; releasing the grab restored normal pointer movement. The real Logitech mouse could be grabbed the same way. Reading mouse-class nodes needs the `input` group (or a udev rule); no root, no driver. The one-line claim in the table above holds. **Shipped** as View > Isolate Mouse and as part of Game Mode on Linux, and **confirmed in a real Proton game**: in Carrier Command 2's free-look cockpit a 400 px sweep from a virtual mouse rotated the camera (23,845 changed frame samples) when ungrabbed and changed 1 sample while Nimbus held the grab, with the game confining the pointer the whole time. The same session showed the Steam "Controller Connected: Xbox 360 Controller" toast for the uinput pad, and its A button advanced the title screen. |
| Wayland form-factor tax (Probe 2) | **Not measured** (X11 host; a nested GNOME Wayland shell would not start). Note that the main window does not use always-on-top or absolute positioning today, so only "does the UI render" is really at stake. |
| Controller-mode enforcement is Windows-only | **No longer.** The keep-alive pulse is driver-agnostic (`src/controller_pulse.py`) and drives the uinput pad; only the Win32 mouse hook and ClipCursor release stay Windows-specific. |

Two nuances worth keeping in mind for L1. Under Wine/Proton, `ClipCursor` becomes an X pointer grab, so without `EVIOCGRAB` the confinement problem looks the same as on Windows and the controller-mode trick is the counter-measure. The F4 consequence in the probe plan is handled the way it predicted: once the physical mouse is grabbed, Nimbus's own window stops receiving it, so the bridge reads the evdev deltas itself, keeps a software cursor, and delivers synthetic Qt mouse events to its window. The widgets are unchanged.

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
| **[Interception](https://github.com/oblitum/Interception)** | Kernel filter driver plus user-mode C library. Captures keyboard and mouse at driver level, can **block** input or transform it, per-device bindings. Functionally Option F, already built. LGPL library with binary distribution rights for open-source use. | **Closest match, but likely dead.** README states "Tested from Windows XP to Windows 10," with no statement on signing status, Secure Boot, or Windows 11. Given 7.4, a cross-signed driver of that vintage is exactly what the April 2026 change targets. **Verify its current signature before considering it**; if it is cross-signed and not allow-listed, it will stop loading. |
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
2. **Check Interception's current signing status** against the April 2026 change. If it still loads on Windows 11 25H2 with Secure Boot on, it prototypes the entire concept with no driver work at all.
3. **Run the Linux probe** ([LINUX_PROBE_PLAN.md](LINUX_PROBE_PLAN.md)). It validates the "Nimbus owns the cursor" model from 7.2 for a weekend, and that model is the part most likely to be wrong.
4. **Open an accessibility dialogue with one anti-cheat vendor** before writing kernel code. If the answer is "we will never allow-list a software mouse-to-pad converter," that is worth knowing before the effort, not after. Respawn's stated position makes them a reasonable first contact.
5. **Only then** build, using `moufiltr` as the base and attestation signing for distribution.

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
