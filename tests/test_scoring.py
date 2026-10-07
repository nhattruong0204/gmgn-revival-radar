import pytest

from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import score_token, status_for
from revival_radar.demo import DemoSource
from revival_radar.models.signal import Structure
from revival_radar.models.token import Candle, TokenSnapshot

from .conftest import changed


@pytest.mark.parametrize(
    "score,status",
    [
        (0, "IGNORE"),
        (39, "IGNORE"),
        (40, "WATCH"),
        (59, "WATCH"),
        (60, "EARLY_WATCH"),
        (69, "EARLY_WATCH"),
        (70, "REVIVING"),
        (79, "REVIVING"),
        (80, "STRONG_REVIVAL"),
        (89, "STRONG_REVIVAL"),
        (90, "HIGH_CONVICTION_REVIVAL"),
        (100, "HIGH_CONVICTION_REVIVAL"),
    ],
)
def test_status_boundaries(score, status):
    assert status_for(score) == status


def test_synthetic_scenarios(config):
    for scenario in DemoSource().data:
        token = TokenSnapshot(**scenario["token"])
        history = [TokenSnapshot(**s) for s in scenario["history"]]
        structure = analyze_structure([Candle(**c) for c in scenario["candles"]], config)
        result = score_token(token, history, structure, config)
        if scenario["label"] == "reviving":
            assert result.score >= 85 and result.eligible
        else:
            assert result.score < 40 and not result.eligible


def test_all_penalties_capped_and_explained(token, history, candles, config):
    structure = analyze_structure(candles, config)
    baseline = score_token(token, history, structure, config)
    sec = token.security.model_dump() | {
        "top10_ratio": 0.8,
        "dangerous": True,
        "dev_ratio": 0,
        "insider_ratio": 0,
        "sniper_ratio": 0.6,
        "bundler_ratio": 0.6,
    }
    bad = changed(token, security=sec, liquidity=40000)
    result = score_token(bad, history, structure, config)
    assert result.score == 0 and result.score < baseline.score
    for name in ("concentration", "danger", "dev", "insider", "sniper", "bundler", "liquidity"):
        assert result.components[f"{name}_penalty"] < 0
    assert not result.eligible


def test_security_penalty_applies_after_positive_cap(token, history, candles, config):
    result = score_token(
        changed(token, security=token.security.model_dump() | {"dangerous": True}),
        history,
        analyze_structure(candles, config),
        config,
    )
    assert result.score == 60
    assert not result.eligible


def test_no_base_or_activity_no_alert(token, history, config, candles):
    assert not score_token(token, history, Structure(), config).eligible
    result = score_token(token, [], analyze_structure(candles, config), config)
    assert not result.eligible
    assert "holders" not in result.components
    assert any("warming up" in w for w in result.warnings)


def test_configurable_weights(token, history, candles, config):
    config.weights.base = 0
    config.weights.base_duration = 0
    result = score_token(token, history, analyze_structure(candles, config), config)
    assert result.score == 85  # 105 possible positive points minus the 20 overridden points
