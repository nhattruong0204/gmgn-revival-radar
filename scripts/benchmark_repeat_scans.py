"""Measure repeated Solana scans against public recorded HTTP responses, without network.

Synthetic prior volume/transaction observations activate candle/security paths.
These are measured offline request counts and processing times, not live latency.
Select the baseline or current scanner using an absolute PYTHONPATH.
"""

import argparse
import asyncio
import gzip
import json
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import httpx
from benchmark_scanner import ROUTES, request_key

from revival_radar.analysis.filters import discovery_prefilter, first_pass
from revival_radar.clients.gmgn import GMGNClient
from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.scanner import Scanner, merge_tokens
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository


async def run(args):
    tape = json.loads(gzip.decompress(args.tape.read_bytes()))
    now = tape["captured_at"]
    calls = Counter()

    def handler(request):
        if request.url.host != "openapi.gmgn.ai" or request.url.path not in ROUTES:
            raise AssertionError("Unexpected benchmark endpoint")
        calls[request.url.path] += 1
        key = request_key(request)
        if key not in tape["responses"]:
            raise AssertionError("Missing recorded response; benchmark incomplete")
        return httpx.Response(200, json=tape["responses"][key])

    with tempfile.TemporaryDirectory(prefix="radar-repeat-benchmark-") as directory:
        config = Settings(
            _env_file=None,
            enabled_chains="sol",
            dry_run=True,
            gmgn_api_key="offline-replay",
            telegram_bot_token="",
            telegram_chat_id="",
            database_path=Path(directory) / "benchmark.db",
            request_spacing_seconds=0.00001,
        )
        db = connect(config.database_path)
        repo = Repository(db)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                # Build only historical observations from real market values. No cache priming.
                source = GMGNClient(config, http)
                with patch("time.time", return_value=now):
                    discovered = merge_tokens(
                        await source.discover("sol", "hot_search")
                        + await source.discover("sol", "trending")
                    )
                    seeds = []
                    for seed in discovered:
                        if discovery_prefilter(seed, config).passed:
                            market = await source.enrich_market(seed)
                            if first_pass(market, config).passed:
                                seeds.append(market)
                for seed in seeds:
                    for offset in (1200, 900, 600, 300):
                        old = seed.model_copy(
                            deep=True,
                            update={
                                "timestamp": now - offset,
                                "volume_5m": (seed.volume_5m or 0) / 6,
                                "volume_1h": (seed.volume_1h or 0) / 6,
                                "tx_5m": int((seed.tx_5m or 0) / 4),
                                "tx_1h": int((seed.tx_1h or 0) / 4),
                                "discovery_source": set(),
                            },
                        )
                        repo.save_snapshot(old)
                results = []
                source = GMGNClient(config, http)
                for index in range(2):
                    calls.clear()
                    with patch("time.time", return_value=now + index * 300):
                        start = time.monotonic()
                        report = await Scanner(
                            config, source, repo, TelegramClient(config, http)
                        ).scan_once()
                        duration = time.monotonic() - start
                    results.append(
                        {
                            "scan": index + 1,
                            "duration_seconds": round(duration, 6),
                            "endpoint_attempts": {route: calls[route] for route in ROUTES},
                            "discovered": len(report.discovered_keys),
                            "evaluated": report.processed,
                            "potential_alerts": report.potential_alerts,
                            "errors": report.errors,
                            "performance": report.performance,
                            "funnel": report.funnel,
                        }
                    )
                result = {
                    "label": args.label,
                    "mode": (
                        "offline Solana public-response replay; "
                        "synthetic history; no network latency"
                    ),
                    "baseline_commit": "e3a6ff8c99458d7a243ffe04c7da494854b465c7",
                    "chains": config.chains,
                    "discovery_limit": config.discovery_limit,
                    "watchlist_limit": config.watchlist_limit,
                    "seeded_tokens": len(seeds),
                    "history_method": (
                        "four prior observations: volume / 6, transactions / 4; 300s apart"
                    ),
                    "scan_spacing_seconds": 300,
                    "scans": results,
                }
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps({k: v for k, v in result.items() if k != "scans"}))
                for row in results:
                    print(
                        json.dumps(
                            {k: v for k, v in row.items() if k not in {"performance", "funnel"}}
                        )
                    )
                return int(any(row["errors"] for row in results))
        finally:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tape", type=Path, default=Path("docs/benchmarks/issue2-solana-responses.json.gz")
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
