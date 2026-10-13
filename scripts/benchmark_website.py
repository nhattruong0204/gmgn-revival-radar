"""Matched offline scanner/public-read replay. No external HTTP or live credentials."""

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import socket
import sqlite3
import statistics
import subprocess
import sys
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

    wait_seconds = REPLAY_WAIT_SECONDS

    async def discover(self, chain, source):
        await asyncio.sleep(self.wait_seconds)
        return await super().discover(chain, source)

    async def enrich_market(self, token):
        await asyncio.sleep(self.wait_seconds)
        return token

    async def enrich_security(self, token):
        await asyncio.sleep(self.wait_seconds)
        return token

    async def candles(self, token):
        await asyncio.sleep(self.wait_seconds)
        return await super().candles(token)


def seed(path, source, config):
    db = connect(path)
    try:
        repo = Repository(db)
        now = time.time() - 60
        context = {"revision": 1, "preset": "synthetic replay"}
        for round_number in range(HISTORY_SCANS):
            timestamp = now - (HISTORY_SCANS - round_number) * 3600
            scan = repo.begin_scan(timestamp - 1, context, origin="synthetic")
            for index, scenario in enumerate(source.data):
                token = TokenSnapshot(**(scenario["token"] | {"timestamp": timestamp}))
                signal = RevivalResult(score=50 + index % 50, status="WATCH", eligible=False)
                repo.save_snapshot(token, origin="synthetic")
                repo.record_evaluation(scan, token, signal, config, context, origin="synthetic")
                if round_number == 0 and index % 5 == 0:
                    # Saved synthetic delivery receipts; never send a real message.
                    alert = repo.reserve_alert(
                        token,
                        RevivalResult(score=95, status="CONFIRMED_REVIVAL", eligible=True),
                        config.model_copy(update={"dry_run": False}),
                        context,
                        origin="synthetic",
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
            repo.save_snapshot(token, origin="synthetic")
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


def database_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def measure(
    tokens=100,
    initial_database: Path | None = None,
    separate_api_process=False,
    read_pause=0.05,
    replay_wait=REPLAY_WAIT_SECONDS,
    read_timeout_ms=1500,
    include_briefs=False,
):
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
        if initial_database is None:
            counts = seed(config.database_path, source, config)
        else:
            # Source remains read-only. Migrations and replay changes affect only
            # the private temporary copy, never the production database.
            with (
                sqlite3.connect(
                    initial_database.resolve().as_uri() + "?mode=ro", uri=True
                ) as original,
                sqlite3.connect(config.database_path) as target,
            ):
                original.backup(target)
            db = connect(config.database_path)
            try:
                counts = {
                    name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in (
                        "token_snapshots",
                        "evaluations",
                        "scan_runs",
                        "alerts",
                        "signal_outcomes",
                    )
                }
                # Replay only synthetic identities; keep production history for
                # realistic public queries without refreshing real watch tokens.
                db.execute("DELETE FROM watch_state")
                db.commit()
            finally:
                db.close()
        for name in ("baseline", "loaded"):
            with (
                sqlite3.connect(config.database_path) as original,
                sqlite3.connect(root / f"{name}.db") as target,
            ):
                original.backup(target)
        hashes = [database_digest(root / f"{name}.db") for name in ("baseline", "loaded")]
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
            replay.wait_seconds = replay_wait
            replay.data = copy.deepcopy(source.data)
            settings = config.model_copy(update={"database_path": root / f"{name}.db"})
            app = create_app(
                WebConfig(
                    web_allow_synthetic_data=True,
                    database_path=settings.database_path,
                    web_public_rate_limit=10000,
                    web_read_timeout_ms=read_timeout_ms,
                )
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
            if include_briefs:
                routes[5] = f"/tokens/sol/{address}/briefs"
            durations = []
            api = None
            transport = httpx.ASGITransport(app=app)
            origin = "http://benchmark"
            if browse and separate_api_process:
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                environment = dict(
                    os.environ,
                    DATABASE_PATH=str(settings.database_path),
                    WEB_ADMIN_ENABLED="false",
                    WEB_ALLOW_SYNTHETIC_DATA="true",
                    WEB_PUBLIC_RATE_LIMIT="10000",
                    WEB_READ_TIMEOUT_MS=str(read_timeout_ms),
                )
                api = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "revival_radar.web",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                origin = f"http://127.0.0.1:{port}"
                transport = None
                async with httpx.AsyncClient(trust_env=False) as ready:
                    for _ in range(100):
                        if api.poll() is not None:
                            raise RuntimeError("Isolated API process failed to start")
                        try:
                            if (await ready.get(origin + "/api/v1/meta")).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(read_pause)
                    else:
                        api.terminate()
                        api.wait(timeout=10)
                        raise RuntimeError("Isolated API readiness timed out")
            async with (
                httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as upstream,
                httpx.AsyncClient(transport=transport, base_url=origin, trust_env=False) as browser,
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
                    assert (
                        radar["total"] == tokens
                        if initial_database is None
                        else radar["total"] >= tokens
                    )
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    public.public_reader = original_reader
                    if api is not None:
                        api.terminate()
                        api.wait(timeout=10)
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
            "production_copy": initial_database is not None,
            "separate_api_process": separate_api_process,
            "replay_wait_per_provider_operation_seconds": replay_wait,
            "clients": CLIENTS,
            "read_pause_seconds": read_pause,
            "read_timeout_ms": read_timeout_ms,
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
                "Current scans use synthetic identities and mocked provider waits, even when "
                "retained history comes from a production copy. Separate-process mode uses "
                "loopback HTTP without SQL tracing; otherwise transport is in-process ASGI. "
                "Neither mode measures live provider quotas or Internet/TLS/proxy latency."
            ),
        }
        print(json.dumps(result, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=int, default=100, choices=range(3, 101))
    parser.add_argument(
        "--initial-database",
        type=Path,
        help="Read-only source copied into isolated replay databases",
    )
    parser.add_argument(
        "--separate-api-process",
        action="store_true",
        help="Match the deployment's separate API process using loopback HTTP",
    )
    parser.add_argument("--read-pause-seconds", type=float, default=0.05)
    parser.add_argument("--replay-wait-seconds", type=float, default=REPLAY_WAIT_SECONDS)
    parser.add_argument("--read-timeout-ms", type=int, default=1500)
    parser.add_argument(
        "--include-briefs",
        action="store_true",
        help="Replace the duplicate radar route with brief archive reads; same gates",
    )
    args = parser.parse_args()
    if not 0.05 <= args.read_pause_seconds <= 5:
        parser.error("read pause must be between 0.05 and 5 seconds")
    if not REPLAY_WAIT_SECONDS <= args.replay_wait_seconds <= 1.5:
        parser.error("replay wait must be between 0.005 and 1.5 seconds")
    if not 100 <= args.read_timeout_ms <= 10000:
        parser.error("read timeout must be between 100 and 10000ms")
    logging.disable(logging.INFO)
    raise SystemExit(
        asyncio.run(
            measure(
                args.tokens,
                args.initial_database,
                args.separate_api_process,
                args.read_pause_seconds,
                args.replay_wait_seconds,
                args.read_timeout_ms,
                args.include_briefs,
            )
        )
    )
