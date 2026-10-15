# V2 deploy runbook (issue #75): NOT EXECUTED

Status: **written, not run.** Nothing in this document was executed against the VPS or
production by the release-gate task. It is modelled on the PR #58 rollout (scanner-owned
migration, matched private backup, one scanner restart, then the website) and must only
be run after the owner approves the deployment explicitly. Read
[V2_RELEASE_GATE.md](V2_RELEASE_GATE.md) first: every open item there (notably the
Radar 7-day read, see "Known issue") must have an owner decision before go.

Placeholders: `<V2_SHA>` is the full 40-character SHA of the reviewed `main` commit to
deploy; `$R` is the release directory `/root/onchainflow-releases/release-<YYYYMMDD>-v2`
(mode 700). The host layout is the existing one: `/opt/gmgn-revival-radar`, compose
files `docker-compose.yml` + `docker-compose.website.yml` + `docker-compose.https.yml`,
services `radar` and `web`, database `data/revival_radar.db`, controls
`data/radar-controls.json`, env files `.env` (scanner) and `.env.web` (website).
Adapt names to what pre-flight actually shows.

## What changes (schema 7 to 9, additive)

| Schema | Added | Owner |
| --- | --- | --- |
| 8 | empty derived `call_performance` table (#90); filled by the scanner after each scan, 500 calls per scan | scanner |
| 9 | empty `replay_candles` and `replay_evaluations` archive tables (#74); filled by the scanner when `REPLAY_RETENTION_ENABLED` (default true) | scanner |

Rehearsal on a copy of the 2026-10-14 production database (1.5 GB, schema 7): migration
2.2 s including `integrity_check`; all 17 pre-existing tables identical in row count and
SHA-256 of ordered rows; second connect a no-op; backfill of 49 calls 0.05 s. The
website serves schema 7, 8 and 9, so the website and scanner can be rolled back
independently only in the "website forward" direction; the schema itself is never
downgraded (see Rollback).

Settings policy (#70): `ALERT_MIN_STAGE=WATCH` and `REALERT_REQUIRES_NEW_EVIDENCE=false`
stay at their defaults, which reproduce current behaviour exactly. Do not edit them,
the thresholds, the cooldown, provider budgets or delivery settings during this
release. Activating the stage gate or evidence-based re-alerts is a separate,
later owner decision with its own review.

## 0. Pre-flight (no service impact)

```bash
cd /opt/gmgn-revival-radar
R=/root/onchainflow-releases/release-<YYYYMMDD>-v2; install -d -m 700 "$R"
git rev-parse HEAD | tee "$R/previous-revision"          # expect a784f67... (production SHA)
test -z "$(git status --porcelain)"                      # clean checkout
docker compose -f docker-compose.yml -f docker-compose.website.yml -f docker-compose.https.yml ps
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | tee "$R/previous-images.txt"
python3 - <<'PY'
import sqlite3
d = sqlite3.connect('file:data/revival_radar.db?mode=ro', uri=True)
print('schema', d.execute('PRAGMA user_version').fetchone()[0])     # must be 7
print('alerts sent', d.execute("SELECT COUNT(*) FROM alerts WHERE delivery_status='sent'").fetchone()[0])
PY
df -h /opt /root; ls -l data/revival_radar.db*; free -m
```

Go criteria: production SHA is `a784f67`; schema is 7; exactly one `radar` container;
free disk at least 3x the database (rehearsal copy + matched backup + restore check +
headroom; the project note is 2 GB RAM, 50 GB disk, about 46% used); `main` at
`<V2_SHA>` has green CI; the owner has approved in writing. Save the pre-release
public figures for later comparison:
`curl -s https://onchainflow.app/api/v1/alerts?page_size=1 | head -c 400`.

## 1. Build before stopping anything

```bash
git fetch origin && git switch --detach <V2_SHA>
docker compose -f docker-compose.yml -f docker-compose.website.yml -f docker-compose.https.yml build radar web \
  > "$R/build.log" 2>&1
docker tag <new radar image> gmgn-revival-radar-radar:v2   # keep previous images by ID from previous-images.txt
```

Building is the long step and happens while production keeps serving the previous
containers. `docker compose build` does not recreate running containers.

## 2. Fresh staging copy on the host and rehearsal

```bash
python3 - "$R/rehearsal.db" <<'PY'
import sqlite3, sys, os
src = sqlite3.connect('file:data/revival_radar.db?mode=ro', uri=True)
src.execute(f"VACUUM INTO '{sys.argv[1]}'")          # consistent copy while the scanner runs
os.chmod(sys.argv[1], 0o600)
PY
```

Rehearse exactly what startup will do, in the new scanner image with no network:

```bash
docker run --rm --network none --user 10001:10001 -v "$R:/release" --entrypoint python \
  gmgn-revival-radar-radar - /release/rehearsal.db <<'PY'
import hashlib, sqlite3, sys, time
from revival_radar.storage.database import connect
path = sys.argv[1]
def snap():
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True); out = {}
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        h = hashlib.sha256(); n = 0
        for r in db.execute(f'SELECT * FROM "{t}" ORDER BY rowid'): h.update(repr(tuple(r)).encode()); n += 1
        out[t] = (n, h.hexdigest())
    db.close(); return out
before = snap(); t = time.time()
db = connect(__import__('pathlib').Path(path))
print('user_version', db.execute('PRAGMA user_version').fetchone()[0], 'integrity', db.execute('PRAGMA integrity_check').fetchone()[0],
      'seconds', round(time.time() - t, 2)); db.close()
after = snap()
assert all(after[t] == before[t] for t in before), 'pre-existing table changed'
print('pre-existing tables identical:', len(before), 'new:', sorted(set(after) - set(before)))
connect(__import__('pathlib').Path(path)).close()      # idempotent
assert snap() == after
PY
```

Then, on the migrated rehearsal copy with the **new web image on loopback**
(`DATABASE_PATH=$R/rehearsal.db`, host 127.0.0.1, a spare port):

1. `python scripts/verify_call_parity.py --db "$R/rehearsal.db" --api http://127.0.0.1:<port>` must print `mismatches=0`.
2. Run the refresh once (`Repository.refresh_call_performance`) on a further copy, then
   `python scripts/verify_call_parity.py --db <that copy> --projection`: `mismatches=0`.
3. `GET /api/v1/track-record/calls?days=30` returns the expected cohort for that
   copy's `as_of` (compare with the pre-release figures from section 0).
4. Optional capacity repeat: `scripts/benchmark_website.py --initial-database "$R/rehearsal.db"
   --separate-api-process --include-calls` steady (`--read-pause-seconds 0.25
   --replay-wait-seconds 0.05`) and burst (defaults): both must pass unchanged gates.

Abort criteria: any assertion above fails, integrity is not `ok`, parity mismatches,
or a read over 1.5 s on a route other than the known issue below. Abort means: remove
`$R/rehearsal.db`, change nothing else, production is untouched.

## 3. Stop services and take the matched backup (downtime starts)

```bash
date -u +%s > "$R/cutover-started"
docker compose -f docker-compose.yml -f docker-compose.website.yml -f docker-compose.https.yml stop radar web
test "$(docker inspect --format '{{.State.Running}}' gmgn-revival-radar-radar-1)" = false
test "$(docker inspect --format '{{.State.Running}}' gmgn-revival-radar-web-1)" = false
python3 scripts/release_backup.py create --database data/revival_radar.db \
  --controls data/radar-controls.json --destination "$R/matched-backup" \
  --revision "$(cat "$R/previous-revision")" --services-stopped
cp .env .env.web "$R/matched-backup/" && chmod 600 "$R/matched-backup/.env" "$R/matched-backup/.env.web"
python3 scripts/release_backup.py stage --bundle "$R/matched-backup" --destination "$R/restore-check" \
  --revision "$(cat "$R/previous-revision")"        # restore check: manifest hashes + integrity
sha256sum .env .env.web "$R/matched-backup/.env" "$R/matched-backup/.env.web"   # pairs must match
```

The matched backup is code revision (`previous-revision`) + database + runtime controls +
the two env files, all private (700/600). Do not proceed unless the stage/restore check
passed and the env hashes match.

## 4. Scanner-owned migration 7 to 9, start the single scanner

```bash
docker run --rm --network none --user 10001:10001 -v "$PWD/data:/app/data" --entrypoint python \
  gmgn-revival-radar-radar - /app/data/revival_radar.db < "$R/migrate-check.py" | tee "$R/production-migration.json"
docker compose -f docker-compose.yml -f docker-compose.website.yml -f docker-compose.https.yml up -d --no-deps --force-recreate radar
```

(`migrate-check.py` is the section 2 snippet saved to a file; it asserts identical
pre-existing tables on the real file. Alternatively skip the explicit run: scanner
startup performs the same migration through `connect`. The explicit run makes the
result auditable and takes about 2 s plus the hashing pass.)

Verify the first completed scan and the backfill (poll until a scan finishes; the
interval is 300 s):

```bash
python3 - <<'PY'
import sqlite3, json, pathlib
cut = int(pathlib.Path('/root/onchainflow-releases/release-<YYYYMMDD>-v2/cutover-started').read_text())
d = sqlite3.connect('file:data/revival_radar.db?mode=ro', uri=True)
r = d.execute('SELECT id,finished,processed,errors FROM scan_runs WHERE finished>? ORDER BY id DESC LIMIT 1', (cut,)).fetchone()
print('schema', d.execute('PRAGMA user_version').fetchone()[0], 'scan', r)
print('call_performance rows', d.execute('SELECT COUNT(*) FROM call_performance').fetchone()[0],
      'sent alerts', d.execute("SELECT COUNT(*) FROM alerts WHERE delivery_status='sent'").fetchone()[0])
assert r and r[3] == 0
PY
test "$(docker ps --filter label=com.docker.compose.service=radar -q | wc -l)" = 1   # exactly one scanner
docker logs --since 15m gmgn-revival-radar-radar-1 2>&1 | grep -i "call performance projection failed\|Traceback" || echo clean
```

Expected: schema 9, one completed scan with 0 errors, `call_performance` rows equal to
sent alerts (at most 500 per scan; 49 in the rehearsal, so one scan), no projection
failure warning. A projection failure never fails a scan; it would leave the website on
the on-read path (still correct). Telegram delivery, cooldowns and the polling offset
come from the preserved controls file; confirm no duplicate alert was sent.

The website can be started as soon as the migration check passes (it serves an empty
projection through the on-read path and schema 7, 8 and 9 alike), shortening the public
outage; the first-scan verification then runs while the site is already up.

## 5. Start the website, HTTPS smoke and parity

```bash
docker compose -f docker-compose.yml -f docker-compose.website.yml -f docker-compose.https.yml up -d --no-deps --force-recreate web
docker ps --format '{{.Names}} {{.Status}}'
cmp .env "$R/matched-backup/.env" && cmp .env.web "$R/matched-backup/.env.web"
python3 scripts/website_health.py --database data/revival_radar.db
python3 scripts/verify_call_parity.py --db "$R/post-release-copy.db" --api https://onchainflow.app
```

`post-release-copy.db` is a fresh `VACUUM INTO` copy taken just before this check (the
live file is not passed to the script). Pass `--as-of` from the API's own `as_of` if the
scanner completes a scan between copy and request, or re-take the copy. Also:

- the section 5 HTTPS smoke from the PR #58 record (public routes 200, `nosniff`, CSP
  `frame-ancestors 'none'`, anonymous admin 401, TLS verified, owner-cookie flags only
  with the temporary smoke session the old `smoke.sh` creates and removes) plus
  `/api/v1/track-record/calls?days=30`, both CSV exports row counts equal to the API,
  Signals, Track Record and a Signal detail page load in a browser;
- the Radar 7-day window issue (below) is expected unchanged; every other route under 1.5 s;
- public browsing adds zero provider requests (scanner logs and provider budget counters
  unchanged).

## Expected downtime

Estimates, not measurements on the VPS. Local timings on this release's copy: backup
API 5.4 s, `VACUUM INTO` 14.9 s, migration 2.2 s, web ready 3 s (all NVMe, no
contention). Budgeting a 5x to 10x slower VPS disk:

| Window | Estimate |
| --- | --- |
| Public website unavailable (stop web to web healthy again) | about 5 to 8 minutes (stop grace up to 30 s, matched backup and restore check 1 to 3 min, migration under 1 min, scanner up, web start 15 s) |
| No scanner running (stop to first scan start) | about 4 to 7 minutes; one scan interval is skipped, observations resume at the next scan, nothing is backfilled for the gap |
| First completed scan and projection verified | up to one scan interval (5 min) after the scanner starts |

The build (section 1) and the staging rehearsal (section 2) add no downtime.

## Rollback (never downgrade the schema in place)

Triggers: migration check fails; integrity not `ok`; first scan has errors; duplicate or
missing Telegram deliveries; parity mismatch on the live API; any public route
regression compared with section 0.

1. Stop `radar` and `web`. Keep the failed-upgrade database for diagnosis:
   `mv data/revival_radar.db "$R/failed-v2.db"` (and its `-wal`/`-shm`).
2. Restore the matched backup, both files from the same bundle:
   `python3 scripts/release_backup.py stage --bundle "$R/matched-backup" --destination <data dir staging>`
   then install `database.db` as `data/revival_radar.db` and `radar-controls.json` as
   `data/radar-controls.json`, mode 600, owner `RADAR_UID`. Restore `.env` and `.env.web`
   from the bundle (`cmp` them).
3. `git switch --detach $(cat "$R/previous-revision")` and recreate `radar` and `web`
   from the previous images (IDs in `previous-images.txt`, or rebuild). Schema 9 is
   rejected by older readers, so old code must only ever see the restored schema-7 file.
4. Verify `PRAGMA user_version` = 7, `integrity_check` = ok, cooldowns and polling offset,
   the first scan, then the smoke checks. Reconcile alerts sent after the backup before
   live delivery resumes (an alert sent during the failed window exists in
   `failed-v2.db` but not in the restored file).

A website-only problem (the scanner is healthy) is rolled back by recreating `web` from
the previous image: the old reader rejects schema 9, so this is **not** available after
migration; fix forward or do the full restore above. The reverse is available: the new
website serves a schema-7 database (verified in the release gate).

## Known issue to decide before go

`GET /api/v1/radar?age_hours=168` (the Radar "Explore saved history" link) exceeds the
1.5 s read deadline on a production-sized copy and returns 503 with the generic
"temporarily unavailable" page. It reproduces on the production code a784f67 with the
unmigrated schema-7 copy, so it is not caused by V2 and the migration does not change it.
The 24 h and 72 h windows are fast. Options for the owner: ship as-is, or hold for a fix
issue. Do not raise the deadline.

## After go-live

Record: deployed SHA, schema 9, migration and first-scan timings, parity output, smoke
result, image IDs, backup bundle location, the real downtime, and anything unexpected.
Keep the matched backup until the next verified daily backup exists. Monitor disk growth:
replay retention is enabled by default (sampled archive capped at 1024 MB).
Close #75 only after the owner confirms the deployed state.
