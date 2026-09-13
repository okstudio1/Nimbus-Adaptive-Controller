#!/usr/bin/env python3
"""
Quick launcher script for Nimbus Adaptive Controller.
Handles virtual environment setup, dependency checking and provides user-friendly error messages.
"""

import sys
import subprocess
import os
import venv
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible."""
    if sys.version_info < (3, 8):
        print("ERROR: Python 3.8 or higher is required")
        print(f"Current version: {sys.version}")
        return False
    return True

def setup_virtual_environment():
    """Create and setup virtual environment if it doesn't exist."""
    venv_path = Path("venv")
    
    if not venv_path.exists():
        print("Creating virtual environment...")
        try:
            venv.create(venv_path, with_pip=True)
            print("Virtual environment created successfully!")
        except Exception as e:
            print(f"ERROR: Failed to create virtual environment: {e}")
            return False
    
    return True

def get_venv_python():
    """Get the path to Python executable in virtual environment."""
    if sys.platform == "win32":
        return Path("venv") / "Scripts" / "python.exe"
    else:
        return Path("venv") / "bin" / "python"

def check_dependencies():
    """Check if required packages are installed in virtual environment."""
    venv_python = get_venv_python()
    
    if not venv_python.exists():
        print("ERROR: Virtual environment Python not found")
        return False
    
    if sys.platform == "win32":
        required_packages = ['pyvjoy', 'numpy', 'PySide6']
    else:
        # vJoy/ViGEm are Windows drivers. Linux uses the kernel uinput module
        # through src/uinput_interface.py, which needs no extra package.
        required_packages = ['numpy', 'PySide6']
    
    # Check if packages are installed
    try:
        result = subprocess.run([
            str(venv_python), '-c', 
            '; '.join([f'import {pkg}' for pkg in required_packages])
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print("Installing dependencies in virtual environment...")
            # Upgrade pip first
            subprocess.check_call([str(venv_python), '-m', 'pip', 'install', '--upgrade', 'pip'])
            # Install requirements
            subprocess.check_call([str(venv_python), '-m', 'pip', 'install', '-r', 'requirements.txt'])
            print("Dependencies installed successfully!")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to install dependencies: {e}")
        print("Please check your internet connection and try again")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error during dependency check: {e}")
        return False

def run_in_venv(target_module: str):
    """Run the application in the virtual environment.

    Args:
        target_module: Python module to run with -m (e.g., 'src.qt_qml_app')
    """
    venv_python = get_venv_python()

    try:
        # Run selected module using the virtual environment Python
        result = subprocess.run([str(venv_python), "-m", target_module], cwd=Path.cwd())
        return result.returncode
    except Exception as e:
        print(f"ERROR: Failed to run application: {e}")
        return 1

def main():
    """Main launcher function."""
    print("=" * 50)
    print("Nimbus Adaptive Controller - Virtual Controller Interface")
    print("=" * 50)
    print()
    
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check Python version
    if not check_python_version():
        input("Press Enter to exit...")
        return 1
    
    # Setup virtual environment
    if not setup_virtual_environment():
        input("Press Enter to exit...")
        return 1
    
    # Check dependencies in virtual environment
    if not check_dependencies():
        input("Press Enter to exit...")
        return 1
    
    # Launch the Qt Quick (QML) UI by default
    target_module = "src.qt_qml_app"

    print("Starting application in virtual environment...")
    print("Launching Qt Quick (PySide6 QML): src.qt_qml_app")
    print()

    try:
        return run_in_venv(target_module)
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        return 0
    except Exception as e:
        print(f"\nERROR: {e}")
        if sys.platform.startswith("linux"):
            print("\nIf this is a uinput error, make sure:")
            print("1. /dev/uinput exists (sudo modprobe uinput)")
            print("2. Your user can write to it (see docs/setup/LINUX.md for the udev rule)")
        else:
            print("\nIf this is a VJoy error, make sure:")
            print("1. VJoy driver is installed")
            print("2. VJoy device #1 is configured with 6+ axes")
            print("3. VJoy device is enabled")
        input("Press Enter to exit...")
        return 1

if __name__ == "__main__":
    sys.exit(main())
