import pytest
from pydantic import ValidationError

from revival_radar.analysis.filters import first_pass
from revival_radar.config import Settings

from .conftest import changed


def test_ath_drawdown(token):
    assert token.drawdown_from_ath == pytest.approx(1 - 820000 / 6200000)
    assert changed(token, ath_market_cap=None).drawdown_from_ath is None
    assert changed(token, ath_market_cap=0).drawdown_from_ath is None
    assert changed(token, market_cap=None).drawdown_from_ath is None


def test_filter_pass_and_boundaries(token, config):
    assert first_pass(token, config).passed
    edge = changed(
        token,
        market_cap=350000,
        ath_market_cap=1000000,
        liquidity=30000,
        holders=300,
        volume_1h=50000,
        token_age_seconds=48 * 3600,
        price_change_5m=30,
        price_change_1h=75,
    )
    assert first_pass(edge, config).passed
    assert first_pass(
        changed(edge, market_cap=50000, ath_market_cap=1000000),
        config.model_copy(update={"min_market_cap": 50000}),
    ).passed


@pytest.mark.parametrize(
    "field,value",
    [
        ("holders", None),
        ("liquidity", None),
        ("ath_market_cap", None),
        ("token_age_seconds", None),
        ("volume_1h", None),
        ("price_change_5m", None),
        ("price_change_1h", None),
        ("market_cap", None),
        ("liquidity", 100),
        ("price_change_1h", 250),
        ("price_change_5m", 31),
        ("holders", 20),
        ("token_age_seconds", 100),
        ("market_cap", 50_000_000),
    ],
)
def test_rejects_missing_or_outside_filters(token, config, field, value):
    result = first_pass(changed(token, **{field: value}), config)
    assert not result.passed
    assert result.reasons


def test_config_validation_and_overrides(monkeypatch):
    monkeypatch.setenv("WEIGHTS__BASE", "20")
    monkeypatch.setenv("ENABLED_CHAINS", "sol,bsc,sol")
    assert Settings(_env_file=None).weights.base == 20
    assert Settings(_env_file=None).chains == ["sol", "bsc"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, min_market_cap=2e7)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, enabled_chains="unsupported")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, scan_interval_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, weights={"typo": 1})
