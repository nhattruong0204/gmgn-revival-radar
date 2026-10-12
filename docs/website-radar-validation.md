# Radar and token detail acceptance (#19) — 12 October 2026

This completes the public discovery/evidence milestone on the website foundation
and read-only API. It leaves scanner scoring, enabled chains, provider budgets and
Telegram delivery unchanged. HTTPS production launch remains tracked separately.

## Usability and evidence changes

- Desktop tables and mobile cards expose search, chain/stage/minimum-score filters
  and sorting. A shared sorting selector works on phones as well as desktops.
  Choosing Below watch resets the minimum to zero; a shared `?status=IGNORE` URL
  also defaults to zero. Normal Radar still defaults to WATCH or above. Filters
  and sorting persist in the URL and survive reload.
- Cards and table token identities show abbreviated contracts alongside names,
  tickers and chains. Details expose the full, selectable contract and an accessible
  copy button. Clipboard failures have a manual-copy explanation. Solana case is
  retained; exact GMGN/supported explorer links come from the saved token identity.
- Details label the saved evaluation's observation time separately from page refresh
  and later market snapshots. Known risks/blockers and unknown security stay visible
  even with a high score. Returning volume/transaction ratios and activity evidence
  are now visible, with unknown values explicit. Negative score deductions keep the
  correct sign.
- Charts show actual coverage dates and sample counts. Null observations split line
  segments, and sparse observations separated by more than 1.8 hours are not joined
  even when the inferred cadence is longer. Cached candle fetch/expiry and empty
  coverage remain visible. These are sampled/cached observations, never provider
  refreshes. Charts use native SVG, so no Lightweight Charts attribution is needed.
- Focusable charts support Home/End and left/right arrows to inspect actual saved
  points; their selected value/time is announced politely. Token freshness includes
  a textual stale label rather than relying on color alone.

## Verification

18 frontend tests passed, including new checks for low-score filtering/shared
sorting, exact contract/manual-copy fallback, chart keyboard navigation and sparse
gaps, high-score risk, saved-versus-later metrics, exact external links, returning
activity and signed deductions. Timer tests demonstrate 30-second polling, no new
requests while hidden, immediate refresh on visibility, and exponential retry
backoff after failures. Existing tests cover null legacy evidence and response races.

TypeScript, production Vite build and Prettier passed. Chromium used the actual
production bundle and FastAPI API over a synthetic SQLite database at 1440px and
390px. Both widths passed Below watch discovery (two sub-40 candidates), mobile/
desktop sorting and reload, keyboard search/open, copying the exact contract, chart
metric tabs and keyboard inspection. Original $820K evaluation metrics stayed
separate from a later $2M snapshot. No JavaScript exceptions, horizontal overflow or
provider requests were observed. Final activity/coverage panels were rechecked at
both widths after the final build.

The inherited six-state demo from #17 was rechecked in all 24 page/state/width
combinations: loading, empty, partial-risk, stale and error previews passed with
zero JavaScript errors, overflow or API requests. Repeatable review/startup instructions are in
`docs/website-foundation.md`; API/evidence definitions are in `docs/website-api.md`.
This is synthetic/local acceptance, not a live market-data or deployment claim.
