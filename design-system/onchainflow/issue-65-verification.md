# Issue #65 verification

`CallPerformanceCard` (`web/src/CallCard.tsx`), frontend only; no API or stored-data change.

## What changed
- Signals list renders one compact card per call; Signal detail shows the full
  card at the top. The separate "Alert-time market cap" stat and "Latest saved
  evaluation" panel were replaced by the card. All other detail sections stay.
- Hierarchy: `$SYMBOL . chain . #id` (+ "Call k of n on this token") -> market cap
  at call -> latest and change since call (sign plus "up/down/unchanged") ->
  elapsed time and latest-observation freshness with exact time -> best/worst
  observed -> +1h/+6h/+24h/+72h price-change strip (full only) -> stage, score,
  preset/revision -> top 2 reasons / top 2 risks (`explain.ts`) -> actions.
- Risks use a warning icon plus text (and a screen-reader "Risk:" prefix). The
  shared `EvidenceList` no longer shows a check icon for warning or danger items.
- Unknowns: legacy call shows "Market cap at call unknown" and "Change since call
  unknown", never 0%. No later observation, stale, pending, missed and
  unavailable checkpoints are distinct states. Best/worst observed can be negative.
- `web/src/demo.ts` now carries `performance` objects: positive, negative, zero,
  legacy unknown, stale, no-later-observation, and a token with 3 calls.

## Browser evidence
Synthetic build (`VITE_DEMO_MODE=true`) served from loopback; production was not
accessed. Evidence in [evidence/issue-65](evidence/issue-65/): Signals and detail
at 390x844 (touch), 768x1024, 1366x768, and 1366 at 200% zoom (683x384, dsf 2),
plus legacy/stale/no-latest details; results in `browser.json`.

- No horizontal overflow, page errors or external requests on any page.
- Minimum computed text contrast in the card is 7.19:1 (threshold 4.5:1),
  computed from rendered colours and composited backgrounds.
- Keyboard order: title link, View token, Open GMGN, Original call (list);
  View token, Open GMGN (detail). Focus shows a 2px solid outline.
- Action targets are 44px high on detail and the Signals actions; the compact title
  link is 27px (minimum 24px).

## Automated
`web/src/call-card.test.tsx` (every state, no check icon on risks, unknown never
0%), updated `publications.test.tsx` and `website.test.tsx`. `npm run
format:check`, `npm test` (94 passed) and `npm run build` pass; ruff and pytest
(946 passed) pass.

Not measured: reduced motion (no motion added); loading/error/permission states
are unchanged page-level behaviour. Visual QA is a single reviewer's reading of
screenshots.
