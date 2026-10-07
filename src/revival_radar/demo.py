import json
from importlib.resources import files

from revival_radar.models.token import Candle, TokenSnapshot


class DemoSource:
    """Deterministic synthetic scenarios; no network and no real token endorsements."""

    def __init__(self):
        self.data = json.loads(
            files("revival_radar").joinpath("fixtures/scenarios.json").read_text()
        )

    def histories(self) -> list[TokenSnapshot]:
        return [TokenSnapshot(**s) for scenario in self.data for s in scenario["history"]]

    async def discover(self, chain: str, source: str) -> list[TokenSnapshot]:
        result = []
        for scenario in self.data:
            values = scenario["token"].copy()
            if values["chain"] != chain:
                continue
            values["discovery_source"] = [source]
            values["trending_rank" if source == "hot_search" else "hot_search_rank"] = None
            result.append(TokenSnapshot(**values))
        return result

    async def enrich(self, token: TokenSnapshot) -> TokenSnapshot:
        return token

    async def enrich_market(self, token: TokenSnapshot) -> TokenSnapshot:
        return await self.enrich(token)

    async def enrich_security(self, token: TokenSnapshot) -> TokenSnapshot:
        return token

    async def candles(self, token: TokenSnapshot) -> list[Candle]:
        scenario = next(
            s for s in self.data if s["token"]["contract_address"] == token.contract_address
        )
        return [Candle(**c) for c in scenario["candles"]]
