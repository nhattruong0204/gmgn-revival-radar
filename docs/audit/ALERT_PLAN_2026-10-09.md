# Production follow-up audit and exploratory alert plan

Audit cutoff: **2026-10-09 20:26:57 Asia/Bangkok (13:26:57 UTC)**.
“Today” means October 9 from midnight Bangkok to that cutoff; the rolling 24h
window starts October 8 at 20:26:57 Bangkok. Today's window is incomplete.

## Decision

**NEEDS ATTENTION for market coverage; service and database integrity are healthy.**
The lack of alerts is explained by the current eligibility and scoring gates,
with additional coverage loss from watch scheduling and API cooldowns. There is
no evidence of a Telegram delivery failure causing the observed silence: zero
potential alerts reached delivery in the last 24h. That does not independently
prove Telegram would deliver a future message.

The requested expansion should use **separate, clearly labeled exploratory watch
notifications alongside revival alerts**, counting distinct `(chain, address)`
tickers per Bangkok calendar day. Preserve the stronger lane initially. A guarded
watch experiment found **seven observed opportunities over 24h and five today**.
This supports testing a wider research feed, not a profitable strategy or a
guaranteed five alerts every day. No strategy settings or production code were
changed in this follow-up. No trades were executed.

## Source, scope and verification

SSH was used to collect a new online SQLite backup from the running VPS. The
backup API included WAL contents, without stopping the service. Integrity and
nine-table row totals were independently checked **inside the VPS container**
against its audit copy; detailed analysis ran locally against the matching copy.
The database SHA-256 is
`59926ca2abd883c927b3704250fb4d03bd735f17c4717a0f35e854a28fe9ad9f`.

The running checkout remains `5b0f6df4a4d7746e3ee6eb98f2d35312c39c3069`, image
`sha256:72fdb27aa088eb27fe821080b11a4d61a643ff86f442358c6a891d44d8f121b9`.
GitHub main is now `5db33ff4acd8be84bdd30e36d196843c4287c415`, which merged
[PR #6](https://github.com/nhattruong0204/gmgn-revival-radar/pull/6).
All ten measured running application module hashes match current GitHub main.
Different checkout SHAs therefore do not imply different measured scanner logic;
the later offline audit utilities and documentation are separate from runtime.

Private, git-ignored evidence under `data/audit/2026-10-09/`:

- `production-bundle.tar.gz`, `production/manifest.json`, snapshot, effective
  sanitized configuration, VPS inventory and bounded log summaries.
- `full/latest_audit.json`: every table/column profile, cohorts, signal paths,
  calibration, near misses, resource and log reconciliation evidence.
- `last24/latest_audit.json`: the same audit with an explicit rolling cutoff.
- `candidate_policy_analysis.json` and `analyze_current_candidates.py`: observed
  policy counts, first qualification anchors, follow-ups and latest candidates.
- `jeanphil_trace.json`, `budget_followup.json`, `source_parity.json` and
  `vps_readonly_check.py`: focused trace, scheduling, source and VPS verification.

The production credentials and database are excluded from publication. This is
an audit of recorded on-chain-derived GMGN fields and their application use;
it is not an independent reindex of every holder or a contract safety guarantee.

## Entire database and retention

Integrity check: **ok**; schema version **4**; foreign-key violations **0**;
checked relationship orphans **0**; duplicate excess rows **0**; invalid JSON in
profiled payload columns **0**; invalid checked snapshot/score domains **0**.
Three unfinished scan rows are old interruptions, with none in the last 24h.

| Table | Rows | Approximate table pages, excluding indexes |
|---|---:|---:|
| token_snapshots | 46,579 | 84.31 MB |
| evaluations | 49,024 | 37.91 MB |
| scan_runs | 666 | 2.65 MB |
| alerts | 9 | 0.004 MB |
| alert_signals | 9 | 0.004 MB |
| signal_outcomes | 15 | 0.004 MB |
| enrichment_cache | 90 | 0.68 MB |
| watch_state | 2,413 | 3.31 MB |
| state | 38,580 | 236.41 MB |

Snapshots span **October 6 17:29:53 UTC to October 9 13:23:38 UTC**;
evaluations/scans begin October 7 at approximately 04:03 UTC. There is roughly
three days of retained market history, not seven full days. The snapshot file
is **378.10 MB**; the live main file at export was 377.74 MB plus 4.19 MB WAL.
Relative to the prior October 8 14:48:34 UTC backup, snapshot file growth was
157.86 MB over 22h38m, equivalent to **167.35 MB/day over this measured interval**.
Do not extrapolate that rate indefinitely: diagnostics are pruned after seven
days, and SQLite page allocation/reuse can change physical file growth.

`state` is the largest contributor: saved evaluation presentations and candidate
traces preserve research detail. Archive those diagnostics privately before their
seven-day pruning. Preserve historical observations and alert/outcome baselines;
separate their retention policy from short-lived UI state. Watch rows include
inactive retained identities and are not the active queue or watch limit.

Legacy baseline gaps remain: six of nine `alert_signals` lack an entry price/cap.
The newly added snapshot asset classification columns are mostly NULL in older
rows; payload classification is used by the current model. Null insider holdings
are not zero holdings. Full column missingness and timestamps are in the JSON.

## Service, resources, API and logs

The container has run since October 8 14:33:29 UTC, approximately **22h53m**, with
**zero restarts**, no OOM flag, persistent `/app/data` bind mount, `unless-stopped`
policy and 10 MiB × 3 log rotation. The VPS has one CPU and 1.9 GiB RAM; inventory
showed 1.4 GiB available, approximately 64 MiB container memory, 21% root disk
usage, and 97–100% CPU idle in short vmstat samples. No VPS upgrade is supported
by these measurements. Historical resource peaks are not established.

| Rolling 24h measurement | Value |
|---|---:|
| Scans started / completed | 289 / 289 |
| Mean / p95 / maximum duration | 61.80 / 67.90 / 73.87 seconds |
| Median start spacing | 300.01 seconds |
| Unique ranking discoveries | 931 |
| Evaluation observations / unique evaluated tokens | 18,376 / 1,247 |
| Market-core passes / full eligible observations | 3,862 / 31 |
| Potential alerts / sent alerts | 0 / 0 |
| Structured GMGN 429 events | 9 |
| Local market operations skipped during shared cooldown | 360 |

All nine 429s occurred on **`/v1/market/rank`**, not token enrichment. Each affected
scan received Hot Search successfully, then the rank request failed; the next
40 logical market operations stopped locally during shared cooldown. They are
**360 skipped operations, not 360 additional HTTP 429 requests**. Nine affected
scans contain 369 reported errors in total. Retry recovery is not recorded for
these requests. A later successful request does not restore missed observations.
The provider's exact route/plan quota or other consumers of this key are unknown.

The retained container log window has 275 completion records; **all match the
database counters**. It begins at this container's creation, so it cannot explain
earlier containers. The log summary's 369 `other_unclassified` lines cannot be
treated as 369 independent unknown outages: structured scan telemetry identifies
the cooldown operations. No SQLite lock, timeout, 5xx, Telegram failure, traceback
or OOM evidence appears in the inspected bounded retained window.

GMGN documents plan-dependent weighted limits and shared cooldown behavior.
Preserve pacing and respect reset times; increasing request counts based solely
on spare CPU or short scans is unjustified. See the provider's
[market API reference](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md#rate-limit-handling).

## Coverage and signal loss

Effective configuration remains **Custom revision 7, Solana only**, threshold 60,
minimum base 8h, drawdown 40–95%, cap $50k–$15M, liquidity $10k, age 1h, holders 75,
1h volume $10k. Market/security/kline budgets are 40/8/12; request spacing 1.5s.
The history maximum gap is 900s, with at least three prior observations for the
five-minute baseline. High/normal/low watch targets are 150/600/1800s; the main
scanner itself only runs every 300s. Weights remain 30/40/30 across dimensions.

The sequential v2 funnel below covers **275 scans with prospective traces**,
not all 289 rolling-window scans. Counts are repeated scan/token observations.
The baseline stage is an intersection; activity also fired on 56 observations
without the full baseline, through alternate evidence in the scorer.

| Stage | Count | Previous-stage percentage |
|---|---:|---:|
| Presented ranking + due watch candidates | 36,585 | 100% |
| Cheap prefilter pass | 29,759 | 81.34% |
| Market enriched | 10,640 | 35.75% |
| Market filter pass | 3,665 | 34.45% |
| Baseline ready | 2,517 | 68.68% |
| Returning activity | 729 | 28.96% |
| Structure available | 625 | 85.73% |
| Base detected | 27 | 4.32% |
| Eligible | 27 | 100% |
| Potential alert / sent | 0 / 0 | 0% / undefined |

Across all last-24h presentations, structure is available in 711/18,376 (3.87%),
but **711/823 (86.39%)** among rows expected to receive it. Base detection is
**31/711 (4.36%)** among candle-evaluated observations. Calling all missing
structures “failed bases” would use the wrong denominator.

Overall valid baseline coverage is **2,845/18,376 (15.48%)**. The retained-snapshot
mature discovery segment is 2,740/4,886 (56.08%); watch-only presentations are
**4/7,904 (0.051%)**. These segments use the first retained market snapshot for
maturity and current discovery sources for watch classification.

Every one of 266 successful v2 scans exhausted the 40-market-operation budget.
All **18,759 traced market-budget deferrals were low-watch observations**.
Low-watch received 7,494 enrichments out of 26,523 presentations (28.25%);
710 passed market gates, but none had a ready baseline or returning activity.
For enriched low-watch tokens, time since the prior poll was typically **70 min**
and p95 **85 min**. Even the configured 30-minute interval exceeds the allowable
15-minute baseline gap. This creates a bootstrap problem: a low score keeps a
token on slow polling, which prevents the history needed to earn activity points.
The current warmup window ends 30 minutes after discovery, before a newborn token
can meet the live one-hour age gate. **87 distinct low-watch contracts** passed
market gates at least once during captured telemetry, without obtaining a baseline.

The general audit's uncensored first-enrichment sample has N=665, median 60.58 min,
p95 110.92 min and maximum 210.96 min; see the focused budget artifact for selection
and censoring details. These delays include deliberate age/market screening, not
only request queues. Of 826 captured new arrivals, 665 were enriched and 161 remained
right-censored; 154 of those never passed the prefilter. Another 380 contracts had
uncaptured or left-censored arrival times and were excluded. Initial Trending
arrivals had median discovery-to-enrichment 60.6 min, but median first observed
prefilter-pass-to-enrichment 10.8 min; Hot Search had corresponding 15.1 min and
19.4 seconds. Only seven captured arrivals passed but lacked enrichment at cutoff,
having waited 199–499 seconds. A last-observed backlog of 96 and lower-bound oldest defer
age of 22.81h are not a guaranteed current live queue: expired/absent candidates
can remain in the last-observed map. Expired-without-enrichment remains unknown.

Top10/sniper coverage is 100% of last-24h presentations, developer 60.95%, bundler
and dangerous assessments 56.99%, insider holdings 0%. These are field-presence
rates, not independent security audits. The code maps the documented
`suspected_insider_hold_rate`; the provider also documents insider **trading volume**
fields, which have a different meaning. The observed absence does not establish
that insider holdings are globally unsupported. See the
[GMGN security field definitions](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md).

## JEANPHIL and signal outcomes

The supplied call matches **legacy alert #5, October 7 18:46:47 Bangkok**, at
$4.561M cap and price $0.0047046842. Its legacy additive score 80 is not the
current normalized score 80; its listed factors would produce approximately
setup 60 / trigger 50 / confirmation 75 and overall 60 under current weights.
A 9h base does not meet the current 48h Strong Revival maturity guard.

Today the saved discovery/evaluation presentations reached **$13.702M at 15:03:36
Bangkok**, first exceeding $10M at 14:18:36. The latest enriched observation at
20:23:36 was **$9.258M**. Relative to the exact historical matching snapshot,
the latest price was +102.97%, and today's saved presentation peak +200.42%.
These are reconstructed quote returns; no legacy NULL baseline was overwritten.

The first 24h checkpoint was approximately **−11.72%**, with sampled first-day
drawdown **−23.17%**. This was delayed appreciation after an adverse first day.
The peak is a selected observation, not an achievable exit or complete candle MFE.
Some rally observations exist only in presentations because drawdown fell below
the 40% prefilter and stopped further snapshot/structure enrichment.

At **06:58:34 today**, JEANPHIL had score 43, setup 50 / trigger 70 / confirmation
0, returning activity and $6.568M cap. It failed `no_base` and threshold 60.
That row is an illustrative exploratory opportunity, identified with hindsight.
At the cutoff its trigger score had fallen to 20, despite returning TX activity;
five-minute volume was only 0.732× its recent baseline.

The one current-version alerted token, **EMBER**, had durable checkpoints
**+15.97% at 1h, +4.26% at 6h, −15.29% at 24h**. Sampled 24h upside/downside were
+28.91%/−22.07%, with gaps as large as 45 minutes. PAID and e/acc alerts from
separate legacy cohorts had 24h checkpoints −20.01% and −64.01%. Their counts and
models must not be pooled with the current strategy as a win rate.

Current cohort: 24,548 evaluations, 1,523 distinct tokens, 71 eligible observations
and **one sent alert**. Broad revision 5 with current scoring: 11,448 evaluations,
34 eligible observations, zero alerts. Other legacy cohorts retain six unknown-
configuration alerts plus one PAID and one e/acc alert. Effective settings hashes
separate reused revisions. **INSUFFICIENT SAMPLE** for score weights, profitability
or expected return. Current score-band entry counts are 18 tokens in 40–49, four
in 50–59, and one in 60–69; their uneven follow-up and correlated observations
cannot justify optimizing weights. No component has an established predictive edge.

The older narrow near-miss study (first rejected row, score ≥50, matured sampled
24h MFE >50%) found zero such winners. It does not include JEANPHIL's score-43
opportunity today or prove that no winners were missed. Today's guarded watch
cohort had five measurable 1h checkpoints: two positive, median **−7.08%**.
Raw watch40 had eight measured today, three positive, median **−10.11%**; COIN and
CONDOM fell 61.39% and 58.60% at those checkpoints. All watch cohort 24h horizons
are still immature; incomplete coverage and execution costs remain material.

## Observed policy comparison

Counts below deduplicate chain/address at first qualification within each window.
Guarded scenarios search **all rows** for the first guarded qualification, rather
than filtering only an earlier unguarded anchor. The analysis uses information
stored at each decision time; future quotes are used only for separately labeled
outcomes. Existing eligibility is preserved in strict threshold comparisons.

| Policy on observed rows | Rolling 24h distinct | Today distinct |
|---|---:|---:|
| Existing eligible, threshold 60 | 0 | 0 |
| Existing eligible, threshold 55 | 1 | 0 |
| Existing eligible, threshold 50 | 2 | 1 |
| Existing eligible, threshold 45 | 4 | 3 |
| Exploratory: setup ≥40, trigger ≥40, overall ≥40 | 12 | 9 |
| Same; age ≥6h, cap ≥$100k, liquidity ≥$30k | **7** | **5** |
| Same; age ≥12h, cap ≥$100k, liquidity ≥$30k | 5 | 4 |

The exploratory rule retains the current complete market gate, returning activity,
meaningful absolute activity floors, and excludes known danger, concentration
violations and stored negative risk components. It does **not** require a base.
Activity must satisfy the existing 5m volume/TX/volume-to-liquidity branch **or**
the existing 1h branch. A lower overall score alone does not remove base/activity
eligibility gates. Threshold 55 would admit PAID with bundler 32.56%; today's
threshold-50 e/acc opportunity had bundler 46.69%, beyond the 30% soft-penalty limit.

These are **observed opportunities, not exact deployment/backtest alert counts**.
Changed candidate ordering, enrichment/security calls, history, delivery outcomes
and cooldown trajectories are unobserved. Unknown risk fields cannot be filled
with zeros. Setting watch overall to 35 expands to 38 distinct today, a substantial
quality/noise change without supporting outcomes. The six-hour age guard is an
experiment motivated by very young candidates' risks, not a fitted five/day rule.

Today's five guarded opportunities were Agency 01:03:33, JEANPHIL 06:58:34,
TWEETCRAFT 08:28:35, fone 16:58:36 and BNKR 17:53:36, Bangkok time. They qualified
earlier; none should be resent solely because this retrospective report found them.
fone's latest evaluation lacks dangerous/bundler assessments; BNKR's latest is
38 minutes old and also lacks those fields. Both need a fresh market/security poll.

## Current conditional monitoring list

These five have usable recent market data, age ≥1 day, cap ≥$100k, liquidity ≥$100k,
meaningful absolute activity, known `dangerous=false`, and concentration within
measured limits. Prioritization favours a positive 1h move, returning activity,
membership in today's guarded opportunity cohort, then five-minute volume;
it is a monitoring
heuristic, not a validated return ranking. **None currently qualifies watch40**.
Insider holdings remain unknown for all five.

| Token, linked by exact CA | Latest Bangkok time | Cap / liquidity | Score; setup/trigger/confirmation | 1h price change | Why wait |
|---|---|---|---|---:|---|
| [HIGGS](https://gmgn.ai/sol/token/DoVAVzViX8Bjy3r15nwikSaSbzE6dV4ovd28aWpJpump) | 20:23:36 | $1.51M / $160.5k | 15; 50/0/0 | +5.66% | 5m volume $1,951, activity not accelerating, no base |
| [JEANPHIL](https://gmgn.ai/sol/token/GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump) | 20:23:36 | $9.26M / $476.6k | 23; 50/20/0 | −10.29% | Volume below recent baseline, no base |
| [TWEETCRAFT](https://gmgn.ai/sol/token/HzYCHqAN2uoHGRnL9v2ChCfFQX3bvJuJd5zu2Hd5MZQy) | 20:18:36 | $1.15M / $139.8k | 15; 50/0/0 | −14.34% | Activity not accelerating, no base; bundler 28.67% near limit |
| [Agency](https://gmgn.ai/sol/token/7VertkgF9KLhxxJXHX6uaWuoYZTP9LdGj2bWmVXVpump) | 20:23:36 | $5.13M / $358.8k | 15; 50/0/0 | −2.49% | 5m volume $1,386, activity not accelerating, no base |
| [PQC](https://gmgn.ai/sol/token/7K52aYQW9rWGjwZmQ7o2d1P6E7bji6hSMsqaLy5EcxLh) | 20:23:36 | $1.27M / $157.2k | 15; 50/0/0 | −8.86% | Active trading but no acceleration or base |

This snapshot cannot identify tickers that will “print money.” It identifies
specific candidates and the conditions still missing. Refresh these values before
acting; rising absolute volume or a prior pump alone is insufficient confirmation.

## Concrete implementation and evaluation plan

1. **Measure and repair coverage first.** Keep market budget 40, discovery limit
   20/source and 1.5s pacing initially. Add an explicitly bounded bootstrap cohort
   beginning at the first successful current market qualification, with meaningful
   absolute activity. Poll those watch tokens every scan until three prior valid
   observations exist (four successful polls over about 15 min), then maintain
   ≤600s gaps for promising watches. Allocate a measured reservation within the
   existing 40 operations (initial trial: about 10 slots), rotating cohort admission
   with aging across source tiers instead of repeatedly giving
   one poll to many low-quality stale watches. Reserve outcome follow-up capacity
   and report when it cannot be served. Do not enlarge the 900s gap merely to make
   stale observations count as a baseline. Measure first-enrichment time, baseline
   attainment, observed stale gaps and admission/expiration for every tier.

2. **Make shared cooldown visible and stop logical fanout.** On a long provider
   reset, leave affected candidates deferred with an explicit API-cooldown reason
   rather than attempting the remaining 40 operations locally. Keep the scan and
   source-failure counters truthful and preserve bounded retries. Record reset
   time and route/plan diagnostics without secrets. Test behavior with mocked
   requests; send no Telegram test alert without explicit message authorization.
   Consider budget 50 only after cooldown/fairness measurements show spare quota
   and improved baseline coverage; it could add up to 2,880 calls/day.

3. **Add the guarded exploratory lane as a separate experiment.** Use setup ≥40,
   trigger ≥40, overall ≥40, returning/meaningful activity, age ≥6h, cap
   $100k–$15M, liquidity ≥$30k, current drawdown 40–95%, holders ≥75 and 1h volume
   ≥$10k. Preserve existing change caps and asset exclusions. Require current
   known concentration fields within limits, fresh `dangerous=false`,
   `mint_renounced=true` and `freeze_renounced=true` before sending; missing or
   adverse assessments block notification pending refresh. All seven rolling-window
   first guarded observations and five today have those recorded values, preserving
   the observed 7/5 counts; prospective API refresh results remain unobserved.
   Label missing insider holdings and sampling limitations. Do not require an 8h
   base for **EXPLORATORY WATCH**, and display that base/confirmation is absent.
   Keep the revival lane at 60 with its existing base, activity and risk policy.
   Do not promote an exploratory notice to STRONG_REVIVAL.

4. **Count tickers and preserve upgrades.** Add a safe additive notification-kind
   field/default for existing revival records; never reset alert history. Give the
   watch lane its own once-per-token-per-Bangkok-day deduplication. A prior watch
   must never suppress a later stronger revival alert through the six-hour revival
   cooldown. Scope revival reservations to revival-kind records, excluding every
   watch status including pending and unknown; changing the schema alone does not
   change the existing query. Track the union of distinct tickers across lanes and lane-specific
   entry quotes, configuration fingerprints and 1/6/24/72h outcomes. Include the
   notification kind in outcome/health/calibration cohorts alongside the score
   version and settings fingerprint. Show a daily
   recap of qualifying distinct tickers; if fewer than five qualify, report the
   shortfall and blockers instead of recycling stale tokens to meet the target.

5. **Shadow, validate and roll out.** Run the frozen rule in shadow first, then
   collect at least seven full Bangkok days including matured 24h outcomes before
   judging the frequency target. Target ≥5 distinct research tickers/day; report
   the median, range, days reaching five, stale/unknown fraction and delivery
   status. A seven-day sample assesses frequency, not statistical profit. Compare
   quote outcomes, sampled upside/downside and coverage with matched observed
   non-alert tokens, holding configuration fixed. Test boundaries, chronology,
   duplicate prevention, stronger upgrades, risk unknowns, additive migration,
   rate limiting and resource budgets. Run repository lint/tests, Compose checks
   and offline validation before an authorized deployment. Rollback disables the
   new lane on a schema-compatible image, preserving additive history. The current
   schema-4 image refuses a newer schema version: a prior-image rollback requires
   an explicit compatibility test or a documented backup restore that archives
   subsequent records. Additive migration alone does not guarantee old-image startup.

The existing runtime override allowlist does not expose request budgets, low-watch
poll intervals or the new lane. A direct controls-file edit is not a safe live
reload; runtime settings load at startup and control writes can race. These changes
require implementation plus a validated owner-control path or controlled restart.
Do not simultaneously lower base duration, activity ratios, concentration limits
and the main revival threshold. Neither a smaller score nor “five per day” is a
justification to erase unknown safety data or guarantee gains.

## Priorities and limits

Technical priorities: (1) watch baseline bootstrap mismatch; (2) across-tier budget
deferrals; (3) shared cooldown fanout/visibility; (4) off-ranking outcome capacity;
(5) privately archived diagnostic retention and file growth. Quantitative risks:
(1) one current alerted token; (2) legacy/current scoring mismatch; (3) incomplete,
selected follow-ups; (4) five/day target and survivor/hindsight bias; (5) unknown
insiders, manipulated activity and concentration despite apparent liquidity.

Today’s enriched-snapshot medians are 5m volume $33.30, 1h volume $3,461.53,
liquidity $8,576.59 and 5m TX 2. The retained three-day mix has corresponding
medians $60.26 / $10,082.90 / $10,727.22 / 3. These unequal, enrichment-selected
windows do not establish a Solana market regime or causally explain alert silence.
Historical ranking/security values not retained at each earlier decision and
raw closed candles for alternate base thresholds cannot be invented. Preserve
source-time and received-time provenance for future walk-forward comparisons.

Validation completed: full-history and rolling-24h read-only audit commands,
manifest/checksum verification, independent VPS row/integrity/24h aggregates,
all ten runtime module hash comparisons, chronological first-match checks and
log/database counter reconciliation. This follow-up changes documentation only;
the production strategy experiments above remain unapplied.
