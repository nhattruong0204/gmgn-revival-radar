# Issue #72 verification

Market regime summary on the Reports page (context only; no score, alert or provider change).

## Fixture
`web/src/demo.ts` now returns `market_context` for `/reports`: `ready` is a normal
20-token Hot Search universe; `partial` is an undersampled universe (3 tokens, all
medians and the share `null` with reason `insufficient_sample`, eligible count unknown);
`empty` is `market_context: null`. The synthetic build (`VITE_DEMO_MODE=true`) was built
to a temp directory outside the repository and served from a loopback static server;
the production site was not accessed.

## Checked in Chromium (390x844 touch, 1366x768)
Evidence in [evidence/issue-72](evidence/issue-72/): `reports-ready-390.png`,
`reports-ready-1366.png`, `reports-insufficient-390.png`,
`reports-insufficient-1366.png`, `reports-none-390.png`, `browser.json`.

- Summary reads "Universe of 20 Hot Search tokens: median 1h change -2.1%, 30% up (scan
  at HH:MM UTC)"; undersampled values read "unknown (fewer than 5 tokens)", never 0.
- The "Context, not a signal" note is visible in the ready and insufficient states.
- No horizontal page overflow, no external requests, no page errors in any capture.
- Contrast computed from computed styles: minimum 6.15:1 (ready/insufficient) and 8.42:1
  (empty), all above 4.5:1. No new colors or fonts were added.
- Keyboard: the summary is static text with no controls, so it adds no tab stops; the
  existing skip link, Refresh and window controls remain reachable in order
  (`tabOrderSample`).
- Automated: `publications.test.tsx` covers the summary, the unknown state and the empty
  state; Python tests cover computation, storage and the API field.

Not separately measured: reduced motion (no motion added), permission states (the
Reports page is public and read-only).
