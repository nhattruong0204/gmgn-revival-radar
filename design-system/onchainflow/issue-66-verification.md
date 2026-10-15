# Issue #66 verification

Track Record rebuilt as a call performance journal (frontend `web/src/CallJournal.tsx`,
`TrackRecord.tsx`; API `/api/v1/track-record/calls` and `/calls/export`). No new
dependency, chart library or external asset; no provider request on any read path.

## What changed
- Default view: summary bar (every count with its denominator), distribution in the
  existing five bins, then a sortable list of dense `CallPerformanceCard variant="row"`
  rows. Best and worst observed are visible on every row (no disclosure), as they are
  sort keys. Desktop (>=1180px) also offers a dense table. Group by token keeps every
  call (loads 100 calls; says so when more exist).
- Filters are URL-backed selects (window, chain, stage, outcome, revision, call age,
  score range); a modal on narrow screens, a disclosure on desktop.
- Caveats appear once per page. Small sample (<20), mixed configurations and unknown
  reasons are explicit notices; unknown is never 0.
- The old checkpoint table lives under the "Checkpoint detail" tab.

## Measurements
- Page height at 1366x768, 44 calls (demo build with `VITE_DEMO_CALLS=44`, first page of
  25 rows, including the synthetic-demo banner): **2,462 px** (target < 2,500). Row
  height 48 px. See `evidence/issue-66/journal-44calls-1366.png`.
- Default 12-call demo: 1,835 px at 1366; 3,412 px at 768; 4,808 px at 390.
- API cold page (no cache), 44 calls x 5,000 snapshots: 0.25 s; 25 calls x 5,000: 0.19 s;
  300 calls x 3,000 snapshots (900k rows): 1.0 s. About 3 ms per call; the 2,000-call cap
  can exceed the 1.5 s default deadline on dense histories and then returns 503 asking
  to narrow, never a partial summary.

## Browser evidence (synthetic build, loopback, production not accessed)
390x844 (touch), 768x1024, 1366x768 and 1366 at 200% zoom (683x384, dsf 2); journal,
worst-first, grouped, dense table (>=1180 only), unknown filter, empty, checkpoint detail
and the mobile filter dialog. Results in `evidence/issue-66/browser.json`.
- No horizontal overflow, page errors or external requests in any capture.
- Minimum computed text contrast 5.13:1 (threshold 4.5:1).
- Keyboard order: skip link, navigation, demo-state select, tabs, window, Filters,
  Export, breakdown summary, sort, ...; focus outline 2-3 px solid on every stop.
- Filter dialog controls are 44 px high. Row token links are 24 px high (WCAG 2.2 AA
  minimum, same as #65); the group checkbox is 22 px inside a 44 px label.
- Not measured: reduced motion (no motion added). Loading/error/empty/partial states
  are covered by unit tests and the demo `preview_state` pattern.

## Automated
Backend `tests/test_call_journal.py` (29): summary and denominators, unknown reasons,
pooled-revision labelling, every filter and sort, reconciliation with `/api/v1/alerts`,
CSV escaping, deadline, cap, cold-page timing. Frontend `web/src/call-journal.test.tsx`
(12). ruff, format, pytest (1028) and `npm run format:check`, `npm test` (118) and
`npm run build` pass.
