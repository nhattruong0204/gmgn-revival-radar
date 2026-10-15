"""Explicit public response contracts; stored JSON is never returned wholesale."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from revival_radar.models.brief import ResearchBrief
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


class BriefPage(PublicModel):
    items: list[ResearchBrief]
    total: int
    page: int
    page_size: int
    as_of: Finite


class CheckpointResearch(PublicModel):
    alert_id: int
    timestamp: Finite
    chain: str
    contract_address: str
    symbol: str
    score: int
    stage: str
    preset: str | None
    revision: int | None
    score_version: str | None
    origin: str
    knowledge: str
    saved_at: Finite | None
    sent_at: Finite | None
    cohort_id: str
    horizon_hours: int
    due_at: Finite
    window_end: Finite
    observed_at: Finite | None
    recorded_at: Finite | None
    checkpoint_origin: str
    initial_price: Finite | None
    checkpoint_price: Finite | None
    checkpoint_market_cap: Finite | None
    reported_return_pct: Finite | None
    return_pct: Finite | None
    state: Literal["return", "missing_price", "unresolved", "pending", "missed"]
    reason: str
    brief_id: str | None
    source: str


class ReturnBin(PublicModel):
    label: str
    count: int


class ResearchAccounting(PublicModel):
    total: int
    observed: int
    returns_available: int
    positive: int
    negative: int
    zero: int
    missing_price: int
    unresolved: int
    pending: int
    missed: int
    median_return_pct: Finite | None
    positive_rate_pct: Finite | None
    small_sample: bool
    comparable: bool
    distribution: list[ReturnBin]


class ResearchCohort(PublicModel):
    id: str
    chain: str
    preset: str | None
    revision: int | None
    score_version: str | None
    origin: str
    knowledge: str
    counts: ResearchAccounting


class TrackRecord(PublicModel):
    methodology_version: Literal["checkpoint-cohort-v1"] = "checkpoint-cohort-v1"
    since: Finite
    as_of: Finite
    filters: dict[str, str | int | None]
    reconciliation: dict[str, int]
    definition: str
    exclusions: list[str]
    limitations: list[str]
    summary: ResearchAccounting
    cohorts: list[ResearchCohort]
    items: list[CheckpointResearch]
    page: int
    page_size: int
    total: int


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
    origin: Literal["live", "synthetic", "test", "unknown"] = "unknown"
    origin_confidence: Literal["recorded", "unknown"] = "unknown"
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
    origin: Literal["live", "synthetic", "test", "unknown"] = "unknown"
    timestamp: Finite
    price: Finite | None
    market_cap: Finite | None


class ScorePoint(PublicModel):
    origin: Literal["live", "synthetic", "test", "unknown"] = "unknown"
    timestamp: Finite
    score: int
    setup_score: int | None
    trigger_score: int | None
    confirmation_score: int | None


class TokenHistory(PublicModel):
    candles_origin: Literal["live", "synthetic", "test", "unknown"] = "unknown"
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


class CallPerformance(PublicModel):
    """Call-to-latest performance computed on read from saved snapshots; nulls carry a reason."""

    call_market_cap: Finite | None = None
    call_price: Finite | None = None
    latest_market_cap: Finite | None = None
    latest_price: Finite | None = None
    latest_observed_at: Finite | None = None
    freshness: Literal["fresh", "stale", "none"] = "none"
    change_since_call_pct: Finite | None = None
    change_reason: Literal["no_call_mc", "no_later_observation"] | None = None
    mfe_pct: Finite | None = None
    mae_pct: Finite | None = None
    observations: int = 0
    max_gap_seconds: Finite | None = None
    elapsed_seconds: Finite | None = None
    token_call_index: int | None = None
    token_call_count: int | None = None
    # Supply basis of the comparison: same (like-with-like snapshots), mixed (had to compare
    # across sources) or unknown (legacy/unlabelled call). Optional for older clients.
    basis: Literal["same", "mixed", "unknown"] | None = None
    call_market_cap_source: Literal["rank", "circulating_enriched"] | None = None
    # Later snapshots on another/unlabelled basis that were left out of a `same` comparison.
    other_basis_observations: int | None = None


class Alert(PublicModel):
    recorded_at: Finite | None = None
    confirmed_at: Finite | None = None
    id: int
    timestamp: Finite
    chain: str
    contract_address: str
    symbol: str
    score: int
    preset: str | None
    revision: int | None
    observation: Observation
    performance: CallPerformance


class AlertGroup(PublicModel):
    """One token's calls in `group=token` mode. `alert_ids` index into `items`."""

    chain: str
    contract_address: str
    symbol: str
    call_count: int
    latest_call_at: Finite
    alert_ids: list[int]
    truncated: bool = False


class AlertsPage(PublicModel):
    items: list[Alert]
    groups: list[AlertGroup] | None = None
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
    checkpoint_research: list[CheckpointResearch] = Field(default_factory=list)
    original_brief_id: str | None = None
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


class RegimeCoverage(PublicModel):
    price_change_1h: int = 0
    price_change_24h: int = 0


class RegimeContext(PublicModel):
    """Saved per-scan market regime; explanatory context, never a signal."""

    scan_finished: Finite
    chain: str
    sources: list[str] = Field(default_factory=list)
    universe_size: int
    median_price_change_1h: Finite | None = None
    median_price_change_1h_reason: str | None = None
    median_price_change_24h: Finite | None = None
    median_price_change_24h_reason: str | None = None
    share_positive_1h: Finite | None = None
    share_positive_1h_reason: str | None = None
    eligible_count: int | None = None
    coverage: RegimeCoverage = Field(default_factory=RegimeCoverage)
    minimum_sample: int = 5


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
    market_context: RegimeContext | None = None


class ChainInfo(PublicModel):
    id: str
    name: str


class PublishingPolicy(PublicModel):
    """Nonsecret summary of the call-publishing rule saved with the latest completed scan."""

    min_stage: Literal["WATCH", "EARLY_REVIVAL", "REVIVING", "STRONG_REVIVAL", "CONFIRMED_REVIVAL"]
    score_threshold: int
    realert_requires_new_evidence: bool
    revision: int | None = None


class Metadata(PublicModel):
    active_chains: list[ChainInfo] = Field(default_factory=list)
    collection_status: Literal["current", "stale", "disabled", "unknown"] = "unknown"
    worker_heartbeat_at: Finite | None = None
    data_mode: Literal["saved", "synthetic"] = "saved"
    publishing_policy: PublishingPolicy | None = None
    chains: list[ChainInfo]
    telegram_url: str | None
    as_of: Finite
    last_scan_at: Finite | None


class JournalGroup(PublicModel):
    """Descriptive counts for one stage or revision; every count has its own denominator."""

    key: str
    calls: int
    measurable: int
    positive: int
    negative: int
    flat: int
    unknown: int
    median_change_pct: Finite | None = None


class JournalSummary(PublicModel):
    calls: int
    measurable: int
    positive: int
    negative: int
    flat: int
    unknown: int
    unknown_reasons: dict[str, int]
    positive_rate_pct: Finite | None
    median_change_pct: Finite | None
    mean_change_pct: Finite | None
    distribution: list[ReturnBin]
    mfe_known: int
    mfe_median_pct: Finite | None
    mae_known: int
    mae_median_pct: Finite | None
    by_stage: list[JournalGroup]
    by_revision: list[JournalGroup]
    revisions: list[str]
    mixed_configurations: bool
    small_sample: bool
    small_sample_threshold: int
    stale_latest: int
    checkpoint_24h: ResearchAccounting


class CallJournal(PublicModel):
    methodology_version: Literal["call-journal-v1"] = "call-journal-v1"
    since: Finite
    as_of: Finite
    last_scan_at: Finite | None
    filters: dict[str, str | int | float | None]
    sort: str
    definition: str
    limitations: list[str]
    reconciliation: dict[str, int]
    summary: JournalSummary
    items: list[Alert]
    total: int
    page: int
    page_size: int
