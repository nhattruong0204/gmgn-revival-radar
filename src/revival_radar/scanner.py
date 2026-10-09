import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from revival_radar.analysis.acceleration import fresh_history
from revival_radar.analysis.filters import discovery_prefilter, first_pass
from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import SCORE_VERSION, score_token
from revival_radar.clients.gmgn import DataSourceError, MarketDataSource
from revival_radar.clients.telegram import TelegramClient, alert_buttons, format_alert
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.enrichment import Enrichment, EnrichmentDeferred
from revival_radar.metrics import CURRENT_SCAN, ScanMetrics
from revival_radar.models.signal import RevivalResult, Structure, has_returning_activity
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
    "baseline_ready",
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
    "outcome_polled",
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
        self.enrichment = Enrichment(config, source, repository, self.metrics)
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
        seed_warnings = list(token.data_warnings)
        try:
            with self.metrics.measure("market"):
                token = await self.enrichment.market(token)
                token.data_warnings = list(dict.fromkeys(seed_warnings + token.data_warnings))
        except EnrichmentDeferred:
            raise
        except Exception as exc:
            self.metrics.failures["market"] += 1
            self.metrics.error("market", exc, token)
            raise
        self._count(report, token.chain, "market_enriched")
        self.metrics.candidate(
            token,
            market_enriched=True,
            market_observed_at=token.timestamp,
            market_received_at=time.time(),
        )
        history = self.repository.history(token, self.config.history_observations * 3)
        self.repository.observe_outcomes(token)
        structure = Structure()
        result = score_token(token, history, structure, self.config)
        if not first_pass(token, self.config).passed:
            return token, result
        self._count(report, token.chain, "market_pass")
        self.metrics.candidate(token, market_pass=True)
        recent = fresh_history(token, history, self.config)
        if (
            sum(entry.volume_5m is not None for entry in recent)
            >= self.config.minimum_history_observations
        ):
            self._count(report, token.chain, "baseline_ready")
            self.metrics.candidate(token, baseline_ready=True)
        activity = has_returning_activity(result)
        if not activity:
            return token, result
        self._count(report, token.chain, "activity_trigger")
        self.metrics.candidate(token, activity_trigger=True)
        if token.security.dangerous is True:
            return token, result
        before_kline = self.enrichment.used["kline"]
        self.metrics.candidate(token, structure_expected=True)
        try:
            with self.metrics.measure("kline"):
                candles = await self.enrichment.candles(token)
                if candles and token.timestamp - max(c.timestamp for c in candles) > 7200:
                    token.data_warnings.append(
                        "Latest closed candle is stale; structure unavailable"
                    )
                else:
                    structure = analyze_structure(candles, self.config)
        except EnrichmentDeferred:
            self.repository.watch_defer(token)
            token.data_warnings.append("Candle request deferred by per-scan budget")
        except Exception as exc:
            self.metrics.failures["kline"] += 1
            self.metrics.error("kline", exc, token)
            token.data_warnings.append(f"Candles unavailable ({type(exc).__name__})")
        finally:
            if self.enrichment.used["kline"] > before_kline:
                self._count(report, token.chain, "kline_requested")
        if structure.base_detected:
            self._count(report, token.chain, "base_detected")
        self.metrics.candidate(
            token, structure_available=structure.available, base_detected=structure.base_detected
        )
        # Security can remove discovery-time penalties as well as add deductions.
        # Reuse the unchanged scorer to form an optimistic bound; don't reject a
        # serious candidate solely because a discovery-time concentration is stale.
        optimistic = score_token(
            token.model_copy(update={"security": Security()}), history, structure, self.config
        )
        if optimistic.eligible and optimistic.score >= self.config.alert_score_threshold:
            self._count(report, token.chain, "serious_candidates")
            self.metrics.candidate(token, security_expected=True)
            security = getattr(self.source, "enrich_security", None)
            if security is not None:
                before_security = self.enrichment.used["security"]
                try:
                    with self.metrics.measure("security"):
                        token = await self.enrichment.security(token)
                    self._count(report, token.chain, "security_enriched")
                    self.metrics.candidate(token, security_checked=True)
                except EnrichmentDeferred:
                    self.repository.watch_defer(token)
                    result = score_token(token, history, structure, self.config)
                    result.eligible = False
                    result.warnings.append("Security request deferred by per-scan budget; no alert")
                    return token, result
                except Exception as exc:
                    self.metrics.failures["security"] += 1
                    self.metrics.error("security", exc, token)
                    if not isinstance(exc, DataSourceError):
                        raise
                    # Preserve the existing nullable-security behavior and warnings.
                    token.data_warnings.append("Security enrichment unavailable")
                finally:
                    if self.enrichment.used["security"] > before_security:
                        self._count(report, token.chain, "security_requested")
        return token, score_token(token, history, structure, self.config)

    async def process(self, token: TokenSnapshot, report: ScanReport) -> None:
        state = self.repository.watch_measurement(token)
        self.metrics.candidate(
            token,
            timestamp=token.timestamp,
            discovery_sources=sorted(token.discovery_source),
            score_version=SCORE_VERSION,
            **state,
        )
        self._count(report, token.chain, "prefilter_checked")
        gate = discovery_prefilter(token, self.config)
        self.metrics.candidate(token, prefilter_pass=gate.passed)
        if gate.passed:
            self._count(report, token.chain, "prefilter_pass")
            try:
                token, result = await self.inspect(token, report)
            except EnrichmentDeferred:
                self.repository.watch_defer(token)
                return
            self.repository.save_snapshot(token)
        else:
            self._count(report, token.chain, "prefilter_rejected")
            history = self.repository.history(token, self.config.history_observations * 3)
            result = score_token(token, history, Structure(), self.config)
        if token.key not in self.enrichment.deferred_tokens:
            self.repository.watch_polled(token, result, self.config)
        report.processed += 1
        self.metrics.candidate(token, evaluated=True, eligible=result.eligible)
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
        self.metrics.candidate(token, potential_alert=True)
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
        except Exception as exc:
            self.metrics.failures["telegram"] += 1
            self.metrics.error("telegram", exc, token)
            raise
        self.repository.finish_alert(alert_id, delivery.status, delivery.message_id)
        if delivery.status == "sent":
            self.metrics.candidate(token, alerted=True)
            report.sent += 1
            self._count(report, token.chain, "alerted")
            log.info("telegram alert sent symbol=%s", token.symbol)
        else:
            report.errors += 1
            self.metrics.failures["telegram"] += 1
            self.metrics.error("telegram", RuntimeError("Delivery failed or unknown"), token)

    async def scan_chain(self, chain: str, report: ScanReport) -> list[TokenSnapshot]:
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
                self.metrics.error("discovery", exc)
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
        for token in candidates:
            self.repository.watch_discovered(token)
        watched = self.repository.watch_due(chain, now, self.config)
        for old in watched:
            old_token = TokenSnapshot.model_validate_json(old["payload"])
            if old_token.key not in keys:
                self._count(report, chain, "watchlist_added")
                candidates.append(
                    TokenSnapshot(
                        chain=chain,
                        contract_address=old_token.contract_address,
                        symbol=old_token.symbol,
                        name=old_token.name,
                        asset_type=old_token.asset_type,
                        asset_classification_reason=old_token.asset_classification_reason,
                        ath_market_cap=old_token.ath_market_cap,
                        data_warnings=["Off rankings; ATH cap from last discovery observation"]
                        + (
                            ["Current ranking coverage incomplete"]
                            if report.source_errors[chain]
                            else []
                        ),
                    )
                )
        return candidates

    def priority(self, token: TokenSnapshot) -> tuple:
        state = self.repository.watch_priority(token)
        sources = token.discovery_source
        # Explicit source tiers: Trending-only, Hot-only, both, then due watchlist tiers.
        if sources == {"trending"}:
            tier, rank = 0, token.trending_rank or 10**9
        elif sources == {"hot_search"}:
            tier, rank = 1, token.hot_search_rank or 10**9
        elif sources:
            tier, rank = 2, min(token.trending_rank or 10**9, token.hot_search_rank or 10**9)
        else:
            tier = 3 if state.get("tier") == "high" else 4 if state.get("tier") == "medium" else 5
            rank = -state.get("last_score", 0)
        return (
            tier,
            -state.get("deferred", 0),
            rank,
            state.get("last_polled", 0),
            -state.get("last_seen", 0),
            *token.key,
        )

    async def scan_once(self) -> ScanReport:
        self.metrics = ScanMetrics()
        if hasattr(self.source, "metrics"):
            self.source.metrics = self.metrics
        self.enrichment = Enrichment(self.config, self.source, self.repository, self.metrics)
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
                    self.metrics.error("chain", outcome)
                    report.errors += 1
                    log.warning("chain failed chain=%s error=%s", chain, type(outcome).__name__)
            candidates = [
                token for outcome in outcomes if isinstance(outcome, list) for token in outcome
            ]
            for token in sorted(candidates, key=self.priority):
                try:
                    await self.process(token, report)
                except Exception as exc:
                    report.errors += 1
                    self.repository.watch_defer(token)
                    if not any(
                        e["chain"] == token.chain
                        and e["contract_address"] == token.contract_address
                        for e in self.metrics.errors
                    ):
                        self.metrics.error("token", exc, token)
                    log.warning(
                        "token failed chain=%s address=%s error=%s",
                        token.chain,
                        token.contract_address,
                        type(exc).__name__,
                    )
            await self.track_outcomes(report)
            report.finished_at = time.time()
            self.repository.finish_scan(report.scan_id, report, report.finished_at)
            report.finished_at = time.time()
            report.duration_seconds = time.monotonic() - started
            report.performance = self.metrics.snapshot(report.duration_seconds)
        except BaseException as exc:
            self.metrics.error("scan", exc)
            raise
        finally:
            CURRENT_SCAN.reset(metric_scope)
            if report.scan_id is not None:
                report.duration_seconds = time.monotonic() - started
                report.performance = self.metrics.snapshot(report.duration_seconds)
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

    async def track_outcomes(self, report: ScanReport) -> None:
        remaining = self.config.max_market_enrich_per_scan - self.enrichment.used["market"]
        due = self.repository.due_outcome_tokens(time.time(), self.config.chains, remaining)
        for seed in due:
            try:
                with self.metrics.measure("market"):
                    token = await self.enrichment.market(seed)
                # A new poll is required; cached/discovery snapshots never count as checkpoints.
                self.repository.save_snapshot(token)
                self.repository.observe_outcomes(token)
                self._count(report, token.chain, "outcome_polled")
            except EnrichmentDeferred:
                break
            except Exception as exc:
                report.errors += 1
                self.metrics.failures["outcome_market"] += 1
                self.metrics.error("outcome_market", exc, seed)
                log.warning(
                    "outcome observation failed chain=%s error=%s", seed.chain, type(exc).__name__
                )
