from revival_radar.analysis.acceleration import calculate_acceleration, fresh_history
from revival_radar.analysis.filters import first_pass
from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import TokenSnapshot

SETUP_COMPONENTS = ("drawdown", "liquidity", "holders", "base", "base_duration", "compression")
TRIGGER_COMPONENTS = (
    "volume_5m",
    "volume_1h",
    "transactions",
    "hot_rank",
    "trending",
    "both_sources",
)
CONFIRMATION_COMPONENTS = ("higher_low", "higher_high", "breakout", "retest")
SCORE_VERSION = "setup-trigger-confirmation-v1"


def status_for(
    score: int,
    *,
    config: Settings | None = None,
    qualifies: bool = False,
    activity: bool = False,
    structure: Structure | None = None,
    meaningful: bool = False,
    dual_trigger: bool = False,
    off_rank: bool = False,
) -> str:
    """Require score AND evidence; total alone never establishes a revival stage."""
    config = config if config is not None else Settings(_env_file=None)
    if not qualifies or score < config.watch_score_threshold:
        return "IGNORE"
    if (
        not activity
        or structure is None
        or not (
            structure.available
            and structure.base_detected
            and structure.base_duration_hours >= config.base_min_hours
        )
    ):
        return "WATCH"
    hl, hh = structure.higher_low_detected, structure.higher_high_detected
    breakout = structure.breakout_detected or structure.retest_detected
    confirmed = hl and hh and breakout
    strong = (hl and hh) or (breakout and (hl or hh))
    off_rank_guard = not off_rank or (dual_trigger and confirmed)
    if (
        score >= config.confirmed_revival_score_threshold
        and confirmed
        and meaningful
        and structure.base_duration_hours >= config.confirmed_base_min_hours
        and off_rank_guard
    ):
        return "CONFIRMED_REVIVAL"
    if (
        score >= config.strong_revival_score_threshold
        and strong
        and meaningful
        and structure.base_duration_hours >= config.strong_base_min_hours
        and off_rank_guard
    ):
        return "STRONG_REVIVAL"
    if score >= config.reviving_score_threshold and (hl or hh or breakout):
        return "REVIVING"
    if score >= config.early_revival_score_threshold:
        return "EARLY_REVIVAL"
    return "WATCH"


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
    has_base = (
        structure.available
        and structure.base_detected
        and structure.base_duration_hours >= config.base_min_hours
    )
    if has_base:
        fraction = next(
            (
                portion
                for hour, portion in reversed(
                    list(
                        zip(
                            config.base_maturity_hours[:4],
                            config.base_maturity_fractions,
                            strict=True,
                        )
                    )
                )
                if structure.base_duration_hours >= hour
            ),
            0,
        )
        components["base"] = round(weights.base * fraction)
    award(
        "base_duration",
        has_base
        and structure.base_duration_hours
        >= max(config.base_sufficient_hours, config.base_maturity_hours[4]),
    )
    award(
        "compression",
        has_base
        and structure.volatility_compression_score is not None
        and structure.volatility_compression_score >= config.volatility_compression_threshold,
    )
    volume_quality_5m = (
        token.volume_5m is not None
        and token.volume_5m >= config.min_volume_5m_for_acceleration
        and acceleration.volume_5m_to_liquidity is not None
        and acceleration.volume_5m_to_liquidity >= config.min_volume_5m_liquidity_ratio
    )
    volume_quality_1h = (
        token.volume_1h is not None
        and token.volume_1h >= config.min_volume_1h
        and acceleration.volume_1h_to_liquidity is not None
        and acceleration.volume_1h_to_liquidity >= config.min_volume_1h_liquidity_ratio
    )
    tx_quality_5m = token.tx_5m is not None and token.tx_5m >= config.min_tx_5m_for_acceleration
    tx_quality_1h = token.tx_1h is not None and token.tx_1h >= config.min_tx_1h_for_acceleration
    volume_5m = (
        volume_quality_5m
        and acceleration.volume_ratio_5m is not None
        and acceleration.volume_ratio_5m >= config.volume_acceleration_threshold
    )
    volume_1h = (
        volume_quality_1h
        and acceleration.volume_acceleration_1h is not None
        and acceleration.volume_acceleration_1h >= config.volume_acceleration_threshold
    )
    tx_5m = (
        tx_quality_5m
        and acceleration.tx_acceleration_5m is not None
        and acceleration.tx_acceleration_5m >= config.tx_acceleration_threshold
    )
    tx_1h = (
        tx_quality_1h
        and acceleration.tx_acceleration_1h is not None
        and acceleration.tx_acceleration_1h >= config.tx_acceleration_threshold
    )
    volume_trigger, tx_trigger = volume_5m or volume_1h, tx_5m or tx_1h
    activity = volume_trigger or tx_trigger
    meaningful = (volume_quality_5m and tx_quality_5m) or (volume_quality_1h and tx_quality_1h)
    award("volume_5m", volume_5m)
    award("volume_1h", volume_1h)
    award("transactions", tx_trigger)
    award(
        "hot_rank",
        "hot_search" in token.discovery_source
        and acceleration.hot_rank_improvement is not None
        and acceleration.hot_rank_improvement >= config.hot_rank_improvement,
    )
    award("trending", "trending" in token.discovery_source and token.trending_rank is not None)
    award("retest", has_base and structure.retest_detected)
    award("both_sources", {"hot_search", "trending"} <= token.discovery_source)
    award("higher_low", has_base and structure.higher_low_detected)
    award("higher_high", has_base and structure.higher_high_detected)
    award("breakout", has_base and structure.breakout_detected)
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

    def dimension(names: tuple[str, ...]) -> int:
        possible = sum(getattr(weights, name) for name in names)
        actual = sum(max(0, components.get(name, 0)) for name in names)
        return min(100, round(100 * actual / possible)) if possible else 0

    setup, trigger, confirmation = (
        dimension(names)
        for names in (SETUP_COMPONENTS, TRIGGER_COMPONENTS, CONFIRMATION_COMPONENTS)
    )
    allocation = (
        weights.setup_dimension,
        weights.trigger_dimension,
        weights.confirmation_dimension,
    )
    positive = (
        round(
            sum(
                score * weight
                for score, weight in zip((setup, trigger, confirmation), allocation, strict=True)
            )
            / sum(allocation)
        )
        if sum(allocation)
        else 0
    )
    total = max(0, min(100, positive) + sum(v for v in components.values() if v < 0))
    if not gate.passed:
        total = min(total, 39)
    if not activity:
        warnings.append("No meaningful activity acceleration: check absolute and liquidity floors")
    eligible = gate.passed and has_base and activity and sec.dangerous is not True
    context = (
        "ACTIVE_DISCOVERY"
        if token.discovery_source
        else "RANKING_UNAVAILABLE"
        if "Current ranking coverage incomplete" in token.data_warnings
        else "WATCHLIST_REVIVAL"
    )
    if gate.passed and not eligible:
        warnings.append("Alert needs a base, returning activity, and no known dangerous flag")
    return RevivalResult(
        score=total,
        status=status_for(
            total,
            config=config,
            qualifies=gate.passed and sec.dangerous is not True,
            activity=activity,
            structure=structure,
            meaningful=meaningful,
            dual_trigger=volume_trigger and tx_trigger,
            off_rank=not token.discovery_source,
        ),
        setup_score=setup,
        trigger_score=trigger,
        confirmation_score=confirmation,
        score_version=SCORE_VERSION,
        discovery_context=context,
        returning_activity=activity,
        eligible=eligible,
        reasons=[f"{name}: {points:+d}" for name, points in components.items()],
        warnings=warnings,
        components=components,
        acceleration=acceleration,
        structure=structure,
    )
