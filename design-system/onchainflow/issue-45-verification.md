# Issue 45 verification

Published comparison base: `97fdd082e9a7fbae33ffacf82ad2157bc3d335bb`
(PR #57, schema six). Feature: saved checkpoint cohorts, schema seven.

## Results and evidence

- Full local Python suite: **920 passed**. Full frontend suite: **65 passed**.
  Calculation, repeated-alert grain, negative/zero/missing/unresolved states,
  inclusive deadlines, causal cutoffs, immutable receipt guards, exact original
  brief association, incompatible cohorts, synthetic/test exclusions, CSV safety,
  complete export and schema-six migration have regression coverage.
- Ruff lint/format, Prettier, TypeScript, Vite production build and diff whitespace
  checks passed. Final CI repeats the full suites and container staging checks.
- [Browser evidence](evidence/issue-45/browser.json): Chromium at 320, 390, 768,
  1024, 1366 and 1920 px; keyboard/Escape/focus return, touch navigation,
  source/back filter preservation, 200% text reflow and contained table scrolling.
  No page overflow, console errors or external requests in these checks.
- The saved-data fixture has 36 alerts: 24 computable returns (8 positive,
  8 negative, 8 zero), 8 missing-price points, 2 pending and 2 missed.
  API/table/full CSV numeric counts agree at all six widths. Incompatible cohorts
  withhold medians/rates. Empty (actual unmatched strategy), loading and error
  states were checked separately; failed reads do not become empty/zero results.
- Matched before/after UI captures use the same synthetic database and current
  API; the before UI is built from the published base. They demonstrate changed
  presentation, not historical production values:
  [mobile before](evidence/issue-45/before-outcomes-390.png),
  [mobile after](evidence/issue-45/after-outcomes-390.png),
  [desktop before](evidence/issue-45/before-outcomes-1366.png),
  [desktop after](evidence/issue-45/after-outcomes-1366.png).
- [Performance](evidence/issue-45/performance.json): JS gzip increased by
  **3,149 bytes**, below the predeclared 10 KiB gate. Maximum measured two-frame
  filter settlement was **23.1 ms**, below 100 ms. This measures two frames after
  opening the modal on the local fixture, not worst-case production responsiveness.

## Scanner/load gates

Unchanged scanner gate: added median scan time must remain within the larger of
25% of baseline or 0.1 seconds; read deadline remains 1,500 ms. Synthetic replay
uses one scanner and a separate local API process with no provider traffic.

- [Final steady profile](evidence/issue-45/steady-final.json): 672/672 HTTP 200,
  median scan 3.3582 s baseline / 3.2759 s loaded, gate passed.
- [Final burst profile](evidence/issue-45/burst-final.json): 672/672 HTTP 200,
  median scan 1.1520 s baseline / 1.2436 s loaded, gate passed.
- Additional 1,000-alert cohort profile initially had **7 read timeouts**:
  [original failure](evidence/issue-45/cohort-load.json). Outcome cache versions
  now track relevant alert/delivery/checkpoint/quarantine changes, avoiding
  invalidation from unrelated snapshots, scans or briefs. Assertions/deadlines
  were unchanged; new cache regression tests cover both churn and quarantine.
  [Corrected profile](evidence/issue-45/cohort-load-fixed.json): 672/672 HTTP 200,
  median scan 1.2606 s baseline / 1.2948 s loaded; track-record p95 0.2342 s;
  gate passed. Original and corrected evidence are both retained.

## Limits and rollout

Local fixtures are explicitly synthetic; no Telegram delivery, production copy,
VPS access, live-provider capacity, Internet/proxy/TLS measurement or live-site
validation occurred. Browser checks do not certify Firefox/WebKit, real devices
or assistive technology. Existing shared typography/theme/motion policy and access
boundaries are retained; no new animation/font/runtime packages were introduced.
The WSL host clock can jump during Vitest runs; deadlines were not relaxed. CI
provides the independent Ubuntu verification of the final commit.

Schema seven is additive with no invented historical backfill. See
[migration and rollback boundary](../../docs/track-record.md). A separate rollout
must stage the migration with a matched private backup and compatible scanner/web
code. Older readers reject schema seven; rollback restores the matched backup.
This PR does not deploy or migrate production. Broader #49 production-capacity,
browser/accessibility and remaining-page verification stays open.
