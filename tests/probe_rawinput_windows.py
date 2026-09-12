"""
Windows Raw Input capture probe (throwaway, not Nimbus code).

Answers one question on the machine it runs on: which user-mode measures stop
a foreground Raw Input consumer (a game) from receiving ``WM_INPUT`` mouse
deltas, and what does a Nimbus-like window still receive under each measure?

This is the Windows counterpart of ``tests/probe_evdev_grab.py`` on the Linux
branch. There the grab is one ``ioctl``; here the point is to prove, with
numbers, that no user-mode call has the same effect.

Roles (one process each)
------------------------
``--role game``
    A window that registers for HID mouse Raw Input the way a game does,
    counts ``WM_INPUT`` / ``WM_MOUSEMOVE`` / focus and activation messages,
    and rewrites a JSON snapshot every 100 ms.  ``--target hwnd|null`` and
    ``--sink none|inputsink|exinputsink`` select the registration style.
``--role blocker``
    Calls ``BlockInput(TRUE)`` for ``--seconds`` and then releases it.
``--role tester`` (default)
    Spawns the game in each registration style, runs the scenario matrix,
    injects mouse motion with ``SendInput``, and prints a table.

Scenarios
---------
baseline            Game is foreground, nothing else.
ll_hook_block_all   A ``WH_MOUSE_LL`` hook returns 1 for every mouse event
                    (the strongest form of what ``mouse_hider.py`` does).
panel_foreground    A Nimbus-like window takes the foreground.
attach_setfocus     ``AttachThreadInput`` to the game's queue, then
                    ``SetFocus`` on the panel (the classic focus trick).
blockinput          Another process holds ``BlockInput(TRUE)``.

Injected motion goes through the same ``win32k`` routing as physical motion
(foreground rules, ``RIDEV_INPUTSINK``, ``WH_MOUSE_LL``), so it is a valid
stand-in for those questions.  It is NOT a stand-in for anything below
``win32k``: a kernel filter would drop physical packets and leave injected
ones alone.  ``WM_INPUT`` from ``SendInput`` arrives with a null device handle.

Run::

    python tests/probe_rawinput_windows.py

Exit code 0 when the matrix ran; the table is the result.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional

if sys.platform != "win32":
    sys.exit("Windows only")

# use_last_error captures GetLastError right after each call, before Python's
# own allocations can clobber it; read it back with ctypes.get_last_error().
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- Win32 constants ------------------------------------------------------
WM_DESTROY = 0x0002
WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_CLOSE = 0x0010
WM_ACTIVATEAPP = 0x001C
WM_INPUT = 0x00FF
WM_MOUSEMOVE = 0x0200
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_APP_ATTACH_FOCUS = WM_APP + 1
WM_APP_DETACH = WM_APP + 2

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008

RIDEV_REMOVE = 0x00000001
RIDEV_NOLEGACY = 0x00000030
RIDEV_INPUTSINK = 0x00000100
RIDEV_EXINPUTSINK = 0x00001000
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x0001
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02

WH_MOUSE_LL = 14
HC_ACTION = 0

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12

PM_REMOVE = 0x0001
IDC_ARROW = 32512
COLOR_WINDOW = 5

# ---- Win32 types ----------------------------------------------------------
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetFocus.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetActiveWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.BlockInput.argtypes = [wintypes.BOOL]
user32.BlockInput.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadCursorW.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

try:
    user32.SetProcessDPIAware()
except Exception:
    pass


# ---- helpers --------------------------------------------------------------
def _hwnd_int(h) -> int:
    return int(h) if h else 0


def press_alt() -> None:
    """Tap Alt so this process counts as having received the last input event.

    That satisfies the ``SetForegroundWindow`` ownership rules.
    """
    down = INPUT(type=INPUT_KEYBOARD)
    down.ki = KEYBDINPUT(VK_MENU, 0, 0, 0, None)
    up = INPUT(type=INPUT_KEYBOARD)
    up.ki = KEYBDINPUT(VK_MENU, 0, KEYEVENTF_KEYUP, 0, None)
    arr = (INPUT * 2)(down, up)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def bring_to_front(hwnd: int, tries: int = 6) -> bool:
    for _ in range(tries):
        if _hwnd_int(user32.GetForegroundWindow()) == hwnd:
            return True
        press_alt()
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
    return _hwnd_int(user32.GetForegroundWindow()) == hwnd


def inject_motion(count: int, step: int, spacing_s: float = 0.004) -> int:
    """Inject ``count`` relative moves alternating +step/-step on both axes.

    The cursor ends near where it started; the raw delta magnitude sums to
    ``2 * step * count``.  Returns the number of events Windows accepted.
    """
    sent = 0
    for i in range(count):
        d = step if i % 2 == 0 else -step
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(d, d, 0, MOUSEEVENTF_MOVE, 0, None)
        sent += user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(spacing_s)
    return sent


def move_cursor_setpos(count: int, step: int, spacing_s: float = 0.004) -> int:
    """Move the cursor with ``SetCursorPos`` in the same +step/-step pattern.

    ``SetCursorPos`` changes the cursor position without generating an input
    event, so this measures what a Raw Input consumer, a ``WH_MOUSE_LL`` hook
    and ``WM_MOUSEMOVE`` see when Nimbus relays physical motion this way (the
    cursor-relay model in WINDOWS_MOUSE_FILTER_PLAN.md). Returns the number of
    calls that succeeded.
    """
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    x, y = pt.x, pt.y
    made = 0
    for i in range(count):
        d = step if i % 2 == 0 else -step
        x += d
        y += d
        if user32.SetCursorPos(x, y):
            made += 1
        time.sleep(spacing_s)
    return made


def window_center(hwnd: int) -> tuple:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


def register_raw_mouse(hwnd: Optional[int], flags: int) -> bool:
    rid = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, flags, hwnd if hwnd else None)
    ok = user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
    if not ok:
        err = ctypes.get_last_error()
        print(f"RegisterRawInputDevices failed: {err}")
    return bool(ok)


def parse_raw_mouse(lparam: int) -> Optional[tuple]:
    """Return ``(flags, dx, dy)`` for a ``WM_INPUT`` mouse packet, else None."""
    handle = ctypes.c_void_p(lparam & (2 ** 64 - 1))
    size = wintypes.UINT(0)
    user32.GetRawInputData(handle, RID_INPUT, None, ctypes.byref(size), 24)
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    got = user32.GetRawInputData(handle, RID_INPUT, buf, ctypes.byref(size), 24)
    if got != size.value or size.value < 48:
        return None
    dw_type = struct.unpack_from("<I", buf, 0)[0]
    if dw_type != RIM_TYPEMOUSE:
        return None
    # RAWINPUTHEADER is 24 bytes on x64; RAWMOUSE follows: usFlags@0, union@4,
    # ulRawButtons@8, lLastX@12, lLastY@16, ulExtraInformation@20.
    flags = struct.unpack_from("<H", buf, 24)[0]
    dx, dy = struct.unpack_from("<ii", buf, 36)
    return flags, dx, dy


class _Counters:
    KEYS = ("input_events", "input_abs", "input_absolute_events", "mousemove", "setfocus",
            "killfocus", "activate", "activate_inactive", "activateapp", "activateapp_false")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.v: Dict[str, int] = {k: 0 for k in self.KEYS}

    def bump(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.v[key] += n

    def snapshot(self) -> Dict[str, int]:
        with self.lock:
            return dict(self.v)


def make_wndproc(counters: _Counters, extra: Optional[Callable[[int, int, int, int], Optional[int]]] = None) -> WNDPROC:
    def proc(hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            parsed = parse_raw_mouse(lparam)
            if parsed:
                flags, dx, dy = parsed
                counters.bump("input_events")
                if flags & MOUSE_MOVE_ABSOLUTE:
                    counters.bump("input_absolute_events")
                else:
                    counters.bump("input_abs", abs(dx) + abs(dy))
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == WM_MOUSEMOVE:
            counters.bump("mousemove")
        elif msg == WM_SETFOCUS:
            counters.bump("setfocus")
        elif msg == WM_KILLFOCUS:
            counters.bump("killfocus")
        elif msg == WM_ACTIVATE:
            counters.bump("activate")
            if (wparam & 0xFFFF) == 0:
                counters.bump("activate_inactive")
        elif msg == WM_ACTIVATEAPP:
            counters.bump("activateapp")
            if wparam == 0:
                counters.bump("activateapp_false")
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if extra is not None:
            r = extra(hwnd, msg, wparam, lparam)
            if r is not None:
                return r
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return WNDPROC(proc)


def create_window(class_name: str, title: str, x: int, y: int, w: int, h: int,
                  wndproc: WNDPROC, ex_style: int = 0) -> int:
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSEXW()
    cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
    cls.style = 0x0003
    cls.lpfnWndProc = wndproc
    cls.hInstance = hinst
    cls.hCursor = user32.LoadCursorW(None, ctypes.cast(IDC_ARROW, wintypes.LPCWSTR))
    cls.hbrBackground = ctypes.cast(COLOR_WINDOW + 1, wintypes.HBRUSH)
    cls.lpszClassName = class_name
    if not user32.RegisterClassExW(ctypes.byref(cls)):
        err = ctypes.get_last_error()
        if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            raise OSError(f"RegisterClassExW failed: {err}")
    hwnd = user32.CreateWindowExW(ex_style, class_name, title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  x, y, w, h, None, None, hinst, None)
    if not hwnd:
        err = ctypes.get_last_error()
        raise OSError(f"CreateWindowExW failed: {err}")
    return _hwnd_int(hwnd)


def pump_once() -> bool:
    """Dispatch pending messages; return False on WM_QUIT."""
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        if msg.message == WM_QUIT:
            return False
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return True


def state_flags(hwnd: int) -> Dict[str, Any]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return {
        "foreground": _hwnd_int(user32.GetForegroundWindow()) == hwnd,
        "focus": _hwnd_int(user32.GetFocus()) == hwnd,
        "active": _hwnd_int(user32.GetActiveWindow()) == hwnd,
        "cursor_over": _hwnd_int(user32.WindowFromPoint(pt)) == hwnd,
    }


# ---- role: game -----------------------------------------------------------
def run_game(args: argparse.Namespace) -> int:
    counters = _Counters()
    proc = make_wndproc(counters)
    hwnd = create_window("NimbusProbeGame", f"Probe game [{args.target}/{args.sink}]", 80, 80, 900, 640, proc)
    flags = {"none": 0, "inputsink": RIDEV_INPUTSINK, "exinputsink": RIDEV_EXINPUTSINK}[args.sink]
    target = hwnd if args.target == "hwnd" else None
    if not register_raw_mouse(target, flags):
        return 2
    bring_to_front(hwnd)
    last_write = 0.0
    while pump_once():
        now = time.monotonic()
        if now - last_write >= 0.1:
            last_write = now
            snap = counters.snapshot()
            snap.update(state_flags(hwnd))
            snap["hwnd"] = hwnd
            snap["tid"] = kernel32.GetCurrentThreadId()
            snap["pid"] = os.getpid()
            snap["t"] = time.time()
            tmp = args.stats + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
            for _ in range(20):  # the tester may hold the file open for a read
                try:
                    os.replace(tmp, args.stats)
                    break
                except PermissionError:
                    time.sleep(0.005)
        time.sleep(0.002)
    return 0


# ---- role: blocker --------------------------------------------------------
def run_blocker(args: argparse.Namespace) -> int:
    ok = user32.BlockInput(True)
    err = ctypes.get_last_error()
    print(f"[blocker] BlockInput(TRUE) -> {bool(ok)} err={err}", flush=True)
    try:
        time.sleep(args.seconds)
    finally:
        user32.BlockInput(False)
        print("[blocker] released", flush=True)
    return 0 if ok else 3


# ---- tester components ----------------------------------------------------
class Panel:
    """Nimbus stand-in: a window on its own thread, registered with RIDEV_INPUTSINK."""

    def __init__(self, ex_style: int = 0) -> None:
        self.counters = _Counters()
        self.hwnd = 0
        self.tid = 0
        self._ex_style = ex_style
        self._ready = threading.Event()
        self._attached_to = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="ProbePanel")
        self._thread.start()
        self._ready.wait(5.0)

    def _extra(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_ATTACH_FOCUS:
            game_tid = int(wparam)
            user32.AttachThreadInput(self.tid, game_tid, True)
            self._attached_to = game_tid
            r = user32.SetFocus(self.hwnd)
            print(f"[panel] AttachThreadInput+SetFocus -> prev focus {_hwnd_int(r)}", flush=True)
            return 0
        if msg == WM_APP_DETACH:
            if self._attached_to:
                user32.AttachThreadInput(self.tid, self._attached_to, False)
                self._attached_to = 0
            return 0
        return None

    def _run(self) -> None:
        self.tid = kernel32.GetCurrentThreadId()
        self._proc = make_wndproc(self.counters, self._extra)
        self.hwnd = create_window("NimbusProbePanel", "Probe panel (Nimbus stand-in)",
                                  1100, 80, 420, 640, self._proc, self._ex_style)
        register_raw_mouse(self.hwnd, RIDEV_INPUTSINK)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def snapshot(self) -> Dict[str, int]:
        return self.counters.snapshot()

    def attach_focus(self, game_tid: int) -> None:
        user32.PostMessageW(self.hwnd, WM_APP_ATTACH_FOCUS, game_tid, 0)

    def detach(self) -> None:
        user32.PostMessageW(self.hwnd, WM_APP_DETACH, 0, 0)

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


class LLHook:
    """WH_MOUSE_LL on a dedicated thread; ``block`` drops every mouse event."""

    def __init__(self) -> None:
        self.block = False
        self.seen = 0
        self.blocked = 0
        self._handle = None
        self._tid = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ProbeLLHook")
        self._thread.start()
        self._ready.wait(5.0)

    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()

        def proc(n_code, wparam, lparam):
            if n_code == HC_ACTION:
                self.seen += 1
                if self.block:
                    self.blocked += 1
                    return 1
            return user32.CallNextHookEx(None, n_code, wparam, lparam)

        self._proc = HOOKPROC(proc)
        self._handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        print(f"[llhook] installed handle={_hwnd_int(self._handle)}", flush=True)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None

    def close(self) -> None:
        self.block = False
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._thread.join(2.0)
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None


class GameProcess:
    def __init__(self, target: str, sink: str, stats_path: str) -> None:
        self.stats_path = stats_path
        if os.path.exists(stats_path):
            os.remove(stats_path)
        self.proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--role", "game",
                                      "--target", target, "--sink", sink, "--stats", stats_path])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not os.path.exists(stats_path):
            time.sleep(0.05)
        snap = self.read()
        self.hwnd = int(snap["hwnd"])
        self.tid = int(snap["tid"])

    def read(self, retries: int = 30) -> Dict[str, Any]:
        for _ in range(retries):
            try:
                with open(self.stats_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                time.sleep(0.03)
        raise RuntimeError("game stats unreadable")

    def settle(self) -> Dict[str, Any]:
        """Wait for the next snapshot after now so counters are current."""
        t0 = time.time()
        for _ in range(40):
            snap = self.read()
            if snap["t"] >= t0:
                return snap
            time.sleep(0.03)
        return self.read()

    def close(self) -> None:
        if self.proc.poll() is None:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            try:
                self.proc.wait(3.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _delta(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    return {k: b.get(k, 0) - a.get(k, 0) for k in _Counters.KEYS}


# ---- role: tester ---------------------------------------------------------
def run_tester(args: argparse.Namespace) -> int:
    scratch = args.scratch or os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_frames")
    os.makedirs(scratch, exist_ok=True)
    stats_path = os.path.join(scratch, "rawinput_game_stats.json")
    results: List[Dict[str, Any]] = []
    variants = [v.split("/") for v in args.variants.split(",")]

    panel = Panel()
    hook = LLHook()
    print(f"[tester] panel hwnd={panel.hwnd} tid={panel.tid}; hook seen so far={hook.seen}", flush=True)

    try:
        for target, sink in variants:
            game = GameProcess(target, sink, stats_path)
            print(f"\n=== game variant target={target} sink={sink} hwnd={game.hwnd} tid={game.tid}", flush=True)
            try:
                scenarios = build_scenarios(args, game, panel, hook, scratch)
                for name, setup, teardown, note, stimulus in scenarios:
                    bring_to_front(game.hwnd)
                    cx, cy = window_center(game.hwnd)
                    user32.SetCursorPos(cx, cy)
                    time.sleep(0.25)
                    ctx: Dict[str, Any] = {}
                    g_pre = game.settle()
                    setup(ctx)
                    time.sleep(0.3)
                    g0 = game.settle()
                    p0 = panel.snapshot()
                    during = {k: bool(g0.get(k)) for k in ("foreground", "focus", "active", "cursor_over")}
                    sent = stimulus(args.moves, args.step)
                    time.sleep(0.35)
                    g1 = game.settle()
                    p1 = panel.snapshot()
                    teardown(ctx)
                    time.sleep(0.4)
                    act = _delta(g_pre, g1)  # activation traffic across setup + injection
                    row = {
                        "variant": f"{target}/{sink}", "scenario": name, "note": ctx.get("note", note),
                        "sent": sent, "expected_abs": 2 * args.step * args.moves,
                        "game": _delta(g0, g1), "panel": _delta(p0, p1),
                        "game_activation": {k: act[k] for k in ("activate", "activate_inactive", "activateapp",
                                                                 "activateapp_false", "killfocus", "setfocus")},
                        "game_state": during,
                    }
                    results.append(row)
                    g = row["game"]
                    print(f"  {name:<20} game WM_INPUT abs={g['input_abs']:>5} ev={g['input_events']:>3} "
                          f"MOUSEMOVE={g['mousemove']:>3} | game sees fg={int(during['foreground'])} "
                          f"focus={int(during['focus'])} act={int(during['active'])} | "
                          f"ACTIVATE={act['activate']}({act['activate_inactive']}) APP={act['activateapp']}({act['activateapp_false']}) "
                          f"KILLFOCUS={act['killfocus']} | panel abs={row['panel']['input_abs']:>5} "
                          f"| sent={sent}  {row['note']}", flush=True)
            finally:
                game.close()
    finally:
        hook.close()
        panel.close()

    out = os.path.join(scratch, "rawinput_probe_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"host": os.environ.get("COMPUTERNAME", ""), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "moves": args.moves, "step": args.step, "results": results}, fh, indent=2)
    print(f"\n[tester] wrote {out}")
    return 0


def build_scenarios(args, game: GameProcess, panel: Panel, hook: LLHook, scratch: str):
    def no_setup(ctx):
        pass

    def no_teardown(ctx):
        pass

    def hook_on(ctx):
        hook.seen = hook.blocked = 0
        hook.block = True

    def hook_off(ctx):
        hook.block = False
        ctx["note"] = f"hook saw {hook.seen}, dropped {hook.blocked}"

    def panel_fg(ctx):
        ok = bring_to_front(panel.hwnd)
        ctx["note"] = f"panel foreground={ok}"

    def game_fg(ctx):
        bring_to_front(game.hwnd)

    def attach(ctx):
        panel.attach_focus(game.tid)

    def detach(ctx):
        panel.detach()
        bring_to_front(game.hwnd)

    def block_on(ctx):
        ctx["blocker"] = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--role", "blocker",
                                           "--seconds", "2.5"])
        time.sleep(0.6)

    def block_off(ctx):
        p = ctx.get("blocker")
        if p:
            p.wait(6.0)
            ctx["note"] = f"blocker exit={p.returncode} (0 means BlockInput succeeded)"

    def hook_count_on(ctx):
        hook.seen = hook.blocked = 0
        hook.block = False

    def hook_count_off(ctx):
        ctx["note"] = f"hook saw {hook.seen} (counting only)"

    # (name, setup, teardown, note, stimulus): the stimulus is what moves the
    # mouse. SendInput is the physical-motion stand-in; SetCursorPos is how
    # the cursor-relay model would move the real cursor from captured packets.
    return [
        ("baseline", no_setup, no_teardown, "", inject_motion),
        ("ll_hook_block_all", hook_on, hook_off, "", inject_motion),
        ("panel_foreground", panel_fg, game_fg, "", inject_motion),
        ("attach_setfocus", attach, detach, "", inject_motion),
        ("blockinput", block_on, block_off, "", inject_motion),
        ("setcursorpos", hook_count_on, hook_count_off, "", move_cursor_setpos),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", choices=["tester", "game", "blocker"], default="tester")
    ap.add_argument("--target", choices=["hwnd", "null"], default="hwnd")
    ap.add_argument("--sink", choices=["none", "inputsink", "exinputsink"], default="none")
    ap.add_argument("--stats", default="")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--moves", type=int, default=40)
    ap.add_argument("--step", type=int, default=9)
    ap.add_argument("--variants", default="hwnd/none,null/none,hwnd/inputsink,hwnd/exinputsink")
    ap.add_argument("--scratch", default="")
    args = ap.parse_args()
    if args.role == "game":
        if not args.stats:
            ap.error("--stats required for --role game")
        return run_game(args)
    if args.role == "blocker":
        return run_blocker(args)
    return run_tester(args)


if __name__ == "__main__":
    sys.exit(main())
