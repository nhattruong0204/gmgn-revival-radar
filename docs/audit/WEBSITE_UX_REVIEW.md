# Website UX review (V2)

Audited live at https://onchainflow.app on 2026-10-14 (~13:40 UTC) with headless
Chromium (Playwright) at 390×844 (touch), 768×1024, 1366×768, 1920×1080 and
1366×768 at 200% zoom (683 CSS px, DPR 2), timezone Asia/Bangkok. Screenshots:
`docs/audit/v2-screenshots/`. Design baseline: `design-system/onchainflow/MASTER.md`.

## Mechanical quality (Fact)

All 8 pages × 5 sizes: no horizontal page overflow, no text below 12px, no console
or page errors, owner area correctly gated behind login. The design system
(dark navy, mint accent, DM Sans, tabular figures) is consistent and should be
**kept**. The problems are information design and product semantics, not styling.

Not verified in this audit: screen readers, Firefox/WebKit, real devices,
authenticated owner screens (no owner credentials were requested or used).

## Page-by-page against one primary question

| Page | Primary question | Verdict | Main problems |
|---|---|---|---|
| Radar `/` | What is developing right now? | Partly answers | Shows 2 WATCH tokens with score/MC/liquidity, which is clear. But no "what changed" (score delta, trigger event), no link to "this token was already called N times", filters (window/chain/stage/min score/sort) take a full row before any result. Copy "Inspect saved evidence, freshness and counter-evidence" describes the system, not the user's job. |
| Signals `/alerts` | What did the system publish, and why? | **Fails** | 25 identical-looking rows per page: symbol, stage chip, timestamp, score. No market cap at call, no current MC, no change, no reason, no grouping by token (PAID appears 12×). "Saved signal · 40m ago · …GMT+7" and "Origin unclassified" repeat on every row. |
| Signal detail `/alerts/:id` | What happened since this call? | **Fails** | Alert-time MC ($4.2M) and latest MC ($3.8M) are in different panels and never compared. Four checkpoint rows each repeat the full token identity, contract, revision and "Unknown score version". "Why it's on the radar" is always empty (defect D3). Risk panel omits the warnings saved with the call (defect D2). The useful "+1h +4.3% / +6h −8.4%" strip is at the very bottom. |
| Token `/tokens/:chain/:addr` | What changed since the signal; what supports/contradicts it? | Partly | Good chart and brief. But it does not list this token's calls or their performance. Raw keys leak (`no_base`, `Source: missing_fields.0`). Counter-evidence list is 12 items of mixed severity with no ranking; "Supporting facts: none". |
| Track record `/outcomes` | How did previous calls perform? | **Fails** | 6,500 px tall at 1366. Headline "Median observed change: **Withheld**". Unit is a checkpoint row, mostly "Pending". No per-call card, no sort by performance, no winners/losers view. The correct caveats are present but they dominate the page. |
| Reports `/reports` | What did the scanner learn about the market? | Adequate | Retrospective top-ten list is understandable. Lacks a one-paragraph market summary (universe drift, eligible count, calls) which is exactly the regime context the product needs. |
| Methodology | How does it work? | Adequate | Accurate, but does not state the current *published* alert threshold or stage policy, which is the most important thing a reader should know right now. |
| Owner `/admin` | — | Gate verified | Login form only (no credentials used). |

## Cross-page incoherence (the "no consistent story" complaint)

1. **Four different units**: Radar = latest evaluation per token; Signals = call;
   Track Record = call × horizon checkpoint; Token = token. Nothing links a call
   to its token's later state except two separate panels.
2. **Two performance measures**: checkpoints use price; cards show MC. Neither is
   labelled as the headline metric.
3. **No shared call component**. Each page renders call facts differently.
4. **Caveat over-weighting**: legally careful text ("not executed P&L", "origin
   unclassified", "legacy unknown") is repeated per row instead of once per page.

## Required V2 UX outcome (proposal)

- One reusable **CallPerformanceCard** (see `docs/architecture/V2_ARCHITECTURE.md` §4)
  used on Signals, Track Record, Token and Signal detail. Dominant figures:
  MC at call → latest MC, change since call, elapsed time. Then MFE/MAE and the
  four checkpoints as a compact strip, stage/score/revision, top 2 reasons, top 2
  risks, freshness of the latest observation.
- **Signals** = chronological feed of CallPerformanceCards, with a "group by
  token" toggle that keeps every call visible inside the group.
- **Track Record** = performance journal: summary bar with explicit denominators
  (calls / measurable / positive / negative / pending; median and distribution of
  change-since-call and of +24h), then sortable card/compact-row list. Pooled
  revisions still show descriptive medians with a visible "mixed configurations"
  label instead of withholding them; exact-cohort selection remains available.
- **Token** = add a "Calls on this token" section using the same cards.
- Translate rule keys to plain language through one dictionary; never print
  `missing_fields.N` style sources to visitors (keep them in a disclosure).
- Show each caveat once per page in a footer/disclosure.

Mobile: CallPerformanceCard must work as the 390px layout first; tables become
cards (already the documented pattern in MASTER.md).
