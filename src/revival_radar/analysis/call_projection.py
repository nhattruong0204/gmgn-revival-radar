"""Call performance projection (schema 8): derived, rebuildable, never authoritative.

``call_performance`` holds, per delivered call, the aggregates that the public
``PublicQueries._performance`` would compute from saved snapshots in
``(call time, updated_at]``. The scanner maintains it after each scan; the website
reads it and folds in any snapshots newer than ``updated_at`` itself, so results are
identical to the on-read computation. The same ``fold`` arithmetic serves both
sides. Nothing here calls a provider or modifies a historical row.
"""

import math
import sqlite3
import time
from dataclasses import dataclass, replace

# Snapshot origins hidden from public reads (mirrors ``web.queries.public_reader``).
EXCLUDED_ORIGINS = "'synthetic','test'"
SOURCE_BASES = ("rank", "circulating_enriched")
DEFAULT_ACTIVE_DAYS = 30
BACKFILL_PER_RUN = 500


def usable(value: object) -> float | None:
    """Finite number or None; bool and non-numbers are unknown."""
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def positive(value: object) -> float | None:
    number = usable(value)
    return number if number is not None and number > 0 else None


@dataclass(frozen=True)
class State:
    """Aggregates over snapshots in (called, covered]; ``filtered`` = like-with-like only."""

    called: float
    call_market_cap: float | None
    call_source: str | None
    valid: int = 0  # finite, positive market caps in range (any basis)
    same: int = 0  # of those, labelled with the call's basis
    count: int = 0  # observations included in the aggregates
    high: float | None = None
    low: float | None = None
    latest_at: float | None = None
    latest_market_cap: float | None = None
    latest_price: float | None = None
    max_gap: float | None = None

    @property
    def filtered(self) -> bool:
        return bool(self.call_source) and self.same > 0

    @property
    def basis(self) -> str:
        if not self.call_source:
            return "unknown"
        if self.same:
            return "same"
        return "mixed" if self.valid else "unknown"

    @property
    def other_basis(self) -> int:
        return self.valid - self.same


def fold(state: State, rows) -> State | None:
    """Fold ordered ``(timestamp, market_cap, price, source)`` rows into the aggregates.

    Rows must already be strictly after the call and after everything folded so far, with
    market_cap positive and finite. Returns None when the basis rule changes (the first
    like-with-like row after only mixed ones): the caller must rebuild from scratch.
    """
    valid, same, count = state.valid, state.same, state.count
    high, low, latest = state.high, state.low, state.latest_at
    latest_mc, latest_price, gap = state.latest_market_cap, state.latest_price, state.max_gap
    source = state.call_source
    filtered = state.filtered
    for t, mc, price, row_source in rows:
        is_same = bool(source) and row_source == source
        if is_same and not filtered and source:
            if same == 0 and valid > 0 and count > 0:
                return None
            filtered = True
        valid += 1
        same += is_same
        if filtered and not is_same:
            continue
        count += 1
        high = mc if high is None else max(high, mc)
        low = mc if low is None else min(low, mc)
        step = t - (latest if latest is not None else state.called)
        gap = step if gap is None else max(gap, step)
        latest, latest_mc, latest_price = t, mc, positive(price)
    return replace(
        state,
        valid=valid,
        same=same,
        count=count,
        high=high,
        low=low,
        latest_at=latest,
        latest_market_cap=latest_mc,
        latest_price=latest_price,
        max_gap=gap,
    )


def fold_all(state: State, rows) -> State:
    """Full build: apply the basis rule to the whole range before aggregating."""
    rows = list(rows)
    source = state.call_source
    if source and any(r[3] == source for r in rows):
        # like-with-like exists: count every row, aggregate only the labelled ones
        selected = [r for r in rows if r[3] == source]
        result = fold(replace(state, same=0, valid=0), selected)
        return replace(result, valid=len(rows), same=len(selected))
    return fold(state, rows)


def snapshot_rows(db, chain, address, after, upto, *, hide_origins: bool):
    """Indexed range read; ``hide_origins`` applies the public view's provenance rule."""
    guard = (
        f""" AND NOT EXISTS(SELECT 1 FROM main.state p WHERE p.key='origin:snapshot:' || s.id
            AND p.value IN ({EXCLUDED_ORIGINS}))"""
        if hide_origins
        else ""
    )
    return db.execute(
        f"""SELECT s.timestamp,s.market_cap,s.price,json_extract(s.payload,'$.market_cap_source')
        FROM token_snapshots s WHERE s.chain=? AND s.contract_address=?
        AND s.timestamp>? AND s.timestamp<=? AND s.market_cap>0 AND s.market_cap<1e300{guard}
        ORDER BY s.timestamp""",
        (chain, address, after, upto),
    ).fetchall()


def call_source_at(db, chain, address, called, call_mc, *, hide_origins: bool):
    """Market-cap basis frozen with the alert: latest snapshot at/before the call, if it matches."""
    if call_mc is None:
        return None
    guard = (
        f""" AND NOT EXISTS(SELECT 1 FROM main.state p WHERE p.key='origin:snapshot:' || s.id
            AND p.value IN ({EXCLUDED_ORIGINS}))"""
        if hide_origins
        else ""
    )
    at_call = db.execute(
        f"""SELECT s.market_cap,json_extract(s.payload,'$.market_cap_source')
        FROM token_snapshots s WHERE s.chain=? AND s.contract_address=? AND s.timestamp<=?{guard}
        ORDER BY s.timestamp DESC LIMIT 1""",
        (chain, address, called),
    ).fetchone()
    if (
        at_call
        and at_call[1] in SOURCE_BASES
        and usable(at_call[0]) is not None
        and abs(at_call[0] - call_mc) <= 1e-9 * call_mc
    ):
        return at_call[1]
    return None


def build(db, chain, address, called, call_mc, upto, *, hide_origins: bool) -> State:
    source = call_source_at(db, chain, address, called, call_mc, hide_origins=hide_origins)
    rows = snapshot_rows(db, chain, address, called, upto, hide_origins=hide_origins)
    return fold_all(State(called, call_mc, source), rows)


COLUMNS = (
    "alert_id,basis,call_source,call_market_cap,latest_market_cap,latest_price,"
    "latest_observed_at,max_market_cap,min_market_cap,observations,max_gap_seconds,"
    "valid_observations,other_basis_observations,updated_at,frozen"
)


def row_to_state(row, called: float) -> State:
    valid = row["valid_observations"]
    other = row["other_basis_observations"]
    source = row["call_source"]
    return State(
        called=called,
        call_market_cap=row["call_market_cap"],
        call_source=source,
        valid=valid,
        same=valid - other,
        count=row["observations"],
        high=row["max_market_cap"],
        low=row["min_market_cap"],
        latest_at=row["latest_observed_at"],
        latest_market_cap=row["latest_market_cap"],
        latest_price=row["latest_price"],
        max_gap=row["max_gap_seconds"],
    )


def save(db, alert_id: int, state: State, updated_at: float, frozen: bool) -> None:
    db.execute(
        f"""INSERT OR REPLACE INTO call_performance({COLUMNS})
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            alert_id,
            state.basis,
            state.call_source,
            state.call_market_cap,
            state.latest_market_cap,
            state.latest_price,
            state.latest_at,
            state.high,
            state.low,
            state.count,
            state.max_gap,
            state.valid,
            state.other_basis,
            updated_at,
            int(frozen),
        ),
    )


def refresh(
    db: sqlite3.Connection,
    now: float | None = None,
    active_days: float = DEFAULT_ACTIVE_DAYS,
    limit: int = BACKFILL_PER_RUN,
) -> dict:
    """Scanner-side maintenance: incremental for active calls, bounded backfill, then freeze.

    Reads only the indexed (chain, contract, timestamp) snapshot range newer than each row's
    ``updated_at``; one short write transaction. Idempotent and safe to re-run.
    """
    now = time.time() if now is None else now
    horizon = now - active_days * 86400
    started = time.monotonic()
    pending = db.execute(
        f"""SELECT a.id,a.timestamp,a.chain,a.contract_address,sg.market_cap,
            cp.alert_id AS has_row,{", ".join("cp." + c for c in COLUMNS.split(",")[1:])}
        FROM alerts a LEFT JOIN alert_signals sg ON sg.alert_id=a.id
        LEFT JOIN call_performance cp ON cp.alert_id=a.id
        WHERE a.delivery_status='sent' AND (cp.alert_id IS NULL OR cp.frozen=0)
        AND NOT EXISTS(SELECT 1 FROM state p WHERE p.key='origin:alert:' || a.id
            AND p.value IN ({EXCLUDED_ORIGINS}))
        ORDER BY (cp.alert_id IS NULL), a.timestamp DESC,a.id DESC"""
    ).fetchall()
    stats = {"updated": 0, "built": 0, "rebuilt": 0, "frozen": 0}
    built = 0
    with db:
        for row in pending:
            called = row["timestamp"]
            call_mc = positive(row["market_cap"])
            chain, address = row["chain"], row["contract_address"]
            old = None
            if row["has_row"] is not None:
                old = row_to_state(row, called)
                if row["call_market_cap"] != call_mc:
                    old = None  # the frozen call context changed: rebuild, never trust
            if old is None:
                if built >= limit:
                    continue
                built += 1
                stats["rebuilt" if row["has_row"] is not None else "built"] += 1
                state = build(db, chain, address, called, call_mc, now, hide_origins=True)
            else:
                rows = snapshot_rows(
                    db, chain, address, max(row["updated_at"], called), now, hide_origins=True
                )
                state = fold(old, rows)
                if state is None:
                    state = build(db, chain, address, called, call_mc, now, hide_origins=True)
                    stats["rebuilt"] += 1
                stats["updated"] += 1
            frozen = called < horizon
            stats["frozen"] += frozen
            save(db, row["id"], state, now, frozen)
    stats["seconds"] = time.monotonic() - started
    return stats
