# Project Nimbus Documentation

> **Quick Start**: See [../DIRECTORY.md](../DIRECTORY.md) for project structure overview.

## Setup & Installation
- [Installation Guide](setup/INSTALLATION.md) — installer, vJoy setup, prerequisites, troubleshooting
- [Profile System](setup/PROFILES.md) — profile storage, JSON structure, custom layout widget properties
- [Packaging Guide](setup/PACKAGING.md) — **build & distribute** — PyInstaller, NSIS, code signing, release checklist

## Borderless Gaming
- [Game Compatibility](GAME_COMPATIBILITY.md) — verified/likely/partial/incompatible games, tips by genre, how ClipCursor release works
- [Host Mode & Input Isolation](vision/HOST_MODE_ISOLATION.md) — research on what it would take to support the Raw Input games listed as incompatible

## Architecture
- [Architecture Overview](architecture/architecture.md) — codebase structure, QML/Python bridge, widget system, borderless module
- [V-Droid Driver Brainstorm](architecture/VDROID_DRIVER_BRAINSTORM.md) — custom virtual HID driver plans (future)

## Development
- [LLM Notes](development/LLM_NOTES.md) — **for AI assistants** — implementation details, conventions, known fixes
- [Integration Guide](development/INTEGRATION_GUIDE.md) — how to add new widgets and features
- [Start Here](development/START_HERE.md) — quick orientation for new contributors
- [Widget Ideas](development/WIDGET_IDEAS.md) — planned and brainstormed widget concepts

## Accessibility
- [Accessibility Spotlight Nomination](accessibility/ACCESSIBILITY_SPOTLIGHT_NOMINATION.md)

## Vision & Expansion
- [Research Platform](vision/RESEARCH_PLATFORM.md) — Nimbus as a disability gaming research tool; telemetry, IRB, university partnerships
- [AAC Integration](vision/AAC_INTEGRATION.md) — Augmentative & Alternative Communication; phrase buttons, scanning, TTS output
- [Modular Control Surface](vision/MODULAR_CONTROL_SURFACE.md) — beyond gaming: video editing, drawing, DAW, streaming, any application
- [Hardware Integration](vision/HARDWARE_INTEGRATION.md) — wrapping XAC, QuadStick, and other adaptive hardware through vJoy; input pipeline architecture
- [Keyboard Output](vision/KEYBOARD_OUTPUT.md) — native keystroke/shortcut emission via SendInput; no external dependencies; bundled in installer
- [Host Mode & Input Isolation](vision/HOST_MODE_ISOLATION.md) — solving the Raw Input tier; VMs, cloud gaming, two-PC streaming, Linux/evdev, and a mouse-class filter driver
- [Linux Probe Plan](vision/LINUX_PROBE_PLAN.md) — proposed weekend experiment to test EVIOCGRAB + uinput against a real EAC game

## Distribution & Sustainability
- [Business Model](distribution/BUSINESS_MODEL.md) — freemium tiers, market research, pricing, revenue scenarios
- [Open Core Playbook](distribution/OPEN_CORE_PLAYBOOK.md) — dual-repo strategy, license gating, CLA, real-world precedents
- [Release Strategy](distribution/RELEASE_STRATEGY.md) — open source philosophy, funding models, sponsorship tiers, grant sources
- [Sponsorship Outreach](distribution/SPONSORSHIP_OUTREACH.md) — email templates, pitch deck outline, partnership guidance
- [Voice Command Integration](distribution/VOICE_COMMAND.md) — speech recognition options, latency analysis, architecture
- [Spectator+ AI Agent](distribution/SPECTATOR_PLUS.md) — AI-assisted play concept, hardware requirements, RL architecture

## Media
- [Screenshots](screenshots/) — application UI screenshots
- [Video](video/) — demo videos

---

## Key Files for AI Assistants

| Task | File |
|------|------|
| Understand project structure | [../DIRECTORY.md](../DIRECTORY.md) |
| Make UI changes | `qml/Main.qml`, `qml/layouts/CustomLayout.qml` |
| Add QML↔Python features | `src/bridge.py` |
| Build releases | [setup/PACKAGING.md](setup/PACKAGING.md) |
| Bug fixes / implementation notes | [development/LLM_NOTES.md](development/LLM_NOTES.md) |
