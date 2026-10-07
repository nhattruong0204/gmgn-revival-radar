import re
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revival_radar.analysis.asset_classification import classify_asset

Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Source = Literal["hot_search", "trending"]


class Security(BaseModel):
    top10_ratio: Fraction | None = None
    dev_ratio: Fraction | None = None
    insider_ratio: Fraction | None = None
    sniper_ratio: Fraction | None = None
    bundler_ratio: Fraction | None = None
    smart_money_count: Count | None = None
    mint_renounced: bool | None = None
    freeze_renounced: bool | None = None
    liquidity_locked_ratio: Fraction | None = None
    liquidity_burned: bool | None = None
    dangerous: bool | None = None
    flags: list[str] = Field(default_factory=list)


class TokenSnapshot(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    timestamp: Nonnegative = Field(default_factory=time.time)
    chain: Literal["sol", "bsc", "base", "robinhood", "arc"]
    contract_address: str
    symbol: str = "?"
    name: str = ""
    asset_type: str | None = None
    asset_classification_reason: str | None = None
    discovery_source: set[Source] = Field(default_factory=set)
    hot_search_rank: Annotated[int, Field(ge=1)] | None = None
    trending_rank: Annotated[int, Field(ge=1)] | None = None
    price: Nonnegative | None = None
    market_cap: Nonnegative | None = None
    ath_market_cap: Nonnegative | None = None
    drawdown_from_ath: Finite | None = None
    liquidity: Nonnegative | None = None
    volume_1m: Nonnegative | None = None
    volume_5m: Nonnegative | None = None
    volume_1h: Nonnegative | None = None
    volume_6h: Nonnegative | None = None
    volume_24h: Nonnegative | None = None
    tx_1m: Count | None = None
    tx_5m: Count | None = None
    tx_1h: Count | None = None
    tx_6h: Count | None = None
    tx_24h: Count | None = None
    buys_5m: Count | None = None
    sells_5m: Count | None = None
    buys_1h: Count | None = None
    sells_1h: Count | None = None
    holders: Count | None = None
    price_change_5m: Finite | None = None
    price_change_1h: Finite | None = None
    price_change_6h: Finite | None = None
    price_change_24h: Finite | None = None
    token_age_seconds: Nonnegative | None = None
    security: Security = Field(default_factory=Security)
    data_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> "TokenSnapshot":
        pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if self.chain == "sol" else r"0x[0-9a-fA-F]{40}"
        if not re.fullmatch(pattern, self.contract_address):
            raise ValueError("Invalid contract address for chain")
        if self.chain != "sol":
            self.contract_address = self.contract_address.lower()
        classification = classify_asset(
            chain=self.chain,
            contract_address=self.contract_address,
            name=self.name,
            asset_type=self.asset_type,
            asset_classification_reason=self.asset_classification_reason,
        )
        self.asset_type = classification.asset_type
        self.asset_classification_reason = classification.reason
        self.drawdown_from_ath = (
            1 - self.market_cap / self.ath_market_cap
            if self.market_cap is not None and self.ath_market_cap and self.ath_market_cap > 0
            else None
        )
        return self

    @field_validator("symbol", "name")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return "".join(c for c in value if c.isprintable())[:100]

    @property
    def key(self) -> tuple[str, str]:
        return self.chain, self.contract_address


class Candle(BaseModel):
    timestamp: Nonnegative
    open: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    high: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    low: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    close: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    volume_usd: Nonnegative | None = None

    @model_validator(mode="after")
    def valid_range(self) -> "Candle":
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLC range")
        return self
