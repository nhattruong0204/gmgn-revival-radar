# Post-alert outcome tracking

Schema 4 adds `alert_signals` and `signal_outcomes`. Existing alerts, snapshot bytes,
Telegram presentation and cooldown semantics remain intact. Back up the running DB
with `scripts/backup_database.py` before upgrading; retain the backup for rollback.
Older code refuses the newer schema, so rolling back code requires the pre-upgrade DB.

Alert reservation and alert-time data are one transaction. `alerts.timestamp` is the
source observation time used by the existing reservation/cooldown contract;
`alert_signals` stores price, market cap, overall score, Setup, Trigger, Confirmation,
stage, preset and revision. Only confirmed `sent` deliveries enter outcome tracking.
Dry runs, failed, pending and uncertain deliveries do not create tracked observations.

For each alert, checkpoints are +1h, +6h, +24h and +72h relative to that saved time.
The first **fresh market observation** within `[due_at, due_at + 1h]` is stored with
its actual observation time, later price/market cap, and percentage price return.
At least price or market cap must be available. If both are missing, retry while the
window is open. If price is missing or the alert-time price is zero/unknown, the return
is NULL; a market-cap-only checkpoint can still be recorded. Return percentages are
finite and calculated as `(later_price / initial_price - 1) * 100`.

A primary key `(alert_id, horizon_hours)` makes the first usable checkpoint immutable
and prevents duplicate observations across scans, processes and restarts. Several
alerts for one token can reuse one market observation. No interpolation, retrospective
price reconstruction or assigning one late snapshot to missed earlier checkpoints.

Normal enriched market snapshots are reused first. After the main candidate pipeline,
remaining market budget may poll outstanding checkpoints, independently of ranking
membership or the 24h watchlist lifetime. Only currently enabled chains are polled.
These reads use the same GMGN adapter, shared HTTP pacing and retries, and consume
`MAX_MARKET_ENRICH_PER_SCAN`; no additional security/candle calls are required.
Failures remain retryable while the window is open and are visible in scan metrics.
Tokens needing extra polling are ordered by earliest checkpoint due time. Pipeline
candidates take priority; sustained budget exhaustion can therefore miss checkpoints.

Downtime, a disabled chain, unavailable market data, or an exhausted request budget
can leave a checkpoint missing. Once its +1h grace window passes, Health labels it
**Missed** rather than fabricating a result. Before that it is **Pending**, including
future checkpoints. Pending counters are per horizon, not scheduled request counts.

**Health → Outcomes** summarizes confirmed alerts from the last seven days, even when
the main Health view covers only 24h. It shows observations, returns available, median
return, pending and missed checks. Median includes only known price returns; unknown
returns are not counted as zero. This sample is subject to missing-data and alert
selection bias and is not a trading-performance or backtest claim.

Migration can recover historical alert-time data only from saved alert presentation
state. Old alerts lacking that state retain their known overall score and timestamp;
other fields remain unknown. There is no rescore and no historical outcome backfill.
Diagnostic pruning removes old scans/evaluations but retains alerts, alert-time data,
alert presentation and outcomes, matching existing alert retention.
