"""Plain-text labels, exact value formatting, and explicit-unit input for Telegram."""

import math
import re
from decimal import Decimal, DecimalException
from typing import Any

from revival_radar.chains import CHAINS

SHORT_LABELS = {
    "token_min_age_hours": "Min. token age",
    "min_market_cap": "Min. market cap",
    "max_market_cap": "Max. market cap",
    "min_liquidity": "Min. liquidity",
    "min_holders": "Min. holders",
    "min_ath_drawdown": "Min. ATH drawdown",
    "max_ath_drawdown": "Max. ATH drawdown",
    "min_volume_1h": "Min. 1h volume",
    "max_price_change_5m": "Max. 5m price rise",
    "max_price_change_1h": "Max. 1h price rise",
    "base_min_hours": "Min. base duration",
    "base_sufficient_hours": "Base score bonus",
    "volume_acceleration_threshold": "Volume acceleration",
    "tx_acceleration_threshold": "TX acceleration",
    "base_max_range_ratio": "Max. base range",
    "holder_retention_ratio": "Holder retention",
    "top10_max_ratio": "Max. top-10 share",
    "alert_score_threshold": "Min. alert score",
    "alert_cooldown_hours": "Alert cooldown",
    "realert_score_increase": "Re-alert score gain",
    "daily_summary_hour": "Daily summary time",
    "discovery_limit": "Results per source",
    "watchlist_limit": "Watchlist per chain",
    "watchlist_hours": "Watchlist lifetime",
    "scan_interval_seconds": "Scan interval",
    "request_spacing_seconds": "API request spacing",
    "history_observations": "History samples",
    "minimum_history_observations": "Min. history samples",
    "history_max_gap_seconds": "Max. history gap",
    "alerts_paused": "Alerts paused",
    "dry_run": "Dry run",
    "daily_summary_enabled": "Daily summary",
    "enabled_chains": "Chains",
    "discovery_interval": "Ranking period",
    "exclude_tokenized_stocks": "Exclude stocks",
    "exclude_stablecoins": "Exclude stablecoins",
    "exclude_wrapped_assets": "Exclude wrapped assets",
}

MONEY_FIELDS = {"min_market_cap", "max_market_cap", "min_liquidity", "min_volume_1h"}
COUNT_FIELDS = {
    "min_holders",
    "discovery_limit",
    "watchlist_limit",
    "history_observations",
    "minimum_history_observations",
}
RATIO_FIELDS = {
    "min_ath_drawdown",
    "max_ath_drawdown",
    "base_max_range_ratio",
    "holder_retention_ratio",
    "top10_max_ratio",
}
PERCENT_FIELDS = {"max_price_change_5m", "max_price_change_1h"}
ACCELERATION_FIELDS = {"volume_acceleration_threshold", "tx_acceleration_threshold"}
HOUR_FIELDS = {
    "token_min_age_hours",
    "base_min_hours",
    "base_sufficient_hours",
    "alert_cooldown_hours",
    "watchlist_hours",
}
SECOND_FIELDS = {
    "scan_interval_seconds",
    "request_spacing_seconds",
    "history_max_gap_seconds",
}

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
_GROUPED_NUMBER = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", re.ASCII)


def _decimal(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Use a finite number.")
    return number


def _number(value: Any) -> str:
    """Keep all configured digits; abbreviating thresholds would hide small edits."""
    text = format(_decimal(value), ",f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def setting_value(key: str, value: Any) -> str:
    """Return a readable plain-text value; the caller escapes Telegram HTML."""
    if isinstance(value, bool):
        return "On" if value else "Off"
    if key == "enabled_chains":
        ids = value.split(",") if isinstance(value, str) else value
        return ", ".join(
            CHAINS[chain].display_name if chain in CHAINS else chain
            for chain in (str(item).strip().lower() for item in ids)
        )
    if key == "discovery_interval":
        return {"1m": "1 min", "5m": "5 min", "1h": "1 h", "6h": "6 h", "24h": "24 h"}.get(
            str(value), str(value)
        )
    if isinstance(value, (int, float, Decimal)):
        text = _number(value)
        if key in MONEY_FIELDS:
            return f"${text}"
        if key in RATIO_FIELDS:
            return f"{_number(_decimal(value) * 100)}%"
        if key in PERCENT_FIELDS:
            return f"{text}%"
        if key in HOUR_FIELDS:
            return f"{text} h"
        if key in SECOND_FIELDS:
            return f"{text} s"
        if key in ACCELERATION_FIELDS:
            return f"{text}×"
        if key == "alert_score_threshold":
            return f"{text}/100"
        if key == "realert_score_increase":
            return f"+{text} points"
        if key == "daily_summary_hour":
            return f"{int(value):02d}:00"
        return text
    return str(value)


def setting_hint(key: str) -> str:
    """Describe the field's accepted input without requiring technical notation."""
    if key in MONEY_FIELDS:
        return "Enter a USD amount, e.g. 15000, $15,000 or 15k."
    if key in RATIO_FIELDS:
        return "Enter a percentage, e.g. 60%, or a decimal ratio, e.g. 0.60."
    if key in PERCENT_FIELDS:
        return "Enter a percentage, e.g. 40 or 40%."
    if key in ACCELERATION_FIELDS:
        return "Enter a multiplier, e.g. 1.4 or 1.4×."
    if key in HOUR_FIELDS:
        return "Enter hours, e.g. 24 or 24h. Decimals are allowed."
    if key in SECOND_FIELDS:
        return "Enter seconds, e.g. 150 or 150s. Decimals are allowed."
    if key == "daily_summary_hour":
        return "Enter a whole hour from 0 to 23, e.g. 9 for 09:00 in your report timezone."
    if key == "alert_score_threshold":
        return "Enter a whole score from 0 to 100, e.g. 65."
    if key == "realert_score_increase":
        return "Enter a whole score increase from 1 to 100, e.g. 10."
    if key in COUNT_FIELDS:
        return "Enter a whole number, e.g. 100 or 1,000."
    return "Enter a number, e.g. 10."


def parse_setting_number(key: str, text: str) -> float:
    """Parse an explicit value; full Settings validation handles field ranges.

    A bare 60 for a decimal-ratio field remains 60 and will fail range validation.
    Only an explicit percent sign divides ratio-field inputs by 100.
    """
    if not isinstance(text, str) or len(text) > 64:
        raise ValueError("Use a number of at most 64 characters.")
    raw = text.strip()
    multiplier = Decimal(1)
    abbreviated = False
    if raw.startswith("$"):
        if key not in MONEY_FIELDS:
            raise ValueError("Dollar amounts are only accepted for USD settings.")
        raw = raw[1:].strip()
    if raw.endswith("%"):
        if key not in RATIO_FIELDS | PERCENT_FIELDS:
            raise ValueError("Percentages are not accepted for this setting.")
        raw = raw[:-1].strip()
        if key in RATIO_FIELDS:
            multiplier = Decimal("0.01")
    elif key in MONEY_FIELDS | COUNT_FIELDS and raw[-1:].lower() in {"k", "m"}:
        multiplier = Decimal(1000 if raw[-1:].lower() == "k" else 1_000_000)
        raw = raw[:-1].strip()
        abbreviated = True
    elif key in ACCELERATION_FIELDS:
        raw = re.sub(r"[xX×]$", "", raw).strip()
    elif key in HOUR_FIELDS:
        raw = re.sub(r"(?:hours?|h)$", "", raw, flags=re.IGNORECASE).strip()
    elif key in SECOND_FIELDS:
        raw = re.sub(r"(?:seconds?|secs?|s)$", "", raw, flags=re.IGNORECASE).strip()
    if "," in raw:
        if (
            key not in MONEY_FIELDS | COUNT_FIELDS
            or abbreviated
            or not _GROUPED_NUMBER.fullmatch(raw)
        ):
            raise ValueError("Use thousands separators like 15,000, or no commas.")
        raw = raw.replace(",", "")
    if not _NUMBER.fullmatch(raw):
        raise ValueError("Use a number with units supported by this setting.")
    try:
        number = float(_decimal(raw) * multiplier)
    except (DecimalException, ValueError, OverflowError):
        raise ValueError("Use a finite number.") from None
    if not math.isfinite(number):
        raise ValueError("Use a finite number.")
    return number
