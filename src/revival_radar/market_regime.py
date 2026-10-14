"""Per-scan market regime context.

Explanatory evidence only: computed from tokens the scan already holds in memory.
It never feeds scoring, eligibility or alert gating, and unknown values are
excluded rather than treated as zero.
"""

import math
from collections.abc import Iterable
from statistics import median

from revival_radar.models.token import TokenSnapshot

MINIMUM_SAMPLE = 5
CONTEXT_VERSION = 1
INSUFFICIENT = "insufficient_sample"


def _usable(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _median(values: list[float]) -> tuple[float | None, str | None]:
    if len(values) < MINIMUM_SAMPLE:
        return None, INSUFFICIENT
    return float(median(values)), None


def regime_context(
    tokens: Iterable[TokenSnapshot], sources: Iterable[str] = (), eligible_count: int | None = None
) -> dict:
    """Summarize the discovered universe of one chain in a single pass."""
    universe = 0
    one_hour: list[float] = []
    day: list[float] = []
    for token in tokens:
        has_1h, has_24h = _usable(token.price_change_1h), _usable(token.price_change_24h)
        if not has_1h and not has_24h:
            continue  # no usable data: outside the universe
        universe += 1
        if has_1h:
            one_hour.append(token.price_change_1h)
        if has_24h:
            day.append(token.price_change_24h)
    median_1h, reason_1h = _median(one_hour)
    median_24h, reason_24h = _median(day)
    if len(one_hour) < MINIMUM_SAMPLE:
        share, reason_share = None, INSUFFICIENT
    else:
        share, reason_share = sum(v > 0 for v in one_hour) / len(one_hour), None
    return {
        "version": CONTEXT_VERSION,
        "sources": sorted(sources),
        "universe_size": universe,
        "median_price_change_1h": median_1h,
        "median_price_change_1h_reason": reason_1h,
        "median_price_change_24h": median_24h,
        "median_price_change_24h_reason": reason_24h,
        "share_positive_1h": share,
        "share_positive_1h_reason": reason_share,
        "eligible_count": eligible_count,
        "coverage": {"price_change_1h": len(one_hour), "price_change_24h": len(day)},
        "minimum_sample": MINIMUM_SAMPLE,
        "role": "context_only",
    }
