"""
Configuration module for the virtual controller.
Handles sensitivity curves, dead zones, and other controller parameters.
"""

import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from .profile_repository import ProfileRepository

# App name for user data directory
APP_NAME = "ProjectNimbus"

# The inner deadzones XInput documents for its two sticks, as fractions of
# the 16-bit axis range. Games that follow the documentation discard stick
# input below these magnitudes, so they are the output anti-deadzone
# defaults when Nimbus presents an XInput (ViGEm) controller. Games override
# them freely, which is why the value is a per-widget setting and not a
# constant of the pipeline.
XINPUT_LEFT_THUMB_DEADZONE = 7849 / 32767.0     # 0.2395
XINPUT_RIGHT_THUMB_DEADZONE = 8689 / 32767.0    # 0.2652

# Per-widget shaping defaults, shared by the bridge (which resolves a widget's
# parameters) and the QML config dialog (which shows them). Percent units
# match the sliders in the dialog; anti-deadzone values are fractions of the
# output range.
DEFAULT_SENSITIVITY_PCT = 50.0
DEFAULT_DEAD_ZONE_PCT = 0.0
DEFAULT_EXTREMITY_PCT_STICK = 5.0
DEFAULT_EXTREMITY_PCT_AXIS = 0.0
DEFAULT_ANTI_DEADZONE_BUFFER = 0.02
DEFAULT_TREMOR_FILTER = 0.0
DEFAULT_PRECISION_GAIN = 0.25


def sensitivity_power(sensitivity_pct: float) -> float:
    """
    Map the sensitivity slider to the exponent of the response curve.

    50 is linear. Below 50 the curve flattens near the centre (exponent up
    to 4.0 at 0); above 50 it steepens (exponent down to 0.1 at 100). This
    is the one place the mapping lives; the curve preview asks the bridge
    for its points rather than re-implementing it.

    Parameters
    ----------
    sensitivity_pct : float
        Slider value, 0 to 100.

    Returns
    -------
    float
        Exponent applied to the normalised magnitude.
    """
    sensitivity = max(0.0, min(100.0, float(sensitivity_pct))) / 100.0
    if abs(sensitivity - 0.5) < 1e-9:
        return 1.0
    if sensitivity < 0.5:
        return 1.0 + (0.5 - sensitivity) * 6.0
    return max(0.1, 1.0 - (sensitivity - 0.5) * 1.8)


def shape_magnitude(magnitude: float,
                    sensitivity: float = DEFAULT_SENSITIVITY_PCT,
                    dead_zone: float = DEFAULT_DEAD_ZONE_PCT,
                    extremity_dead_zone: float = DEFAULT_EXTREMITY_PCT_STICK,
                    anti_deadzone: float = 0.0,
                    anti_deadzone_buffer: float = 0.0,
                    gain: float = 1.0) -> float:
    """
    Shape a non-negative input magnitude into an output magnitude.

    This is the single response formula for every axis Nimbus drives. In
    order: inner deadzone, gain, response curve, then a remap of anything
    non-zero onto ``[anti_deadzone + anti_deadzone_buffer, 1 - extremity]``.
    The floor is applied inside the ceiling so that the smallest real
    movement always lands at exactly the floor the user calibrated, whatever
    the extremity cap is set to.

    Parameters
    ----------
    magnitude : float
        Input magnitude, 0 to 1 (clamped).
    sensitivity : float
        Response curve, percent; 50 is linear (see :func:`sensitivity_power`).
    dead_zone : float
        Inner deadzone, percent of the slider; 100 percent is a quarter of
        the input range, matching the historical dialog units.
    extremity_dead_zone : float
        Percent taken off the top of the output range (a maximum-output cap).
    anti_deadzone : float
        Output floor, 0 to 1: the game's own inner deadzone to skip past.
    anti_deadzone_buffer : float
        Added to the floor so the user can re-introduce a small margin above
        the game's threshold. Only counts when ``anti_deadzone`` is non-zero:
        a margin above nothing is nothing.
    gain : float
        Multiplier on the post-deadzone magnitude; the precision modifier
        passes a value below 1 here.

    Returns
    -------
    float
        Output magnitude in [0, 1]. Exactly 0 inside the deadzone.
    """
    m = max(0.0, min(1.0, float(magnitude)))
    dz = max(0.0, min(100.0, float(dead_zone))) / 100.0 * 0.25
    if m <= dz:
        return 0.0
    normalized = (m - dz) / max(1e-6, 1.0 - dz)
    normalized = max(0.0, min(1.0, normalized * max(0.0, float(gain))))
    if normalized <= 0.0:
        return 0.0
    curved = math.pow(normalized, sensitivity_power(sensitivity))
    ceiling = 1.0 - max(0.0, min(100.0, float(extremity_dead_zone))) / 100.0
    floor = max(0.0, min(1.0, float(anti_deadzone)))
    if floor > 0.0:
        floor += max(0.0, min(1.0, float(anti_deadzone_buffer)))
    floor = min(floor, ceiling)
    return floor + curved * (ceiling - floor)


def shape_vector(x: float, y: float, **params: float) -> Tuple[float, float]:
    """
    Shape a raw stick vector radially.

    The vector is clamped to the unit circle, its magnitude is passed
    through :func:`shape_magnitude`, and the direction is preserved. Radial
    rather than per-axis so the dead region is a circle, diagonals respond
    like cardinals, and the output magnitude never exceeds 1.

    Parameters
    ----------
    x, y : float
        Raw normalised deflection, before any shaping.
    **params
        Keyword arguments for :func:`shape_magnitude`.

    Returns
    -------
    Tuple[float, float]
        Shaped output per axis, each in [-1, 1], magnitude at most 1.
    """
    fx, fy = float(x), float(y)
    mag = math.hypot(fx, fy)
    if mag <= 0.0:
        return 0.0, 0.0
    if mag > 1.0:
        fx, fy, mag = fx / mag, fy / mag, 1.0
    out = shape_magnitude(mag, **params)
    if out <= 0.0:
        return 0.0, 0.0
    scale = out / mag
    return fx * scale, fy * scale


class ControllerConfig:
    """
    Manages configuration settings for the virtual controller.
    
    This class handles loading, saving, and applying configuration settings
    including sensitivity curves, dead zones, and UI parameters.
    """
    
    def __init__(self, config_file: str = "controller_config.json"):
        """
        Initialize the configuration manager.
        
        Args:
            config_file: Path to the configuration file
        """
        self.config_file = config_file
        self.config = self._load_default_config()
        self.load_config()
        
        # Profile system - set up directories
        self._bundled_profiles_dir = self._get_bundled_profiles_dir()
        self._user_data_dir = self._get_user_data_dir()
        self._user_profiles_dir = self._user_data_dir / "profiles"
        self.profiles = ProfileRepository(self._user_profiles_dir, self._bundled_profiles_dir)
        
        # Ensure user profiles directory exists and has default profiles
        self._ensure_user_profiles()
        
        _saved_profile = self.config.get("current_profile", "adaptive_platform_2")
        # If the saved profile was deprecated and removed, fall back to the default
        if _saved_profile in self._DEPRECATED_BUNDLED_PROFILES:
            _saved_profile = "adaptive_platform_2"
            self.set("current_profile", _saved_profile)
            self.save_config()
        self._current_profile: Optional[str] = _saved_profile
    
    @staticmethod
    def _get_user_data_dir() -> Path:
        """
        Get the user data directory for storing profiles and settings.
        
        Returns:
            Path to user data directory (e.g., %APPDATA%/ProjectNimbus on Windows)
        """
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        
        return base / APP_NAME
    
    def _get_bundled_profiles_dir(self) -> Path:
        """
        Get the directory containing bundled default profiles.
        
        Handles both development mode and PyInstaller frozen mode.
        
        Returns:
            Path to bundled profiles directory
        """
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller bundle
            base_path = Path(sys._MEIPASS)
        else:
            # Running in development
            base_path = Path(__file__).resolve().parent.parent
        
        return base_path / "profiles"
    
    # Old bundled profile IDs that have been retired and should be cleaned up
    # from the user profiles directory to avoid confusion. Every id here must
    # be absent from profiles/: an id that is both listed here and still
    # shipped gets deleted and re-copied on every launch, which silently
    # reverts whatever the user changed in it. xbox_controller was in this set
    # while profiles/xbox_controller.json was still being shipped and edited,
    # so it reset itself at every start.
    _DEPRECATED_BUNDLED_PROFILES = {
        "flight_simulator",
        "adaptive_platform_1",
    }

    def _ensure_user_profiles(self) -> None:
        """
        Ensure user profiles directory exists and contains default profiles.
        
        Copies bundled profiles to user directory if they don't exist.
        Also removes deprecated bundled profiles that are no longer shipped.
        """
        self.profiles.ensure_defaults(self._DEPRECATED_BUNDLED_PROFILES)
    
    def _load_default_config(self) -> Dict[str, Any]:
        """
        Load default configuration settings.
        
        Returns:
            Dictionary containing default configuration
        """
        return {
            "joysticks": {
                "left": {
                    "dead_zone": 0.1,
                    "sensitivity": 1.0,
                    "curve_type": "linear",  # linear, exponential, logarithmic
                    "curve_power": 2.0,
                    "invert_x": False,
                    "invert_y": False,
                    "max_range": 1.0
                },
                "right": {
                    "dead_zone": 0.1,
                    "sensitivity": 1.0,
                    "curve_type": "linear",
                    "curve_power": 2.0,
                    "invert_x": False,
                    "invert_y": False,
                    "max_range": 1.0
                }
            },
            "ui": {
                "window_width": 614,
                "window_height": 311,
                "joystick_size": 336,
                "background_color": (20, 20, 20),
                "joystick_bg_color": (80, 20, 20),
                "joystick_fg_color": (255, 50, 50),
                "button_color": (60, 15, 15),
                "button_hover_color": (100, 25, 25),
                "text_color": (255, 255, 255),
                "font_size": 14,
                "scale_factor": 1.0,
                "debug_borders": False
            },
            "vjoy": {
                "device_id": 1,
                "update_rate": 60,  # Hz
                "axis_range": 32767  # VJoy axis range
            },
            "axis_mapping": {
                "left_x": "x",      # Left joystick X -> VJoy X axis
                "left_y": "y",      # Left joystick Y -> VJoy Y axis
                "right_x": "rx",    # Right joystick X -> VJoy RX axis
                "right_y": "ry",    # Right joystick Y -> VJoy RY axis
                "throttle": "z",    # Throttle -> VJoy Z axis (default)
                "rudder": "rz"      # Rudder -> VJoy RZ axis (default)
            },
            "safety": {
                "enable_failsafe": True,
                "failsafe_timeout": 5.0,  # seconds
                "max_update_rate": 100,  # Hz
                "enable_smoothing": True,
                "smoothing_factor": 0.1
            }
        }
    
    def load_config(self) -> None:
        """Load configuration from file if it exists."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    self._merge_config(loaded_config)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load config file: {e}")
                print("Using default configuration.")
    
    def save_config(self) -> None:
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except IOError as e:
            print(f"Error saving config file: {e}")
    
    def _merge_config(self, loaded_config: Dict[str, Any]) -> None:
        """
        Merge loaded configuration with defaults.
        
        Args:
            loaded_config: Configuration loaded from file
        """
        def merge_dict(default: Dict, loaded: Dict) -> Dict:
            result = default.copy()
            for key, value in loaded.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = merge_dict(result[key], value)
                else:
                    result[key] = value
            return result
        
        self.config = merge_dict(self.config, loaded_config)
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.
        
        Args:
            key_path: Path to the configuration key (e.g., "joysticks.left.dead_zone")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key_path.split('.')
        value = self.config
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key_path: str, value: Any) -> None:
        """
        Set configuration value using dot notation.
        
        Args:
            key_path: Path to the configuration key
            value: Value to set
        """
        keys = key_path.split('.')
        config_ref = self.config
        
        for key in keys[:-1]:
            if key not in config_ref:
                config_ref[key] = {}
            config_ref = config_ref[key]
        
        config_ref[keys[-1]] = value
    
    def _settings_params(self, section: str) -> Dict[str, float]:
        """
        Shaping parameters from a profile-level settings block.

        Used by the legacy (non-custom) layouts, whose sticks carry no
        per-widget settings: ``joystick_settings`` for the sticks and
        ``rudder_settings`` for the rudder axis. Every value comes from the
        block itself, including ``anti_deadzone``, which is off unless the
        profile sets it.

        Parameters
        ----------
        section : str
            ``"joystick_settings"`` or ``"rudder_settings"``.

        Returns
        -------
        Dict[str, float]
            Keyword arguments for :func:`shape_magnitude`.
        """
        # No anti-deadzone by default here, even under ViGEm. The output floor
        # is a per-widget feature: the custom-layout dialog is the only place
        # it can be seen, calibrated against a running game, or turned off.
        # Defaulting it on for the legacy layouts would give an adaptive, xbox
        # or flight_sim profile a silent ~0.26 floor with no control anywhere
        # in the UI to lower it, which is wrong for a flight sim whatever it
        # does for aiming. A profile that wants one can still set
        # joystick_settings.anti_deadzone explicitly; XINPUT_LEFT_THUMB_DEADZONE
        # and XINPUT_RIGHT_THUMB_DEADZONE are the values the widget path uses.
        default_adz = 0.0
        return {
            "sensitivity": float(self.get(f"{section}.sensitivity", DEFAULT_SENSITIVITY_PCT)),
            "dead_zone": float(self.get(f"{section}.deadzone", 10.0)),
            "extremity_dead_zone": float(self.get(f"{section}.extremity_deadzone", DEFAULT_EXTREMITY_PCT_STICK)),
            "anti_deadzone": float(self.get(f"{section}.anti_deadzone", default_adz)),
            "anti_deadzone_buffer": float(self.get(f"{section}.anti_deadzone_buffer", DEFAULT_ANTI_DEADZONE_BUFFER)),
        }

    def shape_stick(self, x: float, y: float, joystick: str,
                    output_mode: str = "vjoy", gain: float = 1.0) -> Tuple[float, float]:
        """
        Shape a raw stick vector with the profile's global joystick settings.

        This is the path for the legacy layouts (``adaptive``, ``xbox``,
        ``flight_sim``), whose sticks have no per-widget settings. Custom
        layout widgets are shaped by the bridge from their own settings with
        the same :func:`shape_vector`.

        Parameters
        ----------
        x, y : float
            Raw normalised deflection from the widget, before any shaping.
        joystick : str
            Accepted for call-site symmetry with the widget path; the profile
            block is shared by both sticks, so it does not select anything.
        output_mode : str
            Accepted for the same reason. Unlike the per-widget path, this
            block gets no XInput anti-deadzone default (see
            :meth:`_settings_params`).
        gain : float
            Multiplier on the post-deadzone magnitude (precision modifier).

        Returns
        -------
        Tuple[float, float]
            Shaped output in [-1, 1] per axis, magnitude never exceeding 1.
        """
        del joystick, output_mode   # documented above: neither selects anything here
        params = self._settings_params("joystick_settings")
        return shape_vector(x, y, gain=gain, **params)

    def apply_joystick_dialog_curve(self, value: float) -> float:
        """
        Shape a single bipolar value with the profile's ``joystick_settings``.

        Kept for the Qt Widgets settings dialogs; the same formula as
        :func:`shape_magnitude` without any anti-deadzone.
        """
        v = float(value)
        params = self._settings_params("joystick_settings")
        params["anti_deadzone"] = 0.0
        params["anti_deadzone_buffer"] = 0.0
        out = shape_magnitude(abs(v), **params)
        return out if v >= 0 else -out

    def apply_rudder_sensitivity_curve(self, value: float) -> float:
        """
        Shape a single bipolar value with the profile's ``rudder_settings``.

        Mirrors :meth:`apply_joystick_dialog_curve` for the rudder axis of
        the legacy layouts.
        """
        v = float(value)
        params = self._settings_params("rudder_settings")
        params["anti_deadzone"] = 0.0
        params["anti_deadzone_buffer"] = 0.0
        out = shape_magnitude(abs(v), **params)
        return out if v >= 0 else -out
    
    def get_vjoy_value(self, normalized_value: float) -> int:
        """
        Convert normalized value (-1.0 to 1.0) to VJoy axis value.
        
        Args:
            normalized_value: Normalized input value
            
        Returns:
            VJoy axis value
        """
        axis_range = self.get("vjoy.axis_range", 32767)
        return int((normalized_value + 1.0) * axis_range / 2.0)
    
    def get_scaled_value(self, base_value: float) -> float:
        """
        Get scaled value based on current UI scale factor.
        
        Args:
            base_value: Base value to scale
            
        Returns:
            Scaled value
        """
        scale_factor = self.get("ui.scale_factor", 1.0)
        return base_value * scale_factor
    
    def get_scaled_int(self, base_value: int) -> int:
        """
        Get scaled integer value based on current UI scale factor.
        
        Args:
            base_value: Base integer value to scale
            
        Returns:
            Scaled integer value
        """
        return int(self.get_scaled_value(float(base_value)))
    
    def set_scale_factor(self, scale_factor: float) -> None:
        """
        Set UI scale factor and update related UI values.
        
        Args:
            scale_factor: New scale factor (0.5 to 2.0)
        """
        # Clamp scale factor to reasonable range
        scale_factor = max(0.5, min(2.0, scale_factor))
        self.set("ui.scale_factor", scale_factor)
        
        # Update scaled window dimensions using a custom compact resolution.
        base_width = 614
        base_height = 311
        width = int(base_width * scale_factor)
        height = int(base_height * scale_factor)
        self.set("ui.window_width", width)
        self.set("ui.window_height", height)
        
        # Update scaled joystick size (20% larger base)
        base_joystick_size = 336
        self.set("ui.joystick_size", int(base_joystick_size * scale_factor))
        
        # Update scaled font size
        base_font_size = 16
        self.set("ui.font_size", int(base_font_size * scale_factor))

    def validate_config(self) -> Tuple[bool, str]:
        """
        Validate current configuration.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check required sections
            required_sections = ["joysticks", "ui", "vjoy", "safety"]
            for section in required_sections:
                if section not in self.config:
                    return False, f"Missing required section: {section}"
            
            # Validate joystick configurations
            for joystick in ["left", "right"]:
                if joystick not in self.config["joysticks"]:
                    return False, f"Missing joystick configuration: {joystick}"
                
                # Check dead zone range
                dead_zone = self.get(f"joysticks.{joystick}.dead_zone", 0.1)
                if not 0.0 <= dead_zone <= 0.5:
                    return False, f"Invalid dead zone for {joystick}: {dead_zone}"
                
                # Check sensitivity range
                sensitivity = self.get(f"joysticks.{joystick}.sensitivity", 1.0)
                if not 0.1 <= sensitivity <= 5.0:
                    return False, f"Invalid sensitivity for {joystick}: {sensitivity}"
            
            # Validate VJoy settings
            device_id = self.get("vjoy.device_id", 1)
            if not 1 <= device_id <= 16:
                return False, f"Invalid VJoy device ID: {device_id}"
            
            # Validate scale factor
            scale_factor = self.get("ui.scale_factor", 1.0)
            if not 0.5 <= scale_factor <= 2.0:
                return False, f"Invalid scale factor: {scale_factor}"
            
            return True, "Configuration is valid"
            
        except Exception as e:
            return False, f"Configuration validation error: {str(e)}"

    # -------------------------------------------------------------------------
    # Profile System
    # -------------------------------------------------------------------------

    def get_available_profiles(self) -> List[Dict[str, str]]:
        """
        Get list of available profiles from the user profiles directory.
        
        Returns:
            List of dicts with 'id', 'name', 'description', 'layout_type', 'is_builtin' keys
        """
        return self.profiles.list_profiles()

    def get_current_profile(self) -> str:
        """Get the current profile ID."""
        return self._current_profile or "adaptive_platform_2"

    def get_current_profile_data(self) -> Optional[Dict[str, Any]]:
        """Load and return the current profile's full data."""
        return self.load_profile(self.get_current_profile())

    def load_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a profile by ID from user profiles directory.
        
        Args:
            profile_id: Profile identifier (filename without .json)
            
        Returns:
            Profile data dict or None if not found
        """
        return self.profiles.load(profile_id)

    def switch_profile(self, profile_id: str) -> bool:
        """
        Switch to a different profile.
        
        Args:
            profile_id: Profile identifier to switch to
            
        Returns:
            True if switch was successful, False otherwise
        """
        profile_data = self.load_profile(profile_id)
        if profile_data is None:
            return False
        
        # Update current profile
        self._current_profile = profile_id
        self.set("current_profile", profile_id)
        
        # Apply profile settings to config
        self._apply_profile_settings(profile_data)
        
        # Save config
        self.save_config()
        return True

    def _apply_profile_settings(self, profile_data: Dict[str, Any]) -> None:
        """
        Apply profile settings to the current configuration.
        
        Args:
            profile_data: Profile data dictionary
        """
        # Apply axis mapping
        if "axis_mapping" in profile_data:
            for key, value in profile_data["axis_mapping"].items():
                self.set(f"axis_mapping.{key}", value)
        
        # Apply button settings (preserve toggle_mode from profile)
        if "buttons" in profile_data:
            for btn_key, btn_data in profile_data["buttons"].items():
                if isinstance(btn_data, dict):
                    for setting_key, setting_value in btn_data.items():
                        self.set(f"buttons.{btn_key}.{setting_key}", setting_value)
        
        # Apply joystick settings
        if "joystick_settings" in profile_data:
            for key, value in profile_data["joystick_settings"].items():
                self.set(f"joystick_settings.{key}", value)
        
        # Apply rudder settings
        if "rudder_settings" in profile_data:
            for key, value in profile_data["rudder_settings"].items():
                self.set(f"rudder_settings.{key}", value)

    def get_button_label(self, button_id: int) -> str:
        """
        Get the label for a button based on current profile.
        
        Args:
            button_id: Button number (1-based)
            
        Returns:
            Button label string
        """
        profile_data = self.get_current_profile_data()
        if profile_data and "buttons" in profile_data:
            btn_key = f"button_{button_id}"
            btn_data = profile_data["buttons"].get(btn_key, {})
            if isinstance(btn_data, dict):
                return btn_data.get("label", str(button_id))
        return str(button_id)

    def get_layout_type(self) -> str:
        """Get the layout type of the current profile."""
        profile_data = self.get_current_profile_data()
        if profile_data:
            return profile_data.get("layout_type", "flight_sim")
        return "flight_sim"

    def save_current_profile(self) -> bool:
        """
        Save current settings to the active profile.
        
        Updates the user's profile file with current joystick/rudder settings,
        button configurations, and axis mappings.
        
        Returns:
            True if save was successful, False otherwise
        """
        profile_id = self.get_current_profile()
        
        # Load existing profile data to preserve structure
        profile_data = self.load_profile(profile_id)
        if profile_data is None:
            return False
        
        # Update with current settings
        profile_data["joystick_settings"] = {
            "sensitivity": self.get("joystick_settings.sensitivity", 35.0),
            "deadzone": self.get("joystick_settings.deadzone", 0.0),
            "extremity_deadzone": self.get("joystick_settings.extremity_deadzone", 38.0),
        }
        
        profile_data["rudder_settings"] = {
            "sensitivity": self.get("rudder_settings.sensitivity", 50.0),
            "deadzone": self.get("rudder_settings.deadzone", 10.0),
            "extremity_deadzone": self.get("rudder_settings.extremity_deadzone", 5.0),
        }
        
        # Update button toggle modes
        if "buttons" in profile_data:
            for btn_key in profile_data["buttons"]:
                toggle_mode = self.get(f"buttons.{btn_key}.toggle_mode", False)
                if isinstance(profile_data["buttons"][btn_key], dict):
                    profile_data["buttons"][btn_key]["toggle_mode"] = toggle_mode
        
        # Update axis mapping
        if "axis_mapping" in profile_data:
            for axis_key in profile_data["axis_mapping"]:
                value = self.get(f"axis_mapping.{axis_key}")
                if value is not None:
                    profile_data["axis_mapping"][axis_key] = value
        
        return self.profiles.save(profile_id, profile_data)

    def save_custom_layout(self, widgets: list, grid_snap: int = 10, show_grid: bool = True) -> bool:
        """
        Save custom layout widget data to the current profile.
        
        Args:
            widgets: List of widget dictionaries from the QML canvas
            grid_snap: Grid snap size in pixels
            show_grid: Whether to show the grid overlay
            
        Returns:
            True if save was successful, False otherwise
        """
        profile_id = self.get_current_profile()
        
        profile_data = self.load_profile(profile_id)
        if profile_data is None:
            return False
        
        # Update custom layout section
        if "custom_layout" not in profile_data:
            profile_data["custom_layout"] = {}
        
        profile_data["custom_layout"]["widgets"] = widgets
        profile_data["custom_layout"]["grid_snap"] = grid_snap
        profile_data["custom_layout"]["show_grid"] = show_grid
        
        return self.profiles.save(profile_id, profile_data)

    def save_profile_as(self, profile_id: str, profile_data: dict) -> bool:
        """
        Save profile data as a new profile file.
        
        Args:
            profile_id: New profile identifier (used as filename)
            profile_data: Complete profile data dictionary
            
        Returns:
            True if save was successful, False otherwise
        """
        return self.profiles.save(profile_id, profile_data)

    def reset_profile(self, profile_id: str) -> bool:
        """
        Reset a profile to its default (bundled) settings.
        
        Only works for built-in profiles that have a bundled version.
        
        Args:
            profile_id: Profile identifier to reset
            
        Returns:
            True if reset was successful, False otherwise
        """
        if not self.profiles.reset(profile_id):
            return False
        if profile_id == self._current_profile:
            profile_data = self.load_profile(profile_id)
            if profile_data:
                self._apply_profile_settings(profile_data)
                self.save_config()
        return True

    def duplicate_profile(self, source_id: str, new_name: str) -> Optional[str]:
        """
        Create a copy of an existing profile with a new name.
        
        Args:
            source_id: Profile ID to copy from
            new_name: Display name for the new profile
            
        Returns:
            New profile ID if successful, None otherwise
        """
        source_data = self.load_profile(source_id)
        if source_data is None:
            return None
        
        new_id = self.profiles.unique_id(new_name)
        
        # Create new profile
        new_data = source_data.copy()
        new_data["name"] = new_name
        new_data["description"] = f"Custom profile based on {source_data.get('name', source_id)}"
        
        return new_id if self.profiles.save(new_id, new_data) else None

    def create_profile_as(self, name: str, description: str = "") -> Optional[str]:
        """
        Create a new profile from current settings with a custom name and description.
        
        Args:
            name: Display name for the new profile
            description: Optional description for the profile
            
        Returns:
            New profile ID if successful, None otherwise
        """
        new_id = self.profiles.unique_id(name)

        # Build a blank custom profile — empty canvas, no widgets
        new_data = {
            "name": name,
            "description": description if description else "Custom profile",
            "layout_type": "custom",
            "custom_layout": {
                "canvas_width": 1024,
                "canvas_height": 600,
                "grid_snap": 10,
                "show_grid": True,
                "widgets": []
            },
            "axis_mapping": {},
            "buttons": {},
            "joystick_settings": {
                "sensitivity": 50.0,
                "deadzone": 10.0,
                "extremity_deadzone": 5.0,
            },
            "rudder_settings": {
                "sensitivity": 50.0,
                "deadzone": 10.0,
                "extremity_deadzone": 5.0,
            }
        }

        return new_id if self.profiles.save(new_id, new_data) else None

    def delete_profile(self, profile_id: str) -> bool:
        """
        Delete a user-created profile.
        
        Cannot delete built-in profiles.
        
        Args:
            profile_id: Profile ID to delete
            
        Returns:
            True if deleted, False if not allowed or failed
        """
        if not self.profiles.delete(profile_id):
            return False
        if profile_id == self._current_profile:
            self.switch_profile("adaptive_platform_2")
        return True

    def is_builtin_profile(self, profile_id: str) -> bool:
        """Check if a profile is a built-in (bundled) profile."""
        return self.profiles.is_builtin(profile_id)

    def get_user_profiles_path(self) -> str:
        """Get the path to the user profiles directory."""
        return str(self._user_profiles_dir)
