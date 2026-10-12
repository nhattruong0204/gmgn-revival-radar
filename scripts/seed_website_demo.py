"""Create a new, explicitly synthetic web database with current observation times."""

import argparse
import asyncio
import time
from pathlib import Path

import httpx

from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.demo import DemoSource
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository


def current_demo_source():
    source = DemoSource()
    offset = time.time() - 5 - source.data[0]["token"]["timestamp"]

    def shift(value):
        if isinstance(value, dict):
            return {
                key: item + offset if key == "timestamp" else shift(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [shift(item) for item in value]
        return value

    source.data = shift(source.data)
    return source


async def seed(path: Path):
    if path.exists():
        raise SystemExit("Choose a new demo database path; existing data will not be overwritten.")
    source = current_demo_source()
    config = Settings(
        _env_file=None,
        database_path=path,
        enabled_chains="sol",
        dry_run=True,
        trending_discovery_enabled=True,
        telegram_bot_token="",
        telegram_chat_id="",
    )
    db = connect(path)
    try:
        repo = Repository(db)
        for token in source.histories():
            repo.save_snapshot(token)

        def forbidden(request):
            raise AssertionError("Synthetic website seeding must never use the network")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            result = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
        if result.errors or result.sent or result.processed != 3:
            raise SystemExit("Synthetic website seeding failed validation.")
        print("Synthetic website database created. No real market data or sent alerts.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    raise SystemExit(asyncio.run(seed(parser.parse_args().database)))
