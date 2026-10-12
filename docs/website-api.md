# Website API contract

The separate `revival-radar-web` service returns saved SQLite observations. Its
factory does not open the database, run migrations, start scanners, load the bot's
`.env`, or instantiate upstream provider clients. Each public request opens its own
read-only connection and holds one read transaction in its handler thread.

## Public routes

All routes use the `/api/v1` prefix. Unix timestamps are seconds. Every data route
accepts an optional `as_of` cutoff, which must be finite and no later than the server
time. Observations from unfinished scans, scans completed after that cutoff, and
future observations cannot supply a saved score. Missing historical evidence remains
`null`; it is never rebuilt from a newer market snapshot.

| Route | Query parameters | Response |
| --- | --- | --- |
| `GET /radar` | `search` (100 characters), `chain`, `status`, `min_score` (0–100, default 0), `include_ignore` (default false), `sort` (`score`, `timestamp`, `market_cap`, `liquidity`, `volume_5m`), `order` (`desc`/`asc`), `page` (1–10000), `page_size` (1–100, default 25) | `{items,total,page,page_size,as_of,last_scan_at}` |
| `GET /tokens/{chain}/{address}` | `as_of` | `{observation,latest_market,links,as_of,last_scan_at}` |
| `GET /tokens/{chain}/{address}/history` | `hours` (1–168, default 168), `max_points` (1–1000, default 500) | `{snapshots,scores,candles,candles_fetched_at,candles_expires_at,since,as_of,truncated}` |
| `GET /alerts` | `chain`, `page`, `page_size` | Paginated confirmed sent alerts |
| `GET /alerts/{id}` | `as_of` | Original alert observation, separately named `latest_observation`, saved `outcomes`, `as_of` |
| `GET /reports` | `hours` (1, 4, 12, 24, 72 or 168; default 24) | Existing Solana top-ten report with sanitized `peak` and `latest` observations |
| `GET /outcomes` | `days` (1–7, default 7) | `{since,until,horizons}` using the bot's existing outcome calculation |
| `GET /meta` | `as_of` | Available observed chains, configured public Telegram link, observation cutoff and latest completed scan |

An observation contains `id`, `timestamp`, `chain`, `contract_address`, `symbol`,
`name`, `score`, `status`, `eligible`, dimension scores, selected market metrics,
`blockers`, `missing_fields`, `warnings`, `preset`, `revision`, and explicitly selected
`token` and `signal` fields. It contains no raw configuration, state, control commands,
provider credentials, owner IDs or Telegram delivery identifiers. `latest_market`
has its own timestamp and cannot overwrite historical scoring evidence.

Statuses are `IGNORE`, `WATCH`, `EARLY_REVIVAL`, `REVIVING`, `STRONG_REVIVAL` and
`CONFIRMED_REVIVAL`. Supported chains are `sol`, `bsc`, `base`, `robinhood` and `arc`.
Solana addresses remain case sensitive; EVM addresses are normalized to lowercase.
Radar chooses the latest completed evaluation per chain/address within seven days
before applying filters, so an older high score cannot replace a newer low score.

History returns the latest bounded samples, ordered oldest first, and labels
truncation. Raw market samples can include observations collected during an incomplete
scan; scored samples only use completed scans. Candle data is exclusively cached,
closed 1h OHLC data. An absent or invalid cache produces an explicit empty list.
Fetch/expiry timestamps remain visible even for an expired cache; no request refreshes it.

Alerts expose only delivery status `sent`. Original saved alert-time data remains
separate from current evaluations, and legacy fields remain unknown. Outcomes are
observed signal returns, not executed trading P&L. Horizon keys are `1`, `6`, `24`,
and `72`, each with `observed`, `returns_available`, `positive`, `median_return_pct`,
`pending`, and `missed`. Reports retain completed/incomplete scan counts, sampled
coverage, peak/latest distinctions, and mixed-configuration indication.

## Bounded operation

Public reads have a configurable SQL timeout (default 1500ms), bounded pagination,
chart windows and point counts. Responses are cached in a bounded 256-entry process
cache for up to 10 seconds by default, invalidated by completed scans and new saved
market/alert data. HTTP responses use `Cache-Control: no-store` to avoid accidental
browser caching of private API routes. Public requests are limited to 120 per minute
per client IP by default, with `429` and `Retry-After`; configure trusted proxy IP
handling at the HTTPS proxy/Uvicorn boundary. These process-local limits multiply
with API worker count; start with one API worker on the scanner host.

Invalid query parameters return `422`, missing saved records return `404`, and
missing, locked or incompatible databases return a generic `503` without filesystem
paths or internal exceptions. Validation errors omit supplied input values. API
startup remains available when the database is absent, but observation reads return
`503` until the scanner has initialized it.
