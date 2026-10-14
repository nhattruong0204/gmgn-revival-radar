"""Descriptive call-level accounting over saved performance objects.

Pure arithmetic over values already computed by ``PublicQueries._performance``. Unknown
changes are counted with their reason; they are never zero-filled, interpolated or ranked.
"""

from collections import Counter
from statistics import mean, median

SMALL_SAMPLE = 20
BINS = (
    ("Below -50%", lambda v: v < -50),
    ("-50% to below 0%", lambda v: -50 <= v < 0),
    ("Exactly 0%", lambda v: v == 0),
    ("Above 0% through 50%", lambda v: 0 < v <= 50),
    ("Above 50%", lambda v: v > 50),
)
UNKNOWN_REASONS = ("no_call_mc", "no_later_observation", "unspecified")


def sign(change):
    if change is None:
        return "unknown"
    return "positive" if change > 0 else "negative" if change < 0 else "flat"


def group_counts(key, entries):
    values = [e["perf"].change_since_call_pct for e in entries]
    known = [v for v in values if v is not None]
    return {
        "key": key,
        "calls": len(entries),
        "measurable": len(known),
        "positive": sum(v > 0 for v in known),
        "negative": sum(v < 0 for v in known),
        "flat": sum(v == 0 for v in known),
        "unknown": len(values) - len(known),
        "median_change_pct": median(known) if known else None,
    }


def revision_key(value):
    return "unknown" if value is None else str(value)


def summarize(entries, checkpoint_accounting):
    """entries: dicts with ``perf`` (CallPerformance), ``stage`` and ``revision``."""
    changes = [e["perf"].change_since_call_pct for e in entries]
    known = [v for v in changes if v is not None]
    reasons = Counter(
        e["perf"].change_reason or "unspecified"
        for e, change in zip(entries, changes, strict=True)
        if change is None
    )
    mfe = [e["perf"].mfe_pct for e in entries if e["perf"].mfe_pct is not None]
    mae = [e["perf"].mae_pct for e in entries if e["perf"].mae_pct is not None]
    stages, revisions = {}, {}
    for entry in entries:
        stages.setdefault(entry["stage"] or "UNKNOWN", []).append(entry)
        revisions.setdefault(revision_key(entry["revision"]), []).append(entry)
    ordered = sorted(revisions, key=lambda k: (k == "unknown", -int(k) if k != "unknown" else 0))
    positive = sum(v > 0 for v in known)
    return {
        "calls": len(entries),
        "measurable": len(known),
        "positive": positive,
        "negative": sum(v < 0 for v in known),
        "flat": sum(v == 0 for v in known),
        "unknown": len(entries) - len(known),
        "unknown_reasons": {name: reasons.get(name, 0) for name in UNKNOWN_REASONS},
        "positive_rate_pct": positive / len(known) * 100 if known else None,
        "median_change_pct": median(known) if known else None,
        "mean_change_pct": mean(known) if known else None,
        "distribution": [
            {"label": label, "count": sum(test(v) for v in known)} for label, test in BINS
        ],
        "mfe_known": len(mfe),
        "mfe_median_pct": median(mfe) if mfe else None,
        "mae_known": len(mae),
        "mae_median_pct": median(mae) if mae else None,
        "by_stage": [group_counts(k, v) for k, v in sorted(stages.items())],
        "by_revision": [group_counts(k, revisions[k]) for k in ordered],
        "revisions": ordered,
        "mixed_configurations": len(revisions) > 1,
        "small_sample": len(known) < SMALL_SAMPLE,
        "small_sample_threshold": SMALL_SAMPLE,
        "stale_latest": sum(e["perf"].freshness == "stale" for e in entries),
        "checkpoint_24h": checkpoint_accounting,
    }
