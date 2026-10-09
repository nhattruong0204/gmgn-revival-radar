from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Weight = Annotated[int, Field(ge=0, le=100)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ScoreWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drawdown: Weight = 10
    liquidity: Weight = 10
    holders: Weight = 5
    base: Weight = 15
    base_duration: Weight = 5
    volume_5m: Weight = 15
    volume_1h: Weight = 10
    transactions: Weight = 10
    hot_rank: Weight = 5
    both_sources: Weight = 5
    higher_low: Weight = 5
    higher_high: Weight = 5
    breakout: Weight = 5
    concentration_penalty: Weight = 10
    insider_penalty: Weight = 20
    dev_penalty: Weight = 25
    liquidity_penalty: Weight = 25
    danger_penalty: Weight = 40
    compression: Weight = 5
    trending: Weight = 5
    retest: Weight = 5
    setup_dimension: Weight = 30
    trigger_dimension: Weight = 40
    confirmation_dimension: Weight = 30
    sniper_penalty: Weight = 5
    bundler_penalty: Weight = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_nested_delimiter="__", extra="ignore", allow_inf_nan=False
    )
    gmgn_api_key: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    telegram_owner_id: int = Field(default=0, ge=0)
    telegram_controls_enabled: bool = True
    alerts_paused: bool = False
    exclude_tokenized_stocks: bool = True
    exclude_stablecoins: bool = True
    exclude_wrapped_assets: bool = True
    daily_summary_enabled: bool = False
    daily_summary_hour: int = Field(default=9, ge=0, le=23)
    report_timezone: str = "Asia/Bangkok"
    enabled_chains: str = "sol"
    database_path: Path = Path("data/revival_radar.db")
    scan_interval_seconds: Positive = 300
    token_min_age_hours: Positive = 48
    min_market_cap: Positive = 100_000
    max_market_cap: Positive = 10_000_000
    min_liquidity: Positive = 30_000
    min_ath_drawdown: Ratio = 0.65
    max_ath_drawdown: Ratio = 0.95
    min_holders: int = Field(default=300, ge=0)
    min_volume_1h: Positive = 50_000
    max_price_change_5m: Positive = 30
    max_price_change_1h: Positive = 75
    alert_score_threshold: int = Field(default=75, ge=0, le=100)
    alert_cooldown_hours: Positive = 6
    realert_score_increase: int = Field(default=10, ge=1, le=100)
    dry_run: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    trending_discovery_enabled: bool = False
    discovery_interval: Literal["1m", "5m", "1h", "6h", "24h"] = "1h"
    discovery_limit: int = Field(default=20, ge=1, le=100)
    watchlist_hours: Positive = 24
    watchlist_limit: int = Field(default=100, ge=1, le=1000)
    history_observations: int = Field(default=6, ge=3, le=24)
    minimum_history_observations: int = Field(default=3, ge=1, le=24)
    history_max_gap_seconds: Positive = 900
    acceleration_cap: Positive = 20
    volume_acceleration_threshold: Positive = 1.5
    tx_acceleration_threshold: Positive = 1.5
    hot_rank_improvement: int = Field(default=10, ge=1)
    holder_retention_ratio: Ratio = 0.9
    base_min_hours: Positive = 24
    base_sufficient_hours: Positive = 72
    base_max_range_ratio: Positive = 0.25
    major_low_tolerance: Ratio = 0.03
    breakout_margin: Ratio = 0.01
    retest_tolerance: Ratio = 0.02
    kline_lookback_hours: int = Field(default=168, ge=24, le=720)
    top10_max_ratio: Ratio = 0.5
    insider_drop_threshold: Ratio = 0.05
    dev_drop_threshold: Ratio = 0.02
    liquidity_drop_threshold: Ratio = 0.2
    sniper_max_ratio: Ratio = 0.3
    bundler_max_ratio: Ratio = 0.3
    http_timeout_seconds: Positive = 20
    http_attempts: int = Field(default=3, ge=1, le=5)
    request_spacing_seconds: Positive = 1.5
    retry_max_wait_seconds: Positive = 10
    min_tx_5m_for_acceleration: int = Field(default=10, ge=1)
    min_tx_1h_for_acceleration: int = Field(default=60, ge=1)
    min_volume_5m_for_acceleration: Positive = 2000
    min_volume_5m_liquidity_ratio: Ratio = 0.01
    min_volume_1h_liquidity_ratio: Ratio = 0.06
    volatility_compression_threshold: Ratio = 0.25
    base_maturity_hours: tuple[Positive, Positive, Positive, Positive, Positive] = (
        6,
        12,
        24,
        48,
        72,
    )
    base_maturity_fractions: tuple[Ratio, Ratio, Ratio, Ratio] = (1 / 3, 8 / 15, 0.8, 1)
    strong_base_min_hours: Positive = 48
    confirmed_base_min_hours: Positive = 72
    watch_score_threshold: int = Field(default=40, ge=0, le=100)
    early_revival_score_threshold: int = Field(default=60, ge=0, le=100)
    reviving_score_threshold: int = Field(default=70, ge=0, le=100)
    strong_revival_score_threshold: int = Field(default=80, ge=0, le=100)
    confirmed_revival_score_threshold: int = Field(default=85, ge=0, le=100)
    security_cache_ttl_seconds: float = Field(default=1800, ge=0, allow_inf_nan=False)
    kline_cache_ttl_seconds: float = Field(default=900, ge=0, allow_inf_nan=False)
    max_market_enrich_per_scan: int = Field(default=40, ge=0, le=500)
    max_security_enrich_per_scan: int = Field(default=8, ge=0, le=100)
    max_kline_fetch_per_scan: int = Field(default=12, ge=0, le=100)
    watchlist_high_score_interval_seconds: Positive = 150
    watchlist_normal_interval_seconds: Positive = 600
    # None preserves the existing WATCHLIST_HOURS / Telegram preset setting.
    watchlist_expire_hours: Positive | None = None
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=100, le=30000)
    weights: ScoreWeights = Field(default_factory=ScoreWeights)

    @property
    def chains(self) -> list[str]:
        return list(dict.fromkeys(c.strip().lower() for c in self.enabled_chains.split(",")))

    @property
    def discovery_sources(self) -> tuple[str, ...]:
        return ("hot_search", "trending") if self.trending_discovery_enabled else ("hot_search",)

    @model_validator(mode="after")
    def validate_ranges(self) -> "Settings":
        from revival_radar.chains import CHAINS

        if not self.chains or any(c not in CHAINS for c in self.chains):
            raise ValueError("ENABLED_CHAINS contains an unsupported chain")
        if self.min_market_cap > self.max_market_cap:
            raise ValueError("MIN_MARKET_CAP must be <= MAX_MARKET_CAP")
        if self.min_ath_drawdown > self.max_ath_drawdown:
            raise ValueError("MIN_ATH_DRAWDOWN must be <= MAX_ATH_DRAWDOWN")
        if self.minimum_history_observations > self.history_observations:
            raise ValueError("minimum_history_observations exceeds history_observations")
        if self.history_max_gap_seconds < self.scan_interval_seconds:
            raise ValueError("HISTORY_MAX_GAP_SECONDS must cover the scan interval")
        if self.base_sufficient_hours < self.base_min_hours:
            raise ValueError("BASE_SUFFICIENT_HOURS must cover BASE_MIN_HOURS")
        if self.kline_lookback_hours < self.base_min_hours:
            raise ValueError("KLINE_LOOKBACK_HOURS must cover BASE_MIN_HOURS")
        if list(self.base_maturity_hours) != sorted(set(self.base_maturity_hours)):
            raise ValueError("BASE_MATURITY_HOURS must contain five increasing durations")
        if list(self.base_maturity_fractions) != sorted(self.base_maturity_fractions):
            raise ValueError("BASE_MATURITY_FRACTIONS must be nondecreasing")
        if self.confirmed_base_min_hours < self.strong_base_min_hours:
            raise ValueError("CONFIRMED_BASE_MIN_HOURS must cover STRONG_BASE_MIN_HOURS")
        stage_thresholds = [
            self.watch_score_threshold,
            self.early_revival_score_threshold,
            self.reviving_score_threshold,
            self.strong_revival_score_threshold,
            self.confirmed_revival_score_threshold,
        ]
        if stage_thresholds != sorted(stage_thresholds):
            raise ValueError("Stage score thresholds must be nondecreasing")
        try:
            ZoneInfo(self.report_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("REPORT_TIMEZONE must be a valid IANA timezone") from None
        return self
