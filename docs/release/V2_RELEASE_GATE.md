# V2 release gate (issue #75)

Status: **after the #98 fix (re-measured 2026-10-15, see "Re-measurement after #98") all
20 gates pass (G20 is completed by CI).** The first measurement had G8 and G16 failing on
a pre-existing Radar 7-day read defect; those rows are kept below as history. The thresholds were committed before any measurement. Thresholds below are the existing, unchanged ones (AGENTS.md; #25, #42, #45,
#49, #64, #66, #90, #74). They must not be relaxed after results are seen. A failed
gate stays visible with its fix; a gate that fails because of a code defect is reported
and stopped, not fixed in this branch.

Scope: local evidence and a deploy runbook only. No SSH, no VPS, no production access,
no deployment. Input is a private schema-7 production copy taken 2026-10-14; it is never
modified, committed or published (only summary numbers and screenshots of the public UI).
Every mutating step runs on a temporary copy outside the repository.

## Declared gates

| # | Gate | Threshold |
| --- | --- | --- |
| G1 | Migration 7 to 9 via `storage.database.connect` | `PRAGMA integrity_check` = ok; pre-existing tables: row counts identical before/after AND sha256 of ordered rows identical; second `connect` is a no-op (idempotent, same hashes, user_version 9); duration recorded |
| G2 | `Repository.refresh_call_performance` backfill | completes without error; timing recorded; leaves pre-existing tables unchanged |
| G3 | Parity script, API | `verify_call_parity.py --db <migrated> --api <loopback>`: 0 mismatches |
| G4 | Parity script, projection | `verify_call_parity.py --db <migrated> --projection`: 0 mismatches |
| G5 | Audit figures | `/api/v1/track-record/calls?days=30` at `as_of` = max snapshot time + 1: 49 calls, 43 measurable, 7 positive, 36 negative, median -11.52% |
| G6 | CSV/API parity | both exports (`/track-record/export`, `/track-record/calls/export`) row counts and states equal the API |
| G7 | Schema-7 compatibility | unmigrated schema-7 copy served by the new website: same routes return 200 and the same call cohort figures as G5 (computed on read) |
| G8 | Endpoint timing | every public GET route, cold (fresh process) and warm: each < 1.5 s read deadline, all HTTP 200 |
| G9 | Steady replay | 672/672 HTTP 200 (100 tokens, 8 scans/arm, 6 clients, provider wait 50 ms, read pause 250 ms, separate API process, identical copies, read timeout 1500 ms), 0 scan errors, 0 external requests, 0 SQLite read errors, median loaded scan <= baseline + max(25% of baseline, 100 ms) |
| G10 | Burst replay | same as G9 with provider wait 5 ms and read pause 50 ms |
| G11 | New endpoints in replay | the replay route set includes alerts (performance), `/track-record/calls` and the track-record routes; same pass criteria as G9/G10 |
| G12 | Post-scan hook overhead | scan median with `call_performance` refresh and replay archive enabled <= scan median with them disabled + max(25% of that median, 100 ms); no scan error |
| G13 | Layout | no horizontal overflow at 320, 390, 768, 1024, 1366, 1920 px and at 1366 px with 200% zoom, on every listed page |
| G14 | Contrast | every sampled text element >= 4.5:1 |
| G15 | Keyboard and focus | skip link first, main actions reachable by Tab in DOM order, visible focus indicator on each stop, no keyboard trap |
| G16 | Console | 0 console errors / page errors on every page and size |
| G17 | External requests | 0 requests to any origin other than the loopback server |
| G18 | Reduced motion | with `prefers-reduced-motion: reduce`, no running CSS animation/transition longer than 0.01 ms on any page |
| G19 | Containers | `docker build` of the scanner image and of the `website` target succeeds, and `scripts/validate_website_container.py` passes; otherwise reported as not run and left to CI |
| G20 | Process | runbook committed, not executed; PR says `Refs #75` (not `Closes`); CI checks green; no merge or deploy by this task |

Pages (real copy data, not demo): Radar, Signals (flat and grouped), Signal detail #45
(PAID) and #14 (HOOKED), Token PAID (`98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump`),
Track Record (journal, worst-first, checkpoint tab), Reports, Methodology, Admin login
screen only (no owner credentials, no mutation).

Known measurement limits (declared in advance): loopback HTTP on a WSL2 development
host; synthetic provider waits; no live providers, no Telegram, no TLS/proxy latency, no
VPS resources; Chromium only; no real devices or assistive technology.

## Results

Measured 2026-10-15 on a WSL2 host (loopback, Chromium 1248), worktree of `main` 40a62a4
plus this branch. Thresholds above are unchanged from the first commit of this file.
Evidence JSON (summary numbers only, no database rows) is in
[evidence/](evidence/); screenshots are in [v2-gate-screenshots/](v2-gate-screenshots/).

| # | Gate | Measured | Result |
| --- | --- | --- | --- |
| G1 | Migration 7 to 9 | `integrity_check` ok; 17 pre-existing tables, row counts and SHA-256 of ordered rows identical; new empty tables `call_performance`, `replay_candles`, `replay_evaluations`; second connect a no-op (0.01 s); migration 2.19 s including integrity check | PASS |
| G2 | `refresh_call_performance` | first run built 49 rows in 0.050 s, later runs 0.0015 s; pre-existing tables still identical to the schema-7 copy; integrity ok | PASS |
| G3 | Parity, API (schema 9, default and fixed `as_of`) | 49 calls compared, 0 mismatches, exit 0 | PASS |
| G4 | Parity, `--projection` | 49 projection rows, 0 mismatches, exit 0 | PASS |
| G5 | Audit figures at `as_of` = last snapshot + 1 | 49 calls, 43 measurable, 7 positive, 36 negative, 0 flat, 6 unknown (`no_call_mc`), median -11.5229% (-11.52%) | PASS |
| G6 | CSV/API parity | calls export: 49 rows, ids equal, 0 field mismatches (change, MFE, MAE, market caps, reason) against the API; checkpoint export 44 rows = API total, `return` count equal; journal `performance` equals `/alerts` performance for all 49 | PASS |
| G7 | Schema-7 copy on the new website | same cohort figures, same items hash as schema 9, parity script 0 mismatches over the API, CSV parity 0 mismatches | PASS |
| G8 | Endpoint timing, cold and warm | 23 route variants: all HTTP 200, schema 9 cold max 0.337 s (token history), warm uncached max 0.360 s (`/reports?hours=168`), warm cached max 0.014 s; schema 7 cold max 0.640 s (call journal computed on read), warm max 0.347 s. Server start-up (with report warm-up) about 3 s, reported separately. **Not in that list:** `GET /api/v1/radar?age_hours=168` returned 503 at the 1.5 s deadline (1.50 to 1.53 s, repeated, schema 7 and 9, default and fixed `as_of`) | **FAIL** (pre-existing defect, see below) |
| G9 | Steady replay (production copy, includes calls) | 672/672 HTTP 200; 0 scan errors, 0 external requests, 0 SQLite read errors; median scan 2.0714 s baseline / 2.0745 s loaded, allowance 0.5178 s; 4 scans overlapped the reads | PASS |
| G10 | Burst replay | 672/672 HTTP 200; 0 / 0 / 0; median scan 0.9153 s / 0.9287 s, allowance 0.2288 s; 8 scans overlapped | PASS |
| G11 | New endpoints in replay | `scripts/benchmark_website.py --include-calls` (new, minimal) reads `/alerts` (per-call performance), `/alerts?group=token` and `/track-record/calls?days=30&sort=worst` besides the existing routes; slowest read in either profile 0.413 s | PASS |
| G12 | Post-scan hook overhead (3 runs, 8 scans per arm, 100 tokens) | refresh costs at most 4.2 ms per scan. Medians off/on: steady 2.059/2.467, 2.491/2.560 (swapped order), 2.522/2.078 s; burst 0.892/0.421, 0.893/0.453, 1.002/0.890 s; all within the allowance. Run order and noise dominate: the hooks-off arm was slower in several runs (unexplained, not investigated). **Limit:** the synthetic provider source returns no candles, so `replay_candles` and `replay_evaluations` stayed at 0 rows and the archive write path was not exercised | PASS, with the archive limit |
| G13 | Overflow | 0 of 98 page/size combinations overflow (14 pages x 320, 390, 768, 1024, 1366, 1920, 1366 at 200%); a second pass with the real clock (13 pages) also 0 | PASS |
| G14 | Contrast | 0 of 61,903 sampled text elements below 4.5:1 (693 elements with a background image were skipped, not counted as passing) | PASS |
| G15 | Keyboard and focus | at 1366 and 390: skip link first on every page, up to 40 Tab stops per page, every stop has a visible outline or shadow, main actions reached in DOM order (Refresh, filters, Group by token, call links, Export, tabs, copy contract); no trap | PASS |
| G16 | Console errors | 0 on 13 of 14 pages at all sizes. `radar-history-7d` logs the browser's "Failed to load resource ... 503" at every size because of the defect behind G8; the page itself renders the generic "temporarily unavailable" state | **FAIL** (same defect) |
| G17 | External requests | 0 on every page and size | PASS |
| G18 | Reduced motion | emulated `prefers-reduced-motion: reduce` at 1366 and 390 on every page: 0 running animations longer than 0.01 ms | PASS |
| G19 | Containers | `docker build -t revival-radar:v2gate .` and `--target website` built; `validate_website_container.py` passed (HTTPS routing, CSP, secure cookies, anonymous denial, CSRF, queued command, stale worker, logout, no scanner in the website image) | PASS |
| G20 | Process | runbook written and not executed; no VPS or production access; PR uses `Refs #75`; CI result recorded in the PR | see PR |

### Failure kept visible: Radar 7-day window (G8, G16)

`/api/v1/radar?age_hours=168` (reached from the Radar empty state's "Explore saved
history" link) does not finish inside the 1,500 ms read deadline on the 1.5 GB production
copy and returns 503. This is **not a V2 regression**: the same request on the production
code (`a784f67`, extracted with `git archive`, run against the unmigrated schema-7 copy)
returns 503 at 1.50 to 1.53 s. The 24 h and 72 h windows return in under 0.5 s. Per the
rules of this gate it is not fixed in this branch and the deadline is not changed; a
separate issue is needed (an index or narrower query for the 168 h window). Owner decision
before deployment: ship with it (graceful error state) or hold for the fix. The gate
result for G8 and G16 stays FAIL until a fix is re-measured with the same thresholds.

### Notes and limits

- The copy is 23 to 24 hours older than the measurement time. With the real clock the
  24 h Radar and Reports views are legitimately empty; those states are kept as
  evidence (`browser-real-clock.json`, `radar-empty-stale-*.png`). The populated
  screenshots use a read clock frozen 5 minutes after the copy's last scan (a launcher
  outside the repository; only the website's clock changes, never the data). The default
  Radar filters (score 40 and above, no `ignore`) are also empty at that scan, so
  `radar-all-24h-*` shows the unfiltered saved evaluations.
- Contrast is computed from computed styles with alpha compositing, not by a visual tool.
  Large-text relief was not used. Elements over background images were skipped.
- Browser: Chromium only; no real devices, assistive technology, Firefox or WebKit.
  200% zoom is emulated as a 683 px viewport at device scale 2. Admin was only the login
  screen with a throwaway password hash; no sign-in was attempted.
- Replay and endpoint timings are loopback on one host with mocked provider waits and an
  OS page cache that could not be dropped (`posix_fadvise` was used as a best effort for
  cold starts). They do not measure the VPS, TLS or Internet latency.
- The schema-7 call journal costs up to 0.64 s cold because it is computed on read
  (the projection is empty); this is the rollback/compat path, still under the deadline.
- Screenshots (64-colour palette PNG to keep the repository small, text remains legible):
  full-page 1366 and 390 for Signals (flat), Signal #45, Token PAID, Track Record journal
  and worst-first, Radar (stale/empty and unfiltered 24 h) and the Radar 7-day failure.
  The other pages, the 200% zoom set and all other sizes were captured and checked, but
  their screenshots are not committed; their per-page/per-size results (overflow, contrast,
  console, external requests, keyboard, motion) are in the evidence JSON.
- Private inputs (the database copy and its derived copies) were never modified or
  committed; the pristine schema-7 working copy is byte-identical (sha256) to the original after all steps, and every mutating
  step used a copy outside the repository.

## Re-measurement after #98 (2026-10-15)

Merged `origin/main` `fd1ee2c` (#98/#99: Radar ranks the latest evaluation per token in
one windowed pass) into this branch and re-ran only G8 and G16 with the thresholds
above and the same methods (fresh server per route with `posix_fadvise` on the database
file for cold; same-process repeats for warm; same 1366x768 frozen-clock browser pass).
The first G8 and G16 rows above are kept unchanged as the original result. One
mistake worth recording: my first browser re-run still hit a server process started
before the merge and reproduced the 503; it was killed and the pass repeated on a
fresh process running the merged code.

| # | Gate | Re-measured | Result |
| --- | --- | --- | --- |
| G8 | Endpoint timing, 26 route variants (the 23 original plus three Radar 168 h combinations), cold and warm, schema 9 and schema 7 copies | All HTTP 200. Schema 9: cold max 1.014 s, warm uncached max 0.415 s, warm cached max 0.018 s. Schema 7: cold max 1.153 s, warm uncached max 0.403 s, warm cached max 0.049 s. `/radar?age_hours=168` cold 0.300 s (schema 9) / 0.353 s (schema 7), warm uncached median 0.149 / 0.133 s. Radar 168 h with min score 0 and include ignored: cold 0.281 / 0.349 s | PASS |
| G8 (#100 edge) | Radar 168 h, min score 0, include ignored, sorted by market cap | Cold **1.014 s** (schema 9) and **1.153 s** (schema 7), warm uncached median 0.361 / 0.340 s, warm cached 0.004 s. Under the 1.5 s deadline, but with only 32% (schema 9) and 23% (schema 7) headroom; this is a single cold sample per copy, not a distribution, and tracked in #100 | PASS as measured (thin margin) |
| G16 | Console errors, 14 pages x 7 sizes (98 combinations) | 0 console or page errors on every page and size, including Radar 7-day (now renders data instead of the 503 state); overflow 0, external requests 0, contrast 0 failures in 65,531 elements, keyboard and reduced-motion clean (re-run as part of the same browser pass) | PASS |

Final per-gate status: G1 to G7 and G9 to G15 and G17 to G19 PASS (unchanged from the
first measurement; not re-run, the merge touched only the Radar query and its tests);
G8 PASS and G16 PASS after #98; G20 depends on CI. The Radar 7-day screenshots after the
fix are `radar-history-7d-after98-*.png`; the originals showing the failure stay.

Limits of this re-measurement: the other gates were not re-run after the merge, so
their results stand for `40a62a4` plus the earlier branch, not for `fd1ee2c`; the
cold method is a best-effort page-cache hint on a WSL2 host, so a truly cold VPS disk
may be slower, which matters most for the market-cap-sort combination in #100.
