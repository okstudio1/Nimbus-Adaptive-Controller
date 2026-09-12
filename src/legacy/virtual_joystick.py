"""
Virtual joystick implementation for the controller interface.
Handles mouse input, position tracking, and visual representation.
"""

# DEPRECATED: Legacy pygame component retained for reference only.
# Not used by the Qt Quick (QML) application launched via `src.qt_qml_app`.
# Functionality has modern equivalents in QML components under `qml/components/`.

import pygame
import math
from typing import Tuple, Optional, Callable
from .config import ControllerConfig


class VirtualJoystick:
    """
    A virtual joystick that can be controlled with mouse input.
    
    This class handles the visual representation and input processing
    for a virtual joystick control, including dead zones and sensitivity curves.
    """
    
    def __init__(self, center_x: int, center_y: int, radius: int, 
                 config: ControllerConfig, joystick_id: str):
        """
        Initialize the virtual joystick.
        
        Args:
            center_x: X coordinate of joystick center
            center_y: Y coordinate of joystick center
            radius: Radius of the joystick area
            config: Configuration manager instance
            joystick_id: Identifier for this joystick ("left" or "right")
        """
        self.center_x = center_x
        self.center_y = center_y
        self.radius = radius
        self.config = config
        self.joystick_id = joystick_id
        
        # Current position (normalized -1.0 to 1.0)
        self.x_pos = 0.0
        self.y_pos = 0.0
        
        # Raw position (before sensitivity curves)
        self.raw_x = 0.0
        self.raw_y = 0.0
        
        # Lock states for axes
        self.x_locked = False
        self.y_locked = False
        
        # Mouse interaction state
        self.is_dragging = False
        self.is_hovered = False
        
        # Auto-centering when not being dragged
        self.auto_center = True
        
        # Mouse drag reference point
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.initial_x_pos = 0.0
        self.initial_y_pos = 0.0
        
        # Smoothing for stability
        self.prev_x = 0.0
        self.prev_y = 0.0
        
        # Callbacks for value changes
        self.on_value_changed: Optional[Callable[[float, float], None]] = None
    
    def handle_mouse_down(self, mouse_pos: Tuple[int, int]) -> bool:
        """
        Handle mouse button down event.
        
        Args:
            mouse_pos: Mouse position (x, y)
            
        Returns:
            True if the joystick handled the event
        """
        mouse_x, mouse_y = mouse_pos
        distance = math.sqrt((mouse_x - self.center_x) ** 2 + (mouse_y - self.center_y) ** 2)
        
        if distance <= self.radius:
            self.is_dragging = True
            # Store the drag start position and current joystick position
            self.drag_start_x = mouse_x
            self.drag_start_y = mouse_y
            self.initial_x_pos = self.x_pos
            self.initial_y_pos = self.y_pos
            return True
        
        return False
    
    def handle_mouse_up(self, mouse_pos: Tuple[int, int]) -> None:
        """
        Handle mouse button up event.
        
        Args:
            mouse_pos: Mouse position (x, y)
        """
        if self.is_dragging:
            self.is_dragging = False
            # Don't auto-center - let the user control when to reset
    
    def handle_mouse_motion(self, mouse_pos: Tuple[int, int]) -> None:
        """
        Handle mouse motion event.
        
        Args:
            mouse_pos: Mouse position (x, y)
        """
        if self.is_dragging:
            mouse_x, mouse_y = mouse_pos
            # Calculate relative movement from drag start position
            delta_x = mouse_x - self.drag_start_x
            delta_y = mouse_y - self.drag_start_y
            
            # Apply movement to initial position (with proper direction)
            # Use effective radius so the knob edge cannot cross the boundary circle
            knob_radius = self.config.get_scaled_int(10)
            effective_radius = max(1, self.radius - knob_radius)
            new_x = self.initial_x_pos + (delta_x / effective_radius)
            new_y = self.initial_y_pos + (delta_y / effective_radius)  # Normal Y direction
            
            # Clamp to joystick bounds
            distance = math.sqrt(new_x ** 2 + new_y ** 2)
            if distance > 1.0:
                new_x = new_x / distance
                new_y = new_y / distance
            
            # Update positions
            if not self.x_locked:
                self.x_pos = new_x
                self.raw_x = new_x
            if not self.y_locked:
                self.y_pos = new_y
                self.raw_y = new_y
            
            # Notify callback
            if self.on_value_changed:
                self.on_value_changed(self.x_pos, self.y_pos)
    
    def _update_position_from_offset(self) -> None:
        """Update joystick position based on mouse offset."""
        # This method is no longer used with the new drag behavior
        pass
    
    def _apply_processing(self) -> None:
        """Apply sensitivity curves, dead zones, and smoothing."""
        # Apply sensitivity curves. shape_stick replaced the removed per-axis
        # apply_sensitivity_curve: one radial pass over the vector rather than
        # two independent ones, so the dead region is a circle. The inversion
        # keys that method used to apply are handled here.
        processed_x, processed_y = self.config.shape_stick(self.raw_x, self.raw_y, self.joystick_id)
        if self.config.get(f"joysticks.{self.joystick_id}.invert_x", False):
            processed_x = -processed_x
        if self.config.get(f"joysticks.{self.joystick_id}.invert_y", False):
            processed_y = -processed_y
        
        # Apply smoothing if enabled
        if self.config.get("safety.enable_smoothing", True):
            smoothing_factor = self.config.get("safety.smoothing_factor", 0.1)
            processed_x = self.prev_x + (processed_x - self.prev_x) * smoothing_factor
            processed_y = self.prev_y + (processed_y - self.prev_y) * smoothing_factor
        
        # Update positions
        old_x, old_y = self.x_pos, self.y_pos
        self.x_pos = processed_x
        self.y_pos = processed_y
        self.prev_x = processed_x
        self.prev_y = processed_y
        
        # Trigger callback if values changed significantly
        if (abs(self.x_pos - old_x) > 0.001 or abs(self.y_pos - old_y) > 0.001) and self.on_value_changed:
            self.on_value_changed(self.x_pos, self.y_pos)
    
    def set_position(self, x: float, y: float) -> None:
        """
        Set joystick position programmatically.
        
        Args:
            x: X position (-1.0 to 1.0)
            y: Y position (-1.0 to 1.0)
        """
        if not self.x_locked:
            self.raw_x = max(-1.0, min(1.0, x))
        if not self.y_locked:
            self.raw_y = max(-1.0, min(1.0, y))
        
        self._apply_processing()
    
    def center(self) -> None:
        """Center the joystick position."""
        if not self.x_locked:
            self.raw_x = 0.0
        if not self.y_locked:
            self.raw_y = 0.0
        
        self.mouse_offset_x = 0
        self.mouse_offset_y = 0
        self._apply_processing()
    
    def lock_axis(self, axis: str, locked: bool) -> None:
        """
        Lock or unlock an axis.
        
        Args:
            axis: Axis to lock ("x" or "y")
            locked: Whether to lock the axis
        """
        if axis.lower() == "x":
            self.x_locked = locked
            if locked:
                self.raw_x = 0.0
        elif axis.lower() == "y":
            self.y_locked = locked
            if locked:
                self.raw_y = 0.0
        
        self._apply_processing()
    
    def get_position(self) -> Tuple[float, float]:
        """
        Get current processed joystick position.
        
        Returns:
            Tuple of (x, y) positions (-1.0 to 1.0)
        """
        return self.x_pos, self.y_pos
    
    def get_raw_position(self) -> Tuple[float, float]:
        """
        Get current raw joystick position (before processing).
        
        Returns:
            Tuple of (x, y) raw positions (-1.0 to 1.0)
        """
        return self.raw_x, self.raw_y
    
    def get_display_position(self) -> Tuple[int, int]:
        """
        Get position for display purposes.
        
        Returns:
            Tuple of (x, y) pixel coordinates for drawing
        """
        # Place the knob center using the effective radius so its edge stays within the boundary
        knob_radius = self.config.get_scaled_int(10)
        effective_radius = max(0, self.radius - knob_radius)
        display_x = self.center_x + int(self.raw_x * effective_radius)
        display_y = self.center_y - int(self.raw_y * effective_radius)  # Invert Y for display
        return display_x, display_y
    
    def update(self) -> None:
        """Update joystick state - auto-center if not being dragged."""
        if not self.is_dragging:
            # Gradually move towards center
            if abs(self.x_pos) > 0.01 or abs(self.y_pos) > 0.01:  # Small deadzone to prevent jitter
                self.x_pos *= 0.92  # Decay factor - adjust for centering speed
                self.y_pos *= 0.92
                self.raw_x *= 0.92
                self.raw_y *= 0.92
                
                if abs(self.x_pos) < 0.01:
                    self.x_pos = 0.0
                    self.raw_x = 0.0
                if abs(self.y_pos) < 0.01:
                    self.y_pos = 0.0
                    self.raw_y = 0.0
                
                # Notify callback of position change
                if self.on_value_changed:
                    self.on_value_changed(self.x_pos, self.y_pos)
    
    def draw(self, surface: pygame.Surface) -> None:
        """Draw the joystick on the surface."""
        # Draw boundary (circle)
        boundary_color = (100, 150, 255)
        pygame.draw.circle(surface, (15, 30, 60), (self.center_x, self.center_y), self.radius)
        pygame.draw.circle(surface, boundary_color, (self.center_x, self.center_y), self.radius, 3)
        
        # Draw center lines that move with the knob (clipped to circle)
        center_color = (50, 100, 200)
        knob_radius = self.config.get_scaled_int(10)
        effective_radius = max(0, self.radius - knob_radius)
        knob_x = self.center_x + int(self.x_pos * effective_radius)
        knob_y = self.center_y + int(self.y_pos * effective_radius)
        
        # Calculate intersection points with circle for horizontal line
        import math
        # Horizontal line at knob_y
        dy = knob_y - self.center_y
        if abs(dy) < self.radius:
            dx = math.sqrt(self.radius * self.radius - dy * dy)
            h_start_x = self.center_x - dx
            h_end_x = self.center_x + dx
            pygame.draw.line(surface, center_color, 
                            (int(h_start_x), knob_y), 
                            (int(h_end_x), knob_y), 1)
        
        # Calculate intersection points with circle for vertical line
        # Vertical line at knob_x
        dx = knob_x - self.center_x
        if abs(dx) < self.radius:
            dy = math.sqrt(self.radius * self.radius - dx * dx)
            v_start_y = self.center_y - dy
            v_end_y = self.center_y + dy
            pygame.draw.line(surface, center_color, 
                            (knob_x, int(v_start_y)), 
                            (knob_x, int(v_end_y)), 1)
        
        # Calculate knob position
        knob_x = self.center_x + int(self.x_pos * effective_radius)
        knob_y = self.center_y + int(self.y_pos * effective_radius)
        
        # Draw knob (circle)
        knob_color = (100, 150, 255)
        pygame.draw.circle(surface, knob_color, (knob_x, knob_y), knob_radius)
        pygame.draw.circle(surface, (150, 200, 255), (knob_x, knob_y), knob_radius, 2)

    def is_point_inside(self, point: Tuple[int, int]) -> bool:
        """
        Check if a point is inside the joystick area.
        
        Args:
            point: Point to check (x, y)
            
        Returns:
            True if point is inside joystick area
        """
        x, y = point
        distance = math.sqrt((x - self.center_x) ** 2 + (y - self.center_y) ** 2)
        return distance <= self.radius
