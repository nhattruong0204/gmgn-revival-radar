# Backfill and backtest readiness

**Current assessment: PARTIAL.** The repo has offline fixtures and captured-response
benchmarks, not an exact historical full-universe trading replay engine. The new
read-only audit CLI is descriptive research, not a backtest. Production retention and availability were verified against the VPS backups: about
45h of market observations, 395 retained scans, 31,749 evaluations, nine sent alerts
and eleven stored checkpoints. Six alert entry prices are absent; only one alert
has current dimensional scores. This remains insufficient for exact historical replay.

| Input | Can reconstruct now? | Limitation |
|---|---|---|
| Stored market snapshots | Observed values at client timestamp | Only enriched survivors; ranking fallback not tagged per field; request availability time missing |
| Saved evaluation presentations | Exact retained result/config/feature view | Pruned after seven days; legacy dimensions unknown |
| Scan config snapshots | Retained immutable settings/revision | Diagnostic retention; code SHA/API contract not attached |
| Ranking membership/ATH/security | Values present in retained payloads | Not complete historical universe or server availability-time history |
| Historical K-lines | Current cache window only, plus benchmark fixture | Overwritten; full closed bars/availability not archived |
| Alerts/checkpoints | Original price/signal and 1h/6h/24h/72h when collected | Shared budget, off-ranking selection, first cap-only checkpoint and downtime can censor prices |
| Deferred identities/latency | New prospective candidate traces | Legacy scan marginals cannot recover identities or expired lifetimes |
| True MFE/MAE / execution P&L | No | Sparse sampled prices, no fills/slippage/fee/sellability model |

## Point-in-time data contract

Before replay, archive append-only observations with:

- chain/address, source/endpoint, discovery membership and rank, scan ID;
- event time, fetch-start/end and **available_at**, including candle close/release time;
- per-field origin/freshness (ranking, market, cached/fresh security), units and unknowns;
- candles with resolution and OHLC/USD amount, source and availability time;
- full validated nonsecret configuration, settings fingerprint, score version, code
  commit/source hash, normalization/API version;
- candidate decisions, deferral stage, queue entry/exit/expiry and request/error events;
- independent follow-up price availability for alerted and comparable nonalerted tokens.

Use stable composite keys and append-only arrival history. Keep collection revisions
separate from strategy revisions. Late revisions must not overwrite what was known
at earlier times. Archive a manifest and checksum for each partition/export.

## No-look-ahead replay rules

At simulated time `t`, accept only inputs with `available_at <= t`. A candle closing
at `t` cannot be used before its actual availability. Use only contemporaneously
observed ATH cap, supply, ranking and security. Never multiply today's supply by
future ATH or backfill today's security into old decisions. Do not fill unknown
features with future observations. Replay causal state (history gaps, cache TTL,
watchlist expiry, source-tier priority, budgets, rate-limit cooldown and incomplete
scans) before replaying scores. A pure re-score of enriched winners measures a
conditional scoring experiment, not scanner recall or full-system behavior.

GMGN's official market reference documents historical K-line reads, but ranking,
security and ATH availability must be verified per data source; a current endpoint
response is not a historical point-in-time record. Backfilled candles may support
price paths. They cannot reconstruct past unrecorded universe membership/security.
Keep backfill origin and acquisition time separate from originally observed data.

## Research architecture and validation

1. Collect/export without changing live decisions; retain an independent research
   universe including market-deferred/nonalerted candidates.
2. Write dated compressed observation/candle partitions outside hot SQLite, with
   private manifests. Verify grain, duplicates, missingness and join integrity.
3. Replay through an injected clock/data source into an isolated DB, with outbound
   Telegram/GMGN/trading disabled. Produce original versus replay decision diffs.
4. Compare one predeclared strategy experiment at a time on time-separated cohorts;
   train/tune on earlier periods, validate later, purge overlapping outcome horizons
   and embargo at boundaries. Group uncertainty by token and calendar regime.
5. Include liquidity/age/drawdown/time-matched nonalerted controls. Never select
   controls from later survivorship or maxima; record matching rules before outcomes.
6. Report signal count, outcome availability, median/mean price return, sampled/true
   MFE/MAE distinction, +10/+25/+50 hit N, time-to-25%, breakout timing, worst excursion,
   fees/slippage assumptions and coverage losses. Precision requires a predeclared
   success label/horizon and denominator, not just positive-return counts.

One current-version alert, amid nine mixed historical alerts, cannot tune weights or certify edge. Thirty is only a descriptive
sample-size flag in the audit tool, not a statistical sufficiency test. Effective N
can be far lower than row count because horizons and repeated tokens overlap.

## Separate retention policies (proposal; not applied)

| Dataset | Proposed policy | Reason / gate |
|---|---|---|
| Hot diagnostics/logs | Keep current seven-day DB and bounded log rotation initially | Archive research events before pruning; operational disk evidence required |
| Raw token observations | Trial dated compressed archive with 30/90-day retention | Full-universe research; measure actual bytes/day first |
| Alerts/outcomes/config provenance | Long-term compact retention, including explicit missingness | Small decision cohort and audit continuity |
| Historical candles | Deduplicated time partitions, trial 90-day window | Exact price/structure replay; budget storage from observed size |
| Backtest runs | Store config/input manifests, code SHA and aggregate results; bounded raw runs | Reproducibility without indefinite SQLite growth |

The current pruning deletes evaluations, scan config/presentation/metrics after seven
days; current snapshots and alerts/outcomes are not pruned. Cache rows overwrite
historical candles/security; watch-state pruning removes expired diagnostic lifetimes.
Do not silently extend SQLite forever or alter production retention now. Measure
storage growth, choose an archive budget, then review the concrete retention change within the authorized production scope.
