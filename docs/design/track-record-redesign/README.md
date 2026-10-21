# Track record page — redesign spec

Status: design approved by owner (2026-10-21). Not implemented.
Files in this folder:

| File | What it is |
|---|---|
| `README.md` | This spec: goals, layout, metric definitions, data changes, acceptance criteria, issue breakdown |
| `mockup.dc.html` | Source of the approved interactive mockup (claude.ai Design canvas format). Not runnable standalone: it needs the canvas runtime (`support.js`). Read it for exact layout, colors, copy and computation logic (`renderVals()`). |
| `cohort-fixture.json` | The real 53-alert cohort (Oct 14–21, +24h) transcribed from the live page, with the expected aggregate numbers. Use it as the golden fixture for metric tests. |

## 1. Problem

The current Track record page is an audit log rendered as UI:

- The verdict (median −11.9%, 22.9% positive) is grey 14px text; provenance (checkpoint windows, origin, rule rev, evidence IDs) takes ~80% of the pixels.
- Each table row is ~8 lines; 6 rows per screen. Rule name, "Checkpoint origin: live", "Observed checkpoint price change", "Captured brief association" repeat on every row.
- Newest-first sort puts 16 empty pending rows on the first page.
- Histogram: the −50%..0% bar (27 losers) is **green**; 3 of 5 bins are empty; −1% and −49% share a bin.
- Prices shown with 10 decimals and "unknown quote currency". Price × 1B supply lands at $1–15M, so the quote is USD.
- Repeated tokens are invisible: 53 alerts = 16 tokens. SI and baton were called 8× each.
- Two KPI cards show the same 35/53.

## 2. Page job

Answer, in this order:
1. **Verdict** — did acting on these alerts work? (win rate, median, edge after costs)
2. **Shape** — distribution of outcomes
3. **Drill-down** — which calls worked/failed
4. **Provenance** — raw saved values, IDs (one click deep, never default)

## 3. Layout (top → bottom)

### 3.1 Header
- Breadcrumb, title "Track record", one-line subtitle: "Every delivered alert, measured against the price saved 24h later. Research evidence, not trading P&L."
- Controls: Window (7d/30d), Horizon (+24h now; +1h/+6h once 6.3 lands), **cost toggle** "Apply 4% round-trip cost", Export CSV.
- Replaces: subtitle disclaimer + info banner + footnote (three layers → one subtitle + one footer line).

### 3.2 Verdict band
- Eyebrow: `Verdict · {resolved} resolved calls · {window}`.
- Headline sentence generated from data, e.g. "Alerts lost value 24h after the call. 27 of 35 resolved calls were down."
- Right-aligned note: "Gross returns · no fees or slippage" / "Returns include 4% round-trip fees and slippage".
- 5 tiles: Win rate (`8 of 35 resolved calls up`), Median change, Mean change, Best call (`baton · #69 · $6.95M → $9.15M`), Worst call. Values colored by sign; monospace tabular numbers; 28px.
- Coverage bar: segmented resolved / pending / missed with counts. Missed segment hatched. Legend includes the worst-case line: "If both rugged, median falls to −13.6%".

### 3.3 Insight cards (4, computed — never hardcoded)
| Card | Content (from fixture) |
|---|---|
| Concentration | "53 alerts, only 16 tokens". Top tokens by call count; share of alerts from the top 6 (68%). |
| First call vs re-call | First calls median −13.0% (3/14 up) vs re-calls −8.7% (5/21 up). Note survivorship: re-calls only happen on tokens that survived. |
| One-off calls | Tokens alerted once and their range (all 6 lost, −14%…−36%). Token-weighted median −14.6%. |
| Not collected yet (dashed) | Placeholder for peak / time-to-peak / max drawdown until 6.3 lands. Remove when shipped. |

Cards should only render when the condition is meaningful (e.g. hide Concentration if unique tokens ≈ alerts).

### 3.4 Distribution
- Dot strip: one dot per resolved call, x = change %, green > 0, red < 0, vertical jitter, dashed median line, zero line, ticks −40/−20/0/+20. Hover title: `SYM #id: −12.3%`.
- Replaces the 5-bin histogram. Clicking a dot may filter/scroll the table (nice-to-have).
- Domain: clamp to [p1 − pad, p99 + pad] of data; don't hardcode −45..35.

### 3.5 Table
Toolbar: tabs **Resolved (35) / Pending (16) / Missed (2) / All (53)** · Sort (Newest / Worst / Best; hidden on Pending/Missed) · view toggle **By alert / By token**.

**By alert** — one line per alert:

| Column | Notes |
|---|---|
| Token | Symbol (bold) + `mint[:4]…mint[-4:] · #id` mono 11px |
| Called | `Oct 20 · 21:47` local |
| Call | chip: `1st call` (blue tint) or `Re-call N` (neutral) |
| Entry MC | `$3.09M` |
| MC +24h | `$3.62M`, `—` if none |
| Change +24h | pill (`+17.3%` green / `−24.2%` red, `Pending` amber, `Missed` dashed grey) + diverging bar centered at 0 |
| Peak / Max DD | reserved, `—` until 6.3 |
| Checkpoint | `On time` / `Saved +10m late` (amber if ≥5m) / `due in 22h 40m` / `No price saved in window` |
| Chevron | expands detail |

Expanded row: full mint, entry & checkpoint price (USD, 8 sig figs), rule (`Custom · rev 10 · setup-trigger-confirmation-v1`), observed time + lateness, evidence ID `alert:{id}/checkpoint:24`.

**By token** — one line per token, sorted by call count desc then median asc:
Token · call strip (chronological chips: `+17` / `−24` / `…` pending / `?` missed, hover for #id/time) · Resolved `6 / 8` · Up `3 / 6` · Median · Best · Worst · First call result.

### 3.6 Footer
One short methodology paragraph (data source, quote currency, MC estimation, how change is computed, cost assumption).

## 4. Visual system
Dark, matches current app. From the mockup:
- Ground `#0F1318`, surface `#161B22`, border `#252C36`, row divider `#1F252E`, text `#E7EBF0`, secondary `#A3AEBB`, muted `#8A96A4` (min for small text — passes 4.5:1).
- Semantic: gain `#4ADE80`, loss `#F87171`, pending `#E5B454`, missed `#9AA4B1`, interactive `#8DB4FF`. Never use gain color for a loss bucket. Sign (`+`/`−`) always shown so color isn't the only cue.
- Fonts: IBM Plex Sans (UI), JetBrains Mono (all numbers, `tabular-nums`).
- Tables scroll horizontally in their own container; page works at 400px.

## 5. Metric definitions (backend, single source of truth)
- `ret = (checkpoint_price / initial_price − 1) × 100`; checkpoint = first saved price inside [due, due + 1h].
- Resolved = has `ret`. Pending = due in future. Missed = window closed, no price.
- KPIs computed over resolved only; pending/missed reported as counts, never as zero returns.
- `ret_net = ((1 + ret/100) × (1 − cost) − 1) × 100`, cost default 0.04 (configurable).
- `nth` = 1-based call index for that mint within the selected window (chronological).
- Token-weighted median = median of per-token medians.
- Worst-case median = median with missed treated as −100%.
- MC = price × supply. Store supply (or MC) at alert time; until then estimate with 1B for pump.fun mints and flag others as estimated.

Golden values (`cohort-fixture.json → expected`): resolved 35, positive 8, median −11.86, mean −8.97, best #69 +31.67, worst #79 −40.52, first-call median −12.97 (3/14), re-call −8.74 (5/21), token-weighted −14.59, worst-case −13.64, positive after 4% cost 7.

## 6. Data/backend changes required
1. Cohort metrics endpoint returning everything in §5 (one call, not computed in the browser).
2. Persist supply/MC and quote currency at alert time; drop "unknown quote currency".
3. **Multi-horizon snapshots** at +5m, +15m, +1h, +6h, +24h → derive `peak_multiple`, `time_to_peak`, `max_drawdown`, `hit_1_5x_before_-30%`. A single +24h point hides whether a call hit 2x first.
4. Per-token aggregation endpoint (or same endpoint, `group=token`).
5. Signal quality (separate from UI): re-alert cooldown / dedupe per mint (DOPE #83 and #84 fired 15 min apart).

## 7. Acceptance criteria
- With the fixture loaded, every number on the page equals §5 golden values.
- First screen (1440×900) shows verdict, tiles and coverage without scrolling.
- Resolved tab is default; no pending row appears in it.
- No row exceeds 2 text lines collapsed.
- Loss data never rendered in gain color; all % show sign.
- Cost toggle updates tiles, headline, dots, table, by-token view.
- Works at 400px wide with no page-level horizontal scroll.
- Existing CSV export unchanged (or extended with `nth`, `mc_entry`, `mc_checkpoint`, `ret_net`).

## 8. Suggested issue breakdown
Epic: **Track record page redesign**

| # | Title | Area | Depends on |
|---|---|---|---|
| 1 | Cohort metrics API (KPIs, coverage, first-call vs re-call, token-weighted, worst-case) + golden fixture tests | backend | — |
| 2 | Persist supply/MC and quote currency at alert time; MC formatting helper | backend/data | — |
| 3 | Per-token aggregation (`group=token`) incl. call sequence | backend | 1 |
| 4 | Verdict band: headline, 5 tiles, coverage bar | frontend | 1 |
| 5 | Insight cards (computed, conditional) | frontend | 1, 3 |
| 6 | Dot-strip distribution replacing histogram | frontend | 1 |
| 7 | Compact alert table: tabs, sort, one-line rows, expandable detail | frontend | 1, 2 |
| 8 | By-token view with call strip | frontend | 3 |
| 9 | Round-trip cost toggle (configurable, default 4%) | full-stack | 1 |
| 10 | Copy & color cleanup: single disclaimer, semantic colors, footer methodology | frontend | — |
| 11 | Multi-horizon snapshots + peak / time-to-peak / max drawdown columns | backend + frontend | 1 |
| 12 | Re-alert cooldown / dedupe per mint | alert engine | — |
