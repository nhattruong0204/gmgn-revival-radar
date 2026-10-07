from pydantic import BaseModel, Field


class Acceleration(BaseModel):
    volume_acceleration_5m: float | None = None
    volume_acceleration_1h: float | None = None
    tx_acceleration_5m: float | None = None
    tx_acceleration_1h: float | None = None
    volume_ratio_5m: float | None = None
    volume_5m_to_liquidity: float | None = None
    volume_1h_to_liquidity: float | None = None
    hot_rank_improvement: int | None = None
    previous_hot_rank: int | None = None
    first_hot_appearance: bool = False


class Structure(BaseModel):
    available: bool = False
    base_detected: bool = False
    base_duration_hours: float = 0
    higher_low_detected: bool = False
    higher_high_detected: bool = False
    breakout_detected: bool = False
    retest_detected: bool = False
    volatility_compression_score: float | None = None


class RevivalResult(BaseModel):
    score: int
    status: str
    eligible: bool
    # None keeps older stored observations explicitly unknown, without inventing scores.
    setup_score: int | None = Field(default=None, ge=0, le=100)
    trigger_score: int | None = Field(default=None, ge=0, le=100)
    confirmation_score: int | None = Field(default=None, ge=0, le=100)
    score_version: str | None = None
    discovery_context: str | None = None
    returning_activity: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    components: dict[str, int] = Field(default_factory=dict)
    acceleration: Acceleration = Field(default_factory=Acceleration)
    structure: Structure = Field(default_factory=Structure)


def has_returning_activity(result: RevivalResult) -> bool:
    if result.returning_activity is not None:
        return result.returning_activity
    # Legacy callers/records did not persist the evidence flag.
    return any(name in result.components for name in ("volume_5m", "volume_1h", "transactions"))
