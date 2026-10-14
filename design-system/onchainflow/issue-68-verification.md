# Issue #68 verification

Plain-language call reasons and risks (frontend only; no API or stored-data change).

## Fixture
`web/src/demo.ts` synthetic observations now mimic the current scorer
(`setup-trigger-confirmation-v1`): `signal.reasons` are component strings only
(`"drawdown: +10"`), `signal.components` include a penalty
(`bundler_penalty: -5`), and one observation carries blockers
(`no_base`, `no_returning_activity`, `score_below_threshold`). Others carry
missing fields (`security.dev_ratio`, `baseline_history`). The synthetic build
(`VITE_DEMO_MODE=true`) was served from a loopback static server; the production
site was not accessed.

## Checked in Chromium (390x844 touch, 1366x768)
Token detail and alert detail; evidence in [evidence/issue-68](evidence/issue-68/):
`after-token-390.png`, `after-token-1366.png`, `after-alert-390.png`,
`after-alert-1366.png`, `browser.json`.

- "Why it's on the radar" shows three plain sentences at every width, for example
  "5-minute volume was 4.1× its recent baseline ($93K)".
- No snake_case, `missing_fields.N`, `blockers.N` or `Source:` text outside
  `<details>` on any of the four pages (regex check on the page with disclosures
  removed; `snake: []`).
- No horizontal page overflow; no external requests; no page errors.
- Keyboard: the "Score breakdown and components" summary is reachable by Tab and
  opens with Enter on all four pages; raw keys are inside it.
- Automated: `explain.test.tsx` and `research-brief.test.tsx` cover keys, measured
  values, missing values, unknown-key fallback and hidden identifiers.

Not separately measured: contrast (no new colors or fonts were added; existing
evidence-list and disclosure styles are reused), reduced motion (no motion added).
