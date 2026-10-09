"""GMGN market analytics with independently paced Solana mint verification."""

import httpx

from revival_radar.clients.gmgn import GMGNClient
from revival_radar.clients.helius import HeliusClient
from revival_radar.config import Settings
from revival_radar.metrics import ScanMetrics
from revival_radar.models.token import TokenSnapshot


class HybridDataSource:
    def __init__(self, config: Settings, http: httpx.AsyncClient):
        self.gmgn = GMGNClient(config, http)
        self.helius = HeliusClient(config, http)

    @property
    def config(self) -> Settings:
        return self.gmgn.config

    @config.setter
    def config(self, config: Settings) -> None:
        self.gmgn.config = config
        self.helius.config = config

    @property
    def metrics(self) -> ScanMetrics:
        return self.gmgn.metrics

    @metrics.setter
    def metrics(self, metrics: ScanMetrics) -> None:
        self.gmgn.metrics = metrics
        self.helius.metrics = metrics

    @property
    def helius_enabled(self) -> bool:
        return self.config.helius_enabled and bool(self.config.helius_api_token.get_secret_value())

    async def discover(self, chain: str, source: str):
        return await self.gmgn.discover(chain, source)

    async def enrich_market(self, token: TokenSnapshot):
        return await self.gmgn.enrich_market(token)

    async def enrich_security(self, token: TokenSnapshot):
        return await self.gmgn.enrich_security(token)

    async def candles(self, token: TokenSnapshot):
        return await self.gmgn.candles(token)

    async def candles_since(self, token: TokenSnapshot, since: float):
        return await self.gmgn.candles_since(token, since)

    async def verify_mint(self, token: TokenSnapshot):
        return await self.helius.fetch_mint(token.contract_address)
