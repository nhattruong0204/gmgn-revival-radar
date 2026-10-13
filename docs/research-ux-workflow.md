# OnchainFlow research redesign workflow

The owner requested [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
as the primary skill for the redesign. It supplies UI/interaction guidance; saved
facts, analytics methodology, authentication and billing retain their own contracts
and tests. The research backlog is tracked in [#39](https://github.com/nhattruong0204/gmgn-revival-radar/issues/39).

## Recover and establish the base

The owner reports `https://onchainflow.app` is live privately. PR #38 launch,
query/index/cache and operations changes were reviewed and merged into main on
2026-10-13 before #51/#41 implementation. The owner explicitly waived recovery
of unfinished WSL work for these issues because WSL is unavailable. No local WSL
files were accessed or deleted. #52 can track remaining deployment reconciliation;
this waiver permits #51/#41 to proceed from published source. Do not claim to have
inspected or updated the private production deployment from this environment.

## Use the pinned skill

From the repository root:

```bash
python scripts/setup_ui_ux_skill.py
python .agents/skills/ui-ux-pro-max/scripts/search.py \
  "financial chart comparison" --domain chart -n 2
python .agents/skills/ui-ux-pro-max/scripts/search.py \
  "state forms accessibility" --stack react -n 3
```

`tools/ui-ux-pro-max.json` pins upstream commit
`7246805992a42b7bba37d4cac83b01416f0c6c44`. The installer checks the commit,
copies standard-library Python search tooling, local catalogs/references and the
MIT license, and renders project-local Codex skill paths. It does not install
runtime packages or modify the app. Reuse a verified local source checkout with
`--source /path/to/ui-ux-pro-max-skill`. A different existing skill is preserved
and refused, not overwritten. Pin upgrades require a reviewed repository change.
The ignored `.agents` tooling is excluded from the production Docker context.

Read `AGENTS.md` and the installed `SKILL.md`. Detect the actual stack: React 19,
TypeScript, Vite and CSS; use `--stack react`. For a targeted concern, search one
observable outcome in one domain with 2–5 meaningful terms, inspect relevance,
and retry once if off-topic. No provider data, private identifiers or secrets
belong in queries or design artifacts. Search runs locally without provider calls.

## Establish reviewed design artifacts

[#51](https://github.com/nhattruong0204/gmgn-revival-radar/issues/51) owns:

- `design-system/onchainflow/MASTER.md`: reviewed semantic type/color/spacing,
  density/focus/motion tokens, licensed local fonts, page jobs and shared states.
- Justified overrides in `design-system/onchainflow/pages/`, read after MASTER.
- Shared navigation, filters, table/mobile card, evidence disclosure, chart
  inspection/fallback, freshness/coverage, form/error and owner-command contracts.

Preserve any available user artifacts first; WSL recovery was waived for #51/#41. Do not force-regenerate an existing master
or page override. A product-wide direction uses `--design-system`; inspect the
candidate before persisting with `--persist --output-dir "$PWD"`. The audit's
`crypto research dashboard` search and one narrower `financial analytics dashboard`
retry produced sales/enterprise landing patterns that do not fit the working
research surface. Those outputs were not persisted as a design system. Verified
OHLC/chart-accessibility, focus and React-form guidance is useful. The reviewed
foundation must resolve app navigation/density and font/brand fit, rather than
copying automatic conversion sections, futuristic fonts or invented proof.

Preserve the established OnchainFlow brand unless a reviewed product decision
changes it. Use a compact research hierarchy, readable values and progressive
disclosure; do not impose a sales hero above working evidence. Host fonts/assets
locally, check their licenses and keep the existing CSP. Treat chart libraries,
framework changes and animation packages as reviewed implementation decisions,
not required dependencies of a search result.

## Apply the skill across the backlog

| Issue | Design surface and guidance | Correctness boundary |
| --- | --- | --- |
| #40 | Shell, every public/owner page; typography, focus, responsive hierarchy | Existing owner sessions and exact command previews |
| #41 | Shortlist/history and coverage labels; state clarity | Source/origin, age windows, historical cutoffs, active versus historical chains |
| #42 | Token workspace; OHLCV inspection, pan/zoom, non-drag/table fallback | Closed candles, available timeframe/volume, units and gaps |
| #43 | Concise research brief and version diff; progressive disclosure | Source-linked support/counter-evidence, immutable versions, no invented confidence |
| #44 | Optional scenario overlays; alternate/unresolved/invalidation states | Causal pivots, deterministic rules and offline go/no-go evaluation |
| #45 | Journal/track record; distribution, cohort filters and drill-down | Honest denominators, negative/missing outcomes and original alert evidence |
| #46 | Account/watchlist/preferences; accessible auth and context recovery | Per-user authorization, revocation, recovery and owner isolation |
| #47 | Offer/billing; transparent limits and pending/error/cancel paths | Verified server payment/entitlement state and tested webhook reconciliation |
| #48 | Daily review/notifications; useful changes and preferences | Consent, watermark, deduplication, durable delivery and uncertain outcomes |
| #49 | Shared audit rubric for every milestone | Measured accessibility/browser/parity/security/steady/burst capacity evidence |
| #50 | First-use orientation and research journey | Privacy-aware events, cohort definitions and verified payment transitions |
| #51 | Reviewed master/page/component design foundation | Product fit and retained user work; recommendations are not empirical proof |
| #52 | Preserve and assess recovered UI/design artifacts | Git/deployment/migration correctness does not need a visual skill |

Existing Helius #10–#14 stay a separate backend evidence programme. Future source,
risk, holder, participation and coverage contracts can feed #41–#43/#48. A prettier
label cannot establish wallet attribution, complete holder coverage or predictive
validity. These provider features do not block the basic saved-data redesign.

## Delivery and verification

Start #51 + #41 on the merged published base under the owner’s WSL waiver. Implement #40, then #42/#43/#45.
Account architecture and basic saved tokens in #46 can proceed without waiting
for every brief; add brief attachment when #43 is ready. #48 can ship an in-product
daily review before external delivery. Offer interviews and privacy/event baselines
in #47/#50 start early; taking payment waits for real capabilities and secure
accounts/entitlements. #44 is optional P2 research, not a launch prerequisite.
Run #49 continuously from the shared foundation onward.

Every affected issue should include before/after desktop/mobile and relevant
keyboard/touch/state evidence. Target WCAG 2.2 AA: normal text contrast 4.5:1,
large text/non-text UI 3:1, focus not obscured, accessible status/forms/auth,
non-drag alternatives and correct 24px pointer-target rules/exceptions. Prefer
44px primary mobile hit areas; do not present native iOS/Android units or AAA
requirements as web AA. Verify 200% text resizing and 400%/320px reflow, with
intentional labeled data-region scrolling only. Test the issue's supported browsers,
reduced motion, slow/offline/error, stale/partial/unknown, private-beta, visitor,
subscriber and owner states.

Use the relevant existing tests plus frontend test/type/build/format commands.
Declare performance and load thresholds before comparison. Retain one scanner,
provider pacing, saved-data-only public reads, historical evidence and the existing
private exposure policy. Neither skill installation nor a mockup authorizes a
production deploy, notification, payment or change in public access.
