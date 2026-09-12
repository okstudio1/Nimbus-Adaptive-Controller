# Nimbus as a Modular Control Surface

## The Idea

Project Nimbus was built for gaming, but its core is something more general: a **fully customizable, touch-driven control surface that emits arbitrary input signals to any application on Windows**. The same joystick that controls a game character can control a video editing timeline. The same button grid that maps to controller buttons can map to keyboard shortcuts in Photoshop, DaVinci Resolve, or Blender. The same sensitivity curves designed for flight sims can drive a drawing tablet's pressure response.

> *"A controller is just a surface that maps human intent to application input. Nimbus already does that — the application doesn't have to be a game."*

This reframe expands the addressable market from "disabled gamers" to "anyone who benefits from a custom input surface" — while keeping the accessibility mission at the center.

---

## What Already Works Today

Because Nimbus outputs via **vJoy** (virtual joystick) and **ViGEm** (Xbox 360 emulation), and because Windows allows any application to read joystick/gamepad input, Nimbus already works as a control surface for:

| Application | How | What You'd Control |
|-------------|-----|-------------------|
| **DaVinci Resolve** | Joystick → shuttle/jog via MIDI/controller mapping | Timeline scrubbing, color grading |
| **Blender** | Controller input via Blender's gamepad support | 3D viewport navigation, sculpting |
| **OBS Studio** | vJoy → scene switching, recording start/stop | Streaming control surface |
| **VLC / media players** | Controller buttons → playback controls | Accessible media control |
| **Web browsers** | ViGEm → keyboard emulation | Navigation for motor-impaired users |
| **Any game** | vJoy/ViGEm → standard controller | Original use case |

The limitation today is that Nimbus's UI is **framed around gaming** — joystick widgets, button grids, sensitivity curves. A video editor doesn't think in those terms. The opportunity is to make the same underlying capability accessible to non-gaming use cases through **application-specific profiles and UI modes**.

---

## Use Case Breakdown

### Video Editing

Professional video editors use dedicated hardware control surfaces (Loupedeck, Tangent, DaVinci Resolve Mini Panel — $300–$2,000). These are physical devices with knobs, sliders, and buttons. Nimbus could replace or supplement them for users who:
- Can't afford dedicated hardware
- Have motor disabilities that make physical knobs/sliders difficult
- Want a touchscreen-based surface that adapts to their needs

**What a "Video Editing" Nimbus profile would look like:**
```
Left zone:   Horizontal slider → timeline scrub (jog)
             Vertical slider → zoom in/out
Center zone: Button grid → cut, trim, ripple delete, add marker
Right zone:  Joystick → color wheel (hue/saturation)
             Buttons → lift/gamma/gain selection
```

**Relevant applications:** DaVinci Resolve, Premiere Pro, Final Cut Pro (via Sidecar on Mac), CapCut

### Digital Art / Drawing

Drawing tablets (Wacom, XP-Pen) have express keys and touch rings for shortcuts. Nimbus could serve as an **express key surface** for artists who:
- Use a tablet but want more shortcut buttons than the hardware provides
- Have motor disabilities affecting their non-dominant hand (which normally operates shortcuts)
- Want a larger, more customizable shortcut surface

**What a "Drawing" Nimbus profile would look like:**
```
Left zone:   Buttons → undo, redo, brush size up/down, color picker
             Slider → brush opacity
Center zone: Buttons → layer operations, tool switching
Right zone:  Joystick → canvas rotation/zoom
```

**Relevant applications:** Photoshop, Clip Studio Paint, Procreate (iPad), Krita, Affinity Designer

### Music Production / DAW Control

DAW (Digital Audio Workstation) control surfaces are expensive ($100–$500+). Nimbus could serve as a lightweight DAW controller:

```
Left zone:   Sliders → channel faders
Center zone: Buttons → transport (play, stop, record, loop)
Right zone:  Joystick → pan control
             Buttons → mute/solo per track
```

**Relevant applications:** Ableton Live, FL Studio, Logic Pro, Reaper, GarageBand

### Accessibility Beyond Gaming

For users with motor disabilities, Nimbus as a general control surface means:
- **One device** for gaming, communication (AAC), and productivity
- **Consistent input method** — learn one interface, use it everywhere
- **Adaptive shortcuts** — any application's keyboard shortcuts mapped to large, easy-to-hit touch targets

This is the "universal remote control" vision: Nimbus as the single adaptive interface layer between the user and all their software.

### Streaming / Content Creation

Streamers use Stream Deck ($150–$250) for scene switching, alerts, and overlays. Nimbus could serve the same function for streamers who:
- Can't afford Stream Deck hardware
- Have motor disabilities making physical button presses difficult
- Want a larger, more customizable surface

**What a "Streaming" Nimbus profile would look like:**
```
Top row:    Scene 1, Scene 2, Scene 3, Scene 4, Scene 5
Middle row: Start recording, Stop recording, Mute mic, Camera toggle
Bottom row: Alert 1, Alert 2, Clip, Screenshot, Go live
```

**Relevant applications:** OBS Studio, Streamlabs, XSplit

---

## What Needs to Change

The core engine already works. What's needed is **framing and UI** for non-gaming use cases:

### 1. Keyboard/Mouse Output Mode

Currently Nimbus outputs vJoy/ViGEm (controller signals). For productivity apps, **keyboard shortcut output** is more useful. This means:
- Any button can be mapped to a key combination (Ctrl+Z, Shift+F5, etc.)
- Any slider can be mapped to a scroll wheel or arrow key repeat
- Any joystick can be mapped to mouse movement

This is partially achievable today via ViGEm + Windows key mapping, but a native keyboard output mode would be cleaner and more accessible to non-technical users.

### 2. Application-Specific Profile Templates

Ship pre-built profiles for common non-gaming applications:
- `DaVinci Resolve - Editing.json`
- `Photoshop - Drawing.json`
- `OBS - Streaming.json`
- `Ableton - Performance.json`

These lower the barrier to entry dramatically. A video editor shouldn't need to know what vJoy is.

### 3. Profile Auto-Switch by Active Window

Already planned in the Pro tier (see [BUSINESS_MODEL.md](../distribution/BUSINESS_MODEL.md)). When DaVinci Resolve is in focus, load the editing profile. When a game launches, load the gaming profile. This makes Nimbus feel like a native part of the workflow rather than a separate tool to configure.

### 4. Non-Gaming UI Mode

The current UI is visually oriented around gaming (joystick widgets, controller button labels). A "productivity mode" UI would:
- Use neutral labels ("Shortcut 1" instead of "Button A")
- Show keyboard shortcut assignments instead of controller button names
- Offer a simpler layout builder optimized for button grids rather than joystick + buttons

---

## Market Positioning

### Existing Products in This Space

| Product | Price | Limitations |
|---------|-------|-------------|
| **Elgato Stream Deck** | $150–$250 | Physical hardware; not adaptive; fixed button count |
| **Loupedeck CT** | $549 | Physical hardware; expensive; not accessible |
| **Razer Tartarus** | $80 | Physical hardware; gaming-focused |
| **Companion (Bitfocus)** | Free (software) | Complex; requires Raspberry Pi or dedicated machine |
| **Touch Portal** | Free / $14 one-time | Tablet app; not accessibility-focused; limited customization |
| **Keyboard Maestro** | $36 one-time | Mac only; not touch-native |

**Nimbus's position:** The only **accessibility-first, touch-native, fully customizable** control surface that works across gaming, productivity, and AAC — and is free for basic use.

### The "One Device" Pitch

For a user with a disability, the most compelling pitch is:

> *"One app. One interface you've learned. Works in your game, your video editor, your communication app, and your streaming setup. Adapts to your abilities, not the other way around."*

This is a fundamentally different value proposition than "gaming controller software."

---

## Phased Expansion

### Phase 1: Profile Templates (Low Effort, High Value)
- Create 5–10 pre-built profiles for common non-gaming apps
- Add them to the profile library in the app
- Blog post / community post announcing non-gaming use cases
- No code changes required

### Phase 2: Keyboard Output Mode
- Add native keyboard shortcut output to button widgets
- Allows direct use with any application without vJoy knowledge
- Required for most productivity use cases

### Phase 3: Application Auto-Switch
- Detect active window, load matching profile automatically
- Pro tier feature (see [BUSINESS_MODEL.md](../distribution/BUSINESS_MODEL.md))

### Phase 4: Productivity UI Mode
- Non-gaming visual language in the layout builder
- Simplified onboarding for non-gaming users
- Separate "Gaming" / "Productivity" / "AAC" mode selector

### Phase 5: Community Profile Library
- Users share profiles for specific applications
- Curated library of community-built profiles
- Potential AI tier feature: describe what you want, AI generates the profile

---

## Connection to Other Vision Documents

The three vision documents are related:

```
Modular Control Surface (the platform)
    ├── Gaming use case → original Nimbus
    ├── AAC use case → see AAC_INTEGRATION.md
    └── Research platform → see RESEARCH_PLATFORM.md
              ↓
    All three generate behavioral data
    All three serve the disability community
    All three strengthen the funding story
```

The **research platform** becomes more powerful when Nimbus is used across gaming, AAC, and productivity — because the data covers a broader picture of how disabled users interact with technology, not just games.

The **AAC integration** is the most medically significant use case and opens the most funding doors.

The **modular control surface** framing is the most commercially broad and makes Nimbus relevant to a much larger market.

---

## Funding and Partnership Angle

The control surface framing opens new partnership conversations:

| Partner | Why They'd Care |
|---------|----------------|
| **Adobe** | Accessibility in creative tools; Nimbus as an accessible Photoshop controller |
| **Blackmagic Design** (DaVinci Resolve) | Accessibility in professional video; potential integration |
| **Elgato / Corsair** | Nimbus as a software complement to Stream Deck hardware |
| **Wacom** | Accessible express key surface for tablet users |
| **Microsoft** | Windows accessibility; Surface + Nimbus integration story |
| **YouTube / Twitch** | Accessible streaming tools; creator accessibility |

---

## Related Documents

- [AAC Integration](AAC_INTEGRATION.md) — the communication application of this platform
- [Research Platform](RESEARCH_PLATFORM.md) — data collection across all use cases
- [Voice Command Integration](../distribution/VOICE_COMMAND.md) — voice as a cross-application input method
- [Spectator+ v0 (Game Test Harness, section 4.7)](GAME_TEST_HARNESS.md): scripted primitives through the bridge; the same runner could execute non-gaming sequences
- [Business Model](../distribution/BUSINESS_MODEL.md) — how the expanded use case affects revenue
