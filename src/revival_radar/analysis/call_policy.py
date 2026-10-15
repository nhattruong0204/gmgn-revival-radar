"""Pure published-call policy decisions shared by the scanner repository and replay."""

from math import isfinite

from revival_radar.analysis.scoring import STAGE_ORDER, stage_rank
from revival_radar.models.signal import RevivalResult


def new_evidence_block(
    previous_price: float | None,
    previous_stage: str | None,
    previous_structure: dict | None,
    price: float | None,
    result: RevivalResult,
) -> str | None:
    """Return why a repeat call lacks new evidence, or None when it may publish.

    Requires a higher stage or a newly true higher-high/breakout/retest AND a strictly
    higher price than the previous call. Unknown values block.
    """
    if (
        previous_price is None
        or price is None
        or not isfinite(previous_price)
        or not isfinite(price)
    ):
        return "price_unknown"
    if price <= previous_price:
        return "price_not_higher"
    old_rank = stage_rank(previous_stage) if previous_stage in STAGE_ORDER else None
    if old_rank is not None and stage_rank(result.status) > old_rank:
        return None
    if isinstance(previous_structure, dict):
        for event, now in (
            ("higher_high_detected", result.structure.higher_high_detected),
            ("breakout_detected", result.structure.breakout_detected),
            ("retest_detected", result.structure.retest_detected),
        ):
            if now and previous_structure.get(event) is False:
                return None
    return "no_new_evidence"
