# Saved research brief contract

Issue #43 adds deterministic `brief-v1` records to scanner-owned schema **6**.
`ResearchBrief`/`BriefSource`/`BriefClaim`/`BriefScenario` are typed, finite,
allowlisted contracts in `models/brief.py`. The evaluation ID is the public saved
observation ID; scan ID and its start timestamp distinguish rotating storage
identities, while a UUID identifies the permanent brief and copied candle source.
Strategy preset/revision and score algorithm version remain unknown when absent.
Configuration credentials and arbitrary saved presentation JSON are not exposed.

The scanner saves one first-write version per scan/evaluation/start-time within
the evaluation transaction. Retry cannot rewrite it. The brief copies its source
fields, blockers, missing fields, warnings and at most 25 closed hourly bars from
the matching saved cache. No provider fetch or LLM is added. Observed time,
archive save time, cache fetch/expiry and publication time are separate. A separate
append-only publication receipt exposes it only after scan completion, at the
maximum of completion wall time, reported completion and archive save time.
Incomplete scans have no public version. Historical alert payloads are untouched;
the journal's explicitly separate archive can contain later evaluations.

Price range observations require 25 consecutive closed bars, a fresh cache and
live candle provenance. The preceding 24 bars define descriptive low/high levels;
the last closed bar supplies the comparison. Hourly relative USD volume uses that
same preceding 24-bar mean. Missing volume, duplicate/gapped/open/future bars,
stale data and zero/non-finite baselines remain unknown. Unknown evaluation origin,
strategy revision, required fields or blockers makes the case watch. Known facts
with incomplete evaluation provenance are not promoted into positive evidence.
Supporting/opposing observations are facts, not calibrated forecasts. Provider
identity and price quote currency were never retained and are not inferred.

`GET /api/v1/tokens/{chain}/{address}/briefs` reads only the archive, paginates with
stable IDs (page size 1–100), and accepts the existing finite causal `as_of` cutoff.
Evaluation time, actual save time and publication receipt must all precede that
cutoff. Backdated/newly discovered evidence cannot enter an earlier replay.
Default reads exclude synthetic/test origins; the existing explicit synthetic
preview setting admits synthetic versions and retains its warning banner, while
test records stay excluded. Unknown origin stays explicitly unknown. Browsers
never write, migrate or trigger a provider call. Reads have the existing deadline,
rate limits and same-origin boundaries. Versions survive rotating cache,
evaluation and scan pruning. UPDATE, DELETE and REPLACE are guarded on scanner
connections; first-write publication is idempotent. No history is backfilled from
mutable legacy caches. Tests exercise round trips, causal replay and replacement.

## Deployment boundary

This issue **does not deploy to the VPS**. A later reviewed release must back up
matched code/database/runtime controls, run the scanner-owned additive migration
and use the matching schema-six website reader. Old readers deliberately reject a
newer schema; rollback must restore the matched backup rather than downgrade the
live database. The release backup helper now explicitly closes SQLite connections
so private backup WAL/SHM sidecars are not left waiting for garbage collection.

Archive rows are retained indefinitely and copied candle windows add disk/backup
cost per evaluation. Measure archive growth and plan capacity/archival before
production rollout; do not silently prune accepted historical versions. No visitor
account, paid entitlement, Telegram delivery or exposure policy changes here.
