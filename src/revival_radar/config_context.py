"""Nonsecret immutable configuration context attached to historical observations."""

from revival_radar.config import Settings
from revival_radar.runtime_settings import ALLOWED_SETTINGS, PRESETS

PERFORMANCE_SETTINGS = frozenset(
    {
        "security_cache_ttl_seconds",
        "kline_cache_ttl_seconds",
        "max_market_enrich_per_scan",
        "max_security_enrich_per_scan",
        "max_kline_fetch_per_scan",
        "watchlist_high_score_interval_seconds",
        "watchlist_normal_interval_seconds",
        "watchlist_expire_hours",
    }
)


SCORING_SETTINGS = frozenset(
    {
        "min_tx_5m_for_acceleration",
        "min_tx_1h_for_acceleration",
        "min_volume_5m_for_acceleration",
        "min_volume_5m_liquidity_ratio",
        "min_volume_1h_liquidity_ratio",
        "volatility_compression_threshold",
        "base_maturity_hours",
        "base_maturity_fractions",
        "strong_base_min_hours",
        "confirmed_base_min_hours",
        "watch_score_threshold",
        "early_revival_score_threshold",
        "reviving_score_threshold",
        "strong_revival_score_threshold",
        "confirmed_revival_score_threshold",
    }
)


def configuration_context(config: Settings, revision: int = 0, overrides: int = 0) -> dict:
    preset = next(
        (
            name.title()
            for name, values in PRESETS.items()
            if all(getattr(config, key) == value for key, value in values.items())
        ),
        "Custom",
    )
    values = config.model_dump(mode="json")
    return {
        "preset": preset,
        "revision": revision,
        "overrides": overrides,
        "settings": {
            key: values[key]
            for key in sorted(ALLOWED_SETTINGS | PERFORMANCE_SETTINGS | SCORING_SETTINGS)
        },
    }
