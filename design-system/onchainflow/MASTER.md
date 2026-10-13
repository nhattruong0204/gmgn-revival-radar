# OnchainFlow reviewed research design system

Reviewed 2026-10-13 for #51/#41. This is the source of truth for subsequent
#40–#50 UI work; page rules extend it only where the page job demands it.
Implementation: React 19 + TypeScript, Vite, CSS; no framework or chart dependency
change. Tokens live in `web/src/design-tokens.css`. Radar was the first exemplar; #40 extends the shared system to the shell, public
research and owner screens. See `issue-40-verification.md` for measured scope and
limits; this is not independent whole-site accessibility certification.

## Product and journeys

The reader investigates saved observations and counter-evidence, rather than
receiving trading promises. Keep OnchainFlow's dark navy, mint accent, restrained
borders and dense research identity. Principal journeys:

1. Recent shortlist → token workspace → source-linked brief → save/return.
2. Immutable signal journal → checkpoint evidence → observed track record.
3. Visitor → account/saved research → subscription/preferences (future #46–#48).
4. Owner → diagnostics → exact preview → confirmation → durable result/history.

Navigation labels must describe these jobs. Radar, Signals, Reports, Track record
and Methodology are research navigation. Accounts/billing are a separate future
visitor capability; owner Overview/Settings/Actions remain visibly separate.
Do not implement subscriber or paid entitlements with owner sessions. Preserve
existing URLs/deep links and back/filter context. No fabricated subscribers,
returns, confidence or unlocked features in navigation.

## Skill decision record

Primary skill: UI UX Pro Max, pinned commit
`7246805992a42b7bba37d4cac83b01416f0c6c44`; install via
`python scripts/setup_ui_ux_skill.py`. Read its SKILL.md first. Local searches:

- `analytics dashboard --design-system -p OnchainFlow --density 8`: verified
  Data-Dense Dashboard style; adopted minimal chrome, tabular values, filters,
  table/card parity. Rejected Enterprise Gateway, sales/contact CTA, video/logo
  hero, generated blue/orange palette and external Fira font imports.
- `status warning stale data --domain ux`, retry `data freshness indicators`:
  results addressed forms/bulk entry, not observation freshness. No verified
  freshness match; the explicit time/provenance semantics below are product and
  API contracts, not claimed skill findings.
- `time series missing data --domain chart`: verified Trend Over Time guidance:
  directly labeled series/line styles, keyboard inspection and a visible data
  table. Missingness stays a gap; do not adopt forecasts or install suggested
  libraries. Existing chart improvements belong to #42.
- `state forms accessibility --stack react`: controlled input guidance adopted.
  Preserve existing tested async result handling; useActionState is optional,
  not a mandate to replace the command execution model.

User explicitly waived recovery of unfinished local WSL work on 2026-10-13 for
#51/#41. No WSL files were accessed or deleted. The owner then requested #40 from this
published foundation; no abandoned WSL diff was recreated. Base includes merged PR #38
(launch fixes) and #53 (skill integration). No claim of inspecting private live
production or of deploying these changes.

## Semantic tokens and density

| Role | Token/value | Meaning |
| --- | --- | --- |
| Background/surface | `--research-bg` #0b141d / `--research-surface` #101c26 | Hierarchy, never status |
| Text/muted | #d9e4ea / #a8b7c4 | Primary facts / metadata, not disabled facts |
| Accent/focus | #64dda9 | Navigation/action and visible focus, not asset safety |
| On accent | `--research-on-accent` #0b2318 | Text on mint action buttons; distinct from text on dark surfaces |
| Warning/danger | #e5bb73 / #ef9292 | Missing/stale evidence or error, always with text |
| Control border | #607584 | Visible actionable boundary; decorative panel borders may be subtler |
| Spacing | 4, 8, 12, 16, 24px | Compact rows and progressively larger grouping |
| Radius | 6px controls, 12px panels | Preserve established shape language |
| Motion | 150ms feedback; none for reduced motion | No decorative/continuous animation |

Locally served DM Sans variable 100–1000, normal, font-display swap; sans-serif
fallback. Body/explanations 16px, research controls/body 14px, tables 13px,
secondary numeric/metadata 12px minimum, headings 28/20px. Dense exceptions are
intentional for comparison, never tiny instructions or authentication errors.
Use tabular figures for comparisons; exact units/timestamps must remain visible.
Named `--research-type-*` tokens use rem units (body 0.875, metadata 0.75,
table 0.8125, title 1.75, heading 1.25, value 1.5) so user text sizing scales them.
SVG chart labels use 12px at a minimum 960px SVG width inside a named scrolling
region; the full data table and exact timestamps scale with the text setting.
Fonts and provenance: `web/public/fonts/OFL-DM-Sans.txt` and `fonts/README.md`.
No external font request; existing CSP remains authoritative.

Contrast targets: normal text ≥4.5:1; large text and meaningful UI boundaries
≥3:1. See `verification.md` for measured pairs and browser evidence. The preferred
primary touch target is 44×44 CSS px with 8px spacing. WCAG 2.2 AA minimum is
24×24 or a valid spacing/inline-text exception: document exceptions instead of
claiming all links must be 44px. Keyboard focus: 2px mint outline, 3px offset,
visible and unobscured; no hover-only evidence. Support 320px wide, 200% zoom,
keyboard, touch and prefers-reduced-motion. Charts can scroll inside their own
region; the whole page must not overflow.

## Shared component contracts

| Pattern | Required behavior | Implementation / next consumer |
| --- | --- | --- |
| Filters | Native modal on mobile (immediate URL changes, Show results closes); visible names; URL-backed selection; reset pagination; explain window; search submit/clear; mobile drawer preserves the same controls | Radar / #40 |
| Table/mobile cards | Same identity, timestamp, origin, evidence and principal workspace link; stable sort/pagination; no hidden mobile facts | Radar / #40 |
| ObservationStamp | Visible exact evaluation time, relative age, historical label after 24h, recorded/fixture/unclassified origin; never infer source from score | Shared component, Radar/TokenDetail/Publications |
| Freshness/coverage | Worker heartbeat, scan completion and per-row observation are separate; fresh worker never changes old evidence | #41 API and docs/data-provenance.md |
| Evidence disclosure | Summary then support/counter-evidence/missing fields, source and timestamp; unavailable values stay unknown | SignalEvidence / #42/#43 |
| Chart inspection | Accessible name/units, keyboard/touch inspection, direct legend and table fallback; closed-candle time separate from evaluation | ObservedChart / #42 |
| Form/error | Controlled fields, persistent visible label, linked field error plus summary/focus for multiple errors; retain input on failure | Owner controls / #40/#46 |
| Dialog | Shared native Modal with background isolation and original-trigger capture before async owner requests; name, focus entry/trap/return, Escape/cancel, exact before/after, explicit consequential action; no outside click confirms | Existing tested owner preview / #40 |
| Action/history | Submitted/queued/running/succeeded/failed/uncertain distinct; restore by durable id; refresh history before retrying uncertain actions | Owner commands / #48 |

Shared states:

- Loading: labeled status, reserved area; no fake rows/values. Refresh keeps prior
  saved facts with “refreshing” and their original timestamps.
- Empty: explain selected age/coverage/filters, offer a history/reset action.
- Stale: show exact old evidence time; keep history, never substitute current time.
- Partial/unknown: show unavailable fields/gaps and missing coverage; no zero fill.
- Offline/error: retain saved data with error text and retry, or show unavailable
  if no data; no invented snapshot to fill the gap.
- Unauthorized/private beta: explain access state and valid next action without
  revealing owner settings or treating subscriber login as owner authorization.
- Queued/running: pending result is not success. Failed: show reason and safe
  retry. Uncertain: reconcile durable history; do not auto-repeat a side effect.
- Synthetic preview: persistent text banner on every public fixture page; never
  mix fixture evidence into a default production shortlist or track record.

Every status uses text, not hue alone. Do not add alert-role noise to every row;
announce operation errors/results once. Broader state and page migration follows
#40/#49; billing/auth correctness is independently verified in its own issues.
