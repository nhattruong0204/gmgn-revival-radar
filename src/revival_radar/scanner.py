import asyncio
import logging
import time
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


class Scanner:
    def __init__(
        self,
        config: Settings,
        source: MarketDataSource,
        repository: Repository,
        telegram: TelegramClient,
    ):
        self.config, self.source = config, source
        self.repository, self.telegram = repository, telegram

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
        for source in ("hot_search", "trending"):
            try:
                found.extend(await self.source.discover(chain, source))
                report.sources_ok += 1
            except Exception as exc:
                report.errors += 1
                log.warning(
                    "discovery failed chain=%s source=%s error=%s",
                    chain,
                    source,
                    type(exc).__name__,
                )
        candidates = merge_tokens(found)
        keys = {t.key for t in candidates}
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
        report = ScanReport()
        outcomes = await asyncio.gather(
            *(self.scan_chain(chain, report) for chain in self.config.chains),
            return_exceptions=True,
        )
        for chain, outcome in zip(self.config.chains, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                report.errors += 1
                log.warning("chain failed chain=%s error=%s", chain, type(outcome).__name__)
        log.info(
            "scan complete processed=%d potential_alerts=%d sent=%d errors=%d sources_ok=%d",
            report.processed,
            report.potential_alerts,
            report.sent,
            report.errors,
            report.sources_ok,
        )
        return report
