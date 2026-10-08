# Safe production evidence collection

Target explicitly authorized: `root@130.94.7.127`. Normal SSH host-key verification
stays enabled. Verify a first-use fingerprint yourself before accepting it; stop on
any mismatch and do not delete known_hosts entries automatically. Passwords belong
only in your local terminal's SSH prompt. This audit runtime could reach the host
but cannot provide private password entry; no insecure workaround was used.

The standalone exporter needs Python 3 on the VPS host and access to the existing
Docker container. It imports the *installed* application configuration inside the
container, including read-only runtime overrides. No new package or bot deployment
is needed. Automatic container selection succeeds only with one running name
containing `radar`; otherwise pass its exact name using `--container`.

From the local repository terminal:

```bash
scp scripts/export_audit_bundle.py root@130.94.7.127:/tmp/revival-radar-export.py
ssh root@130.94.7.127 'python3 /tmp/revival-radar-export.py --output-dir /tmp --hours 72'
```

Use the exact archive path printed by the second command. To retrieve it:

```bash
mkdir -p data/audit
scp root@130.94.7.127:/tmp/revival-radar-audit-EXACT-PRINTED-NAME.tar.gz data/audit/
```

The placeholder must be replaced with that printed basename; do not collect wildcard
files, `.env`, keys, or the active DB. The exporter makes a consistent SQLite backup
with the read-only source connection and online backup API, checks integrity before
and after transfer, records source WAL/schema/bytes/times, and hashes the audit copy.
It never runs migrations, VACUUM, pruning, API requests, trading or restart commands.
The new audit backup is left in container `/tmp`; later cleanup is a separate owner
operation. Host temp packaging files belong exclusively to the exporter.

The archive contains **only**:

- `production_snapshot.db` (private history, never a public artifact);
- `manifest.json` (snapshot checksum, backup metadata and host-checkout revision);
- `production_config_sanitized.json` (allowlisted effective config, runtime revision,
  pacing/retry settings and installed-module hashes);
- `container_inventory.json` (allowlisted image/state/mount/resource/log fields,
  environment-variable names only, no argument lists);
- `vps_inventory.json` (bounded CPU/memory/disk/uptime/process executable/resource data);
- `production_logs_summary.json` (up to 72h/100k lines/16MB, recognized event fields
  only, with truncation and attribution limitations).

The archive and generated analysis are mode 0600. Known Docker credential values
are checked against every archive input in memory; if one appears, export is
withheld. No raw `docker inspect`, `.env`, arbitrary logs, command-line process
arguments, SSH key or password is retained. New filesystem writes are only private
audit copies/artifacts. A failure prints exception type only, not validation inputs.

Unpack only this generated archive privately. Python 3.12 supports safe extraction:

```bash
python3 - data/audit/revival-radar-audit-EXACT-PRINTED-NAME.tar.gz <<'PY'
import sys, tarfile
from pathlib import Path
output = Path('data/audit/production')
output.mkdir(parents=True, exist_ok=True, mode=0o700)
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(output, filter='data')
PY
python3 scripts/audit_database.py \
  --database data/audit/production/production_snapshot.db \
  --manifest data/audit/production/manifest.json \
  --source production --output-dir data/audit/production-analysis
```

The manifest is mandatory for a production-labelled analysis and its checksum must
match. Default as-of time comes from backup completion, not a future-dated token
observation. Use `--until` and `--since` UTC epochs for exact UI window comparisons.
The CLI uses standard-library Python only and does not read `.env` or modify source
schema/history. All `data/audit/` files are already protected by `.gitignore`.

Bring the private bundle into this workspace to complete production measurements.
Do not upload it to GitHub, a public Site, or commit it. Only reviewed aggregate
findings belong in `docs/audit/`.
