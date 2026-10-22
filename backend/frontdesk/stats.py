"""Small, dependency-free statistics used by the owner report and the insights API.

Rates are shown with a 95% Wilson interval, not as bare percentages: "31 of 212 handed over"
is 14.6% (10.5% to 20.0%), and an owner deciding whether to hire should see that range.
"""

from __future__ import annotations

import math

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float, float]:
    """Point estimate and Wilson score interval. Well-behaved at 0, n and small n."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def fmt_rate(successes: int, n: int) -> str:
    p, lo, hi = wilson(successes, n)
    return f"{successes} of {n} ({p:.0%}; 95% CI {lo:.0%} to {hi:.0%})"
