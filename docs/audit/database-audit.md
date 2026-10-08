# Database integrity and measurement audit

Status: **PRODUCTION EVIDENCE PENDING**. The production file has not been copied or
queried. No database row count or integrity claim below refers to the VPS.

The current implementation uses schema version 4 and nine tables. `connect()` enables
WAL, serializes additive migrations in a transaction, and rejects newer schemas.
SQLite foreign-key declarations exist on research relationships, but the production
connector does not enable `PRAGMA foreign_keys=ON`. Orphan freedom therefore requires
explicit validation rather than relying on declarations. The audit tool never calls
that connector: it opens the supplied copy with `mode=ro` and `query_only=ON`.

| Table | Intended grain | Persistence/measurement issue |
|---|---|---|
| token_snapshots | chain/address/client observation timestamp | Only market-enriched candidates persist; source fallback/provenance need care; no pruning |
| evaluations | scan/chain/address | Includes prefilter rejects, excludes market-budget deferrals; seven-day pruning |
| scan_runs | scan ID | Null finish can be current/interrupted; final counters absent on older interrupted runs |
| alerts | reservation ID | Delivery states distinguish sent/failed/pending/unknown; no pruning |
| alert_signals | alert ID | Alert-time values; no score-version column, recover version only from saved presentation |
| signal_outcomes | alert/horizon | First checkpoint within one-hour grace; missing prices stay NULL |
| enrichment_cache | kind/chain/address | Overwritten; a cache is not immutable historical security/candles |
| watch_state | chain/address | Last state only; deferral counter mixes stages/errors; last_polled can mean prefilter evaluation |
| state | key | Config/presentation/metric values; scan/evaluation states pruned with diagnostics |

## Reproducible checks

`python3 scripts/audit_database.py` profiles every available table, not only this list:
row/column counts; natural-key duplicate excess; null counts/rates; min/max relevant
times and scheduled versus unexpected future times; invalid JSON; table page bytes
where `dbstat` exists; declared FK violations and explicit orphan joins; invalid
market numbers and evaluation domains; timestamp-gap and snapshots/token distributions;
schema/journal/integrity; scan duration percentiles; cohort field/dimension availability.
Tables not present in legacy copies are left absent. Analysis of a schema-1 test file
preserves its bytes and user_version. No migration is run by the audit CLI.

A point-in-time backup alone cannot establish bytes/day: compare repeated dated
DB+WAL+freelist/page measurements. Table page bytes exclude shared/index overhead
unless separately attributed. A finite filtered research window does not turn total
file size into storage growth. `growth_bytes_per_day` remains NULL until measured.

## UI reconciliation contract

The current `Repository.health()` selects scans by **start time** in `[since, until]`;
evaluations join those scans. Discoveries deduplicate chain/address from completed
scan metadata and exclude watchlist. Evaluation counts include repeated token
observations; blocker counts overlap. Alerts select observation/reservation timestamp,
not delivery time. Main health covers 24h; outcome summary covers seven days.
Coverage uses saved presentations as its known denominator, not successful endpoint
requests. Interrupted legacy scan discovery/metrics may never have been finalized.

Reconcile the UI and analyzer with the same UTC epoch cutoff and 24h start-window,
then segment revisions and identify active scans and missing presentation records.
The request's approximate counters have no exact snapshot timestamp, so discrepancies
cannot be attributed to a UI bug merely by comparing today's later export.
The local P0 fix filters historical outcomes by their actual observation cutoff.

## Synthetic validation only

The isolated demo created 13 snapshots (including fixture history), 3 evaluations,
1 scan, 0 alerts, 0 alert_signals, 0 outcomes, 2 cache entries, 3 watch entries and
6 state rows. Integrity was `ok`. It processed three fixture scenarios with one
potential alert, zero deliveries and zero errors. Fixture timestamps use an independent
synthetic clock; the reproducible analysis explicitly uses `--since 0 --until 1800000001`.
These numbers prove neither production coverage nor statistical performance.

## Next required evidence

Obtain the [consistent private export](production-access.md). Inspect all returned
profiles and malformed JSON/domain defects before relying on outcomes. Compare
stored checkpoints to snapshot paths, alert-time prices to saved presentations,
scan counters to evaluation counts, logs to bounded scan intervals, and discovery
identity sets to trace membership. Keep expired/starved/unobserved candidates marked
missing rather than deleting them from the denominator.
