import asyncio
import logging
import time
import uuid
from contextlib import suppress
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Protocol

import httpx
from pydantic import ValidationError

from revival_radar.clients.normalization import (
    enriched_token,
    number,
    parse_candles,
    ranked_token,
    security_from,
)
from revival_radar.config import Settings
from revival_radar.metrics import ScanMetrics
from revival_radar.models.token import Candle, TokenSnapshot

log = logging.getLogger(__name__)
API_BASE = "https://openapi.gmgn.ai"


class DataSourceError(Exception):
    """Sanitized error: response bodies, headers and credentials are not logged."""


class MarketDataSource(Protocol):
    async def discover(self, chain: str, source: str) -> list[TokenSnapshot]: ...
    async def enrich_market(self, token: TokenSnapshot) -> TokenSnapshot: ...
    async def enrich_security(self, token: TokenSnapshot) -> TokenSnapshot: ...
    async def candles(self, token: TokenSnapshot) -> list[Candle]: ...


def retry_delay(response: httpx.Response, body: dict, now: float) -> float:
    delays = [1.0]
    for value in (response.headers.get("x-ratelimit-reset"), body.get("reset_at")):
        timestamp = number(value)
        if timestamp is not None:
            delays.append(timestamp - now + 1)
    retry = response.headers.get("retry-after")
    seconds = number(retry)
    if seconds is not None:
        delays.append(seconds)
    elif retry:
        with suppress(ValueError, TypeError, OverflowError):
            delays.append(parsedate_to_datetime(retry).timestamp() - now + 1)
    return max(delays)


class GMGNClient:
    def __init__(self, config: Settings, http: httpx.AsyncClient):
        self.config, self.http = config, http
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._blocked_until = 0.0
        self.metrics = ScanMetrics()

    async def request(
        self, method: str, path: str, *, params: dict | None = None, body: dict | None = None
    ) -> Any:
        if not self.config.gmgn_api_key.get_secret_value():
            raise DataSourceError("GMGN_API_KEY is missing; set it securely or use demo")
        async with self._lock:
            for attempt in range(self.config.http_attempts):
                if time.time() < self._blocked_until:
                    raise DataSourceError(
                        f"GMGN cooldown until {int(self._blocked_until)} UTC epoch"
                    )
                await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                self._next_request = time.monotonic() + self.config.request_spacing_seconds
                query = {
                    **(params or {}),
                    "timestamp": int(time.time()),
                    "client_id": str(uuid.uuid4()),
                }
                try:
                    self.metrics.calls[path] += 1
                    response = await self.http.request(
                        method,
                        API_BASE + path,
                        params=query,
                        json=body,
                        headers={
                            "X-APIKEY": self.config.gmgn_api_key.get_secret_value(),
                            "User-Agent": "gmgn-revival-radar/0.2.0",
                        },
                        timeout=self.config.http_timeout_seconds,
                    )
                except httpx.TransportError as exc:
                    if attempt + 1 == self.config.http_attempts:
                        raise DataSourceError(
                            f"GMGN transport failure ({type(exc).__name__})"
                        ) from None
                    await asyncio.sleep(min(2**attempt, self.config.retry_max_wait_seconds))
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                rate_limited = response.status_code == 429 or payload.get("code") in (429, "429")
                if rate_limited:
                    delay = retry_delay(response, payload, time.time())
                    self._blocked_until = time.time() + delay
                    log.warning("gmgn rate_limited retry_in=%.1f", delay)
                    if (
                        delay > self.config.retry_max_wait_seconds
                        or attempt + 1 == self.config.http_attempts
                    ):
                        raise DataSourceError("GMGN rate limited; shared cooldown active")
                    await asyncio.sleep(delay)
                    continue
                if response.status_code in (500, 502, 503, 504):
                    delay = max(2**attempt, retry_delay(response, payload, time.time()))
                    if delay > self.config.retry_max_wait_seconds:
                        self._blocked_until = time.time() + delay
                        raise DataSourceError("GMGN server requested a long retry delay")
                    if attempt + 1 < self.config.http_attempts:
                        await asyncio.sleep(delay)
                        continue
                if response.is_success and payload.get("code") in (0, "0") and "data" in payload:
                    data = payload["data"]
                    # Live /market/rank can wrap its upstream result in a second
                    # code/data envelope. Validate it rather than accepting an error as data.
                    if isinstance(data, dict) and "code" in data and "data" in data:
                        if data["code"] not in (0, "0"):
                            raise DataSourceError(f"GMGN upstream error route={path}")
                        data = data["data"]
                    return data
                raise DataSourceError(
                    f"GMGN request failed HTTP={response.status_code} route={path}"
                )
        raise DataSourceError("GMGN retry attempts exhausted")

    async def discover(
        self, chain: str, source: Literal["hot_search", "trending"]
    ) -> list[TokenSnapshot]:
        params = {
            "chain": chain,
            "interval": self.config.discovery_interval,
            "limit": self.config.discovery_limit,
        }
        if source == "hot_search":
            data = await self.request("POST", "/v1/market/hot_searches", body={"params": [params]})
            if not isinstance(data, list):
                raise DataSourceError("Unexpected Hot Searches response shape")
            rows = []
            for block in data:
                if isinstance(block, dict) and block.get("chain") == chain:
                    if not isinstance(block.get("tokens"), list):
                        raise DataSourceError("Unexpected Hot Searches token list")
                    rows.extend(block["tokens"])
        else:
            data = await self.request("GET", "/v1/market/rank", params=params)
            rows = data.get("rank") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise DataSourceError("Unexpected Trending response shape")
        result = []
        now = time.time()
        for rank, row in enumerate(rows[: self.config.discovery_limit], 1):
            if not isinstance(row, dict) or row.get("chain", chain) != chain:
                continue
            try:
                result.append(
                    ranked_token(row, chain, source, rank, self.config.discovery_interval, now)
                )
            except (ValidationError, ValueError, TypeError):
                log.warning("malformed token chain=%s source=%s rank=%d", chain, source, rank)
        log.info("scanning chain=%s source=%s count=%d", chain, source, len(result))
        return result

    async def enrich_market(self, token: TokenSnapshot) -> TokenSnapshot:
        info = await self.request(
            "GET",
            "/v1/token/info",
            params={"chain": token.chain, "address": token.contract_address},
        )
        if not isinstance(info, dict) or not isinstance(info.get("price"), dict):
            raise DataSourceError("Unexpected token info response shape")
        address = info.get("address")
        if isinstance(address, str) and token.chain != "sol":
            address = address.lower()
        if address is not None and address != token.contract_address:
            raise DataSourceError("Token info address mismatch")
        enriched = enriched_token(token, info, {})
        enriched._market_security_info = {
            "stat": info.get("stat"),
            "wallet_tags_stat": info.get("wallet_tags_stat"),
        }
        return enriched

    async def enrich_security(self, token: TokenSnapshot) -> TokenSnapshot:
        raw = await self.request(
            "GET",
            "/v1/token/security",
            params={"chain": token.chain, "address": token.contract_address},
        )
        if not isinstance(raw, dict):
            raise DataSourceError("Unexpected security response shape")
        return token.model_copy(
            update={
                "security": security_from(
                    token._market_security_info, raw, token.chain, token.security
                )
            }
        )

    async def enrich(self, token: TokenSnapshot) -> TokenSnapshot:
        """Compatibility convenience; the scanner uses the split operations."""
        token = await self.enrich_market(token)
        try:
            return await self.enrich_security(token)
        except DataSourceError:
            token.data_warnings.append("Security enrichment unavailable")
            return token

    async def candles(self, token: TokenSnapshot) -> list[Candle]:
        data = await self.request(
            "GET",
            "/v1/market/token_kline",
            params={
                "chain": token.chain,
                "address": token.contract_address,
                "resolution": "1h",
                "from": int((token.timestamp - self.config.kline_lookback_hours * 3600) * 1000),
                "to": int(token.timestamp * 1000),
            },
        )
        return parse_candles(data, token.timestamp)
