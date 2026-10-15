# V2 release gate (issue #75)

Status: **gates declared, nothing measured yet.** This section was committed before any
measurement. Thresholds below are the existing, unchanged ones (AGENTS.md; #25, #42, #45,
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

(filled in after measurement)
