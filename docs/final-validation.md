# Final Solana validation — 2026-10-07

These are actual measurements from the issue #5 implementation, not estimated
latencies or invented trading outcomes. Both benchmarks use isolated temporary
SQLite databases, Solana only, 20 discovery results per source, a 100-token watchlist,
and dry run. Telegram credentials are cleared in memory and no messages are sent.
No production database, settings or service is modified.

## Live GMGN dry run

Measured 2026-10-07 at 15:57 UTC with securely stored credentials and a slower
**3-second request spacing** override. Saved runtime pacing remains 1.5 seconds;
the configured target scan interval remains 300 seconds. Full non-secret results:
[final live measurement](benchmarks/issue5-final-live.json).

| Metric | Measured |
|---|---:|
| Duration | 18.243793 seconds |
| Hot Search / Trending attempts | 1 / 1 |
| Token info attempts | 5 |
| Security / candles / Telegram attempts | 0 / 0 / 0 |
| Successful HTTP responses | 7 × HTTP 200 |
| Discovered / evaluated | 38 / 38 |
| Prefilter / market pass | 5 / 5 |
| Baseline ready / activity trigger / base | 0 / 0 / 0 |
| Eligible / potential alerts / sent | 0 / 0 / 0 |
| Errors / rate-limit stop | 0 / no |
| Production stage time: discovery | 3.387476 seconds |
| Production stage time: market | 14.823090 seconds |
| Production stage time: SQLite | 0.021053 seconds |

This is a cold temporary database without baseline history or existing sent alerts.
Zero eligible signals is expected in that sample and does not establish steady-state
recall, outcome polling cost, or a guaranteed short cadence. Security and candle paths
were not needed; this live scan therefore provides no security-cache saving estimate.
Market conditions and pacing differ from earlier live benchmarks, so it is not a
controlled before/after speed comparison. Operation times include waiting and may
overlap; their sum need not equal wall-clock duration.

## Repeat public-response replay

[Final repeated measurement](benchmarks/issue5-final-repeat.json) replays the same
captured public Solana HTTP responses used in the issue #3 benchmark. Four market
survivors receive explicitly synthetic history (four observations, 300 seconds apart;
volume divided by six, transactions by four). Two scans run 300 simulated seconds
apart within the same UTC hour. No caches are primed before the first scan.

| Metric | First scan | Second scan |
|---|---:|---:|
| Measured local duration, seconds | 0.049145 | 0.045095 |
| HTTP attempts | 10 | 6 |
| Hot Search / Trending | 1 / 1 | 1 / 1 |
| Market info | 4 | 4 |
| Candles | 4 | 0 |
| Candle cache hits | 0 | 4 |
| Security requests | 0 | 0 |
| Discoveries / evaluations | 36 / 36 | 36 / 36 |
| Baseline ready / activity trigger | 4 / 4 | 4 / 4 |
| Eligible / alerts / errors | 0 / 0 / 0 | 0 / 0 / 0 |

The second scan saves 40% of requests, and the two scans use 16 total attempts.
These are measured mock-transport calls and local processing times, **not live network
latency**. This cohort has no qualifying bases or sent alerts; security-cache reuse
and checkpoint tracking are validated by the deterministic integration tests instead.

## Reproduce

From an installed checkout, without live API access:

```bash
PYTHONPATH="$PWD/src" python scripts/benchmark_repeat_scans.py \
  --label final-solana-repeat --output /tmp/final-repeat.json
```

For a live cold Solana dry run using your existing secure `.env`:

```bash
PYTHONPATH="$PWD/src" python scripts/benchmark_scanner.py \
  --env-file .env --request-spacing-seconds 3 \
  --label final-solana-live --output /tmp/final-live.json
```

The live script clears Telegram configuration, retains credentials only in memory,
stops after authentication/rate-limit responses, and does not record request headers.
If configured pacing exceeds three seconds, omit the override or choose a slower one.
A partial/rate-limited run is not a successful completed benchmark.

## Required checks

446 tests pass with no skips. Validation also passed: Ruff lint and format check,
`docker compose config --quiet`, Docker image build, CLI offline demo, packaged
container demo without network, and the Solana live dry run above. Outcome tests
cover each horizon, first-observation deduplication, restart/concurrent connections,
missing/zero prices, nonfinite calculated returns, normal-poll reuse, expired
watchlists, disabled chains, exhausted budgets and retryable failures. Migration tests
cover existing schemas, byte-preserving old alerts/presentation, rollback and retry,
malformed legacy presentation, and refusal of unknown future schemas.
