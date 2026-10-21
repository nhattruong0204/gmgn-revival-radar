# Saved observation freshness and provenance

The public API reads saved SQLite evidence only. A GET never starts a scanner,
fetches a provider, classifies records, migrates storage or writes persistent data.
SQLite opens with `mode=ro`, then connection-local read projections and
`query_only=ON`. Projections hide **explicitly recorded** synthetic/test origins;
all originals and delivery/checkpoint history remain intact for the owner.

## Separate clocks and coverage

| Clock | API evidence | Meaning |
| --- | --- | --- |
| Worker heartbeat | `/meta.worker_heartbeat_at` | Last published worker liveness; expires after 30 seconds, not data freshness |
| Completed scan | `last_scan_at` | Completion of a saved scan at/before cutoff, not every row's observation time |
| Market observation | token/snapshot `timestamp` | Time attached to the saved market facts; later market data cannot rewrite evaluation/alert facts |
| Evaluation | observation `timestamp` | Saved evaluation's market-evidence time; no separate evaluation wall clock was retained historically |
| Closed candle | candle `timestamp`, `candles_fetched_at`, `candles_expires_at` | Bar start + 1h must be at/before cutoff; fetch/expiry are separate |
| Outcome checkpoint | `due_at` / `observed_at` | Scheduled horizon and actual observation, independent of signal time |

Radar defaults to `age_hours=24`; supported windows are 1, 4, 24, 72 and 168 hours.
Its lower bound is exclusive, upper cutoff inclusive. Count, sorting and pagination
apply to the same latest completed evaluation within that window. Explicit
`as_of` is bounded by now; age/filter/cutoff are cache keys. An old row never
becomes recent merely because the worker is alive or the browser refreshed.
3/7-day windows are labeled History; details, journals and reports retain their
own explicit windows and timestamps. Historical evidence is still accessible.

`/meta.chains` remains the backward-compatible **historical** list of chains with
completed saved evaluations in the last 7 days. `/meta.active_chains` is separate:
it comes only from a fresh worker's `collection.active_chains`, captured in the
service schedule at the actual scan configuration boundary. Requested settings
or pending revisions cannot change that list before they apply. `enabled` in old
worker snapshots means owner commands enabled, not collection enabled; it is not
used as a collection claim. Legacy workers without the new applied-chain contract
report unknown, even if old settings/history mention chains.

`collection_status` is current, stale, disabled (fresh applied empty list) or
unknown (absent/malformed contract). Stale/stopped/future heartbeats give no active
chains. Only allowlisted chain identifiers, status and bounded heartbeat escape
this endpoint; no settings, private IDs, provider credentials or owner revisions.
Metadata is not cached so heartbeat expiry is visible. Historical as-of requests
cannot reconstruct past active worker settings; current/future worker timestamps
do not become past collection proof. Unknown/stale is intentional.

## Recorded origins and confidence

Origins: `live`, `synthetic`, `test`, `unknown`. `HybridDataSource` explicitly
records live-source origin; `DemoSource`/offline demo records synthetic. Third-party
adapters without a valid explicit classification remain unknown. Dry-run mode
mutes delivery; it is **not** evidence of synthetic market data. Symbols, guessed
address names, date or score cannot establish provenance.

Each new scan/snapshot/evaluation/alert records its own origin in additive private
`state` keys in the same existing transaction. Duplicate snapshots do not retag
old evidence. No schema migration or historical backfill is required. Replaced
enrichment caches carry the replacement adapter's classification. History exposes
origins on snapshots/scores and `candles_origin`; missing legacy cache provenance
remains unknown. Known synthetic candles are excluded too.

Public observations expose `origin` and `origin_confidence`: recorded for an
explicit classification, unknown otherwise. “Recorded” means a source declaration
or operator attestation exists, **not** independent verification of market facts,
complete provider coverage or predictive confidence. Saved `discovery_source`
identifies discovery channels separately. Labels never imply asset safety.

Exclusion is record-specific, not token-wide. A synthetic scan excludes its
joined evaluations; a synthetic alert excludes its outcomes. Classifying only
one evaluation does not guess the origin of unrelated snapshots/alerts/caches.
Review and attest related record IDs individually. Unknown legacy rows remain
visible with an unclassified label pending actual evidence.

## Reversible operator audit

No production database was accessed or classified by this change. Before any
production audit, take the existing verified backup, inspect original payloads,
scan/source context, fixture captures and deployment records with owner access.
Keep a private evidence manifest and record why each exact record is classifiable.
If evidence is absent or conflicting, retain unknown. The tool validates scope and
records an attestation; it cannot prove that an operator's claim is true.

A private JSON file for **one** reviewed record:

```json
{"kind":"evaluation","id":123,"origin":"synthetic","evidence":"Reference to retained fixture capture and matching scan record"}
```

From the project environment, with owner filesystem authorization:

```bash
python scripts/classify_observation.py /absolute/path/radar.db /private/path/evidence.json
```

Kinds: scan, snapshot, evaluation, alert. The existing database must exist
(`mode=rw`, no implicit creation/migrations). Validated positive ID, origin and
nonempty evidence are required; missing records/invalid manifests make no writes.
One atomic transaction updates only the origin state and appends a private audit
entry with old/new classification, time, evidence, manifest SHA-256 and original-record SHA-256. Original
market/evaluation/delivery/outcome tables are untouched by classification.
Existing scanner diagnostic/cache retention stays in force: attached origin
markers expire with their records to prevent ID reuse from inheriting an origin;
operator audit entries and original-record fingerprints remain. Public responses never
include the manifest or audit text. Changing classification invalidates caches,
including reversal. To reverse, submit another reviewed manifest for that ID with
its prior origin (unknown if originally unclassified); retain both audit entries.

From schema 12 the cache invalidation signal is the one-row `origin_version` table,
maintained by SQLite triggers on `state` for every insert, update or delete of an
`origin:*` or `provenance:audit:*` key (this covers the scanner, pruning and the
attestation tooling). The website reads that row instead of scanning those keys, and on
schemas 7-11 (no table) falls back to the former count/max query. The counters only
increase, so any origin or audit change still invalidates cached responses.
For legacy cache quarantine, capture/back up the exact entry and use a separately
reviewed owner operation; do not infer cache origin from a token or delete history.

This is a preservation/exclusion policy, not automatic heuristic cleanup. Known
new fixtures are classified at ingestion. Remaining suspicious legacy production
records require real operator evidence, which cloud access has not supplied.

## Fixture safety

Production `WEB_ALLOW_SYNTHETIC_DATA=false` is the default and explicit in the
example configuration. Never seed or point the production service at a fixture
DB. `seed_website_demo.py` refuses an existing path, dry-runs, blocks external HTTP
and tags originals synthetic. Only isolated local/browser/container/load fixtures
opt in with `WEB_ALLOW_SYNTHETIC_DATA=true`; metadata then says synthetic and the
public shell has a persistent fixture warning. The benchmark runs the same indexed
read projections with a reserved excluded-origin predicate in fixture mode.
Frontend `VITE_DEMO_MODE` remains a separate opt-in demo and is not a release build.

Validation: `tests/test_web_provenance.py`, existing read-only/provider-isolation
and cutoff tests, browser state checks and matched scanner/read benchmark. See
`design-system/onchainflow/verification.md` for recorded results and limitations.
