"""Read-only data extraction from a database COPY.

Everything here uses SELECT only on a ``mode=ro`` connection. Unknown values stay
``None``; they are never coerced to 0 and are excluded from aggregates by the callers.
"""

import bisect
import json
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from revival_radar.analysis.call_metrics import finite, market_performance
from revival_radar.analysis.track_record import checkpoint_rows
from revival_radar.storage.database import SCHEMA_VERSION

BUCKET_SECONDS = 6 * 3600
HORIZONS = (6, 24)
CHECKPOINT_HORIZONS = (1, 6, 24, 72)
CALL_MATCH_SECONDS = 300  # an evaluation this close to a sent call is the call's evaluation
UNIVERSE = {"min_market_cap": 300_000.0, "min_drawdown": 0.4, "min_age_days": 3, "min_tracked_h": 6}
LATE_RISE_PCT = 50.0
COHORT_LABELS = {
    "A": "A. Actual calls",
    "B": "B. Eligible, score 40-59, not called",
    "C": "C. Eligible, score < 40",
    "D": "D. Ineligible (failed core filters)",
    "E": "E. Eligible, score >= 60, not called",
}


class ResearchError(Exception):
    """A user-facing refusal or input problem."""


def parse_time(text: str) -> float:
    """ISO-8601 (``Z`` or offset; naive means UTC) or epoch seconds."""
    text = str(text).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError as exc:
        raise ResearchError(f"Cannot parse time {text!r}: use ISO-8601 or epoch seconds") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def iso(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_database_path() -> Path:
    """The configured live database (DATABASE_PATH, .env or default)."""
    value = os.environ.get("DATABASE_PATH")
    if value:
        return Path(value)
    try:
        from revival_radar.config import Settings

        return Path(Settings().database_path)
    except Exception:  # an unreadable .env must not disable the guard
        return Path("data/revival_radar.db")


def refuse_live(path: Path, *, i_know_this_is_a_copy: bool, live: Path | None = None) -> None:
    live = live if live is not None else live_database_path()
    target = Path(path)
    same = target.resolve() == live.resolve()
    if not same and target.exists() and live.exists():
        same = os.path.samestat(target.stat(), live.stat())
    if same and not i_know_this_is_a_copy:
        raise ResearchError(
            f"Refusing to run: {path} is the configured live database ({live}). "
            "Make a copy first, for example: sqlite3 live.db \"VACUUM INTO 'copy.db'\", "
            "and pass the copy. Use --i-know-this-is-a-copy only if you are certain this "
            "path is a copy."
        )


def open_copy(path: Path) -> sqlite3.Connection:
    """Open strictly read-only. Any write attempt raises sqlite3.OperationalError."""
    target = Path(path)
    if not target.is_file():
        raise ResearchError(f"Database copy not found: {path}")
    db = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def tables(db) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def require_schema(db) -> int:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ResearchError(f"Database schema {version} is newer than this code ({SCHEMA_VERSION})")
    missing = {"alerts", "alert_signals", "token_snapshots", "evaluations", "scan_runs"} - tables(
        db
    )
    if missing:
        raise ResearchError(
            f"Not a scanner database (schema {version}); missing tables: {sorted(missing)}"
        )
    return version


@dataclass(frozen=True)
class Window:
    since: float
    until: float  # also the observation cutoff: nothing after it is read
    baseline_since: float  # start of the cohort / revival-universe sample


def _payload(raw):
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _num(value):
    return finite(value) if type(value) in (int, float) else None


def call_table(db, window: Window) -> list[dict]:
    """Delivered calls in [since, until] with performance as of ``until``; one dict per call."""
    have = tables(db)
    facts = "research_alert_facts" in have
    rows = db.execute(
        f"""SELECT a.id,a.timestamp,a.chain,a.contract_address,a.score,
        s.price,s.market_cap,s.setup_score,s.trigger_score,s.confirmation_score,
        s.stage,s.preset,s.revision,
        {"f.payload" if facts else "NULL"} AS frozen,
        {"f.score_version" if facts else "NULL"} AS frozen_version,
        (SELECT value FROM state WHERE key='telegram:alert:' || a.id) AS legacy
        FROM alerts a LEFT JOIN alert_signals s ON s.alert_id=a.id
        {"LEFT JOIN research_alert_facts f ON f.alert_id=a.id" if facts else ""}
        WHERE a.delivery_status='sent' AND a.timestamp BETWEEN ? AND ?
        ORDER BY a.timestamp,a.id""",
        (window.since, window.until),
    ).fetchall()
    sent = db.execute(
        "SELECT id,chain,contract_address,timestamp FROM alerts "
        "WHERE delivery_status='sent' AND timestamp<=? ORDER BY timestamp,id",
        (window.until,),
    ).fetchall()
    per_token = defaultdict(list)
    for r in sent:
        per_token[(r["chain"], r["contract_address"])].append(r["id"])
    calls = []
    for row in rows:
        payload = _payload(row["frozen"]) or _payload(row["legacy"])
        token = payload.get("token") if isinstance(payload.get("token"), dict) else {}
        signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        structure = signal.get("structure") if isinstance(signal.get("structure"), dict) else {}
        accel = signal.get("acceleration") if isinstance(signal.get("acceleration"), dict) else {}
        security = token.get("security") if isinstance(token.get("security"), dict) else {}
        symbol = token.get("symbol") if isinstance(token.get("symbol"), str) else None
        if not symbol:
            fallback = db.execute(
                "SELECT symbol FROM token_snapshots WHERE chain=? AND contract_address=? LIMIT 1",
                (row["chain"], row["contract_address"]),
            ).fetchone()
            symbol = fallback[0] if fallback and fallback[0] else None
        perf = market_performance(
            db, row["chain"], row["contract_address"], row["timestamp"], row["market_cap"],
            window.until,
        )  # fmt: skip
        siblings = per_token[(row["chain"], row["contract_address"])]
        # Late-call evidence from saved snapshots in the 24h up to the call: rise from the
        # lowest market cap to the call, and the largest run-up (a later value over any
        # earlier low, call included). Unknown when no snapshot exists in that window.
        rise = runup = None
        if perf.call_market_cap is not None:
            earlier = [
                r[0]
                for r in db.execute(
                    """SELECT market_cap FROM token_snapshots
                    WHERE chain=? AND contract_address=? AND timestamp>=? AND timestamp<=?
                    AND market_cap>0 AND market_cap<1e300 ORDER BY timestamp""",
                    (
                        row["chain"],
                        row["contract_address"],
                        row["timestamp"] - 86400,
                        row["timestamp"],
                    ),
                )
            ]
            if earlier:
                rise = (perf.call_market_cap / min(earlier) - 1) * 100
                low, best = float("inf"), 0.0
                for value in [*earlier, perf.call_market_cap]:
                    low = min(low, value)
                    best = max(best, value / low - 1)
                runup = best * 100
        calls.append(
            {
                "call_id": row["id"],
                "chain": row["chain"],
                "contract_address": row["contract_address"],
                "symbol": symbol,
                "called_at": iso(row["timestamp"]),
                "called_at_epoch": row["timestamp"],
                "call_market_cap": perf.call_market_cap,
                "call_price": _num(row["price"]) if (_num(row["price"]) or 0) > 0 else None,
                "stage": row["stage"] or signal.get("status") or None,
                "score": row["score"],
                "setup_score": row["setup_score"],
                "trigger_score": row["trigger_score"],
                "confirmation_score": row["confirmation_score"],
                "preset": row["preset"],
                "revision": row["revision"],
                "score_version": row["frozen_version"] or signal.get("score_version"),
                "latest_market_cap": perf.latest_market_cap,
                "latest_observed_at": iso(perf.latest_at),
                "change_since_call_pct": perf.change_since_call_pct,
                "change_reason": perf.change_reason,
                "mfe_pct": perf.mfe_pct,
                "mae_pct": perf.mae_pct,
                "observations": perf.count,
                "max_gap_seconds": perf.max_gap_seconds,
                "basis": perf.basis,
                "token_call_index": siblings.index(row["id"]) + 1
                if row["id"] in siblings
                else None,
                "token_call_count": len(siblings),
                "rise_24h_before_call_pct": rise,
                "runup_24h_before_call_pct": runup,
                "base_duration_hours": _num(structure.get("base_duration_hours")),
                "bundler_ratio": _num(security.get("bundler_ratio")),
                "price_change_1h_at_call": _num(token.get("price_change_1h")),
                "volume_acceleration_5m": _num(accel.get("volume_acceleration_5m")),
            }
        )
    return calls


def checkpoint_table(db, window: Window) -> list[dict]:
    """Saved price checkpoints for the call cohort, via the shared track-record accounting."""

    class Source:  # the two attributes checkpoint_rows needs, over our read-only connection
        research_outcomes_enabled = db.execute("PRAGMA user_version").fetchone()[0] >= 7

    Source.db = db
    if "signal_outcomes" not in tables(db):
        return []
    result = []
    for horizon in CHECKPOINT_HORIZONS:
        for row in checkpoint_rows(Source, window.since, window.until, horizon):
            result.append(
                {
                    "call_id": row["alert_id"],
                    "chain": row["chain"],
                    "contract_address": row["contract_address"],
                    "symbol": row["symbol"] if row["symbol"] != "?" else None,
                    "called_at": iso(row["timestamp"]),
                    "horizon_hours": horizon,
                    "state": row["state"],
                    "reason": row["reason"],
                    "due_at": iso(row["due_at"]),
                    "observed_at": iso(row["observed_at"]),
                    "initial_price": row["initial_price"],
                    "checkpoint_price": row["checkpoint_price"],
                    "checkpoint_market_cap": row["checkpoint_market_cap"],
                    "price_change_pct": row["return_pct"],
                    "score": row["score"],
                    "stage": row["stage"],
                    "score_version": row["score_version"],
                    "origin": row["origin"],
                }
            )
    result.sort(key=lambda r: (r["called_at"], r["call_id"], r["horizon_hours"]))
    return result


def funnel(db, window: Window) -> tuple[list[dict], dict]:
    """Daily and total funnel counts; scans, evaluations, eligibility, score buckets, calls."""
    lo, hi = window.since, window.until
    days: dict[str, dict] = {}

    def day(key):
        return days.setdefault(
            key,
            {
                "date": key,
                "evaluations": 0,
                "distinct_tokens": 0,
                "eligible": 0,
                "score_lt_40": 0,
                "score_40_59": 0,
                "score_ge_60": 0,
                "potential_alerts": 0,
                "sent_calls": 0,
            },
        )

    for d, n, tokens, elig, lt40, mid, ge60 in db.execute(
        """SELECT date(timestamp,'unixepoch'),COUNT(*),COUNT(DISTINCT chain||':'||contract_address),
        COALESCE(SUM(eligible),0),COALESCE(SUM(score<40),0),
        COALESCE(SUM(score>=40 AND score<60),0),COALESCE(SUM(score>=60),0)
        FROM evaluations WHERE timestamp BETWEEN ? AND ? GROUP BY 1""",
        (lo, hi),
    ):
        day(d).update(
            evaluations=n, distinct_tokens=tokens, eligible=elig, score_lt_40=lt40,
            score_40_59=mid, score_ge_60=ge60,
        )  # fmt: skip
    for d, n in db.execute(
        "SELECT date(started,'unixepoch'),COALESCE(SUM(potential_alerts),0) FROM scan_runs "
        "WHERE started BETWEEN ? AND ? GROUP BY 1",
        (lo, hi),
    ):
        day(d)["potential_alerts"] = n
    for d, n in db.execute(
        "SELECT date(timestamp,'unixepoch'),COUNT(*) FROM alerts "
        "WHERE delivery_status='sent' AND timestamp BETWEEN ? AND ? GROUP BY 1",
        (lo, hi),
    ):
        day(d)["sent_calls"] = n
    total = db.execute(
        """SELECT COUNT(*),COUNT(DISTINCT chain||':'||contract_address),COALESCE(SUM(eligible),0),
        COALESCE(SUM(score>=60),0),COALESCE(SUM(score>=60 AND eligible),0)
        FROM evaluations WHERE timestamp BETWEEN ? AND ?""",
        (lo, hi),
    ).fetchone()
    scans = db.execute(
        """SELECT COUNT(*),COALESCE(SUM(errors>0),0),
        COALESCE(SUM(potential_alerts),0) FROM scan_runs
        WHERE finished IS NOT NULL AND started BETWEEN ? AND ?""",
        (lo, hi),
    ).fetchone()
    calls = db.execute(
        "SELECT COUNT(*),COUNT(DISTINCT chain||':'||contract_address) FROM alerts "
        "WHERE delivery_status='sent' AND timestamp BETWEEN ? AND ?",
        (lo, hi),
    ).fetchone()
    summary = {
        "scans_completed": scans[0],
        "scans_with_source_error": scans[1],
        "evaluations": total[0],
        "distinct_tokens": total[1],
        "eligible": total[2],
        "score_ge_60": total[3],
        "score_ge_60_eligible": total[4],
        "potential_alerts": scans[2],
        "sent_calls": calls[0],
        "distinct_tokens_called": calls[1],
    }
    return [days[k] for k in sorted(days)], summary


def _snapshot_series(db, until):
    series = defaultdict(lambda: ([], []))
    for chain, address, t, mc in db.execute(
        "SELECT chain,contract_address,timestamp,market_cap FROM token_snapshots "
        "WHERE market_cap>0 AND market_cap<1e300 AND timestamp<=? ORDER BY timestamp",
        (until,),
    ):
        s = series[(chain, address)]
        s[0].append(t)
        s[1].append(mc)
    return series


def forward(series, key, t, hours, until):
    """Forward market-cap change from the snapshot at ``t`` over ``hours``.

    Returns ((change, mfe, mae, base, end), status); status is one of ok, no_base (no saved
    snapshot within 600s of the sample), pending (horizon after the cutoff) or missing (no
    snapshot within one hour after the horizon). Same rule as the audit prototype.
    """
    ts, mc = series.get(key, ([], []))
    i = bisect.bisect_left(ts, t - 60)
    if i >= len(ts) or abs(ts[i] - t) > 600:
        return None, "no_base"
    base = mc[i]
    if t + hours * 3600 > until:
        return None, "pending"
    j = bisect.bisect_left(ts, t + hours * 3600)
    if j >= len(ts) or ts[j] > t + hours * 3600 + 3600:
        return None, "missing"
    seg = mc[i : j + 1]
    return (
        ((mc[j] / base - 1) * 100, (max(seg) / base - 1) * 100, (min(seg) / base - 1) * 100,
         base, mc[j]),
        "ok",
    )  # fmt: skip


def cohort_samples(db, window: Window) -> list[dict]:
    """One sample per token, cohort and 6h bucket (the first evaluation in it), +6h/+24h."""
    series = _snapshot_series(db, window.until)
    call_times = defaultdict(list)
    for chain, address, t in db.execute(
        "SELECT chain,contract_address,timestamp FROM alerts WHERE delivery_status='sent'"
    ):
        call_times[(chain, address)].append(t)
    seen = set()
    samples = []
    for chain, address, t, symbol, score, eligible in db.execute(
        """SELECT chain,contract_address,timestamp,symbol,score,eligible FROM evaluations
        WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id""",
        (window.baseline_since, window.until),
    ):
        key = (chain, address)
        if any(abs(t - x) < CALL_MATCH_SECONDS for x in call_times.get(key, ())):
            cohort = "A"
        elif eligible and score >= 60:
            cohort = "E"
        elif eligible and score >= 40:
            cohort = "B"
        elif eligible:
            cohort = "C"
        else:
            cohort = "D"
        bucket = int(t // BUCKET_SECONDS)
        if (cohort, key, bucket) in seen:
            continue
        seen.add((cohort, key, bucket))
        for hours in HORIZONS:
            result, status = forward(series, key, t, hours, window.until)
            change, mfe, mae, base, end = result if result else (None,) * 5
            samples.append(
                {
                    "cohort": cohort,
                    "chain": chain,
                    "contract_address": address,
                    "symbol": symbol or None,
                    "sampled_at": iso(t),
                    "bucket_start": iso(bucket * BUCKET_SECONDS),
                    "score": score,
                    "eligible": int(bool(eligible)),
                    "horizon_hours": hours,
                    "coverage": status,
                    "base_market_cap": base,
                    "end_market_cap": end,
                    "change_pct": change,
                    "mfe_pct": mfe,
                    "mae_pct": mae,
                }
            )
    return samples


def _reason(text):
    return str(text).split(":")[0].split("(")[0].strip()[:40]


def revival_universe(db, window: Window) -> list[dict]:
    """Tokens in the revival universe with 24h MFE from first observation; called or not."""
    snaps = defaultdict(list)
    for chain, address, t, mc, dd, age in db.execute(
        """SELECT chain,contract_address,timestamp,market_cap,drawdown_from_ath,token_age_seconds
        FROM token_snapshots WHERE market_cap>0 AND market_cap<1e300
        AND timestamp>=? AND timestamp<=? ORDER BY timestamp""",
        (window.baseline_since, window.until),
    ):
        snaps[(chain, address)].append((t, mc, dd, age))
    evaluations = defaultdict(list)
    for chain, address, t, symbol, score, eligible, reasons in db.execute(
        """SELECT chain,contract_address,timestamp,symbol,score,eligible,rejection_reasons
        FROM evaluations WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id""",
        (window.baseline_since, window.until),
    ):
        try:
            parsed = json.loads(reasons)
        except (ValueError, TypeError):
            parsed = []
        evaluations[(chain, address)].append(
            (t, symbol, score, bool(eligible), parsed if isinstance(parsed, list) else [])
        )
    called = {
        (r[0], r[1])
        for r in db.execute(
            "SELECT chain,contract_address FROM alerts WHERE delivery_status='sent' "
            "AND timestamp<=?",
            (window.until,),
        )
    }
    u = UNIVERSE
    universe = []
    for key, series in snaps.items():
        t0, mc0, drawdown, age = series[0]
        # Unknown drawdown or age cannot qualify; it is excluded, not assumed to be 0.
        if not (
            mc0 >= u["min_market_cap"]
            and drawdown is not None
            and drawdown >= u["min_drawdown"]
            and age is not None
            and age >= u["min_age_days"] * 86400
        ):
            continue
        times = [s[0] for s in series]
        if times[-1] < t0 + u["min_tracked_h"] * 3600:
            continue
        end = bisect.bisect_right(times, t0 + 86400)
        seg = [s[1] for s in series[:end]]
        peak = seg.index(max(seg))
        peak_t = times[peak]
        evs = [e for e in evaluations.get(key, []) if e[0] <= peak_t]
        reasons = Counter(_reason(r) for e in evs for r in e[4])
        universe.append(
            {
                "chain": key[0],
                "contract_address": key[1],
                "symbol": (evs or evaluations.get(key) or [(0, None)])[0][1],
                "first_seen": iso(t0),
                "market_cap_first": mc0,
                "drawdown_from_ath": drawdown,
                "mfe_24h_pct": (max(seg) / mc0 - 1) * 100,
                "mae_24h_pct": (min(seg) / mc0 - 1) * 100,
                "hours_to_peak": (peak_t - t0) / 3600,
                "max_score_to_peak": max((e[2] for e in evs), default=None),
                "eligible_by_peak": any(e[3] for e in evs) if evs else None,
                "called": key in called,
                "top_rejection_reasons": reasons.most_common(4),
            }
        )
    universe.sort(key=lambda r: (-r["mfe_24h_pct"], r["chain"], r["contract_address"]))
    return universe
