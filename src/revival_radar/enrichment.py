"""Persistent slow-data caches and per-scan operation budgets; HTTP pacing stays in GMGN."""

import json

from pydantic import ValidationError

from revival_radar.clients.normalization import security_from
from revival_radar.models.token import Candle, Security


class EnrichmentDeferred(Exception):
    """A request budget was exhausted; retry the candidate in a later scan."""


class Enrichment:
    def __init__(self, config, source, repository, metrics):
        self.config, self.source, self.repo, self.metrics = config, source, repository, metrics
        self.used = {"market": 0, "security": 0, "kline": 0}
        self.deferred_tokens: set[tuple[str, str]] = set()

    def reserve(self, stage, token):
        limit = getattr(
            self.config,
            f"max_{stage}_enrich_per_scan" if stage != "kline" else "max_kline_fetch_per_scan",
        )
        if self.used[stage] >= limit:
            self.metrics.deferred[stage] += 1
            self.deferred_tokens.add(token.key)
            raise EnrichmentDeferred(stage)
        self.used[stage] += 1  # Failed operations consume budget too; retries remain HTTP-paced.
        self.metrics.cache[f"{stage}_fetch"] += 1

    async def market(self, token):
        self.reserve("market", token)
        market = getattr(self.source, "enrich_market", None) or self.source.enrich
        return await market(token)

    async def security(self, token):
        ttl = self.config.security_cache_ttl_seconds
        entry = self.repo.cache_entry("security", token) if ttl else None
        if (
            entry
            and 0 <= token.timestamp - entry["fetched_at"] < ttl
            and token.timestamp < entry["expires_at"]
        ):
            try:
                cached = Security.model_validate_json(entry["payload"])
            except (ValueError, ValidationError):
                self.metrics.cache["security_invalid"] += 1
            else:
                # Fresh token-info statistics retain their original priority over cached data.
                security = security_from(token._market_security_info, {}, token.chain, cached)
                if token.security.dangerous is True:
                    security.dangerous = True
                    security.flags = sorted(set(security.flags + token.security.flags))
                self.metrics.cache["security_hit"] += 1
                token.data_warnings.append(
                    f"Security cached ({int(token.timestamp - entry['fetched_at'])}s old)"
                )
                return token.model_copy(update={"security": security})
        self.reserve("security", token)
        method = getattr(self.source, "enrich_security", None)
        if method is None:
            return token  # Legacy market adapters already enriched security.
        token = await method(token)
        if ttl:
            self.repo.cache_save(
                "security", token, token.security.model_dump_json(), token.timestamp + ttl
            )
        return token

    async def candles(self, token):
        ttl = self.config.kline_cache_ttl_seconds
        kind = f"kline:1h:{self.config.kline_lookback_hours}"
        entry = self.repo.cache_entry(kind, token) if ttl else None
        now, hour = token.timestamp, int(token.timestamp // 3600)
        existing, old_hour = [], None
        if entry and entry["fetched_at"] <= now:
            try:
                payload = json.loads(entry["payload"])
                old_hour = payload["hour"]
                existing = [Candle.model_validate(c) for c in payload["candles"]]
            except (ValueError, KeyError, TypeError, ValidationError):
                self.metrics.cache["kline_invalid"] += 1
            else:
                # Closed bars are immutable within an hour. TTL limits retries for empty data;
                # a new UTC hour always invalidates the cache, even before TTL expires.
                if old_hour == hour and (
                    existing or now < min(entry["expires_at"], entry["fetched_at"] + ttl)
                ):
                    self.metrics.cache["kline_hit"] += 1
                    return self._closed(existing, now)
        self.reserve("kline", token)
        incremental = getattr(self.source, "candles_since", None)
        if existing and old_hour == hour - 1 and incremental is not None:
            incoming = await incremental(token, max(c.timestamp for c in existing))
            bars = list({c.timestamp: c for c in [*existing, *incoming]}.values())
            self.metrics.cache["kline_incremental"] += 1
        else:
            bars = await self.source.candles(token)
        bars = self._closed(bars, now)
        if ttl:
            self.repo.cache_save(
                kind,
                token,
                json.dumps({"hour": hour, "candles": [c.model_dump() for c in bars]}),
                min(now + ttl, (hour + 1) * 3600) if not bars else (hour + 1) * 3600,
            )
        return bars

    def _closed(self, bars, now):
        return sorted(
            (
                c
                for c in bars
                if now - self.config.kline_lookback_hours * 3600 <= c.timestamp
                and c.timestamp + 3600 <= now
            ),
            key=lambda c: c.timestamp,
        )
