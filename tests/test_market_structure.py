from revival_radar.analysis.market_structure import analyze_structure, higher_high, higher_low
from revival_radar.models.token import Candle


def test_base_duration_and_structure(candles, config):
    result = analyze_structure(candles, config)
    assert result.base_detected
    assert result.base_duration_hours == 98
    assert result.higher_low_detected
    assert result.higher_high_detected
    assert result.breakout_detected
    assert result.volatility_compression_score is not None


def test_no_false_duration_from_sparse_candles(candles, config):
    assert not analyze_structure(candles[::6], config).base_detected
    assert not analyze_structure(candles[-10:], config).base_detected
    assert not analyze_structure([], config).available
    assert analyze_structure(candles + candles, config).base_duration_hours == 98


def test_major_new_low_invalidates_base(candles, config):
    last = candles[-1].model_copy(update={"low": 0.0001})
    assert not analyze_structure(candles[:-1] + [last], config).base_detected


def test_swing_direction():
    def bars(prices):
        return [
            Candle(timestamp=i * 3600, open=p, high=p + 0.1, low=p - 0.1, close=p)
            for i, p in enumerate(prices)
        ]

    rising = bars([10, 8, 11, 9, 12, 10])
    falling = bars([12, 10, 11, 9, 10, 8])
    assert higher_low(rising) and higher_high(rising)
    assert not higher_low(falling) and not higher_high(falling)


def test_retest(candles, config):
    ceiling = max(c.high for c in candles[:-2])
    last = Candle(
        timestamp=candles[-1].timestamp,
        open=ceiling * 1.02,
        close=ceiling * 1.01,
        high=ceiling * 1.03,
        low=ceiling * 0.995,
    )
    assert analyze_structure(candles[:-1] + [last], config).retest_detected
