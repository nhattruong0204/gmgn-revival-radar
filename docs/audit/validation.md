# Validation, deployment and rollback

Run date: 2026-10-08. Runtime release: `5b0f6df4a4d7746e3ee6eb98f2d35312c39c3069`.
Corrected offline audit tooling: `ae42599`.

| Check | Result |
|---|---|
| Local `ruff check .` / `ruff format --check .` | PASS |
| Local complete suite after audit-tool corrections | 477 passed in 8.28s; 31 audit cases |
| VPS-built production image complete suite | 475 passed in 11.95s, before offline-only audit corrections |
| Final audit source suite in isolated VPS container | 477 passed in 11.96s at `ae42599`; no production data/env mounted |
| VPS image build | PASS, 11.34s; hash-pinned dependencies; no host package install |
| VPS isolated offline image demo | PASS; network disabled; no production data/env mounted |
| Actual VPS Compose config | PASS |
| Installed source provenance | Ten audited runtime modules match the tested release |
| Effective runtime configuration | Identical to pre-deployment allowlisted snapshot |
| Protected files | `.env` and `data/radar-controls.json` hashes unchanged |
| DB integrity / schema | All three consistent backups `ok`; schema 4 throughout |
| Historical continuity | Every pre-restart scan/evaluation/alert ID row remains; snapshot count increased |
| First three live scans | 61.95 / 61.89 / 63.40s, zero errors, approximately 300s start spacing |
| Runtime state | Running; zero restarts; no OOM flag; no warning/error-level lines at live verification |
| VPS quantitative audit and independent local execution | PASS; all substantive results equal at the same final snapshot/cutoff |
| Surviving old log completion records vs DB | 268 matches, zero counter discrepancies |
| Private artifact checks | `.gitignore` protection; owner-only backup/archive/JSON; raw credentials excluded |
| Git/format validation | `git diff --check` and formatting passed |

The two additional offline tests cover entry-relative excursion bounds/path-edge
gaps and later high-score band entry without repeated-token overcounting. Existing
regressions cover live-WAL backup, no implicit migration, cutoff leakage, cohort
fingerprints, conditional denominators, censoring, safe export, interruption telemetry
and specific short-base blockers. Additional log regressions ensure `errors=0` is a
counter, normal alert cooldown is separate from GMGN cooldown, and real failure
lines remain classifiable. Synthetic tests do not establish empirical trading edge.

## Deployment performed

Authenticated to the saved, authorized VPS with normal known-host verification.
Collected a consistent database backup and bounded sanitized inventory/logs before
mutation. Verified a clean production checkout and its installed source hashes.
Retained the old image, transferred a source-only Git bundle, checked out
`deploy/audit-5b0f6df`, and built on the VPS while the old container remained running.
Ran the isolated image demo and all 475 image tests before restarting.

Waited for the current scan to finish, then ran
`docker compose up -d --no-deps --no-build radar`. The new container started at
`2026-10-08T14:33:29.900438933Z`, with image
`sha256:72fdb27aa088eb27fe821080b11a4d61a643ff86f442358c6a891d44d8f121b9`.
Verified source, configuration and successive real scan writes afterward.
No service-down, reboot, Docker restart, pruning, live secret/strategy edit,
additional chain activation, manual Telegram test message or trade was performed.
The existing alert/control process resumed with its original production settings.

The later offline audit corrections were run from a private `/tmp` source archive
on the VPS; they do not alter the running scanner/scoring/service modules. The host
production checkout remains the runtime release, and local report/tool commits are
separate. No GitHub push or public artifact upload occurred.

## Prepared rollback

Old image ID:
`sha256:eddeefdbcfdc2cb39ed0df1eb92e84f21df5fb4ab017d6290fbc2d911461f9d2`.
Retained tag: `gmgn-revival-radar:rollback-dd14651-20261008`.
Old checkout: `main` at `dd146511c32c02227514660c51b42afe4c2d2520`.
Private deployment record on VPS:
`/opt/gmgn-revival-radar/data/audit-deployment-20261008.json`.
No rollback was necessary or performed.

If an owner later chooses to roll back this code release:

```bash
cd /opt/gmgn-revival-radar
git switch main
docker tag gmgn-revival-radar:rollback-dd14651-20261008 gmgn-revival-radar-radar
docker compose up -d --no-deps --no-build radar
```

These commands keep the existing bind-mounted database and configuration. Do not
restore an old database backup for a code rollback: that would discard newer history.
This release required no schema change. Backups remain private audit evidence.
