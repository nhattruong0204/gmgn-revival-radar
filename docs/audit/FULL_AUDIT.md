# Executive summary

Audit date: **2026-10-08, Asia/Saigon (UTC+7)**. Overall assessment: **NEEDS ATTENTION**
for measurement correctness and unresolved production evidence. This is a completed
local architecture/engineering review and a tested production-evidence collection
workflow. **The quantitative production audit is NOT complete.** No VPS health,
production signal performance, or strategy edge is certified by passing tests.

**FACT:** Local checkout started clean at `dd146511c32c02227514660c51b42afe4c2d2520`.
The GitHub connector independently returned that SHA for current `main` during this
audit. `git log --oneline -20` returned the repository's 14 available commits.
Production code/image revision remains unknown. SSH reached the explicitly confirmed
target `root@130.94.7.127` with normal host-key checking and presented a password
prompt; no new-key trust prompt or mismatch appeared. This runtime has no private
user-entry channel for feeding that prompt without recording a tool argument.
Authentication was cancelled; no password was collected or saved.

**FACT:** The initially available `data/` contained only `.gitkeep`, not the production
DB. Values in the request (approximately 287 scans, 20k evaluations, 86 core passes,
4 alerts, 18% history coverage, 108 candidates/40 requests) are **unverified UI
observations**, not recomputed evidence. No production DB integrity, time range,
row counts, resource pressure, restart history, or log taxonomy has been measured.

**RECOMMENDATION:** Keep the live Solana configuration, thresholds, weights, budgets,
pacing, and enabled chains unchanged. Obtain the private export described in
[production-access.md](production-access.md), then run the offline analysis. Do not
upgrade the VPS or deploy these local changes based on the present evidence alone.

# Data inspected

| Evidence | Authority and scope | Limit |
|---|---|---|
| Current scanner, clients, scoring, service, repository, controls and tests | Actual local implementation at audited base SHA | Does not establish running VPS version |
| Existing benchmark JSON and replay tape | Preserved individual live dry runs or explicitly synthetic replay | Not production history; collection policy/version differs between files |
| GMGN official token/market docs retrieved on audit date | Provider definitions and field semantics | Does not establish actual returned field coverage |
| Offline demo and new deterministic tests | Correctness fixtures and safe-export validation | Synthetic; no trading-performance evidence |
| Production database/logs | **NOT AVAILABLE** | Required for all production quantitative conclusions |

Reproducible implementations: [audit.py](../../src/revival_radar/audit.py),
[audit_database.py](../../scripts/audit_database.py),
[export_audit_bundle.py](../../scripts/export_audit_bundle.py),
and [tests](../../tests/test_audit.py). Private outputs are under git-ignored
`data/audit/`; there is no published production artifact.

# Production health

**FACT:** No authenticated VPS session was obtained. All production operational
answers remain **UNKNOWN**, including container uptime/restarts, host resources,
WAL bytes, DB growth, and production/main equality. A host checkout SHA alone would
not prove image provenance, so the exporter also hashes ten installed source modules.

**FACT (configuration review only):** Compose specifies `restart: unless-stopped`,
`./data:/app/data`, a 30-second stop grace period, non-root application user, and
JSON log rotation at `10m × 3`. Those declarations support persistent history across
rebuild/restart/reboot when deployed as written. Actual production mounts, rotation,
image, restart policy, OOM status and disk use require inventory verification.
No restart, rebuild, deployment, config write, production migration, package
installation or trade was performed.

# Scanner performance

**FACT:** [Preserved final live benchmark](../benchmarks/issue5-final-live.json),
2026-10-07 15:57:12 UTC: **18.243793 seconds**, 38 unique discoveries/evaluations,
5 market fetches, 7 HTTP attempts (all 200), 0 candle/security fetches, 0 returning
activity, 0 alerts/errors. This cold-start sample does not measure steady-state
watchlist load, tail latency, or recall.

**FACT:** [Repeat benchmark](../benchmarks/issue5-final-repeat.json) explicitly uses
synthetic history and mock network latency. Scan 1: 4 candles fetched; scan 2:
0 candle fetches and 3 candle hits. It demonstrates cache reuse, not production
latency or predictive signals.

**FACT (code):** The service awaits each scan, then waits
`max(0, interval - elapsed)`; it does not spawn overlapping scans on overrun. The
150-second high-watchlist interval is a due-time target within a service whose
default scan cadence is 300 seconds; it does not imply a 150-second sampler.
The configured operation budgets are global across enabled chains; retries consume
HTTP attempts beyond operation counts. Production cadence and percentiles are unknown.

The offline analyzer reports mean/p50/p90/p95/p99/max duration, start spacing,
unfinished scans (including a currently running scan), errors/scan, endpoint
attempts, cache/deferred counts, and cohort-specific scan summaries. A null `finished`
row alone cannot distinguish an active scan from an interrupted one. Future requests
need exporter time and logs to distinguish them.

# API / errors

**FACT:** Two preserved dry-run artifacts each contain a Trending HTTP 429:
[14:35:08 UTC](../benchmarks/issue2-after-live-rate-limited.json) and
[14:36:44 UTC](../benchmarks/issue2-after-live-rate-limited-retry.json), 2026-10-07.
Both stopped for authentication/rate-limit handling and each reports six scanner
errors after one observed HTTP 429. This demonstrates that one upstream failure
can fan out into several failed operations. It does **not** establish production
429 frequency or six independent upstream incidents.

**FACT (code):** `scan_runs.errors` counts discovery, token-processing and delivery
failures. Caught optional candle/security failures and malformed discovery rows may
appear only in separate metrics or warnings. Existing logs often retain only
`DataSourceError`, which cannot distinguish timeout/429/5xx. Existing per-stage
failure totals are not an error taxonomy and cannot establish retry recovery.

**Implemented P1:** New fixed-label error events retain timestamp, stage and validated
chain/address, with no exception message. HTTP counters separately retain response
status, transport/429/5xx/cooldown counts, and successfully retried envelopes. A
recovered envelope is not necessarily a successfully normalized token. Interrupted
scans preserve partial metrics while keeping `finished IS NULL`. Legacy log exports
retain only recognized event/numeric/identity fields; arbitrary log bodies and raw
Docker environment are excluded. Taxonomy counts from logs are lines, not incidents.

# Request-budget starvation

**FACT (code and regression test):** `priority()` sorts source tier before negative
deferral count: Trending-only, Hot-only, both sources, then high/medium/low watchlist.
Twenty deferrals do not let a lower source tier outrank a fresh Trending-only token.
This is cross-tier starvation potential despite rotation within each tier. The
actual frequency, loss rate, queue ages, and rejected future winners are unmeasured.

**FACT:** Market-budget deferrals return before snapshot/evaluation persistence.
`watch_state.deferred` also increments on candle/security budgets and token errors,
and `last_polled` updates even for cheap prefilter rejection. Neither field is an
exact history of market enrichment. Historical 108/40/68 UI values cannot be turned
into per-token latency or expiration statistics.

**FACT:** Outcome-only polling is last and uses the remaining market budget. A
perpetually full candidate budget can miss horizon checkpoints, especially after a
token leaves rankings or the watchlist. No budget is reserved for outcomes.

**Implemented P1:** Per-scan candidate traces retain first/last seen, previous poll,
prior deferral count/tier, source membership, stage flags and separate market/candle/
security deferrals. Traces are stored in the existing metric state record, without
schema change or altering scheduling. The offline analyzer reports observed streaks,
30-minute starvation counts and lower-bound deferral ages. First-enrichment delay is
reported only if telemetry captures discovery itself. Expired candidates without a
complete event history remain unknown. These records have existing seven-day
retention; archive them for longer research.

**HYPOTHESIS:** Small fairness quotas or aging across source tiers could improve
coverage. **EXPERIMENT ONLY:** Measure a prospective control with unchanged ranking
limits, then compare latency/recall/cost. No source-priority/budget change was applied.

# Data coverage

**FACT (code):** Baseline readiness requires distinct, cadence-spaced contiguous
prior observations with enough known 5m volume. The default gap limit is 900 seconds,
minimum count 3, history cap 6. Low-score watchlist intervals can be 1800 seconds,
longer than that gap limit. Discovery churn, market deferrals, source-tier priority,
and intermittent coverage can prevent a valid baseline without an API mapping bug.
One-step TX/hourly acceleration can trigger before the multi-observation 5m-volume
baseline is ready. Missing history does not imply every activity pathway is absent.

Production global 5m/TX and mature/watchlist baseline rates are **UNKNOWN**. The
analyzer separates presentation availability from field availability, retained
snapshot age from actual discovery age, watchlist versus discovery evaluations,
score bands and cohorts. Mature-age segmentation from legacy snapshots is explicitly
an approximation; it does not fabricate first discovery times.

**P1 correction:** A short identified consolidation range now adds `base_too_short`
even when `base_detected` is false. The broader `no_base` blocker remains; blockers
are intentionally overlapping. Previously, the detector required the duration
threshold before setting `base_detected`, making the specific short-base blocker
unreachable for actual short ranges. Eligibility and scores are unchanged.

# Sequential funnel

The operational pipeline is:

```text
Discovery + due watchlist → known-field prefilter → fresh market → full market gate
  → activity evidence (multi-observation baseline is one branch)
  → closed candle analysis → optimistic score → security if serious → final score
  → eligible → threshold → dry-run/pause/cooldown/delivery → sent
```

**FACT:** Existing marginal stage counters do not identify candidate intersections.
Adding watchlist work can make later counts larger than discovery counts. Dividing
all counters by discoveries would produce invalid funnel percentages. `no_base` in
an unfetched evaluation is not evidence of failed consolidation detection.

New candidate traces support an actual set-intersection funnel, separately for
ranking discoveries, watchlist work, and cohorts. Each stage reports previous-stage
and universe denominators. The baseline-ready research path also exposes the
activity-without-baseline branch. Security coverage is conditional on serious
candidates; it is not a mandatory stage for every eligible token. Raw blockers stay
separate. Legacy rows without traces report the complete sequential path as unknown.
Production rates await the export.

# Configuration cohorts

Every scored evaluation has a saved presentation with config context, score version,
and dimensional scores when available. The analyzer groups by preset, revision,
score version **and settings fingerprint**, preventing reused revision numbers from
merging different weights. Missing dimensions/version remain **LEGACY**, not zeros.
Cohort outputs contain counts, unique tokens, eligible observations, sent alerts,
dimensional distributions, outcomes, near misses, calibration, budget/error metrics.
Global summaries describe collection only; decisions must use cohort results.
No production cohort results are available.

# Signal outcomes

**P0 fixed locally:** `outcome_summary(since, until)` previously selected alerts by
window but did not constrain the joined checkpoint's `observed_at`. A report as of
3900 seconds could include a return collected at 4000 seconds. The join now requires
`observed_at <= until`; regression tests confirm no future observation leaks.

**FACT (existing semantics):** Only `sent` alerts are tracked at 1h/6h/24h/72h. The
first market observation within `[horizon, horizon+1h]` becomes the immutable
checkpoint. Missing/zero starting price leaves return NULL; price/cap both absent
allows a retry. A market-cap-only checkpoint is immutable and can prevent later
price recovery at that horizon. This existing contract is documented and tested;
changing it requires a separate outcome-policy design.

No production alert/outcome rows have been inspected. If the UI's four alerts are
confirmed, the sample is **INSUFFICIENT SAMPLE**. No profitability, expected return,
reliable precision/win rate or statistical edge is claimed.

The research tool uses durable stored checkpoints when present. Sampled MFE/MAE use
only subsequent stored prices within the horizon and as-of cutoff. It reports N,
sample times and gaps; sampled MFE is a lower bound on true MFE, and sampled MAE an
upper bound on true MAE. Missing terminal prices never become zero returns. Partial
future horizons have no finalized excursion statistic. These are price changes,
not executable trading returns after costs.

# Near-miss / missed winners

Production missed winners are **NOT MEASURABLE YET**. No winner count is invented.
The offline study uses the first rejected score≥50 observation per token/cohort,
partitions anchors into 50–59/60–64/65–69/70–74/75–100, then measures horizon returns
and sampled 24h MFE>50%. Attributions are overlapping, with unique token/cohort N.
Historical tokens never enriched lack an independent future price path; absence
of measured winners does not mean no winners were missed. Deferral-only candidates
cannot be retrospectively assigned blocker/outcome data that was never collected.

# False positives

Production false positives are **UNKNOWN**. The research definition is a sent alert
with a known negative +24h price return, explicitly separate from execution loss.
Insufficient N, missing checkpoints, partial horizons and survivor-biased sampling
must be displayed before comparing any characteristics. Trigger/Confirmation mix,
base length, absolute TX/volume, liquidity decline, sniper/bundler measures and
ranking context are plausible explanatory features, not proven predictors.

# Score calibration

Setup (30), Trigger (40) and Confirmation (30) are normalized dimension allocations
under current defaults; penalties are applied to the weighted total. Overall
40–49/50–59/60–69/70–79/80–89/90–100 and dimensional 0–20/21–40/41–60/61–80/81–100
summaries use first token/cohort anchors. N and missingness accompany every statistic.

**FACT:** No component is proven predictive from the available evidence. Absolute
activity floors and nonzero baseline checks prevent mathematical false surges;
that is correctness evidence, not predictive validation. Unfetched structure has
unavailable Confirmation, so low scores in skipped candidates are censored by the
collection policy. Reweighting from this selected dataset would be biased.

**RECOMMENDATION:** Do not tune weights or thresholds now. Compare time-separated,
cohort-isolated, token-clustered walk-forward samples with matched nonalerted controls.

# Market regime

Production today/3-day/7-day medians and score/discovery/activity changes are unknown.
The tool computes local-day and rolling-window medians from stored enriched
observations, explicitly labels the nested windows and selection bias, and assigns
no unsupported regime label. Score-dependent enrichment mix must be adjusted before
calling a change in collected liquidity a change in the whole Solana market.

# Structure analysis

**FACT (code):** Candles are fetched after returning activity and market gate pass;
there is no proactive Setup maintenance. Closed hourly bars are deduplicated and
gap-checked; a suffix after >5400-second gaps is used, and fewer than six contiguous
bars gives unavailable structure. The base is a bounded range before the final two
bars, with major-low stability; higher lows/highs use confirmed interior pivots.
Future/incomplete bars are excluded. The latest-bar age check is 7200 seconds.

Global and conditional coverage must differ. The tool reports available structure
among evaluated presentations and among inferred expected recipients, bases among
available structure, HL/HH/breakout among bases, and same-evaluation retest/breakout
overlap. Retest can follow a prior-bar breakout without a same-bar breakout; this
statistic must not be read as a longitudinal retest probability.

**HYPOTHESIS:** Activity gating could discover a mature Setup late.
**EXPERIMENT ONLY:** Maintain a small shadow candle allocation for liquid, mature,
deep-drawdown watchlist tokens; measure earlier Setup knowledge versus opportunity
cost to active candidates. No proactive K-line allocation was implemented.

# Security coverage

**FACT:** `suspected_insider_hold_rate` is the correctly mapped holdings field in
[GMGN's official token reference](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md).
`rat_trader_amount_rate` is trading volume and must not substitute for holdings.
The official reference documents insider holdings; it does not establish universal
availability or a Solana guarantee. Production 0% coverage would mean missing, not
safe and not necessarily unsupported. Existing alert presentation already suppresses
repeated missing-insider watchouts, while keeping known risk deductions.

Coverage must distinguish ranking/token-info fields, cached security, and fresh
security-endpoint results. Current security caching permits 30-minute-old fields
under defaults; fresh token-info fields keep precedence. Known danger cannot be
cleared by a cached safe result. Optional security endpoint failure may retain
nullable eligibility; budget-deferred security suppresses alerts. Those existing
policies are strategy/security-policy questions, not validated guarantees of safety.
No live security policy was changed.

# Backtest readiness

**Assessment: PARTIAL / NOT research-grade for exact long-horizon replay.** Snapshots
and saved evaluation presentations can explain retained past decisions, but candle
cache overwrites, unobserved deferred tokens, incomplete outcome paths and seven-day
diagnostic pruning prevent exact full-universe replay. Raw ranking/security response
history and availability times are not durably recorded. See
[backtest-readiness.md](backtest-readiness.md) for the point-in-time data contract,
retention split, architecture and walk-forward design.

# Risks / biases

Top five technical risks, with evidence:

1. Cross-tier starvation: tier precedes deferral count (`Scanner.priority`; regression).
2. Outcome starvation: polls consume only remaining market budget (`track_outcomes`).
3. History loss: seven-day evaluation/config/metrics pruning; candle cache overwrite
   (`prune_diagnostics`, `cache_save`). Snapshots/alerts grow without separate policy.
4. Error underclassification: exception-type-only legacy logs and optional failures
   outside `scan_runs.errors`; new telemetry cannot recover already lost facts.
5. Observation provenance: market price inherits seed observation time and can fall
   back to ranking fields when fresh token-info metrics are missing. Client timestamp
   is not a server event time. Separate field availability/fetch timestamps are needed.

Top five quantitative risks:

1. Few alerts cannot establish edge; confirmed sample N remains unavailable here.
2. Score-dependent data collection biases near-miss outcomes and calibration.
3. Sparse sampled paths understate favorable/worst adverse excursions; missingness
   can reflect delisting, liquidity disappearance, or budget starvation.
4. Same token/repeated/config-correlated observations inflate effective sample size.
5. Market drift, selection changes, execution costs and liquidity constraints can
   explain apparent returns without an incremental signal effect.

# Recommendations

**Safe local changes implemented:** as-of cutoff correction; specific short-range
blocker alongside overlapping no-base; fixed-label operation-error and HTTP retry
telemetry; per-candidate stage/deferral traces; partial interrupted-scan telemetry;
standalone consistent-WAL private exporter; read-only cohort-isolated research CLI.
No schema change, live migration, secret/config alteration, strategy change, enabled
chain change, production deployment or trade occurred. The existing `.gitignore`
already protects all `data/audit/` outputs, DB/WAL/SHM and log files.

The optional Telegram Research page was intentionally deferred: the CLI/report gives
a reviewable measurement surface without expanding production controls before the
primary production dataset is inspected.

# Priority roadmap

| Priority | Work | Applied? | Evidence needed next |
|---|---|---|---|
| P0 | As-of cutoff correction | Local only | Read-only historical reconciliation on export |
| P1 | Candidate/error telemetry and short-base attribution | Local only | Prospective cohort data after reviewed deployment |
| P1 | Export production DB/log/config/inventory; reconcile UI at identical cutoff | Export tooling ready; actual export pending | Private VPS bundle |
| P1 | Archive research facts before seven-day pruning | Documented, not scheduled | DB/WAL growth and desired storage budget |
| P2 | Fairness quotas/aging; reserve outcome coverage | Experiment, not applied | Latency/streak/expiration/missing-checkpoint evidence |
| P2 | Resource upgrade | Not recommended now | Sustained CPU/RSS/swap/I/O/restart pressure |
| P3 | Proactive structure, weight/threshold comparison | Experiment, not applied | Time-travel data and walk-forward controls |

# Validation and delivery status

The original 446 tests passed after installing the project in the local virtual
environment. New audit tests verify cohorts, denominators, delay/censoring, nullable
outcomes and excursions, read-only legacy-schema analysis, live-WAL backup, export
inventory/log allowlists, known-secret rejection, interruption persistence, and
as-of cutoff. Exact final validation results are in
[validation.md](validation.md). Passing tests establish implementation correctness
within their fixtures, not production market measurement quality.

## Required production answers

| Requested conclusion | Current answer |
|---|---|
| Overall bot health | NEEDS ATTENTION (measurement gaps; production operational state unknown) |
| Scanner performance adequate | Scheduling implementation sound; production duration/cadence unknown |
| API budget misses coverage | Starvation mechanism proven in code/tests; production frequency unknown |
| History coverage adequate | Unknown; global UI percentage cannot answer mature/watchlist coverage |
| Statistical meaning/current returns | Production N/returns unknown; four alerts, if confirmed, INSUFFICIENT SAMPLE |
| Missed winners / false positives | Unknown; no production outcome attribution performed |
| Useful score components | Mathematical guards validated; predictive components all unproven |
| Change live configuration now | No evidence supports tuning now |
| Safe changes / experiments / readiness | Listed above; backtest readiness PARTIAL |
| Files, tests and commit SHA | See validation and final handoff; audited base `dd146511c32c02227514660c51b42afe4c2d2520` |
| VPS audited | NO (reachable; no secure authentication channel) |
| Production SHA vs current GitHub main | UNKNOWN; current main verified as audited base during run |
| Container uptime/restarts | UNKNOWN |
| Production DB time range/row counts/integrity/size/growth | UNKNOWN; only demo fixture integrity checked |
| VPS CPU/memory/disk / LightNode adequacy | UNKNOWN; no upgrade recommended without pressure evidence |
| Production errors/429/candidate latency | UNKNOWN; historical dry-run 429 evidence is separate |
| Logs ↔ DB consistency/UI correctness | UNKNOWN pending same-cutoff snapshot and logs |
| Production changes recommended | Obtain export first; deployment/strategy/resource changes require subsequent review and approval |
