# Website validation — 12 October 2026

The public dashboard and private owner-control implementation are verified locally.
Production HTTPS launch is **pending**, so this does not complete tracking issue #16.
The proposed VPS is `130.94.7.127`; the proposed `onchain.revival.com` name requires
control of `revival.com`. SSH access, DNS and production smoke checks are outstanding.

## Automated checks

- Python 3.12: **826 tests passed**, including public response parity, original alert
  evidence, unknown legacy fields, anonymous/private isolation, CSRF, session expiry,
  login throttling, queue ownership, duplicate submissions, revision conflicts,
  settings receipt recovery and no replay of uncertain delivery.
- Ruff lint and format, dependency installation with lockfile hashes, and the
  deterministic offline scanner demo passed (scores 90/0/39, sent=0, errors=0).
- Node 24: frontend TypeScript and production Vite build passed. **7 frontend tests
  passed**, covering actual chain metadata, legacy token/alert rendering, unknown
  security, HTML escaping, bounded filters, chart gaps and refresh race ordering.
- Static-serving tests verify direct SPA navigation, real missing-asset/API 404s,
  absent-database 503s, and browser security headers.
- The existing schema-4-copy migration test validates schema 5, preserved old rows,
  unknown signal fields, runtime revision and Telegram polling offset. Restore on
  the real production host remains part of the launch checklist.

Reproduce from the checkout:

```bash
source .venv/bin/activate
ruff check .
ruff format --check .
pytest -q
revival-radar demo --database /tmp/radar-cli-validation.db
cd web
npm ci
npm run format:check
npm test
npm run build
```

CI now runs frontend install, formatting, tests and build in addition to the Python
and existing Docker checks. The npm lockfile is tracked for reproducible installs.

## Built-site browser checks

Chromium loaded the default production bundle through the real FastAPI/SQLite
service at 1440×900 and 390×844. Radar, reports, outcomes, alerts, methodology,
owner sign-in and token details passed at both widths. No JavaScript exceptions
or horizontal overflow were observed. A separate mock-worker browser check also
passed owner login, durable Scan Now preview/confirmation, settings rendering and
logout at 1440px and 390px; logout revoked access to the private status API. No
Telegram messages or provider requests were made. Data used current-time synthetic SQLite
fixtures, not frontend demo interception or live market data. The default build
does not load external fonts or initiate provider requests.

The original CLI fixture timestamps are deterministic and can be later than the
machine's current time. The public API correctly excludes future observations.
Use `python scripts/seed_website_demo.py /tmp/new-web-demo.db` for browser testing;
it shifts all fixture times consistently, refuses existing paths, forces dry-run
and fails any external HTTP attempt. Set `WEB_ALLOW_SYNTHETIC_DATA=true` only
for this isolated fixture database; the default API excludes marked fixtures.
Never present its tokens as live market data.

## Concurrent read comparison

Run `.venv/bin/python scripts/benchmark_website.py`. The benchmark now uses
byte-identical initial database copies, 100-token scanner replays, 2,400 saved
evaluations and six browser clients. The predeclared gate remains zero errors or
external requests and median scanner overhead at most `max(25% of baseline, 100ms)`.

The issue #25 run passed all 672 reads with zero scanner/SQLite errors; scanner
medians were 526.28ms baseline and 605.86ms loaded against a 131.57ms overhead
allowance. See [release validation](website-release-validation.md) for the actual
workload, API latency/query counts, parity/isolation/restore evidence and limits,
and [raw load results](website-load-results.json) for per-scan measurements.

This synthetic ASGI replay does not establish production-size capacity or VPS,
proxy, Internet or live-provider behavior. Repeat on a protected production copy
before public launch.

## Remaining launch work

Follow [deployment and rollback](website-deployment.md): confirm SSH and domain
control, back up production data/configuration, upgrade the one scanner, enable
the shared command worker, configure owner access, deploy the same-origin HTTPS
proxy, compare real data, and verify recovery/monitoring on the host. Record the
deployed URL and results here before completing #16. Live Telegram test delivery
has not been performed in this cloud task.
