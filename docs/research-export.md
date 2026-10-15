# Research export and signal-quality report

`python -m revival_radar.research` packages the audit prototypes
(`docs/audit/v2-analysis/`) into a reproducible command line (issue #71). It reads
saved data only: no provider request, no write, no scanner import beyond shared pure
definitions. Figures are research observations, not forecasts or executable returns
(see [performance metrics](performance-metrics.md)).

## Run it on a copy

The tool works on a **copy** of the scanner database and opens it with SQLite
`mode=ro` (plus `PRAGMA query_only`). Make a consistent copy without touching the live
file's contents:

```bash
sqlite3 data/revival_radar.db "VACUUM INTO '/path/outside/repo/research-copy.db'"
```

`VACUUM INTO` writes a rollback-journal file, so reading it creates no `-wal` or `-shm`
sidecars. A plain file copy of a WAL database also works, but SQLite may create
sidecar files next to it; the database file itself is never modified.

The tool **refuses** a path that resolves (including symlinks and hard links) to the
configured live database (`DATABASE_PATH`, `.env`, or `data/revival_radar.db`) unless
`--i-know-this-is-a-copy` is passed. Keep outputs outside the repository; they are
derived from private data and must not be committed.

```bash
python -m revival_radar.research export --db copy.db \
  --since 2026-10-07T13:36:00Z --until 2026-10-14T13:36:00Z --out /path/outside/repo/export
python -m revival_radar.research report --db copy.db \
  --since 2026-10-07T13:36:00Z --until 2026-10-14T13:36:00Z --out report.md
```

Times are ISO-8601 (`Z`, offset, or naive meaning UTC) or epoch seconds. `--until` is
also the **observation cutoff**: no snapshot after it is read, so a re-run on a later
copy reproduces the same numbers. `--baseline-since` (default `--since`) sets where the
cohort baseline and revival-universe samples start; the audit used the start of the
threshold-40 era (`2026-10-09T15:00:00Z`).

## `export`

Writes to `--out`:

| File | Content |
|---|---|
| `calls.csv` | one row per delivered call: identity, call market cap/price, stage, score, setup/trigger/confirmation, preset, revision, score version, latest market cap/time, change since call, `change_reason`, MFE/MAE, observations, max gap, basis, token call index/count, pre-call rise and run-up, and the features used by the report (base duration, bundler ratio, 1h price change, 5m volume acceleration) |
| `checkpoints.csv` | +1h/+6h/+24h/+72h price-change checkpoints per call, from the shared track-record accounting (`state` is `return`, `pending`, `missed`, `missing_price` or `unresolved`) |
| `funnel.csv` | per UTC day: evaluations, distinct tokens, eligible, score buckets (<40, 40-59, >=60), potential alerts, sent calls |
| `cohorts.csv` | forward +6h/+24h market-cap change samples for cohorts A-E with a `coverage` status per sample |
| `manifest.json` | inputs (path, size, SHA-256), `PRAGMA quick_check`, schema version, row counts, cutoffs, git SHA when available, output hashes and the definitions |

**An empty cell means unknown, never 0.** `change_reason` gives the reason a headline
figure is unknown (`no_call_mc`, `no_later_observation`).

### Cohort sampling

Evaluations since `--baseline-since` are classified, then one sample is kept per token,
cohort and 6-hour bucket (the first evaluation in the bucket):

| Cohort | Rule |
|---|---|
| A | within 300 s of a sent call on that token |
| E | eligible, score >= 60, not called |
| B | eligible, score 40-59, not called |
| C | eligible, score < 40 |
| D | ineligible (failed core filters) |

Forward change is measured from the saved snapshot nearest the sample (within 600 s)
to the first snapshot at or after the horizon. `coverage` is `ok`, `no_base` (no
snapshot near the sample), `pending` (horizon after `--until`) or `missing` (no
snapshot within one hour after the horizon). Only `ok` samples are summarised.

## `report`

Markdown with: funnel; call outcomes (positive/negative, median/mean, MFE/MAE,
MFE >= 25% / 50% counts); the cohort baseline with coverage and a survivorship note;
Spearman rank correlations of component scores and selected features with change since
call (n shown, labelled hypotheses; tied values receive average ranks); first versus
repeat calls; top missed movers in the revival universe (market cap >= $300K, drawdown
>= 40%, age >= 3 days, tracked >= 6 h); late calls (the token's saved market cap rose
>= 50% from an earlier low in the 24 h before the call); and a "what this does not
support" section.

Every rate prints its denominator. Unknowns are excluded from medians, rates and
correlations and the excluded count is shown. Samples are small and overlapping and come
from one market regime; nothing in the report is evidence that a score predicts success.

## Shared definitions

Call performance (`change_since_call_pct`, MFE/MAE, basis, observations, max gap) is
computed by `revival_radar.analysis.call_metrics.market_performance`, the same pure
function the public API's `PublicQueries._performance` now calls, so the two cannot
drift. `scripts/verify_call_parity.py` deliberately does **not** use it: it recomputes
from raw rows as an independent check, and a test asserts the export agrees with it.
Checkpoints reuse `analysis.track_record.checkpoint_rows`.

## Differences from the audit prototypes

- Spearman uses average ranks for ties. The prototype ranked ties in row order, which
  changes coefficients for integer scores with many ties.
- The window and the observation cutoff are explicit (`--since`, `--until`); the
  prototypes used the end of the data and hard-coded dates.
- Cohort E (eligible, score >= 60, not called) is kept as in the prototype.
- Calls are delivered alerts only; call performance uses the same-basis rule of
  `docs/performance-metrics.md`.

## `replay`

`python -m revival_radar.research replay` re-scores saved evaluations under a candidate
configuration, simulates the publish decision and compares it with the in-force baseline and
the calls actually published, labelling input fidelity (faithful or partial) and supporting
a walk-forward `--split`. It shares the `mode=ro` and live-database refusal above. See
[replay](replay.md) for the retention policy it depends on, measured storage and validation.
