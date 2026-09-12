"""
Property checks for tests/frame_motion.py, the frame motion measurement the
game harness uses on games with no console. Pure numpy, no hardware:
synthetic pictures are shifted, rotated and zoomed by known amounts and the
estimates have to come back within tolerance; a HUD-style change in one
corner has to read as STILL, a turn behind a static overlay has to be found
anyway, and a picture replaced wholesale has to read as MOVED.

Run from the repo root (the runner does this)::

    venv\\Scripts\\python -m tests.test_frame_motion
"""
from __future__ import annotations

import math
import sys

import numpy as np

from tests.frame_motion import (
    Thresholds, change_grid, coherent, measure, moving_peak, phase_correlate, rotation_correlate, to_gray, verdict,
)

PASSES = 0
FAILS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSES, FAILS
    if ok:
        PASSES += 1
    else:
        FAILS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def texture(h: int, w: int, seed: int, blur: int = 3) -> np.ndarray:
    """A random RGB picture with structure at every scale and in several
    directions: box-blurred noise, oriented gratings and filled rectangles.
    Isotropic noise alone has a round spectrum and cannot show a rotation."""
    rng = np.random.default_rng(seed)
    pad = blur
    noise = rng.random((h + 2 * pad, w + 2 * pad)).astype(np.float64)
    c = np.cumsum(np.cumsum(noise, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * pad + 1
    img = ((c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k))[:h, :w]
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    for _ in range(8):
        ang = rng.uniform(0.0, math.pi)
        freq = rng.uniform(0.02, 0.12)
        phase = rng.uniform(0.0, 2.0 * math.pi)
        img += 0.35 * np.sin(2.0 * math.pi * freq * (x * math.cos(ang) + y * math.sin(ang)) + phase)
    for _ in range(12):
        y0, x0 = rng.integers(0, h - 20), rng.integers(0, w - 20)
        hh, ww = rng.integers(8, 60), rng.integers(8, 60)
        img[y0:y0 + hh, x0:x0 + ww] += rng.uniform(-0.8, 0.8)
    gray = ((img - img.min()) / (img.max() - img.min() + 1e-9) * 255.0).astype(np.uint8)
    return np.stack([gray, np.roll(gray, 1, axis=1), np.roll(gray, 1, axis=0)], axis=2)


def rotate_zoom(img: np.ndarray, deg: float, zoom: float) -> np.ndarray:
    """``img`` turned ``deg`` clockwise on screen and zoomed in by ``zoom``
    about its centre, nearest neighbour, the outside filled with the mean."""
    h, w = img.shape[:2]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    ys, xs = np.mgrid[0:h, 0:w]
    x = (xs - cx) / zoom
    y = (ys - cy) / zoom
    th = math.radians(deg)
    sx = x * math.cos(th) + y * math.sin(th) + cx
    sy = -x * math.sin(th) + y * math.cos(th) + cy
    ix = np.rint(sx).astype(int)
    iy = np.rint(sy).astype(int)
    inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    out = np.empty_like(img)
    out[...] = img.reshape(-1, img.shape[2]).mean(axis=0).astype(img.dtype)
    out[inside] = img[iy[inside], ix[inside]]
    return out


def main() -> int:
    h, w = 240, 400
    a = texture(h, w, seed=1)

    # to_gray
    g = to_gray(a, scale=2)
    check("to_gray halves both dimensions at scale 2", g.shape == (h // 2, w // 2), f"{g.shape}")
    g4 = to_gray(a, skip_top=16, scale=4)
    check("to_gray drops skip_top rows before pooling", g4.shape == ((h - 16) // 4, w // 4), f"{g4.shape}")

    # phase correlation on the arrays themselves
    ga = to_gray(a, scale=1)
    gb = np.roll(ga, (-7, 12), axis=(0, 1))
    c = phase_correlate(ga, gb)
    check("a shift of (+12, -7) is measured within a pixel", abs(c["dx"] - 12) < 1.0 and abs(c["dy"] + 7) < 1.0,
          f"dx={c['dx']:.2f} dy={c['dy']:.2f} peak={c['peak']:.3f} conf={c['conf']:.0f}")
    check("a shift's moving peak is believable", coherent(c, Thresholds()), f"peak={c['peak']:.3f} conf={c['conf']:.0f}")
    check("a shift leaves nothing at the origin", c["static"] < 0.2, f"static={c['static']:.3f}")
    c = phase_correlate(ga, ga)
    check("an identical picture has its peak at the origin", c["static"] > 0.9, f"static={c['static']:.3f}")
    check("an identical picture has no moving peak worth the name", c["peak"] < 0.05, f"peak={c['peak']:.4f}")

    # measure: full-resolution pixels through the downsample
    b = np.roll(a, (-7, 12), axis=(0, 1))
    m = measure(a, b, scale=2)
    check("measure reports the shift in full-resolution pixels", abs(m["dx"] - 12) <= 2.0 and abs(m["dy"] + 7) <= 2.0,
          f"dx={m['dx']} dy={m['dy']} px={m['px']}")
    check("measure of a shift kind has no rotation keys", "deg" not in m)
    check("a whole-picture shift changes most grid cells", m["cells_moved"] >= 14, f"{m['cells_moved']}/16")

    # a turn behind a static overlay: the world shifts, a band at the bottom does not
    overlay = a.copy()
    overlay[h - 50:] = texture(50, w, seed=7)
    shifted = np.roll(overlay, (0, 18), axis=(0, 1))
    shifted[h - 50:] = overlay[h - 50:]
    m = measure(overlay, shifted, scale=2)
    check("a shift behind a static overlay is still found", abs(m["dx"] - 18) <= 2.0 and abs(m["dy"]) <= 2.0,
          f"dx={m['dx']} dy={m['dy']} conf={m['conf']} static={m['static']}")
    check("the overlay shows up as the static peak", m["static"] > 0.05, f"static={m['static']}")
    check("a shift behind an overlay is MOVED", verdict(m, Thresholds()) == "MOVED", verdict(m, Thresholds()))

    # rotation and scale
    r5 = rotate_zoom(a, 5.0, 1.0)
    rc = rotation_correlate(to_gray(a, scale=1), to_gray(r5, scale=1))
    check("a 5 degree clockwise turn measures near +5", abs(rc["deg"] - 5.0) < 1.0,
          f"deg={rc['deg']:.2f} conf={rc['conf']:.0f}")
    check("a pure rotation keeps scale near one", abs(rc["scale"] - 1.0) < 0.03, f"scale={rc['scale']:.3f}")
    r_neg = rotate_zoom(a, -8.0, 1.0)
    rc = rotation_correlate(to_gray(a, scale=1), to_gray(r_neg, scale=1))
    check("an 8 degree anticlockwise turn measures near -8", abs(rc["deg"] + 8.0) < 1.0, f"deg={rc['deg']:.2f}")
    z = rotate_zoom(a, 0.0, 1.15)
    rc = rotation_correlate(to_gray(a, scale=1), to_gray(z, scale=1))
    check("a 15 percent zoom in measures scale near 1.15", abs(rc["scale"] - 1.15) < 0.03,
          f"scale={rc['scale']:.3f} conf={rc['conf']:.0f}")
    check("a pure zoom keeps the angle near zero", abs(rc["deg"]) < 1.0, f"deg={rc['deg']:.2f}")
    m = measure(a, r5, kind="rotate", scale=1)
    check("measure of a rotate kind carries deg, scale and rot_conf",
          all(k in m for k in ("deg", "scale", "rot_conf")), f"{sorted(m)}")
    check("a 5 degree turn of a rotate kind is MOVED", verdict(m, Thresholds()) == "MOVED",
          f"{verdict(m, Thresholds())} deg={m['deg']} rot_conf={m['rot_conf']}")

    # the HUD case: a panel in the corner changes its appearance in place
    # (every pixel of it by more than the change threshold), the rest of the
    # picture the same. On a frame-sized picture, because the peak floor was
    # set on real 2560x1440 and 1280x720 frames and a 400x240 picture has a
    # noise floor a few times higher.
    frame = texture(720, 1280, seed=5)
    hud = frame.copy()
    hud[600:700, 40:300] = np.where(hud[600:700, 40:300] > 127, 255, 0).astype(np.uint8)
    m = measure(frame, hud)
    check("a changed corner's moving peak, if any, is under the threshold",
          not coherent(m, Thresholds()) or m["px"] < Thresholds().min_px,
          f"peak={m['peak']} conf={m['conf']} px={m['px']}")
    check("a changed corner is confidently the same picture", m["static_conf"] >= 8.0, f"static_conf={m['static_conf']}")
    check("a changed corner lights one grid cell", m["cells_moved"] == 1, f"{m['cells_moved']} {m['grid'][-1]}")
    check("the HUD case is STILL", verdict(m, Thresholds()) == "STILL", verdict(m, Thresholds()))

    # a picture replaced wholesale: no peak, every cell changed, MOVED anyway
    other = texture(h, w, seed=2)
    m = measure(a, other, scale=2)
    check("an unrelated picture has nothing at the origin", m["static_conf"] < 8.0,
          f"peak={m['peak']} conf={m['conf']} static_conf={m['static_conf']}")
    check("an unrelated picture changes every cell", m["cells_moved"] == 16, f"{m['cells_moved']}")
    check("a wholesale change is MOVED", verdict(m, Thresholds()) == "MOVED", verdict(m, Thresholds()))

    # a real shift is MOVED
    m = measure(a, b, scale=2)
    check("a 14 px shift is MOVED", verdict(m, Thresholds()) == "MOVED", verdict(m, Thresholds()))

    # two motions at once: a bobbing overlay (the taller peak, under the
    # threshold) and a turning world (the one that matters). PowerWash
    # Simulator's wand bobs 8 px while the camera turns.
    two = a.copy()
    two[h - 60:] = texture(60, w, seed=11)
    two_after = np.roll(two, (0, 40), axis=(0, 1))
    two_after[h - 60:] = np.roll(two[h - 60:], (0, 3), axis=(0, 1))
    m = measure(two, two_after, scale=2)
    t_bob = Thresholds(min_px=12.0)
    p = moving_peak(m, t_bob)
    check("the peaks list carries more than one motion", len(m["peaks"]) >= 2, f"{len(m['peaks'])}")
    check("a turn behind a bobbing overlay is found past the bob",
          p is not None and abs(p["dx"] - 40) <= 3.0, f"peaks={[(q['dx'], q['dy'], q['peak']) for q in m['peaks']]}")
    check("a turn behind a bobbing overlay is MOVED", verdict(m, t_bob) == "MOVED", verdict(m, t_bob))

    # a peak straddling the zero line: the top half of the picture shifts
    # (40, +2) and the bottom half (40, -2), so at scale 2 the world's peak
    # is two halves one sample either side of dy = 0, and only their sum
    # tells the truth about the shift
    straddle = a.copy()
    straddle[: h // 2] = np.roll(a[: h // 2], (2, 40), axis=(0, 1))
    straddle[h // 2:] = np.roll(a[h // 2:], (-2, 40), axis=(0, 1))
    m = measure(a, straddle, scale=2)
    p = moving_peak(m, Thresholds())
    check("a peak split across the zero line is merged into one motion",
          p is not None and abs(p["dx"] - 40) <= 3.0 and abs(p["dy"]) <= 3.0 and p.get("parts", 1) >= 2,
          f"peaks={[(q['dx'], q['dy'], q['peak'], q.get('parts')) for q in m['peaks']]}")

    # change_grid
    half = a.copy()
    half[: h // 2] = (half[: h // 2].astype(int) + 128) % 256
    grid = change_grid(a, half)
    check("change_grid puts a changed top half in the top rows",
          all(v > 95 for v in grid[0] + grid[1]) and all(v < 5 for v in grid[2] + grid[3]), f"{grid}")

    # verdict on synthetic measurements
    t = Thresholds(min_px=8.0, min_deg=1.0, conf_min=8.0, noise=400)
    still = {"kind": "shift", "px": 0.4, "peak": 0.01, "conf": 3, "static_conf": 60, "cells_moved": 2}
    check("no moving peak with a confident origin is STILL", verdict(still, t) == "STILL")
    check("coherent 20 px is MOVED", verdict({"kind": "shift", "px": 20, "peak": 0.2, "conf": 15, "static_conf": 30,
                                              "cells_moved": 9}, t) == "MOVED")
    check("a tall sidelobe ratio with a tiny peak is not a move",
          verdict({"kind": "shift", "px": 20, "peak": 0.01, "conf": 15, "static_conf": 300, "cells_moved": 0}, t) == "STILL")
    check("a low-confidence partial change is INCONCLUSIVE",
          verdict({"kind": "shift", "px": 20, "peak": 0.1, "conf": 4, "static_conf": 5, "cells_moved": 6}, t) == "INCONCLUSIVE")
    check("a spread change above the floor is MOVED without a peak",
          verdict({"kind": "shift", "px": 0.0, "peak": 0.02, "conf": 4, "static_conf": 20, "cells_moved": 6}, t, changed=6000) == "MOVED")
    check("a spread change under the floor is not a move",
          verdict({"kind": "shift", "px": 0.0, "peak": 0.02, "conf": 4, "static_conf": 20, "cells_moved": 6}, t, changed=900) == "INCONCLUSIVE")
    check("three lit cells with a confident origin are INCONCLUSIVE, not STILL",
          verdict(dict(still, cells_moved=3), t) == "INCONCLUSIVE")
    check("no measurement is n/a", verdict(None, t) == "n/a")
    rot_still = {"kind": "rotate", "px": 0.5, "peak": 0.01, "conf": 3, "static_conf": 50, "deg": 0.2, "scale": 1.001,
                 "rot_peak": 0.01, "rot_conf": 3, "rot_static_conf": 40, "cells_moved": 1}
    check("rotate kind: a fifth of a degree is STILL", verdict(rot_still, t) == "STILL")
    rot_moved = dict(rot_still, deg=6.0, rot_peak=0.1, rot_conf=12)
    check("rotate kind: six degrees is MOVED even with no translation peak", verdict(rot_moved, t) == "MOVED")
    zoomed = dict(rot_still, scale=1.2, rot_peak=0.1, rot_conf=12)
    check("rotate kind: a 20 percent zoom is MOVED", verdict(zoomed, t) == "MOVED")

    # thresholds from idle samples
    ti = Thresholds.from_idle([{"px": 1.0, "peak": 0.2, "conf": 20}, {"px": 6.5, "peak": 0.2, "conf": 20},
                               {"px": 2.0, "peak": 0.2, "conf": 20}], changed=[300, 900, 500])
    check("idle thresholds double the largest idle shift", abs(ti.min_px - 13.0) < 1e-9, f"{ti.min_px}")
    check("idle thresholds take the largest idle changed count as the floor", ti.noise == 900, f"{ti.noise}")
    ti = Thresholds.from_idle([{"px": 40.0, "peak": 0.2, "conf": 2}])
    check("an idle shift nobody believes does not raise the threshold", ti.min_px == 8.0, f"{ti.min_px}")
    ti = Thresholds.from_idle([{"px": 0.0, "peak": 0.9, "conf": 50}])
    check("idle thresholds never drop below the floor", ti.min_px == 8.0 and ti.min_deg == 1.0, f"{ti.as_dict()}")

    # the scene's own idle offsets: a texture that correlates with itself a
    # period away, or an animation, is not the camera
    idle_m = {"px": 48.0, "peaks": [{"dx": 0.0, "dy": 48.0, "px": 48.0, "peak": 0.06, "conf": 14, "parts": 2},
                                    {"dx": 30.0, "dy": 0.0, "px": 30.0, "peak": 0.03, "conf": 9, "parts": 1}]}
    ti = Thresholds.from_idle([idle_m])
    check("an idle pair of halves does not raise the shift threshold", ti.min_px == 8.0, f"{ti.min_px}")
    check("idle offsets of any real height are kept for exclusion", len(ti.idle_peaks) == 2, f"{ti.idle_peaks}")
    at_idle = {"peaks": [{"dx": 3.0, "dy": 46.0, "px": 46.1, "peak": 0.2, "conf": 30, "parts": 1}]}
    check("a step peak at an idle offset is not motion", moving_peak(at_idle, ti) is None)
    elsewhere = {"peaks": [{"dx": 80.0, "dy": 0.0, "px": 80.0, "peak": 0.2, "conf": 30, "parts": 1}]}
    check("a step peak away from the idle offsets is motion", moving_peak(elsewhere, ti) is not None)
    near_idle = {"peaks": [{"dx": 38.0, "dy": 2.0, "px": 38.1, "peak": 0.2, "conf": 30, "parts": 1}]}
    check("a step peak within 12 px of an idle offset is not motion", moving_peak(near_idle, ti) is None)
    check("a believed single idle shift still raises the threshold",
          Thresholds.from_idle([{"peaks": [{"dx": 20.0, "dy": 0.0, "px": 20.0, "peak": 0.2, "conf": 30,
                                            "parts": 1}]}]).min_px == 40.0)

    print(f"\n{PASSES}/{PASSES + FAILS} checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
