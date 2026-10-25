"""Small, dependency-free statistics helpers.

Percentiles use linear interpolation between order statistics (the same
definition as numpy's default). Example: p95 of 1..100 is 95.05.
"""
import math
from typing import Iterable, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Return the p-th percentile (0-100) with linear interpolation.

    Raises ValueError on an empty sequence: a missing metric must never be
    silently reported as zero.
    """
    if not values:
        raise ValueError("percentile() requires at least one value")
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = (p / 100.0) * (len(s) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return float(s[lo] + (s[hi] - s[lo]) * (rank - lo))


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean() requires at least one value")
    return float(sum(values)) / len(values)


def stdev(values: Sequence[float]) -> float:
    """Sample standard deviation (n-1). Returns 0.0 for a single value."""
    n = len(values)
    if n == 0:
        raise ValueError("stdev() requires at least one value")
    if n == 1:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (n - 1))


def summarize(values: Sequence[float]) -> dict:
    """Mean, median, p95, p99, max and stdev for a non-empty sequence."""
    return {
        "mean": round(mean(values), 2),
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "p99": round(percentile(values, 99), 2),
        "max": round(float(max(values)), 2),
        "stdev": round(stdev(values), 2),
    }


def longest_run_at_or_above(values: Iterable[float], threshold: float) -> int:
    """Length of the longest consecutive run of values >= threshold."""
    best = cur = 0
    for v in values:
        if v >= threshold:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
