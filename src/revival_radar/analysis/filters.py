from dataclasses import dataclass

from revival_radar.config import Settings
from revival_radar.models.token import TokenSnapshot


@dataclass
class FilterResult:
    passed: bool
    reasons: list[str]


def first_pass(token: TokenSnapshot, config: Settings) -> FilterResult:
    """Unknown essential metrics prevent eligibility; never impute them as zero."""
    checks = {
        "token_age_seconds": (config.token_min_age_hours * 3600, None),
        "market_cap": (config.min_market_cap, config.max_market_cap),
        "liquidity": (config.min_liquidity, None),
        "drawdown_from_ath": (config.min_ath_drawdown, config.max_ath_drawdown),
        "holders": (config.min_holders, None),
        "volume_1h": (config.min_volume_1h, None),
        "price_change_5m": (None, config.max_price_change_5m),
        "price_change_1h": (None, config.max_price_change_1h),
    }
    reasons = []
    for field, (minimum, maximum) in checks.items():
        value = getattr(token, field)
        if value is None:
            reasons.append(f"{field}: unavailable")
        elif minimum is not None and value < minimum:
            reasons.append(f"{field}: below minimum")
        elif maximum is not None and value > maximum:
            reasons.append(f"{field}: above maximum")
    return FilterResult(not reasons, reasons)
