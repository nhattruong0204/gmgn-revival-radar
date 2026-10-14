# V2 implementation plan

Owner review required before any implementation starts. Issue drafts with full
acceptance criteria: `docs/roadmap/issues/`. Effort sizes: S ≈ one focused agent
session, M ≈ 1–2 sessions, L ≈ 3+ sessions split into PRs.

## Wave 0 — decisions (owner, this week)

| Decision | Recommendation | Alternative | Trade-off |
|---|---|---|---|
| Published-call policy (V2-Q1) | Stage gate at EARLY_REVIVAL + evidence-based re-alerts; WATCH stays website-only | Keep threshold 40 and only improve the UI | Fewer calls (expect ~0–3/day in the current regime) vs. a record that keeps measuring WATCH noise. A silent radar is honest; a noisy one is not useful. |
| Headline performance metric | Market-cap change since call (labelled "observed") | Price change | MC matches what traders read on GMGN; price is kept for checkpoints. |
| Track Record pooling | Show descriptive stats for mixed revisions with a label | Keep withholding | Withholding is statistically pure but makes the page useless; labelling preserves honesty. |
| Subscriptions/paid (#46/#47/#50) | Block until the V2 gate passes | Continue in parallel | Selling an unproven record would be misleading. |

## Wave 1 — P0 trust foundation (≈ 2–3 weeks of agent sessions)

Order matters: data contract → component → pages.

1. **V2-D2** Alert risk warnings fix (S) — isolated bug, ship first.
2. **V2-D1** Call performance read model (M).
3. **V2-D3** Call data-integrity acceptance suite + parity script (M).
4. **V2-U1** CallPerformanceCard (M).
5. **V2-U2** Track Record performance journal (L, split API/UI PRs).
6. **V2-Q1** Published-call policy code behind settings (M); owner activates after review.

## Wave 2 — P1 coherence and research (≈ 2–3 weeks)

- V2-U3 Signals feed, V2-U4 plain-language reasons, V2-U5 token calls section.
- V2-Q2 research export/report, V2-Q3 regime context, V2-Q4 metric definitions.
- V2-G1 release gate executed on staging with a production copy → owner-approved deploy.

## Wave 3 — P2 evidence and research depth

- V2-Q5 replay-grade retention and harness.
- Helius #10 (Token-2022 risks), #11 (holders), #12 re-scoped (participation), #13.
- Only after 2–4 weeks of stage-gated calls: component re-weighting proposals,
  evaluated walk-forward on data not used to design them.

## Mandatory data-integrity tests (V2-D3)

1. Published call preserves original timestamp and market cap.
2. Latest MC comes from a timestamped, strictly later observation.
3. Displayed arithmetic equals recomputed arithmetic.
4. Missing observations → Unknown, not zero.
5. Multiple calls for one token stay distinct.
6. Telegram/API/Signals/Track Record/CSV share call IDs.
7. Strategy revision stays attached to the original call.
8. Enrichment never overwrites historical snapshots.
9. Positive/negative/pending/stale/failed states render correctly.
10. Historical cohorts reproduce at a fixed `as_of`.

## Costs

- No new paid infrastructure required. Current VPS (2 GB RAM, 50 GB disk, 46% used)
  is sufficient; watch disk growth (DB 1.5 GB after ~8 days, mostly evaluation
  state) and add retention (V2-Q5) before it becomes a problem.
- Helius/GMGN: no additional calls in Wave 1–2. Wave 3 Helius features consume the
  existing Helius plan budget; size them in their issues before enabling.
- Agent effort: Wave 1 ≈ 8–10 focused sessions, Wave 2 ≈ 8 sessions.

## Release gate for V2

Not ready until: parity script passes on a production copy; screenshots at
390/768/1366/1920/200% for Signals, Track Record, Token, Signal detail; negative
and missing-data scenarios shown; load replay including the performance query
passes the unchanged gate; production SHA equals `main`; owner approves deploy.
