# Alert ↔ record consistency audit (V2)

Audit date: 2026-10-14. Evidence source: a consistent `VACUUM INTO` copy of the
production database (schema 7, `PRAGMA quick_check = ok`) taken at 13:36 UTC from
host `130.94.7.127` (verified: `onchainflow.app` resolves there and the host owns
that address), plus the live public API and website at the same time. Production
runs `a784f67` = GitHub `main`. The copy was kept outside the repository and the
temporary server-side copy was deleted. No production data was modified.

Labels used: **Fact** (verified against data/code), **Evidence** (how), **Hypothesis**,
**Recommendation**.

## 1. Identity model as implemented

| Concept | Current identity | Where | Assessment |
|---|---|---|---|
| Token | `(chain, contract_address)` | every table | Sound. Symbols are not unique: two different `SI` tokens were called (`9aqmJj…`, `DEW9dS…`). UI must always show the contract. |
| Scan | `scan_runs.id` | scanner | Sound. |
| Evaluation | `evaluations.id`, unique `(scan_id, chain, contract_address)` | scanner | Sound. Diagnostic pruning can remove old evaluations. |
| Signal / call | `alerts.id` (autoincrement) | reservation | Sound and stable. This **is** the call ID; it is shown as "alert #45" in the checkpoint table only. |
| Telegram notification | `alerts.telegram_message_id` | delivery | 1:1 with call. Not exposed publicly (fine). |
| Alert-time snapshot | `state['telegram:alert:<id>']` JSON + `alert_signals` row (+ `research_alert_facts` from schema 7) | reservation transaction | Immutable in practice; `research_alert_facts` is trigger-protected. |
| Track Record entry | one row per `(alert_id, horizon)` checkpoint | API | **Defect (UX/model):** the unit shown is a *checkpoint*, not a *call*. |
| Outcome observation | `signal_outcomes(alert_id, horizon_hours)` + `research_checkpoint_facts` | scanner | Sound: first point in `[due, due+1h]`, immutable. |
| Strategy revision | `configuration.preset` + `configuration.revision` + `signal.score_version` inside the snapshot | snapshot | Stored per call. Correct. |

**Fact:** a token can have many calls and each keeps its own snapshot. PAID
(`98kfF7…`) has 12 calls, JEANPHIL 8, HOOKED 5. Nothing overwrote an earlier
call's market data.

## 2. Layer-by-layer parity (all 49 calls)

| Check | Result | Evidence |
|---|---|---|
| DB `alerts` ↔ API `/api/v1/alerts` (id, timestamp, contract, score, MC, stage, revision) | **49/49 match** | scripted comparison of both API pages to the DB copy |
| Alert-time MC/price present | 43/49. Calls #1–#6 are legacy (pre-schema-4) and have **no alert-time MC/price**; #1–#3 were Robinhood-chain calls (#1 = tokenized `GOOGL`) | `alert_signals` NULLs |
| Checkpoint arithmetic `(p/p0−1)·100` | **131/131 exact** | recomputed in SQL |
| Checkpoint in window `[due, due+1h]`, anchored to alert observation time | **131/131** | `anchor_err = 0`, max lag 8.8 min |
| Price return vs market-cap return | differ by 1–2 pp on 7 checkpoints (PAID, e/acc) | supply/circulating differences at provider; see §4 |
| Schema-7 immutable facts | only calls #48–#49 (post-cutover) have `research_alert_facts` / delivery receipts | expected — no backfill by design |
| Telegram ↔ API | message IDs present for all 49 `sent` rows | `alerts.telegram_message_id` |

**Conclusion (Fact):** the *stored* call data is internally consistent and the API
faithfully reproduces it. The owner's impression that "alert records and tracked
outcomes appear inconsistent" is **not caused by corrupted or overwritten data**.
It is caused by presentation and modelling choices (§3) and by the strategy
configuration that generates the calls (see `SEVEN_DAY_SIGNAL_REVIEW.md`).

## 3. Defects found

| ID | Severity | Defect | Evidence |
|---|---|---|---|
| D1 | P0 | **No "current vs call" comparison exists anywhere.** The API never computes latest MC, change since call, elapsed time, MFE or MAE for a call. The alert detail shows "Alert-time market cap $4.2M" and, in a separate panel, "Latest saved evaluation … $3.8M" without relating them. | `web/queries.py::alert`, screenshot `v2-screenshots/signal-detail-1366.png` |
| D2 | P0 | **Alert risk warnings are dropped.** Saved alert snapshots keep warnings under `signal.warnings` (e.g. "bundler penalty", "Insider holdings unavailable", "Token-2022 extension risks not assessed"), but `observation()` reads top-level `value["warnings"]`, which only evaluation rows have. The alert's Risk panel therefore shows none of the risks that were known at publication. | `src/revival_radar/web/queries.py` `observation()`; API `observation.warnings == []` for #49 while `signal.warnings` has entries |
| D3 | P1 | **"Why it's on the radar" is always empty for current-scorer calls.** The scorer only emits component strings (`volume_5m: +15`); the UI removes those as duplicates of the breakdown and then prints "No narrative reasons were saved". | `web/src/TokenDetail.tsx` lines 40–59 |
| D4 | P0 | **Track Record unit is a checkpoint, not a call**, and the default 7-day view **withholds** the median and positive rate because the 44 calls span four configuration revisions (`comparable: false`). The page therefore cannot answer "did the calls work?" by default. | `/api/v1/track-record?days=7&horizon=24` → `median_return_pct: null`; screenshot `track-record-1366.png` |
| D5 | P1 | Signals list shows no market cap, no performance and no grouping; 12 PAID rows look like 12 unrelated events. | `signals-1366.png` |
| D6 | P1 | Token page does not list the token's calls or their performance (PAID has 12). | `token-1366.png` |
| D7 | P2 | Raw internal keys leak into the UI (`no_base`, `Source: missing_fields.0`, `blockers.1`). | `token-1366.png` |
| D8 | P2 | 47/49 calls show "Origin unclassified" — accurate (pre-schema-7 provenance), but repeated on every row it reads as an error. | `signals-1366.png` |
| D9 | P2 | Two outcome definitions coexist (price return in checkpoints; MC in UI cards). Needs one documented definition per metric. | §2 |

## 4. Recommended call-performance contract

Definitions (all from saved observations; no provider calls on read):

- `call_mc` = `alert_signals.market_cap` (unknown for legacy #1–#6; never filled).
- `latest_mc`, `latest_observed_at` = most recent `token_snapshots` row for the
  token with `timestamp > call_time` and `market_cap > 0`. Status: `fresh` if
  `now − latest_observed_at ≤ 30 min`, `stale` otherwise, `none` if no row.
- `change_since_call_pct` = `(latest_mc / call_mc − 1)·100` only when both are
  finite and `> 0`, else `null` with a reason code (`no_call_mc`, `no_later_observation`).
- `mfe_pct` / `mae_pct` = max/min saved MC over `(call_time, latest_observed_at]`
  relative to `call_mc`, with the observation count and the largest gap between
  observations (saved snapshots, not intrabar highs — label "observed").
- Checkpoints stay as they are (price-based, immutable). Display them as
  "+1h/+6h/+24h/+72h price change"; display the headline as **market-cap change**.
  Document that the two can differ when circulating supply changes.

Required invariants/tests are listed in `docs/roadmap/V2_IMPLEMENTATION_PLAN.md` §P0.

## 5. Per-call reconstruction (snapshot 2026-10-14 13:36 UTC)

Latest MC is the newest saved snapshot (all ≤ 20 min old at audit time). MFE/MAE
use saved ~5-minute snapshots, not intrabar extremes. Checkpoint columns are the
existing price-based checkpoints. Generated by `v2-analysis/call_performance.py`.

| Call | Token | Alert (UTC) | Stage · score · rev | MC at call | Latest MC | Δ since call | Max ↑ (MFE) | Max ↓ (MAE) | +1h | +6h | +24h | +72h (price) |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| #1 | GOOGL | 10-06 18:19 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | missed/none | missed/none | missed/none |
| #2 | DELTA | 10-07 05:22 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | missed/none | missed/none | missed/none |
| #3 | ZZZ | 10-07 05:33 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | missed/none | missed/none | missed/none |
| #4 | JEANPHIL | 10-07 10:04 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | — | — | — |
| #5 | JEANPHIL | 10-07 11:46 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | — | — | missed/none |
| #6 | e/acc | 10-07 14:19 | legacy (no alert-time MC saved) | unknown | — | unknown | — | — | missed/none | — | — | — |
| #7 | PAID `98kfF7` | 10-07 15:11 | Reviving · 75 · 5 | $5.11M | $3.85M | **-24.6%** | +1.6% | -35.7% | +0.9% | -6.3% | -20.0% | -32.8% |
| #8 | e/acc `CbcyNo` | 10-07 15:30 | Reviving · 70 · 5 | $5.87M | $1.25M | **-78.7%** | +3.2% | -81.9% | -2.0% | +3.0% | -64.0% | -68.2% |
| #9 | EMBER `5dvXTZ` | 10-08 09:12 | Early Revival · 60 · 7 | $7.06M | $2.82M | **-60.1%** | +28.9% | -60.9% | +16.0% | +4.3% | -15.3% | -25.9% |
| #10 | PAID `98kfF7` | 10-09 15:38 | Watch · 50 · 9 | $4.73M | $3.85M | **-18.5%** | +6.6% | -30.5% | -8.4% | -9.4% | -28.1% | -5.8% |
| #11 | PAID `98kfF7` | 10-10 02:02 | Watch · 45 · 9 | $4.46M | $3.85M | **-13.6%** | +13.1% | -26.3% | -5.6% | -20.9% | -21.3% | +7.5% |
| #12 | SI `9aqmJj` | 10-10 02:17 | Watch · 46 · 9 | $1.44M | $0.83M | **-42.1%** | +24.9% | -42.1% | -4.8% | -6.6% | +0.3% | -4.0% |
| #13 | DARK `7KEPAp` | 10-10 05:29 | Watch · 41 · 9 | $5.26M | $2.99M | **-43.0%** | +0.0% | -62.8% | -4.9% | -8.3% | -34.4% | -49.0% |
| #14 | HOOKED `C1mBfB` | 10-10 14:19 | Watch · 42 · 9 | $7.40M | $1.98M | **-73.3%** | +7.4% | -74.0% | +3.0% | -18.1% | -35.9% | -53.6% |
| #15 | baton `Hg5Ja5` | 10-10 17:19 | Watch · 42 · 9 | $11.08M | $10.55M | **-4.8%** | +35.3% | -12.6% | -2.4% | +10.0% | +26.3% | +16.5% |
| #16 | PAID `98kfF7` | 10-11 01:29 | Watch · 42 · 9 | $3.53M | $3.85M | **+9.1%** | +42.8% | -3.6% | -1.3% | +15.9% | +19.7% | +6.9% |
| #17 | e/acc `CbcyNo` | 10-11 02:54 | Watch · 50 · 9 | $1.78M | $1.25M | **-29.9%** | +26.5% | -40.4% | -2.5% | -2.4% | +6.7% | -25.6% |
| #18 | PAID `98kfF7` | 10-11 07:29 | Watch · 45 · 9 | $4.09M | $3.85M | **-5.8%** | +23.2% | -13.7% | -6.0% | -1.0% | +1.9% | +7.7% |
| #19 | DARK `7KEPAp` | 10-11 08:14 | Watch · 41 · 9 | $3.82M | $2.99M | **-21.7%** | +21.6% | -48.9% | +0.2% | -24.6% | +3.2% | -10.4% |
| #20 | RARI `EFn88C` | 10-11 18:49 | Watch · 41 · 9 | $1.13M | $0.43M | **-62.3%** | +25.8% | -67.9% | +6.2% | +4.8% | -32.0% | pending |
| #21 | e/acc `CbcyNo` | 10-11 22:44 | Watch · 42 · 9 | $2.09M | $1.25M | **-40.0%** | +0.8% | -49.0% | -4.0% | -6.3% | -32.2% | pending |
| #22 | PAID `98kfF7` | 10-11 22:44 | Watch · 42 · 9 | $4.23M | $3.85M | **-8.9%** | +19.3% | -16.5% | +1.8% | +6.1% | +9.0% | pending |
| #23 | HOOKED `C1mBfB` | 10-12 04:35 | Watch · 43 · 9 | $5.68M | $1.98M | **-65.2%** | +0.1% | -66.1% | -2.1% | +0.1% | -41.4% | pending |
| #24 | JEANPHIL `GTBxUi` | 10-12 06:10 | Watch · 50 · 9 | $8.84M | $7.89M | **-10.7%** | +6.5% | -27.9% | -0.2% | -7.4% | -18.1% | pending |
| #25 | PAID `98kfF7` | 10-12 09:00 | Watch · 41 · 9 | $4.40M | $3.85M | **-12.4%** | +14.6% | -19.7% | -0.6% | +4.4% | -3.0% | pending |
| #26 | PAID `98kfF7` | 10-12 11:10 | Watch · 56 · 9 | $5.00M | $3.85M | **-23.0%** | +0.8% | -29.4% | -5.0% | -6.9% | -17.5% | pending |
| #27 | HOOKED `C1mBfB` | 10-12 13:45 | Watch · 49 · 10 | $5.13M | $1.98M | **-61.5%** | +2.1% | -62.5% | -1.6% | -23.8% | -29.3% | pending |
| #28 | HOTBOT `8nnaeW` | 10-12 15:25 | Early Revival · 60 · 10 | $4.53M | $4.36M | **-3.8%** | +14.3% | -30.4% | -2.9% | -19.2% | -22.1% | pending |
| #29 | Agency `7Vertk` | 10-12 16:55 | Watch · 41 · 10 | $1.91M | $1.41M | **-26.3%** | +3.4% | -29.9% | -9.5% | -17.0% | -15.5% | pending |
| #30 | Agency `7Vertk` | 10-12 17:20 | Watch · 53 · 10 | $1.92M | $1.41M | **-26.5%** | +0.0% | -30.1% | -10.9% | -17.1% | -16.5% | pending |
| #31 | JEANPHIL `GTBxUi` | 10-12 19:40 | Watch · 46 · 10 | $8.14M | $7.89M | **-3.0%** | +15.6% | -21.7% | +0.3% | -15.0% | +9.9% | pending |
| #32 | JEANPHIL `GTBxUi` | 10-13 00:55 | Early Revival · 63 · 10 | $6.37M | $7.89M | **+23.8%** | +47.7% | +0.0% | +11.1% | +14.8% | +38.9% | pending |
| #33 | PAID `98kfF7` | 10-13 06:10 | Watch · 41 · 10 | $4.80M | $3.85M | **-19.7%** | +0.8% | -26.4% | -1.2% | -22.3% | -20.3% | pending |
| #34 | PAID `98kfF7` | 10-13 09:30 | Watch · 53 · 10 | $4.35M | $3.85M | **-11.5%** | +2.7% | -18.9% | -4.5% | -12.3% | -2.3% | pending |
| #35 | Agency `7Vertk` | 10-13 10:20 | Watch · 43 · 10 | $1.56M | $1.41M | **-9.5%** | +14.0% | -14.0% | -4.3% | +3.5% | -10.6% | pending |
| #36 | HOOKED `C1mBfB` | 10-13 10:55 | Watch · 49 · 10 | $3.64M | $1.98M | **-45.6%** | +9.0% | -47.1% | +6.2% | -3.7% | -45.5% | pending |
| #37 | JEANPHIL `GTBxUi` | 10-13 14:35 | Watch · 41 · 10 | $8.50M | $7.89M | **-7.2%** | +10.7% | -13.7% | -0.0% | -4.2% | pending | pending |
| #38 | SI `DEW9dS` | 10-13 20:45 | Watch · 42 · 10 | $14.80M | $14.85M | **+0.3%** | +5.7% | -6.4% | +5.7% | +0.8% | pending | pending |
| #39 | baton `Hg5Ja5` | 10-13 21:30 | Watch · 41 · 10 | $12.17M | $10.55M | **-13.3%** | +3.4% | -20.4% | -4.1% | +1.7% | pending | pending |
| #40 | baton `Hg5Ja5` | 10-13 21:45 | Watch · 53 · 10 | $11.69M | $10.55M | **-9.7%** | +7.6% | -17.2% | -2.7% | +6.6% | pending | pending |
| #41 | JEANPHIL `GTBxUi` | 10-14 03:55 | Watch · 41 · 10 | $7.71M | $7.89M | **+2.4%** | +14.8% | -4.9% | +0.0% | -2.0% | pending | pending |
| #42 | baton `Hg5Ja5` | 10-14 04:55 | Watch · 47 · 10 | $11.62M | $10.55M | **-9.2%** | +0.0% | -16.7% | -6.8% | -2.9% | pending | pending |
| #43 | DARK `7KEPAp` | 10-14 05:50 | Watch · 57 · 10 | $2.90M | $2.99M | **+3.1%** | +27.9% | -7.0% | +19.6% | +3.2% | pending | pending |
| #44 | PAID `98kfF7` | 10-14 06:45 | Watch · 42 · 10 | $3.95M | $3.85M | **-2.5%** | +13.3% | -3.4% | +6.4% | +0.0% | pending | pending |
| #45 | PAID `98kfF7` | 10-14 07:25 | Watch · 58 · 10 | $4.21M | $3.85M | **-8.5%** | +6.3% | -9.3% | +4.3% | -8.4% | pending | pending |
| #46 | JEANPHIL `GTBxUi` | 10-14 11:00 | Watch · 46 · 10 | $8.72M | $7.89M | **-9.5%** | +1.6% | -11.0% | -2.2% | pending | pending | pending |
| #47 | HOOKED `C1mBfB` | 10-14 11:00 | Watch · 43 · 10 | $1.96M | $1.98M | **+1.2%** | +6.9% | -1.6% | +6.5% | pending | pending | pending |
| #48 | SI `DEW9dS` | 10-14 12:56 | Watch · 45 · 10 | $14.41M | $14.85M | **+3.0%** | +4.2% | -2.2% | pending | pending | pending | pending |
| #49 | CRAWL `BXoHJd` | 10-14 13:06 | Watch · 40 · 10 | $2.37M | $2.20M | **-7.4%** | +0.0% | -7.6% | pending | pending | pending | pending |

Summary of the 43 measurable calls: **7 positive, 36 negative; median −11.5%,
mean −20.2%; median MFE +7.4%, median MAE −21.7%; no call reached +50% observed
MFE.** These are observed market-cap changes, not executable returns, and the
calls overlap heavily (16 distinct tokens; see the seven-day review for bias).
