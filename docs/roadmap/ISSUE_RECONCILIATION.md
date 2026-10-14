# Issue reconciliation (V2 audit, 2026-10-14)

Source: all 60 GitHub issues/PRs and 27 comments (public API), current `main`
(`a784f67`, which is what production runs), and the live-site/data audit in
`docs/audit/`. "Verified in production" means checked on onchainflow.app or the
production DB copy during this audit.

**Applied on 2026-10-14** after the owner authorised GitHub changes: new issues
#62–#75 created, labels/comments applied, superseded PRs #9/#15 closed. Items
that need an owner decision (#52 closure, branch deletion) were left open.

## Decisions

| # | Title (short) | Code status | Production verified | Decision | Action |
|---|---|---|---|---|---|
| 1–5 | Telegram UX, pipeline, caching, S/T/C scoring, health/outcomes | Merged | Yes (scanner runs; outcomes recorded) | Completed and verified | None. #4's scoring is re-examined in V2-Q issues, not reopened. |
| 6–8 | Audit/plan/Hot-Search-only PRs | Merged | Yes (Hot Search only, 20/scan) | Completed | None. |
| PR 9 | Helius mint verification | Branch is **0 commits ahead of `main`**; mint verification is live (`solana_mint.provider = helius` in call snapshots) | Yes | **Superseded** (landed via #38) | Close with comment. |
| 10 | Helius Token-2022 extension risk | Not implemented: live calls say "Token-2022 extension risks not assessed" | — | Still necessary, **P2** | Keep; label `v2-p2`. |
| 11 | Helius holder concentration/growth | Not implemented | — | Still useful as evidence, **P2** | Keep; feed into V2-Q enrichment decision. |
| 12 | Helius buyer participation + exploratory WATCH alerts | Not implemented | — | **Needs re-scope**: participation metric is useful; *exploratory WATCH alerts* contradict the V2 finding that WATCH-quality calls underperform | Comment and narrow to participation evidence only. |
| 13 | Activity-triggered refresh | Not implemented | — | Still necessary, **P2** | Keep. |
| 14 | Batch mint verification | Not implemented | — | Low value at 20 checks/scan, **P3** | Keep, low priority. |
| PR 15 | Solana market reports | Branch **0 commits ahead of `main`**; reports live | Yes (`/reports`, 4-hourly reports in DB) | **Superseded** | Close with comment. |
| 16–26 | Website launch epic and children | Merged | Yes (HTTPS, public API, owner gate) | Completed and verified | None. |
| 27–38 | Website PRs | Merged | Yes | Completed | None. |
| 39 | Research product epic | Open | — | Still the right umbrella, **priorities wrong** | Update body: add "V2 trust foundation" wave first; move #46/#47/#50 behind it. |
| 40 | Readable, coherent pages | Closed | Mechanically yes (no overflow, ≥12px, focus) | Completed in code; **product outcome not met** (pages not coherent) | Keep closed; link follow-ups #65 (V2-U1)…U5 as the coherence regression. |
| 41 | Current vs historical separation | Closed | Yes (24h default window, origin labels) | Completed and verified | None. |
| 42 | Candle workspace | Closed | Yes (chart renders saved candles) | Completed and verified | None. |
| 43 | Versioned research brief | Closed | Yes, but "Supporting facts: none" and empty "Why it's on the radar" | Completed in code; **partially useful** | Link #68 (V2-U4) (plain-language reasons). |
| 44 | Elliott-wave prototype | Open | — | **No longer aligned** with priorities | Keep open, label `deferred`; do not schedule before V2 gate. |
| 45 | Auditable track record | Closed | Yes — deployed today | Completed in code and verified for correctness; **fails the product question** | Keep closed (its contracts are correct and needed); link #66 (V2-U2) as the regression follow-up. |
| 46 | Subscriber accounts | Open | — | Still valid later, **blocked** by V2 trust foundation | Label `blocked-by-v2`. |
| 47 | Paid offer | Open | — | **Blocked**: selling an unproven track record would be misleading | Label `blocked-by-v2`. |
| 48 | Daily research loop | Open | — | Valid after calls are trustworthy | Label `blocked-by-v2`. |
| 49 | Accessibility/browser/capacity gate | Open | Partly (this audit adds 5-size Chromium evidence) | Still necessary, continuous | Add comment with this audit's evidence; keep open. |
| 50 | Activation/conversion measurement | Open | — | Premature | Label `blocked-by-v2`. |
| 51 | Design system | Closed | Yes | Completed and verified | None. |
| 52 | Reconcile deployed release / WSL work | Open | **Yes: production = `main` = `a784f67`**, schema 7, single scanner | Acceptance item 1–2 met; access-policy item needs owner confirmation | Comment with evidence; close after owner confirms the public access policy. |
| 53–60 | Design/research/deploy PRs | Merged | Yes | Completed | None. |

## Branch hygiene

All remote feature branches except four are 0 commits ahead of `main`. The
remaining ones (`feat/cohort-track-record` 1, `feat/versioned-research-brief` 2,
`fix/production-report-read-cost` 1, `fix/report-configuration-index` 9) were
squash-merged; their extra commits are pre-squash history. They can be deleted
after the owner confirms. Six local stashes from the Codex deployment sessions
exist on the owner's WSL checkout and must not be dropped without review.

## New issues proposed

See `docs/roadmap/issues/` (14 drafts) and `V2_IMPLEMENTATION_PLAN.md`.
