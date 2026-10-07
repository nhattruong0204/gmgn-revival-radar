from revival_radar.analysis.acceleration import calculate_acceleration, fresh_history
from revival_radar.analysis.filters import first_pass
from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import TokenSnapshot


def status_for(score: int) -> str:
    if score >= 90:
        return "HIGH_CONVICTION_REVIVAL"
    if score >= 80:
        return "STRONG_REVIVAL"
    if score >= 70:
        return "REVIVING"
    if score >= 60:
        return "EARLY_WATCH"
    if score >= 40:
        return "WATCH"
    return "IGNORE"


def score_token(
    token: TokenSnapshot, history: list[TokenSnapshot], structure: Structure, config: Settings
) -> RevivalResult:
    acceleration = calculate_acceleration(token, history, config)
    recent = fresh_history(token, history, config)
    previous = recent[0] if recent else None
    gate = first_pass(token, config)
    components: dict[str, int] = {}
    warnings = list(token.data_warnings) + gate.reasons
    weights = config.weights

    def award(name: str, condition: bool) -> None:
        if condition:
            components[name] = getattr(weights, name)

    def penalize(name: str, condition: bool) -> None:
        if condition:
            components[name] = -getattr(weights, name)
            warnings.append(name.replace("_", " "))

    award(
        "drawdown",
        token.drawdown_from_ath is not None
        and config.min_ath_drawdown <= token.drawdown_from_ath <= config.max_ath_drawdown,
    )
    award("liquidity", token.liquidity is not None and token.liquidity >= config.min_liquidity)
    award(
        "holders",
        bool(
            previous
            and token.holders is not None
            and previous.holders
            and token.holders >= config.min_holders
            and token.holders / previous.holders >= config.holder_retention_ratio
        ),
    )
    award("base", structure.base_detected)
    award(
        "base_duration",
        structure.base_detected and structure.base_duration_hours >= config.base_sufficient_hours,
    )
    award(
        "volume_5m",
        acceleration.volume_ratio_5m is not None
        and acceleration.volume_ratio_5m >= config.volume_acceleration_threshold,
    )
    award(
        "volume_1h",
        acceleration.volume_acceleration_1h is not None
        and acceleration.volume_acceleration_1h >= config.volume_acceleration_threshold,
    )
    tx_ratios = [
        r
        for r in (acceleration.tx_acceleration_5m, acceleration.tx_acceleration_1h)
        if r is not None
    ]
    award("transactions", bool(tx_ratios) and max(tx_ratios) >= config.tx_acceleration_threshold)
    award(
        "hot_rank",
        acceleration.hot_rank_improvement is not None
        and acceleration.hot_rank_improvement >= config.hot_rank_improvement,
    )
    award("both_sources", {"hot_search", "trending"} <= token.discovery_source)
    award("higher_low", structure.base_detected and structure.higher_low_detected)
    award("higher_high", structure.base_detected and structure.higher_high_detected)
    award("breakout", structure.breakout_detected)
    sec = token.security
    penalize(
        "concentration_penalty",
        sec.top10_ratio is not None and sec.top10_ratio > config.top10_max_ratio,
    )
    penalize("danger_penalty", sec.dangerous is True)
    penalize(
        "sniper_penalty",
        sec.sniper_ratio is not None and sec.sniper_ratio > config.sniper_max_ratio,
    )
    penalize(
        "bundler_penalty",
        sec.bundler_ratio is not None and sec.bundler_ratio > config.bundler_max_ratio,
    )
    if previous:
        penalize(
            "liquidity_penalty",
            previous.liquidity is not None
            and previous.liquidity > 0
            and token.liquidity is not None
            and 1 - token.liquidity / previous.liquidity >= config.liquidity_drop_threshold,
        )
        for name, threshold in (
            ("dev", config.dev_drop_threshold),
            ("insider", config.insider_drop_threshold),
        ):
            before, now = getattr(previous.security, f"{name}_ratio"), getattr(sec, f"{name}_ratio")
            penalize(
                f"{name}_penalty",
                before is not None and now is not None and before - now >= threshold,
            )
    if sec.top10_ratio is None:
        warnings.append("Top 10 concentration unavailable")
    if sec.insider_ratio is None:
        warnings.append("Insider holdings unavailable")
    if sec.dangerous is None:
        warnings.append("Security assessment unavailable; absence is not safety")
    if not structure.available:
        warnings.append("Insufficient contiguous closed candles")
    if acceleration.volume_ratio_5m is None:
        warnings.append("5m volume baseline unavailable or zero; warming up")
    if acceleration.first_hot_appearance:
        warnings.append("First observed Hot Search appearance (within retained history)")
    total = min(100, sum(v for v in components.values() if v > 0))
    total = max(0, total + sum(v for v in components.values() if v < 0))
    if not gate.passed:
        total = min(total, 39)
    activity = any(name in components for name in ("volume_5m", "volume_1h", "transactions"))
    eligible = gate.passed and structure.base_detected and activity and sec.dangerous is not True
    if gate.passed and not eligible:
        warnings.append("Alert needs a base, returning activity, and no known dangerous flag")
    return RevivalResult(
        score=total,
        status=status_for(total),
        eligible=eligible,
        reasons=[f"{name}: {points:+d}" for name, points in components.items()],
        warnings=warnings,
        components=components,
        acceleration=acceleration,
        structure=structure,
    )
