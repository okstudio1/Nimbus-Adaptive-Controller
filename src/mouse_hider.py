"""
Controller Mode Enforcement for Nimbus Adaptive Controller.

Makes games voluntarily release the mouse by keeping them in "controller mode."

Most modern games have dual input detection:
  - Mouse/keyboard input detected → M/KB mode → cursor captured, mouse prompts
  - Controller input detected → Controller mode → cursor RELEASED, gamepad prompts

Since Nimbus Adaptive Controller already creates a virtual Xbox 360 controller via ViGEm,
we can exploit this by:
  1. Sending a constant stream of controller keep-alive signals (tiny stick
     oscillations below deadzone) so the game stays in controller mode
  2. Optionally using a WH_MOUSE_LL hook to detect mouse-over-game events and
     immediately counter them with a burst of controller input
  3. Combining with ClipCursor(NULL) release so the cursor stays free

This approach makes the game VOLUNTARILY stop capturing the mouse, rather than
fighting ClipCursor in a race condition.

Pure ctypes implementation, no extra dependencies; the pad object comes from src/padbus_client.py.

See: research/in-progress/controller-mode-enforcement.md
"""
from __future__ import annotations

import sys
import math
import threading
import time
from typing import Any, Callable, Optional

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Low-level mouse hook
    WH_MOUSE_LL = 14
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONDOWN = 0x0207
    WM_MBUTTONUP = 0x0208
    WM_MOUSEWHEEL = 0x020A

    # For mouse_event blocking
    MOUSEEVENTF_MOVE = 0x0001

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long,    # return
        ctypes.c_int,     # nCode
        wintypes.WPARAM,  # wParam
        wintypes.LPARAM,  # lParam
    )

    # Declare argtypes so ctypes handles 64-bit LPARAM correctly
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,   # hhk
        ctypes.c_int,     # nCode
        wintypes.WPARAM,  # wParam
        wintypes.LPARAM,  # lParam
    ]
    user32.CallNextHookEx.restype = ctypes.c_long

    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,     # idHook
        HOOKPROC,         # lpfn
        wintypes.HINSTANCE,  # hMod
        wintypes.DWORD,   # dwThreadId
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK

    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL

    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_controller_mode_active = False
_controller_mode_lock = threading.Lock()

# Keep-alive pulse thread
_pulse_thread: Optional[threading.Thread] = None
_pulse_gamepad: Any = None  # ViGEm gamepad reference
_vigem_iface_ref: Any = None  # ViGEmInterface reference (for current_values save/restore)
_pulse_hz: int = 30
_pulse_game_hwnd: int = 0

# Mouse hook thread
_hook_thread: Optional[threading.Thread] = None
_hook_handle: Any = None
_hook_callback_ref: Any = None  # prevent GC of the ctypes callback
_hook_stop_event = threading.Event()

# Stats for debugging
_stats_lock = threading.Lock()
_stats = {
    "pulses_sent": 0,
    "mouse_events_over_game": 0,
    "controller_bursts_sent": 0,
    "mode_started_at": 0.0,
}

# Nimbus window handle (to identify "our" window and let mouse through)
_nimbus_hwnd: int = 0

# Cursor parking position (screen coords) — set when controller mode starts
_park_x: int = 0
_park_y: int = 0

# Callback for status changes
_status_callback: Optional[Callable[[bool], None]] = None

# Safety: maximum duration before auto-stop (seconds). 0 = no limit.
_MAX_DURATION: float = 0

# Emergency hotkey state (Ctrl+Alt+F12)
_hotkey_thread: Optional[threading.Thread] = None
_hotkey_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Controller keep-alive pulse
# ---------------------------------------------------------------------------

def _pulse_loop() -> None:
    """
    High-frequency controller keep-alive loop.

    Sends tiny analog stick oscillations through ViGEm that are below any
    game's deadzone (typically 0.1-0.3) but large enough to register as
    "controller input received" in the game's input detection system.

    This keeps the game in controller mode so it voluntarily releases
    the mouse cursor.
    """
    global _park_x, _park_y

    # Elevate thread priority for consistent timing
    try:
        THREAD_PRIORITY_ABOVE_NORMAL = 1
        kernel32.SetThreadPriority(
            kernel32.GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL)
    except Exception:
        pass

    gamepad = _pulse_gamepad
    if gamepad is None:
        print("[mouse_hider] No gamepad reference — pulse thread exiting")
        return

    interval = 1.0 / max(1, _pulse_hz)
    tick = 0
    game_hwnd = _pulse_game_hwnd

    print(f"[mouse_hider] Pulse loop started at {_pulse_hz}Hz "
          f"(interval={interval*1000:.1f}ms)")

    # Initial burst: send several strong controller signals to force switch
    _send_controller_burst(gamepad, count=10, delay=0.016)

    while _controller_mode_active:
        try:
            # Oscillate left stick in a tiny circle below deadzone
            # Amplitude 0.08 is below most games' deadzones (0.15-0.3)
            # but large enough that UE/Unity input detection registers it
            # as "controller input changed" via XInputGetState packet counter
            angle = (tick % 60) * (2.0 * math.pi / 60.0)
            micro_x = 0.08 * math.cos(angle)
            micro_y = 0.08 * math.sin(angle)

            # Save current axis state so macro/joystick values aren't clobbered
            iface = _vigem_iface_ref
            if iface is not None:
                saved_lx = iface.current_values.get('left_x', 0.0)
                saved_ly = iface.current_values.get('left_y', 0.0)
                saved_rx = iface.current_values.get('right_x', 0.0)
                saved_ry = iface.current_values.get('right_y', 0.0)
            else:
                saved_lx = saved_ly = saved_rx = saved_ry = 0.0

            gamepad.left_joystick_float(saved_lx + micro_x, saved_ly + micro_y)
            gamepad.update()

            # Restore the actual axis state immediately
            if iface is not None:
                gamepad.left_joystick_float(saved_lx, saved_ly)
                gamepad.right_joystick_float(saved_rx, saved_ry)
                gamepad.update()

            with _stats_lock:
                _stats["pulses_sent"] += 1

            tick += 1

            # Also release ClipCursor periodically (every 4th pulse)
            if tick % 4 == 0 and sys.platform == "win32":
                if game_hwnd:
                    _release_clip_with_attach(game_hwnd)
                else:
                    ctypes.windll.user32.ClipCursor(None)

            # Refresh parking position & park cursor if over game (every 8th pulse)
            if tick % 8 == 0 and sys.platform == "win32":
                try:
                    _park_x, _park_y = _compute_park_position()
                    pt = wintypes.POINT()
                    if user32.GetCursorPos(ctypes.byref(pt)):
                        if _is_point_in_game_window(pt.x, pt.y) and not _is_point_in_nimbus_window(pt.x, pt.y):
                            user32.SetCursorPos(_park_x, _park_y)
                except Exception:
                    pass

        except Exception as e:
            if _controller_mode_active:
                print(f"[mouse_hider] Pulse error: {e}")

        time.sleep(interval)

    # Clean up: return stick to exact center
    try:
        gamepad.left_joystick_float(0.0, 0.0)
        gamepad.update()
    except Exception:
        pass

    print("[mouse_hider] Pulse loop stopped")


def _send_controller_burst(gamepad: Any, count: int = 10,
                           delay: float = 0.016) -> None:
    """
    Send a rapid burst of controller inputs to force the game into
    controller mode.

    Uses ABOVE-deadzone stick deflections (0.5) to reliably trigger the
    input-mode switch in Unreal Engine and other games with large deadzones
    (typically 0.2-0.3). Also presses the A button briefly — many games
    (including UE titles) detect button presses as unambiguous controller
    input and immediately switch input mode.
    """
    try:
        # Phase 1: Strong stick deflection to exceed any deadzone
        for i in range(count):
            val = 0.5 * (1.0 if i % 2 == 0 else -1.0)
            gamepad.left_joystick_float(val, 0.0)
            gamepad.update()
            time.sleep(delay)

        # Phase 2: Button press — unambiguous "controller is being used"
        try:
            from .padbus_client import XUSB_BUTTON
            gamepad.press_button(button=XUSB_BUTTON.XUSB_GAMEPAD_A)
            gamepad.update()
            time.sleep(0.05)
            gamepad.release_button(button=XUSB_BUTTON.XUSB_GAMEPAD_A)
            gamepad.update()
        except Exception:
            pass  # no button constants available; the stick burst alone may suffice

        # Return to center
        gamepad.left_joystick_float(0.0, 0.0)
        gamepad.update()

        with _stats_lock:
            _stats["controller_bursts_sent"] += 1

        print(f"[mouse_hider] Sent {count}-pulse controller burst (amplitude=0.5 + A press)")
    except Exception as e:
        print(f"[mouse_hider] Burst error: {e}")


def _release_clip_with_attach(game_hwnd: int) -> None:
    """Release ClipCursor with thread-input attachment to game."""
    try:
        our_tid = kernel32.GetCurrentThreadId()
        game_tid = user32.GetWindowThreadProcessId(game_hwnd, None)
        if game_tid and game_tid != our_tid:
            user32.AttachThreadInput(our_tid, game_tid, True)
        ctypes.windll.user32.ClipCursor(None)
        if game_tid and game_tid != our_tid:
            user32.AttachThreadInput(our_tid, game_tid, False)
    except Exception:
        ctypes.windll.user32.ClipCursor(None)


# ---------------------------------------------------------------------------
# Mouse-over-game detection hook (WH_MOUSE_LL)
# ---------------------------------------------------------------------------

def _is_point_in_game_window(x: int, y: int) -> bool:
    """Check if a screen coordinate falls within the game window."""
    game_hwnd = _pulse_game_hwnd
    if not game_hwnd:
        return False
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(game_hwnd, ctypes.byref(rect))
        return (rect.left <= x <= rect.right and
                rect.top <= y <= rect.bottom)
    except Exception:
        return False


def _is_point_in_nimbus_window(x: int, y: int) -> bool:
    """Check if a screen coordinate falls within the Nimbus window."""
    if not _nimbus_hwnd:
        return False
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(_nimbus_hwnd, ctypes.byref(rect))
        return (rect.left <= x <= rect.right and
                rect.top <= y <= rect.bottom)
    except Exception:
        return False


def _compute_park_position() -> tuple:
    """
    Compute cursor parking position.

    Prefers the center of the Nimbus window so the cursor stays visible
    and accessible. Falls back to the top-left screen corner (1, 1).
    """
    if _nimbus_hwnd:
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(_nimbus_hwnd, ctypes.byref(rect))
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            if cx > 0 or cy > 0:
                return cx, cy
        except Exception:
            pass
    return 1, 1


def _low_level_mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
    """
    WH_MOUSE_LL callback. Runs for every system-wide mouse event.

    Strategy:
      - When cursor is over the NIMBUS window: pass through so the user
        can interact with Nimbus widgets normally.
      - When cursor is over the GAME window: SUPPRESS WM_MOUSEMOVE so
        the game camera does not respond to mouse movement. Clicks are
        always passed through.
      - All other events pass through normally.

    This requires Raw Input: OFF in Minecraft's mouse settings, because
    WH_MOUSE_LL cannot intercept WM_INPUT raw events.

    Safe because:
      - ctypes argtypes are declared (no LPARAM overflow crash)
      - Emergency kill: Ctrl+Alt+F12 stops controller mode instantly
    """
    if n_code >= 0 and _controller_mode_active:
        try:
            ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            px, py = ms.pt.x, ms.pt.y

            if w_param == WM_MOUSEMOVE:
                # Always allow movement over the Nimbus window
                if _is_point_in_nimbus_window(px, py):
                    return user32.CallNextHookEx(
                        _hook_handle, n_code, w_param, l_param)

                # Suppress mouse movement over the game window
                # so the camera cannot move. Clicks still pass through.
                if _is_point_in_game_window(px, py):
                    with _stats_lock:
                        _stats["mouse_events_over_game"] += 1
                    # Counter with controller input to keep game in controller mode
                    gamepad = _pulse_gamepad
                    if gamepad:
                        try:
                            iface = _vigem_iface_ref
                            slx = iface.current_values.get('left_x', 0.0) if iface else 0.0
                            sly = iface.current_values.get('left_y', 0.0) if iface else 0.0
                            srx = iface.current_values.get('right_x', 0.0) if iface else 0.0
                            sry = iface.current_values.get('right_y', 0.0) if iface else 0.0
                            gamepad.left_joystick_float(slx + 0.04, sly)
                            gamepad.update()
                            gamepad.left_joystick_float(slx, sly)
                            gamepad.right_joystick_float(srx, sry)
                            gamepad.update()
                        except Exception:
                            pass
                    # Suppress — game camera stays still
                    return 1
        except Exception:
            pass

    return user32.CallNextHookEx(_hook_handle, n_code, w_param, l_param)


def _hook_thread_func() -> None:
    """
    Dedicated thread for the WH_MOUSE_LL hook.
    Requires a Win32 message loop to function.
    """
    global _hook_handle, _hook_callback_ref

    try:
        _hook_callback_ref = HOOKPROC(_low_level_mouse_proc)
        _hook_handle = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            _hook_callback_ref,
            None,  # hMod — NULL for WH_MOUSE_LL
            0      # dwThreadId — 0 = global
        )

        if not _hook_handle:
            err = ctypes.get_last_error()
            print(f"[mouse_hider] Failed to install mouse hook (error {err})")
            return

        print(f"[mouse_hider] Mouse hook installed (handle={_hook_handle})")

        # Run message loop until stop event
        msg = wintypes.MSG()
        while not _hook_stop_event.is_set():
            # PeekMessage with PM_REMOVE — non-blocking check
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.001)  # Yield CPU when no messages

    except Exception as e:
        print(f"[mouse_hider] Hook thread error: {e}")
    finally:
        if _hook_handle:
            user32.UnhookWindowsHookEx(_hook_handle)
            _hook_handle = None
            _hook_callback_ref = None
            print("[mouse_hider] Mouse hook removed")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_controller_mode(
    gamepad: Any,
    game_hwnd: int = 0,
    nimbus_hwnd: int = 0,
    pulse_hz: int = 30,
    use_mouse_hook: bool = True,
    callback: Optional[Callable[[bool], None]] = None,
    vigem_interface: Any = None,
) -> bool:
    """
    Start Controller Mode Enforcement.

    This makes the game think only a controller is connected by:
      1. Sending a burst of controller inputs to force controller mode
      2. Running a continuous keep-alive pulse at the specified frequency
      3. Optionally installing a mouse hook that detects mouse-over-game
         and immediately counters with controller input

    The game will voluntarily release the mouse cursor because it thinks
    the player is using a gamepad, not a mouse.

    Args:
        gamepad: ViGEm gamepad object (from vigem_interface.py).
        game_hwnd: HWND of the game window (for targeted release + detection).
        nimbus_hwnd: HWND of the Nimbus window (to allow mouse passthrough).
        pulse_hz: Keep-alive frequency in Hz (10-60 recommended).
        use_mouse_hook: Install WH_MOUSE_LL for mouse-over-game detection.
        callback: Optional status callback(bool).

    Returns:
        True if started successfully.
    """
    global _controller_mode_active, _pulse_thread, _pulse_gamepad, _vigem_iface_ref
    global _pulse_hz, _pulse_game_hwnd, _nimbus_hwnd
    global _hook_thread, _status_callback
    global _park_x, _park_y

    if sys.platform != "win32":
        print("[mouse_hider] Controller mode only available on Windows")
        return False

    with _controller_mode_lock:
        if _controller_mode_active:
            print("[mouse_hider] Controller mode already active")
            return True

        if gamepad is None:
            print("[mouse_hider] No gamepad provided — cannot start")
            return False

        _pulse_gamepad = gamepad
        _vigem_iface_ref = vigem_interface
        _pulse_game_hwnd = game_hwnd
        _nimbus_hwnd = nimbus_hwnd
        _pulse_hz = max(5, min(120, pulse_hz))
        _status_callback = callback
        _controller_mode_active = True

        # Compute cursor parking position (center of Nimbus window)
        _park_x, _park_y = _compute_park_position()
        print(f"[mouse_hider] Cursor parking position: ({_park_x}, {_park_y})")

        # Reset stats
        with _stats_lock:
            _stats["pulses_sent"] = 0
            _stats["mouse_events_over_game"] = 0
            _stats["controller_bursts_sent"] = 0
            _stats["mode_started_at"] = time.time()

    # Start keep-alive pulse thread
    _pulse_thread = threading.Thread(
        target=_pulse_loop, daemon=True, name="ControllerPulse")
    _pulse_thread.start()

    # Start mouse hook thread (optional but recommended)
    if use_mouse_hook and game_hwnd:
        _hook_stop_event.clear()
        _hook_thread = threading.Thread(
            target=_hook_thread_func, daemon=True, name="MouseHook")
        _hook_thread.start()

    # Start emergency hotkey listener (Ctrl+Alt+F12 kills controller mode)
    _start_emergency_hotkey()

    print(f"[mouse_hider] Controller Mode started "
          f"(game_hwnd={game_hwnd or 'none'}, pulse={_pulse_hz}Hz, "
          f"hook={'on' if use_mouse_hook and game_hwnd else 'off'})")
    print(f"[mouse_hider] Emergency stop: press Ctrl+Alt+F12")

    if callback:
        callback(True)

    return True


def stop_controller_mode() -> None:
    """
    Stop Controller Mode Enforcement.

    Stops the keep-alive pulse and removes the mouse hook.
    The game will revert to its normal input detection behavior.
    """
    global _controller_mode_active, _pulse_thread, _hook_thread
    global _pulse_gamepad, _status_callback

    with _controller_mode_lock:
        if not _controller_mode_active:
            return
        _controller_mode_active = False

    # Stop emergency hotkey
    _stop_emergency_hotkey()

    # Stop mouse hook
    _hook_stop_event.set()
    if _hook_thread and _hook_thread.is_alive():
        _hook_thread.join(timeout=1.0)
    _hook_thread = None

    # Stop pulse thread
    if _pulse_thread and _pulse_thread.is_alive():
        _pulse_thread.join(timeout=1.0)
    _pulse_thread = None

    # Print stats
    with _stats_lock:
        elapsed = time.time() - _stats["mode_started_at"]
        print(f"[mouse_hider] Controller Mode stopped after {elapsed:.1f}s")
        print(f"  Pulses sent: {_stats['pulses_sent']}")
        print(f"  Mouse events over game: {_stats['mouse_events_over_game']}")
        print(f"  Controller bursts: {_stats['controller_bursts_sent']}")

    cb = _status_callback
    _status_callback = None
    _pulse_gamepad = None

    if cb:
        cb(False)


def is_controller_mode_active() -> bool:
    """Check if Controller Mode Enforcement is currently running."""
    return _controller_mode_active


def send_controller_burst(gamepad: Any = None, count: int = 15) -> None:
    """
    Send a one-shot burst of controller signals.

    Useful for forcing a game into controller mode without enabling
    the continuous keep-alive. Call this when the user first launches
    a game or when the game switches back to M/KB mode.

    Args:
        gamepad: ViGEm gamepad (uses the active one if None).
        count: Number of pulses in the burst.
    """
    gp = gamepad or _pulse_gamepad
    if gp is None:
        print("[mouse_hider] No gamepad available for burst")
        return
    _send_controller_burst(gp, count=count)


def get_controller_mode_stats() -> dict:
    """Get statistics about the current/last Controller Mode session."""
    with _stats_lock:
        result = _stats.copy()
    result["active"] = _controller_mode_active
    result["pulse_hz"] = _pulse_hz
    result["game_hwnd"] = _pulse_game_hwnd
    return result


# ---------------------------------------------------------------------------
# Emergency hotkey (Ctrl+Alt+F12)
# ---------------------------------------------------------------------------

def _emergency_hotkey_loop() -> None:
    """
    Poll for Ctrl+Alt+F12 to instantly kill controller mode.

    Uses GetAsyncKeyState so it works even when the app doesn't have focus.
    This is a last-resort safety mechanism in case something goes wrong.
    """
    if sys.platform != "win32":
        return

    VK_F12 = 0x7B
    VK_CONTROL = 0x11
    VK_MENU = 0x12  # Alt key

    print("[mouse_hider] Emergency hotkey listener active (Ctrl+Alt+F12)")

    while not _hotkey_stop_event.is_set():
        try:
            ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
            alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
            f12 = user32.GetAsyncKeyState(VK_F12) & 0x8000

            if ctrl and alt and f12:
                print("[mouse_hider] *** EMERGENCY STOP: Ctrl+Alt+F12 pressed ***")
                # Force-stop without going through the lock
                global _controller_mode_active
                _controller_mode_active = False
                # Also unhook immediately
                _hook_stop_event.set()
                # Release any ClipCursor
                try:
                    ctypes.windll.user32.ClipCursor(None)
                except Exception:
                    pass
                break
        except Exception:
            pass

        _hotkey_stop_event.wait(timeout=0.05)  # Poll at 20Hz

    print("[mouse_hider] Emergency hotkey listener stopped")


def _start_emergency_hotkey() -> None:
    """Start the emergency hotkey listener thread."""
    global _hotkey_thread
    _hotkey_stop_event.clear()
    _hotkey_thread = threading.Thread(
        target=_emergency_hotkey_loop, daemon=True, name="EmergencyHotkey")
    _hotkey_thread.start()


def _stop_emergency_hotkey() -> None:
    """Stop the emergency hotkey listener thread."""
    global _hotkey_thread
    _hotkey_stop_event.set()
    if _hotkey_thread and _hotkey_thread.is_alive():
        _hotkey_thread.join(timeout=1.0)
    _hotkey_thread = None
