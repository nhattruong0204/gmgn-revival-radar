# Foundation and freshness verification — 2026-10-13

Scope: #51 shared contracts and Radar exemplar, #41 saved freshness/provenance.
Base: `6aa3d65` (merged launch PR #38), including pinned skill PR #53.
The owner waived unavailable WSL recovery for these two issues. No WSL data or
private live deployment was inspected, changed or deleted.

## Checks and limits

- Python: **895 passed**, Ruff check/format passed. New cases cover age windows,
  historical cutoffs/pagination, effective versus pending chain settings, malformed
  worker state, heartbeat expiry under caching, origin confidence, read-only
  exclusions across public evidence routes, cache invalidation/reversal, invalid
  attestations, duplicate snapshots and classification safety after retained IDs
  are reused. Existing owner/provider-isolation and command recovery tests pass.
- Frontend: **42 passed**, formatting, TypeScript and default production build
  passed. New tests exercise age/history URLs, historical/current coverage
  separation, unknown origin, empty history recovery and exact timestamp/timezone
  semantics. No app dependency or framework changes.
- Actual saved-API browser checks: 320/390/768/1024/**1366**/1440/1920 widths,
  no whole-page horizontal overflow; 200% zoom; keyboard focus and window changes;
  44px window control; reduced motion; local DM Sans loaded; no external requests
  or browser errors on checked journeys. Empty/error/partial state responses were
  deliberately controlled after actual API/UI parity checks. Cross-page smoke
  verified exact token identities/scores, reports, empty journal/outcomes,
  methodology and disabled owner access at 1440/390. These are fixture checks,
  not a screen-reader certification or a claim that every legacy screen meets AA.
- Rebuilt website container: verified HTTPS with local Caddy CA, same-origin
  routing, CSP, Secure cookies, anonymous owner denial, CSRF, queued command,
  stale worker, logout and no public sent alerts. Website starts no scanner.
  This is isolated container staging, not deployment to onchainflow.app.

Measured semantic contrast (WCAG luminance, automated token test):

| Foreground | Surface #101c26 | Background #0b141d |
| --- | ---: | ---: |
| Primary text | 13.35:1 | 14.34:1 |
| Muted text | 8.42:1 | 9.04:1 |
| Accent/focus | 10.24:1 | 11.00:1 |
| Warning | 9.61:1 | 10.32:1 |
| Danger | 7.56:1 | 8.12:1 |
| Control border | 3.60:1 | 3.86:1 |

These pairs satisfy 4.5:1 normal-text and 3:1 meaningful-UI targets. Disabled
controls are exempt; decorative panel borders are not actionable boundaries.
The existing shell and unmigrated pages need #40/#49 audits for all actual
rendered combinations; the semantic-token test alone is not whole-site compliance.

## Matched scanner/read load

Both profiles were declared before their measurement: 100 synthetic tokens/scan,
24 retained scans (2,400 evaluations), 8 scans/arm, byte-identical initial SQLite
copies, six clients × 112 reads, separate API process over loopback HTTP. The
unchanged gate is zero HTTP/scanner/provider-isolation errors and loaded scanner
median at most baseline + max(25% baseline, 100ms). No live provider credentials.
Fixture mode still exercises the indexed public-origin projections.

| Profile | Read pause | Mock provider wait/op | Baseline median | Loaded median | Allowance | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Steady | 250ms | 50ms | 2,790.91ms | 2,688.62ms | 697.73ms | Pass |
| Burst | 50ms | 5ms | 578.89ms | 554.15ms | 144.72ms | Pass |

Each completed **672/672 HTTP 200 reads**, zero scan errors and zero external
requests. Faster loaded medians are normal measurement variation, not evidence
that web traffic accelerates scans. Raw per-scan/API latencies are retained below.
These bounded synthetic loads do not establish production-size/VPS/provider/
Internet/TLS capacity or overturn the prior production-copy burst limitation in
`docs/website-launch.md`. #49 still owns full-site and production release gates.

Reproduce load with the project virtual environment:

```bash
python scripts/benchmark_website.py --separate-api-process \
  --read-pause-seconds 0.25 --replay-wait-seconds 0.05
python scripts/benchmark_website.py --separate-api-process
```

## Reviewable artifacts

- [Browser results](evidence/browser-checks.json)
- [Steady raw load](evidence/steady-load.json), [burst raw load](evidence/burst-load.json)
- [Before desktop](evidence/radar51-before-1440.png), [after desktop](evidence/radar51-after-1440.png)
- [Before mobile](evidence/radar51-before-390.png), [after mobile](evidence/radar51-after-390.png)

Before images use the published base's frontend/backend; after images use this
implementation. Both use an isolated, time-shifted synthetic SQLite fixture, not
live market facts. After images visibly label fixture mode. No performance/return
claims can be inferred from these demo scores.

For browser reproduction, seed a **new** temporary database with
`scripts/seed_website_demo.py`, build the default frontend, serve it with
`DATABASE_PATH`/`WEB_STATIC_DIRECTORY` and fixture-only
`WEB_ALLOW_SYNTHETIC_DATA=true`, then use Chromium/Playwright at the listed widths.
Keep external requests blocked. Before builds can be reproduced from `git archive
6aa3d65 web src` into a separate temporary directory; do not reset a working tree.

## Remaining work

#40 migrates the shell and other pages to these contracts. #42/#43/#45 consume
truthful clocks/origins for charts, briefs and track record. Suspicious legacy
production records remain unknown until reviewed evidence is available: use
`docs/data-provenance.md` and the reversible private attestation tool. Nothing
here authorizes automatic production cleanup, demo seeding or deployment.
