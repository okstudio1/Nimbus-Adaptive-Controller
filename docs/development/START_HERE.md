# Project Nimbus - Quick Start Guide

## 🚀 How to Run the Application

### Option 1: Double-click to run (Recommended)
- **Windows**: Double-click `run.bat`
- **Cross-platform**: Run `run.py`

### Option 2: Command line
```bash
python run.py
```

## 📁 Project Structure

```
Project-Nimbus/
├── 🎯 run.py          # Main launcher script (START HERE)
├── 🎯 run.bat         # Windows batch launcher (START HERE)
├── 📋 requirements.txt # Python dependencies
├── ⚙️ controller_config.json # Configuration file
├── 📖 README.md       # Detailed documentation
├── 🖼️ logo.png        # Project logo
├── 📂 src/            # Source code
│   ├── main.py        # Main application
│   ├── config.py      # Configuration management
│   ├── virtual_joystick.py # Joystick controls
│   ├── vjoy_interface.py   # VJoy driver interface
│   ├── axis_config_dialog.py # Axis configuration
│   └── joystick_settings_dialog.py # Settings dialog
└── 📂 tests/          # Test files
    ├── run_fast_tests.py        # the fast, hardware-free suite (what CI runs)
    ├── test_*.py                # fast tests, run as modules: python -m tests.test_bridge_services
    ├── frame_motion.py          # frame motion measurement for the game harness
    ├── game_harness.py          # real-game harness (docs/vision/GAME_TEST_HARNESS.md)
    ├── games/                   # one recipe per game
    ├── probe_*_windows.py       # hardware probes a person runs
    ├── simple_vjoy_test.py      # vJoy driver diagnostics
    └── test_vjoy.py
```

## 🎮 Features
- Dual virtual joysticks with lock/unlock functionality
- Throttle and rudder controls
- Resizable interface (50% - 200% scaling)
- Compact 16:9 aspect ratio layout
- VJoy integration for game compatibility

## 🔧 Requirements
- Python 3.8+
- VJoy driver installed and configured
- Dependencies auto-installed via run.py

---
**Quick Start**: Just run `run.py` or `run.bat` - everything else is handled automatically!
