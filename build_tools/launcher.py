#!/usr/bin/env python3
"""
Launcher for Nimbus Adaptive Controller executable.
This is a simplified entry point for PyInstaller builds that doesn't use stdin/input.
"""

import sys
import os
from pathlib import Path

from src import __version__

def main():
    """Main launcher function for executable."""
    # Change to script/executable directory
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        application_path = Path(sys.executable).parent
    else:
        # Running as script
        application_path = Path(__file__).parent
    
    os.chdir(application_path)
    
    # Import and run the Qt QML application directly
    try:
        from src.qt_qml_app import main as app_main
        return app_main()
    except Exception as e:
        # For GUI apps, we can't use input(), so just show error via message box
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            
            error_msg = f"Failed to start Nimbus Adaptive Controller:\n\n{str(e)}\n\n"
            error_msg += "Common issues:\n"
            error_msg += "1. ViGEmBus driver not installed. Run the Nimbus installer again and tick ViGEmBus on the drivers page.\n"
            error_msg += "2. ViGEmClient.dll missing — reinstall Nimbus Adaptive Controller\n"
            error_msg += "3. vJoy driver not installed (only needed for vJoy profiles; the installer offers it too)\n"
            error_msg += "4. Windows Defender blocking the virtual controller driver"
            
            QMessageBox.critical(None, "Nimbus Adaptive Controller Error", error_msg)
        except:
            # If even Qt fails, just exit silently
            pass
        return 1

if __name__ == "__main__":
    sys.exit(main())
