import argparse
import asyncio
import fcntl
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic import ValidationError

from revival_radar.clients.gmgn import DataSourceError
from revival_radar.clients.providers import HybridDataSource
from revival_radar.clients.telegram import TelegramClient, format_alert
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.demo import DemoSource
from revival_radar.diagnostics import format_health
from revival_radar.logging_config import configure_logging
from revival_radar.models.token import TokenSnapshot
from revival_radar.runtime_settings import RuntimeSettings, SettingsChangeError
from revival_radar.scanner import Scanner
from revival_radar.service import run_service
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

log = logging.getLogger(__name__)


@contextmanager
def scanner_lock(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(database) + ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another scanner is using this database") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def execute(args: argparse.Namespace, config: Settings) -> int:
    runtime = None
    if args.command in {"run", "scan-once", "inspect", "settings"}:
        runtime = RuntimeSettings(config)
        config = runtime.effective()
    if args.command == "settings":
        print(json.dumps(runtime.public_values(), indent=2))
        return 0
    if args.command == "demo":
        config = config.model_copy(
            update={
                "dry_run": True,
                "enabled_chains": "sol",
                "database_path": args.database,
                "trending_discovery_enabled": True,
                "helius_enabled": False,
            }
        )
    if (
        args.command in {"run", "scan-once", "inspect"}
        and not config.gmgn_api_key.get_secret_value()
    ):
        raise DataSourceError("GMGN_API_KEY is required; use demo for an offline scan")
    if args.command == "test-telegram" and config.dry_run:
        raise DataSourceError("Set DRY_RUN=false to explicitly enable the Telegram test message")
    if (
        args.command in {"run", "scan-once", "test-telegram"}
        and not config.dry_run
        and (not config.telegram_bot_token.get_secret_value() or not config.telegram_chat_id)
    ):
        raise DataSourceError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for live delivery")
    async with httpx.AsyncClient() as http:
        if args.command == "setup-profile":
            from revival_radar.bot_profile import configure_profile

            result = await configure_profile(config, http, args.photo)
            print(json.dumps(result, indent=2))
            return 0 if result and all(v == "updated" for v in result.values()) else 1
        telegram = TelegramClient(config, http)
        if args.command == "test-telegram":
            delivery = await telegram.send_text("♻️ Revival Radar test — Telegram connected.")
            log.info("telegram test status=%s", delivery.status)
            return 0 if delivery.status == "sent" else 1
        db = connect(config.database_path, config.sqlite_busy_timeout_ms)
        try:
            repo = Repository(db)
            if args.command == "health":
                if not 0 < args.hours <= 168:
                    raise RuntimeError("Health window must be greater than 0 and at most 168 hours")
                health = repo.health(time.time() - args.hours * 3600)
                health["timezone"] = config.report_timezone
                print(json.dumps(health, indent=2) if args.json else format_health(health))
                return 0
            if args.command == "run":
                with scanner_lock(config.database_path):
                    await run_service(runtime, repo, http)
                return 0
            source = DemoSource() if args.command == "demo" else HybridDataSource(config, http)
            scanner = Scanner(
                config,
                source,
                repo,
                telegram,
                configuration=configuration_context(config, runtime.revision if runtime else 0),
            )
            if args.command == "demo":
                for snapshot in source.histories():
                    repo.save_snapshot(snapshot)
            if args.command == "inspect":
                seed = TokenSnapshot(chain=args.chain, contract_address=args.contract)
                # Find current discovery ATH cap if the token is listed; never invent it.
                for discovery in config.discovery_sources:
                    try:
                        matches = await source.discover(args.chain, discovery)
                        match = next((t for t in matches if t.key == seed.key), None)
                        if match:
                            seed = match
                            break
                    except DataSourceError:
                        log.warning("inspect discovery unavailable source=%s", discovery)
                token, result = await scanner.inspect(seed)
                print(
                    json.dumps(
                        {"token": token.model_dump(mode="json"), "signal": result.model_dump()},
                        indent=2,
                    )
                )
                return 0
            with scanner_lock(config.database_path):
                report = await scanner.scan_once()
                if args.command == "demo":
                    for token, result in report.signals:
                        if result.eligible:
                            print(format_alert(token, result, scanner.configuration))
                return 1 if report.errors else 0
        finally:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only GMGN revival radar")
    sub = parser.add_subparsers(dest="command")
    profile = sub.add_parser(
        "setup-profile",
        help="Explicitly update official Telegram name, descriptions, commands and optional photo",
    )
    profile.add_argument("--photo", type=Path)
    sub.add_parser("run", help="Scan continuously (default)")
    sub.add_parser("scan-once", help="One live scan; nonzero exit on partial failures")
    sub.add_parser("test-telegram", help="Send one test message when DRY_RUN=false")
    sub.add_parser(
        "settings", help="Print effective nonsecret settings, including Telegram overrides"
    )
    health = sub.add_parser("health", help="Report persisted scan diagnostics; no network required")
    health.add_argument("--hours", type=float, default=24)
    health.add_argument("--json", action="store_true")
    inspect = sub.add_parser("inspect", help="Fetch and explain one token without alerting")
    inspect.add_argument("chain", choices=["sol", "bsc", "base", "robinhood", "arc"])
    inspect.add_argument("contract")
    demo = sub.add_parser("demo", help="Offline fixture scan; always dry-run")
    demo.add_argument("--database", type=Path, default=Path("data/demo.db"))
    args = parser.parse_args()
    args.command = args.command or "run"
    try:
        config = Settings()
        configure_logging(config.log_level)
        raise SystemExit(asyncio.run(execute(args, config)))
    except KeyboardInterrupt:
        log.info("scanner stopped")
    except ValidationError as exc:
        # Pydantic's default str(exc) includes invalid inputs, possibly secrets.
        fields = [".".join(str(x) for x in e["loc"]) or "configuration" for e in exc.errors()]
        parser.exit(2, f"Invalid configuration/token fields: {', '.join(fields)}\n")
    except (DataSourceError, RuntimeError, SettingsChangeError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
