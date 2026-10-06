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

from revival_radar.clients.gmgn import DataSourceError, GMGNClient
from revival_radar.clients.telegram import TelegramClient, format_alert
from revival_radar.config import Settings
from revival_radar.demo import DemoSource
from revival_radar.logging_config import configure_logging
from revival_radar.models.token import TokenSnapshot
from revival_radar.scanner import Scanner
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
    if args.command == "demo":
        config = config.model_copy(
            update={"dry_run": True, "enabled_chains": "sol", "database_path": args.database}
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
        telegram = TelegramClient(config, http)
        if args.command == "test-telegram":
            delivery = await telegram.send_text("♻️ Revival Radar test — Telegram connected.")
            log.info("telegram test status=%s", delivery.status)
            return 0 if delivery.status == "sent" else 1
        db = connect(config.database_path, config.sqlite_busy_timeout_ms)
        try:
            repo = Repository(db)
            source = DemoSource() if args.command == "demo" else GMGNClient(config, http)
            scanner = Scanner(config, source, repo, telegram)
            if args.command == "demo":
                for snapshot in source.histories():
                    repo.save_snapshot(snapshot)
            if args.command == "inspect":
                seed = TokenSnapshot(chain=args.chain, contract_address=args.contract)
                # Find current discovery ATH cap if the token is listed; never invent it.
                for discovery in ("hot_search", "trending"):
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
                while True:
                    started = time.monotonic()
                    report = await scanner.scan_once()
                    if args.command == "demo":
                        for token, result in report.signals:
                            if result.eligible:
                                print(format_alert(token, result))
                    if args.command != "run":
                        return 1 if report.errors else 0
                    await asyncio.sleep(
                        max(0, config.scan_interval_seconds - (time.monotonic() - started))
                    )
        finally:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only GMGN revival radar")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Scan continuously (default)")
    sub.add_parser("scan-once", help="One live scan; nonzero exit on partial failures")
    sub.add_parser("test-telegram", help="Send one test message when DRY_RUN=false")
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
    except (DataSourceError, RuntimeError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
