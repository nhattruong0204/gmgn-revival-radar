import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from revival_radar.analysis.filters import discovery_prefilter, first_pass
from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import score_token
from revival_radar.clients.gmgn import DataSourceError, MarketDataSource
from revival_radar.clients.telegram import TelegramClient, alert_buttons, format_alert
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.metrics import CURRENT_SCAN, ScanMetrics
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import Security, TokenSnapshot
from revival_radar.storage.repository import Repository

log = logging.getLogger(__name__)
FUNNEL_STAGES = (
    "discovered",
    "watchlist_added",
    "prefilter_checked",
    "prefilter_pass",
    "prefilter_rejected",
    "market_enriched",
    "market_pass",
    "activity_trigger",
    "kline_requested",
    "base_detected",
    "serious_candidates",
    "security_requested",
    "security_enriched",
    "evaluated",
    "eligible",
    "potential_alerts",
    "alerted",
)


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
    performance: dict = field(default_factory=dict)
    funnel: dict[str, dict[str, int]] = field(default_factory=dict)


class Scanner:
    def __init__(
        self,
        config: Settings,
        source: MarketDataSource,
        repository: Repository,
        telegram: TelegramClient,
        delivery_muted: Callable[[], bool] | None = None,
        configuration: dict | None = None,
    ):
        self.config, self.source = config, source
        self.repository, self.telegram = repository, telegram
        self.delivery_muted = delivery_muted
        self.metrics = ScanMetrics()
        self.configuration = (
            configuration if configuration is not None else configuration_context(config)
        )

    @staticmethod
    def _count(report: ScanReport | None, chain: str, stage: str) -> None:
        if report is not None:
            counts = report.funnel.setdefault(chain, {})
            counts[stage] = counts.get(stage, 0) + 1

    async def inspect(
        self, token: TokenSnapshot, report: ScanReport | None = None
    ) -> tuple[TokenSnapshot, RevivalResult]:
        # The fallback keeps old third-party adapters usable; production GMGN is split.
        market = getattr(self.source, "enrich_market", None) or self.source.enrich
        try:
            with self.metrics.measure("market"):
                token = await market(token)
        except Exception:
            self.metrics.failures["market"] += 1
            raise
        self._count(report, token.chain, "market_enriched")
        history = self.repository.history(token, self.config.history_observations * 3)
        structure = Structure()
        result = score_token(token, history, structure, self.config)
        if not first_pass(token, self.config).passed:
            return token, result
        self._count(report, token.chain, "market_pass")
        activity = any(
            name in result.components for name in ("volume_5m", "volume_1h", "transactions")
        )
        if not activity:
            return token, result
        self._count(report, token.chain, "activity_trigger")
        if token.security.dangerous is True:
            return token, result
        self._count(report, token.chain, "kline_requested")
        try:
            with self.metrics.measure("kline"):
                candles = await self.source.candles(token)
                if candles and token.timestamp - max(c.timestamp for c in candles) > 7200:
                    token.data_warnings.append(
                        "Latest closed candle is stale; structure unavailable"
                    )
                else:
                    structure = analyze_structure(candles, self.config)
        except Exception as exc:
            self.metrics.failures["kline"] += 1
            token.data_warnings.append(f"Candles unavailable ({type(exc).__name__})")
        if structure.base_detected:
            self._count(report, token.chain, "base_detected")
        # Security can remove discovery-time penalties as well as add deductions.
        # Reuse the unchanged scorer to form an optimistic bound; don't reject a
        # serious candidate solely because a discovery-time concentration is stale.
        optimistic = score_token(
            token.model_copy(update={"security": Security()}), history, structure, self.config
        )
        if optimistic.eligible and optimistic.score >= self.config.alert_score_threshold:
            self._count(report, token.chain, "serious_candidates")
            security = getattr(self.source, "enrich_security", None)
            if security is not None:
                self._count(report, token.chain, "security_requested")
                try:
                    with self.metrics.measure("security"):
                        token = await security(token)
                    self._count(report, token.chain, "security_enriched")
                except Exception as exc:
                    self.metrics.failures["security"] += 1
                    if not isinstance(exc, DataSourceError):
                        raise
                    # Preserve the existing nullable-security behavior and warnings.
                    token.data_warnings.append("Security enrichment unavailable")
        return token, score_token(token, history, structure, self.config)

    async def process(self, token: TokenSnapshot, report: ScanReport) -> None:
        self._count(report, token.chain, "prefilter_checked")
        gate = discovery_prefilter(token, self.config)
        if gate.passed:
            self._count(report, token.chain, "prefilter_pass")
            token, result = await self.inspect(token, report)
            self.repository.save_snapshot(token)
        else:
            self._count(report, token.chain, "prefilter_rejected")
            history = self.repository.history(token, self.config.history_observations * 3)
            result = score_token(token, history, Structure(), self.config)
        report.processed += 1
        report.signals.append((token, result))
        self._count(report, token.chain, "evaluated")
        if result.eligible:
            self._count(report, token.chain, "eligible")
        if report.scan_id is not None:
            self.repository.record_evaluation(
                report.scan_id, token, result, self.config, self.configuration
            )
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
        self._count(report, token.chain, "potential_alerts")
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
        alert_id = self.repository.reserve_alert(token, result, self.config, self.configuration)
        if alert_id is None:
            log.info("alert cooldown chain=%s symbol=%s", token.chain, token.symbol)
            return
        try:
            with self.metrics.measure("telegram"):
                delivery = await self.telegram.send_text(
                    format_alert(token, result, self.configuration), alert_buttons(token, alert_id)
                )
        except Exception:
            self.metrics.failures["telegram"] += 1
            raise
        self.repository.finish_alert(alert_id, delivery.status, delivery.message_id)
        if delivery.status == "sent":
            report.sent += 1
            self._count(report, token.chain, "alerted")
            log.info("telegram alert sent symbol=%s", token.symbol)
        else:
            report.errors += 1
            self.metrics.failures["telegram"] += 1

    async def scan_chain(self, chain: str, report: ScanReport) -> None:
        found = []
        report.funnel[chain] = dict.fromkeys(FUNNEL_STAGES, 0)
        report.discovered_by_source[chain] = {}
        report.source_errors[chain] = {}
        for source in ("hot_search", "trending"):
            try:
                with self.metrics.measure("discovery"):
                    discovered = await self.source.discover(chain, source)
                found.extend(discovered)
                report.discovered_by_source[chain][source] = len(discovered)
                report.sources_ok += 1
            except Exception as exc:
                report.errors += 1
                report.source_errors[chain][source] = 1
                self.metrics.failures["discovery"] += 1
                log.warning(
                    "discovery failed chain=%s source=%s error=%s",
                    chain,
                    source,
                    type(exc).__name__,
                )
        candidates = merge_tokens(found)
        keys = {t.key for t in candidates}
        report.discovered_by_chain[chain] = len(keys)
        report.funnel.setdefault(chain, {})["discovered"] = len(keys)
        report.discovered_keys.update(keys)
        now = time.time()
        watched = self.repository.watchlist(
            chain, now - self.config.watchlist_hours * 3600, self.config.watchlist_limit
        )
        for old in watched:
            if old.key not in keys:
                self._count(report, chain, "watchlist_added")
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
        self.metrics = ScanMetrics()
        if hasattr(self.source, "metrics"):
            self.source.metrics = self.metrics
        report = ScanReport(started_at=time.time())
        started = time.monotonic()
        metric_scope = CURRENT_SCAN.set(self.metrics)
        try:
            report.scan_id = self.repository.begin_scan(report.started_at, self.configuration)
            outcomes = await asyncio.gather(
                *(self.scan_chain(chain, report) for chain in self.config.chains),
                return_exceptions=True,
            )
            for chain, outcome in zip(self.config.chains, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    report.errors += 1
                    log.warning("chain failed chain=%s error=%s", chain, type(outcome).__name__)
            report.finished_at = time.time()
            self.repository.finish_scan(report.scan_id, report, report.finished_at)
            report.finished_at = time.time()
            report.duration_seconds = time.monotonic() - started
            report.performance = self.metrics.snapshot(report.duration_seconds)
        finally:
            CURRENT_SCAN.reset(metric_scope)
        self.repository.persist_scan_metrics(report.scan_id, report)
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
