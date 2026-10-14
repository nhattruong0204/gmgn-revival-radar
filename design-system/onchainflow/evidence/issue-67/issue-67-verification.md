# Issue #67 verification: Signals feed and Signal detail

Evidence is from the synthetic demo build (`VITE_DEMO_MODE=true`), built outside the repo and served on
loopback only. No production access. Metrics: `metrics.json` (Playwright + Chromium, `--no-sandbox`).

## What changed

- Signals list: compact card keeps market cap at call to latest, change since call, elapsed and freshness
  visible. Reasons, risks, best/worst, stage/score and links sit in one `details` disclosure (closed).
- `Group by token` switch, URL-backed (`?group=token`). Server mode `GET /api/v1/alerts?group=token`
  pages over tokens (default 10, max 20), newest token first, each token lists its calls newest first
  (cap 25 per token; `truncated` and `call_count` say so). Same read deadline and cache as the flat feed;
  uses the `(chain, contract_address, timestamp)` index. Nothing is computed in the browser across pages.
- Caveats (confirmed-delivery scope, origin unclassified count, legacy calls without call market cap,
  best/worst definition) appear once per page in a footer disclosure.
- Signal detail: removed the duplicate reason and risk panels and the identity/stat row. One open
  disclosure "Call record" holds the contract with copy, observation/capture/delivery times, liquidity at
  call, origin, missing-evidence chips, mint/freeze/top10/danger rows and every saved reason and risk.
  Score breakdown, market structure, brief archive and checkpoints are unchanged.
- `SignalEvidence` (shared with the Token page) gained an optional `summary` prop (default true), so
  Token and Reports output is unchanged; `RiskList` and `SecurityRows` are exported.

## Calls per 390x844 screen

- Scrolled to the feed (toggle at top of viewport): 3 compact calls fit flat (average card 231px), 2 grouped.
- As first loaded in the demo build: 1 fully visible card, because the demo banner, page heading and panel
  header use about 480px; production has no demo banner.

## Screenshots (this directory)

Signals flat, Signals grouped, Signal detail at `390x844`, `768x1024`, `1366x768`, `1366x768-zoom200`
(683x384 CSS px at device scale 2), named `<page>-<viewport>.png` (full page); mobile also has
`-first-screen`, `signals-flat-390x844-disclosure-open.png`, `signals-grouped-390x844-toggle-focus.png`.

## Checks

- Overflow: horizontal overflow 0 on all 12 page/viewport combinations.
- Contrast: lowest text contrast measured 4.75:1 (detail) and 5.26:1 (lists); AA met.
- Touch targets: every summary, switch, call action and copy button is at least 44px high. One footer
  link (Methodology, 16px tall) is pre-existing and outside this change.
- Keyboard: the switch is a real `button role=switch` with `aria-checked`; Tab moves to the next control,
  focus ring is 2px solid; Space toggles and the URL updates. Summaries are native and keyboard operable.
- Reduced motion: the switch knob transition is disabled under `prefers-reduced-motion`.
- States: loading, error and empty reuse existing components; unknown market cap, stale, no-later-observation
  and zero cards are in the demo data. Owner controls are not affected.

## Tests

Backend `tests/test_signals_group.py` (token paging, all calls listed, bound and 422s, unsent excluded,
deadline returns 503). Frontend `web/src/signal-feed.test.tsx` (grouping keeps all call ids, toggle URL
state, mobile disclosure, caveat once, detail has no duplicate panels but exposes contract, security and
times). Existing call-card and publications tests were updated for the new structure.

## Token page (merged with #69 from main)

`token-page-390x844.png`: the compact cards under "Calls on this token" share the same dense layout. They are
rendered with the new `hideTokenLink` prop, so no card links to the page it is on (0 "View token" links,
3 cards, no horizontal overflow); "Open GMGN" and "Original call" remain. `group=token` also honours the
`address` filter from #69.
