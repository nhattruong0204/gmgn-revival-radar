# Production audit and deployment

Follow-up: the [October 9 production re-audit and exploratory alert plan](ALERT_PLAN_2026-10-09.md)
uses a fresh VPS backup, explains the subsequent zero-alert window and API cooldowns,
and compares separate watch notifications against the five-distinct-tickers/day goal.
The dated measurements below remain the October 8 deployment record.

Audit date: 2026-10-08, UTC+7. Final database cutoff: **2026-10-08 14:48:34 UTC**.
Overall assessment: **NEEDS ATTENTION for data coverage and research readiness**.
Production scheduling, persistence and VPS resources are healthy in the measured
window. Current scoring has **INSUFFICIENT SAMPLE** to establish predictive value.

The authorized deployment is complete. The bot runs commit
`5b0f6df4a4d7746e3ee6eb98f2d35312c39c3069` in image `sha256:72fdb27aa088eb27fe821080b11a4d61a643ff86f442358c6a891d44d8f121b9`.
It restarted at `2026-10-08T14:33:29.900438933Z`. Three subsequent scans completed in
61.95, 61.89 and 63.40 seconds, with zero scan errors and five-minute start spacing.
All historical scan/evaluation/alert ID rows checked across the restart remain;
both `.env` and `data/radar-controls.json` retain their original hashes.
No thresholds, weights, enabled chains or secrets changed. No trades were executed.

The full quantitative audit ran **on the VPS host**, against a consistent online
SQLite backup, and again locally. All substantive results match. Corrected audit
utilities are at commit `ae42599`; the live application remains the tested runtime
release above. Reports and private audit tools do not require another bot restart.

## Evidence and production provenance

Production directory: `/opt/gmgn-revival-radar`. Initial checkout was clean at
`dd146511c32c02227514660c51b42afe4c2d2520`; ten installed module SHA-256 hashes
matched that revision. GitHub main was independently checked and remained at the
same SHA. Production was equal to main before deployment; it is now one audit
implementation commit ahead, on `deploy/audit-5b0f6df`. Nothing was pushed to GitHub.
The original image is retained as `gmgn-revival-radar:rollback-dd14651-20261008`.

Three consistent backups were collected, without stopping the bot for backup:
pre-deployment, first post-deployment and final. Every backup passed integrity checks,
has schema version 4, and has a manifest binding its SHA-256 to collection times.
The authoritative final private copy is `data/audit/final/production_snapshot.db`.
`data/audit/latest_audit.json` contains all table/column profiles, cohort studies,
individual signal paths, calibration buckets and deployment verification.
`data/audit/vps_latest_audit.json` is the independently executed VPS result.
All private artifacts remain git-ignored; only aggregate documentation is committed.

Repository architecture, tests and preserved benchmark fixtures were also examined.
Benchmarks and the offline demo are validation fixtures, never production outcomes.
Host/image provenance, actual Docker mounts and effective runtime configuration were
verified directly, rather than inferred from README defaults.

## Production resources and Docker

The VPS has one CPU, 1.9 GiB RAM, 3.8 GiB swap and a 50 GiB root filesystem.
Pre-deployment load averages were 0.08/0.02/0.01; vmstat samples showed 98–100% idle
CPU, no sustained swap or I/O wait. About 1.4 GiB RAM and 41 GiB disk were available.
The bot used approximately 108 MiB Docker-accounted memory and 1.39% CPU at the
inventory sample. The old container had zero restarts, no OOM flag and 22h uptime.
The new container also had zero restarts and no OOM flag. Bounded kernel-journal
checks found no OOM evidence. These short measurements support the current VPS;
they do not establish every historical peak. **No upgrade is justified now.**

Actual persistence is `/opt/gmgn-revival-radar/data:/app/data`, restart policy
`unless-stopped`, application UID/GID 10001, and JSON logs rotated at 10 MiB × 3.
History survived this authorized image replacement. Reboot behavior was inferred
from restart policy and the persistent mount; the VPS was not rebooted.
The old log file was approximately 3.18 MB. No Docker pruning was performed.

## Effective live configuration

Preset **Custom**, revision **7**, Solana only, alert threshold **60**, five-minute
cadence, discovery limit 20/source, watchlist limit 100, watch lifetime 24h.
Market/security/candle budgets are **40/8/12** operations per scan; retries can use
more HTTP attempts. Request spacing is 1.5s, timeout 20s, three attempts and maximum
retry wait 10s. History requires three observations, keeps up to six, and accepts
maximum 900s gaps. High/normal/low watch targets are 150/600/1800s.
Effective base minimum is **8h**, sufficient base **48h**; those differ from defaults
and were preserved. Setup/Trigger/Confirmation allocations remain **30/40/30**.
Current score version is `setup-trigger-confirmation-v1`. Alerts remain enabled,
dry-run false, and daily summaries disabled, matching original production settings.

## Scanner performance and historical errors

| Population | Completed scans | Mean / p50 seconds | p90 / p95 / p99 seconds | Max seconds |
|---|---|---|---|---|
| All retained history | 391 | 144.97 / 63.39 | 330.69 / 335.69 / 654.99 | 1,367.05 |
| Old container, after 2026-10-07 16:07 UTC | 268 | 63.17 / approximately 63 | No 300s overruns | 72.43 |
| First three deployed scans | 3 | 62.41 / 61.95 | Short validation window | 63.4 |

There are four unfinished rows at the final cutoff: three old interruptions (IDs
2, 99, 106) and one scan active at backup time (395). The earlier active scan 390
finished normally before deployment. The full retained history contains 100 scans
longer than 300s, all preceding the stable old-container window. Mixing those with
current scans would falsely suggest current scheduling still averages over two minutes.
The service awaits one scan at a time; overruns do not create overlapping scans.

The 219 retained scan errors occurred in exactly three older scans: ID 43 (96),
102 (31), 103 (92). Both discovery sources succeeded in those scans. The pre-deployment
24h window contained 123 of those errors, all in IDs 102/103. They reduced processed
candidate coverage and cannot simply be called harmless. Their actual causes and
individual token identities cannot be recovered from the surviving container logs.
They predate that container's creation; CPU or GMGN blame would be speculation.

All 268 completed scans in the retained old-container log window had zero errors.
All 268 log completion records matched database counters at corresponding finish
times; no counter disagreement was found. Four normal alert cooldown records were
present. No 429, timeout, 5xx, malformed-token, SQLite-lock, Telegram-failure or
traceback evidence occurred in these bounded surviving logs. This is evidence of
absence in that retained window, not proof none occurred in removed containers.

The original exporter incorrectly classified `errors=0` completion counters as
error text and alert cooldown as GMGN cooldown. This was corrected, covered by
regressions and applied to extracted summaries using known application log grammar;
original archives remain unchanged. Final export uses the corrected parser directly.
New structured telemetry distinguishes HTTP status/retry recovery from failed
operations, optional enrichment failures and normalization problems. Legacy
`DataSourceError` alone cannot identify a cause; unknown values remain unknown.

Endpoint attempt totals over retained telemetry (288 measured scans, not all
395 scan rows) are shown below. These may include retries and are not counts of
unique enriched tokens. Legacy status/recovery metadata is unavailable; new HTTP
status metrics cover only the prospective deployment window.

| Endpoint | HTTP attempts |
|---|---|
| /v1/market/hot_searches | 288 |
| /v1/market/rank | 288 |
| /v1/token/info | 11702 |
| /v1/token/security | 4 |
| /v1/market/token_kline | 254 |

Kline cache: 488 hits / 244 fetches, 66.67% hits among hits plus fetches. Security cache: 3 hits / 3 fetches, 50.00% hits among hits plus fetches. Cache accounting differs from total endpoint attempts; incremental candle calls
and retries must not be mistaken for additional independent recipients. Stage-time
sums include API wait, and market waiting dominates measured scan time.

## UI reconciliation

| 24h metric | Pre-deployment cutoff 14:27:29 UTC | Final cutoff 14:48:34 UTC |
|---|---|---|
| Scans started / completed | 289 / 287 | 290 / 288 |
| Mean completed scan seconds | 66.59 | 64.22 |
| Unique ranking discoveries | 938 | 933 |
| Evaluation observations | 19,664 | 19,670 |
| Core checks passed: full eligibility | 82 | 80 |
| Market filters only | 3,097 | 3139 |
| Sent alerts | 3 | 3 |

These counters follow the UI's scan-start window and separate alerts by reservation
time. Ranking discoveries exclude watchlist work; evaluations include repeated
watchlist observations and prefilter rejections. Market-budget-deferred candidates
are not evaluated. The UI label “Core checks passed” counts `eligible`, which also
requires base/activity checks; it is not the market-only pass count.
The request's approximate 287 scans/20k evaluations/86 passes/4 alerts had no exact
snapshot time. The measured later sliding windows are consistent with changing
window membership; no UI arithmetic defect is established by those differences.
The historical outcome as-of join did have a verified look-ahead defect and is fixed.

## Request budgets, fairness and baseline history

Every one of the old container's 268 completed scans reached 40 market operations.
Of 28,330 prefilter survivors, 10,720 were enriched and **17,610 were deferred**:
62.16% of survivors. Mean deferrals were 65.71 per scan. Outcome-only polls were
zero in retained telemetry; discovery/watchlist processing consistently exhausted
the shared budget before an independent outcome poll could run. Naturally evaluated
alert tokens still supplied some checkpoints. Zero polls alone does not mean every
checkpoint was missed, but off-ranking follow-up has no reserved coverage.

Priority sorts source tiers ahead of accumulated deferral counts. Aging helps within
a tier and cannot guarantee service to lower tiers. The first deployed scan had
111 survivors, 40 enriched and 71 deferred. All 71 deferred identities were watchlist
only; the 40 enrichments included 12 current ranking candidates and 28 watchlist tokens.
This establishes real coverage loss; it does not establish how many profitable
opportunities were lost.

The final artifact contains 404 prospective candidate traces across three completed
scans: 213 deferral observations, last-observed backlog 70, maximum observed streak
three. The oldest lower-bound defer age is about 900s as of export; candidate absence
and the short trace window limit interpretation. There is no uncensored first-
enrichment latency sample yet: p50/p90/p95/max are unknown. Expired-without-enrichment
count is unknown. No observed 30-minute starvation is certified from 15 minutes of
telemetry. `watch_state.deferred` mixes stages and failures and cannot recover legacy
consecutive market deferrals or expired lifetimes.

Pre-deployment snapshot coverage spans 1,780 tokens, median 13 snapshots/token.
Across all chains, median sampling gap was 981s and 18,591/34,080 gaps exceeded 900s.
For Solana alone, median gap was 649s, but **10,995/24,214 gaps (45.4%)** exceeded
900s; p95 was 4,200s. A 1,800s low-watch target itself exceeds the history gap limit.
The 150s high-watch target is also bounded by the 300s main scan cadence.

Among 19,873 evaluations with saved presentations, baseline readiness was 15.52%.
Mature discovery candidates were 53.20% ready (2,376/4,466), new candidates 1.21%
(74/6,094), and watchlist-only candidates 6.82% (635/9,313). Current Custom r7 was
16.37% ready (1,141/6,972). Maturity uses first retained snapshot as an explicitly
imperfect proxy for first discovery. Global 5m volume/TX availability was 60.81%,
substantially higher than readiness: valid contiguous history, not merely one known
5m field, is the stronger requirement. The evidence implicates pacing, scheduling,
churn and budget selection; it does not justify changing history rules blindly.

## Sequential funnel and conditional structure

A complete legacy intersection funnel cannot be reconstructed from marginal counts.
The new traces produce the following **baseline-ready path**, pooled only over three
completed scans with the same current configuration. Ranking and watchlist funnels
are also emitted separately in the private JSON.

| Stage | Count | From previous stage | From candidate universe |
|---|---|---|---|
| candidate | 404 | 100.00% | 100.00% |
| prefilter_pass | 333 | 82.43% | 82.43% |
| market_enriched | 120 | 36.04% | 29.70% |
| market_pass | 38 | 31.67% | 9.41% |
| baseline_ready | 27 | 71.05% | 6.68% |
| activity_trigger | 5 | 18.52% | 1.24% |
| structure_available | 5 | 100.00% | 1.24% |
| base_detected | 0 | 0.00% | 0.00% |
| eligible | 0 | unknown | 0.00% |
| potential_alert | 0 | unknown | 0.00% |
| alerted | 0 | unknown | 0.00% |

Security is a conditional branch after the optimistic score reaches seriousness;
it was 0/0 expected recipients in these three scans, not 0% coverage. Activity can
also arise without a multi-observation baseline; the artifact records that branch
separately. This small prospective path does not reconstruct older unobserved tokens.

Pre-deployment structure was available for 761/19,873 presentations (3.83%) globally,
but for **731/750 inferred eligible recipients (97.47%)**. Global low coverage mostly
reflects deliberate activity/market gating. Bases existed in 92/761 available structures;
HL/HH/breakout appeared in 27/17/14 of those 92, respectively. Same-evaluation
breakout+retest count was zero; retest can follow a prior-bar breakout, so this is not
a longitudinal failure rate. Nineteen expected recipients lacked structure; API versus
empty-cache/data reasons are not recoverable individually from legacy marginals.

Closed hourly candles are deduplicated, gap-checked and evaluated only after market
and activity gates. Fewer than six contiguous closed bars gives unavailable structure;
latest-bar age limit is 7,200s and suffix gap limit 5,400s. Future/incomplete bars are
excluded. Activity-first gating may recognize an already mature Setup late, but the
existing data cannot prove this causes missed winners. Shadow proactive structure
sampling is an experiment, not an applied change.

## Configuration cohorts and signal quality

Cohorts separate preset, revision, score version and a settings fingerprint. Two
Broad r5 legacy fingerprints differ, so revision alone would merge incompatible
configurations. Missing dimensions/version stay LEGACY and are never zero-filled.

| Preset / revision / score version / fingerprint | Evaluations | Eligible observations | Sent alerts |
|---|---|---|---|
| UNKNOWN / None / LEGACY / 44136fa355b3678a | 11,575 | 15 | 6 |
| Broad / 5 / LEGACY / ef52ce23a787e9c2 | 1,109 | 6 | 1 |
| Broad / 5 / LEGACY / d996d03cec74a8c7 | 344 | 3 | 1 |
| Broad / 5 / setup-trigger-confirmation-v1 / dd954079fdb66dbb | 11,448 | 34 | 0 |
| Custom / 7 / setup-trigger-confirmation-v1 / 16e5c72dff197b91 | 7,273 | 40 | 1 |

All nine sent alerts are listed below. Contract addresses, checkpoint timestamps,
stored prices, sparse paths and sample gaps are in the private JSON. The first six
have no retained original price, so even collected later prices cannot establish
returns. Historical non-Solana alerts remain historical; no additional chain was enabled.

| Alert | Time UTC | Score | Setup/Trigger/Confirm | Stage | +1h | +6h | +24h | +72h |
|---|---|---|---|---|---|---|---|---|
| 1: Legacy Robinhood #1 | 10-06 18:19:13 UTC | 75 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 2: Legacy Robinhood #2 | 10-07 05:22:36 UTC | 70 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 3: Legacy Robinhood #3 | 10-07 05:33:24 UTC | 80 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 4: Legacy Solana #4 | 10-07 10:04:00 UTC | 60 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 5: Legacy Solana #5 | 10-07 11:46:47 UTC | 80 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 6: Legacy Solana #6 | 10-07 14:19:22 UTC | 60 | unknown/unknown/unknown | LEGACY | unknown | unknown | unknown | unknown |
| 7: PAID | 10-07 15:11:11 UTC | 75 | unknown/unknown/unknown | REVIVING | 0.87% | -6.27% | unknown | unknown |
| 8: e/acc | 10-07 15:30:47 UTC | 70 | unknown/unknown/unknown | REVIVING | -1.97% | 2.98% | unknown | unknown |
| 9: EMBER | 10-08 09:12:24 UTC | 60 | 76/50/75 | EARLY_REVIVAL | 15.97% | unknown | unknown | unknown |

EMBER is the only sent alert with the current dimensional score version: Custom r7,
score 60, dimensions 76/50/75, 12h base, HL/HH/breakout true, retest false, $419k
liquidity, $13.9k 5m volume and 109 5m transactions at alert time. Its +1h price return
was +15.97%. Sampled 1h MFE was +28.91%, MAE 0% including entry price, and the first
sample at +25% occurred after 1,800s. Its +6h was not due at the final cutoff.
Neither absolute activity nor one favorable return validates a trading strategy.

PAID's sampled 6h MFE/MAE were +1.65%/−6.55%; e/acc's were +3.24%/−5.27%.
PAID had a 9h base with no confirmed HL/HH/breakout/retest, but legacy dimensional
scores are unknown. Comparing these two legacy alerts with EMBER cannot validate
Confirmation weights or explain winners versus losers statistically.

**INSUFFICIENT SAMPLE.** No current-version alert has a mature 24h price return.
Legacy matured 24h checkpoints with missing entry prices remain unknown. The
+24h false-positive study therefore has no usable positive/negative classification;
a returned count of zero is not a 0% false-positive rate. No precision, win rate,
expected return, profitability or statistical edge is claimed.

Outcome checkpoints select the first usable price/cap in the one-hour grace window
and are immutable. A cap-only checkpoint can prevent subsequent price recovery.
Only enabled chains are polled; disabled historical chains are not silently revived.
MFE includes zero return at entry, remains unknown without follow-up prices, and is
a lower bound from sparse sampling. MAE is an upper bound. Entry/terminal sample gaps
are recorded. These are observed price changes, not returns after fees/slippage/fills.

## Near misses and calibration

The near-miss anchor is the first rejected score ≥50 per token/config cohort.
Of 34 anchors, 24 legacy-unidentified anchors lack price context. There are 30 anchors
in 50–59, four in 60–64, and none in 65–69/70–74/75+. No sampled mature 24h MFE exceeded
+50%, but only two anchors had a usable mature 24h excursion path and no terminal
+24h returns were available. This cannot establish that no future winner was rejected.
The two current Custom r7 near misses both scored 50–59: median +1h −9.97% (N=2),
+6h +9.52% (N=1), +24h unavailable. Blockers overlap; budget-only deferred candidates
have neither counterfactual score nor reliable future price path.

First-universe evaluation anchors mostly score 0–39. Using only those anchors would
exclude later high-score observations. A second descriptive study uses first entry
per token/cohort/dimension/band, allowing dependent appearances in multiple bands.
The complete dimensional tables include outcome N, MFE/MAE, missingness and horizons
in the artifact. Selected overall band results are:

| Cohort | Band | Token entries | 1h N | 1h median | 6h N | 6h median | 24h N |
|---|---|---|---|---|---|---|---|
| Broad r5 | 40-49 | 5 | 5 | 8.38% | 5 | -10.37% | 0 |
| Custom r7 | 40-49 | 7 | 6 | -7.66% | 2 | -21.15% | 0 |
| Custom r7 | 50-59 | 2 | 2 | -9.97% | 1 | 9.52% | 0 |
| Custom r7 | 60-69 | 1 | 1 | 15.97% | 0 | unknown | 0 |

There are no current-version overall score 70+ band entries. Higher Setup/Trigger/
Confirmation bins have very few independently sampled tokens. Low-score rows also
include cheap rejects whose missing optional features were never fetched. Bucket
entry times differ and collection is score-dependent; observed medians cannot isolate
component effects. No weight or threshold optimization is supported.
Absolute TX/volume floors and positive-baseline guards are useful for mathematical
correctness. Their predictive value, and that of Hot Search, concentration, base,
HL/HH/breakout or dimensional allocations, remain unproven.

## On-chain/security data and market regime

This is an audit of the bot's stored GMGN-provided market/on-chain measurements;
it is not an independent historical RPC reconstruction of token holders or sellability.
Among pre-deployment saved presentations: top10 and sniper ratios were 100% populated,
dev ratio 60.81%, bundler ratio 53.14%, explicit dangerous flag 54.91%, insider
holdings **0%**. Populated summary fields do not mean the security endpoint was called
for every token or certify a token is safe. Optional API failures and 30-minute
security caching can leave unknown/stale fields; known danger is not cleared by a
cached safe result. Budget-deferred security suppresses alerts, while nullable
security on optional request failure follows the existing policy.

The provider's [official token field reference](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md)
defines `suspected_insider_hold_rate` as holdings; `rat_trader_amount_rate` is trading
volume and must not replace it. Missing insider holdings is unknown, not zero.
An insider penalty that lacks input cannot be evaluated for predictive usefulness.
The user-facing missing-field warning remains suppressed as designed; coverage is
visible in research diagnostics. Current API responses cannot reconstruct past
ownership/security or ATH availability for a faithful backtest.

There is under 48h of retained market observation history, so 3-day and 7-day windows
contain the same observations. The pre-deployment nested collected-universe medians
for today were $0 5m volume, $638 1h volume, $7,324 liquidity, 0 5m TX and 17 1h TX;
for both longer windows they were $65, $12,826, $11,235, 3 and 152. Those are selected,
all-chain observation mixes with structural zero/default ambiguity, not an independent
Solana market sample. No HIGH_ACTIVITY/LOW_LIQUIDITY/CHOPPY regime label is justified.
Solana-only gaps were separately measured; future regime work must also hold chain,
collection cohort, token survival and liquidity selection constant.

## Changes applied and remaining work

Deployed: historical outcome cutoff correction; reachable short-base blocker;
per-candidate stage/deferral trace; fixed-label operation/HTTP/retry/normalization
telemetry; partial interrupted-scan measurements. No schema migration was needed.
Audit tools now also distinguish completion counters from errors, distinguish alert
from GMGN cooldown, include entry in excursion bounds, record path-edge gaps and
measure later score-band entries. The corrected tools ran directly on the VPS.

| Priority | Next action | Evidence / constraint |
|---|---|---|
| P1 | Collect/archive candidate and outcome measurements before seven-day pruning | Legacy streaks/expired lifetimes irrecoverable; prospective window currently short |
| P1 | Design research archive/storage budget | Snapshots grow without pruning; state/presentations dominate current file |
| P2 | Shadow-test aging across source tiers and independent outcome reservation | Every observed stable scan exhausted market budget; do not tune live strategy from this alone |
| P2 | Resolve late/cap-only outcome semantics with explicit missingness | Six missing entry prices and short/missing paths prevent performance validation |
| P3 | Shadow proactive Setup sampling | 97.5% conditional structure availability; timing benefit unproven |
| P3 | Walk-forward threshold/weight comparison with matched controls | One current-version alert, dependent bucket entries and endogenous collection |
| No action | VPS upgrade or automatic threshold relaxation | Resource pressure absent; predictive evidence insufficient |

Top technical risks: cross-tier starvation, shared outcome budget, diagnostic/candle
history loss, legacy error underclassification, and per-field observation provenance.
Top quantitative risks: tiny current signal N, endogenous selection, sparse path
censoring, repeated-token/config dependence, and unmodeled execution/regime costs.
Backtest readiness remains **PARTIAL**; see [backtest-readiness.md](backtest-readiness.md).
Database findings and storage checks: [database-audit.md](database-audit.md).
Checks and deployment rollback: [validation.md](validation.md).
Access/backup method: [production-access.md](production-access.md).
