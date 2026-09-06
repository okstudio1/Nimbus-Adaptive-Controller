"""
Nimbus Mouse Filter probe (throwaway, not Nimbus code).

Exercises the kernel filter in ``driver/`` through ``src/mouse_isolation_win.py``.
Needs the driver installed and started (``driver/install-dev.ps1``).

Unattended checks (no mouse movement required, safe to run over a remote
session; the mouse is isolated for at most a few seconds at a time):

  U1  status readable and the interface version matches
  U2  MouseIsolation.start() turns isolation on with a read pending; stop() turns it off
  U3  handle drop: isolation set on a raw handle is cleared when the handle closes
  U4  watchdog: isolation with no read pending is cleared within ~3 s
  U5  exclusivity: a second open fails while the first handle is open
  U6  a read after the watchdog release fails at once with ERROR_NOT_READY

Robustness checks (also unattended, about a minute; skip with ``--quick``).
These are the ones that do not need a hand on the mouse but go beyond the
happy path: process death, a hung client, races, and malformed requests.

  U7   client API edges: availability, double start/stop, busy, stop_all()
  U8   watchdog timing: with no read ever issued, release lands 2.0 to 2.25 s in
  U9   reads parked and cancelled inside the window keep isolation on
  U10  a client killed with TerminateProcess while a read is parked releases at once
  U11  a client suspended past the watchdog: with driver interface v3 its parked
       read is handed back empty at 1 s and the watchdog releases at 2 s while it
       is still frozen, and it reports the stop on resume; with v2 isolation
       persists until the kill by design (the behaviour v3 was written to remove)
  U12  hundreds of start/stop cycles leak no handles and leave clean counters
  U13  threads racing to open the exclusive device never both succeed
  U14  several reads parked on one handle: CancelIoEx takes one, release fails the rest
  U15  malformed IOCTLs, reads and writes fail cleanly (any interactive user can
       open the device, so this is the local denial-of-service surface)
  U16  idle soak (``--soak``, default 30 s): isolation with a parked read and no
       motion stays on, the watchdog never fires (v3: heartbeat ticks, about 1/s)
  U17  Ctrl+Alt+F12, injected with SendInput, releases from the reader thread

Attended phases (``--attended``): a fake Raw Input game and a Nimbus stand-in
from ``probe_rawinput_windows.py`` are spawned, and someone at the physical
mouse moves it during four timed phases. A dialog on screen introduces each
phase (click OK, then move the mouse until the next dialog), the fake game's
title bar counts down, and a final dialog shows the verdict, so the person at
the mouse needs no terminal:

  pass-through   game receives WM_INPUT, driver captures nothing
  isolated       game receives NOTHING, driver captures the motion, cursor frozen
  released       game receives WM_INPUT again
  relay          isolation with the cursor relay: game receives NOTHING, the real
                 cursor MOVES (SetCursorPos), clicks are replayed with SendInput

Each phase also reports the driver's own counters (packets passed to mouclass
and packets captured), which tells "nobody moved the mouse" apart from "motion
bypassed the filter". Injected motion cannot test the filter: SendInput enters
above it, and so does every remote-control tool (TeamViewer, RDP and the like),
which is why this one needs a hand on the real mouse at the console.

Run::

    venv\\Scripts\\python tests\\probe_mouse_filter_windows.py            # U1 to U16
    venv\\Scripts\\python tests\\probe_mouse_filter_windows.py --quick    # U1 to U6 only
    venv\\Scripts\\python tests\\probe_mouse_filter_windows.py --attended # plus the three phases
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("Windows only")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from src import mouse_isolation_win as iso  # noqa: E402

RESULTS: List[Dict[str, object]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "note": note})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {note}", flush=True)


# ---- unattended ---------------------------------------------------------------
def unattended() -> None:
    print("Unattended checks")
    try:
        st = iso.get_status()
    except RuntimeError as exc:
        record("U1 status readable", False, str(exc))
        return
    record("U1 status readable", st["version"] == iso.INTERFACE_VERSION,
           f"version={st['version']} mice={st['connected_mice']} isolating={st['isolating']} "
           f"passed={st['packets_passed']} captured={st['packets_captured']}")

    # U2: the module's own lifecycle
    motion = {"n": 0}
    stops: List[str] = []
    m = iso.MouseIsolation(lambda dx, dy: motion.__setitem__("n", motion["n"] + 1), lambda c, p: None,
                           on_stopped=stops.append)
    try:
        m.start()
        time.sleep(0.5)
        # The device is exclusive, so status while active must go through the
        # instance's own handle; get_status() would fail here.
        live = m.status()
        active_ok = m.active and live["isolating"] == 1
        grabbed = m.grabbed_devices
        m.stop("probe")
        time.sleep(0.3)
        st2 = iso.get_status()
        record("U2 start/stop lifecycle",
               active_ok and st2["isolating"] == 0 and len(grabbed) == 1 and stops == ["probe"],
               f"active={active_ok} isolating_live={live['isolating']} pending_reads_live={live['pending_reads']} "
               f"grabbed={len(grabbed)} isolating_after_stop={st2['isolating']} stop_reasons={stops} motion_events={motion['n']}")
    except Exception as exc:
        record("U2 start/stop lifecycle", False, str(exc))
        try:
            m.stop("probe error")
        except Exception:
            pass

    # U3: handle drop clears isolation
    try:
        h = iso._open_device()
        iso._set_isolation(h, True)
        time.sleep(0.3)
        iso._k32.CloseHandle(h)
        time.sleep(0.3)
        st3 = iso.get_status()
        record("U3 handle drop clears isolation", st3["isolating"] == 0, f"isolating={st3['isolating']}")
    except Exception as exc:
        record("U3 handle drop clears isolation", False, str(exc))

    # U4: watchdog clears isolation when nobody reads. U6 reuses the handle, so
    # both close in a finally: a leaked handle would make U5 fail for the wrong
    # reason (the device is exclusive) and hold isolation on.
    h = None
    event = None
    u4_done = u6_done = False
    try:
        before = iso.get_status()["watchdog_releases"]
        h = iso._open_device()
        iso._set_isolation(h, True)
        print("      (mouse isolated with no reader; the watchdog should release it in about 2 s)", flush=True)
        time.sleep(3.2)
        # Status through the same handle: the device is exclusive.
        st4 = iso._query_status(h)
        isolating, releases = st4["isolating"], st4["watchdog_releases"]
        record("U4 watchdog releases isolation", isolating == 0 and releases == before + 1,
               f"isolating={isolating} watchdog_releases {before} -> {releases}")
        u4_done = True

        # U6: a read after the release must fail fast with ERROR_NOT_READY, which
        # is how MouseIsolation's reader learns the mouse was given back.
        buf = ctypes.create_string_buffer(iso._MOUSE_INPUT_DATA.size)
        returned = wintypes.DWORD(0)
        ov = iso._OVERLAPPED()
        event = iso._k32.CreateEventW(None, True, False, None)
        ov.hEvent = event
        t0 = time.monotonic()
        ok = iso._k32.ReadFile(h, buf, ctypes.sizeof(buf), ctypes.byref(returned), ctypes.byref(ov))
        err = 0 if ok else ctypes.get_last_error()
        if err == iso.ERROR_IO_PENDING:
            # Give a slow completion a moment, then treat a still-pending read as the old hang.
            time.sleep(0.5)
            if iso._k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(returned), False):
                err = 0
            else:
                err = ctypes.get_last_error()
            if err == 996:  # ERROR_IO_INCOMPLETE: still pending, would wait forever
                iso._k32.CancelIoEx(h, None)
                iso._k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(returned), True)
        elapsed = time.monotonic() - t0
        record("U6 read after release fails with ERROR_NOT_READY", err == iso.ERROR_NOT_READY,
               f"error={err} after {elapsed:.3f}s (expected {iso.ERROR_NOT_READY})")
        u6_done = True
    except Exception as exc:
        if not u4_done:
            record("U4 watchdog releases isolation", False, str(exc))
        else:
            record("U6 read after release fails with ERROR_NOT_READY", False, str(exc))
    finally:
        if event:
            iso._k32.CloseHandle(event)
        if h:
            iso._k32.CloseHandle(h)
        if not u6_done and not u4_done:
            record("U6 read after release fails with ERROR_NOT_READY", False, "skipped (U4 did not complete)")

    # U5: exclusivity
    try:
        h1 = iso._open_device()
        try:
            h2 = iso._open_device()
            iso._k32.CloseHandle(h2)
            record("U5 exclusive open", False, "second open succeeded")
        except RuntimeError as exc:
            record("U5 exclusive open", True, str(exc))
        finally:
            iso._k32.CloseHandle(h1)
    except Exception as exc:
        record("U5 exclusive open", False, str(exc))


# ---- robustness (unattended, longer) ------------------------------------------
_k32 = iso._k32
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.WriteFile.restype = wintypes.BOOL
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.GetProcessHandleCount.restype = wintypes.BOOL
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.TerminateProcess.restype = wintypes.BOOL
_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

ERROR_INVALID_FUNCTION = 1        # STATUS_INVALID_DEVICE_REQUEST
ERROR_INSUFFICIENT_BUFFER = 122   # STATUS_BUFFER_TOO_SMALL
ERROR_IO_INCOMPLETE = 996
WAIT_OBJECT_0 = 0
PACKET = iso._MOUSE_INPUT_DATA.size


class _Read:
    """One overlapped ReadFile on the control handle, with its own event."""

    def __init__(self, handle: int, size: int) -> None:
        self.handle = handle
        self.buf = ctypes.create_string_buffer(size) if size else None
        self.returned = wintypes.DWORD(0)
        self.event = _k32.CreateEventW(None, True, False, None)
        self.ov = iso._OVERLAPPED()
        self.ov.hEvent = self.event
        ok = _k32.ReadFile(handle, self.buf, size, ctypes.byref(self.returned), ctypes.byref(self.ov))
        self.err = 0 if ok else ctypes.get_last_error()   # ERROR_IO_PENDING while parked

    def wait(self, ms: int) -> int:
        """Final Win32 error (0 = success), or ERROR_IO_INCOMPLETE if still parked after ``ms``."""
        if self.err != iso.ERROR_IO_PENDING:
            return self.err
        if _k32.WaitForSingleObject(self.event, ms) != WAIT_OBJECT_0:
            return ERROR_IO_INCOMPLETE
        ok = _k32.GetOverlappedResult(self.handle, ctypes.byref(self.ov), ctypes.byref(self.returned), False)
        self.err = 0 if ok else ctypes.get_last_error()
        return self.err

    def cancel(self) -> None:
        _k32.CancelIoEx(self.handle, ctypes.byref(self.ov))

    def close(self) -> None:
        if self.err == iso.ERROR_IO_PENDING:
            self.cancel()
            _k32.GetOverlappedResult(self.handle, ctypes.byref(self.ov), ctypes.byref(self.returned), True)
        _k32.CloseHandle(self.event)


def _read_once(handle: int, size: int, ms: int = 1000) -> int:
    """Issue one read and return its final error; a read still parked after ``ms`` is cancelled."""
    rd = _Read(handle, size)
    try:
        return rd.wait(ms)
    finally:
        rd.close()


def _write_once(handle: int, data: bytes) -> int:
    buf = ctypes.create_string_buffer(data, len(data))
    returned = wintypes.DWORD(0)
    event = _k32.CreateEventW(None, True, False, None)
    ov = iso._OVERLAPPED()
    ov.hEvent = event
    try:
        ok = _k32.WriteFile(handle, buf, len(data), ctypes.byref(returned), ctypes.byref(ov))
        err = 0 if ok else ctypes.get_last_error()
        if err == iso.ERROR_IO_PENDING:
            if _k32.WaitForSingleObject(event, 1000) != WAIT_OBJECT_0:
                _k32.CancelIoEx(handle, ctypes.byref(ov))
                return ERROR_IO_INCOMPLETE
            ok = _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), False)
            err = 0 if ok else ctypes.get_last_error()
        return err
    finally:
        _k32.CloseHandle(event)


def _raw_ioctl(handle: int, code: int, in_bytes: Optional[bytes], out_size: int) -> Tuple[int, int, bytes]:
    """DeviceIoControl with arbitrary buffer sizes. Returns (error, bytes returned, output)."""
    in_buf = ctypes.create_string_buffer(in_bytes, len(in_bytes)) if in_bytes else None
    out_buf = ctypes.create_string_buffer(out_size) if out_size else None
    returned = wintypes.DWORD(0)
    event = _k32.CreateEventW(None, True, False, None)
    ov = iso._OVERLAPPED()
    ov.hEvent = event
    try:
        ok = _k32.DeviceIoControl(handle, code, in_buf, len(in_bytes) if in_bytes else 0,
                                  out_buf, out_size, ctypes.byref(returned), ctypes.byref(ov))
        err = 0 if ok else ctypes.get_last_error()
        if err == iso.ERROR_IO_PENDING:
            ok = _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), True)
            err = 0 if ok else ctypes.get_last_error()
        out = out_buf.raw[:returned.value] if out_buf else b""
        return err, returned.value, out
    finally:
        _k32.CloseHandle(event)


def _open_raw(path: str, access: int) -> Tuple[Optional[int], int]:
    """CreateFile with a chosen access mask. Returns (handle or None, error)."""
    h = _k32.CreateFileW(path, access, iso.FILE_SHARE_READ | iso.FILE_SHARE_WRITE, None,
                         iso.OPEN_EXISTING, iso.FILE_FLAG_OVERLAPPED, None)
    if h == iso.INVALID_HANDLE_VALUE or h is None:
        return None, ctypes.get_last_error()
    return h, 0


def _handle_count() -> int:
    n = wintypes.DWORD(0)
    _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


def _wait_device_free(timeout: float) -> Optional[Dict[str, int]]:
    """Poll until the exclusive device opens again (a dying client's handle takes a moment)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return iso.get_status()
        except RuntimeError:
            time.sleep(0.02)
    return None


def _parse_kv(line: str) -> Dict[str, str]:
    return dict(tok.split("=", 1) for tok in line.split()[1:] if "=" in tok)


class _Child:
    """This script re-run with ``--child``: it isolates and answers commands on stdin.

    ``Popen`` hands back the process it started, and in a venv on Windows that
    is ``Scripts\\python.exe``, a launcher that runs the real interpreter as a
    child. Suspending or terminating the launcher does nothing to the process
    that holds the driver open, so the child reports its own pid in the READY
    line and :attr:`handle` opens that process directly.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--child"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.seen: List[str] = []
        self.pid = 0
        self._phandle: Optional[int] = None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line.rstrip())
        self.lines.put(None)

    def expect(self, prefix: str, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return None
            self.seen.append(line)
            if line.startswith("READY"):
                self.pid = int(_parse_kv(line).get("pid", "0"))
            if line.startswith(prefix):
                return line

    def send(self, command: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    @property
    def handle(self) -> int:
        """A handle to the interpreter that holds the driver, not the launcher."""
        if self._phandle is None:
            if not self.pid:
                raise RuntimeError("child pid unknown (no READY line yet)")
            access = 0x0001 | 0x0800 | 0x1000 | 0x00100000   # TERMINATE, SUSPEND_RESUME, QUERY_LIMITED, SYNCHRONIZE
            h = _k32.OpenProcess(access, False, self.pid)
            if not h:
                raise RuntimeError(f"OpenProcess({self.pid}) failed: Windows error {ctypes.get_last_error()}")
            self._phandle = h
        return self._phandle

    def kill(self) -> None:
        """TerminateProcess on the interpreter itself, then the launcher."""
        _k32.TerminateProcess(self.handle, 1)
        self.proc.kill()
        self.proc.wait(timeout=5)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.kill()
        if self._phandle:
            _k32.CloseHandle(self._phandle)
            self._phandle = None
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


def child() -> int:
    """``--child`` role for U10 and U11: isolate, report, then wait for commands."""
    def on_stopped(reason: str) -> None:
        print(f"STOPPED {reason}", flush=True)

    m = iso.MouseIsolation(lambda dx, dy: None, lambda c, p: None, on_stopped=on_stopped)
    try:
        m.start()
    except Exception as exc:
        print(f"ERROR {exc}", flush=True)
        return 1
    st = m.status()
    print(f"READY pid={os.getpid()} isolating={st['isolating']} pending_reads={st['pending_reads']} "
          f"watchdog_releases={st['watchdog_releases']}", flush=True)
    for line in sys.stdin:
        command = line.strip()
        if command == "status":
            try:
                st = m.status()
                print(f"STATUS active={m.active} isolating={st['isolating']} pending_reads={st['pending_reads']} "
                      f"watchdog_releases={st['watchdog_releases']} ticks={m.ticks} "
                      f"stop_reason={m.stop_reason.replace(' ', '_') or '-'}", flush=True)
            except RuntimeError as exc:
                print(f"STATUS active={m.active} error={str(exc).replace(' ', '_')} "
                      f"stop_reason={m.stop_reason.replace(' ', '_') or '-'}", flush=True)
        elif command == "quit":
            break
    m.stop("child quit")
    return 0


def check_api_edges() -> None:
    name = "U7 client API edges"
    notes: List[str] = []
    ok = True

    available = bool(iso.MOUSE_ISOLATION_AVAILABLE and iso.is_available())
    devices = iso.list_pointer_devices()
    listed = len(devices) == 1 and devices[0]["connected_mice"] >= 1 and devices[0]["node"] == iso.DEVICE_PATH
    ok = ok and available and listed
    notes.append(f"available={available} listed={listed}")

    stops: List[str] = []
    m = iso.MouseIsolation(lambda dx, dy: None, lambda c, p: None, on_stopped=stops.append)
    first = m.start()
    again = m.start()                       # idempotent: no second handle, same answer
    twice = again == first and m.active and m.status()["isolating"] == 1
    m.stop("first")
    m.stop("second")                        # idempotent: one callback, reason kept
    stopped = stops == ["first"] and m.stop_reason == "first" and not m.active
    ok = ok and twice and stopped
    notes.append(f"double start ok={twice} double stop ok={stopped}")

    raw = iso._open_device()
    try:
        try:
            m.start()
            busy = "no error"
            m.stop("unexpected")
        except iso.DriverBusyError:
            busy = "DriverBusyError"
        except Exception as exc:
            busy = f"{type(exc).__name__}: {exc}"
        still_available = iso.is_available()
        listed_busy = iso.list_pointer_devices()
    finally:
        _k32.CloseHandle(raw)
    busy_ok = busy == "DriverBusyError" and still_available and listed_busy == []
    ok = ok and busy_ok
    notes.append(f"while another handle is open: start -> {busy}, is_available={still_available}, "
                 f"list_pointer_devices={len(listed_busy)} entries")

    m.start()
    iso.stop_all("probe stop_all")
    st = iso.get_status()
    all_ok = not m.active and m.stop_reason == "probe stop_all" and st["isolating"] == 0
    ok = ok and all_ok
    notes.append(f"stop_all ok={all_ok}")
    record(name, ok, "; ".join(notes))


def check_watchdog_timing() -> None:
    h = iso._open_device()
    released_at: Optional[float] = None
    try:
        before = iso._query_status(h)["watchdog_releases"]
        t0 = time.monotonic()
        iso._set_isolation(h, True)
        while time.monotonic() - t0 < 4.0:
            if iso._query_status(h)["isolating"] == 0:
                released_at = time.monotonic() - t0
                break
            time.sleep(0.01)
        after = iso._query_status(h)["watchdog_releases"]
    finally:
        _k32.CloseHandle(h)
    ok = released_at is not None and 1.9 <= released_at <= 2.6 and after == before + 1
    when = f"released after {released_at:.3f} s" if released_at is not None else "NOT released within 4 s"
    record("U8 watchdog release timing", ok,
           f"no read issued: {when} (2 s timeout sampled every 250 ms, so 2.0 to 2.25 expected), "
           f"watchdog_releases {before} -> {after}")


def check_reads_keep_isolation() -> None:
    h = iso._open_device()
    samples: List[int] = []
    cancels: List[object] = []
    try:
        before = iso._query_status(h)["watchdog_releases"]
        iso._set_isolation(h, True)
        t0 = time.monotonic()
        for _ in range(6):
            rd = _Read(h, PACKET)
            if rd.err != iso.ERROR_IO_PENDING:
                cancels.append(f"issue failed {rd.err}")
                rd.close()
                break
            time.sleep(0.9)
            samples.append(iso._query_status(h)["isolating"])
            rd.cancel()
            cancels.append(rd.wait(1000))
            rd.close()
        held = time.monotonic() - t0
        st = iso._query_status(h)
        iso._set_isolation(h, False)
    finally:
        _k32.CloseHandle(h)
    ok = (len(samples) == 6 and all(s == 1 for s in samples) and st["watchdog_releases"] == before
          and all(c == iso.ERROR_OPERATION_ABORTED for c in cancels))
    record("U9 reads cycling inside the window keep isolation", ok,
           f"6 reads each parked 0.9 s then cancelled, {held:.1f} s total: isolating samples={samples}, "
           f"cancel results={cancels} (995 expected), watchdog_releases {before} -> {st['watchdog_releases']}")


def check_hard_kill() -> None:
    name = "U10 hard kill with a read parked releases"
    ch = _Child()
    try:
        ready = ch.expect("READY", 15.0)
        if ready is None:
            record(name, False, "child never reported READY: " + " | ".join(ch.seen[-5:]))
            return
        kv = _parse_kv(ready)
        before = int(kv["watchdog_releases"])
        t0 = time.monotonic()
        ch.kill()
        st = _wait_device_free(3.0)
        elapsed = time.monotonic() - t0
    finally:
        ch.close()
    ok = (st is not None and kv["isolating"] == "1" and kv["pending_reads"] == "1"
          and st["isolating"] == 0 and st["pending_reads"] == 0 and st["watchdog_releases"] == before)
    if st is None:
        outcome = f"device still busy {elapsed:.1f} s after TerminateProcess"
    else:
        outcome = (f"device free {elapsed:.3f} s after TerminateProcess, isolating={st['isolating']} "
                   f"pending_reads={st['pending_reads']} watchdog_releases {before} -> {st['watchdog_releases']} "
                   f"(handle cleanup, not the watchdog)")
    record(name, ok, f"child isolating={kv['isolating']} pending_reads={kv['pending_reads']}; {outcome}")


def check_suspended_client() -> None:
    version = iso.get_status()["version"]
    if version >= 3:
        name = "U11 suspended client loses the mouse to the watchdog, learns it on resume"
    else:
        name = "U11 suspended client keeps isolation, kill releases"
    ch = _Child()
    status_line: Optional[str] = None
    stopped: List[str] = []
    st: Optional[Dict[str, int]] = None
    freed: Optional[Dict[str, int]] = None
    expected = False
    try:
        ready = ch.expect("READY", 15.0)
        if ready is None:
            record(name, False, "child never reported READY: " + " | ".join(ch.seen[-5:]))
            return
        before = int(_parse_kv(ready)["watchdog_releases"])
        ntstatus = _ntdll.NtSuspendProcess(ch.handle) & 0xFFFFFFFF
        time.sleep(3.5)                     # well past the 2 s watchdog
        _ntdll.NtResumeProcess(ch.handle)
        time.sleep(0.5)
        ch.send("status")
        status_line = ch.expect("STATUS", 5.0)
        stopped = [line for line in ch.seen if line.startswith("STOPPED")]
        kv = _parse_kv(status_line) if status_line else {}
        if version >= 3:
            # Frozen, the child could not answer the 1 s tick, so the watchdog
            # released at 2 s. On resume its next read failed with
            # ERROR_NOT_READY, it reported the stop and closed the handle, and
            # the device is free for us to read the counters.
            freed = _wait_device_free(3.0)
            expected = (kv.get("active") == "False" and any("watchdog" in line for line in stopped)
                        and freed is not None and freed["isolating"] == 0
                        and freed["watchdog_releases"] == before + 1)
        else:
            expected = (kv.get("active") == "True" and kv.get("isolating") == "1" and kv.get("pending_reads") == "1"
                        and kv.get("watchdog_releases") == str(before) and not stopped)
        ch.kill()
        st = _wait_device_free(3.0)
    finally:
        ch.close()
    released = st is not None and st["isolating"] == 0
    ok = ntstatus == 0 and expected and released
    if version >= 3:
        detail = (f"after resume: {status_line or 'no STATUS reply'} stopped={stopped}; device "
                  + (f"free, isolating={freed['isolating']} watchdog_releases {before} -> {freed['watchdog_releases']}"
                     if freed else "still busy"))
    else:
        detail = (f"after resume: {status_line or 'no STATUS reply'} stopped={stopped}. On v2 a parked read counts "
                  f"as alive, so a hung Nimbus holds the mouse until it is killed")
    record(name, ok, f"driver v{version}; NtSuspendProcess={ntstatus:#x}, 3.5 s suspended; {detail}; "
                     f"after kill isolating={st['isolating'] if st else '?'}")


def check_cycles() -> None:
    m = iso.MouseIsolation(lambda dx, dy: None, lambda c, p: None)
    st0 = iso.get_status()
    with contextlib.redirect_stdout(io.StringIO()):   # the module prints once per start and stop
        for _ in range(5):
            m.start()
            m.stop("warm-up")
        h0 = _handle_count()
        t0 = time.monotonic()
        for _ in range(200):
            m.start()
            m.stop("cycle")
        t_client = time.monotonic() - t0
        h1 = _handle_count()
    t0 = time.monotonic()
    for _ in range(500):
        h = iso._open_device()
        try:
            iso._set_isolation(h, True)
            iso._set_isolation(h, False)
        finally:
            _k32.CloseHandle(h)
    t_raw = time.monotonic() - t0
    h2 = _handle_count()
    st = iso.get_status()
    ok = ((h1 - h0) <= 2 and (h2 - h1) <= 2 and st["isolating"] == 0 and st["pending_reads"] == 0
          and st["watchdog_releases"] == st0["watchdog_releases"])
    record("U12 start/stop cycles leak nothing", ok,
           f"200 client cycles in {t_client:.2f} s ({t_client / 200 * 1000:.1f} ms each), "
           f"500 raw isolate/release cycles in {t_raw:.2f} s; process handles {h0} -> {h1} -> {h2}; "
           f"isolating={st['isolating']} pending_reads={st['pending_reads']} "
           f"watchdog_releases {st0['watchdog_releases']} -> {st['watchdog_releases']}")


def check_open_storm() -> None:
    threads_n, tries = 16, 40
    state: Dict[str, object] = {"open_now": 0, "max_open": 0, "opened": 0, "busy": 0, "other": []}
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(tries):
            try:
                h = iso._open_device()
            except iso.DriverBusyError:
                with lock:
                    state["busy"] += 1  # type: ignore[operator]
                time.sleep(0.0005)      # back off so more threads get a turn at the device
                continue
            except Exception as exc:
                with lock:
                    state["other"].append(f"{type(exc).__name__}: {exc}")  # type: ignore[attr-defined]
                continue
            with lock:
                state["open_now"] += 1  # type: ignore[operator]
                state["opened"] += 1  # type: ignore[operator]
                state["max_open"] = max(state["max_open"], state["open_now"])  # type: ignore[type-var]
            time.sleep(0.001)
            with lock:
                # Decrement before the close: another thread cannot open until
                # CloseHandle lands, so this never undercounts overlap.
                state["open_now"] -= 1  # type: ignore[operator]
            _k32.CloseHandle(h)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    try:
        iso.get_status()
        reopen = True
    except RuntimeError:
        reopen = False
    other: List[str] = state["other"]  # type: ignore[assignment]
    ok = state["max_open"] == 1 and not other and state["opened"] > 0 and reopen  # type: ignore[operator]
    record("U13 concurrent opens stay exclusive", ok,
           f"{threads_n} threads x {tries} opens in {elapsed:.2f} s: {state['opened']} succeeded, "
           f"{state['busy']} refused as busy, {len(other)} other errors {other[:3]}, "
           f"max simultaneous={state['max_open']}, device opens afterwards={reopen}")


def check_read_drain() -> None:
    h = iso._open_device()
    reads: List[_Read] = []
    try:
        iso._set_isolation(h, True)
        reads = [_Read(h, PACKET) for _ in range(4)]
        issued = [r.err for r in reads]
        parked = iso._query_status(h)["pending_reads"]
        reads[0].cancel()
        cancelled = reads[0].wait(1000)
        after_cancel = iso._query_status(h)["pending_reads"]
        t0 = time.monotonic()
        iso._set_isolation(h, False)
        drained = [r.wait(1000) for r in reads[1:]]
        dt = time.monotonic() - t0
        st = iso._query_status(h)
    finally:
        for r in reads:
            r.close()
        _k32.CloseHandle(h)
    ok = (all(e == iso.ERROR_IO_PENDING for e in issued) and parked == 4
          and cancelled == iso.ERROR_OPERATION_ABORTED and after_cancel == 3
          and all(d == iso.ERROR_NOT_READY for d in drained)
          and st["pending_reads"] == 0 and st["isolating"] == 0)
    record("U14 parked reads drain on cancel and release", ok,
           f"4 reads issued (codes {issued}, 997 expected), pending_reads={parked}; CancelIoEx on one -> {cancelled} "
           f"(995 expected), pending_reads={after_cancel}; SET_ISOLATION(0) -> {drained} (21 expected) "
           f"within {dt * 1000:.0f} ms, pending_reads={st['pending_reads']} isolating={st['isolating']}")


def check_malformed() -> None:
    """Every request an interactive user could send wrongly must fail cleanly."""
    SET, GET = iso.IOCTL_NIMBUS_SET_ISOLATION, iso.IOCTL_NIMBUS_GET_STATUS
    rows: List[Tuple[str, int, Tuple[int, ...]]] = []

    def case(label: str, got: int, *expected: int) -> None:
        rows.append((label, got, expected))

    def isolate_then_reset(handle: int, payload: bytes) -> int:
        """SET_ISOLATION with an odd payload; -1 if it 'succeeded' without isolating."""
        err = _raw_ioctl(handle, SET, payload, 0)[0]
        on = iso._query_status(handle)["isolating"]
        if on:
            iso._set_isolation(handle, False)
        return err if (err != 0 or on == 1) else -1

    h = iso._open_device()
    try:
        case("SET_ISOLATION with a 0-byte input", _raw_ioctl(h, SET, b"", 0)[0], ERROR_INSUFFICIENT_BUFFER)
        case("SET_ISOLATION with a 2-byte input", _raw_ioctl(h, SET, b"\x01\x00", 0)[0], ERROR_INSUFFICIENT_BUFFER)
        case("SET_ISOLATION with an 8-byte input (extra ignored, isolates)",
             isolate_then_reset(h, struct.pack("<II", 1, 0xDEADBEEF)), 0)
        case("SET_ISOLATION with value 0xFFFFFFFF (any nonzero isolates)",
             isolate_then_reset(h, struct.pack("<I", 0xFFFFFFFF)), 0)
        case("GET_STATUS with a 0-byte output", _raw_ioctl(h, GET, None, 0)[0], ERROR_INSUFFICIENT_BUFFER)
        case("GET_STATUS with a 31-byte output", _raw_ioctl(h, GET, None, 31)[0], ERROR_INSUFFICIENT_BUFFER)
        err, n, _ = _raw_ioctl(h, GET, None, 64)
        case("GET_STATUS with a 64-byte output (returns 32)", err if n == 32 else -1, 0)
        case("unknown IOCTL, function 0x802", _raw_ioctl(h, 0x00222008, None, 64)[0], ERROR_INVALID_FUNCTION)
        case("unknown IOCTL, METHOD_NEITHER", _raw_ioctl(h, 0x00222003, None, 0)[0], ERROR_INVALID_FUNCTION)
        case("foreign IOCTL (IOCTL_MOUSE_QUERY_ATTRIBUTES)",
             _raw_ioctl(h, 0x000F0080, None, 64)[0], ERROR_INVALID_FUNCTION)
        case("ReadFile 24 bytes while not isolating", _read_once(h, PACKET), iso.ERROR_NOT_READY)
        case("ReadFile 0 bytes (the framework completes it)", _read_once(h, 0), 0)
        case("ReadFile 1 byte", _read_once(h, 1), ERROR_INSUFFICIENT_BUFFER)
        case("ReadFile 23 bytes", _read_once(h, PACKET - 1), ERROR_INSUFFICIENT_BUFFER)
        case("WriteFile 24 bytes (no write handler)", _write_once(h, b"\0" * PACKET), ERROR_INVALID_FUNCTION)
        iso._set_isolation(h, True)
        big = _Read(h, 256 * 1024)
        parked = big.err
        iso._set_isolation(h, False)
        got = big.wait(1000)
        big.close()
        case("ReadFile 256 KB while isolating parks, release fails it",
             got if parked == iso.ERROR_IO_PENDING else -1, iso.ERROR_NOT_READY)
    finally:
        _k32.CloseHandle(h)

    # Other access masks and the unaliased device path. One handle at a time: the device is exclusive.
    h, err = _open_raw(iso.DEVICE_PATH, iso.GENERIC_READ)
    if h is None:
        case("open with GENERIC_READ only", err, 0)
    else:
        try:
            case("read-only handle can isolate (IOCTLs are FILE_ANY_ACCESS)",
                 isolate_then_reset(h, struct.pack("<I", 1)), 0)
        finally:
            _k32.CloseHandle(h)
    h, err = _open_raw(iso.DEVICE_PATH, iso.GENERIC_WRITE)
    if h is None:
        case("open with GENERIC_WRITE only", err, 0)
    else:
        try:
            case("ReadFile on a write-only handle (I/O manager refuses)", _read_once(h, PACKET), iso.ERROR_ACCESS_DENIED)
        finally:
            _k32.CloseHandle(h)
    h, err = _open_raw(r"\\?\GLOBALROOT\Device\NimbusMouseFilter", iso.GENERIC_READ | iso.GENERIC_WRITE)
    if h is None:
        case("open by \\Device name through GLOBALROOT", err, 0)
    else:
        try:
            case("open by \\Device name through GLOBALROOT, GET_STATUS", _raw_ioctl(h, GET, None, 32)[0], 0)
        finally:
            _k32.CloseHandle(h)

    final = iso.get_status()
    healthy = final["isolating"] == 0 and final["pending_reads"] == 0 and final["connected_mice"] >= 1
    bad = [f"{label}: got {got}, expected {'/'.join(map(str, exp))}" for label, got, exp in rows if got not in exp]
    for label, got, exp in rows:
        print(f"      {'ok ' if got in exp else 'BAD'} {label}: {got}", flush=True)
    record("U15 malformed requests fail cleanly", not bad and healthy,
           f"{len(rows) - len(bad)}/{len(rows)} cases as expected; device healthy afterwards={healthy}"
           + (f"; unexpected: {bad}" if bad else ""))


def check_soak(seconds: float) -> None:
    name = "U16 idle isolation soak"
    motion = {"n": 0}
    stops: List[str] = []
    m = iso.MouseIsolation(lambda dx, dy: motion.__setitem__("n", motion["n"] + 1), lambda c, p: None,
                           on_stopped=stops.append)
    try:
        m.start()
        first = m.status()
        version, before = first["version"], first["watchdog_releases"]
        print(f"      (mouse isolated for {seconds:.0f} s with the reader parked and nobody moving it; "
              f"the watchdog must not fire)", flush=True)
        samples: List[Tuple[int, int, int]] = []
        t_end = time.monotonic() + seconds
        while m.active and time.monotonic() < t_end:
            time.sleep(1.0)
            if m.active:
                st = m.status()
                samples.append((st["isolating"], st["pending_reads"], st["watchdog_releases"]))
        steady = all(s[0] == 1 and s[2] == before for s in samples)
        parked = sum(1 for s in samples if s[1] >= 1)
        early = list(stops)                 # anything here means the driver let go before we did
        ticks = m.ticks
        # v3 hands an idle read back empty 1.0 to 1.25 s after it arrived and the
        # reader re-issues at once; v2 never completes an idle read.
        ticks_ok = (seconds * 0.7 <= ticks <= seconds + 1) if version >= 3 else ticks == 0
        ok = m.active and steady and not early and len(samples) >= int(seconds) - 1 and ticks_ok
        m.stop("soak done")
        record(name, ok,
               f"{seconds:.0f} s, {len(samples)} samples: isolating stayed 1 and watchdog_releases stayed "
               f"{before}: {steady}; a read was parked in {parked}/{len(samples)} samples; heartbeat ticks={ticks} "
               f"(v{version}: {'about one per second expected' if version >= 3 else 'none expected'}); "
               f"early stops={early}; motion events={motion['n']}")
    except Exception as exc:
        record(name, False, f"{type(exc).__name__}: {exc}")
        try:
            m.stop("soak error")
        except Exception:
            pass


def _press_hotkey(hold_s: float) -> None:
    """Hold Ctrl+Alt+F12 with SendInput for ``hold_s`` seconds (the keyboard is never filtered)."""
    from probe_rawinput_windows import INPUT, KEYBDINPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP, user32
    keys = (0x11, 0x12, 0x7B)   # VK_CONTROL, VK_MENU, VK_F12

    def send(vk: int, flags: int) -> None:
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    for vk in keys:
        send(vk, 0)
    time.sleep(hold_s)
    for vk in reversed(keys):
        send(vk, KEYEVENTF_KEYUP)


def check_hotkey() -> None:
    name = "U17 Ctrl+Alt+F12 releases from the reader thread"
    stops: List[str] = []
    m = iso.MouseIsolation(lambda dx, dy: None, lambda c, p: None, on_stopped=stops.append, hotkey=True)
    m.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    _press_hotkey(0.5)
    while m.active and time.monotonic() - t0 < 3.0:
        time.sleep(0.02)
    elapsed = time.monotonic() - t0
    released = not m.active
    st = _wait_device_free(2.0)
    m.stop("cleanup")
    ok = released and stops == ["emergency hotkey"] and st is not None and st["isolating"] == 0
    record(name, ok, f"injected Ctrl+Alt+F12 held 0.5 s: released={released} after {elapsed:.2f} s, "
                     f"stops={stops}, isolating={st['isolating'] if st else '?'}")


def robustness(soak_seconds: float) -> None:
    print("\nRobustness checks (unattended; the mouse is isolated in short stretches, then for the soak)")
    checks = (
        (check_api_edges, ()),
        (check_watchdog_timing, ()),
        (check_reads_keep_isolation, ()),
        (check_hard_kill, ()),
        (check_suspended_client, ()),
        (check_cycles, ()),
        (check_open_storm, ()),
        (check_read_drain, ()),
        (check_malformed, ()),
        (check_soak, (soak_seconds,)),
        (check_hotkey, ()),
    )
    for fn, args in checks:
        try:
            fn(*args)
        except Exception as exc:
            record(f"{fn.__name__} crashed", False, f"{type(exc).__name__}: {exc}")
            iso.stop_all("probe error")


# ---- attended -----------------------------------------------------------------
def attended(seconds: float) -> None:
    from probe_rawinput_windows import GameProcess, Panel, bring_to_front, window_center, user32

    scratch = os.path.join(REPO, "tests", "probe_frames")
    os.makedirs(scratch, exist_ok=True)
    game = GameProcess("hwnd", "none", os.path.join(scratch, "filter_game_stats.json"))
    panel = Panel()
    motion = {"abs": 0, "events": 0}

    def on_motion(dx, dy):
        motion["abs"] += abs(dx) + abs(dy)
        motion["events"] += 1

    m = iso.MouseIsolation(on_motion, lambda c, p: None)
    # Phase 4: the same capture, but the reader moves the real cursor and
    # buttons and wheel are replayed with SendInput (no Nimbus window here).
    relay = iso.MouseIsolation(on_motion, iso.inject_button, on_wheel=iso.inject_wheel, cursor_relay=True)

    def cursor_pos() -> Tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    MB_OK, MB_ICONINFORMATION, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x40, 0x10000, 0x40000
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]

    def driver_counters() -> Dict[str, int]:
        # get_status() reads through the active instance's own handle while
        # this process is isolating (the device is exclusive).
        st = iso.get_status()
        return {"passed": st["packets_passed"], "captured": st["packets_captured"]}

    hints = {
        "isolated": "The cursor will FREEZE during this phase. Keep moving anyway.",
        "relay": "The cursor should move normally, but the game window must not react to it.",
    }

    def phase(index: int, name: str, setup, teardown) -> Dict[str, int]:
        user32.MessageBoxW(None,
                           f"Phase {index} of 4: {name}\n\n"
                           f"Click OK (or press Enter), then move the mouse continuously "
                           f"for {seconds:.0f} seconds, until the next box appears.\n\n"
                           + hints.get(name, "The cursor should move normally."),
                           "Nimbus Mouse Filter probe", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)
        bring_to_front(game.hwnd)
        cx, cy = window_center(game.hwnd)
        user32.SetCursorPos(cx, cy)
        setup()
        time.sleep(0.4)
        d0 = driver_counters()
        g0 = game.settle()
        m0 = dict(motion)
        print(f"\n>>> {name}: MOVE THE PHYSICAL MOUSE for {seconds:.0f} s", flush=True)
        travel = 0                          # how far the real cursor moved, sampled at 10 Hz
        last = cursor_pos()
        for remaining in range(int(seconds), 0, -1):
            user32.SetWindowTextW(game.hwnd, f"{name}: MOVE THE MOUSE ({remaining} s left)")
            print(f"    {remaining}...", end="\r", flush=True)
            for _ in range(10):
                time.sleep(0.1)
                now_pos = cursor_pos()
                travel += abs(now_pos[0] - last[0]) + abs(now_pos[1] - last[1])
                last = now_pos
        user32.SetWindowTextW(game.hwnd, f"{name}: done")
        g1 = game.settle()
        m1 = dict(motion)
        d1 = driver_counters()
        teardown()
        time.sleep(0.4)
        out = {
            "game_input_abs": g1["input_abs"] - g0["input_abs"],
            "game_mousemove": g1["mousemove"] - g0["mousemove"],
            "captured_abs": m1["abs"] - m0["abs"],
            "captured_events": m1["events"] - m0["events"],
            "driver_passed": d1["passed"] - d0["passed"],
            "driver_captured": d1["captured"] - d0["captured"],
            "cursor_travel": travel,
        }
        print(f"    {name:<13} game WM_INPUT abs={out['game_input_abs']:>7} MOUSEMOVE={out['game_mousemove']:>5} "
              f"| client captured abs={out['captured_abs']:>7} events={out['captured_events']:>5} "
              f"| driver passed={out['driver_passed']:>5} captured={out['driver_captured']:>5} "
              f"| cursor travel={out['cursor_travel']:>6} px", flush=True)
        return out

    try:
        p1 = phase(1, "pass-through", lambda: None, lambda: None)
        p2 = phase(2, "isolated", lambda: m.start(), lambda: m.stop("phase done"))
        p3 = phase(3, "released", lambda: None, lambda: None)
        p4 = phase(4, "relay", lambda: relay.start(), lambda: relay.stop("phase done"))
    finally:
        for inst in (m, relay):
            try:
                inst.stop("probe end")
            except Exception:
                pass
        panel.close()
        game.close()

    if p1["driver_passed"] == 0 and p1["game_input_abs"] == 0:
        print("  (no packets passed the filter and none reached the game: the mouse did not move in phase 1)")
    record("A1 pass-through reaches the game", p1["game_input_abs"] > 0 and p1["captured_abs"] == 0,
           f"game={p1['game_input_abs']} client_captured={p1['captured_abs']} driver_passed={p1['driver_passed']}")
    record("A2 isolated: game blind, driver sees motion", p2["game_input_abs"] == 0 and p2["captured_abs"] > 0,
           f"game={p2['game_input_abs']} client_captured={p2['captured_abs']} driver_captured={p2['driver_captured']} "
           f"driver_passed={p2['driver_passed']}")
    record("A3 released: game sees the mouse again", p3["game_input_abs"] > 0,
           f"game={p3['game_input_abs']} driver_passed={p3['driver_passed']}")
    record("A4 relay: game blind, real cursor moves",
           p4["game_input_abs"] == 0 and p4["cursor_travel"] > 0 and p4["captured_abs"] > 0,
           f"game={p4['game_input_abs']} MOUSEMOVE={p4['game_mousemove']} cursor_travel={p4['cursor_travel']} px "
           f"client_captured={p4['captured_abs']} driver_captured={p4['driver_captured']} "
           f"driver_passed={p4['driver_passed']}")

    # Show the verdict on screen too, for whoever is at the mouse.
    lines = [f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}" for r in RESULTS if str(r["check"]).startswith("A")]
    summary = "\n".join(lines) + (
        f"\n\nphase 1: game saw {p1['game_input_abs']} px, filter passed {p1['driver_passed']} packets"
        f"\nphase 2: game saw {p2['game_input_abs']} px, filter captured {p2['driver_captured']} packets"
        f"\nphase 3: game saw {p3['game_input_abs']} px, filter passed {p3['driver_passed']} packets"
        f"\nphase 4: game saw {p4['game_input_abs']} px, cursor moved {p4['cursor_travel']} px, "
        f"filter captured {p4['driver_captured']} packets"
        "\n\nAll done. You can close this.")
    user32.MessageBoxW(None, summary, "Nimbus Mouse Filter probe: result",
                       MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attended", action="store_true", help="also run the three hands-on phases")
    ap.add_argument("--seconds", type=float, default=6.0, help="length of each attended phase")
    ap.add_argument("--quick", action="store_true", help="only U1 to U6; skip the robustness checks")
    ap.add_argument("--soak", type=float, default=30.0, help="length of the idle isolation soak (U16)")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)   # internal, see _Child
    args = ap.parse_args()

    if args.child:
        return child()

    unattended()
    if not args.quick:
        robustness(args.soak)
    if args.attended:
        print("\nAttended phases (a fake game window and a panel will appear)")
        attended(args.seconds)

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
