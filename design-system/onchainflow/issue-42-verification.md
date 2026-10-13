# Issue #42 saved candle workspace verification

Published base: `429eaff` (merged #40); WSL recovery remains explicitly waived.
The feature reads the same saved caches and adds no scanner, fetch trigger,
provider budget, database migration, authentication or production exposure change.
The private live site was neither inspected nor deployed.

## Behavior and decisions

Read `docs/website-api.md#saved-candle-workspace-contract-42` before extending the
workspace. Only closed saved 1h bars are supported. The shared token shell keeps
its observed-price/market-cap/score views and chain/address-specific external
market links. The new candle view links price and USD-volume panels on one time
scale with pointer/touch crosshair, drag pan, keyboard inspection and native
pan/zoom/reset/preset buttons. Unsupported presets are disabled; All saved covers
actual retention. Filled/hollow candles supplement color. Missing bars/volume are
blank/unknown; provider identity and price quote remain explicitly unrecorded.

Observed range extrema require 12+ consecutive hourly bars and full selected-window
coverage; expired/unknown cache or last bar ≥2h behind the saved cutoff disables
boundaries. These extrema are not predictive support/resistance. Relative volume
uses the preceding 24 consecutive closed 1h USD-volume bars, excluding the selected
bar, with complete samples and a positive mean; no rolling five-minute volume is
summed. Exact timestamps/units, baseline count, coverage, as-of and fetch time stay
visible. The fallback pages 25 OHLCV rows; UTC CSV exports the entire selected range
at stored precision. Cache replacement is explicitly not a permanent archive.

The pinned UI UX Pro Max guidance and renderer decision are recorded in
`pages/token-workspace.md`. Existing SVG/React adds no runtime dependency/license,
CDN or provider feed. New formatter reuse and bounded table rendering address the
measured maximum-history update cost without reducing chart or export coverage.

## Checks and evidence

- **55 frontend tests**, **897 Python tests**. New API fixture proves closed OHLCV,
  zero versus null volume, cutoff and unknown origin. New interaction/calculation
  tests cover duplicates/open bars, causal baseline, gaps/zero/null, stale/range
  gates, pan/zoom/reset, table pagination, CSV and empty→available measurement.
- Frontend format/type/build and Python Ruff checks pass. Existing owner/auth/
  public navigation and polling tests remain active.
- Actual saved-data API served over loopback, with a clearly synthetic 168-hour
  cache fixture (167 returned closed bars). Chromium at 320/375/390/768/1024/1366/
  1920 widths plus 844×390 landscape: keyboard, touch, drag, pan/zoom/reset,
  plotted/table/UTC-export parity, minimum 12px axes, 200% text resize and 320px
  reflow. Whole-page overflow is rejected; the named OHLCV table may scroll.
  No external requests. Controlled overlays separately cover stale, partial,
  null volume, zero baseline, empty, error and loading/offline behavior.
- Existing seven-page browser regression passes at all six #49 widths, including
  200% text resize and CDP 200%/400% layout equivalents. This is not browser-toolbar
  zoom or assistive-technology certification. Firefox/WebKit/real mobile devices
  and private production capacity remain #49/#52 release work.
- Existing local website container HTTPS staging checks verify CSP/same-origin,
  secure cookies, anonymous denial, CSRF, queued commands, stale worker and logout;
  no scanner starts in the website image and no real notifications are sent.

[Changed workspace browser evidence](evidence/issue-42/browser.json),
[seven-page regression](evidence/issue-42/shell-browser.json),
[steady load](evidence/issue-42/steady-load.json),
[burst load](evidence/issue-42/burst-load.json).

Matched before/after 390/1366 screenshots are under `evidence/issue-42/` and use
identical controlled hourly OHLCV values against the published base/current builds.
The independently seeded evaluation/fetch wall clocks differ slightly; they are
synthetic local observations, not live market evidence.

Steady/burst limits inherited unchanged: 100 tokens, 24 saved scans/2,400 evaluations,
eight scans per arm, six clients ×112 HTTP reads, separate API process, identical
starting DB copies. Zero read/scanner/provider-isolation failures and scanner
median overhead ≤max(25% baseline,100ms). Both passed with 672/672 successful reads.

| Profile | Provider wait / read pause | Baseline | Loaded | Allowed overhead |
| --- | --- | --- | --- | --- |
| Steady | 50ms / 250ms | 2,657.21ms | 2,796.83ms | 664.30ms |
| Burst | 5ms / 50ms | 664.82ms | 554.96ms | 166.20ms |

These are mock-provider/local HTTP measurements, not Internet/VPS capacity.
Final chart range/inspection gate is ≤100ms; gzip JS increase ≤10 KiB against base.
Measured results and declared thresholds are in `performance.json` beside the
browser evidence. Chart contrast uses the existing reviewed semantic colors;
normal text ≥4.5:1 and meaningful strokes/control boundaries ≥3:1. Disabled text
is excluded from contrast requirements. No theme migration or external asset ships.

## Reproduction and limits

Run `npm test`, `npm run format:check`, `npm run build` from `web`, and `.venv/bin/pytest -q`
plus Ruff from root. Browser fixtures use a **new** temporary database seeded by
`scripts/seed_website_demo.py`, `WEB_ALLOW_SYNTHETIC_DATA=true` and local static
builds. Populate only that temporary cache with closed hourly OHLCV, compare API
rows with table/CSV, block external requests and test the documented state gates.
Published-base build uses `git archive 429eaff web` in a temporary directory,
without resetting a worktree. Run `scripts/benchmark_website.py` with
`--separate-api-process` and the listed read/provider waits; existing gates are
unchanged. HTTPS validation uses `scripts/validate_website_container.py` against
the locally rebuilt website image. No production credentials are necessary.
