"""Pure call-vs-snapshot arithmetic shared by the public API and the research export.

Takes a plain SQLite connection and raw values; imports nothing from the web layer.
``scripts/verify_call_parity.py`` deliberately does NOT use this module: it recomputes
the same figures independently so agreement is a real check.
"""

import math
import sqlite3
from dataclasses import dataclass


def finite(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class MarketPerformance:
    call_market_cap: float | None
    call_source: str | None
    basis: str  # same | mixed | unknown
    other_basis: int
    count: int
    high: float | None
    low: float | None
    latest_at: float | None
    latest_market_cap: float | None
    latest_price: float | None
    max_gap_seconds: float | None
    change_since_call_pct: float | None
    change_reason: str | None  # no_call_mc | no_later_observation
    mfe_pct: float | None
    mae_pct: float | None


def market_performance(
    db: sqlite3.Connection,
    chain: str,
    address: str,
    called: float,
    call_market_cap: object,
    until: float,
) -> MarketPerformance:
    """Compare the frozen call market cap with saved snapshots in (called, until]; read-only."""
    call_mc = finite(call_market_cap)
    call_mc = call_mc if call_mc is not None and call_mc > 0 else None
    # Alert-time market-cap source: the latest snapshot at or before the call (indexed
    # LIMIT 1), accepted only if its market cap is the one the alert froze. Legacy
    # payloads carry no source and stay unknown; nothing is inferred or rewritten.
    call_source = None
    if call_mc is not None:
        at_call = db.execute(
            """SELECT market_cap,json_extract(payload,'$.market_cap_source')
            FROM token_snapshots WHERE chain=? AND contract_address=? AND timestamp<=?
            ORDER BY timestamp DESC LIMIT 1""",
            (chain, address, called),
        ).fetchone()
        if (
            at_call
            and at_call[1] in ("rank", "circulating_enriched")
            and finite(at_call[0]) is not None
            and abs(at_call[0] - call_mc) <= 1e-9 * call_mc
        ):
            call_source = at_call[1]
    # Finite, positive market caps only; 1e300 excludes +inf without Python callbacks.
    # One indexed range read (chain, contract_address, timestamp) bounded by the cutoff.
    source_filter, source_args = "", ()
    basis, other_basis = "unknown", 0
    if call_source:
        total, same = db.execute(
            """SELECT COUNT(*),COALESCE(SUM(json_extract(payload,'$.market_cap_source')=?),0)
            FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
            AND market_cap>0 AND market_cap<1e300""",
            (call_source, chain, address, called, until),
        ).fetchone()
        if same:
            # Prefer like-with-like; the excluded count is reported, never dropped silently.
            basis, other_basis = "same", total - same
            if other_basis:
                source_filter = " AND json_extract(payload,'$.market_cap_source')=?"
                source_args = (call_source,)
        elif total:
            basis = "mixed"  # only other-basis or unlabelled snapshots exist to compare
    agg = db.execute(
        f"""WITH r AS (SELECT timestamp t,market_cap mc FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
            AND market_cap>0 AND market_cap<1e300{source_filter})
        SELECT COUNT(*),MAX(mc),MIN(mc),MAX(t),
            (SELECT MAX(g) FROM (SELECT t-LAG(t,1,?) OVER (ORDER BY t) g FROM r)) FROM r""",
        (chain, address, called, until, *source_args, called),
    ).fetchone()
    count, high, low, latest_at, gap = agg
    latest_mc = latest_price = None
    if count:
        latest = db.execute(
            """SELECT market_cap,price FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp=?""",
            (chain, address, latest_at),
        ).fetchone()
        latest_mc = finite(latest[0])
        price = finite(latest[1])
        latest_price = price if price is not None and price > 0 else None
    change = mfe = mae = None
    reason = None
    if call_mc is None:
        reason = "no_call_mc"
    elif not count:
        reason = "no_later_observation"
    else:
        change = (latest_mc / call_mc - 1) * 100
        mfe = (high / call_mc - 1) * 100
        mae = (low / call_mc - 1) * 100
    return MarketPerformance(
        call_market_cap=call_mc,
        call_source=call_source,
        basis=basis,
        other_basis=other_basis,
        count=count,
        high=high,
        low=low,
        latest_at=latest_at if count else None,
        latest_market_cap=latest_mc,
        latest_price=latest_price,
        max_gap_seconds=gap if count else None,
        change_since_call_pct=change,
        change_reason=reason,
        mfe_pct=mfe,
        mae_pct=mae,
    )
