# Hardware Integration — Wrapping Physical Adaptive Controllers

## The Idea

Project Nimbus currently generates virtual controller output (vJoy/ViGEm) from its own touch UI. But many users already own physical adaptive hardware — an **Xbox Adaptive Controller**, a **QuadStick**, a sip-and-puff device, a head tracker, a foot pedal, or any other HID-compliant input device. Rather than replacing that hardware, Nimbus can **wrap it**: read the physical device's input, apply transformations (remapping, sensitivity curves, macros, voice augmentation), and re-emit the result through vJoy/ViGEm.

This makes Nimbus an **input processing layer** that sits between any physical adaptive hardware and any game or application — extending, combining, and enhancing what the hardware can do.

```
Physical hardware (XAC, QuadStick, foot pedal, head tracker)
    ↓  [read via XInput / DirectInput / HID]
Nimbus input processor
    ↓  [remap, curve, macro, voice augment, AI assist]
vJoy / ViGEm virtual controller
    ↓
Game or application
```

---

## How Physical Controllers Appear on Windows

Understanding how each device type presents itself to Windows is the foundation for reading them in Python.

### Xbox Adaptive Controller (XAC)

The XAC presents as an **XInput device** on Windows — identical to any Xbox 360 or Xbox One controller from the OS's perspective. This means:

- Windows sees it as `XINPUT_GAMEPAD` with standard axes and buttons
- All 3.5mm jack inputs (external switches, joysticks) are mapped to standard XInput buttons/axes by the XAC itself
- The XAC's USB-C PC connection exposes the full XInput interface
- **Readable in Python via:** `xinput` (ctypes), `pygame.joystick`, `inputs` library, `pyxinput`

**Key implication:** Any Python code that reads an Xbox controller reads the XAC. No special driver or SDK needed.

### QuadStick

The QuadStick (mouth-operated joystick with sip/puff inputs) presents as a **DirectInput / HID joystick** on Windows:

- Exposes as a standard USB HID gamepad
- Axes: X/Y joystick (mouth movement) + additional axes for sip/puff pressure
- Buttons: mapped to sip/puff combinations
- **Readable in Python via:** `pygame.joystick`, `inputs` library, `pysdl2`, raw HID via `hidapi`

### Other Common Adaptive Hardware

| Device | Windows Interface | Python Library |
|--------|------------------|----------------|
| **Xbox Adaptive Controller** | XInput | `inputs`, `pygame`, `xinput` ctypes |
| **QuadStick** | DirectInput / HID | `inputs`, `pygame.joystick` |
| **Logitech Adaptive Gaming Kit** | DirectInput / HID | `inputs`, `pygame.joystick` |
| **Tobii Eye Tracker** | Tobii SDK / mouse emulation | Tobii Python SDK or mouse API |
| **Head trackers (TrackIR, NaturalPoint)** | DirectInput / HID | `inputs`, `pygame.joystick` |
| **Foot pedals (Olympus, Kinesis)** | HID keyboard or joystick | `inputs`, `keyboard` library |
| **Sip-and-puff (Jouse, Integra Mouse)** | HID mouse or joystick | `inputs`, `mouse` library |
| **Switch interfaces (AbleNet, Enabling Devices)** | HID keyboard | `keyboard`, `inputs` |

---

## Reading Physical Input in Python

### XInput (Xbox Adaptive Controller)

```python
# Read XAC / any Xbox controller via XInput
# Uses the `inputs` library: pip install inputs

from inputs import get_gamepad

def read_xac():
    while True:
        events = get_gamepad()
        for event in events:
            # event.code: 'ABS_X', 'ABS_Y', 'BTN_SOUTH', etc.
            # event.state: axis value (-32768 to 32767) or button (0/1)
            print(f"{event.code}: {event.state}")
```

### DirectInput / HID (QuadStick, generic adaptive joysticks)

```python
# Read any DirectInput joystick via pygame
import pygame

pygame.init()
pygame.joystick.init()

joystick = pygame.joystick.Joystick(0)  # First joystick
joystick.init()

while True:
    pygame.event.pump()
    x = joystick.get_axis(0)   # -1.0 to 1.0
    y = joystick.get_axis(1)
    btn = joystick.get_button(0)
    print(f"X:{x:.3f} Y:{y:.3f} Btn0:{btn}")
```

### Unified Input Reader (Both XInput and DirectInput)

```python
# inputs library handles both XInput and DirectInput transparently
from inputs import devices

for device in devices.gamepads:
    print(f"Found: {device.name}")
    # XAC will appear as "Microsoft X-Box One pad"
    # QuadStick will appear as "QuadStick Game Controller"
```

---

## The Input Pipeline Architecture

### Proposed Module: `src/hardware/`

```
src/
└── hardware/
    ├── device_scanner.py      # Enumerate connected physical devices
    ├── xinput_reader.py       # Read XInput devices (XAC, Xbox controllers)
    ├── hid_reader.py          # Read DirectInput/HID devices (QuadStick, etc.)
    ├── input_merger.py        # Combine multiple physical inputs into one stream
    ├── passthrough.py         # Forward physical input → vJoy with optional transforms
    └── transform_pipeline.py  # Apply remapping, curves, macros to physical input
```

### Passthrough Mode

The simplest integration: physical controller input passes through Nimbus to vJoy unchanged. This is useful when:
- The game doesn't natively support the physical device's input format
- The user wants to add Nimbus overlays (voice commands, Spectator+) on top of their physical hardware
- The user wants to record their physical input for the research telemetry platform

```python
# src/hardware/passthrough.py (sketch)
from .xinput_reader import XInputReader
from ..vjoy_interface import VJoyInterface

class HardwarePassthrough:
    def __init__(self, source_device, vjoy: VJoyInterface):
        self.source = source_device
        self.vjoy = vjoy

    def tick(self):
        state = self.source.read()
        # Direct passthrough — no transformation
        self.vjoy.set_axis(1, state.left_x)
        self.vjoy.set_axis(2, state.left_y)
        self.vjoy.set_axis(3, state.right_x)
        self.vjoy.set_axis(4, state.right_y)
        for i, btn in enumerate(state.buttons):
            self.vjoy.set_button(i + 1, btn)
```

### Transform Mode

The more powerful integration: physical input is intercepted, transformed, and re-emitted. Transformations available through the existing Nimbus profile system:

| Transform | What It Does | Use Case |
|-----------|-------------|----------|
| **Axis remapping** | Map physical axis N → vJoy axis M | QuadStick sip/puff → trigger axis |
| **Sensitivity curve** | Apply existing Nimbus curves to physical input | Reduce tremor, expand deadzone |
| **Button remapping** | Map physical button N → vJoy button M | Remap XAC 3.5mm jack inputs |
| **Macro trigger** | Physical button → multi-step macro sequence | QuadStick sip → combo move |
| **Axis inversion** | Flip axis direction | Correct inverted joystick |
| **Axis combination** | Two physical axes → one output axis | Combine sip/puff into single axis |
| **Threshold gate** | Only pass input above threshold | Filter tremor/noise |

---

## Xbox Adaptive Controller: Specific Integration

### What the XAC Brings

The XAC is designed as a **hub** — it accepts external switches and joysticks via 3.5mm jacks and USB-A ports, then presents them all as a single XInput gamepad to the PC. This means:

- A user might have a foot pedal on jack 1, a head switch on jack 2, and a sip-and-puff on USB
- The XAC maps all of these to standard Xbox buttons/axes
- Nimbus reads the resulting XInput stream and can further transform it

### Extending the XAC with Nimbus

The XAC has limitations that Nimbus can address:

| XAC Limitation | Nimbus Solution |
|---------------|----------------|
| Fixed button mapping (no software curves) | Apply sensitivity curves to XAC axis output |
| No macro support | Trigger macros from XAC button presses |
| No voice augmentation | Add voice commands on top of XAC input |
| No AI assistance | Layer Spectator+ over XAC input |
| No telemetry | Capture XAC session data for research platform |
| Limited to Xbox/PC | Re-emit via ViGEm for broader compatibility |
| No per-game profiles | Auto-switch Nimbus profiles based on active game |

### XAC + Nimbus UI Concept

When an XAC is detected, Nimbus could show a **hardware mode panel** alongside (or instead of) the touch UI:

```
┌─────────────────────────────────────────┐
│  Xbox Adaptive Controller  [Connected]  │
│                                         │
│  Left Stick:  [Curve: Accessibility]    │
│  Right Stick: [Curve: Linear]           │
│  Jack 1 (A): → [Macro: Jump + Dodge]   │
│  Jack 2 (B): → [Button: B]             │
│  USB-A:      → [Axis: Left Trigger]    │
│                                         │
│  [+ Add Voice Command Layer]            │
│  [+ Enable Spectator+ Assist]           │
└─────────────────────────────────────────┘
```

---

## QuadStick: Specific Integration

### What the QuadStick Is

The QuadStick is a mouth-operated controller for users with high-level spinal cord injury (C3–C5 and above) or other conditions affecting all four limbs. It provides:
- **Joystick axis:** Mouth/head movement → X/Y axes
- **Sip inputs:** Soft sip, hard sip (two pressure levels)
- **Puff inputs:** Soft puff, hard puff (two pressure levels)
- **Total:** ~4 axes + 4 "buttons" from breath inputs

### QuadStick Limitations Nimbus Can Address

| QuadStick Limitation | Nimbus Solution |
|---------------------|----------------|
| Only 4 breath inputs | Map breath combos → macros (sip+puff = complex action) |
| No sensitivity adjustment | Apply curves to mouth joystick (tremor filtering, deadzone) |
| No voice augmentation | Add voice commands for actions mouth can't reach |
| Fatigue from sustained input | Spectator+ AI handles execution; user directs via breath |
| No telemetry | Research platform captures QuadStick usage patterns |

### QuadStick + Voice: The Compelling Combination

For a QuadStick user, the mouth is already occupied with the joystick. Voice commands become the **secondary input channel** — the user moves with their mouth and speaks commands for actions that would otherwise require additional breath inputs:

```
Mouth joystick → movement (continuous)
Voice "jump"   → jump button (discrete)
Voice "attack" → attack combo (macro)
Breath sip     → dodge (time-critical)
```

This combination — QuadStick + Nimbus voice layer — gives a user with high-level SCI access to a much richer control vocabulary than either device provides alone.

---

## Telemetry Extension for Hardware Users

When a physical device is connected and routed through Nimbus, the research telemetry platform (see [RESEARCH_PLATFORM.md](RESEARCH_PLATFORM.md)) gains additional data:

| Data Point | What It Reveals |
|------------|----------------|
| Physical device type + model | What hardware the community actually uses |
| Raw vs. transformed input delta | How much Nimbus's transforms help |
| Which transforms are applied | What adaptations are most needed |
| Input frequency from physical device | Fatigue patterns specific to device type |
| Voice command usage alongside hardware | How users blend physical + voice input |
| Passthrough vs. transform mode usage | Whether users need remapping or just pass-through |

This is data that hardware manufacturers (Microsoft, QuadStick) don't have — and would find valuable.

---

## Existing Ecosystem Tools (Prior Art)

Nimbus doesn't need to reinvent this wheel entirely. These tools already solve parts of the problem:

| Tool | What It Does | Nimbus Relationship |
|------|-------------|---------------------|
| **UCR (Universal Control Remapper)** | Physical → vJoy remapping, GUI | Nimbus does this + accessibility UI |
| **xboxToVJoy** | XInput → vJoy translation | Nimbus does this + transforms + voice |
| **Joystick Gremlin** | Physical joystick remapping with curves | Nimbus does this + touch UI + AI |
| **reWASD** | Controller remapping | Nimbus does this + accessibility focus |
| **AntiMicroX** | Gamepad → keyboard mapping | Nimbus does this + more |

The key differentiator: all of these are **general-purpose remappers** with no accessibility focus. Nimbus is built for the accessibility use case from the ground up, with the UI, profiles, and features that disabled users actually need.

---

## Detection and Auto-Configuration

When Nimbus starts, it should enumerate connected devices and offer to configure them:

```python
# src/hardware/device_scanner.py (sketch)
from inputs import devices as input_devices
import pygame

KNOWN_ADAPTIVE_DEVICES = {
    "Microsoft X-Box Adaptive Controller": "xac",
    "QuadStick Game Controller": "quadstick",
    "Logitech G Adaptive Gaming Kit": "logitech_agk",
}

def scan_devices():
    found = []
    for device in input_devices.gamepads:
        device_type = KNOWN_ADAPTIVE_DEVICES.get(device.name, "generic_gamepad")
        found.append({"name": device.name, "type": device_type, "device": device})
    return found
```

When a known adaptive device is detected, Nimbus could:
1. Notify the user: *"Xbox Adaptive Controller detected — load hardware profile?"*
2. Offer a pre-built profile optimized for that device
3. Show the hardware configuration panel alongside the touch UI

---

## Implementation Roadmap

### Phase 1: Passthrough (Low Effort)
- Add `inputs` library dependency
- Implement `device_scanner.py` — enumerate connected gamepads
- Implement `passthrough.py` — forward physical input → vJoy unchanged
- UI: "Hardware Input" toggle in settings; shows connected devices
- **Value:** Users can use XAC/QuadStick with Nimbus's vJoy output immediately

### Phase 2: Transform Pipeline
- Implement `transform_pipeline.py` — apply existing Nimbus curves/mappings to physical input
- Expose physical device axes/buttons in the profile editor
- **Value:** Users can apply sensitivity curves and remapping to their physical hardware

### Phase 3: Hardware Profile Templates
- Pre-built profiles for XAC, QuadStick, common adaptive devices
- Auto-detect device and suggest matching profile
- **Value:** Zero-configuration setup for common hardware

### Phase 4: Hardware + Voice Fusion
- Voice commands work alongside physical hardware input simultaneously
- UI shows both hardware state and voice command status
- **Value:** QuadStick + voice combination; XAC + voice for extended vocabulary

### Phase 5: Telemetry Integration
- Hardware session data feeds into research platform (opt-in)
- Device type, transform usage, input patterns
- **Value:** Research data on real adaptive hardware usage

---

## Partnership Opportunities

| Partner | Why They'd Care |
|---------|----------------|
| **Microsoft (XAC team)** | Nimbus extends XAC capability; potential co-marketing |
| **QuadStick** | Nimbus is a free software layer that makes QuadStick more powerful |
| **Logitech (Adaptive Gaming Kit)** | Same — software extension of their hardware |
| **AbleGamers** | They recommend hardware; Nimbus makes that hardware work better |
| **Tobii** | Eye tracker + Nimbus = powerful combination; SDK integration |
| **SpecialEffect** | They set up custom hardware rigs; Nimbus is the software layer |

---

## Related Documents

- [Research Platform](RESEARCH_PLATFORM.md) — hardware telemetry feeds the research dataset
- [AAC Integration](AAC_INTEGRATION.md) — hardware devices used for AAC (switch access, eye gaze)
- [Modular Control Surface](MODULAR_CONTROL_SURFACE.md) — hardware integration extends to non-gaming apps
- [Voice Command Integration](../distribution/VOICE_COMMAND.md) — voice augments physical hardware
- [Spectator+ v0 (Game Test Harness, section 4.7)](GAME_TEST_HARNESS.md): scripted primitives through the bridge, the assist layer that would sit over physical hardware input
