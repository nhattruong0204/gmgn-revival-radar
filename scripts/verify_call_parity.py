"""Read-only call parity check (#64): saved database versus the public alerts API.

The call performance is recomputed here from raw rows with plain SQL and Python and
deliberately does not import ``revival_radar.web.queries``; agreement is therefore an
independent check, not the same code compared with itself. The database is opened
with ``mode=ro`` and the API is only queried with GET.

    python scripts/verify_call_parity.py --db copy.db [--api https://host] [--as-of 1.8e9]

Exit status 0 means every compared call matched, 1 means at least one mismatch
(or a call visible on only one side), 2 means the check could not run.
"""

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FRESH_SECONDS = 30 * 60  # documented contract: a latest observation is fresh within 30 minutes
TOLERANCE = 1e-6
FIELDS = (
    "timestamp",
    "chain",
    "contract_address",
    "call_market_cap",
    "latest_market_cap",
    "latest_observed_at",
    "freshness",
    "change_since_call_pct",
    "change_reason",
    "mfe_pct",
    "mae_pct",
    "observations",
    "max_gap_seconds",
    "token_call_index",
    "token_call_count",
)


def usable(value):
    """A finite, strictly positive number, else None (unknown is never coerced to zero)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 < value < 1e300 else None


def open_readonly(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def expected_calls(db, until):
    """Recompute each delivered call's performance from alerts, alert_signals, snapshots."""
    calls = db.execute(
        """SELECT a.id,a.timestamp,a.chain,a.contract_address,s.market_cap
        FROM alerts a LEFT JOIN alert_signals s ON s.alert_id=a.id
        WHERE a.delivery_status='sent' AND a.timestamp<=? ORDER BY a.timestamp,a.id""",
        (until,),
    ).fetchall()
    per_token = {}
    for row in calls:
        per_token.setdefault((row["chain"], row["contract_address"]), []).append(row["id"])
    result = {}
    for row in calls:
        called = row["timestamp"]
        call_mc = usable(row["market_cap"])
        later = [
            (r[0], usable(r[1]))
            for r in db.execute(
                """SELECT timestamp,market_cap FROM token_snapshots
                WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
                ORDER BY timestamp""",
                (row["chain"], row["contract_address"], called, until),
            )
        ]
        later = [(t, mc) for t, mc in later if mc is not None]
        item = {
            "timestamp": called,
            "chain": row["chain"],
            "contract_address": row["contract_address"],
            "call_market_cap": call_mc,
            "latest_market_cap": later[-1][1] if later else None,
            "latest_observed_at": later[-1][0] if later else None,
            "observations": len(later),
            "max_gap_seconds": None,
            "freshness": "none",
            "change_since_call_pct": None,
            "change_reason": None,
            "mfe_pct": None,
            "mae_pct": None,
        }
        siblings = per_token[(row["chain"], row["contract_address"])]
        item["token_call_index"] = siblings.index(row["id"]) + 1
        item["token_call_count"] = len(siblings)
        if later:
            times = [called] + [t for t, _ in later]
            item["max_gap_seconds"] = max(b - a for a, b in zip(times, times[1:], strict=False))
            item["freshness"] = "fresh" if until - later[-1][0] <= FRESH_SECONDS else "stale"
        if call_mc is None:
            item["change_reason"] = "no_call_mc"
        elif not later:
            item["change_reason"] = "no_later_observation"
        else:
            item["change_since_call_pct"] = (later[-1][1] / call_mc - 1) * 100
            item["mfe_pct"] = (max(mc for _, mc in later) / call_mc - 1) * 100
            item["mae_pct"] = (min(mc for _, mc in later) / call_mc - 1) * 100
        result[row["id"]] = item
    return result


def get_json(url, timeout=20):
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.load(response)


def fetch_api(base_url, fetch=None, as_of=None):
    """Page through /api/v1/alerts with GET only; returns (as_of, {call_id: flat item})."""
    fetch = fetch or get_json
    base = base_url.rstrip("/") + "/api/v1/alerts"
    items, page, cutoff = {}, 1, as_of
    while True:
        query = {"page": page, "page_size": 100}
        if cutoff is not None:
            query["as_of"] = repr(float(cutoff))
        body = fetch(base + "?" + urllib.parse.urlencode(query))
        cutoff = body["as_of"] if cutoff is None else cutoff
        for item in body["items"]:
            items[item["id"]] = {
                **{k: item.get(k) for k in ("timestamp", "chain", "contract_address")},
                **item["performance"],
            }
        if not body["items"] or len(items) >= body["total"] or page >= 1000:
            return cutoff, items
        page += 1


def same(left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
    return left == right


def compare(expected, api):
    """List of (call_id, field, recomputed, api) for every disagreement."""
    mismatches = []
    for identity in sorted(set(expected) | set(api)):
        if identity not in api:
            mismatches.append((identity, "presence", "in database", "absent from API"))
        elif identity not in expected:
            mismatches.append((identity, "presence", "absent from database", "in API"))
        else:
            for field in FIELDS:
                if not same(expected[identity][field], api[identity].get(field)):
                    mismatches.append(
                        (identity, field, expected[identity][field], api[identity].get(field))
                    )
    return mismatches


def fmt(value, digits=1):
    if value is None:
        return "unknown"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def table(expected, bad_ids):
    columns = ["call", "token", "callMC", "latestMC", "change%", "obs", "fresh", "#/n", "status"]
    rows = []
    for identity, e in expected.items():
        change = e["change_reason"] or fmt(e["change_since_call_pct"], 2)
        rows.append(
            [
                str(identity),
                e["contract_address"][:10] + "..",
                fmt(e["call_market_cap"], 0),
                fmt(e["latest_market_cap"], 0),
                change,
                str(e["observations"]),
                e["freshness"],
                f"{e['token_call_index']}/{e['token_call_count']}",
                "MISMATCH" if identity in bad_ids else "ok",
            ]
        )
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(columns)]
    lines = ["  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)) for row in rows]
    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    return "\n".join([header, "-" * len(header), *lines])


def run(db_path, api=None, as_of=None, fetch=None, out=print):
    """Returns the mismatch list; prints the per-call table and the count."""
    api_items = {}
    until = as_of
    if api:
        until, api_items = fetch_api(api, fetch, as_of)
    elif until is None:
        until = time.time()
    db = open_readonly(db_path)
    try:
        expected = expected_calls(db, until)
    finally:
        db.close()
    mismatches = compare(expected, api_items) if api else []
    out(f"as_of={until} database={db_path} api={api or 'not compared'}")
    out(table(expected, {m[0] for m in mismatches}))
    for identity, field, mine, theirs in mismatches:
        out(f"MISMATCH call {identity} {field}: recomputed={mine!r} api={theirs!r}")
    compared = len(api_items) if api else 0
    out(f"calls={len(expected)} compared={compared} mismatches={len(mismatches)}")
    return mismatches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, type=Path, help="SQLite copy, opened read-only")
    parser.add_argument("--api", help="public base URL; compared with GET requests only")
    parser.add_argument("--as-of", type=float, help="cutoff (default: the API's, else now)")
    args = parser.parse_args(argv)
    try:
        mismatches = run(args.db, args.api, args.as_of)
    except (sqlite3.Error, urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"Cannot verify: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
