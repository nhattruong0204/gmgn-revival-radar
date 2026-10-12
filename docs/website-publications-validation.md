# Public publication pages (#20) — 12 October 2026

The confirmed-alert, Solana report, outcome and methodology pages complete their
local acceptance on the existing read-only API. Scanner scoring/provider budgets,
Telegram delivery and report/outcome calculations are unchanged.

## Behavior

Alerts show chain/contract identity, original alert-time score, metrics, timestamp
and saved preset/revision. The latest saved evaluation is a separate labeled panel;
unknown historical detail remains unknown. Failed detail requests show an alert
empty/error state rather than silently falling back to the list. Non-sent records
are excluded by the existing API, and the private synthetic Test Alert creates no
public alerts/outcomes.

Solana report entries retain server ranking and all six 1h/4h/12h/24h/72h/7d windows.
Expandable peak/latest evidence shows each observation's score, timestamp, revision,
risks, blockers and missing fields. The collapsed summary flags known blockers and
missing evidence. Counts, excluded incomplete scans and mixed configurations remain
visible; sparse samples and stale evaluation times are labeled. Top-ten/seven-day
coverage remains explicit.

Outcomes show all four horizons with observed/known-return counts, median,
positive-return proportion and its known-return denominator, and pending/missed
counts. Unknown medians have neutral styling. Unavailable responses display unknown
counts rather than invented zero denominators; empty confirmed cohorts are labeled.
Retry controls distinguish failures from genuine empty observations. These are
observed signal returns with sampled coverage, not executed-trade P&L.

Methodology describes all six stages, discovery, score dimensions, tracked-universe
limits and heuristic scores rather than calibrated probabilities. Navigation uses
`WEB_TELEGRAM_URL` from the public metadata contract when configured; no channel
address is invented and no private owner/chat ID is exposed. Deployment instructions
already describe that setting.

## Verification

- 26 frontend tests passed. Eight new publication tests cover original/later metric
  separation, unavailable and legacy alert details, peak/latest risks and report
  windows, known-return denominators and unavailable medians, failure denominators,
  methodology stages and the configured public Telegram navigation link.
- TypeScript/default production build and Prettier passed. No demo interception is
  enabled in the default production bundle.
- 70 relevant Python public API/control tests passed, including report/outcome parity,
  missing prices, non-sent alert privacy and private synthetic Test Alert isolation.
  GitHub CI runs the full Python/Docker and frontend jobs before merge.
- Chromium tested ready/loading/empty/partial/stale/error states for alerts, reports
  and outcomes at 1440px and 390px: 36 scenarios passed with no JavaScript exceptions
  or horizontal overflow. Expanded peak/latest report and alert panels passed at both
  widths. Final publication/methodology navigation checks passed with zero API requests
  in the explicitly synthetic demo mode.

The checks use synthetic local fixtures. Production HTTPS deployment and live data/
load validation remain in #16/#25/#26; this page does not claim those are complete.
