"""Continuous scanning and Telegram controls share a process, not a scan snapshot."""

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from revival_radar.clients.providers import HybridDataSource
from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.market_report import market_report_buttons, market_report_page
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.scanner import Scanner
from revival_radar.storage.repository import Repository
from revival_radar.telegram_controls import TelegramControls
from revival_radar.telegram_views import health_page

log = logging.getLogger(__name__)
MARKET_REPORT_POLL_SECONDS = 30


async def daily_summary(
    repo: Repository, config: Settings, telegram: TelegramClient, now: float | None = None
) -> str | None:
    if not config.daily_summary_enabled or config.dry_run or config.alerts_paused:
        return None
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, ZoneInfo(config.report_timezone))
    if local.hour < config.daily_summary_hour:
        return None
    # Claim before sending. A crash or uncertain send must not duplicate a daily message.
    day_key = f"{config.report_timezone}:{local.date().isoformat()}"
    if not repo.claim_daily_summary(day_key, now):
        return None
    report = repo.health(now - 86400, now=now)
    report["timezone"] = config.report_timezone
    delivery = await telegram.send_text(health_page(report))
    repo.set_state(
        "daily_summary_delivery", json.dumps({"day": day_key, "status": delivery.status})
    )
    log.info("daily summary status=%s", delivery.status)
    return delivery.status


def market_report_period(now: float, timezone: str) -> tuple[str, float, float]:
    """Latest local 00/04/08/12/16/20 boundary, with an actual four-hour window."""
    zone = ZoneInfo(timezone)
    local = datetime.fromtimestamp(now, zone)
    boundaries = set()
    # Validate wall-clock times by round trip: timezone transitions can skip a
    # boundary. Choose the first occurrence of repeated wall-clock boundaries.
    for days_ago in (0, 1):
        day = local.date() - timedelta(days=days_ago)
        for hour in range(0, 24, 4):
            occurrences = set()
            for fold in (0, 1):
                boundary = datetime(day.year, day.month, day.day, hour, tzinfo=zone, fold=fold)
                timestamp = boundary.timestamp()
                actual = datetime.fromtimestamp(timestamp, zone)
                if actual.replace(tzinfo=None) == boundary.replace(tzinfo=None):
                    occurrences.add(timestamp)
            if occurrences and (first := min(occurrences)) <= now:
                boundaries.add(first)
    until = max(boundaries)
    return f"{timezone}:{int(until)}", until - 4 * 3600, until


async def four_hour_market_report(
    repo: Repository, config: Settings, telegram: TelegramClient, now: float | None = None
) -> str | None:
    """Send the latest due period once, independently of revival alert settings."""
    if (
        not config.market_report_enabled
        or config.dry_run
        or not config.telegram_bot_token.get_secret_value()
        or not config.telegram_chat_id.strip()
    ):
        return None
    now = time.time() if now is None else now
    period_key, since, until = market_report_period(now, config.report_timezone)
    if not repo.claim_market_report(period_key, now):
        return None
    send_started = False
    try:
        # Materialize and persist the snapshot before the first await. Neither
        # later observations nor a restart can change this period's message.
        report = repo.market_report(since, now=until, timezone=config.report_timezone)
        report["generated_at"] = now
        repo.set_state(
            f"market_report:snapshot:{period_key}", json.dumps(report, allow_nan=False), now
        )
        message, buttons = market_report_page(report), market_report_buttons(report)
        send_started = True
        delivery = await telegram.send_text(message, buttons)
        status = delivery.status if delivery.status in {"sent", "failed", "unknown"} else "unknown"
        repo.finish_market_report(period_key, status, delivery.message_id, timestamp=now)
        log.info("market report period=%s status=%s", period_key, status)
        return status
    except asyncio.CancelledError:
        with suppress(Exception):
            repo.finish_market_report(
                period_key, "unknown" if send_started else "failed", timestamp=now
            )
        raise
    except Exception as exc:
        status = "unknown" if send_started else "failed"
        repo.finish_market_report(period_key, status, timestamp=now)
        # Exception messages may contain private Telegram URLs or upstream data.
        log.error(
            "market report period=%s status=%s error=%s", period_key, status, type(exc).__name__
        )
        return status


async def market_reports_loop(
    runtime: RuntimeSettings, repo: Repository, http: httpx.AsyncClient
) -> None:
    while True:
        try:
            config = runtime.effective()
            await four_hour_market_report(repo, config, TelegramClient(config, http))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A failed query, claim, or delivery-state write must not stop future periods.
            log.error("market report background error=%s", type(exc).__name__)
        await asyncio.sleep(MARKET_REPORT_POLL_SECONDS)


def _background_finished(task: asyncio.Task) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        # Exception text can contain an HTTP URL with the Telegram token.
        log.error(
            "background service stopped name=%s error=%s", task.get_name(), type(error).__name__
        )


async def run_service(runtime: RuntimeSettings, repo: Repository, http: httpx.AsyncClient) -> None:
    scan_requested = asyncio.Event()
    schedule = {"running": False, "last_finished": None, "next_due": None}

    def request_scan() -> str:
        if schedule["running"]:
            return "A scan is already running. Wait until it finishes."
        if scan_requested.is_set():
            return "A scan is already queued."
        scan_requested.set()
        return "Scan requested. Existing API rate limits still apply."

    def market_report_provider(hours: int) -> dict:
        now = time.time()
        return repo.market_report(
            now - hours * 3600, now=now, timezone=runtime.effective().report_timezone
        )

    controls = TelegramControls(
        runtime,
        http,
        health_provider=lambda: repo.health(time.time() - 86400) | {"schedule": dict(schedule)},
        detail_provider=repo.presentation_detail,
        scan_request=request_scan,
        schedule_provider=lambda: dict(schedule),
        market_report_provider=market_report_provider,
    )
    tasks: list[asyncio.Task] = []
    source = HybridDataSource(runtime.effective(), http)

    async def summaries() -> None:
        while True:
            config = runtime.effective()
            await daily_summary(repo, config, TelegramClient(config, http))
            await asyncio.sleep(30)

    def muted() -> bool:
        current = runtime.effective()
        return current.dry_run or current.alerts_paused

    try:
        if controls.enabled:
            tasks.append(asyncio.create_task(controls.run(), name="telegram-controls"))
        else:
            log.info(
                "telegram controls unavailable: configure bot token and private owner identity"
            )
        tasks.append(asyncio.create_task(summaries(), name="daily-summary"))
        tasks.append(
            asyncio.create_task(market_reports_loop(runtime, repo, http), name="market-reports")
        )
        for task in tasks:
            task.add_done_callback(_background_finished)
        while True:
            config = runtime.effective()
            runtime.active_revision = runtime.revision
            # One immutable configuration per scan; preserve the shared API cooldown gate.
            source.config = config
            telegram = TelegramClient(config, http)
            scanner = Scanner(
                config,
                source,
                repo,
                telegram,
                delivery_muted=muted,
                configuration=configuration_context(config, runtime.revision),
            )
            started = time.monotonic()
            log.info(
                "scan configuration revision=%d threshold=%d",
                runtime.revision,
                config.alert_score_threshold,
            )
            scan_requested.clear()
            schedule["running"] = True
            schedule["next_due"] = None
            try:
                # Await completion even on cadence overruns: no timer spawns another scan.
                await scanner.scan_once()
            finally:
                schedule["running"] = False
                schedule["last_finished"] = time.time()
            repo.prune_diagnostics(time.time() - 7 * 86400)
            delay = max(0, config.scan_interval_seconds - (time.monotonic() - started))
            schedule["next_due"] = time.time() + delay
            with suppress(TimeoutError):
                await asyncio.wait_for(scan_requested.wait(), timeout=delay)
    finally:
        controls.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
