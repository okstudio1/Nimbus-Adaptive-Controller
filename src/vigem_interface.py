"""
ViGEmBus interface wrapper for emulating Xbox 360 controllers.
This provides XInput-compatible virtual controllers that work with modern games
like No Man's Sky that don't support DirectInput (vJoy).
"""

import sys
import time
import threading
from typing import Optional, Dict, Any
from .config import ControllerConfig

try:
    import vgamepad as vg
    VIGEM_AVAILABLE = True
    print("[OK] vgamepad imported successfully - Xbox 360 controller emulation available")
except Exception as e:
    # ImportError when missing; other errors if the ViGEmClient DLL cannot load.
    VIGEM_AVAILABLE = False
    if sys.platform == "win32":
        print(f"Warning: vgamepad not available: {e}")
        print("Install with: pip install vgamepad")
        print("This will also install the ViGEmBus driver for Xbox controller emulation")


class ViGEmInterface:
    """
    Wrapper for ViGEmBus Xbox 360 controller emulation.
    
    This creates a virtual Xbox 360 controller that games see as a real
    XInput device, solving compatibility issues with games like No Man's Sky.
    """
    
    def __init__(self, config: ControllerConfig):
        """
        Initialize the ViGEm interface.
        
        Args:
            config: Configuration manager instance
        """
        self.config = config
        self.gamepad: Optional[Any] = None
        self.is_connected = False
        self.last_update_time = 0.0
        self.update_rate = config.get("vjoy.update_rate", 60)
        
        # Current axis values (normalized -1.0 to 1.0)
        self.current_values = {
            'left_x': 0.0,
            'left_y': 0.0,
            'right_x': 0.0,
            'right_y': 0.0,
            'left_trigger': 0.0,
            'right_trigger': 0.0
        }
        
        # Button states
        self.button_states: Dict[int, bool] = {}
        
        # Initialize ViGEm connection
        self._initialize_vigem()
    
    def _initialize_vigem(self) -> None:
        """Initialize ViGEmBus Xbox 360 controller."""
        if not VIGEM_AVAILABLE:
            print("ViGEm interface not available - running in simulation mode")
            print("\nTo enable Xbox controller emulation:")
            print("1. Run: pip install vgamepad")
            print("2. This will prompt to install the ViGEmBus driver")
            print("3. Accept the driver installation")
            print("4. Restart this application")
            return
        
        try:
            print("Creating virtual Xbox 360 controller...")
            self.gamepad = vg.VX360Gamepad()
            self.is_connected = True
            print("[OK] Virtual Xbox 360 controller created successfully")
            print("Games should now see this as 'Xbox 360 Controller for Windows'")
            
            # Reset to center position
            self._reset_axes()
            
        except Exception as e:
            print(f"[ERROR] Failed to create virtual Xbox controller: {e}")
            print("\nTroubleshooting steps:")
            print("1. Make sure ViGEmBus driver is installed")
            print("2. Try running: pip install vgamepad --force-reinstall")
            print("3. Restart your computer after driver installation")
            print("4. Run this application as administrator")
            self.is_connected = False
            self.gamepad = None
    
    def _reset_axes(self) -> None:
        """Reset all axes to center/zero position."""
        if not self.gamepad:
            return
        
        try:
            # Reset joysticks to center
            self.gamepad.left_joystick_float(0.0, 0.0)
            self.gamepad.right_joystick_float(0.0, 0.0)
            
            # Reset triggers to zero
            self.gamepad.left_trigger_float(0.0)
            self.gamepad.right_trigger_float(0.0)
            
            # Apply the update
            self.gamepad.update()
            
            # Update current values
            for axis in self.current_values:
                self.current_values[axis] = 0.0
                
        except Exception as e:
            print(f"Error resetting axes: {e}")
    
    def set_left_stick(self, x: float, y: float) -> bool:
        """
        Set left joystick position.
        
        Args:
            x: X axis value (-1.0 to 1.0)
            y: Y axis value (-1.0 to 1.0)
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            # Clamp values
            x = max(-1.0, min(1.0, x))
            y = max(-1.0, min(1.0, y))
            
            self.gamepad.left_joystick_float(x, y)
            self.gamepad.update()
            
            self.current_values['left_x'] = x
            self.current_values['left_y'] = y
            self.last_update_time = time.time()
            
            return True
            
        except Exception as e:
            print(f"Error setting left stick: {e}")
            return False
    
    def set_right_stick(self, x: float, y: float) -> bool:
        """
        Set right joystick position.
        
        Args:
            x: X axis value (-1.0 to 1.0)
            y: Y axis value (-1.0 to 1.0)
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            # Clamp values
            x = max(-1.0, min(1.0, x))
            y = max(-1.0, min(1.0, y))
            
            self.gamepad.right_joystick_float(x, y)
            self.gamepad.update()
            
            self.current_values['right_x'] = x
            self.current_values['right_y'] = y
            self.last_update_time = time.time()
            
            return True
            
        except Exception as e:
            print(f"Error setting right stick: {e}")
            return False
    
    def set_left_trigger(self, value: float) -> bool:
        """
        Set left trigger value.
        
        Args:
            value: Trigger value (0.0 to 1.0)
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            value = max(0.0, min(1.0, value))
            self.gamepad.left_trigger_float(value)
            self.gamepad.update()
            
            self.current_values['left_trigger'] = value
            self.last_update_time = time.time()
            
            return True
            
        except Exception as e:
            print(f"Error setting left trigger: {e}")
            return False
    
    def set_right_trigger(self, value: float) -> bool:
        """
        Set right trigger value.
        
        Args:
            value: Trigger value (0.0 to 1.0)
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            value = max(0.0, min(1.0, value))
            self.gamepad.right_trigger_float(value)
            self.gamepad.update()
            
            self.current_values['right_trigger'] = value
            self.last_update_time = time.time()
            
            return True
            
        except Exception as e:
            print(f"Error setting right trigger: {e}")
            return False
    
    def set_button(self, button_id: int, pressed: bool) -> bool:
        """
        Set button state using Xbox button mapping.
        
        Button mapping (matching Nimbus Adaptive Controller conventions):
            1: A
            2: B
            3: X
            4: Y
            5: LB (Left Bumper)
            6: RB (Right Bumper)
            7: Back/View
            8: Start/Menu
            9: LS (Left Stick Click)
            10: RS (Right Stick Click)
            11: DPad Up
            12: DPad Down
            13: DPad Left
            14: DPad Right
            
        Args:
            button_id: Button ID (1-14)
            pressed: Whether button is pressed
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            # Map button IDs to vgamepad button constants
            button_mapping = {
                1: vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
                2: vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
                3: vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
                4: vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
                5: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
                6: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
                7: vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
                8: vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
                9: vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
                10: vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
                11: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
                12: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
                13: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
                14: vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            }
            
            if button_id not in button_mapping:
                print(f"Unknown button ID: {button_id}")
                return False
            
            button = button_mapping[button_id]
            
            if pressed:
                self.gamepad.press_button(button)
            else:
                self.gamepad.release_button(button)
            
            self.gamepad.update()
            self.button_states[button_id] = pressed
            self.last_update_time = time.time()
            
            return True
            
        except Exception as e:
            print(f"Error setting button {button_id}: {e}")
            return False
    
    def update_axis(self, axis: str, value: float) -> bool:
        """
        Update a single axis value (compatibility with vJoy interface).
        
        Args:
            axis: Axis name ('x', 'y', 'z', 'rx', 'ry', 'rz')
            value: Normalized value (-1.0 to 1.0)
            
        Returns:
            True if update was successful
        """
        if not self.is_connected or not self.gamepad:
            return False
        
        try:
            # Map vJoy axis names to Xbox controller
            if axis == 'x':
                self.current_values['left_x'] = value
                self.gamepad.left_joystick_float(value, self.current_values['left_y'])
            elif axis == 'y':
                self.current_values['left_y'] = value
                self.gamepad.left_joystick_float(self.current_values['left_x'], value)
            elif axis == 'rx':
                self.current_values['right_x'] = value
                self.gamepad.right_joystick_float(value, self.current_values['right_y'])
            elif axis == 'ry':
                self.current_values['right_y'] = value
                self.gamepad.right_joystick_float(self.current_values['right_x'], value)
            elif axis == 'z':
                # Z axis -> Left Trigger (convert from -1..1 to 0..1)
                trigger_value = (value + 1.0) / 2.0
                self.current_values['left_trigger'] = trigger_value
                self.gamepad.left_trigger_float(trigger_value)
            elif axis == 'rz':
                # RZ axis -> Right Trigger (convert from -1..1 to 0..1)
                trigger_value = (value + 1.0) / 2.0
                self.current_values['right_trigger'] = trigger_value
                self.gamepad.right_trigger_float(trigger_value)
            else:
                print(f"Unknown axis: {axis}")
                return False
            
            self.gamepad.update()
            self.last_update_time = time.time()
            return True
            
        except Exception as e:
            print(f"Error updating axis {axis}: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current interface status.
        
        Returns:
            Dictionary containing status information
        """
        return {
            'connected': self.is_connected,
            'device_type': 'Xbox 360 Controller',
            'last_update': self.last_update_time,
            'current_values': self.current_values.copy(),
            'button_states': self.button_states.copy(),
            'vigem_available': VIGEM_AVAILABLE
        }
    
    def emergency_stop(self) -> None:
        """Emergency stop - immediately center all axes."""
        print("EMERGENCY STOP ACTIVATED")
        if self.gamepad:
            try:
                self._reset_axes()
                # Release all buttons
                self.gamepad.reset()
                self.gamepad.update()
            except Exception as e:
                print(f"Error during emergency stop: {e}")
    
    def shutdown(self) -> None:
        """Shutdown the ViGEm interface safely."""
        print("Shutting down ViGEm interface...")
        
        if self.gamepad:
            try:
                self._reset_axes()
                self.gamepad.reset()
                self.gamepad.update()
                # Note: vgamepad handles cleanup automatically
                self.gamepad = None
            except Exception as e:
                print(f"Error during shutdown: {e}")
        
        self.is_connected = False
        print("ViGEm interface shutdown complete")
    
    def __del__(self):
        """Destructor - ensure clean shutdown."""
        try:
            self.shutdown()
        except:
            pass
