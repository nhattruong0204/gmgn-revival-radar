# Production database audit

Final cutoff: **2026-10-08 14:48:34 UTC**. Source: consistent online backup of
`/app/data/revival_radar.db`, mounted from `/opt/gmgn-revival-radar/data`.
Backup SHA-256: `16929eb4641ba9e4e2ed7ca2a8597b6c9f559fe5760998aae2cef67e8603a91c`.
Integrity **ok**, schema **4**, WAL enabled; no migration, VACUUM or history reset.
Final private backup size: 220,237,824 bytes (210.04 MiB).
Live source DB/WAL sizes at export: 220,114,944 /
4,169,472 bytes. All analysis used read-only copies.

| Table | Rows | Time field | Oldest UTC | Newest UTC | Table MiB | Duplicate excess |
|---|---|---|---|---|---|---|
| token_snapshots | 36,059 | timestamp | 2026-10-06 17:29:53 UTC | 2026-10-08 14:43:34 UTC | 61.85 | 0 |
| alerts | 9 | timestamp | 2026-10-06 18:19:13 UTC | 2026-10-08 09:12:24 UTC | 0.00 | 0 |
| scan_runs | 395 | started | 2026-10-07 04:03:42 UTC | 2026-10-08 14:48:32 UTC | 1.51 | 0 |
| evaluations | 31,749 | timestamp | 2026-10-07 04:03:43 UTC | 2026-10-08 14:43:34 UTC | 22.78 | 0 |
| state | 20,762 | updated_at | 2026-10-07 14:20:09 UTC | 2026-10-08 14:48:32 UTC | 112.90 | 0 |
| enrichment_cache | 40 | fetched_at | 2026-10-07 15:30:47 UTC | 2026-10-08 14:43:33 UTC | 0.39 | 0 |
| watch_state | 1,592 | first_seen | 2026-10-06 17:30:00 UTC | 2026-10-08 14:43:34 UTC | 2.09 | 0 |
| alert_signals | 9 | none | not stored | not stored | 0.00 | 0 |
| signal_outcomes | 11 | observed_at | 2026-10-07 16:12:22 UTC | 2026-10-08 14:22:25 UTC | 0.00 | 0 |

Table pages exclude index/shared overhead. Full column schema/null counts/rates,
JSON validity and scheduled versus observed timestamp checks are in private
`data/audit/latest_audit.json`. Every profiled natural key was unique; no invalid
market-number/score/eligibility domains were found. JSON was valid in all explicitly
profiled JSON columns. There were no unexpected future observed timestamps, declared
FK violations or explicit evaluation/alert-signal/outcome orphan joins.
Scheduled cache expiry and next_due values may legitimately be in the future.
The application does not enable foreign-key enforcement, so observed orphan freedom
is a measurement, not a guarantee about future writes.

Important missingness and interpretation:

- Six of nine original alert prices/caps are missing, eight of nine dimensional
  score triples are missing, and six of eleven stored checkpoint returns are NULL.
  Later prices cannot repair missing entry information without an explicit defensible
  historical source. No price or return was invented.
- 11,575 legacy evaluations lack retained presentations; that limits feature/cohort
  reconstruction. The complete known-presentation denominator is recorded separately.
- Initial snapshot columns had absent drawdown in 71 rows; most source ranks and
  optional asset classifications are NULL by design. Snapshot numeric defaults can
  conceal missing upstream fields; inspect payload/presentation availability rather
  than treating every zero as measured activity.
- Four scan finish/duration pairs are NULL: old IDs 2/99/106 and the current scan 395.
  The active pre-deployment scan completed. Historical interruptions remain untouched.
- Watch `last_polled=0` means never processed and cannot be treated as a billions-of-
  seconds latency. Last_polled also includes prefilter work; deferral counts combine
  stages/failures. Mutable watch/cache rows are not full lifecycle histories.

At the pre-deployment cutoff, snapshots spanned 2026-10-06 17:29 UTC onward, while
retained scan/evaluation rows began 2026-10-07 04:03 UTC. Historical alert timestamps
can precede retained scan metadata; no missing past scan was invented or repaired.
Historical chain rows included Solana, Robinhood, BSC, Base and Arc. Current runtime
is Solana only. Mixed historical populations are separated from current conclusions.

## Persistence and growth

Before service replacement there were 391 scans, 31,558 evaluations, nine alerts and
35,939 snapshots. The first post-deployment backup had 392 scans, 31,622 evaluations,
nine alerts and 35,979 snapshots. Every original ID row in scans/evaluations/alerts
was verified still present; snapshot count increased. Both protected configuration
files retained identical hashes, and schema stayed at 4.

Multiple backup sizes provide a short physical-growth observation, not a reliable
steady-state daily growth rate. The pre/first-post source DB+WAL increased by about
1.27 MB in 8.24 minutes; simple extrapolation is roughly 222 MB/day. New candidate
telemetry changes storage demand, WAL checkpoints/page reuse affect sizes, and
seven-day pruning has not reached steady state. The durable growth metric remains
NULL pending a longer measurement period. State/presentations account for over half
the initial table pages; token snapshots about 61.5 MiB and evaluations about 22.6 MiB.

Evaluations, scan/config/presentation/metric diagnostics prune after seven days;
snapshots and alerts/outcomes do not. Caches overwrite candle/security history;
expired watch records are pruned. This cannot support exact full-universe replay.
No pruning schedule or retention setting was changed by this audit. Archive before
pruning, measure DB+WAL+freelist/page sizes across days, then set an explicit disk budget.

## Reproduction and UI contract

```bash
python3 scripts/audit_database.py \
  --database data/audit/final/production_snapshot.db \
  --manifest data/audit/final/manifest.json \
  --source production --since 0 --output-dir data/audit
```

The CLI verifies the manifest checksum, defaults until to backup completion, and
never imports the application's migration connector. Scan/evaluation windows use
scan start; alert windows use reservation time; outcome values require observed_at
at or before the cutoff. Discovery deduplicates chain/address and excludes watchlist;
evaluation counts include repeated observations. UI core passes are full eligibility,
market passes are a different narrower set of checks, and blocker counts overlap.
VPS and local executions against the same final copy matched exactly before adding
deployment metadata. See [FULL_AUDIT.md](FULL_AUDIT.md) for same-cutoff UI numbers,
cohort isolation and limits on performance claims.
