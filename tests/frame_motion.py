"""
Frame motion for the game harness: what the camera did between two captures.

Pure numpy, no hardware, no Qt. ``tests/game_harness.py`` calls ``measure``
on the before and after frames of every step, and ``verdict`` turns the
result into MOVED, STILL or INCONCLUSIVE. ``tests/test_frame_motion.py``
checks both against synthetic pictures, so this file is part of the fast
suite as well as the game one.

Why this exists
---------------
The frame-differencing oracle used to count sampled pixels that changed and
call the step MOVED above three times an idle second's count. A count
cannot tell a camera from a HUD: on Halo Wars an animated unit panel
changed about 1,300 samples, which one run's idle floor called STILL and
the next run's called MOVED, and the MOVED one was wrong. A camera move is
a *coherent* change, the whole picture going one way, and that is what is
measured here.

What is measured
----------------
``shift``
    Phase correlation. Grayscale, downsample, Hann window, the normalised
    cross-power spectrum of the two FFTs, inverse FFT. The surface has a
    peak at every offset by which some part of the picture moved, and two
    of them matter. The peak at the origin is everything that stayed put:
    the HUD, a weapon viewmodel, PowerWash Simulator's wand, and it is
    reported as ``static``. The best peak *away* from the origin is the
    part that moved, reported as ``dx``, ``dy`` with its height ``peak``
    and ``conf``, its height in standard deviations of the rest of the
    surface. Both have to clear a floor before the offset is believed:
    measured on the saved frames of five games, a real move has a peak of
    0.06 or more and spurious sidelobes 0.04 or less, while the sidelobe
    ratio alone let an idle Halo Wars picture (peak 0.002) through.
    Keeping the static and moving peaks apart is what lets a turn behind a
    big static overlay register at all; a single-peak estimate locked onto
    the overlay and called a whole sweep of PowerWash Simulator turns "no
    shift".
``rotate``
    The same two-peak correlation on the log-polar resampling of the two
    magnitude spectra (Reddy and Chatterji, 1996), taken over the central
    square of the picture, because the map is only isotropic when the two
    frequency axes have the same resolution. The magnitude spectrum
    ignores translation, rotates with the picture and scales inversely
    with it, so a rotation about the centre becomes a shift along the
    angle axis and a zoom a shift along the log-radius axis. Halo Wars is
    the game that asks for it: its right stick swings the camera around
    the point under it and zooms. The angle is signed, positive clockwise
    on screen, and only unambiguous below 90 degrees because the spectrum
    is symmetric under a half turn. It recovers a rotation of the picture;
    a game camera swinging round a 3D scene is only approximately that,
    and a swing past the overlap gives no peak.
``grid``
    A 4 by 4 grid of the changed-sample fraction, in percent, using the
    same sampler as ``frame_diff``. A camera move changes every cell; a
    HUD animation changes a few at the bottom. It is what separated the
    Halo Wars false positive from a real rotation, and it is the fallback
    when the camera moved too far for the correlation to find any overlap:
    a picture that changed wholesale is MOVED even without a peak, and so
    is a change spread over a quarter of the picture that is well above
    the idle floor (a walk forward is an expansion, not a shift).

What the numbers are not
------------------------
The shift is a measurement of whatever moved most coherently, and it is
only the camera's when the world is what moved. In a scene with a
repeating texture the peak sits at the true shift modulo the texture's
period: Left 4 Dead 2's safe room has wallpaper striped every 56 pixels,
and every turn in the saved sweep measured its true shift less a whole
number of stripes, sign and all. In a scene with a swinging flashlight,
an idle second measures the flashlight. The verdict survives both, because
any coherent move past the idle-derived threshold is a move; the pixel
figure is a number to band only where a run shows it repeats.

Sign conventions
----------------
``dx`` and ``dy`` are the offset of content from the first frame to the
second, in full-resolution pixels, right and down positive. ``deg`` is
positive for a clockwise turn of the picture on screen. ``scale`` is above
one when the second picture is a zoom in on the first.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULT_SCALE = 4        # downsample factor before the FFT (2560 wide becomes 640)
N_ANGLES = 360           # log-polar rows over 180 degrees: half a degree per row
R_MIN = 3.0              # smallest spectrum radius sampled by the log-polar map
CHANGE_STEP = 6          # the sampler frame_diff uses
CHANGE_THRESHOLD = 60    # summed RGB delta that counts as a changed sample
GRID_ROWS = 4
GRID_COLS = 4
CONF_CAP = 9999.0        # identical pictures have a zero-variance surface


# ---- pictures --------------------------------------------------------------------
def to_gray(frame: np.ndarray, skip_top: int = 0, scale: int = DEFAULT_SCALE) -> np.ndarray:
    """Grayscale float32, mean-pooled by ``scale``, the top ``skip_top`` rows dropped."""
    a = np.asarray(frame)
    if a.ndim == 3:
        a = a[skip_top:, :, :3].astype(np.float32).mean(axis=2)
    else:
        a = a[skip_top:].astype(np.float32)
    h = (a.shape[0] // scale) * scale
    w = (a.shape[1] // scale) * scale
    if h == 0 or w == 0:
        raise ValueError(f"frame too small to downsample: {a.shape}")
    return a[:h, :w].reshape(h // scale, scale, w // scale, scale).mean(axis=(1, 3))


def _hann(h: int, w: int) -> np.ndarray:
    return np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)


def _parabolic(left: float, centre: float, right: float) -> float:
    """Sub-sample offset of a peak from a parabola through it and its neighbours."""
    denom = left - 2.0 * centre + right
    if denom >= 0.0 or abs(denom) < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, 0.5 * (left - right) / denom))


def _signed(i: int, n: int) -> int:
    return i if i <= n // 2 else i - n


def _psr(r: np.ndarray, mask: np.ndarray, peak: float) -> float:
    rest = r[mask]
    if rest.size < 8:
        return 0.0
    sd = float(rest.std())
    if sd < 1e-9:
        return CONF_CAP
    return min(CONF_CAP, float((peak - rest.mean()) / sd))


def phase_correlate(a: np.ndarray, b: np.ndarray, exclude: int = 1, n_peaks: int = 3,
                    merge: float = 2.5) -> Dict[str, Any]:
    """The correlation surface of ``a`` against ``b``, read at its peaks.

    Returns a dict: ``dx``, ``dy`` (the tallest peak more than ``exclude``
    samples from the origin, refined to a fraction of a sample; content at
    (x, y) in ``a`` sits at (x + dx, y + dy) in ``b``), ``peak`` and
    ``conf`` (that peak's height, and its height in standard deviations of
    the rest of the surface), ``peaks`` (the ``n_peaks`` tallest such
    peaks, tallest first, each a dict of the same four keys plus
    ``parts``: a picture with two motions in it, a bobbing overlay and a
    turning world, has a peak for each), and ``static`` and
    ``static_conf`` (the same for the origin, which is everything that did
    not move). Offsets are in samples of the arrays given.

    A motion whose offset sits on the zero line of either axis, a pure
    yaw or a pure scroll, with a little jitter the other way, shows up as
    two halves of a peak, one each side of the line, and each half can
    sit under the height floor while the motion is plain. PowerWash
    Simulator's one-pixel drag did exactly that: a 63 px shift as two
    halves of 0.035 at 4 px up and 4 px down. Raw peaks within ``merge``
    samples of a taller one are summed into it (``parts`` counts them),
    and the floors are applied to the sum.
    """
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a = a[:h, :w].astype(np.float32)
    b = b[:h, :w].astype(np.float32)
    win = _hann(h, w)
    fa = np.fft.fft2((a - a.mean()) * win)
    fb = np.fft.fft2((b - b.mean()) * win)
    cross = fb * np.conj(fa)
    cross /= np.maximum(np.abs(cross), 1e-9)
    r = np.fft.ifft2(cross).real

    yy = np.array([_signed(i, h) for i in range(h)])[:, None]
    xx = np.array([_signed(j, w) for j in range(w)])[None, :]
    near = (yy * yy + xx * xx) <= exclude * exclude
    static = float(r[near].max())

    taken = near.copy()
    found: List[Tuple[int, int, float]] = []
    for _ in range(max(1, n_peaks) * 3):
        r_moving = np.where(taken, -np.inf, r)
        py, px = (int(v) for v in np.unravel_index(int(np.argmax(r_moving)), r.shape))
        if not np.isfinite(r_moving[py, px]):
            break
        found.append((py, px, float(r[py, px])))
        taken[max(0, py - 3):py + 4, max(0, px - 3):px + 4] = True
    rest_mask = ~taken
    static_conf = _psr(r, rest_mask, static)

    clusters: List[Dict[str, float]] = []
    for py, px, peak in found:                      # tallest first
        sub_y = _parabolic(float(r[(py - 1) % h, px]), peak, float(r[(py + 1) % h, px]))
        sub_x = _parabolic(float(r[py, (px - 1) % w]), peak, float(r[py, (px + 1) % w]))
        dx = float(_signed(px, w) + sub_x)
        dy = float(_signed(py, h) + sub_y)
        for c in clusters:
            if math.hypot(dx - c["dx"], dy - c["dy"]) <= merge:
                total = c["peak"] + peak
                c["dx"] = (c["dx"] * c["peak"] + dx * peak) / total
                c["dy"] = (c["dy"] * c["peak"] + dy * peak) / total
                c["peak"] = total
                c["parts"] += 1
                break
        else:
            clusters.append({"dx": dx, "dy": dy, "peak": peak, "parts": 1})
    clusters.sort(key=lambda c: -c["peak"])
    peaks: List[Dict[str, float]] = [
        {"dx": c["dx"], "dy": c["dy"], "peak": c["peak"], "conf": _psr(r, rest_mask, c["peak"]),
         "parts": int(c["parts"])}
        for c in clusters[:max(1, n_peaks)]]
    first = peaks[0] if peaks else {"dx": 0.0, "dy": 0.0, "peak": 0.0, "conf": 0.0, "parts": 0}
    return {**first, "peaks": peaks, "static": static, "static_conf": static_conf}


def log_polar(spec: np.ndarray, n_angles: int = N_ANGLES, n_radii: Optional[int] = None,
              r_min: float = R_MIN) -> Tuple[np.ndarray, float]:
    """Resample a centred magnitude spectrum onto (angle, log radius): rows are
    angles from 0 to 180 degrees, columns radii from ``r_min`` to the largest
    that fits, spaced by a constant factor, which is returned beside the map."""
    h, w = spec.shape
    cy, cx = h / 2.0, w / 2.0
    max_r = min(cx, cy) - 1.0
    if n_radii is None:
        n_radii = max(8, int(max_r))
    base = math.exp(math.log(max_r / r_min) / n_radii)
    radii = r_min * base ** np.arange(n_radii, dtype=np.float64)
    theta = np.linspace(0.0, math.pi, n_angles, endpoint=False)
    ys = cy + radii[None, :] * np.sin(theta)[:, None]
    xs = cx + radii[None, :] * np.cos(theta)[:, None]
    iy = np.clip(np.rint(ys).astype(int), 0, h - 1)
    ix = np.clip(np.rint(xs).astype(int), 0, w - 1)
    return spec[iy, ix], base


def _centre_square(a: np.ndarray) -> np.ndarray:
    h, w = a.shape
    n = min(h, w)
    y0 = (h - n) // 2
    x0 = (w - n) // 2
    return a[y0:y0 + n, x0:x0 + n]


def _log_spectrum(a: np.ndarray) -> np.ndarray:
    h, w = a.shape
    f = np.fft.fftshift(np.fft.fft2((a - a.mean()) * _hann(h, w)))
    mag = np.log1p(np.abs(f))
    # Reddy and Chatterji's high-pass emphasis, so the low frequencies that
    # every picture shares do not vote for "no rotation" on their own.
    y = np.cos(np.pi * (np.arange(h) / h - 0.5))
    x = np.cos(np.pi * (np.arange(w) / w - 0.5))
    X = y[:, None] * x[None, :]
    return mag * (1.0 - X) * (2.0 - X)


def rotation_correlate(a: np.ndarray, b: np.ndarray, n_angles: int = N_ANGLES) -> Dict[str, float]:
    """The rotation and scale taking ``a`` to ``b`` about the picture's centre,
    from the central square of both.

    Returns ``deg`` (positive clockwise on screen, in (-90, 90]), ``scale``
    (above one for a zoom in), ``peak`` and ``conf`` for the best peak away
    from "no change", and ``static`` and ``static_conf`` for the peak at it.
    """
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    la = _log_spectrum(_centre_square(a[:h, :w].astype(np.float32)))
    lb = _log_spectrum(_centre_square(b[:h, :w].astype(np.float32)))
    lpa, base = log_polar(la, n_angles=n_angles)
    lpb, _ = log_polar(lb, n_angles=n_angles)
    c = phase_correlate(lpa, lpb)
    deg = c["dy"] * (180.0 / n_angles)
    if deg <= -90.0:
        deg += 180.0
    return {"deg": float(deg), "scale": float(base ** (-c["dx"])), "peak": c["peak"], "conf": c["conf"],
            "static": c["static"], "static_conf": c["static_conf"]}


def change_grid(a: np.ndarray, b: np.ndarray, rows: int = GRID_ROWS, cols: int = GRID_COLS,
                step: int = CHANGE_STEP, threshold: int = CHANGE_THRESHOLD,
                skip_top: int = 0) -> List[List[float]]:
    """The changed-sample fraction of each cell of a ``rows`` by ``cols`` grid,
    in percent, from the same sampler as ``frame_diff``."""
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    sa = a[skip_top:h:step, 0:w:step].astype(np.int16)
    sb = b[skip_top:h:step, 0:w:step].astype(np.int16)
    changed = np.abs(sa - sb).sum(axis=2) > threshold
    gh, gw = changed.shape
    grid: List[List[float]] = []
    for i in range(rows):
        row: List[float] = []
        for j in range(cols):
            cell = changed[i * gh // rows:(i + 1) * gh // rows, j * gw // cols:(j + 1) * gw // cols]
            row.append(round(100.0 * float(cell.mean()), 1) if cell.size else 0.0)
        grid.append(row)
    return grid


# ---- the measurement -------------------------------------------------------------
def measure(a: np.ndarray, b: np.ndarray, kind: str = "shift", skip_top: int = 0,
            scale: int = DEFAULT_SCALE, rotation: Optional[bool] = None,
            cell_pct: float = 20.0) -> Dict[str, Any]:
    """Everything the harness records about the motion from frame ``a`` to
    frame ``b``: the moving peak (``dx``, ``dy``, ``px``, ``peak``,
    ``conf``), the static one (``static``, ``static_conf``), with ``kind ==
    "rotate"`` the rotation and scale too (``deg``, ``scale``, ``rot_peak``,
    ``rot_conf``, ``rot_static_conf``), and the change grid with the count
    of cells past ``cell_pct`` percent. Pixel values are in full-resolution
    pixels."""
    ga = to_gray(a, skip_top, scale)
    gb = to_gray(b, skip_top, scale)
    c = phase_correlate(ga, gb)
    out: Dict[str, Any] = {
        "kind": kind,
        "dx": round(c["dx"] * scale, 1), "dy": round(c["dy"] * scale, 1),
        "px": round(math.hypot(c["dx"], c["dy"]) * scale, 1),
        "peak": round(c["peak"], 4), "conf": round(c["conf"], 1),
        "peaks": [{"dx": round(p["dx"] * scale, 1), "dy": round(p["dy"] * scale, 1),
                   "px": round(math.hypot(p["dx"], p["dy"]) * scale, 1),
                   "peak": round(p["peak"], 4), "conf": round(p["conf"], 1), "parts": int(p.get("parts", 1))}
                  for p in c["peaks"]],
        "static": round(c["static"], 4), "static_conf": round(c["static_conf"], 1),
    }
    if rotation if rotation is not None else kind == "rotate":
        rc = rotation_correlate(ga, gb)
        out.update({"deg": round(rc["deg"], 2), "scale": round(rc["scale"], 4),
                    "rot_peak": round(rc["peak"], 4), "rot_conf": round(rc["conf"], 1),
                    "rot_static_conf": round(rc["static_conf"], 1)})
    grid = change_grid(a, b, skip_top=skip_top)
    out["grid"] = grid
    out["cells"] = GRID_ROWS * GRID_COLS
    out["cells_moved"] = sum(1 for row in grid for v in row if v >= cell_pct)
    return out


class Thresholds:
    """What counts as motion, set from the idle samples of a run.

    Parameters
    ----------
    min_px : float
        Smallest coherent shift that is a move, in full-resolution pixels.
    min_deg : float
        Smallest rotation that is a move (``rotate`` kind only).
    min_scale : float
        Smallest ``abs(log(scale))`` that is a zoom (``rotate`` kind only).
    conf_min : float
        The sidelobe ratio a peak needs before its offset is believed.
    peak_min : float
        The height a peak needs as well. Real moves measured 0.06 and up
        on five games' saved frames, sidelobes 0.04 and down.
    whole_cells : int
        Cells past the change fraction that make a picture "changed
        wholesale": MOVED with no peak, because the camera outran the overlap.
    spread_cells : int
        Cells past the change fraction that, with ``changed`` above
        ``spread_factor`` times the idle floor, are a move without a peak:
        a walk forward is an expansion and has no single offset.
    noise : int
        The idle floor in changed samples (the largest of several idle
        measurements), for the spread rule.
    idle_peaks : list of (dx, dy)
        Offsets the idle pictures correlate at on their own: a repeating
        texture correlates with itself a period away whether or not
        anything moved (Elden Ring's idle frames carry peaks near 48 px
        vertically in every sample, at 97 changed samples), and a scene's
        idle animation has its own offset. A step's peak within
        ``exclude_px`` of one of these is the scene, not the camera.
    exclude_px : float
        The radius of that exclusion, in full-resolution pixels.
    """

    def __init__(self, min_px: float = 8.0, min_deg: float = 1.0, min_scale: float = 0.03,
                 conf_min: float = 8.0, peak_min: float = 0.05, whole_cells: int = 12,
                 spread_cells: int = 3, spread_factor: float = 3.0, noise: int = 0,
                 idle_peaks: Optional[List[Any]] = None, exclude_px: float = 12.0) -> None:
        self.min_px = float(min_px)
        self.min_deg = float(min_deg)
        self.min_scale = float(min_scale)
        self.conf_min = float(conf_min)
        self.peak_min = float(peak_min)
        self.whole_cells = int(whole_cells)
        self.spread_cells = int(spread_cells)
        self.spread_factor = float(spread_factor)
        self.noise = int(noise)
        self.idle_peaks: List[Tuple[float, float]] = [(float(p[0]), float(p[1])) for p in (idle_peaks or [])]
        self.exclude_px = float(exclude_px)

    @classmethod
    def from_idle(cls, idle: List[Dict[str, Any]], changed: Optional[List[int]] = None,
                  floor_px: float = 8.0, floor_deg: float = 1.0, **kw: Any) -> "Thresholds":
        """Thresholds from the motion measured over several idle samples.

        The shift threshold is twice the largest believed idle shift (a
        single peak; a pair of halves merged across a zero line in an idle
        picture is a texture's self-correlation, and goes to the exclusion
        list instead), never below the floors; the noise floor is the
        largest idle changed count; and every idle peak of any real height
        (half the peak floor and up) becomes an offset later steps may not
        claim as motion.
        """
        base = cls(**kw)
        px = max([float(p.get("px", 0.0)) for m in idle for p in _peaks(m)
                  if coherent(p, base) and int(p.get("parts", 1)) == 1] + [0.0])
        deg = max([abs(float(m.get("deg", 0.0))) for m in idle if rot_coherent(m, base)] + [0.0])
        sc = max([abs(math.log(max(1e-6, float(m.get("scale", 1.0))))) for m in idle if rot_coherent(m, base)]
                 + [0.0])
        noise = max([int(c) for c in (changed or []) if c is not None] + [0])
        idle_peaks = [(float(p.get("dx", 0.0)), float(p.get("dy", 0.0))) for m in idle for p in _peaks(m)
                      if float(p.get("peak", 0.0)) >= 0.5 * base.peak_min]
        return cls(min_px=max(floor_px, 2.0 * px), min_deg=max(floor_deg, 2.0 * deg),
                   min_scale=max(0.03, 2.0 * sc), noise=noise, idle_peaks=idle_peaks, **kw)

    def near_idle(self, p: Dict[str, Any]) -> bool:
        """Whether a peak sits at an offset the idle pictures correlate at on their own."""
        dx, dy = float(p.get("dx", 0.0)), float(p.get("dy", 0.0))
        return any(math.hypot(dx - ix, dy - iy) <= self.exclude_px for ix, iy in self.idle_peaks)

    def as_dict(self) -> Dict[str, Any]:
        return {"min_px": self.min_px, "min_deg": self.min_deg, "min_scale": self.min_scale,
                "conf_min": self.conf_min, "peak_min": self.peak_min, "whole_cells": self.whole_cells,
                "spread_cells": self.spread_cells, "spread_factor": self.spread_factor, "noise": self.noise,
                "idle_peaks": [[round(x, 1), round(y, 1)] for x, y in self.idle_peaks],
                "exclude_px": self.exclude_px}


def _peaks(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    peaks = m.get("peaks")
    if peaks:
        return list(peaks)
    return [m] if "px" in m or "dx" in m else []


def coherent(p: Dict[str, Any], t: Thresholds) -> bool:
    """Whether a moving peak (a ``measure`` result, or one entry of its
    ``peaks``) is believable: tall enough and clear of the sidelobes."""
    return float(p.get("conf", 0.0)) >= t.conf_min and float(p.get("peak", 0.0)) >= t.peak_min


def moving_peak(m: Optional[Dict[str, Any]], t: Thresholds) -> Optional[Dict[str, Any]]:
    """The tallest believable peak past the shift threshold, or ``None``.
    This is the shift the verdict is made on, and the number worth banding."""
    if not m:
        return None
    for p in _peaks(m):
        if (coherent(p, t) and not t.near_idle(p)
                and float(p.get("px", math.hypot(p.get("dx", 0.0), p.get("dy", 0.0)))) >= t.min_px):
            return p
    return None


def rot_coherent(m: Dict[str, Any], t: Thresholds) -> bool:
    """The same for the rotation peak of a ``rotate`` measurement."""
    return ("deg" in m and float(m.get("rot_conf", 0.0)) >= t.conf_min
            and float(m.get("rot_peak", 0.0)) >= t.peak_min)


def verdict(m: Optional[Dict[str, Any]], t: Thresholds, changed: Optional[int] = None) -> str:
    """MOVED, STILL or INCONCLUSIVE from a ``measure`` result.

    MOVED is any of: a coherent shift past the threshold; for the
    ``rotate`` kind a coherent rotation or zoom past theirs; a picture that
    changed wholesale; a change spread over ``spread_cells`` cells and
    ``spread_factor`` times the idle floor. STILL is none of those with a
    confident peak at the origin (the same picture, whatever animated
    inside it) and no more than two cells changed. Anything else is
    INCONCLUSIVE.
    """
    if m is None:
        return "n/a"
    if moving_peak(m, t) is not None:
        return "MOVED"
    rotate = m.get("kind") == "rotate" and "deg" in m
    if rotate and rot_coherent(m, t):
        deg = abs(float(m.get("deg", 0.0)))
        zoom = abs(math.log(max(1e-6, float(m.get("scale", 1.0)))))
        if deg >= t.min_deg or zoom >= t.min_scale:
            return "MOVED"
    cells = int(m.get("cells_moved", 0))
    if cells >= t.whole_cells:
        return "MOVED"
    if changed is not None and cells >= t.spread_cells and changed > t.spread_factor * max(t.noise, 50):
        return "MOVED"
    if float(m.get("static_conf", 0.0)) >= t.conf_min and cells <= 2:
        return "STILL"
    return "INCONCLUSIVE"


def describe(m: Optional[Dict[str, Any]]) -> str:
    """One short string for a results line."""
    if not m:
        return "no motion"
    s = (f"shift=({m.get('dx', 0):+.0f},{m.get('dy', 0):+.0f}) px peak={m.get('peak', 0):.2f} "
         f"conf={m.get('conf', 0):.0f}")
    others = [p for p in (m.get("peaks") or [])[1:] if float(p.get("peak", 0.0)) >= 0.05]
    if others:
        s += " also " + " ".join(f"({p['dx']:+.0f},{p['dy']:+.0f})@{p['peak']:.2f}"
                                 + (f"x{p['parts']}" if int(p.get("parts", 1)) > 1 else "") for p in others)
    s += f" static={m.get('static', 0):.2f}"
    if "deg" in m:
        s += (f" rot={m['deg']:+.1f} deg scale={m.get('scale', 1.0):.3f} peak={m.get('rot_peak', 0):.2f} "
              f"conf={m.get('rot_conf', 0):.0f}")
    s += f" cells={m.get('cells_moved', 0)}/{m.get('cells', GRID_ROWS * GRID_COLS)}"
    return s
