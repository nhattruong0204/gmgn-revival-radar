# OnchainFlow V2 audit — executive summary

Date: 2026-10-14. Scope: repository, all 60 GitHub issues/PRs, live website,
production database copy, VPS runtime. Read-only: no production setting, data,
service or GitHub state was changed. Companion documents:

| Document | Content |
|---|---|
| `ALERT_RECORD_CONSISTENCY.md` | Call identity, layer parity, defects D1–D9, per-call table |
| `SEVEN_DAY_SIGNAL_REVIEW.md` | Funnel, outcomes, baselines, scoring components, missed/late calls |
| `WEBSITE_UX_REVIEW.md` | Page-by-page review with screenshots |
| `../architecture/V2_ARCHITECTURE.md` | Call model, read model decision, card contract |
| `../roadmap/V2_IMPLEMENTATION_PLAN.md` | Waves, decisions, tests, costs |
| `../roadmap/ISSUE_RECONCILIATION.md` | Decision per existing issue |
| `../roadmap/issues/` | 14 new issue drafts |
| `v2-analysis/` | Reproducible analysis scripts (run against a DB copy) |

## Production facts (verified)

- Host `130.94.7.127` serves `onchainflow.app` (DNS and host IP checked).
- Running code `a784f67` = GitHub `main`; schema 7; containers web (healthy),
  radar (exactly one scanner), caddy. Scans every ~5 min, 0 errors since cutover.
- DB 1.5 GB after ~8 days. Largest objects: `state` 726 MB (102K saved evaluation
  presentations), **`state_report_configuration` index 471 MB** (it stores each
  ~4 KB configuration JSON inside the index), `token_snapshots` 187 MB.
- Daily online backup at 03:00 to `/root/onchainflow-backups/daily` on the **same
  host**; 4.7 GB of release-rehearsal copies in `/root/onchainflow-releases`.
  Disk 46% of 50 GB. RAM 2 GB; web ~160 MB, scanner ~40 MB.
- Public API: main pages 40–130 ms warm; 7-day report ~1 s cold (budget 1.5 s).

## Overall assessment of V1

The engineering foundation is **better than the product**. Data capture is
careful: calls are immutable, checkpoints are exact, API and DB agree on all 49
calls, unknowns are preserved. The product fails for two separate reasons:

1. **What gets published.** Since Custom revision 9 lowered the alert threshold
   from 60 to 40, almost every call (40/43 this week) is a WATCH-stage observation,
   and declining tokens are re-called every 6 hours. These calls perform no
   better than any other Hot Search token (median −17.5% vs −15.2% at 24h).
2. **What the site shows.** No page answers "how did this call do?". There is no
   MC-at-call vs latest-MC comparison anywhere, Track Record withholds its median
   by default and lists checkpoints instead of calls, and the explanation panel
   is always empty.

## Top 10 product defects

1. No call performance (MC at call → latest MC, change, elapsed) anywhere (D1).
2. Track Record withholds its headline median and is organised by checkpoint (D4).
3. Alert pages hide the risk warnings saved with the call (D2).
4. "Why it's on the radar" is always empty for current calls (D3).
5. Signals list has no market cap/performance and no token grouping (D5).
6. Token page does not list the token's calls (D6).
7. Pages use four different units (token, evaluation, call, checkpoint) with no shared component.
8. Caveats repeated per row ("Origin unclassified", "legacy unknown") drown the facts.
9. Internal rule keys leak into visitor UI (D7).
10. Methodology does not state the live publishing threshold/stage policy.

## Top 10 data / quant problems

1. Alert threshold 40 publishes WATCH-quality calls; no stage gate in `Scanner.process`.
2. Re-alerts need only a 6h cooldown, not new evidence: 12 calls on PAID, 5 on falling HOOKED.
3. Overall score has ~zero rank correlation with outcome (ρ = +0.01, n = 43).
4. Calls ≈ baseline cohorts; no measurable edge this week.
5. Late calls after the move (HOTBOT +236% while rejected, then called at the top).
6. Discovery is Hot Search top-20 only, so Trending/both-source components are almost unearnable.
7. Bundler share is the strongest negative signal observed but carries only −5.
8. Missing risk evidence (insider ratio usually null) is never penalised.
9. Two performance definitions (price checkpoints vs MC) undocumented (D9).
10. Evaluations are pruned, so faithful replay/optimisation is not possible yet.

## Are alert records and performance trustworthy?

**Stored records: yes.** Identity, timestamps, alert-time MC and checkpoint
arithmetic are correct and immutable; 49/49 calls match across DB and API.
**Displayed performance: incomplete**, not wrong — it never shows the comparison
users need, and some saved risk context is dropped. Legacy calls #1–#6 lack
alert-time MC and must stay "unknown".

## Last seven days (summary)

43 calls on 13 tokens; 7 up, 36 down since call; median −11.5%; median MFE +7.4%,
median MAE −21.7%; none reached +50%. Sample is small, overlapping and from one
falling regime. It is enough to say "these calls have no demonstrated edge" and
not enough to tune weights.

## Scoring components: useful vs unproven

- Weak positive hints (hypotheses): base duration, Setup score, lower bundler share.
- Non-discriminating: drawdown, liquidity, holder retention (almost always earned).
- Unproven/noisy: 5m volume acceleration, 1h price change at call (negative).
- Structurally unearnable now: Trending, both-sources bonus.

## API / data-enrichment recommendations

| Candidate | Hypothesis | Source | Cost | Place |
|---|---|---|---|---|
| Buyer/seller counts & imbalance | Real demand precedes revivals | GMGN snapshot already has buys/sells 5m/1h | none | Evidence first, then score after trial |
| Holder growth (Δ holders/24h) | Survivors gain holders | GMGN `holders` across snapshots | none | Evidence |
| Bundler/insider share | Concentration predicts dumps | GMGN security (insider often null) | none | Stronger penalty after trial |
| Token-2022 extension risks (#10) | Hidden transfer controls | Helius (already called) | negligible | Risk gate |
| Holder concentration via Helius (#11) | GMGN top-10 can be wrong | Helius `getTokenLargestAccounts` | ~1 call/candidate | Evidence |
| Regime context (#72 (V2-Q3)) | Revivals fail in falling markets | Already-fetched discovery data | none | Explanation |
| Smart-money/wallet flows | Informed buying | GMGN `smart_money_count` exists; deeper flows need paid APIs | unknown | Defer |

Do not add paid data before the stage-gated call record exists to evaluate it.

## Owner decisions needed

1. Approve the published-call policy (stage gate + evidence-based re-alerts) — #70 (V2-Q1).
2. Approve MC change as the headline metric and labelled pooled stats on Track Record.
3. Confirm blocking subscriptions/payments (#46/#47/#50) until the V2 gate passes.
4. Approve closing superseded PRs #9/#15 and closing #52 after confirming access policy.
5. Decide on an off-host backup target (risk: daily backups live on the same VPS).
6. Consider rebuilding `state_report_configuration` to index a short configuration
   hash instead of the full JSON (≈ 470 MB saving) — needs a measured rollout.
7. Provide authenticated GitHub access (`gh auth login`) so issues can be created.
