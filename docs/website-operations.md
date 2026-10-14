# Website release operations (#26)

The website is live at **https://onchainflow.app** on the existing bot host. The owner purchased the domain through Cloudflare and confirmed
successful browser sign-in after clearing a cached negative DNS result.
See [the production launch record](website-launch.md) for actual-host smoke,
backup/recovery, parity, freshness and capacity evidence.

## Package and host prerequisites

Use the existing checkout/project and scanner configuration on the same host.
Keep SQLite, WAL and shared-memory files on local disk; no remote database mount.
The default Dockerfile still builds the scanner. The `website` target builds a
separate nonroot image with the hash-locked web dependencies and the locked npm
frontend. It starts only the website API, with no scanner/provider task.

The Compose overlay shares `./data` and the configured UID/GID with the existing
`radar` service, binds the API to loopback, drops capabilities, uses a read-only
root filesystem, and caps JSON logs at 3x10MB. Compose >=2.30 is required for raw
`env_file` support, preserving the dollar signs in the scrypt hash. The website
receives only `.env.web`, never the scanner's `.env`.

Before release record CPU/RAM, free disk/inodes, existing service names, image IDs,
full Git commit SHA, database path, UID/GID and current scan/provider/report settings.
Check there is only one scanner: inspect the existing Compose project and systemd
services; do not introduce a second project or scanner process. Obtain a domain
you control, point A/AAAA records at this host, verify the existing firewall/proxy,
and review public-display permissions for the data being published. The production
domain is `onchainflow.app`.

## Controlled release (default Docker layout)

Adapt paths to the existing installation; do not replace the scanner's `.env`.
Keep secure backup files outside the checkout. Build before stopping services:

```bash
cp deploy/web.compose.env.example .env.web
chmod 600 .env.web
# Edit .env.web privately: real trusted HTTPS origin, password hash and admin enablement.
# Generate the hash interactively: python -m revival_radar.web.auth
# Retain prior scanner settings; enable WEB_CONTROLS_ENABLED in its .env at release.
docker compose -f docker-compose.yml -f docker-compose.website.yml build radar web
```

The example copying step is for a first install only; preserve an existing
`.env.web`. Do not paste a password into chat or command arguments. Compose raw
files contain unquoted literal values. Restrict backup copies of `.env`/`.env.web`
to the same private backup location, since they contain secrets; never commit them.
Record/tag the existing scanner and website image IDs before replacing them.

Stop the existing scanner gracefully, then the API. Verify they stopped before
creating the matching database/controls backup. For a first website deployment,
stop only the existing scanner because `web` does not exist yet.

```bash
docker compose -f docker-compose.yml -f docker-compose.website.yml stop radar web
python scripts/release_backup.py create \
  --database data/revival_radar.db --controls data/radar-controls.json \
  --destination /private/backups/release-before-upgrade \
  --revision FULL_PRE_UPGRADE_GIT_SHA --services-stopped
python scripts/release_backup.py stage \
  --bundle /private/backups/release-before-upgrade \
  --destination /private/staging/restore-check \
  --revision FULL_PRE_UPGRADE_GIT_SHA
```

Destinations must be new directories with existing private parent directories.
The backup uses SQLite's consistent backup API, saves matching controls, a code
revision and SHA-256 checksums, verifies integrity and uses 700/600 permissions.
Staging validates checksums and code revision and preserves the schema without
calling migrations. `--services-stopped` is an operator assertion, not automatic
process detection; stop both services to guarantee a matched point in time.
Missing controls fail explicitly: investigate the existing scanner's actual
controls path rather than inventing an offset/defaults file.

Exercise upgrade/recovery on the staged copy with dry-run settings and mock/replayed
providers first. Never start that copy with production Telegram/provider secrets.
Then start the existing scanner once; it owns migrations. Wait for a completed
scan and fresh worker heartbeat before starting the website:

```bash
docker compose -f docker-compose.yml -f docker-compose.website.yml up -d --no-deps radar
docker compose -f docker-compose.yml -f docker-compose.website.yml up -d --no-deps web
```

Use the existing host HTTPS proxy. `deploy/Caddyfile` forwards the same-origin
frontend/API to loopback port 8000; set the real `RADAR_DOMAIN`, validate the
configuration and certificate/DNS, and expose only the required proxy ports.
Do not use a staging internal CA or disable TLS verification for the public site.
For a native/systemd deployment use [website deployment](website-deployment.md).

If Docker's build network lacks DNS, populate `.wheelhouse` on the host using
`pip download --require-hashes` for the runtime/build/web locks, and populate the
ignored `.npm-cache` with `npm --prefix web ci --cache "$PWD/.npm-cache"`. Build
with `--build-arg PIP_NO_INDEX=1 --build-arg NPM_CONFIG_OFFLINE=true`. Hash and npm
integrity verification stay enabled; do not change locks or disable TLS checks.

## Monitoring and routine backups

- `docker compose ... ps` must show exactly the existing scanner plus the website;
  inspect any host scanner units too. The API healthcheck reads `/api/v1/meta`.
  Unhealthy does not automatically restart Docker; investigate database/schema,
  permissions and the bounded logs before an explicit restart.
- Probe HTTPS pages and public `/api/v1/meta` externally; monitor certificate
  renewal, HTTP 5xx/429, disk/inodes, DB/WAL size and backup age. Successful API
  readiness is separate from data freshness and worker availability.
- Sign in to inspect heartbeat age, completed scans, saved/active revisions,
  provider/data coverage and pending/unknown commands. An unfinished scan cannot
  establish worker liveness. Preserve unknown send results; never auto-resend.
- Scanner and website log rotation is bounded in Compose. For systemd, use the
  service rate limits and the host's journald retention policy; do not silently
  overwrite a shared host logging policy. Monitor `journalctl`/Compose logs.
- The existing database-only `scripts/backup_database.py` can run online for
  routine consistent DB snapshots. It is not a matching code/controls rollback
  bundle. Schedule operator-controlled stopped-service release bundles and
  periodic restore drills; restrict access and retain copies off the VPS.
- Rotate owner credentials using [owner authentication](website-owner-auth.md).
  Updating the configured hash revokes old sessions. Provider/Telegram credentials
  stay in the scanner; no public/admin response exposes their values.

## Rollback drill and production rollback

Staging a bundle exercises verified database/settings restoration. Compare saved
signals, cooldowns, outcomes, Telegram offset and controls revision before running
any scanner. Keep the matching image/code available; do not edit `user_version`
to bypass old-code compatibility checks.

For a real rollback, stop both services. Preserve the entire failed-release data
directory (including WAL/SHM) separately. Restore verified staged database and
controls into a fresh local data directory with the recorded UID/GID and private
permissions. Restore the matching prior scanner configuration and image/code,
then start that one scanner. Restoring an older point loses later observations or
delivery receipts: reconcile messages delivered after the backup before enabling
live delivery. Unknown or pending commands must not be blindly replayed.

An older scanner that rejects schema 5 requires its matching earlier DB backup.
If that release predates the website, keep the website stopped; the current API
correctly rejects the older schema. Restore/probe the prior scanner first and
repeat the website upgrade later. Restoring current-schema backups also preserves
session/command tables; review pending actions before resuming owner controls.

## Staging evidence and repeatable check

Local verification passed both image builds and the existing scanner CLI. The
actual Compose container received an unchanged scrypt hash; configuration checks
confirmed one scanner service and shared data/identity with loopback-only API.
The HTTPS container test used synthetic copied data and an internal staging CA,
with certificate verification enabled. Same-origin pages/API, CSP, secure cookies,
anonymous denial, CSRF, logout, stale-worker status and an explicitly queued owner
command passed. The website started no scanner and created no public test alert.
Matching backup/restore permission, checksum, code-revision and integrity tests
passed, together with all 868 Python regressions. No VPS or live Telegram/provider
operation was performed.

Repeat the container check after building `revival-radar-web:staging`:

```bash
RADAR_STAGE_WEB_IMAGE=revival-radar-web:staging \
  .venv/bin/python scripts/validate_website_container.py
```

It creates only synthetic state, a private temporary environment file and its own
containers/network, then removes them and their anonymous volumes. Caddy image
`caddy:2-alpine` must be available or pullable. Port 18443 must be free. If the daemon
injects an outbound HTTP proxy, the script exempts only its local backend IP from
that proxy. This is internal staging routing, not a production network change.
CI also builds and runs this HTTPS check.

## Release acceptance for subsequent deployments

Record the purchased domain, deployed HTTPS URL, approved data-display rights,
resource checks and release/image references in #26/#16. At the real URL verify
certificate validity, same-origin pages/API, Secure/HttpOnly/SameSite cookies,
anonymous denial, owner preview/confirm/logout and truthful freshness/outage state.
Compare reports/outcomes with the bot and verify exactly one scanner, unchanged
chains/provider budgets, Telegram delivery and independent market reports. Repeat
the [load check](website-release-validation.md) on a protected production-sized
copy. Test actual-host recovery and record results before closing #26 or #16.

## Call parity check (#64)

`scripts/verify_call_parity.py` is a read-only operator check that every published call
shows the same numbers in the saved data and in the public API. It opens the SQLite file
with `mode=ro`, recomputes each delivered call's market-cap change, latest observation,
freshness, MFE/MAE and per-token call index in plain SQL/Python (it does not import the
website query code), and optionally compares the result with `GET /api/v1/alerts`.

```bash
# Always use a consistent copy (for example from release_backup.py), never the live file.
python scripts/verify_call_parity.py --db /path/to/copy.db
python scripts/verify_call_parity.py --db /path/to/copy.db --api https://onchainflow.app
```

With `--api` it pages through the alerts list with GET requests, takes the API's `as_of`
and computes with that same cutoff (`--as-of` overrides it). It prints one row per call
and a `mismatches=N` line. Exit status: `0` all match, `1` a mismatch or a call present
on one side only, `2` the check could not run. Unknown values must match as unknown (never
zero). Use a copy taken at about the same time as the API read; snapshots saved between
the two legitimately differ.
