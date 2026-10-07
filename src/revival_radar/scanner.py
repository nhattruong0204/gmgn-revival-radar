import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from revival_radar.analysis.filters import first_pass
from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import score_token
from revival_radar.clients.gmgn import MarketDataSource
from revival_radar.clients.telegram import TelegramClient, format_alert
from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import TokenSnapshot
from revival_radar.storage.repository import Repository

log = logging.getLogger(__name__)


def merge_tokens(tokens: list[TokenSnapshot]) -> list[TokenSnapshot]:
    merged: dict[tuple[str, str], TokenSnapshot] = {}
    for token in tokens:
        if token.key not in merged:
            merged[token.key] = token
            continue
        previous = merged[token.key]
        values = previous.model_dump()
        values.update(
            {
                k: v
                for k, v in token.model_dump().items()
                if v is not None and k not in {"security", "discovery_source"}
            }
        )
        values["discovery_source"] = previous.discovery_source | token.discovery_source
        values["security"] = {
            **previous.security.model_dump(),
            **{
                k: v
                for k, v in token.security.model_dump().items()
                if v is not None and k != "flags"
            },
        }
        values["security"]["flags"] = list(set(previous.security.flags + token.security.flags))
        if previous.security.dangerous is True or token.security.dangerous is True:
            values["security"]["dangerous"] = True
        merged[token.key] = TokenSnapshot(**values)
    return list(merged.values())


@dataclass
class ScanReport:
    processed: int = 0
    errors: int = 0
    sent: int = 0
    potential_alerts: int = 0
    sources_ok: int = 0
    signals: list[tuple[TokenSnapshot, RevivalResult]] = field(default_factory=list)
    scan_id: int | None = None
    started_at: float = 0
    finished_at: float = 0
    duration_seconds: float = 0
    discovered_by_chain: dict[str, int] = field(default_factory=dict)
    discovered_by_source: dict[str, dict[str, int]] = field(default_factory=dict)
    source_errors: dict[str, dict[str, int]] = field(default_factory=dict)
    discovered_keys: set[tuple[str, str]] = field(default_factory=set)


class Scanner:
    def __init__(
        self,
        config: Settings,
        source: MarketDataSource,
        repository: Repository,
        telegram: TelegramClient,
        delivery_muted: Callable[[], bool] | None = None,
    ):
        self.config, self.source = config, source
        self.repository, self.telegram = repository, telegram
        self.delivery_muted = delivery_muted

    async def inspect(self, token: TokenSnapshot) -> tuple[TokenSnapshot, RevivalResult]:
        token = await self.source.enrich(token)
        history = self.repository.history(token, self.config.history_observations * 3)
        structure = Structure()
        if first_pass(token, self.config).passed:
            try:
                candles = await self.source.candles(token)
                if candles and token.timestamp - max(c.timestamp for c in candles) > 7200:
                    token.data_warnings.append(
                        "Latest closed candle is stale; structure unavailable"
                    )
                else:
                    structure = analyze_structure(candles, self.config)
            except Exception as exc:
                # Adapter boundary: malformed/unsupported candles affect this token only.
                token.data_warnings.append(f"Candles unavailable ({type(exc).__name__})")
        result = score_token(token, history, structure, self.config)
        return token, result

    async def process(self, token: TokenSnapshot, report: ScanReport) -> None:
        token, result = await self.inspect(token)
        self.repository.save_snapshot(token)
        report.processed += 1
        report.signals.append((token, result))
        if report.scan_id is not None:
            self.repository.record_evaluation(report.scan_id, token, result, self.config)
        log.info(
            "signal symbol=%s chain=%s score=%d status=%s eligible=%s",
            token.symbol,
            token.chain,
            result.score,
            result.status,
            result.eligible,
        )
        if not result.eligible or result.score < self.config.alert_score_threshold:
            return
        report.potential_alerts += 1
        if self.config.dry_run:
            log.info(
                "dry_run potential_alert chain=%s symbol=%s score=%d",
                token.chain,
                token.symbol,
                result.score,
            )
            return
        if self.config.alerts_paused or (self.delivery_muted and self.delivery_muted()):
            log.info("alerts paused chain=%s symbol=%s", token.chain, token.symbol)
            return
        alert_id = self.repository.reserve_alert(token, result, self.config)
        if alert_id is None:
            log.info("alert cooldown chain=%s symbol=%s", token.chain, token.symbol)
            return
        delivery = await self.telegram.send_text(format_alert(token, result))
        self.repository.finish_alert(alert_id, delivery.status, delivery.message_id)
        if delivery.status == "sent":
            report.sent += 1
            log.info("telegram alert sent symbol=%s", token.symbol)
        else:
            report.errors += 1

    async def scan_chain(self, chain: str, report: ScanReport) -> None:
        found = []
        report.discovered_by_source[chain] = {}
        report.source_errors[chain] = {}
        for source in ("hot_search", "trending"):
            try:
                discovered = await self.source.discover(chain, source)
                found.extend(discovered)
                report.discovered_by_source[chain][source] = len(discovered)
                report.sources_ok += 1
            except Exception as exc:
                report.errors += 1
                report.source_errors[chain][source] = 1
                log.warning(
                    "discovery failed chain=%s source=%s error=%s",
                    chain,
                    source,
                    type(exc).__name__,
                )
        candidates = merge_tokens(found)
        keys = {t.key for t in candidates}
        report.discovered_by_chain[chain] = len(keys)
        report.discovered_keys.update(keys)
        now = time.time()
        watched = self.repository.watchlist(
            chain, now - self.config.watchlist_hours * 3600, self.config.watchlist_limit
        )
        for old in watched:
            if old.key not in keys:
                # Only identity + historical ATH survive. Live metrics and ranks must be refreshed.
                candidates.append(
                    TokenSnapshot(
                        chain=chain,
                        contract_address=old.contract_address,
                        symbol=old.symbol,
                        name=old.name,
                        asset_type=old.asset_type,
                        asset_classification_reason=old.asset_classification_reason,
                        ath_market_cap=old.ath_market_cap,
                        data_warnings=["Off rankings; ATH cap from last discovery observation"],
                    )
                )
        for token in candidates:
            try:
                await self.process(token, report)
            except Exception as exc:
                report.errors += 1
                log.warning(
                    "token failed chain=%s address=%s error=%s",
                    chain,
                    token.contract_address,
                    type(exc).__name__,
                )

    async def scan_once(self) -> ScanReport:
        report = ScanReport(started_at=time.time())
        started = time.monotonic()
        report.scan_id = self.repository.begin_scan(report.started_at)
        outcomes = await asyncio.gather(
            *(self.scan_chain(chain, report) for chain in self.config.chains),
            return_exceptions=True,
        )
        for chain, outcome in zip(self.config.chains, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                report.errors += 1
                log.warning("chain failed chain=%s error=%s", chain, type(outcome).__name__)
        report.finished_at = time.time()
        report.duration_seconds = time.monotonic() - started
        self.repository.finish_scan(report.scan_id, report, report.finished_at)
        log.info(
            "scan complete processed=%d potential_alerts=%d sent=%d errors=%d sources_ok=%d "
            "duration_seconds=%.1f",
            report.processed,
            report.potential_alerts,
            report.sent,
            report.errors,
            report.sources_ok,
            report.duration_seconds,
        )
        if report.duration_seconds > self.config.scan_interval_seconds:
            log.warning(
                "scan exceeded target interval duration_seconds=%.1f target_seconds=%.1f; "
                "reduce discovery/watchlist limits if history becomes stale",
                report.duration_seconds,
                self.config.scan_interval_seconds,
            )
        return report
