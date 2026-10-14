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
| `GET /alerts` | `chain`, `address` (requires `chain`; validated like the token routes, so one token's calls), `page` (1–10000), `page_size` (1–100, default 25), `as_of` | Paginated confirmed sent alerts, newest first, each with `performance`. Uses the `alerts_token_time` index when `address` is given; the usual read deadline and cache apply |
| `GET /alerts/{id}` | `as_of` | Original alert observation, separately named `latest_observation`, saved `outcomes`, `performance`, `as_of` |
| `GET /reports` | `hours` (1, 4, 12, 24, 72 or 168; default 24) | Existing Solana top-ten report with sanitized `peak` and `latest` observations |
| `GET /outcomes` | `days` (1–7, default 7) | `{since,until,horizons}` using the bot's existing outcome calculation |
| `GET /meta` | `as_of` | Available observed chains, configured public Telegram link, observation cutoff and latest completed scan |

An observation contains `id`, `timestamp`, `chain`, `contract_address`, `symbol`,
`name`, `score`, `status`, `eligible`, dimension scores, selected market metrics,
`blockers`, `missing_fields`, `warnings`, `preset`, `revision`, and explicitly selected
`token` and `signal` fields. It contains no raw configuration, state, control commands,
provider credentials, owner IDs or Telegram delivery identifiers. `latest_market`
has its own timestamp and cannot overwrite historical scoring evidence.

### Call performance

Every alert in `/alerts` and `/alerts/{id}` carries a `performance` object computed
on read from saved `token_snapshots` only (no provider calls, no stored data is
changed). The immutable call fields stay unchanged. Snapshots after the `as_of`
cutoff are ignored; a page computes at most 25 calls and its cache key includes the
newest snapshot id, so new observations invalidate cached pages.

| Field | Definition |
| --- | --- |
| `call_market_cap`, `call_price` | Values saved with the call (`alert_signals`); `null` for legacy calls or non-positive/non-finite values |
| `latest_market_cap`, `latest_price`, `latest_observed_at` | Newest saved snapshot of the same token after the call with a finite `market_cap > 0` |
| `freshness` | `fresh` if the latest observation is at most 30 minutes before the cutoff, `stale` if older, `none` without an observation |
| `change_since_call_pct` | `(latest_market_cap / call_market_cap - 1) * 100`; `null` unless both are finite and positive |
| `change_reason` | `no_call_mc` or `no_later_observation` when the change is `null` (`no_call_mc` wins when both apply), otherwise `null` |
| `mfe_pct`, `mae_pct` | Highest and lowest saved market cap in `(call, latest_observed_at]` relative to the call market cap; `null` when not computable |
| `observations` | Count of saved observations in that range |
| `max_gap_seconds` | Largest gap between consecutive observations, counting from the call time to the first one; `null` without observations |
| `elapsed_seconds` | Call time to the response cutoff (`as_of`) |
| `token_call_index`, `token_call_count` | 1-based position of this call among visible sent calls for the same token, and the total |
| `basis` | `same`, `mixed` or `unknown` (optional in clients). `same`: latest market cap, MFE and MAE use only later snapshots with the call's market-cap source. `mixed`: no same-source later snapshot exists, so other-source snapshots were used. `unknown`: legacy or unlabelled call source, or nothing to compare. Rule in [performance metrics](performance-metrics.md) |
| `call_market_cap_source` | `rank` or `circulating_enriched` when the call snapshot recorded it, otherwise `null` |
| `other_basis_observations` | With `basis: same`, count of later snapshots on another or unlabelled basis that were excluded; otherwise `null` |

Unknown values are `null`, never `0`. Performance is observational: it does not
imply a forecast, a safe asset or a tradable price.

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
cache for up to 10 seconds by default, invalidated by completed scans, new saved market data, sent-delivery receipts,
outcome checkpoints and newer enrichment-cache fetches. HTTP responses use `Cache-Control: no-store` to avoid accidental
browser caching of private API routes. Public requests are limited to 120 per minute
per client IP by default, with `429` and `Retry-After`; configure trusted proxy IP
handling at the HTTPS proxy/Uvicorn boundary. These process-local limits multiply
with API worker count; start with one API worker on the scanner host.

Invalid query parameters return `422`, missing saved records return `404`, and
missing, locked or incompatible databases return a generic `503` without filesystem
paths or internal exceptions. Validation errors omit supplied input values. API
startup remains available when the database is absent, but observation reads return
`503` until the scanner has initialized it.

## Owner routes and response contracts

All paths below are under `/api/v1/admin`. Only the session endpoint permits
anonymous access; status, settings and commands authenticate on the server.
Owner access is disabled until configured. The API never writes
`radar-controls.json`; the existing scanner consumes its durable command queue.

| Method and route | Request | Response / responsibility |
| --- | --- | --- |
| `GET /session` | Cookie when present | `{authenticated,enabled,csrf_token,expires_at?}`; disabled/anonymous responses have null CSRF |
| `POST /session` | `{password}` and matching `Origin` | Session response; sets HttpOnly/Secure/SameSite=Strict cookie restricted to `/api/v1/admin`; never returns password |
| `DELETE /session` | Cookie, origin, `X-CSRF-Token` | `{authenticated:false,csrf_token:null}`; revokes persisted session and cookie |
| `GET /status` | Cookie | `{worker,health}`; private heartbeat, schedule, scan/delivery diagnostics; `worker.available=false` if disabled or heartbeat exceeds 30 seconds |
| `GET /settings` | Cookie | `{settings,revision,active_revision,presets,available}`; scanner-published allowlisted settings and saved/active revisions |
| `POST /commands` | Cookie, origin, CSRF and command object | `202` and persisted command; submission does not establish execution |
| `GET /commands` | Cookie; `limit` 1–50, default 50 | `{items:[command,...]}` newest first, bound to current owner session |
| `GET /commands/{uuid}` | Cookie and UUID | One owner-bound command; another session's ID returns `404` |

Commands supply `id` (client UUID for idempotency), `action` (`preview`/`confirm`)
and optional nonnegative `expected_revision`. A preview supplies `desired_action`
(`settings`, `reset`, `pause`, `resume`, `scan`, `test`); only settings accepts
`changes` or a `strict`/`balanced`/`broad` preset. Confirmation supplies the saved
`preview_id`, with no replacement action or changes.

Command responses contain `id`, `action`, `expected_revision`, `status`,
`created_at`, nullable `started_at`/`finished_at`/`target_revision` and `result`.
Internal `owner` and raw request `payload` are omitted. Status moves from `queued`
to `running`, then `succeeded`, `failed` or `unknown`. Failures use `result.code`
and `result.message`. Unknown effects must not be blindly replayed. Preview
results contain normalized `action`, `changes`, `before`, `after`, `revision`,
`preview_id` and `expires_at`. Previews expire after ten minutes, bound to the
session, changes and revision. Ordinary strategy changes apply at the next scan;
results distinguish saved `revision` from `active_revision`. Scan commands use the
existing scheduler and never start a second scanner.

Owner responses are `Cache-Control: no-store`. Safe errors use `detail`: `401`
unauthenticated/expired session, `403` origin/CSRF failure, `404` unknown command,
`409` UUID bound to different input, `422` invalid request, `429` login throttling
or full queue, `503` owner access/storage/settings unavailable. Login permits ten
attempts per address per 15 minutes; mutation bodies are limited to 16 KiB.
Validation errors omit supplied passwords/input values. Stale revisions produce a
failed worker result, not a successfully applied HTTP submission.

## Synthetic examples and exact shapes

[Public example](examples/website-public.json) shows a valid Radar response with
unknown historical token/signal detail, a known blocker, observation time and page
cutoff. [Owner examples](examples/website-admin.json) show disabled login and a
settings preview/confirmation. Values are illustrative, with no passwords, cookies,
credentials, Telegram/owner identifiers or real commands. Public models contain
no private settings or control records; full nullability/defaults are defined in
`src/revival_radar/web/models.py`.

`GET /meta` returns `chains:[{id,name}]`, not strings. Chart snapshots are
`{timestamp,price,market_cap}`; scores contain timestamp, overall and dimension
scores; candles contain timestamp/open/high/low/close. They are not full token
observations. Timestamps describe saved measurements/candle open times, independently
of page refresh. Missing/truncated chart evidence and gaps remain explicit.


## Public-read acceptance (#18)

Public readers check the scanner-owned schema version in their read transaction
and reject older/newer layouts without attempting migration. Search by token name
and sorting by market cap/liquidity/volume use the same identity-validated saved
evidence as serialization; malformed or mismatched payloads cannot contribute
metrics and sort after known measurements. Symbols and contract addresses remain
searchable from their evaluation identity.

Cache versions include completed-scan time, snapshot maximum, sent-alert maximum
and count, outcome maximum/count, and latest enrichment fetch. Counting sent receipts
handles an older pending alert completing after a newer one; outcome inserts can
invalidate results independently from snapshot writes. The cache remains bounded
to 256 entries with a 10-second default TTL; it does not promise immediate visibility
for arbitrary manual rewrites of old rows. Query deadlines also apply to version
lookups and validated search/sort work; timeout errors return the generic 503.

Validation and scope evidence are recorded in [public API validation](website-public-api-validation.md).

## Freshness and provenance additions (#41)

See [saved freshness/provenance](data-provenance.md) for exact field semantics,
24h Radar defaults and `age_hours` windows, historical versus active chain
coverage, recorded/unknown origin confidence and reversible exclusion policy.
Existing `/meta.chains` is historical; it is not current worker coverage.

## Saved candle workspace contract (#42)

History is a read-only view of the latest scanner-owned `kline:1h:*` cache
available by `as_of`; it does not fetch a provider, retain every prior cache
version, or guarantee a permanent archive. Its query horizon is 1–168 hours and
at most 1,000 returned points; actual retention/coverage may be shorter. Supported
candle timeframe is **3,600 seconds only**. Each bar carries Unix-second **start**
time, positive finite `open/high/low/close`, and optional nonnegative `volume_usd`.
Null volume means unavailable, zero means observed zero. Price quote currency and
provider identity were not retained in these caches: the workspace explicitly
labels them unrecorded, rather than inferring a provider from `candles_origin`.
Origin describes live/synthetic/test/unclassified provenance, not accuracy.

The server sorts saved bars and requires `since < timestamp` and
`timestamp + 3600 <= as_of`. Acquisition `candles_fetched_at` and cache expiry
`candles_expires_at` are separate from bar closure and evaluation times. A current
worker or page refresh cannot make old candles current. UI defensively excludes
open/future/invalid bars and all bars with duplicate timestamps; no interpolation
fills missing candles or volume. Ranges unsupported by available history are
disabled. Visible gaps are blank, and counts compare saved bars with the selected
hourly window. `truncated` remains visible above the workspace.

The displayed range low/high are descriptive observed extrema, **not predictive
support/resistance**. Boundaries require at least 12 consecutive bars and 100%
coverage of the selected hourly window. They are disabled when cache expiry is
unknown/elapsed or the last closed bar is at least two hours behind `as_of`.
Relative volume uses the preceding 24 consecutive hourly USD volumes (excluding
the inspected bar), 24/24 valid samples and a positive arithmetic mean. Missing,
gapped, zero-mean or stale evidence gives unknown; snapshots' overlapping rolling
five-minute volumes never enter this calculation. Display prices use ten significant
digits, axes rounded/scientific values; full stored numeric values are available
in the table's `data` attributes and UTC CSV. Screen times include browser timezone.
Existing chain/address-specific external links allow separate market follow-up;
external charts do not replace saved research evidence.

The HTML OHLCV fallback pages 25 rows at a time; range changes reset pagination.
CSV exports **all** visible-range bars, independently of the table page. Preset
24h/3d/7d buttons require enough retained history; “All saved” uses the actual
available start/end rather than promising seven complete days. With the existing
strict `since` boundary and an open current hour, a seven-day query commonly
contains 167 closed hourly bars, so the full 7d preset can remain disabled.
