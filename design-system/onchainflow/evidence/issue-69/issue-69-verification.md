# Issue #69 verification: Calls on this token

Scope: token page section "Calls on this token", chart call markers, and the
`chain` + `address` filter on `GET /api/v1/alerts`. Evidence uses the synthetic
demo build (`VITE_DEMO_MODE=true`), built outside the repository and served on
loopback only. No production access. Raw measurements: `issue-69-browser.json`.

## Behavior

- API: `/api/v1/alerts?chain=&address=` lists confirmed sent calls for one token,
  newest first, with `performance`. `address` requires `chain`, is validated with
  the same rule as the token routes (422 otherwise), `page_size` stays 1-100, the
  query uses `alerts_token_time`, and it runs under the existing read deadline
  and cache.
- Section placement: after the summary and the chart, before the research brief
  (390px: section at y=1272, brief at y=3980; 1366px: 1156 and 2687).
- Cards: compact `CallPerformanceCard`, 5 per page, server-side pagination
  (Newer/Older, 44px targets), empty state "No published calls for this token",
  error notice with retry, loading state.
- Chart: each call in the chart window is a focusable marker (Tab to enter, Left
  and Right move between markers, Enter or Space selects, Escape dismisses;
  focus and tap also show the readout). Accessible name example: "Call 2 of 3,
  alert #1, Oct 13, 2026, 05:04:05 PM GMT+7, market cap at call $3M"; unknown
  call market cap reads "market cap at call unknown". Calls outside the chart
  window are not drawn and are counted in the chart note. Markers appear on the
  price, market-cap and score views. The 1h candle workspace is not marked.
  The marker query is capped at the newest 100 calls.

## Results (demo token with 3 calls, `sol/Demo111...111`)

| Check | 390x844 | 768x1024 | 1366x768 | 1366 at 200% zoom (683 css px) |
| --- | --- | --- | --- | --- |
| Horizontal overflow (px) | 0 | 0 | 0 | 0 |
| Compact cards rendered | 3 | 3 | 3 | 3 |
| Markers with accessible names | 3 | 3 | 3 | 3 |
| Empty-state token overflow (px) | 0 | 0 | 0 | 0 |

- Keyboard: marker focus shows the readout and a 3px light outline on the
  diamond plus a browser focus rectangle on the 44px hit area; ArrowRight moves
  to the next marker. Verified in all four viewports.
- Touch (390px, touch context): tapping a marker opens its readout
  (`issue-69-marker-touch-390.png`).
- Touch targets: marker hit area is 44px wide; Newer/Older, "Open call record"
  and "Dismiss" are at least 44px tall; card action links are 44px tall. Known
  exception inherited from `CallCard.tsx`: the `$SYMBOL` title link in the
  compact card is 44x27 px (above the 24px WCAG 2.2 minimum; not changed here
  because the card is shared with Signals).
- Contrast: marker glyph and line use `--research-warning` (#e5bb73) on the
  chart panel (#101c26), about 9:1; readout text uses the body text color on the
  same panel; the readout link uses the accent color with an underline.
- Reduced motion: no animations or transitions were added (0 running
  animations in a `reduce` context).
- States: loading and error preview states render the existing page-level
  states; the section has its own loading, error and empty handling. Permission
  state: public reads only, no owner data involved.

## Screenshots

- `issue-69-token-full-{390,768,1366,1366-zoom200}.png` full page
- `issue-69-calls-section-{390,768,1366,1366-zoom200}.png` section
- `issue-69-marker-focus-{390,768,1366,1366-zoom200}.png` focused marker readout
- `issue-69-marker-touch-390.png` touch inspection
- `issue-69-empty-{390,1366}.png` empty state

## Test results

- `ruff check .`: passed. `ruff format --check .`: passed.
- `pytest -q`: 995 passed. One earlier full run had a single failure in
  `tests/test_telegram_controls.py::test_pause_chains_assets_summary_and_reset_require_confirm`
  (KeyError); it passes alone and on rerun, so it is intermittent and unrelated.
- `web`: `npm run format:check`, `npm test` (100 passed), `npm run build` passed.
  One earlier `npm test` run failed 8 tests across unrelated files and passed on
  the next three runs, which points to load-related timeouts.
