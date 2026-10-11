"""Explicit public response contracts; stored JSON is never returned wholesale."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from revival_radar.models.token import Candle

Finite = Annotated[float, Field(allow_inf_nan=False)]


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class PublicSecurity(PublicModel):
    top10_ratio: Finite | None = None
    dev_ratio: Finite | None = None
    insider_ratio: Finite | None = None
    sniper_ratio: Finite | None = None
    bundler_ratio: Finite | None = None
    smart_money_count: int | None = None
    mint_renounced: bool | None = None
    freeze_renounced: bool | None = None
    liquidity_locked_ratio: Finite | None = None
    liquidity_burned: bool | None = None
    dangerous: bool | None = None
    flags: list[str] = Field(default_factory=list)


class PublicToken(PublicModel):
    timestamp: Finite
    chain: str
    contract_address: str
    symbol: str = "?"
    name: str = ""
    asset_type: str | None = None
    discovery_source: list[str] = Field(default_factory=list)
    hot_search_rank: int | None = None
    trending_rank: int | None = None
    price: Finite | None = None
    market_cap: Finite | None = None
    ath_market_cap: Finite | None = None
    drawdown_from_ath: Finite | None = None
    liquidity: Finite | None = None
    volume_1m: Finite | None = None
    volume_5m: Finite | None = None
    volume_1h: Finite | None = None
    volume_6h: Finite | None = None
    volume_24h: Finite | None = None
    tx_1m: int | None = None
    tx_5m: int | None = None
    tx_1h: int | None = None
    tx_6h: int | None = None
    tx_24h: int | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    buys_1h: int | None = None
    sells_1h: int | None = None
    holders: int | None = None
    price_change_5m: Finite | None = None
    price_change_1h: Finite | None = None
    price_change_6h: Finite | None = None
    price_change_24h: Finite | None = None
    token_age_seconds: Finite | None = None
    security: PublicSecurity = Field(default_factory=PublicSecurity)
    data_warnings: list[str] = Field(default_factory=list)


class PublicAcceleration(PublicModel):
    volume_acceleration_5m: Finite | None = None
    volume_acceleration_1h: Finite | None = None
    tx_acceleration_5m: Finite | None = None
    tx_acceleration_1h: Finite | None = None
    volume_ratio_5m: Finite | None = None
    volume_5m_to_liquidity: Finite | None = None
    volume_1h_to_liquidity: Finite | None = None
    hot_rank_improvement: int | None = None
    previous_hot_rank: int | None = None
    first_hot_appearance: bool = False


class PublicStructure(PublicModel):
    available: bool = False
    base_detected: bool = False
    base_duration_hours: Finite = 0
    higher_low_detected: bool = False
    higher_high_detected: bool = False
    breakout_detected: bool = False
    retest_detected: bool = False
    volatility_compression_score: Finite | None = None


class PublicSignal(PublicModel):
    score: int
    status: str
    eligible: bool
    setup_score: int | None = None
    trigger_score: int | None = None
    confirmation_score: int | None = None
    score_version: str | None = None
    discovery_context: str | None = None
    returning_activity: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    components: dict[str, int] = Field(default_factory=dict)
    acceleration: PublicAcceleration = Field(default_factory=PublicAcceleration)
    structure: PublicStructure = Field(default_factory=PublicStructure)


class Observation(PublicModel):
    id: int
    timestamp: Finite
    chain: str
    contract_address: str
    symbol: str
    name: str = ""
    score: int
    status: str
    eligible: bool | None
    setup_score: int | None = None
    trigger_score: int | None = None
    confirmation_score: int | None = None
    price: Finite | None = None
    market_cap: Finite | None = None
    liquidity: Finite | None = None
    drawdown_from_ath: Finite | None = None
    volume_5m: Finite | None = None
    volume_1h: Finite | None = None
    tx_5m: int | None = None
    holders: int | None = None
    blockers: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    preset: str | None = None
    revision: int | None = None
    token: PublicToken | None = None
    signal: PublicSignal | None = None


class RadarPage(PublicModel):
    items: list[Observation]
    total: int
    page: int
    page_size: int
    as_of: Finite
    last_scan_at: Finite | None


class TokenDetail(PublicModel):
    observation: Observation
    latest_market: PublicToken | None
    as_of: Finite
    last_scan_at: Finite | None
    links: dict[str, str]


class SnapshotPoint(PublicModel):
    timestamp: Finite
    price: Finite | None
    market_cap: Finite | None


class ScorePoint(PublicModel):
    timestamp: Finite
    score: int
    setup_score: int | None
    trigger_score: int | None
    confirmation_score: int | None


class TokenHistory(PublicModel):
    snapshots: list[SnapshotPoint]
    scores: list[ScorePoint]
    candles: list[Candle]
    candles_fetched_at: Finite | None
    candles_expires_at: Finite | None
    as_of: Finite
    since: Finite
    truncated: bool


class OutcomeHorizon(PublicModel):
    observed: int
    returns_available: int
    positive: int
    median_return_pct: Finite | None
    pending: int
    missed: int


class Outcomes(PublicModel):
    since: Finite
    until: Finite
    horizons: dict[str, OutcomeHorizon]


class Alert(PublicModel):
    id: int
    timestamp: Finite
    chain: str
    contract_address: str
    symbol: str
    score: int
    preset: str | None
    revision: int | None
    observation: Observation


class AlertsPage(PublicModel):
    items: list[Alert]
    total: int
    page: int
    page_size: int
    as_of: Finite
    last_scan_at: Finite | None


class AlertOutcome(PublicModel):
    horizon_hours: int
    due_at: Finite
    observed_at: Finite
    price: Finite | None
    market_cap: Finite | None
    return_pct: Finite | None


class AlertDetail(Alert):
    latest_observation: Observation | None
    outcomes: list[AlertOutcome]
    as_of: Finite


class ReportEntry(PublicModel):
    chain: str
    contract_address: str
    symbol: str
    observations: int
    peak: Observation
    latest: Observation


class MarketReport(PublicModel):
    since: Finite
    until: Finite
    timezone: str
    hours: Finite
    chain: str
    evaluations: int
    unique_tokens: int
    completed_scans: int
    incomplete_scans: int
    first_observation: Finite | None
    last_finished: Finite | None
    multiple_configurations: bool
    entries: list[ReportEntry]


class ChainInfo(PublicModel):
    id: str
    name: str


class Metadata(PublicModel):
    chains: list[ChainInfo]
    telegram_url: str | None
    as_of: Finite
    last_scan_at: Finite | None
