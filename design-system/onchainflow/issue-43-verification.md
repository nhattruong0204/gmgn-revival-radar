# Issue #43 verification

Base: published main `ea1dc2e0270b473494d4c944d095c48a910a0946` (PR #56).
Unfinished WSL tracked/untracked changes were preserved in a named stash on
`feat/research-ux-audit`; no reset/clean or replacement of merged #40–#42 work.
The feature branch starts from origin/main. No private VPS audit or deployment.

## Evidence and gates

- Python: 910 tests passed, including source round trips, bull/bear/watch rules,
  missing/zero/stale/gapped/duplicate/future evidence, actual archive writes,
  incomplete-scan isolation, backdated availability cutoffs, cache replacement,
  database immutability/REPLACE guards, provenance isolation, pagination bounds,
  credential exclusion and read-only API behavior. Ruff check/format passed.
- Frontend: existing 55 regressions plus four new tests for immutable diffs,
  paired missing/counter-evidence, keyboard source disclosure, distinct empty/error
  states and comparison across archive pages. Type/build/format checks passed.
  WSL sometimes reports a 5-second timeout in unchanged tests within 50–350ms;
  the final full 59-test run passed. The clean GitHub CI run also verifies the
  complete suite independently. No deadlines
  or test assertions were relaxed to hide the host clock problem.
- Real loopback API against a new explicitly synthetic database; main's archived
  frontend and the changed build use identical fixture values. Chromium at
  320/390/768/1024/1366/1920px: version selection/comparison, historical/unknown
  coverage, source disclosure/focus via keyboard and touch, preserved research
  filter return, error/loading/legacy-empty, reduced motion and 200% text resizing.
  No page overflow or external requests. Named raw-source regions may scroll.
- New controls reuse reviewed normal text/muted/action colors (AA ≥4.5:1) and
  control border (≥3:1); primary native selects are at least 44px high, inline source
  links at least 24px. No new color or font source. This is bounded component
  verification, not screen-reader or whole-site accessibility certification.
- Predeclared bundle increase ≤10 KiB gzip and comparison update ≤100ms; measured
  by gzip of base/current builds and Chromium performance.now to two animation
  frames. Exact measurements: [performance.json](evidence/issue-43/performance.json).
- Existing steady/burst scanner gates unchanged: 100 tokens, 24 saved scans,
  eight scans per arm, six readers ×112 requests, separate API process, identical
  database copies; zero failures/external requests, scanner overhead
  ≤max(25% baseline,100ms). Both passed, 672 successful reads each.
- Additional archive burst profile replaces only the duplicate Radar route with
  `/briefs`; same load and thresholds, 672 successful reads, 96 brief reads,
  zero failures/external calls. This supplements rather than replaces old gates.

[Browser results](evidence/issue-43/browser.json),
[steady](evidence/issue-43/steady-load.json),
[burst](evidence/issue-43/burst-load.json),
[archive burst](evidence/issue-43/brief-load.json).
Matched before/after 390/1366 screenshots are in `evidence/issue-43/`; all are
labeled synthetic fixtures, not private production observations.

## Reproduce and limits

Run `.venv/bin/pytest -q`, Ruff check/format, and from `web` run `npm test`,
`npm run format:check`, `npm run typecheck`, `npm run build`. Existing
`scripts/benchmark_website.py --tokens 100 --separate-api-process` uses
50ms provider waits/250ms read pauses for steady and 5ms/50ms for burst; add
`--include-briefs` for the archive profile. Providers remain mocked.

Browser fixtures use a new temporary schema-six database, two scanner-created
synthetic evaluations/revisions and closed cache windows; serve built assets with
`WEB_ALLOW_SYNTHETIC_DATA=true` on loopback. Archive main's frontend into a separate
temporary directory for matched screenshots, never reset the working tree.
Chromium-only local development evidence: Firefox/WebKit, real devices, browser
toolbar zoom/assistive technology, private production capacity and rollout remain
#49/#52. CI also runs the existing local container HTTPS/security/scanner-isolation
checks. No production migration, owner sign-in, notification or payment was tried.
