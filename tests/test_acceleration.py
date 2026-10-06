import pytest

from revival_radar.analysis.acceleration import calculate_acceleration, safe_ratio

from .conftest import changed


@pytest.mark.parametrize(
    "current,baseline,expected",
    [
        (61000, 29000, 61000 / 29000),
        (0, 100, 0),
        (100, 0, None),
        (None, 1, None),
        (1, None, None),
        (-1, 1, None),
        (1, -1, None),
        (1, float("nan"), None),
        (float("inf"), 1, None),
        (100000, 1, 20),
    ],
)
def test_safe_ratio(current, baseline, expected):
    assert safe_ratio(current, baseline) == expected


def test_acceleration_and_ranks(token, history, config):
    result = calculate_acceleration(token, history, config)
    assert result.volume_ratio_5m == pytest.approx(61000 / 13250)
    assert result.volume_acceleration_1h == pytest.approx(184000 / 90000)
    assert result.tx_acceleration_5m == pytest.approx(318 / 166)
    assert result.hot_rank_improvement == 19
    assert not result.first_hot_appearance


def test_missing_stale_and_duplicate_observations(token, history, config):
    stale = [changed(s, timestamp=s.timestamp - 10000) for s in history]
    assert calculate_acceleration(token, stale, config).volume_ratio_5m is None
    assert calculate_acceleration(token, [history[-1]] * 5, config).volume_ratio_5m is None
    assert calculate_acceleration(token, history[:1], config).volume_ratio_5m is None
    absent = [changed(s, volume_5m=None) for s in history]
    assert calculate_acceleration(token, absent, config).volume_ratio_5m is None
    zero = [changed(s, volume_5m=0) for s in history]
    assert calculate_acceleration(token, zero, config).volume_ratio_5m is None


def test_rank_direction_and_first_appearance(token, history, config):
    result = calculate_acceleration(changed(token, hot_search_rank=90), history, config)
    assert result.hot_rank_improvement == -63
    assert calculate_acceleration(token, [], config).first_hot_appearance
    assert not calculate_acceleration(
        changed(token, hot_search_rank=None), [], config
    ).first_hot_appearance
