from statistics import mean

from revival_radar.config import Settings
from revival_radar.models.signal import Structure
from revival_radar.models.token import Candle


def swing_points(candles: list[Candle]) -> tuple[list[float], list[float]]:
    lows, highs = [], []
    for left, center, right in zip(candles, candles[1:], candles[2:], strict=False):
        if center.low < left.low and center.low < right.low:
            lows.append(center.low)
        if center.high > left.high and center.high > right.high:
            highs.append(center.high)
    return lows, highs


def higher_low(candles: list[Candle]) -> bool:
    lows, _ = swing_points(candles)
    return len(lows) >= 2 and lows[-1] > lows[-2]


def higher_high(candles: list[Candle]) -> bool:
    _, highs = swing_points(candles)
    return len(highs) >= 2 and highs[-1] > highs[-2]


def analyze_structure(candles: list[Candle], config: Settings) -> Structure:
    """Analyze a contiguous base before the final two closed hourly candles."""
    ordered = sorted({c.timestamp: c for c in candles}.values(), key=lambda c: c.timestamp)
    if len(ordered) < 6:
        return Structure()
    # Do not mistake sparse observations for several days of consolidation.
    for i in range(len(ordered) - 1, 0, -1):
        if ordered[i].timestamp - ordered[i - 1].timestamp > 5400:
            ordered = ordered[i:]
            break
    if len(ordered) < 6:
        return Structure()
    prior = ordered[:-2]
    base: list[Candle] = []
    for candle in reversed(prior):
        trial = [candle, *base]
        low, high = min(c.low for c in trial), max(c.high for c in trial)
        if (high - low) / low > config.base_max_range_ratio:
            break
        base = trial
    result = Structure(available=True)
    if len(base) < 2:
        return result
    duration = (base[-1].timestamp - base[0].timestamp + 3600) / 3600
    floor, ceiling = min(c.low for c in base), max(c.high for c in base)
    latest_low = min(c.low for c in ordered[-2:])
    stable = latest_low >= floor * (1 - config.major_low_tolerance)
    result.base_duration_hours = duration
    result.base_detected = duration >= config.base_min_hours and stable
    result.higher_low_detected = higher_low(base)
    result.higher_high_detected = higher_high(base)
    result.breakout_detected = result.base_detected and (
        ordered[-1].close > ceiling * (1 + config.breakout_margin)
    )
    result.retest_detected = result.base_detected and (
        ordered[-2].close > ceiling * (1 + config.breakout_margin)
        and abs(ordered[-1].low / ceiling - 1) <= config.retest_tolerance
        and ordered[-1].close >= ceiling
    )
    split = len(base) // 2
    earlier = mean((c.high - c.low) / c.close for c in base[:split])
    later = mean((c.high - c.low) / c.close for c in base[split:])
    result.volatility_compression_score = max(0, min(1, 1 - later / earlier)) if earlier else 0
    return result
