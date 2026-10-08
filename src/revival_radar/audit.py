"""Offline research over a supplied consistent SQLite copy. Standard library only.

No migrations, HTTP, Settings/.env loading, interpolation, or rescore. Returns are
sampled observations, not execution P&L. Legacy facts stay unknown.
"""

import bisect
import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

HORIZONS = (1, 6, 24, 72)
DIMENSIONS = ("score", "setup_score", "trigger_score", "confirmation_score")
SECURITY = (
    "top10_ratio",
    "dev_ratio",
    "sniper_ratio",
    "bundler_ratio",
    "insider_ratio",
    "dangerous",
)
NEAR_BANDS = ((50, 59), (60, 64), (65, 69), (70, 74), (75, 100))
OVERALL_BANDS = ((0, 39), (40, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 100))
DIMENSION_BANDS = ((0, 20), (21, 40), (41, 60), (61, 80), (81, 100))


def decoded(value, fallback=None):
    try:
        return json.loads(value) if isinstance(value, str) else fallback
    except (ValueError, TypeError):
        return fallback


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quantile(values, fraction):
    ordered = sorted(v for v in values if finite(v))
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    left, right = math.floor(position), math.ceil(position)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def distribution(values):
    values = [v for v in values if finite(v)]
    return {
        "n": len(values),
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        **{f"p{int(p * 100)}": quantile(values, p) for p in (0.5, 0.9, 0.95, 0.99)},
        "min": min(values, default=None),
        "max": max(values, default=None),
    }


def rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "pct": numerator / denominator * 100 if denominator else None,
    }


def cohort(context, signal, *, require_dimensions=True):
    context, signal = context or {}, signal or {}
    version = signal.get("score_version")
    if not version or (require_dimensions and any(signal.get(d) is None for d in DIMENSIONS[1:])):
        version = "LEGACY"
    # Settings fingerprint protects against revision counters reused after reset/redeploy.
    import hashlib

    settings = context.get("settings") or {}
    fingerprint = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    return (context.get("preset") or "UNKNOWN", context.get("revision"), version, fingerprint)


def sequential_funnel(traces):
    """Intersect candidate sets, never divide overlapping marginal stage counters.

    This is the baseline-ready research path; activity may ALSO arise from one-step
    hourly/TX acceleration without a multi-observation volume baseline.
    """
    fields = (
        "candidate",
        "prefilter_pass",
        "market_enriched",
        "market_pass",
        "baseline_ready",
        "activity_trigger",
        "structure_available",
        "base_detected",
        "eligible",
        "potential_alert",
        "alerted",
    )
    survivors = list(traces)
    universe = len(survivors)
    previous = universe
    result = []
    for field in fields:
        if field != "candidate":
            survivors = [t for t in survivors if t.get(field) is True]
        count = len(survivors)
        result.append(
            {
                "stage": field,
                "count": count,
                "from_previous": rate(count, previous),
                "from_universe": rate(count, universe),
            }
        )
        previous = count
    return {
        "grain": "scan/chain/address; discovered and due watchlist separate populations",
        "stages": result,
        "activity_without_baseline": sum(
            t.get("activity_trigger") is True and t.get("baseline_ready") is not True
            for t in traces
        ),
        "security": rate(
            sum(t.get("security_checked") is True for t in traces),
            sum(t.get("security_expected") is True for t in traces),
        ),
        "note": "Security is a conditional branch after optimistic score, not a mandatory "
        "stage for every eligible token. Outcome-only polls excluded.",
    }


def sampled_outcome(points, timestamp, initial, until):
    """First post-horizon price in +1h grace; excursions over sampled prices only."""
    points = sorted((t, p) for t, p in points if finite(t) and finite(p) and p >= 0 and t <= until)
    valid_initial = finite(initial) and initial > 0
    times = [t for t, _ in points]
    result = {}
    for hours in HORIZONS:
        due, end = timestamp + hours * 3600, timestamp + (hours + 1) * 3600
        left = bisect.bisect_left(times, due)
        observation = points[left] if left < len(points) and points[left][0] <= end else None
        followup = points[bisect.bisect_right(times, timestamp) : bisect.bisect_right(times, due)]
        returns = [(t, (p / initial - 1) * 100) for t, p in followup] if valid_initial else []
        returns = [(t, v) for t, v in returns if finite(v)]
        change = (observation[1] / initial - 1) * 100 if observation and valid_initial else None
        completed = due <= until
        result[str(hours)] = {
            "observed_at": observation[0] if observation else None,
            "return_pct": change if finite(change) else None,
            "checkpoint_state": "observed"
            if observation
            else "missed"
            if end < until
            else "pending",
            "horizon_mature": completed,
            "sampled_points": len(returns),
            "first_sample_at": returns[0][0] if returns else None,
            "last_sample_at": returns[-1][0] if returns else None,
            "max_sample_gap_seconds": max(
                (b[0] - a[0] for a, b in zip(returns, returns[1:], strict=False)), default=None
            ),
            "sampled_mfe_pct": max((v for _, v in returns), default=None) if completed else None,
            "sampled_mae_pct": min((v for _, v in returns), default=None) if completed else None,
            "time_to_25pct_seconds": next((t - timestamp for t, v in returns if v >= 25), None),
        }
    return result


def performance_summary(records):
    result = {
        "n": len(records),
        "evidence": "INSUFFICIENT SAMPLE"
        if len(records) < 30
        else "DESCRIPTIVE ONLY; effective independent N and bias need validation",
    }
    for hours in HORIZONS:
        entries = [r["outcomes"][str(hours)] for r in records]
        returns = [e["return_pct"] for e in entries if finite(e["return_pct"])]
        mfes = [e["sampled_mfe_pct"] for e in entries if finite(e["sampled_mfe_pct"])]
        result[str(hours)] = {
            "returns": distribution(returns),
            "sampled_mfe": distribution(mfes),
            "sampled_mae": distribution([e["sampled_mae_pct"] for e in entries]),
            "positive_return": rate(sum(v > 0 for v in returns), len(returns)),
            "sampled_hit_rates": {
                str(level): rate(sum(v >= level for v in mfes), len(mfes)) for level in (10, 25, 50)
            },
            "pending": sum(e["checkpoint_state"] == "pending" for e in entries),
            "missed": sum(e["checkpoint_state"] == "missed" for e in entries),
            "time_to_25pct_seconds": distribution([e["time_to_25pct_seconds"] for e in entries]),
        }
    return result


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def database_profile(db, until):
    natural = {
        "token_snapshots": ("chain", "contract_address", "timestamp"),
        "evaluations": ("scan_id", "chain", "contract_address"),
        "signal_outcomes": ("alert_id", "horizon_hours"),
        "alert_signals": ("alert_id",),
        "watch_state": ("chain", "contract_address"),
        "enrichment_cache": ("kind", "chain", "contract_address"),
        "state": ("key",),
    }
    sizes = {}
    with suppress(sqlite3.Error):
        sizes = dict(db.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name"))
    tables = {}
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = quoted(name)
        columns = [dict(r) for r in db.execute(f"PRAGMA table_info({table})")]
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        nulls = {
            c["name"]: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {quoted(c['name'])} IS NULL"
            ).fetchone()[0]
            for c in columns
        }
        times = {}
        for c in columns:
            if c["name"] in {
                "timestamp",
                "started",
                "finished",
                "due_at",
                "observed_at",
                "updated_at",
                "first_seen",
                "last_seen",
                "last_polled",
                "fetched_at",
                "expires_at",
                "next_due",
            }:
                field = quoted(c["name"])
                lo, hi, future, negative = db.execute(
                    f"SELECT MIN({field}),MAX({field}),SUM({field}>?),SUM({field}<0) FROM {table}",
                    (until + 300,),
                ).fetchone()
                times[c["name"]] = {
                    "min": lo,
                    "max": hi,
                    "negative": negative or 0,
                    "future": future or 0,
                    "future_may_be_scheduled": c["name"] in {"due_at", "expires_at", "next_due"},
                }
        keys = natural.get(name, tuple(c["name"] for c in columns if c["pk"]))
        duplicates = None
        if keys:
            fields = ",".join(quoted(k) for k in keys)
            duplicates = db.execute(
                f"SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM {table} "
                f"GROUP BY {fields} HAVING COUNT(*)>1)"
            ).fetchone()[0]
        invalid_json = {}
        for c in columns:
            if c["name"] in {
                "payload",
                "rejection_reasons",
                "missing_fields",
                "warnings",
                "discovery_sources",
                "discovered_keys",
                "discovered_by_chain",
                "discovered_by_source",
                "source_errors",
            }:
                field = quoted(c["name"])
                invalid_json[c["name"]] = db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE NOT json_valid({field})"
                ).fetchone()[0]
        tables[name] = {
            "rows": count,
            "columns": columns,
            "null_counts": nulls,
            "null_pct": {k: v / count * 100 if count else None for k, v in nulls.items()},
            "times": times,
            "duplicate_excess_rows": duplicates,
            "invalid_json": invalid_json,
            "table_bytes": sizes.get(name),
        }
    relationships = {}
    for child, field, parent in (
        ("evaluations", "scan_id", "scan_runs"),
        ("alert_signals", "alert_id", "alerts"),
        ("signal_outcomes", "alert_id", "alerts"),
    ):
        if child in tables and parent in tables:
            relationships[f"{child}->{parent}"] = db.execute(
                f"SELECT COUNT(*) FROM {quoted(child)} c LEFT JOIN {quoted(parent)} p "
                f"ON c.{quoted(field)}=p.id WHERE p.id IS NULL"
            ).fetchone()[0]
    return {
        "integrity": [r[0] for r in db.execute("PRAGMA integrity_check")],
        "foreign_key_check": len(db.execute("PRAGMA foreign_key_check").fetchall()),
        "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
        "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
        "tables": tables,
        "orphan_counts": relationships,
        "foreign_keys_enforced_on_audit_connection": db.execute("PRAGMA foreign_keys").fetchone()[
            0
        ],
        "growth_bytes_per_day": None,
        "growth_note": "A single DB file cannot establish actual storage growth. Compare "
        "dated backups including WAL and freelist.",
    }


def rows(db, table):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)
    ).fetchone():
        return []
    return [dict(row) for row in db.execute(f"SELECT * FROM {quoted(table)}")]


def budget_analysis(traces, until):
    by_token = defaultdict(list)
    for trace in traces:
        by_token[(trace["chain"], trace["contract_address"])].append(trace)
    delays, ages, consecutive = [], [], []
    unresolved, starved = 0, 0
    for observations in by_token.values():
        observations.sort(key=lambda t: t.get("timestamp", 0))
        first = observations[0].get("first_seen")
        enriched = next((t for t in observations if t.get("market_enriched")), None)
        # A last_polled value is ambiguous: a prefilter reject also updates it.
        # Delay is exact only when the capture covers the actual discovery observation.
        captured_from_discovery = finite(first) and observations[0].get("timestamp") == first
        if enriched and captured_from_discovery:
            received = enriched.get("market_received_at", enriched["market_observed_at"])
            if received >= first:
                delays.append(received - first)
        streak, max_streak, deferred_start = 0, 0, None
        for t in observations:
            if t.get("market_deferred"):
                streak += 1
                deferred_start = (
                    deferred_start if deferred_start is not None else t.get("timestamp")
                )
            else:
                streak, deferred_start = 0, None
            max_streak = max(max_streak, streak)
        consecutive.append(max_streak)
        if observations[-1].get("market_deferred"):
            unresolved += 1
            if deferred_start is not None:
                age = max(0, until - deferred_start)
                ages.append(age)
                starved += age >= 1800  # Explicit operational definition, not a strategy threshold.
    return {
        "market_deferred_observations": sum(t.get("market_deferred", False) for t in traces),
        "market_backlog_at_last_observation": unresolved,
        "oldest_deferred_seconds_observed_lower_bound": max(ages, default=None),
        "first_enrichment_delay_seconds": distribution(delays),
        "consecutive_observed_market_deferrals": distribution(consecutive),
        "starved_candidates_30m_observed": starved,
        "expired_without_enrichment": None,
        "note": "Deferral ages are lower bounds if telemetry starts mid-queue. "
        "Backlog is last captured "
        "candidate state, not guaranteed current queue: absent/expired candidates may remain. "
        "Legacy watch_state.deferred combines budget deferrals and processing failures.",
    }


def correlate_logs(scans, log_summary):
    """Correlate sanitized Docker timestamps; legacy attribution is temporal, not exact."""
    categories = defaultdict(lambda: {"lines": 0, "scans": set(), "tokens": set()})
    completions = []
    unmatched = 0
    for event in log_summary.get("events", []):
        try:
            stamp = event.get("timestamp_text") or ""
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00").replace(",", "."))
            if parsed.tzinfo is None:
                unmatched += 1
                continue  # Naive application timestamps do not establish a UTC instant.
            timestamp = parsed.timestamp()
        except (ValueError, TypeError):
            unmatched += 1
            continue
        matching = [s for s in scans if s["started"] <= timestamp <= (s["finished"] or timestamp)]
        if event.get("event") == "scan_complete":
            nearby = [
                s
                for s in scans
                if s["finished"] is not None and abs(s["finished"] - timestamp) <= 5
            ]
            if nearby:
                selected = min(nearby, key=lambda s: abs(s["finished"] - timestamp))
                difference = {
                    name: {"log": event[name], "db": selected[name]}
                    for name in ("processed", "errors", "sent", "potential_alerts", "sources_ok")
                    if name in event and event[name] != selected[name]
                }
                completions.append(
                    {
                        "scan_id": selected["id"],
                        "counter_differences": difference,
                        "time_offset_seconds": timestamp - selected["finished"],
                    }
                )
            else:
                unmatched += 1
        category = event.get("category")
        if category:
            item = categories[category]
            item["lines"] += 1
            item["scans"].update(s["id"] for s in matching)
            if event.get("chain") and event.get("contract_address"):
                item["tokens"].add((event["chain"], event["contract_address"]))
    return {
        "categories": {
            k: {
                "lines": v["lines"],
                "temporally_affected_scans": len(v["scans"]),
                "identified_tokens": len(v["tokens"]),
            }
            for k, v in categories.items()
        },
        "completion_checks": completions,
        "unmatched_or_naive_timestamps": unmatched,
        "limitations": "Temporal association is not causal attribution. Interrupted scans have "
        "unknown end; log truncation/rotation and absent scan IDs limit consistency checks.",
    }


def analyze(database, *, since=None, until=None, source="research", timezone="Asia/Saigon"):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("Supply an existing consistent audit copy")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        scans_all = rows(db, "scan_runs")
        snapshots_all = rows(db, "token_snapshots")
        if until is None:
            until = max(
                [r["started"] for r in scans_all] + [r["timestamp"] for r in snapshots_all],
                default=0,
            )
            until = max(until, max((r["finished"] or 0 for r in scans_all), default=0))
        since = until - 7 * 86400 if since is None else since
        profile = database_profile(db, until)
        state_rows = rows(db, "state")
        states = {r["key"]: r["value"] for r in state_rows}
        state_times = {r["key"]: r["updated_at"] for r in state_rows}
        scans = [r for r in scans_all if since <= r["started"] <= until]
        scan_ids = {r["id"] for r in scans}
        evaluations = [
            r
            for r in rows(db, "evaluations")
            if r["scan_id"] in scan_ids and r["timestamp"] <= until
        ]
        evaluations.sort(key=lambda e: (e["timestamp"], e["id"]))
        config_by_scan = {
            s["id"]: decoded(states.get(f"telegram:scan:{s['id']}"), {}) for s in scans
        }
        metrics = {
            s["id"]: decoded(states.get(f"scan:metrics:{s['id']}"), {})
            if state_times.get(f"scan:metrics:{s['id']}", until) <= until
            else {}
            for s in scans
        }
        payloads, groups = {}, defaultdict(list)
        for e in evaluations:
            detail = decoded(states.get(f"telegram:evaluation:{e['id']}"), {})
            if not isinstance(detail, dict):
                detail = {}
            payloads[e["id"]] = detail
            e["cohort"] = cohort(
                detail.get("configuration") or config_by_scan.get(e["scan_id"]),
                detail.get("signal"),
            )
            groups[e["cohort"]].append(e)
        prices = defaultdict(list)
        samples = defaultdict(list)
        invalid_values = Counter()
        for r in snapshots_all:
            if r["timestamp"] > until:
                continue
            samples[(r["chain"], r["contract_address"])].append(r)
            if finite(r.get("price")):
                prices[(r["chain"], r["contract_address"])].append((r["timestamp"], r["price"]))
            for key in (
                "price",
                "liquidity",
                "market_cap",
                "volume_5m",
                "volume_1h",
                "tx_5m",
                "tx_1h",
                "holders",
            ):
                if r.get(key) is not None and (not finite(r[key]) or r[key] < 0):
                    invalid_values[key] += 1
        for values in samples.values():
            values.sort(key=lambda r: r["timestamp"])
        profile["invalid_snapshot_values"] = dict(invalid_values)
        profile["invalid_evaluation_score_or_eligible"] = sum(
            not finite(e["score"]) or not 0 <= e["score"] <= 100 or e["eligible"] not in (0, 1)
            for e in evaluations
        )
        profile["unfinished_scans_total"] = sum(s["finished"] is None for s in scans_all)
        gaps = [
            b["timestamp"] - a["timestamp"]
            for values in samples.values()
            for a, b in zip(values, values[1:], strict=False)
        ]
        snapshot_profile = {
            "snapshots_per_token": distribution([len(v) for v in samples.values()]),
            "sampling_interval_seconds": distribution(gaps),
            "tokens": len(samples),
            "gaps_over_900s": sum(g > 900 for g in gaps),
            "note": "900s reference only; cohort-specific HISTORY_MAX_GAP_SECONDS "
            "controls readiness.",
        }
        coverage_counts, segment_counts = Counter(), defaultdict(Counter)
        structure_counts = Counter()
        overlap = Counter()
        inferred_traces = []
        for e in evaluations:
            detail = payloads[e["id"]]
            token, signal = detail.get("token", {}), detail.get("signal", {})
            missing = decoded(e["missing_fields"], [])
            reasons = decoded(e["rejection_reasons"], [])
            overlap.update(reasons if isinstance(reasons, list) else [])
            if not token or not signal:
                continue
            coverage_counts["known_presentations"] += 1
            for field in ("volume_5m", "tx_5m", "tx_1h", "ath_market_cap"):
                coverage_counts[field] += token.get(field) is not None
            sec, structure = token.get("security", {}), signal.get("structure", {})
            for field in SECURITY:
                coverage_counts[f"security.{field}"] += sec.get(field) is not None
            coverage_counts["structure_available"] += bool(structure.get("available"))
            baseline = "baseline_history" not in missing
            coverage_counts["baseline_ready"] += baseline
            previous = samples.get((e["chain"], e["contract_address"]), [])
            first = previous[0]["timestamp"] if previous else None
            age = e["timestamp"] - first if first is not None else None
            settings = (detail.get("configuration") or config_by_scan.get(e["scan_id"], {})).get(
                "settings", {}
            )
            mature_after = (
                settings.get("minimum_history_observations", 3)
                * settings.get("scan_interval_seconds", 300)
                * 2
            )
            source_list = decoded(e["discovery_sources"], [])
            population = (
                "watchlist"
                if not source_list
                else "new"
                if age is None or age < mature_after
                else "mature_discovery"
            )
            for segment in (population, f"cohort:{e['cohort']}", f"score:{e['score'] // 10 * 10}"):
                segment_counts[segment]["evaluations"] += 1
                segment_counts[segment]["baseline_ready"] += baseline
            activity = signal.get("returning_activity")
            if activity is None:
                activity = any(
                    k in signal.get("components", {})
                    for k in ("volume_5m", "volume_1h", "transactions")
                )
            market_pass = not any(": " in str(r) for r in reasons)
            expected = market_pass and activity and sec.get("dangerous") is not True
            for key, value in {
                "expected": expected,
                "available": structure.get("available"),
                "available_when_expected": expected and structure.get("available"),
                "base": structure.get("available") and structure.get("base_detected"),
                "hl_and_base": structure.get("base_detected")
                and structure.get("higher_low_detected"),
                "hh_and_base": structure.get("base_detected")
                and structure.get("higher_high_detected"),
                "breakout_and_base": structure.get("base_detected")
                and structure.get("breakout_detected"),
                "retest_and_breakout": structure.get("breakout_detected")
                and structure.get("retest_detected"),
            }.items():
                structure_counts[key] += bool(value)
            inferred_traces.append(
                {
                    "chain": e["chain"],
                    "contract_address": e["contract_address"],
                    "baseline_ready": baseline,
                    "activity_trigger": activity,
                    "market_pass": market_pass,
                    "structure_available": bool(structure.get("available")),
                    "base_detected": bool(structure.get("base_detected")),
                    "eligible": bool(e["eligible"]),
                }
            )
        traces, all_errors, counters = [], [], defaultdict(Counter)
        for identity, item in metrics.items():
            perf = item.get("performance", {})
            for t in perf.get("candidate_traces", []):
                if t.get("timestamp", until) <= until:
                    traces.append(t | {"scan_id": identity})
            for event in perf.get("error_events", []):
                all_errors.append(event | {"scan_id": identity})
            for key in (
                "calls",
                "failures",
                "cache",
                "deferred",
                "http_statuses",
                "http_errors",
                "recovered_requests",
            ):
                counters[key].update(perf.get(key, {}))
        discovered = set()
        observations = 0
        for s in scans:
            keys = decoded(s["discovered_keys"], [])
            discovered.update(tuple(k) for k in keys)
            observations += len(keys)
        known = coverage_counts["known_presentations"]
        coverage = {
            k: rate(v, known) for k, v in coverage_counts.items() if k != "known_presentations"
        }
        coverage["presentations"] = rate(known, len(evaluations))
        coverage["segments"] = {
            k: rate(v["baseline_ready"], v["evaluations"]) for k, v in segment_counts.items()
        }
        coverage["segment_note"] = (
            "Maturity age uses first RETAINED market snapshot, not actual first discovery; "
            "watchlist requires empty current discovery sources. "
            "Legacy missing diagnostics may bias readiness."
        )
        cohorts = []
        alerts = [r for r in rows(db, "alerts") if since <= r["timestamp"] <= until]
        outcome_rows = [r for r in rows(db, "signal_outcomes") if r["observed_at"] <= until]
        sent_signals = []
        alert_lookup = {}
        for a in alerts:
            detail = decoded(states.get(f"telegram:alert:{a['id']}"), {}) or {}
            signal, token = detail.get("signal", {}), detail.get("token", {})
            saved = next((r for r in rows(db, "alert_signals") if r["alert_id"] == a["id"]), {})
            if not token and saved:
                token = {"price": saved.get("price")}
            key = cohort(
                detail.get("configuration")
                or {"preset": saved.get("preset"), "revision": saved.get("revision")},
                signal,
            )
            record = {
                "alert_id": a["id"],
                "timestamp": a["timestamp"],
                "chain": a["chain"],
                "contract_address": a["contract_address"],
                "score": a["score"],
                "delivery_status": a["delivery_status"],
                "cohort": key,
                **{d: signal.get(d, saved.get(d)) for d in DIMENSIONS[1:]},
                "stage": signal.get("status", saved.get("stage")),
                "outcomes": sampled_outcome(
                    prices[(a["chain"], a["contract_address"])],
                    a["timestamp"],
                    token.get("price"),
                    until,
                ),
            }
            record["stored_checkpoints"] = [r for r in outcome_rows if r["alert_id"] == a["id"]]
            for checkpoint in record["stored_checkpoints"]:
                horizon = record["outcomes"][str(checkpoint["horizon_hours"])]
                horizon["observed_at"] = checkpoint["observed_at"]
                horizon["return_pct"] = checkpoint["return_pct"]
                horizon["checkpoint_state"] = "observed"
            alert_lookup[a["id"]] = record
            if a["delivery_status"] == "sent":
                sent_signals.append(record)
        anchors = {}
        near_anchors = {}
        for e in evaluations:
            token, signal = payloads[e["id"]].get("token", {}), payloads[e["id"]].get("signal", {})
            identity = (e["cohort"], e["chain"], e["contract_address"])
            record = {
                "evaluation_id": e["id"],
                "timestamp": e["timestamp"],
                "cohort": e["cohort"],
                "chain": e["chain"],
                "contract_address": e["contract_address"],
                "score": e["score"],
                "eligible": bool(e["eligible"]),
                **{d: signal.get(d) for d in DIMENSIONS[1:]},
                "blockers": decoded(e["rejection_reasons"], []),
                "baseline_missing": "baseline_history" in decoded(e["missing_fields"], []),
                "features": {
                    k: token.get(k)
                    for k in ("liquidity", "volume_5m", "tx_5m", "volume_1h", "tx_1h")
                },
                "outcomes": sampled_outcome(
                    prices[(e["chain"], e["contract_address"])],
                    e["timestamp"],
                    token.get("price"),
                    until,
                ),
            }
            anchors.setdefault(identity, record)
            if e["score"] >= 50 and (
                not e["eligible"] or "score_below_threshold" in record["blockers"]
            ):
                near_anchors.setdefault(identity, record)
        calibration = {}
        for key, entries in groups.items():
            unique = {(e["chain"], e["contract_address"]) for e in entries}
            own_alerts = [a for a in sent_signals if tuple(a["cohort"]) == key]
            own_anchors = [r for (c, _, _), r in anchors.items() if c == key]
            dimension_distributions = {
                d: distribution(
                    [
                        payloads[e["id"]].get("signal", {}).get(d) if d != "score" else e[d]
                        for e in entries
                    ]
                )
                for d in DIMENSIONS
            }
            cohorts.append(
                {
                    "key": key,
                    "evaluations": len(entries),
                    "unique_tokens": len(unique),
                    "eligible_evaluations": sum(e["eligible"] for e in entries),
                    "sent_alerts": len(own_alerts),
                    "score_distributions": dimension_distributions,
                    "signal_outcomes": performance_summary(own_alerts),
                }
            )
            calibration[str(key)] = {
                d: {
                    f"{lo}-{hi}": performance_summary(
                        [r for r in own_anchors if finite(r.get(d)) and lo <= r[d] <= hi]
                    )
                    for lo, hi in (OVERALL_BANDS if d == "score" else DIMENSION_BANDS)
                }
                for d in DIMENSIONS
            }
        near = list(near_anchors.values())
        winners = [
            r
            for r in near
            if finite(r["outcomes"]["24"]["sampled_mfe_pct"])
            and r["outcomes"]["24"]["sampled_mfe_pct"] > 50
        ]
        missed_attribution = Counter(blocker for r in winners for blocker in r["blockers"])
        bad_signals = [
            r
            for r in sent_signals
            if finite(r["outcomes"]["24"]["return_pct"]) and r["outcomes"]["24"]["return_pct"] < 0
        ]
        duration = [
            s["duration_seconds"]
            for s in scans
            if s["finished"] is not None and s["finished"] <= until
        ]
        starts = sorted(s["started"] for s in scans)
        cadence = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        errors_by_category = {}
        for category in sorted({e["category"] for e in all_errors}):
            entries = [e for e in all_errors if e["category"] == category]
            errors_by_category[category] = {
                "count": len(entries),
                "share": rate(len(entries), len(all_errors)),
                "affected_scans": len({e["scan_id"] for e in entries}),
                "affected_tokens": len(
                    {
                        (e["chain"], e["contract_address"])
                        for e in entries
                        if e.get("contract_address")
                    }
                ),
                "first_at": min(e["timestamp"] for e in entries),
                "last_at": max(e["timestamp"] for e in entries),
            }
        today = (
            datetime.fromtimestamp(
                until, ZoneInfo("Asia/Ho_Chi_Minh" if timezone == "Asia/Saigon" else timezone)
            )
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        regimes = {}
        for name, cutoff in (
            ("today", today),
            ("last_3_days", until - 3 * 86400),
            ("last_7_days", until - 7 * 86400),
        ):
            selected = [r for r in snapshots_all if cutoff <= r["timestamp"] <= until]
            regimes[name] = {
                "snapshot_observations": len(selected),
                "unique_tokens": len({(r["chain"], r["contract_address"]) for r in selected}),
                "medians": {
                    k: distribution([r.get(k) for r in selected])["median"]
                    for k in ("volume_5m", "volume_1h", "liquidity", "tx_5m", "tx_1h")
                },
                "label": None,
                "note": "Enrichment-selected observation mix; nested unequal windows, not "
                "whole Solana market. No causal regime label.",
            }
        last_24 = [s for s in scans_all if until - 86400 <= s["started"] <= until]
        own24 = [e for e in rows(db, "evaluations") if e["scan_id"] in {s["id"] for s in last_24}]
        keys24 = {tuple(k) for s in last_24 for k in decoded(s["discovered_keys"], [])}
        cohort_research = {}
        for key, entries in groups.items():
            own_ids = {e["scan_id"] for e in entries}
            own_traces = [t for t in traces if t["scan_id"] in own_ids]
            own_misses = [r for r in near if r["cohort"] == key]
            own_winners = [r for r in winners if r["cohort"] == key]
            own_scans = [s for s in scans if s["id"] in own_ids]
            own_errors = [e for e in all_errors if e["scan_id"] in own_ids]
            own_signals = [r for r in sent_signals if r["cohort"] == key]
            cohort_research[str(key)] = {
                "signal_quality": performance_summary(own_signals),
                "near_miss_bands": {
                    f"{lo}-{hi}": performance_summary(
                        [r for r in own_misses if lo <= r["score"] <= hi]
                    )
                    for lo, hi in NEAR_BANDS
                },
                "sampled_missed_winners": len(own_winners),
                "overlapping_missed_winner_blockers": dict(
                    Counter(blocker for r in own_winners for blocker in r["blockers"])
                ),
                "baseline_universe": performance_summary(
                    [r for (c, _, _), r in anchors.items() if c == key]
                ),
                "scan_duration_seconds": distribution(
                    [
                        s["duration_seconds"]
                        for s in own_scans
                        if s["finished"] is not None and s["finished"] <= until
                    ]
                ),
                "budget_latency": budget_analysis(own_traces, until) if own_traces else None,
                "error_counts": dict(Counter(e["category"] for e in own_errors)),
            }
        report = {
            "audit_version": 1,
            "source": source,
            "time_window": {
                "since": since,
                "until": until,
                "timezone": timezone,
                "selection": "scan start for evaluations/scans; observation time for prices; "
                "alert time for outcomes",
            },
            "database": profile | {"bytes": database.stat().st_size},
            "scans": {
                "started": len(scans),
                "completed": sum(
                    s["finished"] is not None and s["finished"] <= until for s in scans
                ),
                "unfinished_including_current": sum(
                    s["finished"] is None or s["finished"] > until for s in scans
                ),
                "duration_seconds": distribution(duration),
                "start_spacing_seconds": distribution(cadence),
                "reported_errors": sum(s["errors"] for s in scans),
                "errors_per_scan": sum(s["errors"] for s in scans) / len(scans) if scans else None,
                "telemetry_records": sum(bool(m) for m in metrics.values()),
                **{k: dict(v) for k, v in counters.items()},
            },
            "ui_reconciliation_24h": {
                "scans_started": len(last_24),
                "scans_finished": sum(
                    s["finished"] is not None and s["finished"] <= until for s in last_24
                ),
                "mean_scan_seconds": distribution(
                    [
                        s["duration_seconds"]
                        for s in last_24
                        if s["finished"] is not None and s["finished"] <= until
                    ]
                )["mean"],
                "unique_discovered": len(keys24),
                "evaluations": len(own24),
                "market_core_pass_evaluations": sum(
                    not any(": " in str(r) for r in decoded(e["rejection_reasons"], []))
                    for e in own24
                ),
                "sent_alerts": sum(
                    a["delivery_status"] == "sent" and a["timestamp"] >= until - 86400
                    for a in alerts
                ),
                "user_ui_snapshot_time_unknown": True,
            },
            "universe": {
                "unique_discovered": len(discovered),
                "discovery_observations": observations,
                "evaluations": len(evaluations),
                "unique_evaluated": len({(e["chain"], e["contract_address"]) for e in evaluations}),
            },
            "coverage": coverage,
            "snapshot_history": snapshot_profile,
            "structure": {
                "global": rate(structure_counts["available"], known),
                "conditional": rate(
                    structure_counts["available_when_expected"], structure_counts["expected"]
                ),
                "base_among_available": rate(
                    structure_counts["base"], structure_counts["available"]
                ),
                "hl_among_base": rate(structure_counts["hl_and_base"], structure_counts["base"]),
                "hh_among_base": rate(structure_counts["hh_and_base"], structure_counts["base"]),
                "breakout_among_base": rate(
                    structure_counts["breakout_and_base"], structure_counts["base"]
                ),
                "retest_and_breakout": rate(
                    structure_counts["retest_and_breakout"], structure_counts["breakout_and_base"]
                ),
                "note": "Retest can follow prior-bar breakout without same-bar breakout; "
                "conditional legacy eligibility inferred.",
            },
            "funnel": {
                "telemetry_v2": sequential_funnel(traces) if traces else None,
                "discovery_only": sequential_funnel(
                    [t for t in traces if t.get("discovery_sources")]
                ),
                "watchlist_only": sequential_funnel(
                    [t for t in traces if not t.get("discovery_sources")]
                ),
                "by_cohort": {
                    str(key): sequential_funnel(
                        [
                            t
                            for t in traces
                            if cohort(
                                config_by_scan.get(t["scan_id"]),
                                {"score_version": t.get("score_version")},
                                require_dimensions=False,
                            )
                            == key
                        ]
                    )
                    for key in groups
                },
                "legacy_conditional_path": None,
                "legacy_note": "Marginal counters cannot identify intersection sets or deferred "
                "identities. Do not fabricate a sequential funnel.",
                "overlapping_blockers": dict(overlap),
                "candidate_traces": len(traces),
            },
            "cohorts": cohorts,
            "research_by_cohort": cohort_research,
            "signals": list(alert_lookup.values()),
            "signal_quality": performance_summary(sent_signals),
            "pooled_summary_warning": (
                "Global summaries describe collection only. Performance decisions must use "
                "research_by_cohort and score_calibration; do not compare pooled configurations."
            ),
            "near_misses": {
                "anchor_policy": "First rejected score>=50 per token/cohort; one anchor per token.",
                "bands": {
                    f"{lo}-{hi}": performance_summary([r for r in near if lo <= r["score"] <= hi])
                    for lo, hi in NEAR_BANDS
                },
                "sampled_24h_mfe_above_50pct": len(winners),
                "overlapping_winner_blockers": dict(missed_attribution),
                "missed_winner_records": winners,
            },
            "false_positives": {
                "definition": "sent alert with observed negative +24h return; not executable loss",
                "count": len(bad_signals),
                "records": bad_signals,
            },
            "score_calibration": calibration,
            "baseline_universe": performance_summary(list(anchors.values())),
            "baseline_note": "First evaluation per token/cohort, includes selected and "
            "unselected tokens; "
            "not a matched control and unobserved prices remain unknown. "
            "Evaluation/snapshot collection is selection-biased.",
            "market_regime": regimes,
            "budget_latency": budget_analysis(traces, until)
            if traces
            else {"status": "UNMEASURABLE_LEGACY", "first_enrichment_delay_seconds": None},
            "error_taxonomy": errors_by_category,
            "recommendations": [
                {
                    "priority": "P0",
                    "action": "Fix as-of outcome cutoff; preserve production until reviewed "
                    "deployment.",
                },
                {
                    "priority": "P1",
                    "action": "Capture candidate deferrals and structured errors; archive "
                    "diagnostics before seven-day pruning.",
                },
                {
                    "priority": "P2",
                    "action": "Measure across-tier fairness and outcome budget starvation "
                    "before budget changes.",
                },
                {
                    "priority": "P3",
                    "action": "Compare proactive structure and weights using walk-forward "
                    "research; no live threshold change.",
                },
            ],
            "limitations": [
                "INSUFFICIENT SAMPLE does not establish profitability, precision, "
                "expected return or edge.",
                "Sampled MFE is a lower bound and sampled MAE an upper bound on "
                "true excursions; no intrabar OHLC reconstructed.",
                "Outcome collection is endogenous to score, request budget, and token survival.",
                "LEGACY dimensional scores are unknown; no values invented or rescored.",
                "Missing terminal prices are not zero returns; missingness is "
                "potentially informative.",
                "Source timestamps are client observation times; pipeline fetch lag may remain.",
                "This process opens only the supplied audit copy in mode=ro; no "
                "migration or production API requests.",
            ],
        }
        return report
    finally:
        db.close()
