# OnchainFlow V2 architecture

Status: proposal for owner review (2026-10-14). Nothing here is implemented.

## 1. Verdict on the current stack

**Keep React/TypeScript + Vite, FastAPI, SQLite (WAL), one scanner, Docker + Caddy.**

Evidence: 1.5 GB database, ~100K evaluations/week, one writer, public reads are
saved-data-only and bounded at 1.5 s, the 7-day report is ~1 s cold, and the
production-copy replay passed 672/672 reads. The product's problems are in the
call model and presentation, not in the framework or database. A Postgres or
frontend-framework migration would not fix any defect found in this audit.

Revisit SQLite only if: sustained writes exceed one scanner, the DB passes
~10 GB, or multi-user accounts (#46) need concurrent writes. Mitigation before
then: retention/pruning of `evaluations` and `state['telegram:evaluation:*']`
(102K rows, the bulk of the file).

## 2. Core concept: the Call

```
Scan ─▶ Evaluation ─▶ Alert decision ─▶ Call (alerts.id) ─▶ Telegram message
                                          │
                                          ├─ Call snapshot (immutable: state JSON, alert_signals, research_alert_facts)
                                          ├─ Checkpoints +1/+6/+24/+72h (immutable, price-based)
                                          └─ Call performance projection (mutable, recomputed)  ◀── token_snapshots
```

- **Call ID = `alerts.id`**, shown everywhere as `#<id>` (Telegram, API, all pages).
- A **token** has 0..n calls. Grouping by token is a view, never a merge.
- **Call snapshot** is immutable and already exists. No schema change needed.
- **Call performance** is new and mutable by design: latest observed MC/price,
  `latest_observed_at`, change since call, MFE/MAE, observation count, largest
  observation gap, freshness state. It is derived, never a source of truth, and
  can be rebuilt from `token_snapshots` at any time.

## 3. Call performance read model — decision

| | Option A: compute on read (recommended first) | Option B: scanner-maintained projection table |
|---|---|---|
| How | One SQL per page: per call, latest snapshot via `snapshots_token_time` index + `MAX/MIN(market_cap)` over `(call_time, now]` | `call_performance(alert_id PK, …)` updated by the scanner after each snapshot insert for calls < 30 days old |
| Cost now | 49 calls × ≤800 rows ≈ 40K index rows per uncached request; fits the 1.5 s budget with the existing response cache | Constant per read |
| Migration | None | Additive schema 8 + backfill (derived data, safe) |
| Risk | Grows with calls × age; needs a bound (e.g. 90-day window, paginated) | Another scanner write path; must not slow scans |

**Recommendation:** ship A behind the existing bounded cache, with a load test at
10× today's call count. Move to B only if the gate fails. Either way, the derived
values are documented as "observed saved snapshots", not intrabar highs.

Definitions are in `docs/audit/ALERT_RECORD_CONSISTENCY.md` §4. Unknowns stay
`null` with a reason code, never `0`.

## 4. CallPerformanceCard contract

One API shape (`CallSummary`) and one React component used by Signals, Track
Record, Token and Signal detail.

```
CallSummary {
  call_id, chain, contract_address, symbol, name,
  called_at, delivered_at|null,
  call: { market_cap|null, price|null, liquidity|null,
          score, setup, trigger, confirmation, stage, preset, revision, score_version },
  latest: { market_cap|null, price|null, observed_at|null, freshness: fresh|stale|none },
  change_since_call_pct|null, change_reason|null,
  elapsed_seconds,
  mfe_pct|null, mae_pct|null, observations, max_gap_seconds,
  checkpoints: [{ horizon: 1|6|24|72, state: observed|pending|missed|unavailable, return_pct|null, observed_at|null }],
  reasons: [plain-language top 3], risks: [plain-language top 3],
  token_call_index: k, token_call_count: n   // "call 3 of 12 on this token"
}
```

Visual hierarchy (390px first): `$SYMBOL · chain · #id` → **MC at call → latest
MC** and **change since call** (largest type) → elapsed and freshness → MFE / MAE →
checkpoint strip → stage/score/revision → reasons/risks → actions (token, GMGN,
original call, chart, other calls on this token). Colour never alone conveys
direction; the sign and word ("up/down") are always present.

## 5. Published calls vs. watch observations

Today every eligible observation ≥ the threshold becomes a Telegram call. V2
separates:

- **Call**: published (Telegram + Signals + Track Record), stage-gated
  (`EARLY_REVIVAL`+ by default), re-alert only on new evidence.
- **Watch**: website-only list from Radar, never counted in the Track Record.

This is a policy change in `Scanner.process` (`src/revival_radar/scanner.py`, the `alert_score_threshold` check) and `Repository.reserve_alert` (cooldown/re-alert rule), plus runtime settings. It
needs owner approval and a measured trial (see plan #70 (V2-Q1)).

## 6. Research data layer

- Keep scanner-owned writes; website remains read-only.
- Add a reproducible **research export** CLI (`python -m revival_radar.research
  export --db <copy> --since … --out dir/`) producing calls, call performance,
  checkpoints, cohort baselines and funnel as CSV/Parquet from a DB **copy**. The
  audit scripts in `docs/audit/v2-analysis/` are the prototype.
- Log a per-scan **regime context** (universe median 1h/24h change, eligible
  count) into `scan_runs` JSON for explanation and later research.
- Backfill/replay: see `docs/audit/backtest-readiness.md`. Candle-only replay is a
  partial reconstruction and must be labelled so; faithful replay needs saved
  evaluations, which pruning currently removes. Retain evaluations for called
  tokens and a random sample of non-called tokens indefinitely.

## 7. Non-goals for V2

No framework or database migration, no paid infrastructure, no automated trading,
no live weight changes without a measured trial, no new browser-side provider
calls, no subscriptions or billing before the V2 release gate passes.
