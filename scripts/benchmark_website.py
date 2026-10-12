"""Matched offline scanner/public-read replay. No external HTTP or live credentials."""

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import sqlite3
import statistics
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import httpx
from seed_website_demo import current_demo_source

from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.demo import DemoSource
from revival_radar.models.signal import RevivalResult
from revival_radar.models.token import TokenSnapshot
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web import public
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig

# Declare before measurement; never adapt gates to an observed result.
SCANS = 8
CLIENTS = 6
READS_PER_CLIENT = 112
HISTORY_SCANS = 24
REPLAY_WAIT_SECONDS = 0.005
GATE = {"relative_scan_overhead": 0.25, "absolute_scan_overhead_seconds": 0.1}


def expanded_source(tokens: int) -> DemoSource:
    original = current_demo_source()
    source = DemoSource()
    source.data = []
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    for index in range(tokens):
        number, suffix = index + 1, ""
        while number:
            number, digit = divmod(number, 58)
            suffix = alphabet[digit] + suffix
        address = suffix.rjust(32, "A")
        scenario = copy.deepcopy(original.data[index % len(original.data)])
        for token in [scenario["token"], *scenario["history"]]:
            token["contract_address"] = address
        source.data.append(scenario)
    return source


class ReplaySource(DemoSource):
    """Identical saved responses with declared cooperative provider wait latency."""

    async def discover(self, chain, source):
        await asyncio.sleep(REPLAY_WAIT_SECONDS)
        return await super().discover(chain, source)

    async def enrich_market(self, token):
        await asyncio.sleep(REPLAY_WAIT_SECONDS)
        return token

    async def enrich_security(self, token):
        await asyncio.sleep(REPLAY_WAIT_SECONDS)
        return token

    async def candles(self, token):
        await asyncio.sleep(REPLAY_WAIT_SECONDS)
        return await super().candles(token)


def seed(path, source, config):
    db = connect(path)
    try:
        repo = Repository(db)
        now = time.time() - 60
        context = {"revision": 1, "preset": "synthetic replay"}
        for round_number in range(HISTORY_SCANS):
            timestamp = now - (HISTORY_SCANS - round_number) * 3600
            scan = repo.begin_scan(timestamp - 1, context)
            for index, scenario in enumerate(source.data):
                token = TokenSnapshot(**(scenario["token"] | {"timestamp": timestamp}))
                signal = RevivalResult(score=50 + index % 50, status="WATCH", eligible=False)
                repo.save_snapshot(token)
                repo.record_evaluation(scan, token, signal, config, context)
                if round_number == 0 and index % 5 == 0:
                    # Saved synthetic delivery receipts; never send a real message.
                    alert = repo.reserve_alert(
                        token,
                        RevivalResult(score=95, status="CONFIRMED_REVIVAL", eligible=True),
                        config.model_copy(update={"dry_run": False}),
                        context,
                    )
                    assert alert is not None
                    repo.finish_alert(alert, "sent")
                    if index % 10 == 0:
                        repo.observe_outcomes(
                            token.model_copy(
                                update={
                                    "timestamp": timestamp + 3600,
                                    "price": token.price * 1.1 if token.price else None,
                                }
                            )
                        )
            repo.finish_scan(scan, SimpleNamespace(processed=len(source.data)), timestamp + 1)
        for token in source.histories():
            repo.save_snapshot(token)
        return {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "token_snapshots",
                "evaluations",
                "scan_runs",
                "state",
                "alerts",
                "signal_outcomes",
            )
        }
    finally:
        db.close()


def percentile(values, fraction):
    return sorted(values)[max(0, int(len(values) * fraction + 0.999) - 1)]


async def measure(tokens=100):
    with tempfile.TemporaryDirectory(prefix="radar-web-load-") as directory:
        root = Path(directory)
        source = expanded_source(tokens)
        config = Settings(
            _env_file=None,
            database_path=root / "seed.db",
            dry_run=True,
            enabled_chains="sol",
            trending_discovery_enabled=True,
            telegram_bot_token="",
            telegram_chat_id="",
            gmgn_api_key="",
            discovery_limit=tokens,
            watchlist_limit=tokens,
        )
        counts = seed(config.database_path, source, config)
        for name in ("baseline", "loaded"):
            with (
                sqlite3.connect(config.database_path) as original,
                sqlite3.connect(root / f"{name}.db") as target,
            ):
                original.backup(target)
        hashes = [
            hashlib.sha256((root / f"{name}.db").read_bytes()).hexdigest()
            for name in ("baseline", "loaded")
        ]
        assert hashes[0] == hashes[1], "Both arms must start with identical saved data"
        attempts, scan_errors, scans_during_reads = 0, 0, 0
        latencies, statuses, statements, sqlite_errors = {}, Counter(), Counter(), Counter()
        lock = Lock()
        original_reader = public.public_reader

        @contextmanager
        def instrumented_reader(web_config):
            try:
                with original_reader(web_config) as queries:

                    def trace(statement):
                        with lock:
                            statements[statement.split()[0].upper()] += 1

                    queries.db.set_trace_callback(trace)
                    yield queries
            except sqlite3.Error as exc:
                with lock:
                    sqlite_errors[type(exc).__name__] += 1
                raise

        def forbidden(request):
            nonlocal attempts
            attempts += 1
            raise AssertionError("Benchmark must never use external HTTP")

        async def arm(name, browse):
            nonlocal scan_errors, scans_during_reads
            db = connect(root / f"{name}.db")
            repo = Repository(db)
            replay = ReplaySource()
            replay.data = copy.deepcopy(source.data)
            settings = config.model_copy(update={"database_path": root / f"{name}.db"})
            app = create_app(
                WebConfig(database_path=settings.database_path, web_public_rate_limit=10000)
            )
            address = source.data[0]["token"]["contract_address"]
            routes = [
                "/radar",
                "/reports?hours=24",
                "/outcomes",
                "/alerts",
                "/meta",
                "/radar?sort=market_cap&search=",
                f"/tokens/sol/{address}/history",
            ]
            durations = []
            async with (
                httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as upstream,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://benchmark"
                ) as browser,
            ):

                async def read_client(index):
                    for iteration in range(READS_PER_CLIENT):
                        route = routes[(index + iteration) % len(routes)]
                        started = time.perf_counter()
                        response = await browser.get("/api/v1" + route)
                        latencies.setdefault(route, []).append(time.perf_counter() - started)
                        statuses[response.status_code] += 1
                        await asyncio.sleep(0.05)

                task = None
                if browse:
                    public.public_reader = instrumented_reader
                    task = asyncio.gather(*(read_client(i) for i in range(CLIENTS)))
                try:
                    for _ in range(SCANS):
                        started = time.perf_counter()
                        report = await Scanner(
                            settings, replay, repo, TelegramClient(settings, upstream)
                        ).scan_once()
                        durations.append(time.perf_counter() - started)
                        scan_errors += report.errors
                        assert (
                            report.processed == tokens and report.errors == 0 and report.sent == 0
                        )
                        if task is not None and not task.done():
                            scans_during_reads += 1
                    if task is not None:
                        await task
                    radar = (
                        await browser.get("/api/v1/radar?page_size=1&include_ignore=true")
                    ).json()
                    assert radar["total"] == tokens
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    public.public_reader = original_reader
                    db.close()
            return durations

        baseline = await arm("baseline", False)
        loaded = await arm("loaded", True)
        base, load = statistics.median(baseline), statistics.median(loaded)
        allowance = max(
            base * GATE["relative_scan_overhead"], GATE["absolute_scan_overhead_seconds"]
        )
        passed = (
            load <= base + allowance
            and scan_errors == 0
            and attempts == 0
            and statuses == {200: CLIENTS * READS_PER_CLIENT}
            and not sqlite_errors
            and scans_during_reads > 0
        )
        result = {
            "gate": GATE,
            "tokens_per_scan": tokens,
            "scans_per_arm": SCANS,
            "identical_initial_database": hashes[0] == hashes[1],
            "initial_rows": counts,
            "replay_wait_per_provider_operation_seconds": REPLAY_WAIT_SECONDS,
            "clients": CLIENTS,
            "public_requests": sum(statuses.values()),
            "http_statuses": dict(statuses),
            "external_requests": attempts,
            "scan_errors": scan_errors,
            "scans_completed_while_reading": scans_during_reads,
            "baseline_scan_seconds": baseline,
            "loaded_scan_seconds": loaded,
            "baseline_median_seconds": base,
            "loaded_median_seconds": load,
            "allowed_overhead_seconds": allowance,
            "api_latency_seconds": {
                route: {
                    "count": len(values),
                    "median": statistics.median(values),
                    "p95": percentile(values, 0.95),
                    "max": max(values),
                }
                for route, values in latencies.items()
            },
            "read_sql_statements": dict(statements),
            "read_sqlite_errors": dict(sqlite_errors),
            "passed": passed,
            "limitations": (
                "Synthetic bounded corpus and ASGI transport; 5ms replay waits are not live "
                "provider latency. Production-size database, proxy/network and VPS resources "
                "still require launch validation."
            ),
        }
        print(json.dumps(result, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=int, default=100, choices=range(3, 101))
    args = parser.parse_args()
    logging.disable(logging.INFO)
    raise SystemExit(asyncio.run(measure(args.tokens)))
