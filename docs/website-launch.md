# OnchainFlow launch — 12 October 2026

The public website is live at **https://onchainflow.app**. Private owner controls
are at **https://onchainflow.app/admin**. The owner confirmed successful browser
sign-in with a password configured privately on the host. A cached NXDOMAIN
response cleared after DNS propagation/cache flushing.

## Verified behavior

- The real HTTPS certificate and hostname passed external validation.
- Public pages, Radar, alerts, outcomes and 24-hour/seven-day reports returned 200.
- Anonymous admin reads were denied. CSRF rejection, durable preview/confirmation
  and logout passed using a short-lived test session that was removed afterward.
- Confirmation resaved the existing threshold and preserved effective settings.
- Public Radar, report rankings/evidence and outcomes matched the bot's saved
  production data at a fixed historical cutoff.
- One scanner remains active, with its prior provider pacing and independent
  reports enabled. A fresh completed scan had zero errors; the API and control
  worker were healthy. No synthetic Telegram message was sent.
- Matched private backup/restoration and copied scanner-owned migration passed
  integrity checks and preserved controls. Previous images remain available.
- Daily database backups and five-minute HTTPS/data/worker checks passed their
  first actual-host runs. Failures appear in systemd status/journal.

Detailed host identities, private backup locations, receipts and copied-data
measurements are retained privately with the operator's release record.

## Capacity and implementation

Production-size copied history exposed slow queries. The release adds covering
freshness/token indexes, indexed latest-observation reads, completed-scan cache
versions, narrower report rows and an early exit after two report configurations.
Historical cutoffs and ranking semantics remain covered by regression tests.

The fast burst replay did **not** meet the scanner-overhead gate. A paced launch
profile using the production discovery size, separate API process, slower mocked
provider waits and paced reads passed the unchanged overhead gate with no read or
scanner failures. This result does not establish burst capacity. The benchmark
supports these profiles explicitly and never uses live provider credentials.

The website has bounded queries, caching and per-client rate limits. Broader
traffic requires additional capacity testing. The 870-test Python suite, Ruff
and formatting passed, followed by an additional completed-scan cache regression.
Prior frontend/browser/container checks are documented in
[release validation](website-release-validation.md).

See [operations](website-operations.md) for deployment, password rotation,
backups, monitoring and rollback. For password rotation, run
`scripts/configure_web_owner.py` privately and recreate only the website service.
Password input is hidden; only its salted hash is saved atomically with mode 600.
