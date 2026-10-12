# Public API acceptance (#18) — 12 October 2026

All public routes were already present in the website implementation. This issue
finishes their schema/evidence/cache boundaries and records API acceptance; it does
not change discovery, scoring, provider budgets, enabled chains or Telegram delivery.

## Corrections

- Every request owns a read-only SQLite connection and a consistent read transaction.
  It checks the current scanner-owned schema and returns generic 503 for an older or
  newer version. API startup and reads never call migrating `connect()`.
- Cache versions include sent-alert count and outcome row count/maximum, so an older
  pending alert finishing after a newer receipt, or an outcome arriving without a
  snapshot, invalidates results. A new cached-candle fetch invalidates history.
- Token-name search and market-metric sorting validate saved identity, timestamp,
  score/stage and eligibility using the existing evidence policy. Invalid saved
  payloads produce unknown metrics rather than influencing public ordering/search.
  SQL filter values remain parameters; sort expressions are allowlisted.

## Acceptance coverage

The 15 new tests in `tests/test_web_api_readiness.py`, alongside existing public
API tests, demonstrate:

- Unsupported schemas are rejected unchanged; error responses expose no internal
  schema/path details. Existing serializers exclude raw settings/state/commands,
  credentials, Telegram IDs and provider-private fields.
- Duplicate tickers remain distinct by chain/address; Solana address case is retained.
  Pagination visits each tied token exactly once. An SQL-like search is literal and
  cannot broaden results or mutate storage. Invalid payload identity cannot supply
  search/sort metrics. Existing tests preserve original scores versus later markets.
- All six Solana report windows match repository ranking, including below-threshold
  candidates. Outcomes match repository counts with missing initial prices remaining
  unknown and excluded from returns/medians. Existing tests keep non-sent alert IDs
  private and preserve original alert evidence.
- All public GET routes, including sent-alert details and metadata, work while scan
  writes commit. A held WAL snapshot retains its original count while a separate
  writer commits; the next request sees the newer saved data.
- Provider/Telegram constructors, external HTTP transport, scanner execution and
  migrating `connect()` are forbidden during the startup/read test. None are called.
  An intentionally expensive recursive SQL query is interrupted by the configured
  100ms deadline and returns a safe 503. Existing tests cover bounds and throttling.

## Load comparison

`python scripts/benchmark_website.py` passed again after these changes: 360 public
requests across six concurrent clients, zero read/scan failures and zero external
requests. Twelve fixture scans per arm measured median 5.61ms without browsing and
68.79ms with browsing, within the predeclared 100ms overhead allowance.

This is a small synthetic/ASGI workload, not a production-size capacity claim.
Production-sized load validation stays in the #16/#25/#26 launch work. Bounded
queries can deliberately return 503 under timeout rather than hold scan transactions.
The process cache uses the documented brief TTL and does not guarantee immediate
invalidation after arbitrary manual changes to historic rows.

Reproduce with `pytest -q tests/test_web_public.py tests/test_web_api_readiness.py`,
`ruff check .`, and the benchmark above. Full Python/CI checks run before merge.
Public/admin shapes and startup instructions remain in `docs/website-api.md` and
`docs/website-foundation.md`.
