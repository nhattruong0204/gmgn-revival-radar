# Saved checkpoint cohorts and schema seven

Checkpoint values are **price change**, not market-cap change; see
[performance metrics](performance-metrics.md).

Issue #45 adds `checkpoint-cohort-v1` and scanner-owned schema **7**, on top of
#43's schema six. This is saved research, not executed or realized trading P&L.

## Frozen inclusion and arithmetic

One row is one delivered alert; repeat tokens remain repeated alerts, not unique
assets or independent trades. The inclusive alert-observation interval is
`[as_of - days*86400, as_of]`; supported horizons are 1/6/24/72 hours, each anchored
to the original alert observation rather than Telegram delivery. The first usable
snapshot (price or market cap available) in `[due_at, due_at+3600]`, inclusive,
becomes the checkpoint. Later snapshots never fill or overwrite that first point.
Before the window closes an absent point is pending; after it closes it is missed.
An observed point without usable arithmetic is unavailable or unresolved, not 0%.

A finite return requires a positive original price, nonnegative finite checkpoint
price, valid window and a finite stored scalar matching `(later/original - 1)*100`
within floating-point tolerance. Invalid/mismatched inputs remain unresolved;
the reported scalar is retained for inspection but excluded from aggregates.
Negative and exactly-zero computed changes are included. Distribution bins are
`<-50`, `[-50,0)`, `0`, `(0,50]`, `>50` percent. Every count/rate has its own
explicit sample denominator; samples below 20 have a descriptive warning, not a
confidence or success claim. Missingness is never treated as a price movement.

Compatible cohort IDs hash the saved chain, strategy preset/revision, score
algorithm version, alert provenance and alert capture/delivery knowledge class.
These are alert-time definitions, not current settings, subsequent outcomes or
retrospective peak-ranked reports. API filters independently select these fields,
horizon/window, exact cohort, checkpoint state and explicitly retrospective bucket.
Pooled incompatible cohorts show coverage and descriptive frequencies; rates and
medians are withheld. Selecting one exact cohort exposes its arithmetic summaries.
No statistical independence, confidence interval, forward recommendation, execution,
liquidity/fee/slippage feasibility or verified strategy success is inferred.
Price quote currency and provider identity remain unknown; raw prices are not
labeled USD. Only retained alerts can contribute; absent archives aren't recreated.

`GET /api/v1/track-record` returns definitions, exclusions, source IDs, compatible
cohorts, coverage reconciliation and paginated checkpoint rows. Root confirmed/
legacy-sent counts, fixture exclusions and subsequent filter exclusions explain
differences from a bot summary. Bot/public legacy `/outcomes` use the same validated
checkpoint accounting under the same cutoff/provenance policy. The bot's historical
pooled summary isn't a comparison across compatible strategy cohorts.

`GET /api/v1/track-record/export` uses the same filters and an explicit cutoff to
export all selected rows, including unobserved states, not just the visible page.
UTC boundaries/recording times, methods/filters/exclusions/units/version and source
IDs accompany the data. Metadata is also encoded in a header field, allowing an
empty export to retain definitions with **zero** data rows. Formula-like text is
apostrophe-escaped; numeric negatives and genuine zeros remain numeric.

## Schema seven and historical knowledge

Three append-only tables add facts without altering legacy alerts/checkpoints:

- `research_alert_facts`: first-recorded time, original nonsecret presentation,
  immutable original price, score version and a nullable exact brief association.
- `research_delivery_receipts`: first confirmed delivery time.
- `research_checkpoint_facts`: first-recorded and observed times, original window,
  prices/market cap/scalar and recorded checkpoint origin.

Scanner capture is atomic with existing reservations/checkpoints. UPDATE, DELETE
and REPLACE guards protect these records. Checkpoint calculation reads the frozen
original price. Existing mutable compatibility projections cannot rewrite the new
research evidence. Actual save/delivery/checkpoint recording times constrain causal
cutoffs; backdated observation time cannot make later-saved evidence appear earlier.
Journal detail separates alert observation, capture and confirmed delivery times;
all four horizons show pending/missed/unavailable/unresolved alongside measured data.

No historical rows are backfilled. Legacy availability remains `legacy_unknown`;
its retrospective event-time view cannot establish what was known at a past cutoff.
Use `knowledge=recorded` for the strictly recorded cohort. An exact alert-to-brief
link is captured at reservation only when an available brief's digest matches the
original token, signal and normalized nonsecret strategy context. Older schema-six
briefs lack that digest; no later cache/search guesses a historical association.
The association's original UUID never changes; publication/cutoff/quarantine must
also permit visibility. Unsupported cases offer a separately labeled later archive.

Public reads stay query-only and make no provider requests. Default origin policy
excludes known synthetic/test alerts/checkpoints; unknowns stay labeled. Existing
reviewed alert attestations invalidate outcome caches immediately. Outcome cache
versions track alert/delivery/checkpoint evidence rather than unrelated scanner,
snapshot or brief churn. Cached responses retain their actual displayed cutoff;
CSV binds that cutoff. The existing bounded/coalesced cache and read deadline are
retained. Exact accounting is bounded to 10,000 candidate alerts; oversized queries
fail closed and require narrower windows rather than silently truncating totals.

## Migration and rollback boundary

This task does **not deploy or touch the VPS**. A later reviewed rollout must stop
services for a matched private code/database/runtime-control backup, verify it in
staging, run the scanner-owned additive migration, and pair it with the schema-seven
website reader. Six-to-seven migration creates empty new tables/triggers, leaves
legacy evidence and #43 archives unchanged, and is transactional/idempotent.
Older application readers reject schema seven. Rollback restores the matched
pre-release database/control backup with its matching code; never downgrade or
remove historical receipts in place. Restore/stage must preserve private permissions
and immutable records. No access-policy or provider-budget change is authorized here.
The deployment follow-up adds `state_report_configuration`, a compatible expression
index built by scanner startup, including on existing schema-seven databases. It
indexes the same nullable saved configuration/version fields and preserves malformed
or missing presentations as unknown; no historical state is rewritten. Build and
measure this index on the staged copy before cutover: uniform-configuration reports
must also meet the unchanged read deadline, not just mixed synthetic replay cohorts.
Public readers use connection-local 8 MiB page-cache targets for main and temporary
pages, retaining file-backed temporary storage and the same 1,500 ms read deadline.
These are cache targets, not hard process-memory limits; measure concurrency and
memory on the actual host. No persistent database/owner/provider setting changes.
The companion `evaluations_report_window` index covers the scalar report window.
A request-owned timer interrupts active SQLite work at the configured deadline,
avoiding repeated Python progress callbacks. A final elapsed-time check enforces
the same budget for short SQL/model work or cache waits; the timer is cancelled
and joined before closing its connection. The production read deadline remains
1,500 ms. Over-budget results remain service errors rather than partial data.
Server startup warms the 24-hour/seven-day saved reports before accepting requests,
with at most three attempts per window under that same per-read budget. Missing
storage remains unavailable and is never created/migrated by the website. Record
startup time separately from request latency; do not label pre-startup diagnostics
as serving readiness. Failed or over-budget models are never response-cached.
Measure archive/database/backup growth and sustained production capacity before
rollout; local fixture capacity is not VPS or Internet capacity certification.

## Call journal (#66)

The default Track Record view is one row per **call** (delivered alert), not per
checkpoint. `GET /api/v1/track-record/calls` (and `/calls/export`, CSV) reuses
`PublicQueries._performance`, so each item carries the same `performance` object as
`/api/v1/alerts`; no arithmetic is duplicated. The checkpoint endpoint, its
immutability, causal cutoffs and CSV escaping are unchanged and remain available
behind the "Checkpoint detail" tab (`/outcomes?view=checkpoints`).

- Filters: `days` (1/7/30/365), `chain`, `stage`, `min_score`/`max_score`, `outcome`
  (positive/negative/flat/unknown), `revision` (number or `unknown`),
  `min_age_hours`/`max_age_hours`. Sorts: `newest`, `age` (oldest first), `best`,
  `worst`, `mfe`, `mae`, `score`; unknown values always sort last.
- Summary over the whole filtered cohort (not the page): calls, measurable,
  positive/negative/flat, unknown with reason counts (`no_call_mc`,
  `no_later_observation`), median and mean change since call, five-bin distribution,
  MFE/MAE medians with their own counts, by-stage and by-revision groups, the +24h
  price-change accounting from checkpoints for the same calls, and a small-sample flag
  below 20 measurable calls.
- Pooled revisions are described, not withheld: `mixed_configurations: true` with the
  `revisions` list. Filtering to one revision gives the exact cohort.
- Bounds: at most 2,000 calls per cohort (503 "narrow it" beyond), the existing read
  deadline and response cache. Market-cap change and price change are different
  metrics; the journal uses market-cap change since the call, checkpoints use price.
- Change since call is a saved-snapshot observation, not executed or realized P&L.
