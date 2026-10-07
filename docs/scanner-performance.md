# Scanner performance (issue #2)

The scanner now rejects known discovery failures before token-info requests, uses
market history to decide whether candles are needed, and requests security only for
serious candidates. Thresholds, presets, weights, scoring formulas, discovery limits,
request pacing, retries, cooldowns and alert delivery rules remain unchanged.

## Pipeline and compatibility

```text
Hot Search + Trending → merge → cheap discovery prefilter
  → token info → market filter → history / activity
  → closed candles if activity returns → security for serious candidates → score
```

The prefilter uses existing thresholds only when the discovery field is known.
Missing fields pass to market enrichment; they are never converted to zero. Ranking
volume belongs to its declared interval, so 5m or 24h volume cannot fail the 1h
volume threshold. Existing asset exclusions also apply. Known prefilter rejects
still receive diagnostic evaluations, but do not create market-history snapshots.
Watchlist observations have unknown live metrics and proceed to market enrichment.

After token info, missing essential market fields prevent eligibility as before.
Candidates with no returning volume/transaction activity skip candles and security.
Candles must establish the existing base requirement. Security is requested when
the unchanged scorer, with security deductions provisionally absent, could meet the
alert threshold. This optimistic check lets fresh security replace stale discovery
concentration deductions. Known dangerous tokens remain ineligible. Security then
uses the same normalization and field precedence as the previous unified client;
optional security failures retain the existing nullable-data warning policy.
Scores without fetched candles have unavailable structure, rather than invented
patterns. The scoring formulas themselves have not changed.

`enrich_market` and `enrich_security` separate production GMGN requests. The legacy
`enrich` convenience method remains available. Old third-party adapters using only
`enrich` remain callable, but their internal security work cannot be separated.
SQLite schema version 2 and existing snapshots/alerts stay compatible. Final metrics
use the existing state table under `scan:metrics:<id>`, are removed with seven-day
scan diagnostics, and do not require a migration. Legacy scans show unknown metrics.

## What is measured

**Health → Performance** shows the latest scan's total duration and discovery,
market, security, candles, SQLite and Telegram operation durations. **Funnel** shows
candidate counts through each stage. `ScanReport.performance` and `.funnel` expose
the same measurements for scripts. Explicit zeros mean an operation was skipped;
legacy or unfinished scans have no completed metric record.

- Durations use a monotonic clock and include API pacing, retries and parsing.
  Candle timing includes structure analysis. Multiple chains may overlap, so stage
  durations are not additive and can exceed total wall time.
- SQLite timing covers outer scan repository operations, including serialization
  and diagnostic calculations. Nested history calls are counted once. Unrelated
  background health queries are excluded. The final metric-record write is excluded
  to avoid measuring the persistence of its own timing.
- API counters count actual HTTP attempts at each of the five documented endpoints,
  including retries and failed attempts. Calls blocked by an existing cooldown do
  not increment the counter. Operation-failure counts describe failed operations,
  not every HTTP retry. No credentials, request headers or credential-bearing URLs
  are recorded in scan metrics.
- Funnel counts describe operations, including watchlist work, rather than disjoint
  populations. Discovery counts exclude watchlist additions. Data coverage includes
  prefilter evaluations with unfetched fields; it is not an API success rate.

## Actual Solana measurements

Recorded on 2026-10-07 against baseline commit
`365fbfd12f2ccf058f29b932c5c58f88ceabd108`. Each benchmark uses a fresh temporary
database, Solana only, Strict defaults, 20 results per discovery source, a watchlist
limit of 100, and dry-run with Telegram credentials/destination cleared. It does not
change the live VPS configuration or database.

| Measurement | Before live | After live | Before exact-cohort replay | After exact-cohort replay |
|---|---:|---:|---:|---:|
| Total elapsed seconds | 115.985463 | 18.199205 | 0.492662 | 0.041193 |
| Actual HTTP attempts | 78 | 7 | 78 | 6 |
| Hot Search | 1 | 1 | 1 | 1 |
| Trending | 1 | 1 | 1 | 1 |
| Token info | 36 | 5 | 36 | 4 |
| Security | 36 | 0 | 36 | 0 |
| Candles | 4 | 0 | 4 | 0 |
| Unique discoveries / evaluations | 36 / 36 | 36 / 36 | 36 / 36 | 36 / 36 |
| Cheap prefilter survivors | not implemented | 5 | not implemented | 4 |
| Market filter survivors | 4 | 5 | 4 | 4 |
| Returning activity / detected bases | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Serious candidates / eligible / sent | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Errors | 0 | 0 | 0 | 0 |

The live runs are real measurements, but **not a controlled latency comparison**:
the market changed between runs, and the successful optimized run used slower
three-second benchmark pacing versus 1.5 seconds before. Production pacing remains
unchanged. Two intervening optimized runs received Trending HTTP 429 responses;
the benchmark stopped further requests. Their partial results are retained as
`issue2-after-live-rate-limited*.json` and are not used as successful speedups.
The cause of the rate limits is not established by this sample.

The exact-cohort replay uses the baseline run's captured public responses and time.
It verifies **78 → 6 requests (92.3% fewer)** on identical discoveries and unchanged
market survivors. Its elapsed seconds measure local processing with mock HTTP and
are **not network latency estimates**. These are cold-start scans: without prior
history no activity baseline warmed up. They do not establish steady-state scan
duration, recall, or a guaranteed short cadence on an existing watchlist. Tests with
seeded history exercise active candidates, candles, fresh security, unchanged scores,
and security failures.

Adapter-level request timings (including waits) from the live benchmark:

| Operation | Before seconds | After seconds |
|---|---:|---:|
| Discovery | 1.843138 | 3.209834 |
| Market | 54.350248 | 14.962563 |
| Security | 53.577010 | 0 |
| Candles | 6.124848 | 0 |
| SQLite repository operations | 0.048935 | 0.013925 |
| Telegram | 0 | 0 |

Adapter request timings differ slightly from production stage timings because the
production measurements also include parsing/analysis. The JSON artifacts contain
both, plus full stage counts and the effective non-secret configuration.

## Reproduce

From an installed development checkout, replay without API or Telegram credentials:

```bash
PYTHONPATH="$PWD/src" python scripts/benchmark_scanner.py \
  --replay docs/benchmarks/issue2-solana-responses.json.gz \
  --label optimized-replay --output /tmp/optimized-replay.json
```

For the baseline, run the same script with `PYTHONPATH` pointing to `src` in a clean
checkout of the baseline commit. The compressed tape contains public market/security
response envelopes, with credential values and credential fields removed; it contains
no request headers. It is an offline fixture, not a refreshed market feed.

A live benchmark requires `GMGN_API_KEY` in the specified `.env` file:

```bash
PYTHONPATH="$PWD/src" python scripts/benchmark_scanner.py \
  --env-file .env --request-spacing-seconds 3 \
  --label sol-live --output /tmp/sol-live.json --record /tmp/sol-tape.json
```

The optional spacing override permits slower pacing only; it does not alter saved
configuration. Mock replay uses negligible pacing without making network calls.
The tool stops subsequent requests after an authentication or rate-limit response.
Do not use a partial rate-limited scan as evidence of a completed faster scan.
Full results and the replay tape are in [benchmarks](benchmarks/).
