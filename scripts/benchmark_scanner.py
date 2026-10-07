"""Measure a single isolated Solana dry-run scan, or replay a captured public response tape.

Select the scanner version with PYTHONPATH before running this script. The same
script works with the pre-optimization commit. Live results measure real GMGN
requests; replay results measure local execution and never claim network latency.
No Telegram calls, live database changes, credentials, or request headers are saved.
"""

import argparse
import asyncio
import gzip
import json
import math
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from revival_radar.analysis.filters import first_pass
from revival_radar.clients.gmgn import DataSourceError, GMGNClient
from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

ROUTES = {
    "/v1/market/hot_searches": "discovery",
    "/v1/market/rank": "discovery",
    "/v1/token/info": "market",
    "/v1/token/security": "security",
    "/v1/market/token_kline": "kline",
}


def request_key(request: httpx.Request) -> str:
    params = dict(request.url.params)
    if request.method == "POST":
        params = json.loads(request.content)["params"][0]
    names = ("chain", "address", "interval", "limit", "resolution")
    values = {key: str(params[key]) for key in names if key in params}
    return json.dumps([request.method, request.url.path, values], sort_keys=True)


def sanitized(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, list):
        return [sanitized(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitized(item, secret)
            for key, item in value.items()
            if key.lower() not in {"api_key", "apikey", "authorization", "x-apikey"}
        }
    return value


async def run(args) -> int:
    base = Settings(_env_file=args.env_file)
    if not args.replay and not base.gmgn_api_key.get_secret_value():
        print("GMGN credentials are unavailable; no live requests made.")
        return 2
    if args.request_spacing_seconds is not None and (
        not math.isfinite(args.request_spacing_seconds)
        or args.request_spacing_seconds < base.request_spacing_seconds
    ):
        print("Live benchmark pacing must not be faster than the configured spacing.")
        return 2
    calls, response_counts, seconds = Counter(), Counter(), Counter()
    tape = (
        json.loads(
            gzip.decompress(args.replay.read_bytes())
            if args.replay.suffix == ".gz"
            else args.replay.read_bytes()
        )
        if args.replay
        else None
    )
    captured = {"captured_at": time.time(), "responses": {}}
    stopped = False
    secret = base.gmgn_api_key.get_secret_value()

    async def on_request(request):
        if stopped:
            raise DataSourceError("Benchmark stopped after auth/rate-limit response")
        if request.url.host != "openapi.gmgn.ai" or request.url.path not in ROUTES:
            raise RuntimeError("Benchmark only permits documented GMGN read endpoints")
        calls[request.url.path] += 1

    async def on_response(response):
        nonlocal stopped
        await response.aread()
        response_counts[str(response.status_code)] += 1
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code in {401, 403, 429} or (
            isinstance(payload, dict)
            and payload.get("code") in {401, "401", 403, "403", 429, "429"}
        ):
            stopped = True
        if response.is_success and isinstance(payload, dict) and payload.get("code") in (0, "0"):
            captured["responses"][request_key(response.request)] = sanitized(payload, secret)

    class MeasuredClient(GMGNClient):
        async def request(self, method, path, **kwargs):
            if stopped:
                raise DataSourceError("Benchmark stopped after auth/rate-limit response")
            started = time.monotonic()
            try:
                return await super().request(method, path, **kwargs)
            finally:
                seconds[ROUTES[path]] += time.monotonic() - started

    def replay(request):
        key = request_key(request)
        if key not in tape["responses"]:
            return httpx.Response(500, json={"code": 500})
        return httpx.Response(200, json=tape["responses"][key])

    with tempfile.TemporaryDirectory(prefix="radar-benchmark-") as directory:
        config = base.model_copy(
            update={
                "enabled_chains": "sol",
                "dry_run": True,
                "alerts_paused": False,
                "telegram_bot_token": SecretStr(""),
                "telegram_chat_id": "",
                "database_path": Path(directory) / "benchmark.db",
                "gmgn_api_key": SecretStr("replay-placeholder") if tape else base.gmgn_api_key,
                **(
                    {"request_spacing_seconds": 0.00001}
                    if tape
                    else {"request_spacing_seconds": args.request_spacing_seconds}
                    if args.request_spacing_seconds is not None
                    else {}
                ),
            }
        )
        db = connect(config.database_path)
        repo = Repository(db)
        depth = 0

        def timed(method):
            def wrapper(*method_args, **kwargs):
                nonlocal depth
                started, outer = time.monotonic(), depth == 0
                depth += 1
                try:
                    return method(*method_args, **kwargs)
                finally:
                    depth -= 1
                    if outer:
                        seconds["sqlite"] += time.monotonic() - started

            return wrapper

        for name in (
            "begin_scan",
            "history",
            "watchlist",
            "save_snapshot",
            "record_evaluation",
            "reserve_alert",
            "finish_alert",
            "finish_scan",
            "persist_scan_metrics",
        ):
            if hasattr(repo, name):
                setattr(repo, name, timed(getattr(repo, name)))
        try:
            transport = httpx.MockTransport(replay) if tape else None
            with (
                patch("time.time", return_value=tape["captured_at"])
                if tape
                else patch("time.time", wraps=time.time)
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    event_hooks={"request": [on_request], "response": [on_response]},
                ) as http:
                    source = MeasuredClient(config, http)
                    started = time.monotonic()
                    report = await Scanner(
                        config, source, repo, TelegramClient(config, http)
                    ).scan_once()
                    elapsed = time.monotonic() - started
            signals = report.signals
            candidates = {
                "discovered": len(report.discovered_keys),
                "evaluated": report.processed,
                "market_filter_pass": sum(first_pass(token, config).passed for token, _ in signals),
                "activity_trigger": sum(
                    any(
                        name in result.components
                        for name in ("volume_5m", "volume_1h", "transactions")
                    )
                    for _, result in signals
                ),
                "base_detected": sum(result.structure.base_detected for _, result in signals),
                "eligible": sum(result.eligible for _, result in signals),
                "potential_alerts": report.potential_alerts,
                "sent": report.sent,
                "errors": report.errors,
            }
            result = {
                "label": args.label,
                "mode": "captured response replay; no network latency"
                if tape
                else "live GMGN Solana dry run",
                "measured_at_utc": datetime.now(UTC).isoformat(),
                "duration_seconds": round(elapsed, 6),
                "endpoint_attempts": {route: calls[route] for route in ROUTES},
                "http_status_counts": dict(response_counts),
                "adapter_request_seconds": {
                    stage: round(seconds[stage], 6)
                    for stage in ("discovery", "market", "security", "kline", "sqlite", "telegram")
                },
                "candidates": candidates,
                "discovered_keys": sorted(report.discovered_keys),
                "configuration": configuration_context(config),
                "instrumentation": getattr(report, "performance", {}),
                "funnel": getattr(report, "funnel", {}),
                "stopped_for_auth_or_rate_limit": stopped,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
            if args.record:
                args.record.parent.mkdir(parents=True, exist_ok=True)
                args.record.write_text(json.dumps(captured, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in result.items()
                        if key not in ("configuration", "discovered_keys")
                    }
                ),
                flush=True,
            )
            return 1 if report.errors or stopped else 0
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--request-spacing-seconds", type=float)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from revival_radar.logging_config import configure_logging

    configure_logging("WARNING")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
