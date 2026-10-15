"""Markdown signal-quality report. Every rate states its denominator; unknowns are excluded."""

from collections import defaultdict

from revival_radar.research import data
from revival_radar.research.data import (
    COHORT_LABELS,
    HORIZONS,
    LATE_RISE_PCT,
    UNIVERSE,
    Window,
    iso,
)
from revival_radar.research.stats import avg, is_known, known, med, spearman

CORRELATION_FEATURES = [
    ("Overall score", "score"),
    ("Setup score", "setup_score"),
    ("Trigger score", "trigger_score"),
    ("Confirmation score", "confirmation_score"),
    ("Base duration (hours)", "base_duration_hours"),
    ("Bundler ratio", "bundler_ratio"),
    ("1h price change at call", "price_change_1h_at_call"),
    ("5m volume acceleration", "volume_acceleration_5m"),
]


def pct(value, digits=1):
    return "unknown" if not is_known(value) else f"{value:+.{digits}f}%"


def plain_pct(value, digits=1):
    return "unknown" if not is_known(value) else f"{value:.{digits}f}%"


def rate(count, denominator):
    if not denominator:
        return "n/a (0 known)"
    return f"{count:,} of {denominator:,} ({count / denominator * 100:.1f}%)"


def row(*cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def table(header, rows, align=None):
    align = align or ["---"] * len(header)
    return "\n".join([row(*header), row(*align), *(row(*r) for r in rows)])


def outcome_stats(calls):
    changes = known(c["change_since_call_pct"] for c in calls)
    mfe = known(c["mfe_pct"] for c in calls)
    mae = known(c["mae_pct"] for c in calls)
    return {
        "n": len(changes),
        "positive": sum(v > 0 for v in changes),
        "negative": sum(v < 0 for v in changes),
        "flat": sum(v == 0 for v in changes),
        "median": med(changes),
        "mean": avg(changes),
        "median_mfe": med(mfe),
        "median_mae": med(mae),
        "n_mfe": len(mfe),
        "mfe25": sum(v >= 25 for v in mfe),
        "mfe50": sum(v >= 50 for v in mfe),
    }


def funnel_section(days, summary):
    rows = [
        ("Scans completed (finished)", summary["scans_completed"]),
        ("Scans that reported errors", summary["scans_with_source_error"]),
        ("Evaluations", f"{summary['evaluations']:,}"),
        ("Distinct tokens evaluated", f"{summary['distinct_tokens']:,}"),
        (
            "Eligible evaluations",
            rate(summary["eligible"], summary["evaluations"]),
        ),
        (
            "Evaluations with score >= 60",
            rate(summary["score_ge_60"], summary["evaluations"]),
        ),
        (
            "... of which eligible",
            rate(summary["score_ge_60_eligible"], summary["score_ge_60"]),
        ),
        ("Potential alerts (sum over scans)", summary["potential_alerts"]),
        ("Sent calls", summary["sent_calls"]),
        ("Distinct tokens called", summary["distinct_tokens_called"]),
    ]
    daily = [
        (
            d["date"],
            f"{d['evaluations']:,}",
            f"{d['distinct_tokens']:,}",
            f"{d['eligible']:,}",
            f"{d['score_lt_40']:,}",
            f"{d['score_40_59']:,}",
            f"{d['score_ge_60']:,}",
            f"{d['potential_alerts']:,}",
            f"{d['sent_calls']:,}",
        )
        for d in days
    ]
    return "\n\n".join(
        [
            table(["Stage", "Count"], rows, ["---", "---:"]),
            "Daily (UTC):",
            table(
                [
                    "Date",
                    "Evaluations",
                    "Tokens",
                    "Eligible",
                    "<40",
                    "40-59",
                    ">=60",
                    "Potential alerts",
                    "Sent",
                ],
                daily,
                ["---"] + ["---:"] * 8,
            ),  # fmt: skip
        ]
    )


def outcomes_section(calls):
    s = outcome_stats(calls)
    unknown = len(calls) - s["n"]
    if not calls:
        return "No delivered calls in the window."
    reasons = defaultdict(int)
    for c in calls:
        if not is_known(c["change_since_call_pct"]):
            reasons[c["change_reason"] or "unknown"] += 1
    note = ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())) or "none"
    rows = [
        ("Calls in window", len(calls)),
        (
            "Calls with a known change since call",
            f"{s['n']} (unknown, excluded: {unknown}; {note})",
        ),
        ("Positive since call", rate(s["positive"], s["n"])),
        ("Negative since call", rate(s["negative"], s["n"])),
        ("Exactly 0%", rate(s["flat"], s["n"])),
        ("Median change since call", pct(s["median"])),
        ("Mean change since call", pct(s["mean"])),
        ("Median observed MFE", f"{pct(s['median_mfe'])} (n={s['n_mfe']})"),
        ("Median observed MAE", f"{pct(s['median_mae'])} (n={s['n_mfe']})"),
        ("Calls with MFE >= +25%", rate(s["mfe25"], s["n_mfe"])),
        ("Calls with MFE >= +50%", rate(s["mfe50"], s["n_mfe"])),
    ]
    return table(["Metric", "Value"], rows)


def cohort_section(samples):
    grouped = defaultdict(list)
    for s in samples:
        grouped[(s["cohort"], s["horizon_hours"])].append(s)
    rows = []
    for cohort in COHORT_LABELS:
        for hours in HORIZONS:
            group = grouped.get((cohort, hours), [])
            if not group:
                continue
            coverage = defaultdict(int)
            for s in group:
                coverage[s["coverage"]] += 1
            ok = [s for s in group if s["coverage"] == "ok"]
            changes = [s["change_pct"] for s in ok]
            cov = " / ".join(f"{coverage[k]}" for k in ("ok", "pending", "missing", "no_base"))
            rows.append(
                (
                    COHORT_LABELS[cohort],
                    f"+{hours}h",
                    len(ok),
                    cov,
                    pct(med(changes)),
                    pct(avg(changes)),
                    rate(sum(v > 0 for v in changes), len(changes)),
                    pct(med(s["mfe_pct"] for s in ok)),
                    pct(med(s["mae_pct"] for s in ok)),
                    rate(sum(s["mae_pct"] <= -30 for s in ok), len(ok)),
                )
            )
    if not rows:
        return "No evaluations in the baseline window."
    return "\n\n".join(
        [
            table(
                [
                    "Cohort",
                    "Horizon",
                    "n (ok)",
                    "Coverage ok / pending / missing / no_base",
                    "Median",
                    "Mean",
                    "Positive",
                    "Median MFE",
                    "Median MAE",
                    "MAE <= -30%",
                ],
                rows,
                ["---", "---", "---:", "---", "---:", "---:", "---", "---:", "---:", "---"],
            ),  # fmt: skip
            "**Survivorship and coverage bias.** Samples exist only while the scanner kept "
            "saving snapshots for a token. Tokens that fall out of discovery (most "
            "ineligible tokens) stop being tracked, so their later outcomes are `missing` and "
            "are excluded rather than counted as losses or zeros; the surviving subset can "
            "bias both tails. Cohorts are not independent: the same token appears in several "
            "6h buckets and cohorts, and forward windows overlap, so effective sample size is "
            "smaller than n. All cohorts share one market regime.",
        ]
    )


def correlation_section(calls):
    target = [c["change_since_call_pct"] for c in calls]
    rows = []
    for label, key in CORRELATION_FEATURES:
        rho, n = spearman([c[key] for c in calls], target)
        rows.append((label, n, "unknown (n < 3 or constant)" if rho is None else f"{rho:+.2f}"))
    return "\n\n".join(
        [
            table(
                ["Feature", "n (pairs with both known)", "Spearman rho vs change since call"],
                rows,
                ["---", "---:", "---:"],
            ),
            "These are hypotheses, not findings: calls overlap heavily on a few tokens, have "
            "outcome windows of different length, and come from one market regime. Pairs "
            "with an unknown value on either side are dropped, never counted as 0.",
        ]
    )


def first_repeat_section(calls):
    first = [c for c in calls if c["token_call_index"] == 1]
    repeat = [c for c in calls if (c["token_call_index"] or 0) > 1]
    hi = [c for c in calls if is_known(c["score"]) and c["score"] >= 50]
    lo = [c for c in calls if is_known(c["score"]) and c["score"] < 50]
    rows = []
    for label, group in (
        ("First call on a token", first),
        ("Repeat call on a token", repeat),
        ("Score >= 50", hi),
        ("Score < 50", lo),
    ):
        s = outcome_stats(group)
        rows.append(
            (
                label,
                len(group),
                s["n"],
                pct(s["median"]),
                rate(s["positive"], s["n"]),
            )
        )
    return "\n\n".join(
        [
            table(
                ["Group", "Calls", "n with known change", "Median change", "Positive"],
                rows,
                ["---", "---:", "---:", "---:", "---"],
            ),
            "A repeat call has a shorter elapsed time on average, so first-versus-repeat is "
            "confounded with horizon length. First/repeat counts every delivered call up to "
            "the cutoff, including those before the window.",
        ]
    )


def missed_section(universe):
    u = UNIVERSE
    called = [r for r in universe if r["called"]]
    other = [r for r in universe if not r["called"]]
    head = (
        f"Revival universe: first observation with market cap >= ${u['min_market_cap']:,.0f}, "
        f"drawdown from ATH >= {u['min_drawdown'] * 100:.0f}%, token age >= {u['min_age_days']} "
        f"days, tracked >= {u['min_tracked_h']}h. Tokens with unknown drawdown or age are "
        f"excluded. {len(universe)} tokens qualify."
    )
    rows = []
    for label, group in (("Called", called), ("Not called", other)):
        mfe = [r["mfe_24h_pct"] for r in group]
        rows.append(
            (
                label,
                len(group),
                pct(med(mfe)),
                rate(sum(v >= 50 for v in mfe), len(mfe)),
                rate(sum(v >= 100 for v in mfe), len(mfe)),
            )
        )
    top = [
        (
            (r["symbol"] or "?")[:12],
            r["contract_address"][:6],
            f"${r['market_cap_first'] / 1e6:.2f}M",
            plain_pct(r["drawdown_from_ath"] * 100, 0),
            pct(r["mfe_24h_pct"], 0),
            f"{r['hours_to_peak']:.1f}h",
            "unknown" if r["max_score_to_peak"] is None else r["max_score_to_peak"],
            {True: "yes", False: "no", None: "unknown"}[r["eligible_by_peak"]],
            ", ".join(f"{k} ({v})" for k, v in r["top_rejection_reasons"]) or "none recorded",
        )
        for r in other[:10]
    ]
    parts = [
        head,
        table(
            ["Group", "Tokens", "Median 24h MFE", "MFE >= +50%", "MFE >= +100%"],
            rows,
            ["---", "---:", "---:", "---", "---"],
        ),
    ]
    if top:
        parts += [
            "Top missed movers (not called; 24h MFE from first observation):",
            table(
                [
                    "Token",
                    "Address",
                    "MC at first obs",
                    "Drawdown",
                    "24h MFE",
                    "To peak",
                    "Max score",
                    "Eligible",
                    "Top rejection reasons (evaluations)",
                ],
                top,
            ),  # fmt: skip
        ]
    parts.append(
        "Very large raw movers are often brand-new low-cap launches outside this universe; "
        "missing them is not evidence of a scoring failure."
    )
    return "\n\n".join(parts)


def late_section(calls):
    measured = [c for c in calls if is_known(c["runup_24h_before_call_pct"])]
    late = [c for c in measured if c["runup_24h_before_call_pct"] >= LATE_RISE_PCT]
    head = (
        f"Late call: within the 24h before the call the token's saved market cap rose at "
        f"least +{LATE_RISE_PCT:.0f}% from an earlier low (call market cap included). "
        f"{rate(len(late), len(measured))} of calls with saved pre-call snapshots "
        f"({len(calls) - len(measured)} without pre-call snapshots, excluded)."
    )
    if not late:
        return head
    rows = [
        (
            c["call_id"],
            c["symbol"] or "?",
            c["called_at"],
            pct(c["runup_24h_before_call_pct"], 0),
            pct(c["rise_24h_before_call_pct"], 0),
            pct(c["change_since_call_pct"]),
        )
        for c in late
    ]
    return (
        head
        + "\n\n"
        + table(
            [
                "Call",
                "Token",
                "Called at (UTC)",
                "Run-up in prior 24h",
                "Rise to call from low",
                "Change since call",
            ],
            rows,
            ["---:", "---", "---", "---:", "---:", "---:"],
        )
    )


NOT_SUPPORTED = """\
- Nothing here establishes that any score, stage or component predicts success, or that a
  different weight set or threshold would be better. Do not tune on one week of data.
- Results are market-cap observations from ~5-minute saved snapshots. They are not executable
  returns: fees, slippage, liquidity depth, latency and position sizing are ignored.
- Samples are small, overlapping (the same token is called and sampled repeatedly), taken in
  one market regime, and have outcome windows of different length. No significance is claimed.
- The cohort baseline suffers survivorship and coverage bias (see above); a cohort with many
  `missing` samples is not comparable to one with few.
- Rank correlations are exploratory hypotheses with the stated n; they are not tested
  relationships and make no causal claim.
- Market-cap basis (circulating versus fully diluted) is unverified for rank-sourced rows
  (docs/performance-metrics.md); ATH-based figures use the provider's basis.
- "Missed" and "late" are descriptive labels over saved data. They do not say a missed token
  should have been called, and nothing here is a forecast, a safety rating or investment advice."""


def build_report(db, window: Window) -> str:
    version = data.require_schema(db)
    calls = data.call_table(db, window)
    days, summary = data.funnel(db, window)
    samples = data.cohort_samples(db, window)
    universe = data.revival_universe(db, window)
    tokens = len({(c["chain"], c["contract_address"]) for c in calls})
    parts = [
        "# Signal-quality report",
        f"Window: {iso(window.since)} to {iso(window.until)} (UTC). Observations are read up to "
        f"the end of the window. Cohort and universe sample start: {iso(window.baseline_since)}. "
        f"Schema version {version}.",
        f"> **Sample warning.** {len(calls)} calls on {tokens} tokens. Calls overlap on a few "
        "tokens, outcome windows differ in length, and the window is one market regime. "
        "Treat everything below as descriptive, not statistically established.",
        "## 1. Funnel\n\n" + funnel_section(days, summary),
        "## 2. Call outcomes (observed market cap)\n\n" + outcomes_section(calls),
        "## 3. Cohort baseline (forward market-cap change)\n\n" + cohort_section(samples),
        "## 4. Rank correlations with change since call (hypotheses)\n\n"
        + correlation_section(calls),
        "## 5. First versus repeat calls\n\n" + first_repeat_section(calls),
        "## 6. Missed movers in the revival universe\n\n" + missed_section(universe),
        "## 7. Late calls\n\n" + late_section(calls),
        "## 8. What this does not support\n\n" + NOT_SUPPORTED,
    ]
    return "\n\n".join(parts) + "\n"
