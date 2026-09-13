"""
Property checks for the stick shaping formula in ``src/config.py``.

Layer 1 of the verification plan in ``docs/vision/AIM_ASSISTANCE.md``
section 12. Pure Python: no Qt, no driver, no hardware. Every claim the
pipeline makes about its output is checked here against
:func:`src.config.shape_magnitude` and :func:`src.config.shape_vector`:

- linear defaults reach 0.95, where the old double chain reached 0.901
- the smallest movement lands at exactly the anti-deadzone plus buffer and
  full deflection still hits the ceiling; the floor never exceeds the
  ceiling; a buffer above a zero anti-deadzone adds nothing
- the deadzone is radial: a diagonal inside it is zero
- diagonals match cardinals in magnitude across the sensitivity range and
  nothing exceeds 1, including convex curves and input that overflows the
  unit circle
- the slider-to-exponent mapping is unchanged
- gain scales the post-deadzone magnitude
- direction and sign are preserved

Run::

    venv\\Scripts\\python tests\\test_stick_shaping.py
"""
from __future__ import annotations

import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src.config import (  # noqa: E402
    XINPUT_LEFT_THUMB_DEADZONE,
    XINPUT_RIGHT_THUMB_DEADZONE,
    sensitivity_power,
    shape_magnitude,
    shape_vector,
)

FAILS = 0
PASSES = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS, PASSES
    if cond:
        PASSES += 1
    else:
        FAILS += 1
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  ({detail})" if detail else ""))


def main() -> int:
    print("Shaping property checks")

    # Defaults: linear, no inner deadzone, 5% cap, no anti-deadzone
    check("linear default maps 1.0 to 0.95", abs(shape_magnitude(1.0) - 0.95) < 1e-9, f"{shape_magnitude(1.0):.4f}")
    check("linear default maps 0.5 to 0.475", abs(shape_magnitude(0.5) - 0.475) < 1e-9)
    check("zero in, zero out", shape_magnitude(0.0) == 0.0)
    old_chain = ((0.95 - 0.025) / 0.975) * 0.95
    check("the old widget-then-global chain reached only 0.901", abs(old_chain - 0.9013) < 1e-3, f"{old_chain:.4f}")

    # Anti-deadzone floor and buffer
    p = dict(anti_deadzone=XINPUT_RIGHT_THUMB_DEADZONE, anti_deadzone_buffer=0.02)
    tiny = shape_magnitude(0.001, **p)
    check("smallest movement lands at floor = anti-deadzone + buffer",
          abs(tiny - (XINPUT_RIGHT_THUMB_DEADZONE + 0.02)) < 1e-3, f"{tiny:.4f}")
    check("full deflection still hits the 0.95 ceiling", abs(shape_magnitude(1.0, **p) - 0.95) < 1e-9)
    check("a buffer above a zero anti-deadzone adds no floor",
          shape_magnitude(0.001, anti_deadzone=0.0, anti_deadzone_buffer=0.02) < 0.01)
    check("the floor never exceeds the ceiling",
          shape_magnitude(0.5, anti_deadzone=0.99, extremity_dead_zone=10) <= 0.9 + 1e-9)
    check("output rises monotonically above the floor",
          all(shape_magnitude(i / 100, **p) <= shape_magnitude((i + 1) / 100, **p) + 1e-12 for i in range(100)))

    # Inner deadzone: 10% on the slider is 0.025 of the range, radially
    check("inside the deadzone is exactly zero", shape_magnitude(0.02, dead_zone=10.0) == 0.0)
    check("just outside the deadzone is small and positive", 0 < shape_magnitude(0.03, dead_zone=10.0) < 0.02)
    vx, vy = shape_vector(0.017, 0.017, dead_zone=10.0)   # magnitude 0.024, inside radially
    check("a diagonal inside the radial deadzone is zero", (vx, vy) == (0.0, 0.0))
    ax, _ = shape_vector(0.024, 0.0, dead_zone=10.0)
    check("a cardinal at the same magnitude is zero too", ax == 0.0)

    # Radial: diagonals match cardinals, magnitude never exceeds 1
    radial_ok = True
    bound_ok = True
    for sens in (0, 20, 50, 80, 100):
        for m in (0.1, 0.5, 0.707, 1.0):
            cx, cy = shape_vector(m, 0.0, sensitivity=sens, extremity_dead_zone=0)
            dx, dy = shape_vector(m / math.sqrt(2), m / math.sqrt(2), sensitivity=sens, extremity_dead_zone=0)
            if abs(math.hypot(dx, dy) - math.hypot(cx, cy)) > 1e-9:
                radial_ok = False
            ox, oy = shape_vector(m * 3, m * 3, sensitivity=sens, extremity_dead_zone=0,
                                  anti_deadzone=0.3, anti_deadzone_buffer=0.05)
            if math.hypot(ox, oy) > 1.0 + 1e-9:
                bound_ok = False
    check("diagonals match cardinals in magnitude across the sensitivity range", radial_ok)
    check("magnitude never exceeds 1, even for overflowing input with a floor", bound_ok)
    check("a convex curve keeps a clamped diagonal inside the circle",
          math.hypot(*shape_vector(0.707, 0.707, sensitivity=90, extremity_dead_zone=0)) <= 1.0 + 1e-9)

    # The sensitivity slider maps to the same exponents as before
    check("sensitivity 50 is linear (power 1)", sensitivity_power(50) == 1.0)
    check("sensitivity 20 is power 2.8", abs(sensitivity_power(20) - 2.8) < 1e-9)
    check("sensitivity 0 is power 4.0", abs(sensitivity_power(0) - 4.0) < 1e-9)
    check("sensitivity 100 is power 0.1", abs(sensitivity_power(100) - 0.1) < 1e-9)
    check("the old 20% through 20% double curve was power 7.84", abs(2.8 * 2.8 - 7.84) < 1e-9)

    # Gain (the precision modifier)
    quarter = shape_magnitude(1.0, extremity_dead_zone=0, gain=0.25)
    check("gain 0.25 at full deflection gives 0.25", abs(quarter - 0.25) < 1e-9, f"{quarter:.4f}")
    q2 = shape_magnitude(1.0, extremity_dead_zone=0, gain=0.25, anti_deadzone=0.265, anti_deadzone_buffer=0.02)
    check("gain 0.25 with an anti-deadzone sits above the floor and below half", 0.285 < q2 < 0.5, f"{q2:.3f}")
    check("gain 0 gives zero", shape_magnitude(1.0, gain=0.0) == 0.0)

    # Direction and sign
    ox, oy = shape_vector(-0.3, 0.4, extremity_dead_zone=0)
    check("direction is preserved", abs(math.atan2(oy, ox) - math.atan2(0.4, -0.3)) < 1e-9)
    check("sign is preserved on a bipolar axis", shape_vector(-1.0, 0.0)[0] < 0)
    check("an input of (0, 0) is (0, 0)", shape_vector(0.0, 0.0, anti_deadzone=0.3) == (0.0, 0.0))

    print(f"\nXInput defaults: left {XINPUT_LEFT_THUMB_DEADZONE:.4f} right {XINPUT_RIGHT_THUMB_DEADZONE:.4f}")
    print(f"\n{PASSES}/{PASSES + FAILS} checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
