"""Continuous scanning and Telegram controls share a process, not a scan snapshot."""

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from revival_radar.clients.gmgn import GMGNClient
from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.diagnostics import format_health
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.scanner import Scanner
from revival_radar.storage.repository import Repository
from revival_radar.telegram_controls import TelegramControls

log = logging.getLogger(__name__)


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
    delivery = await telegram.send_text(format_health(report))
    repo.set_state(
        "daily_summary_delivery", json.dumps({"day": day_key, "status": delivery.status})
    )
    log.info("daily summary status=%s", delivery.status)
    return delivery.status


def _background_finished(task: asyncio.Task) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        # Exception text can contain an HTTP URL with the Telegram token.
        log.error(
            "background service stopped name=%s error=%s", task.get_name(), type(error).__name__
        )


async def run_service(runtime: RuntimeSettings, repo: Repository, http: httpx.AsyncClient) -> None:
    controls = TelegramControls(
        runtime, http, health_provider=lambda: repo.health(time.time() - 86400)
    )
    tasks: list[asyncio.Task] = []
    source = GMGNClient(runtime.effective(), http)

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
        for task in tasks:
            task.add_done_callback(_background_finished)
        while True:
            config = runtime.effective()
            runtime.active_revision = runtime.revision
            # One immutable configuration per scan; preserve the shared API cooldown gate.
            source.config = config
            telegram = TelegramClient(config, http)
            scanner = Scanner(config, source, repo, telegram, delivery_muted=muted)
            started = time.monotonic()
            log.info(
                "scan configuration revision=%d threshold=%d",
                runtime.revision,
                config.alert_score_threshold,
            )
            await scanner.scan_once()
            repo.prune_diagnostics(time.time() - 7 * 86400)
            await asyncio.sleep(max(0, config.scan_interval_seconds - (time.monotonic() - started)))
    finally:
        controls.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
