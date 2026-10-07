# Persistent enrichment caches and scan budgets (issue #3)

Solana remains the default and example enabled chain. Discovery coverage stays at
20 results per source and 100 watchlist entries per chain. This change does not
alter scoring formulas, thresholds, presets, Telegram delivery rules or GMGN's
request pacing/retries/shared cooldown. It adds a transactional schema version 3
migration with two new tables; existing snapshots, alerts, diagnostics and saved
Telegram presentation/metric records are preserved.

## Configuration

Add or override these values in `.env` and restart the service:

| Setting | Default | Meaning |
|---|---:|---|
| `SECURITY_CACHE_TTL_SECONDS` | 1800 | Reuse successful security data for at most 30 minutes; 0 disables reuse |
| `KLINE_CACHE_TTL_SECONDS` | 900 | Retry interval for empty candle responses; 0 disables reuse |
| `MAX_MARKET_ENRICH_PER_SCAN` | 40 | Market operations across the entire scan |
| `MAX_SECURITY_ENRICH_PER_SCAN` | 8 | Security fetch operations, excluding cache hits |
| `MAX_KLINE_FETCH_PER_SCAN` | 12 | Candle fetch operations, excluding cache hits |
| `WATCHLIST_HIGH_SCORE_INTERVAL_SECONDS` | 150 | High-priority polling interval |
| `WATCHLIST_NORMAL_INTERVAL_SECONDS` | 600 | Medium-priority polling interval; low-priority uses three times this |
| `WATCHLIST_EXPIRE_HOURS` | unset | Optional lifetime since last discovery; unset preserves `WATCHLIST_HOURS` (24 by default) |

Budgets can be zero to defer that class of fetch entirely. Failed operations consume
budget. Existing HTTP retries are still paced and counted as actual attempts, so
an operation budget is not a hard cap on HTTP attempts when retries occur. Cache
hits do not consume fetch budgets. No faster request-rate setting is introduced.
New performance settings are recorded in the non-secret scan configuration context.
Existing Telegram strategy presets and controls continue to work.

## Cache freshness

Security entries are keyed by chain/address and retain the complete normalized
security assessment: top10, developer, insider, sniper and bundler ratios, flags,
danger assessment, mint/freeze state, and other available fields. Missing fields
remain unknown. Fresh token-info statistics retain their previous field priority;
a cached safe assessment cannot override a newly known dangerous flag. Messages
identify reused security data and its age. Cache age is bounded by both the saved
expiry and the current configured TTL, so shortening TTL applies immediately after
restart. Invalid, expired, future-dated or failed entries are not used as fresh data.
Optional network failures retain the existing nullable-security warning policy;
a security request deferred by budget suppresses an alert for that scan.

Closed 1h candles are keyed by chain/address/resolution/lookback. Nonempty closed
bars are immutable during the same UTC hour, so they are reused even after the
900-second retry TTL. **A new UTC hour invalidates them regardless of TTL.** This
avoids redownloading unchanged bars every 15 minutes while refreshing as soon as a
new closed bar can exist. Empty responses retry after the configured TTL or the
next hour, whichever comes first. Unfinished bars are never stored or analyzed.

For adjacent hourly periods, the GMGN client fetches from the last stored candle,
merges with one overlapping bar, deduplicates timestamps and trims to the configured
lookback. A missing cache, changed lookback, invalid data or multiple missed hours
uses the full lookback. Failed refreshes do not replace the successful cache and
are retried later; expired data is not silently substituted. Existing stale-candle
and gap checks still decide whether structure is available. Cache rows persist
across restarts and are pruned once their expiry is older than diagnostic retention.

## Candidate priority and watchlist lifetime

Discovery is gathered and merged before any expensive work. The merged population
is sorted globally so concurrent discovery completion cannot change budget winners.
The issue's source tiers are disjoint:

1. Current Trending only.
2. Current Hot Search only.
3. Present in both current sources.
4. Due high-priority watchlist candidates.
5. Due medium-priority / near-miss candidates.
6. Due low-priority / aging candidates.

Within each tier, previously deferred candidates go first, followed by current rank
or last score, oldest polling time, recent discovery time, and chain/address ties.
This is deterministic and rotates deferred candidates within their tier. Higher
source tiers can still consume the full market budget; budgets do not guarantee
service to every tier on every scan. Increasing a budget preserves more polling
coverage at the cost of more calls. Discovery limits are not reduced to meet budgets.

High-priority watchlist entries have a last score of at least 60, or are recent
market-filter survivors still warming their activity baseline. The warm-up window
is twice `MINIMUM_HISTORY_OBSERVATIONS × SCAN_INTERVAL_SECONDS` (30 minutes with
defaults). Scores 40–59 are medium; weaker entries are low. High/medium intervals
are capped at 75% of `HISTORY_MAX_GAP_SECONDS` to avoid deliberately scheduling
past the existing finite-history budget. Low-tier aging entries can have unavailable
acceleration until sufficient fresh observations exist again; no history is fabricated.

Current discoveries are inspected regardless of watchlist due time. Polling never
extends discovery lifetime. Deferred discoveries, including ones with no market
snapshot yet, are persisted and remain due for subsequent scans until they expire
from last discovery. Candle/security budget deferrals keep the candidate due;
completed evaluations update score/tier/next polling time. Existing installations
seed watch state once from actual discovery snapshots and latest saved scores,
without changing historical rows. `WATCHLIST_LIMIT` limits selected due entries,
not their persistence. Off-ranking candidates clear live ranking/market fields and
refresh token info as before; only identity and historical ATH cap are retained.

## Metrics and overlap

**Health → Performance** exposes `market_fetch`, `security_fetch`, `kline_fetch`,
`security_hit`, `kline_hit`, incremental updates, invalid-cache counts, and deferred
market/security/candle operations. These are also in `ScanReport.performance` and
persisted scan metrics. Endpoint counters still measure actual attempts including
retries; funnel request counts exclude cache hits and budget-only deferrals.

The service awaits each scan before calculating the next delay. A cadence overrun
starts the next scan only after completion. Manual scan requests cannot launch a
second scan while one is running. Tests cover both manual requests and an overrun.
This describes one running service process; independent duplicate deployments are
not coordinated by a distributed scheduler.

## Measured Solana repeat-scan benchmark

The benchmark replays the issue #2 public response tape offline against baseline
`e3a6ff8c99458d7a243ffe04c7da494854b465c7` and this implementation. Both discover
and evaluate the same 36 Solana tokens, with 20 results per source, watchlist limit
100, and no Telegram calls. Four prior observations per market survivor are
explicitly synthetic (volume / 6, transactions / 4, at 300-second intervals) to
exercise returning activity. Two scans use timestamps 300 seconds apart within
the same hour. No cache is primed before the measured first scan.

| Measured result | Before scan 1 | Before scan 2 | After scan 1 | After scan 2 |
|---|---:|---:|---:|---:|
| HTTP attempts | 10 | 10 | 10 | 6 |
| Hot Search / Trending | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 |
| Market info | 4 | 4 | 4 | 4 |
| Candles | 4 | 4 | 4 | 0 |
| Security | 0 | 0 | 0 | 0 |
| Candle cache hits | 0 | 0 | 0 | 4 |
| Discoveries / evaluations | 36 / 36 | 36 / 36 | 36 / 36 | 36 / 36 |
| Eligible / alerts / errors | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Local elapsed seconds | 0.093000 | 0.061111 | 0.061688 | 0.048529 |

The second scan uses **40% fewer requests**; across both scans, 20 → 16 attempts
is a 20% reduction. These are actual mock-transport attempts and processing times,
**not live network latency estimates or proof of trading recall**. No candidate
established a qualifying base, so this sample does not measure security-cache
savings. Separate tests verify actual HTTP security reuse, TTL expiry, restart,
all security fields, budgets and watchlist tiers. Results are in
[before](benchmarks/issue3-before-repeat.json) and
[after](benchmarks/issue3-after-repeat.json).

Reproduce without API keys or network access, from an installed development checkout:

```bash
PYTHONPATH="$PWD/src" python scripts/benchmark_repeat_scans.py \
  --label after --output /tmp/issue3-after-repeat.json
```

Run the same script with `PYTHONPATH` pointing to `src` in a clean baseline checkout
for comparison. The script uses a temporary database, Solana-only dry-run and cleared
Telegram credentials; it does not modify the VPS configuration or database.
