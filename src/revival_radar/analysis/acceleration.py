import math
from statistics import mean

from revival_radar.config import Settings
from revival_radar.models.signal import Acceleration
from revival_radar.models.token import TokenSnapshot


def safe_ratio(current: float | None, baseline: float | None, cap: float = 20) -> float | None:
    if current is None or baseline is None:
        return None
    if not math.isfinite(current) or not math.isfinite(baseline) or current < 0 or baseline <= 0:
        return None
    return min(current / baseline, cap)


def fresh_history(
    token: TokenSnapshot, history: list[TokenSnapshot], config: Settings
) -> list[TokenSnapshot]:
    """Keep contiguous, distinct observations at approximately the polling cadence."""
    result = []
    newer = token.timestamp
    for previous in sorted(history, key=lambda x: x.timestamp, reverse=True):
        if previous.key != token.key or previous.timestamp >= newer:
            continue
        gap = newer - previous.timestamp
        if gap < config.scan_interval_seconds * 0.5:
            continue
        if gap > config.history_max_gap_seconds:
            break
        result.append(previous)
        newer = previous.timestamp
        if len(result) == config.history_observations:
            break
    return result


def calculate_acceleration(
    token: TokenSnapshot, history: list[TokenSnapshot], config: Settings
) -> Acceleration:
    recent = fresh_history(token, history, config)
    result = Acceleration()
    if recent:
        for field in ("volume_5m", "volume_1h", "tx_5m", "tx_1h"):
            name = field.replace("_", "_acceleration_", 1)
            setattr(
                result,
                name,
                safe_ratio(
                    getattr(token, field), getattr(recent[0], field), config.acceleration_cap
                ),
            )
        result.previous_hot_rank = recent[0].hot_search_rank
        if token.hot_search_rank is not None and result.previous_hot_rank is not None:
            result.hot_rank_improvement = result.previous_hot_rank - token.hot_search_rank
    values = [s.volume_5m for s in recent if s.volume_5m is not None]
    if len(values) >= config.minimum_history_observations:
        result.volume_ratio_5m = safe_ratio(token.volume_5m, mean(values), config.acceleration_cap)
    result.first_hot_appearance = token.hot_search_rank is not None and not any(
        s.hot_search_rank is not None for s in history
    )
    return result
