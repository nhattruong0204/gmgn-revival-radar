# Shared website migration — issue #40

Published source base: `2c7f42f` (merged #51/#41, PR #54). The owner chose to
continue #40 from this reviewed foundation instead of unavailable unfinished WSL
work. No WSL files or private live production data were accessed or changed.

## Implemented contract

OnchainFlow now uses one local DM Sans family, rem-based type roles, semantic
colors, compact headings/panels and tabular values across public and owner pages.
Body/form copy is 14px, essential metadata at least 12px, tables 13px and common
headings 28/20px at the default 16px root size. SVG labels are a documented 12px
exception in a minimum-960px, named scrolling chart region; the full saved-value
table grows with the text setting. Disabled-control contrast exceptions apply.

Radar begins at the shortlist. Collection/scan context follows in a disclosure;
mobile filters use the same controls/URL handlers in a dialog. Filter changes
apply immediately; Show results closes the dialog. Token pages put saved score,
full chain/contract, exact timestamp and chart first; mobile market metrics and
score dimensions/components have labeled disclosures. Duplicate component-reason
strings are removed from narrative support; saved narrative and counter-evidence
remain intact. Reports put leaders before collection totals. Empty outcome views
show one useful explanation instead of four duplicate unknown metric panels.

Navigation labels have one research job: Radar, Signals, Reports, Track record,
Methodology. Routes remain compatible. Report windows, outcome windows and
journal pagination are URL-backed. Workspace links preserve validated same-origin
return context; browser back restores the selected window/filter. Owner access is
separate. No scoring, provider, scanner, delivery, storage or authorization logic
changed; there are no new application dependencies or external fonts.

One native Modal handles navigation, filters and exact owner previews. It isolates
the background, locks page scrolling, focuses the first control, traps keyboard
focus, supports Escape where safe, and restores the original trigger. Owner
triggers are captured **before** an asynchronous request disables them. Pending
confirmations do not dismiss/replay on Escape. Preview values/revisions, expiry,
validation, queued/failed/unknown results and recovery history retain their
existing contracts. A skip link and named main landmark support navigation.

## Skill review

Used pinned UI UX Pro Max commit `7246805992a42b7bba37d4cac83b01416f0c6c44` and
existing MASTER/page rules; no regeneration or new visual direction.

- `keyboard focus modal --domain ux`: adopted visible focus and AA focus-not-
  obscured minimum; the enhanced AAA result was not treated as an AA requirement.
- `badge chip label wraps --domain ux`: adopted recoverable full values and visible
  input labels; allowed long unpredictable labels to wrap without hidden facts.
- `focus restoration dialog --stack react`: adopted proper focus management and
  return focus through one shared component. No additional modal/animation library.

## Validation

- **48 frontend tests passed**: existing auth/preview/expiry/revision/unknown-result
  coverage plus new navigation isolation, Tab/Escape/return focus, mobile filter
  parity, pending dismissal protection, URL/back context and evidence deduplication.
- **896 Python tests passed**; updated paired on-accent contrast test also passed.
  Ruff, frontend format, TypeScript and default production build passed.
- Actual saved-API browser journeys passed for Radar, token workspace, signals,
  reports, outcomes, methodology and owner login at **320, 390, 768, 1024, 1366,
  1920** widths. No whole-page horizontal overflow. Essential visible HTML text
  was measured at ≥12px. First candidate/workspace chart starts inside the initial
  viewport, including **1366×768 and 390×844**.
- Text resizing to 200% passed for every checked page. CDP viewport/device-scale
  emulation checked the layout equivalent of 200% zoom (683 CSS px from a 1366px
  desktop) and 400%/320px reflow. This is explicit scaled-layout evidence, not a
  claim that a headless browser's toolbar zoom control was exercised.
- Mobile navigation/filter isolation, Escape, return focus and route focus handoff
  passed. Reduced motion was enabled for the matrix. Controlled loading, empty,
  partial, stale, offline and refresh-error responses passed; refresh errors retained
  the original rows and timestamps. Normal journeys used actual saved API data.
- Owner browser flows at 1366/390 passed login, diagnostics, exact before/after
  settings confirmation, reset cancellation/return focus, private **mock** Test
  Alert, empty public journal and no overflow/errors. No real messages/providers.
- Rendered text contrast passed measured WCAG thresholds on 14 public/login views
  at 390/1366 (4.5 normal, 3 large). The on-mint action foreground is explicitly
  separate and measures **9.83:1**. The check composes ancestor background colors;
  decorative gradients/graph strokes and assistive-technology behavior still need
  the broader #49 audit. No independent whole-site AA certification is claimed.
- Rebuilt website container passed verified local HTTPS, CSP/same-origin routing,
  Secure cookies, anonymous denial, CSRF, queued commands, stale worker and logout.
  Website image starts no scanner. Production was not deployed or inspected.

Predeclared load profiles inherited unchanged from #51: 100 synthetic tokens,
24 retained scans/2,400 evaluations, eight scans per arm, six clients ×112 reads,
byte-identical starting DB copies and a separate API process over loopback HTTP.
Gate: zero read/scanner/provider-isolation failures and median scanner overhead
≤max(25% baseline,100ms); limits were not changed after measurement.

| Profile | Read pause / mock provider wait | Baseline median | Loaded median | Allowed overhead | Result |
| --- | --- | ---: | ---: | ---: | --- |
| Steady | 250ms / 50ms | 2,654.58ms | 2,700.67ms | 663.65ms | Pass |
| Burst | 50ms / 5ms | 518.40ms | 570.47ms | 129.60ms | Pass |

Both completed **672/672 HTTP 200** reads, zero scan errors/external requests.
These bounded fixtures do not establish VPS/Internet/provider/production capacity
or supersede the production-copy limitation in `docs/website-launch.md`.

## Evidence and reproduction

[Browser matrix](evidence/issue-40/browser.json), [states](evidence/issue-40/states.json),
[contrast](evidence/issue-40/contrast.json), [steady load](evidence/issue-40/steady-load.json),
[burst load](evidence/issue-40/burst-load.json).

Matched before/after screenshots are under `evidence/issue-40/`, named
`before-<page>-<width>.png` / `after-<page>-<width>.png` at 390 and 1366 widths for
Radar, token, signals, reports, outcomes, methodology, authenticated owner and
owner preview. They show isolated time-shifted fixtures, not live market facts.
Before uses the published base; after uses this implementation. Owner passwords
are temporary random fixture values and are not in screenshots or reports.

Reproduce from the project environment: run frontend tests/build, seed a **new**
temporary DB with `scripts/seed_website_demo.py`, and serve the default build with
fixture-only `WEB_ALLOW_SYNTHETIC_DATA=true`. Use Chromium/Playwright at the listed
viewports with external requests blocked. For owner flows, use the tested mock
worker/controller, an ephemeral credential and local HTTP-only cookie override;
never use a production account or send a real Test Alert merely for validation.
Before builds can be made from `git archive 2c7f42f web src` in a temporary directory
without resetting a worktree. Existing deployment/rollback procedures remain.

#42 owns richer saved OHLCV/timeframe/inspection work; #43 owns source-linked
research briefs; #45 owns deeper track-record cohorts. #49 retains full release,
assistive-technology/security and production capacity gates. #52 retains private
host/image/access-policy reconciliation. This issue does not change exposure,
billing, provider rights or production legacy classifications.
