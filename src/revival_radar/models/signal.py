from pydantic import BaseModel, Field


class Acceleration(BaseModel):
    volume_acceleration_5m: float | None = None
    volume_acceleration_1h: float | None = None
    tx_acceleration_5m: float | None = None
    tx_acceleration_1h: float | None = None
    volume_ratio_5m: float | None = None
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
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    components: dict[str, int] = Field(default_factory=dict)
    acceleration: Acceleration = Field(default_factory=Acceleration)
    structure: Structure = Field(default_factory=Structure)
