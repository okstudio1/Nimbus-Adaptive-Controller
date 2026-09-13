# VDroid — Custom Virtual Controller Driver Brainstorm

> **Superseded in part, 2026-09-09.** Option C (fork ViGEmBus) is worked out
> properly in [docs/vision/PAD_BUS_FORK_PLAN.md](../vision/PAD_BUS_FORK_PLAN.md),
> against a real clone of the upstream source. Two premises below are wrong and
> the plan corrects them: **(1)** the "8 axes" ceiling is DirectInput's shape
> (`DIJOYSTATE2`), not vJoy's, so a wider HID device does not give games more
> axes; **(2)** Options A, B and D (UMDF/KMDF HID, VHF, HID-over-transport)
> cannot do XInput at all, because `XInput1_4.dll` only sees devices bound to
> `xusb22.sys`, which binds to the USB compatible ID `USB\MS_COMP_XUSB10`. USB
> device emulation is the price of XInput. Read the plan before building from
> this page.

## The Problem

vJoy provides **8 axes** (X, Y, Z, RX, RY, RZ, SL0, SL1) per virtual device. That means a maximum of **4 two-axis joysticks**. For users who need 5+ joysticks, multiple sliders, and analog controls simultaneously, vJoy becomes the bottleneck — not our software.

ViGEm (Xbox 360 emulation) is even more constrained: **2 sticks + 2 triggers + 14 buttons**.

A custom virtual controller driver ("VDroid") would remove these limits entirely and let Project Nimbus scale to any number of axes, buttons, and input devices.

---

## What We'd Be Building

A **Windows kernel-mode or user-mode virtual HID device driver** that:
1. Presents one or more virtual joystick/gamepad devices to Windows
2. Exposes a user-mode API (DLL or named pipe) for our Python app to send axis/button data
3. Supports an arbitrary number of axes, buttons, and POV hats per device
4. Is recognized by DirectInput and ideally XInput-compatible games

---

## Architecture Options

### Option A: UMDF (User-Mode Driver Framework) HID Minidriver

**How it works:**
- Write a UMDF2 driver that implements a virtual HID device
- The driver presents a custom HID Report Descriptor defining N axes and M buttons
- A user-mode companion service or DLL accepts data from our Python app and passes it to the driver via IOCTL

**Pros:**
- User-mode = safer (crashes don't BSOD)
- Microsoft provides UMDF HID samples (e.g. `vhidmini2`)
- Easier to debug than kernel-mode
- Can be signed with a standard EV certificate

**Cons:**
- Still requires driver signing (EV cert + WHQL or attestation signing)
- UMDF has some latency overhead vs. kernel-mode
- Complex HID report descriptor authoring

**Effort:** Medium-high. Microsoft's `vhidmini2` sample is a starting point.

### Option B: KMDF (Kernel-Mode Driver Framework) HID Minidriver

**How it works:**
- Same concept as UMDF but runs in kernel space
- Lower latency, more control

**Pros:**
- Lowest possible latency
- Full control over HID descriptors and timing

**Cons:**
- Kernel-mode crash = BSOD
- Requires WHQL signing for Windows 10/11 (expensive, slow process)
- Much harder to develop and debug
- Needs a dedicated test machine or VM

**Effort:** High. Only justified if UMDF latency is insufficient.

### Option C: ViGEmBus Fork / Extension

**How it works:**
- Fork the ViGEmBus driver (open-source, kernel-mode)
- Add a new device type beyond Xbox 360 / DualShock 4
- Define a custom gamepad with more axes

**Pros:**
- Proven, production-tested driver framework
- Already handles device creation/destruction
- Community and documentation exist
- C codebase, well-structured

**Cons:**
- ViGEmBus is Xbox-specific in many places; extending it may fight the architecture
- Still kernel-mode with signing requirements
- ViGEm project is in maintenance mode (creator stepped back)
- XInput games may not recognize extra axes

**Effort:** Medium. Good if we want Xbox compatibility + extra axes.

### Option D: Virtual HID via HID over I2C / SPI Shim

**How it works:**
- Use Windows' built-in HID transport support
- Create a virtual I2C/SPI bus driver that injects HID reports
- The OS treats it as a real HID device

**Pros:**
- Leverages existing Windows HID infrastructure
- No custom HID minidriver needed (just the bus driver)

**Cons:**
- Very niche, poorly documented approach
- May not be stable across Windows versions
- Still needs driver signing

**Effort:** Medium, but risky due to sparse documentation.

### Option E: Multi-Device vJoy (No Custom Driver)

**How it works:**
- vJoy supports up to **16 virtual devices** (device IDs 1-16)
- Each device has 8 axes + 128 buttons
- We'd open multiple vJoy devices simultaneously

**Pros:**
- **No driver development needed** — just use existing vJoy
- 16 devices × 8 axes = **128 axes** (64 joysticks!)
- 16 devices × 128 buttons = **2048 buttons**
- Works today with current vJoy installation

**Cons:**
- Games typically only see one joystick device at a time
- Would need a "device combiner" or the game must support multi-device
- Some games and middleware (Steam Input) handle multiple devices fine
- Adds complexity to our bridge layer

**Effort:** **Low.** This is the pragmatic first step before writing a custom driver.

---

## HID Report Descriptor Deep Dive

The key to a custom driver is crafting the **HID Report Descriptor** — a binary blob that tells Windows "this device has N axes and M buttons."

### Standard Axis Usages (HID Usage Tables)
```
Usage Page: Generic Desktop (0x01)
  X       (0x30)     RX      (0x33)
  Y       (0x31)     RY      (0x34)
  Z       (0x32)     RZ      (0x35)
  Slider  (0x36)     Dial    (0x37)
  Wheel   (0x38)     Hat     (0x39)
  Vx      (0x40)     Vy      (0x41)
  Vz      (0x42)     Vbrx    (0x43)
  Vbry    (0x44)     Vbrz    (0x45)
  Vno     (0x46)

Usage Page: Simulation Controls (0x02)
  Throttle        (0xBB)
  Rudder          (0xBA)
  Aileron         (0xB0)
  Elevator        (0xB8)
  Steering        (0xC8)
  Accelerator     (0xC4)
  Brake           (0xC5)
  Clutch          (0xC6)
```

### What This Means

Windows HID supports **far more axis types** than vJoy exposes. A custom driver could define:
- 6 standard axes (X/Y/Z/RX/RY/RZ)
- 2+ sliders
- Throttle, rudder, aileron, elevator (simulation)
- Steering, accelerator, brake, clutch (driving)
- Velocity axes (Vx/Vy/Vz) for 3D spatial input
- Up to **65535 buttons** per HID report

Realistically, **32 axes + 256 buttons** in a single device would cover any conceivable accessibility use case.

---

## Recommended Path

### Phase 1: Multi-Device vJoy (Now)
- Open 2-4 vJoy devices from our bridge
- Map widgets to specific devices
- Test with games that support multiple controllers
- **Zero driver development, works today**

### Phase 2: UMDF Virtual HID (Future)
- Use Microsoft's `vhidmini2` sample as a starting point
- Define a custom HID report descriptor with 16-32 axes + 256 buttons
- Build a companion DLL with a simple API: `VDroidSetAxis(device, axis, value)`
- Write a Python ctypes wrapper
- Sign with our EV certificate (attestation signing for Windows 10+)

### Phase 3: XInput Compatibility Layer (Optional)
- If games need XInput, add a translation layer
- Maps first 2 joysticks to virtual Xbox sticks
- Rest of axes available via DirectInput
- Could extend ViGEmBus or build a standalone XInput wrapper

---

## Development Requirements

### For Phase 2 (Custom Driver)

| Requirement | Details |
|-------------|---------|
| Language | C/C++ (WDK required) |
| SDK | Windows Driver Kit (WDK) |
| IDE | Visual Studio 2022 with WDK integration |
| Signing | EV code signing certificate (we have one) |
| Testing | WHQL test machine or Windows VM with test signing |
| Attestation | Microsoft Hardware Dev Center account for attestation signing |

### Signing Options

1. **Test Signing** — Enable test mode on dev machines, no cert needed
2. **Attestation Signing** — Submit driver to Microsoft's portal, signed by Microsoft (free with Hardware Dev Center account + EV cert)
3. **WHQL** — Full certification, expensive ($250+), guarantees compatibility

Since we already have an EV certificate, **attestation signing** is the pragmatic path. It's free and Microsoft signs the driver for us.

---

## Key Questions to Resolve

1. **Do games actually need more than 8 axes on a single device?** Or is multi-device vJoy sufficient?
2. **What's the minimum viable axis count?** 16 axes (8 joysticks) covers most cases.
3. **Is XInput compatibility essential?** Many accessibility-focused games use DirectInput.
4. **Should VDroid be a standalone project or embedded in Nimbus?** Standalone would benefit the wider accessibility community.
5. **Can we partner with vJoy maintainers** to upstream extended axis support instead?

---

## Resources

- [Microsoft vhidmini2 sample](https://github.com/microsoft/Windows-driver-samples/tree/main/hid/vhidmini2) — UMDF virtual HID minidriver
- [ViGEmBus source](https://github.com/ViGEm/ViGEmBus) — Kernel-mode virtual gamepad bus driver
- [HID Usage Tables (USB-IF)](https://usb.org/sites/default/files/hut1_4.pdf) — Definitive list of HID axis/button usages
- [vJoy source](https://github.com/njz3/vJoy) — Community fork of vJoy driver
- [Windows Driver Kit docs](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/) — HID driver development guide
- [Attestation signing](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/attestation-signing-a-kernel-driver-for-public-release) — How to get Microsoft to sign your driver

---

## TL;DR

**Short-term**: Use multiple vJoy devices (16 devices × 8 axes = 128 axes). Zero development needed.

**Medium-term**: Build a UMDF virtual HID driver using Microsoft's `vhidmini2` sample. Define 32+ axes and 256+ buttons per device. Sign via attestation with our EV cert.

**Long-term**: Consider open-sourcing VDroid as a standalone project so the entire accessibility and sim community benefits from a modern, extensible virtual controller driver.
