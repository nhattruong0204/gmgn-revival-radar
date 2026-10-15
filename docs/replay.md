# Replay-grade evidence and point-in-time replay

Issue #74. Two parts: a scanner-owned, append-only retention policy for the evidence needed
to re-score history, and a read-only harness, `python -m revival_radar.research replay`,
that re-scores saved evaluations under a candidate configuration without looking ahead.
Both are research tools. Nothing here changes the live scoring, the alert path, the provider
budgets or what the website reads.

## Retention policy

Pruning is unchanged: evaluations, scan runs, `telegram:evaluation:*` presentations and
expired cache rows still disappear after seven days. Before that delete, in the same
transaction, `Repository.prune_diagnostics(before, settings)` copies the evidence worth
replaying into two append-only tables (schema 9, additive `_migrate_replay_archive`, no
backfill, `UPDATE`/`DELETE` rejected by trigger):

| Table | Content |
|---|---|
| `replay_evaluations` | the evaluation row plus the full saved presentation (enriched token as scored, signal, nonsecret configuration), zlib-compressed. Unique on chain, contract, timestamp |
| `replay_candles` | the 1h candle series in the candle cache when an evaluation used candles, zlib-compressed, deduplicated by content hash |

What is kept, indefinitely:

1. **Called window.** Every evaluation of a token that was ever sent as a call, within
   `REPLAY_CALLED_WINDOW_DAYS` (default 3, maximum 6 so it stays inside the seven-day prune)
   of any call. Reason `called_window`.
2. **Comparison sample.** Every evaluation of a deterministic sample of other tokens,
   `REPLAY_SAMPLE_FRACTION` (default 0.05) of tokens chosen by
   `sha256("chain:contract")`, so the sample is reproducible and nested (raising the
   fraction only adds tokens). Reason `sample`. Bounded by
   `REPLAY_SAMPLE_MAX_MEGABYTES` (default 1024, compressed bytes); when reached, new sample
   rows are skipped and a warning is logged. Called-window rows are never capped.
3. **Candles.** When an evaluation actually used candles (structure available), the cached
   current-hour series is copied at write time (`record_evaluation`). Which tokens end up
   called is unknown at that time, so candles are archived for every evaluation that used
   them; identical series are stored once. The measured cost is small (below).

`REPLAY_RETENTION_ENABLED=false` switches the archive off. Existing snapshots, alerts and
outcomes are never rewritten (`token_snapshots` is not pruned at all and remains the price
history). Evaluations that were already pruned before this ships cannot be recovered; replay
labels them legacy.

### Measured storage and projection

Private production copy of the audit week (read-only, `dbstat`; only summary numbers are
recorded here). Database 1.52 GB, schema 7, 2026-10-07 to 2026-10-14.

| Item | Measured |
|---|---|
| Evaluations | 102,782 in 7 days (7.2k to 19.0k per day), 2,759 distinct tokens |
| Evaluation presentations (`state telegram:evaluation:*`) | 589 MB raw, 5.7 KB each, about 84 MB per day; 53 percent is the repeated configuration |
| Expression index `state_report_configuration` | 471 MB, about 67 MB per day, pruned with the rows |
| Token snapshots (not pruned) | 97.7k rows, 187 MB, about 14 MB per day |
| Kline cache | 244 rows, 5.1 KB raw, 1.3 KB compressed each (overwritten in place) |
| Sent calls | 49 on 16 tokens |

Applying the default policy to that week:

| Retained | Evaluations | Raw | Compressed (archive size) | Per day |
|---|---|---|---|---|
| Called window (3 days) | 10,538 | 63.2 MB | 23.4 MB | about 3.3 MB |
| 5 percent sample (145 tokens) | 4,849 | 27.9 MB | 10.2 MB | about 1.5 MB |
| Candles (1,680 distinct token-hours with structure) | n/a | n/a | about 2 MB | about 0.3 MB |
| **Total** | 15,387 (15 percent of evaluations) | 91 MB | about 36 MB | **about 5 MB** |

Projection: roughly 5 MB per day, about 1.8 GB per year, against 84 plus 67 MB per day of
hot, pruned evaluation data. The sample part is capped at 1 GB; the called part scales with
the number of calls (this week had 49 on 16 tokens, a high rate), at about 2.2 KB compressed
per retained evaluation. The archive carries no expression index, which is what makes it small.
The disk figure should be rechecked after a month of production data before raising the
fraction or window.

## The replay command

```bash
sqlite3 data/revival_radar.db "VACUUM INTO '/path/outside/repo/copy.db'"
python -m revival_radar.research replay --db copy.db \
  --since 2026-10-07T13:36:00Z --until 2026-10-14T13:37:00Z \
  --config '{"alert_min_stage":"EARLY_REVIVAL","realert_requires_new_evidence":true}' \
  [--baseline-config current|<json or file>] [--split 2026-10-11T00:00:00Z] \
  [--out replay.md] [--json-out replay.json]
```

Same safety as `export`/`report`: `mode=ro` plus `query_only`, a refusal for the configured
live database unless `--i-know-this-is-a-copy`, outputs outside the repository.

How a replay works:

* **Inputs per evaluation.** The saved presentation (the enriched token exactly as scored),
  the token's snapshots with `timestamp` strictly before the evaluation (the same
  `history()` query and `fresh_history` windows, using the candidate's
  `history_observations`, cadence and gap settings), and the saved or archived structure.
  Nothing timestamped at or after the evaluation is read for a decision. Evaluations come
  from the live table and `replay_evaluations`, ordered by time, deduplicated.
* **Settings.** `--baseline-config current` (default) is the settings recorded in each
  evaluation's `configuration.settings`, i.e. the configuration in force then. `--config`
  is a JSON object of overrides applied on top of those recorded settings. Unknown or
  invalid keys are refused.
* **Re-scoring.** The unchanged `score_token`. When the original evaluation used candles
  and the exact series is archived, the structure is re-derived with `analyze_structure`
  under the candidate settings (closed bars only, the scanner's staleness rule).
* **Decision.** Threshold, `alert_min_stage`, cooldown and `realert_score_increase`, and
  `realert_requires_new_evidence` through the shared `call_policy.new_evidence_block`
  (also used by the repository). Sent calls before the window seed the cooldown state.
  Every accepted call counts as sent: delivery, pausing, muting and `dry_run` are not
  simulated.
* **Outcomes.** Observed forward results for each simulated call come from saved snapshots
  through `call_metrics.market_performance`. They are attached after the fact and never
  influence a decision. With `--split`, design-period outcomes are observed only up to the
  split (purged), holdout outcomes up to `--until`.

### Fidelity labels

| Label | Meaning |
|---|---|
| `faithful` | payload and configuration retained; if the evaluation used candles the series is archived, so structure is re-derived |
| `partial (candle-only/legacy: saved structure reused ...)` | payload retained, candles not: the saved structure is reused. Exact unless the candidate changes a structure setting |
| `partial (candle-only/legacy: no replayable payload ...)` | no payload or no recorded settings: only the recorded score, stage and eligibility can be gated; nothing is re-scored |

Counts per label are always shown, summaries are always broken out per label, and the
baseline re-score is compared with the recorded score, stage and eligibility per label.
Evidence from before this retention ships is `partial` by construction.

### Walk-forward and tuning

`--split` reports the design period `[since, split)` and the holdout `[split, until]`
separately. **Live settings must not be tuned on the holdout.** Pick settings on the design
period, look at the holdout once, and treat anything under a few dozen independent calls as
descriptive (calls cluster on few tokens; see `docs/audit/SEVEN_DAY_SIGNAL_REVIEW.md`, 8 and
9).

### Limits

* A conditional re-score of saved evaluations. Discovery, watchlist timing, provider budgets
  and deferral queues are not replayed, so tokens the scanner never evaluated cannot appear
  and a candidate that would have fetched candles for more tokens cannot show them.
* The saved token is the post-enrichment view; later-corrected provider values are not
  recoverable.
* Scores written by an older `score_version` re-score differently under current code. The
  validation below shows this explicitly.
* Output is research observation, not an executable return and not a recommendation.

## Validation: audit week on the production copy

Actual configuration in force, `2026-10-07T13:36Z` to `2026-10-14T13:37Z`:
102,782 evaluations replayed in about 40 seconds (96,619 of 97,468 retained-payload
evaluations re-score to the identical score, stage and eligibility; the rest differ).

| Quantity | Result |
|---|---|
| Actual sent calls | 44 |
| Reproduced by the baseline replay | 41 |
| Not reproduced | 3, all on 2026-10-07 (see below) |
| Simulated calls never published | 0 |
| Fidelity of all 102,782 evaluations | faithful 97,468 (no calls: structure reused), partial saved-structure 4,578, partial legacy 736 |

The 3 mismatches: one is an evaluation without a replayable payload or recorded
configuration (`partial_legacy`: no settings were saved, so the default threshold was
assumed); two are evaluations written by an older score version whose recorded scores (75,
70) the current code re-scores to 46 and 44. Both reasons are printed in the report.

Every one of the 41 matched calls sits on an evaluation that used candles, and the candle
archive does not exist in the copy, so all of them are `partial (saved structure reused)`.
Faithful replays of called evaluations become possible only for data collected after this
ships. Candidate `alert_min_stage=EARLY_REVIVAL` with `realert_requires_new_evidence=true`:

| Set | Calls | Tokens | Median change since call | Mean | MFE >= 25% | Label |
|---|---|---|---|---|---|---|
| Baseline (in force) | 41 | 13 | -10.7% | -18.7% | 7 | partial |
| Candidate | 3 | 3 | -23.6% | -29.1% | 1 | partial |
| Removed by candidate | 39 | 11 | -10.7% | -18.0% | 6 | partial |
| Added by candidate | 1 | 1 | -23.6% | -23.6% | 0 | partial |

Walk-forward at 2026-10-11T00:00Z: design baseline 7 calls, candidate 1; holdout baseline 34
calls, candidate 2. Three candidate calls cannot support any conclusion. This is research
output, not a recommendation to change live settings.
