# Nimbus Adaptive Controller — Next Steps

> **Current state**: v1.5.0 — renamed to Nimbus Adaptive Controller; UI refinements, profile system, smart layout  
> **Last updated**: March 29, 2026

---

## Immediate (Release v1.5.0)

### 1. Build the v1.5.0 Executable

```powershell
# From project root
.venv\Scripts\pyinstaller.exe build_tools\Project-Nimbus.spec --noconfirm
```

**Output**: `dist\Project-Nimbus-1.5.0.exe`

### 2. Test v1.5.0 Changes

**Critical test cases**:
- Switch profiles via File → Profile → (any profile): canvas must reload, not stay blank
- Create a new profile (File → New Profile...): confirm blank canvas, no inherited widgets
- Recent Profiles: switch to 3 different profiles, close app, reopen — verify recent list persists
- Add 4+ widgets: each new widget must land in a free area, not on top of the previous
- Click "Save Layout" while on `adaptive_platform_2` (bundled): must redirect to Save As dialog
- Click output mode label in bottom ribbon: popup menu must appear to switch ViGEm ↔ vJoy

### 3. Build Installer & Sign

```powershell
powershell -ExecutionPolicy Bypass -File build_tools\fetch_redist.ps1   # bundled vJoy + ViGEmBus setups
& "C:\Program Files (x86)\NSIS\makensis.exe" build_tools\installer.nsi
cmd /c build_tools\sign_exe.bat
```

### 4. Tag and Release

```powershell
git tag v1.5.0
git push origin v1.5.0
```

Upload to GitHub Releases:
- `Project-Nimbus-1.5.0.exe` (signed)
- `Project-Nimbus-Setup-1.5.0.exe` (signed)
- Release notes from `CHANGELOG.md` [1.5.0] section

---

## Near-Term (1-2 Sessions)

### 7. Expand the Game Compatibility Database

**Current state**: 30 games in `src/borderless.py`'s `GAME_COMPATIBILITY` list

**Action items**:
- Every game you or a tester verifies should be added to the list
- Update `docs/GAME_COMPATIBILITY.md` to match
- The in-app Compatibility tab surfaces this automatically

**How to add a game**:

1. Edit `src/borderless.py` → add a new `GameCompatEntry` to the list:
```python
GameCompatEntry(
    name="Game Name",
    status="verified",  # or "likely", "partial", "incompatible"
    input_method="clipcursor",  # or "rawinput", "exclusive_fullscreen"
    notes="Works perfectly with borderless + cursor release.",
    window_title_hint="Game Name",  # substring to match window title
    needs_borderless=True,
    needs_cursor_release=True,
    recommended_interval_ms=50
),
```

2. Update `docs/GAME_COMPATIBILITY.md` → add to the appropriate table (Verified/Likely/Partial/Incompatible)

3. Commit: `git commit -m "feat: add [Game Name] to compatibility database"`

**Priority games to test**:
- Popular indie games (Hollow Knight, Dead Cells, Hades)
- Strategy games (Civilization VI, Age of Empires)
- Simulation games (Cities: Skylines, Planet Coaster)
- Any game with known accessibility barriers

### 8. Test with Existing Users' Profiles

**Scenario**: Users who had `flight_simulator`, `xbox_controller`, or `adaptive_platform_1` in their `%APPDATA%\ProjectNimbus\profiles\` will still have those files.

**Expected behavior**:
- Those profiles still appear in the **File → Profile** menu
- They still load correctly (backward compatible)
- New installs only get `adaptive_platform_2`

**Test**:
1. Manually create `%APPDATA%\ProjectNimbus\profiles\flight_simulator.json` with old content
2. Launch Nimbus
3. Verify it appears in the profile menu
4. Switch to it — verify it loads the FlightSimLayout
5. Switch back to Adaptive Platform 2

**Why this matters**: Ensures we didn't break anyone's existing setup.

---

## Longer-Term (From the Roadmap)

### 9. Voice Command Integration

**Status**: Architecture documented in `docs/distribution/VOICE_COMMAND.md`

**What it is**: Speak to trigger buttons, axes, and macros. Offline engines (Faster-Whisper, Vosk) for low latency and privacy, or cloud engines (DeepGram Nova-3) for higher accuracy.

**Next steps**:
- Port the existing DeepGram streaming implementation from the companion project to Python/PySide6
- Add a **Voice** tab to the settings menu
- Implement interim result handling for time-critical commands (e.g., "fire" triggers immediately on interim, confirms on final)
- Add voice-triggered macro support to the macro joystick system

**Priority**: High for accessibility users who cannot use physical input

### 10. Keyboard Output Mode

**Status**: Documented in `docs/vision/KEYBOARD_OUTPUT.md`

**What it is**: Any Nimbus button or slider can emit native keyboard shortcuts to any application — Photoshop, DaVinci Resolve, OBS, a browser — with no external software.

**Implementation**:
- Use Windows `SendInput` API (same `user32.dll` already used by Nimbus)
- Fully bundled in the installer, no driver install required
- Per-widget config: "Output Type" dropdown → "vJoy Button" or "Keyboard Shortcut"
- Shortcut editor: Ctrl+Shift+K, etc.

**Use cases**:
- **Stream Deck replacement** — buttons trigger OBS scene switches, mute/unmute
- **Drawing tablet express keys** — buttons trigger Photoshop brush size, undo, layer shortcuts
- **DAW controller** — sliders control volume, buttons trigger play/pause/record
- **Video editing** — buttons trigger DaVinci Resolve cut, ripple delete, color grade shortcuts

**Priority**: Medium-high — expands Nimbus beyond gaming into productivity

### 11. Physical Hardware Integration

**Status**: Documented in `docs/vision/HARDWARE_INTEGRATION.md`

**What it is**: Nimbus can wrap existing adaptive hardware — **Xbox Adaptive Controller**, **QuadStick**, foot pedals, head trackers — reading their input via XInput/DirectInput and re-emitting through vJoy with Nimbus's sensitivity curves, macros, and voice layer applied on top.

**Implementation**:
- Detect XInput devices (XAC appears as a standard XInput device)
- Add "Input Source" dropdown in settings: "Mouse" or "XInput Device 1" or "DirectInput Device"
- Map XAC buttons/sticks → Nimbus widgets → vJoy output with sensitivity curves applied
- No special SDK needed — XAC is just XInput

**Use case**: User has an XAC but wants Nimbus's macro system, voice commands, and sensitivity curves on top of it.

**Priority**: Medium — requires hardware for testing

### 12. AAC — Augmentative & Alternative Communication

**Status**: Documented in `docs/vision/AAC_INTEGRATION.md`

**What it is**: The same customizable button surface that controls a game can output spoken phrases via text-to-speech, navigate AAC vocabulary pages, or trigger keyboard shortcuts in dedicated AAC software.

**Implementation**:
- Add "AAC Mode" toggle in widget config
- AAC button config: phrase text, TTS voice, volume
- Switch access (single/dual switch scanning) support
- Eye gaze integration (Windows Eye Control already works with Nimbus today)

**Use case**: A gaming + AAC hybrid on one screen — something no commercial product currently offers.

**Priority**: High for non-verbal users

### 13. Research Platform

**Status**: Documented in `docs/vision/RESEARCH_PLATFORM.md`

**What it is**: Every Nimbus session generates behavioral data — axis configurations, input frequency, profile choices, fatigue patterns — that the disability gaming research community doesn't have at scale.

**Implementation**:
- Opt-in, IRB-appropriate telemetry
- Anonymized session logs: widget usage, sensitivity settings, game compatibility results
- Partnership with AbleGamers, Shirley Ryan AbilityLab, CMU HCII, etc.

**Use case**: Understand how people with disabilities actually play, at scale, to inform future assistive tech design.

**Priority**: Low — requires institutional partnerships and IRB approval

### 14. Modular Control Surface

**Status**: Documented in `docs/vision/MODULAR_CONTROL_SURFACE.md`

**What it is**: Beyond gaming — Nimbus as a universal adaptive input layer for video editing, music production, streaming, and any Windows application.

**Implementation**:
- Pre-built profiles for DaVinci Resolve, Photoshop, Ableton, OBS
- Auto-switch profiles by active window (detect foreground app, load matching profile)
- Keyboard output mode (see #10) is the foundation for this

**Use case**: One device, one learned interface, works everywhere.

**Priority**: Medium — depends on keyboard output mode (#10)

---

## Sustainability, Distribution & Growth

**Core principle**: Nimbus Adaptive Controller is and will remain **free for all accessibility use**.

**Full proposal**: [`docs/vision/TELEMETRY_AND_ACCOUNTS.md`](vision/TELEMETRY_AND_ACCOUNTS.md)

---

### Phase 1 — Crash Reporting (1 session)

**Goal**: Visibility into production bugs without waiting for user reports.

1. Add `sentry-sdk` to `requirements.txt`
2. Add opt-in checkbox to Settings → Privacy: *"Send crash reports"*
3. Initialize Sentry in `src/qt_qml_app.py` conditioned on opt-in flag
4. Write `_scrub_event()` to strip any file paths / OS usernames before send
5. Store opt-in state in `controller_config.json` under `telemetry.crash_reports`

**Delivers**: Immediate production visibility. No accounts required.

---

### Phase 2 — Anonymous Usage Analytics (1–2 sessions)

**Goal**: Understand which features, output modes, and games users actually use.

1. Implement `src/telemetry.py` — local event buffer, 5-minute batch flush to API
2. Events to track: `session_start`, `profile_switch`, `widget_added`, `game_mode_start`
3. Game identification: `sha256(window_title)` — cross-reference with known game list to decode
4. Add opt-in checkbox alongside crash reporting in Settings → Privacy
5. Host ingestion: Supabase Edge Function or Plausible (self-hosted)

**Key constraint**: No plaintext window titles, no axis values, no PII — hashes only.

**Delivers**: Data to prioritize game compatibility, feature work, and research partnerships.

---

### Phase 3 — User Accounts (3–5 sessions)

**Goal**: Identity layer enabling cloud sync, premium licensing, and community features.

**Backend**: Supabase (auth + Postgres + storage, open-source, can self-host)

1. Set up Supabase project: `auth.users`, `profiles` table, `sessions` table
2. Implement `src/cloud_client.py` — login/logout, token refresh, profile upsert/pull
3. Register `nimbus://` custom URL scheme in NSIS installer for OAuth callback
4. Add **File → Account → Sign In / Sign Out** to main menu
5. Profile sync for Nimbus+ users: push on save, pull on startup, last-write-wins merge
6. Add account status (avatar / email) to status ribbon
7. Store OAuth token in OS credential vault via `keyring` (no plaintext tokens on disk)

**Tiers**:
- **Free**: All core features, local profiles only
- **Nimbus+** ($5–8/month): Cloud profile sync, voice commands, AI copilot, advanced macros
- **Institutional**: Bulk licenses for hospitals/VA/rehab centers
- **Research**: Free (application) — opt-in session logging for IRB studies

---

### Phase 4 — Distribution Expansion (parallel with Phase 3)

**Goal**: Reach users beyond GitHub; reduce SmartScreen friction.

1. **Auto-update** — startup check against `https://nimbus.app/version.json`; non-intrusive ribbon if update available
2. **winget** — submit to `microsoft/winget-pkgs` (`winget install ProjectNimbus.Nimbus`)
3. **Microsoft Store (MSIX)** — Store auto-updates, no SmartScreen, no admin required; blocker is ViGEmBus driver (prompt `winget install nefarius.vigembus` at first run)
4. **nimbus.app website** — landing page, download button, account portal, public profile gallery

**Update channels**: `stable` (all users), `beta` (opt-in), `dev` (contributors)

---

### Phase 5 — Premium Billing (2–3 sessions, after accounts)

1. Stripe integration for Nimbus+ subscriptions
2. JWT entitlement claims checked in `cloud_client.py`
3. Feature gates in QML: voice/AI/macros show upsell if not Nimbus+
4. Institutional license flow (email-based initially)
5. Apply for grants: NIH SBIR, NSF, AbleGamers Foundation, Microsoft Accessibility

---

### Business Model Summary

| Revenue Stream | Timeline |
|---|---|
| Nimbus+ subscriptions ($5–8/mo) | After Phase 4 (accounts) |
| Institutional licenses | Outreach alongside accounts |
| Corporate sponsorships (Microsoft, Logitech) | Ongoing |
| Research grants (NIH SBIR, NSF) | Apply now — 6–12 month lag |

---

## Testing Checklist (Before Each Release)

**Core layout**
- [ ] Build exe with PyInstaller
- [ ] Switch profiles: canvas reloads with correct widgets (not blank)
- [ ] Create new profile: blank canvas, no inherited widgets
- [ ] Recent Profiles persists across app restart
- [ ] Add 4+ widgets: each lands in a free spot, no overlap
- [ ] Save Layout on bundled profile → redirected to Save As dialog
- [ ] Status ribbon: click output mode label → popup menu appears
- [ ] Widget drag/resize/delete in edit mode
- [ ] Joystick triple-click lock + unlock
- [ ] Macro joystick with all action types

**Output modes**
- [ ] ViGEm palette shows Xbox-specific widgets; vJoy shows generic widgets
- [ ] Xbox button labels are read-only in ViGEm config dialog
- [ ] sl0/sl1 axes hidden in ViGEm axis dropdowns
- [ ] Test vJoy connection status indicator
- [ ] Test ViGEm connection (if available)

**Game integration**
- [ ] Test borderless gaming with 3+ games
- [ ] Test Game Focus Mode (Windows only)
- [ ] Test controller mode enforcement (ViGEm keep-alive)

**Build & release**
- [ ] Build installer with NSIS
- [ ] Sign exe + installer with EV cert
- [ ] Verify UIAccess works (install in trusted location)
- [ ] Test uninstall (verify profiles preserved in %APPDATA%)
- [ ] Tag release, upload to GitHub Releases

---

## Documentation Maintenance

**Keep these in sync**:
- `README.md` — user-facing overview, features, installation
- `CHANGELOG.md` — version history with semantic versioning
- `docs/GAME_COMPATIBILITY.md` — game list (matches `src/borderless.py`)
- `docs/architecture/architecture.md` — technical architecture
- `docs/development/LLM_NOTES.md` — AI assistant reference
- `DIRECTORY.md` — project structure guide

**When adding a new feature**:
1. Update `README.md` features section
2. Add to `CHANGELOG.md` under `[Unreleased]` or next version
3. Update `docs/architecture/architecture.md` if it changes the architecture
4. Update `docs/development/LLM_NOTES.md` if it adds new APIs or conventions
5. Update `DIRECTORY.md` if it adds new files or directories

---

## Contact & Support

- **GitHub Issues**: Bug reports, feature requests
- **Email**: [your email]
- **Discord**: [if you have a community server]
- **AbleGamers**: Partnership for accessibility testing and outreach

---

**Remember**: Every feature should make the app more accessible, not less. If a feature adds complexity, add a tutorial or simplify the UI. The goal is a **software Xbox Adaptive Controller** that anyone can use, regardless of physical ability.
