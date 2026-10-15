"""CSV export and manifest. Unknown values are written as an empty cell, never 0."""

import csv
import hashlib
import json
import subprocess
from pathlib import Path

from revival_radar.research import data
from revival_radar.research.data import Window, iso

CALL_COLUMNS = [
    "call_id", "chain", "contract_address", "symbol", "called_at", "called_at_epoch",
    "call_market_cap", "call_price", "stage", "score", "setup_score", "trigger_score",
    "confirmation_score", "preset", "revision", "score_version", "latest_market_cap",
    "latest_observed_at", "change_since_call_pct", "change_reason", "mfe_pct", "mae_pct",
    "observations", "max_gap_seconds", "basis", "token_call_index", "token_call_count",
    "rise_24h_before_call_pct", "runup_24h_before_call_pct", "base_duration_hours", "bundler_ratio",
    "price_change_1h_at_call", "volume_acceleration_5m",
]  # fmt: skip
CHECKPOINT_COLUMNS = [
    "call_id", "chain", "contract_address", "symbol", "called_at", "horizon_hours", "state",
    "reason", "due_at", "observed_at", "initial_price", "checkpoint_price",
    "checkpoint_market_cap", "price_change_pct", "score", "stage", "score_version", "origin",
]  # fmt: skip
FUNNEL_COLUMNS = [
    "date", "evaluations", "distinct_tokens", "eligible", "score_lt_40", "score_40_59",
    "score_ge_60", "potential_alerts", "sent_calls",
]  # fmt: skip
COHORT_COLUMNS = [
    "cohort", "chain", "contract_address", "symbol", "sampled_at", "bucket_start", "score",
    "eligible", "horizon_hours", "coverage", "base_market_cap", "end_market_cap", "change_pct",
    "mfe_pct", "mae_pct",
]  # fmt: skip

DEFINITIONS = {
    "unknown": "An empty CSV cell means unknown (missing, non-positive or non-finite); "
    "it is never 0. change_reason says why a headline figure is unknown.",
    "call": "A delivered alert (delivery_status='sent') with timestamp in [since, until].",
    "change_since_call_pct": "(latest saved market cap / market cap frozen with the call - 1) "
    "* 100, over snapshots in (call, until]. Same-basis snapshots only when the call's "
    "market-cap source is known (docs/performance-metrics.md).",
    "mfe_pct / mae_pct": "Highest / lowest saved market cap after the call relative to the "
    "call market cap. Observed on ~5-minute snapshots, not intrabar.",
    "checkpoints": "Saved price-change checkpoints from the shared track-record accounting "
    "(+1h/+6h/+24h/+72h); price change, not market-cap change.",
    "funnel": "Daily (UTC) counts of saved evaluations; potential_alerts is the sum of "
    "scan_runs.potential_alerts for scans started that day; sent_calls counts sent alerts.",
    "cohorts": "Evaluations since baseline_since, one sample per token, cohort and 6h bucket "
    "(first evaluation in the bucket). A: within 300s of a sent call; E: eligible, score >= 60, "
    "not called; B: eligible, 40-59; C: eligible, < 40; D: ineligible. Forward market-cap "
    "change at +6h/+24h from saved snapshots. coverage: ok; no_base (no snapshot within 600s "
    "of the sample); pending (horizon after until); missing (no snapshot within 1h after the "
    "horizon). Only ok samples are summarised.",
    "rise_24h_before_call_pct": "Call market cap relative to the lowest saved market cap in "
    "the 24h up to the call; unknown when no snapshot exists in that window.",
    "runup_24h_before_call_pct": "Largest rise of any saved market cap (the call included) over "
    "an earlier low within the 24h up to the call; the report's late-call criterion.",
}


def fmt(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return value


def write_csv(path: Path, columns, rows) -> int:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([fmt(row.get(c)) for c in columns])
    return len(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def quick_check(db) -> str:
    return "; ".join(r[0] for r in db.execute("PRAGMA quick_check"))


def export(db, db_path: Path, window: Window, out_dir: Path, *, hash_input=True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    version = data.require_schema(db)
    calls = data.call_table(db, window)
    checkpoints = data.checkpoint_table(db, window)
    days, summary = data.funnel(db, window)
    samples = data.cohort_samples(db, window)
    counts = {
        "calls.csv": write_csv(out_dir / "calls.csv", CALL_COLUMNS, calls),
        "checkpoints.csv": write_csv(out_dir / "checkpoints.csv", CHECKPOINT_COLUMNS, checkpoints),
        "funnel.csv": write_csv(out_dir / "funnel.csv", FUNNEL_COLUMNS, days),
        "cohorts.csv": write_csv(out_dir / "cohorts.csv", COHORT_COLUMNS, samples),
    }
    manifest = {
        "inputs": {
            "database": str(db_path),
            "database_bytes": db_path.stat().st_size,
            "database_sha256": sha256(db_path) if hash_input else None,
            "opened": "mode=ro",
        },
        "quick_check": quick_check(db),
        "schema_version": version,
        "code_schema_version": data.SCHEMA_VERSION,
        "code_version": git_sha(),
        "cutoffs": {
            "since": window.since,
            "since_iso": iso(window.since),
            "until": window.until,
            "until_iso": iso(window.until),
            "baseline_since": window.baseline_since,
            "baseline_since_iso": iso(window.baseline_since),
            "note": "until is also the observation cutoff: no snapshot after it is read.",
        },
        "row_counts": counts,
        "funnel_summary": summary,
        "outputs": {name: sha256(out_dir / name) for name in counts},
        "definitions": DEFINITIONS,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
