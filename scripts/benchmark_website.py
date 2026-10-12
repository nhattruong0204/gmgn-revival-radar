"""Offline scanner/public-read comparison; no credentials or provider network calls."""

import asyncio
import json
import logging
import statistics
import tempfile
import time
from pathlib import Path

import httpx
from seed_website_demo import current_demo_source

from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig


async def measure():
    # Predeclared gate: no scan/read failures; median scan overhead <= max(25%, 100ms).
    # The absolute allowance avoids interpreting noise in millisecond fixture scans.
    with tempfile.TemporaryDirectory(prefix="radar-web-load-") as directory:
        path = Path(directory) / "radar.db"
        config = Settings(
            _env_file=None,
            database_path=path,
            dry_run=True,
            enabled_chains="sol",
            trending_discovery_enabled=True,
        )
        source = current_demo_source()
        db = connect(path)
        repo = Repository(db)
        for snapshot in source.histories():
            repo.save_snapshot(snapshot)
        network_attempts = 0

        def forbidden(request):
            nonlocal network_attempts
            network_attempts += 1
            raise AssertionError("Benchmark must not make external requests")

        app = create_app(WebConfig(database_path=path, web_public_rate_limit=10000))
        routes = ["/radar", "/reports?hours=24", "/outcomes", "/alerts", "/meta"]
        reads = []
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as upstream,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://benchmark"
            ) as browser,
        ):

            async def scan():
                started = time.perf_counter()
                result = await Scanner(
                    config, source, repo, TelegramClient(config, upstream)
                ).scan_once()
                assert result.processed == 3 and result.errors == 0 and result.sent == 0
                return time.perf_counter() - started

            # Populate completed evidence before browser readers start.
            await scan()
            populated = (await browser.get("/api/v1/radar")).json()
            assert populated["total"] > 0, "Benchmark must browse real saved evaluations"
            baseline = [await scan() for _ in range(12)]

            async def browse():
                for _ in range(12):
                    responses = await asyncio.gather(
                        *[browser.get("/api/v1" + route) for route in routes for _ in range(6)]
                    )
                    reads.extend(response.status_code for response in responses)

            browsing = asyncio.create_task(browse())
            loaded = []
            for _ in range(12):
                await asyncio.sleep(0.005)
                loaded.append(await scan())
            await browsing
        db.close()
        base, load = statistics.median(baseline), statistics.median(loaded)
        allowance = max(base * 0.25, 0.1)
        result = {
            "workload": "three-token synthetic scanner; 12 scans per arm; 6 browser clients",
            "public_requests": len(reads),
            "public_failures": sum(code != 200 for code in reads),
            "external_requests": network_attempts,
            "baseline_median_seconds": base,
            "loaded_median_seconds": load,
            "allowed_overhead_seconds": allowance,
            "passed": load <= base + allowance
            and all(code == 200 for code in reads)
            and network_attempts == 0,
            "limitations": (
                "Small synthetic data, ASGI transport, no production quota/network latency. "
                "Repeat on a copied production database before launch."
            ),
        }
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    logging.disable(logging.INFO)
    raise SystemExit(asyncio.run(measure()))
