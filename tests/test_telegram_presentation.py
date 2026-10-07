import pytest
from pydantic import ValidationError

from revival_radar.config import Settings
from revival_radar.telegram_presentation import (
    SHORT_LABELS,
    parse_setting_number,
    setting_hint,
    setting_value,
)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("min_liquidity", 15000, "$15,000"),
        ("min_market_cap", 1000000.25, "$1,000,000.25"),
        ("min_market_cap", 15000.001, "$15,000.001"),
        ("min_ath_drawdown", 0.6, "60%"),
        ("base_max_range_ratio", 0.2555, "25.55%"),
        ("max_price_change_5m", 40, "40%"),
        ("base_min_hours", 24, "24 h"),
        ("scan_interval_seconds", 150, "150 s"),
        ("request_spacing_seconds", 1.5, "1.5 s"),
        ("volume_acceleration_threshold", 1.4, "1.4×"),
        ("alert_score_threshold", 65, "65/100"),
        ("realert_score_increase", 10, "+10 points"),
        ("daily_summary_hour", 9, "09:00"),
        ("daily_summary_enabled", True, "On"),
        ("dry_run", False, "Off"),
        ("min_holders", 1500, "1,500"),
        ("discovery_interval", "5m", "5 min"),
        ("enabled_chains", "sol,robinhood", "Solana, Robinhood Chain"),
        ("enabled_chains", ["bsc", "base"], "BNB Smart Chain, Base"),
    ],
)
def test_exact_readable_values(key, value, expected):
    assert setting_value(key, value) == expected


@pytest.mark.parametrize(
    ("key", "text", "expected"),
    [
        ("min_liquidity", "$15k", 15000),
        ("min_liquidity", "15K", 15000),
        ("min_liquidity", "15,000", 15000),
        ("max_market_cap", "$1,000,000.25", 1000000.25),
        ("max_market_cap", "15m", 15000000),
        ("min_holders", "1.5k", 1500),
        ("min_holders", " 1,000 ", 1000),
        ("min_ath_drawdown", "60%", 0.6),
        ("min_ath_drawdown", "0.60", 0.6),
        ("min_ath_drawdown", "60", 60),
        ("base_max_range_ratio", "25.55%", 0.2555),
        ("holder_retention_ratio", "90 %", 0.9),
        ("max_price_change_5m", "40%", 40),
        ("max_price_change_1h", "40", 40),
        ("volume_acceleration_threshold", "1.4x", 1.4),
        ("tx_acceleration_threshold", "1.4×", 1.4),
        ("scan_interval_seconds", "150s", 150),
        ("request_spacing_seconds", "1.5 seconds", 1.5),
        ("watchlist_hours", "24h", 24),
        ("base_min_hours", "12 hours", 12),
        ("min_liquidity", "1.5e4", 15000),
    ],
)
def test_supported_inputs(key, text, expected):
    assert parse_setting_number(key, text) == expected


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("min_liquidity", "15,00"),
        ("min_liquidity", "1,5"),
        ("min_liquidity", "1,,000"),
        ("min_liquidity", "1500,000"),
        ("min_liquidity", "15,000k"),
        ("min_liquidity", "15k%"),
        ("min_liquidity", "15%"),
        ("min_liquidity", "15kk"),
        ("min_liquidity", "1 500"),
        ("min_holders", "$1500"),
        ("scan_interval_seconds", "1.5k"),
        ("scan_interval_seconds", "150h"),
        ("scan_interval_seconds", "1,500"),
        ("volume_acceleration_threshold", "140%"),
        ("min_ath_drawdown", "60x"),
        ("min_ath_drawdown", "60%%"),
        ("min_ath_drawdown", "1k"),
        ("daily_summary_hour", "09:00"),
        ("alert_score_threshold", "65/100"),
        ("min_liquidity", "NaN"),
        ("min_liquidity", "inf"),
        ("min_liquidity", "-Infinity"),
        ("min_liquidity", "1e999999999"),
        ("min_liquidity", "9" * 65),
        ("min_liquidity", ""),
        ("min_liquidity", "<b>15000</b>"),
        ("min_liquidity", "١٥٠٠٠"),
    ],
)
def test_reject_ambiguous_or_incompatible_units(key, text):
    with pytest.raises(ValueError):
        parse_setting_number(key, text)


def test_hints_explain_ratio_and_price_percentage_difference():
    assert "60%" in setting_hint("min_ath_drawdown")
    assert "0.60" in setting_hint("min_ath_drawdown")
    assert "40 or 40%" in setting_hint("max_price_change_5m")


def test_bare_percentage_does_not_silently_change_ratio_units(config):
    raw = parse_setting_number("min_ath_drawdown", "60")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(config.model_dump() | {"min_ath_drawdown": raw}))
    explicit = parse_setting_number("min_ath_drawdown", "60%")
    settings = Settings(_env_file=None, **(config.model_dump() | {"min_ath_drawdown": explicit}))
    assert settings.min_ath_drawdown == 0.6


def test_labels_are_short_and_cover_control_groups():
    # Import locally to keep the presentation module independent of controls.
    from revival_radar.telegram_controls import GROUPS, TOGGLES

    numeric_fields = {key for group in GROUPS.values() for key, _ in group}
    assert numeric_fields | TOGGLES <= SHORT_LABELS.keys()
    assert all(len(label) <= 23 for label in SHORT_LABELS.values())
