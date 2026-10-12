# Website release validation: issue #25

Validated on 2026-10-12 in the cloud checkout using synthetic observations,
replayed data sources and mock transports. Production VPS/HTTPS and real-data
operational acceptance remain under issue #16; this record does not claim a live
launch or production-size capacity.

## Evidence and isolation

The cross-feature release tests compare every report entry's identity, peak and
latest ID/score/time/status/eligibility against the saved bot report for identical
24-hour windows and two cutoffs. They include three identical symbols with distinct
contracts, an unfinished scan and a scan completed only after the earlier cutoff.
Legacy missing detail stays unknown; absent candles and outcomes remain empty.
Outcome summaries match the bot and independently check median(+100%, -50%)=25%.

The public projection drops credentials, provider state, owner/Telegram identifiers
and internal error fields even when nested in saved payloads. Hostile token text
remains literal data; React rendering creates no injected image/script element.
Existing authorization regressions cover anonymous access, guessed IDs, CSRF,
Origin, session expiry/rotation and owner-bound command history/previews. Existing
command/service tests cover shared Telegram/web revisions, exact confirmation
payloads, expired/changed previews, repeated scan requests, pause/report
independence, crash recovery and no resend of uncertain synthetic messages.

The new restore test invokes the actual backup script against an active WAL
fixture, checks private backup permissions, restores through SQLite's backup API,
and compares all saved tables. The matching controls file retains revision,
settings and Telegram offset. Unknown-delivery cooldowns still suppress a second
reservation, and queued commands remain queued. Existing copied schema-4 migration
coverage checks alerts, signals, saved evidence, outcomes and unchanged controls.

## Matched concurrent replay

Run `.venv/bin/python scripts/benchmark_website.py`. Before measurement the gate
requires zero read/scanner/external-request errors and median scanner overhead at
most `max(25% of baseline, 100ms)`. Both arms start from byte-identical SQLite
backup copies and identical saved provider responses. Replay waits are 5ms per
provider operation; six browser clients issue sequential requests, pausing 50ms
between reads. Scanner and public reads share the loaded arm's WAL database.

The original larger replay exposed 12 read failures in 672 requests and excessive
scanner slowdown (524ms baseline versus 2061ms loaded median). The report now
validates tie-break evidence only for tokens whose peak score can reach the top
list, retaining all boundary-score ties. Concurrent cache misses for the same
route/filter/saved-data version share one fill; unrelated keys remain independent,
failed fills release waiters, and disabled caching still bypasses coalescing.
Ranking and cache concurrency regressions verify these properties.

Final actual measurements are committed in
[website-load-results.json](website-load-results.json):

| Measurement | Result |
| --- | --- |
| Replay | 100 Solana tokens per scan; 8 scans per arm |
| Initial database | 2,800 snapshots; 2,400 evaluations; 24 scans; 20 synthetic alert receipts; 10 outcomes |
| Public load | 6 clients; 672 requests; 96 per route |
| Scans completed while readers were active | 8 |
| Baseline / loaded median scan | 526.28ms / 605.86ms |
| Median overhead / declared allowance | 79.57ms / 131.57ms |
| Scanner errors / public failures / external attempts | 0 / 0 / 0 |
| Read SQLite errors | 0 |
| Traced read statements | 3,560 SELECT; 76 WITH |
| Gate | Passed |

| Public route | Median | p95 | Maximum |
| --- | --- | --- | --- |
| Metadata | 8.55ms | 32.08ms | 153.78ms |
| Radar | 13.59ms | 125.75ms | 203.30ms |
| Radar market-cap sort | 10.96ms | 153.57ms | 205.08ms |
| Reports, 24h | 10.21ms | 240.65ms | 293.71ms |
| Outcomes | 6.29ms | 19.07ms | 154.82ms |
| Alerts | 8.37ms | 59.31ms | 154.50ms |
| Token history | 9.80ms | 51.16ms | 127.20ms |

Trace instrumentation is included in the loaded measurement. No SQLite error was
observed; this does not prove every lock wait was zero. The test uses one synthetic
chain, bounded history and ASGI transport on this machine. It does not measure live
provider quotas, Internet/proxy latency, VPS resources or production database size.
Repeat on a protected production copy during deployment; keep the declared gate.

## Build and primary flows

All 866 Python tests, Ruff, 37 frontend tests, TypeScript/Vite production build
and Prettier passed. The offline CLI demo retained its expected 0/39/90 scores, processed
three tokens with zero errors and sent zero messages.

Chromium at 1440px and 390px compared rendered token identities and scores with
saved API data and exercised reports, token evidence/history, empty alerts/outcomes,
methodology and the isolated owner sign-in page. Browser external requests were
blocked and none attempted; no browser errors or page overflow were observed.
Owner primary flows use real local cookie sessions and a worker with mock-only
Telegram: settings preview/confirmation, reset cancellation and a private Test
Alert, with no public alert created. No live provider or Telegram send was used.

See [admin validation](website-admin-validation.md),
[command recovery](website-command-validation.md) and
[deployment/rollback](website-deployment.md) for the remaining launch procedure.
