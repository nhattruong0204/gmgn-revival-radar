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


@dataclass
class ScanMetrics:
    seconds: Counter = field(default_factory=Counter)
    calls: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    cache: Counter = field(default_factory=Counter)
    deferred: Counter = field(default_factory=Counter)

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
