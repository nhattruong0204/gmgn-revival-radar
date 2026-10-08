"""Per-scan operation times and actual HTTP attempts; no URLs, payloads or credentials."""

import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

STAGES = ("discovery", "market", "security", "kline", "sqlite", "telegram")
ENDPOINTS = (
    "/v1/market/hot_searches",
    "/v1/market/rank",
    "/v1/token/info",
    "/v1/token/security",
    "/v1/market/token_kline",
)


def error_category(error: BaseException, stage: str = "") -> str:
    """Classify locally; only a fixed label is retained, never exception text."""
    import sqlite3

    import httpx
    from pydantic import ValidationError

    if isinstance(error, sqlite3.Error):
        return "sqlite"
    if isinstance(error, httpx.TimeoutException):
        return "gmgn_timeout"
    if isinstance(error, httpx.TransportError):
        return "gmgn_transport"
    if isinstance(error, ValidationError):
        return "token_normalization"
    if (
        isinstance(error, (KeyboardInterrupt, SystemExit))
        or type(error).__name__ == "CancelledError"
    ):
        return "interrupted_scan"
    message = str(error).lower()
    if "token normalization" in message:
        return "token_normalization"
    if "rate limited" in message or "http=429" in message:
        return "gmgn_429"
    if "cooldown" in message:
        return "gmgn_cooldown"
    if "timeout" in message:
        return "gmgn_timeout"
    if any(f"http={code}" in message for code in (500, 502, 503, 504)):
        return "gmgn_5xx"
    if "response shape" in message or "token list" in message or "address mismatch" in message:
        return "malformed_response"
    return stage if stage in {"telegram", "security", "kline", "configuration"} else "other"


@dataclass
class ScanMetrics:
    seconds: Counter = field(default_factory=Counter)
    calls: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    cache: Counter = field(default_factory=Counter)
    deferred: Counter = field(default_factory=Counter)
    http_errors: Counter = field(default_factory=Counter)
    http_statuses: Counter = field(default_factory=Counter)
    recovered_requests: Counter = field(default_factory=Counter)
    errors: list[dict] = field(default_factory=list)
    candidates: dict = field(default_factory=dict)

    def error(self, stage: str, error: BaseException, token=None) -> None:
        self.errors.append(
            {
                "timestamp": time.time(),
                "stage": stage,
                "category": error_category(error, stage),
                "chain": token.chain if token else None,
                "contract_address": token.contract_address if token else None,
            }
        )

    def candidate(self, token, **values) -> dict:
        trace = self.candidates.setdefault(
            token.key,
            {"chain": token.chain, "contract_address": token.contract_address},
        )
        trace.update(values)
        return trace

    @contextmanager
    def measure(self, stage: str):
        started = time.monotonic()
        try:
            yield
        finally:
            self.seconds[stage] += time.monotonic() - started

    def snapshot(self, total: float) -> dict:
        return {
            "seconds": {
                "total": round(total, 6),
                **{stage: round(self.seconds[stage], 6) for stage in STAGES},
            },
            "calls": {route: self.calls[route] for route in ENDPOINTS},
            "failures": dict(self.failures),
            "cache": dict(self.cache),
            "deferred": dict(self.deferred),
            "http_statuses": dict(self.http_statuses),
            "http_errors": dict(self.http_errors),
            "recovered_requests": dict(self.recovered_requests),
            "error_events": list(self.errors),
            "candidate_traces": list(self.candidates.values()),
            "telemetry_version": 2,
        }


CURRENT_SCAN: ContextVar[ScanMetrics | None] = ContextVar("radar_scan_metrics", default=None)
_REPOSITORY_DEPTH: ContextVar[int] = ContextVar("radar_repository_timing_depth", default=0)


def sqlite_timed(method):
    """Time outer repository calls once; background health work isn't part of a scan."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        metrics = CURRENT_SCAN.get()
        if metrics is None or _REPOSITORY_DEPTH.get():
            return method(*args, **kwargs)
        depth = _REPOSITORY_DEPTH.set(1)
        try:
            with metrics.measure("sqlite"):
                return method(*args, **kwargs)
        finally:
            _REPOSITORY_DEPTH.reset(depth)

    return wrapped
