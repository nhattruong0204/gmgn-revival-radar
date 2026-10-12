# Website operation and HTTPS launch

The website is implemented on `feat/public-website`. Local verification is recorded
in [website validation](website-validation.md). The proposed host is VPS `130.94.7.127`; the proposed domain is
`onchain.revival.com`. SSH access and control of the parent domain `revival.com`
have not been confirmed, so there is **no verified deployed URL**.
Issue #16's HTTPS launch acceptance remains open until deployment and smoke checks.

## Local development

Use Python 3.12 and Node 22.12+ (Node 24 also works). From the repository root:

```bash
bash scripts/bootstrap.sh
source .venv/bin/activate
python scripts/seed_website_demo.py /tmp/radar-web-demo.db
cd web
npm ci
npm test
npm run build
cd ..
DATABASE_PATH=/tmp/radar-web-demo.db WEB_STATIC_DIRECTORY="$PWD/web/dist" revival-radar-web
```

This serves the built public pages and `/api/v1` on one local origin. Development
can instead run `npm run dev` from `web`; Vite forwards `/api` to port 8000. The API
does not initialize/migrate storage or start a scanner. A missing database yields
503 until the scanner or offline demo creates it. The demo does not send messages.
The synthetic frontend (`VITE_DEMO_MODE=true npm run build`) is a separate opt-in;
never publish that bundle as real scanner data. Use the default build for production.

For local owner-control development use a test database and fixture/mock worker.
The API requires `WEB_ADMIN_ENABLED=true`, `WEB_ADMIN_PASSWORD_HASH`, and an exact
`WEB_TRUSTED_ORIGIN`. Cookies are Secure by default; `WEB_COOKIE_SECURE=false` is
permitted only for local HTTP development. Generate a password hash interactively
with `python -m revival_radar.web.auth`; do not put plaintext passwords in shell
arguments, logs or source. See the existing authorization/recovery tests.

## Upgrade the existing scanner host

Keep SQLite, WAL and shared-memory files on the same local disk. The web service
and scanner must use the **same absolute database path** and filesystem identity.
Do not run a second scanner or give the website provider/Telegram credentials.

1. Record the existing scanner image/commit and configuration. Run
   `python scripts/backup_database.py` in the existing environment before upgrade.
   It uses SQLite's consistent backup API and verifies integrity. Stop the worker,
   then preserve `data/radar-controls.json`, its permissions and the backup outside
   the checkout. This preserves revisions, polling offset and delivery cooldowns.
2. Install the new checkout at `/opt/gmgn-revival-radar` (adapt the provided paths
   if the existing host uses another location). Run the Python bootstrap and
   `cd web && npm ci && npm test && npm run build`. The existing Docker scanner
   deployment can keep using its image, but it must be rebuilt from this revision
   so scanner startup creates schema 5 and the command worker is present.
3. Set `WEB_CONTROLS_ENABLED=true` in the **scanner's** secure configuration.
   Restart the single existing scanner; it owns the schema-4-to-5 migration and
   publishes heartbeat/settings. Keep all existing provider, chain, delivery and
   report settings. Telegram and website now use the same runtime controller.
4. Create `/etc/revival-radar/web.env` from `deploy/web.env.example`, mode 600,
   replacing database/static paths and trusted origin. Store the generated scrypt
   hash securely and set `WEB_ADMIN_ENABLED=true`. Website login works independently
   of Telegram credentials. Test Alert still requires the scanner's private owner.
5. Install `deploy/revival-radar-web.service`, adapting user/group/paths to the
   existing identity. Ensure that identity can write the database directory for
   sessions/commands and SQLite WAL. Run `systemctl daemon-reload` and
   `systemctl enable --now revival-radar-web`. One Uvicorn process is intentional;
   process-local rate limits multiply if more API workers are introduced.

## HTTPS reverse proxy

Use the host's existing HTTPS proxy where available. `deploy/Caddyfile` is an
optional Caddy configuration for a same-host installation: set `RADAR_DOMAIN` to
the real domain, configure DNS, and expose ports 80/443 for certificate issuance
and HTTPS. Validate it with `caddy validate --config deploy/Caddyfile` before
installing it. The backend listens only on loopback port 8000. Do not expose that
port to the Internet or allow untrusted clients to forge forwarded client IPs.
Uvicorn trusts loopback proxies by default; if the proxy is elsewhere, configure
the exact trusted proxy IPs rather than using a wildcard.

The web password hash is consumed locally, so a proxy-substituted placeholder is
not suitable. On the production host, supply the actual hash through its protected
service environment. Provider and Telegram credentials remain in the scanner.

## Launch smoke checks and monitoring

- Verify HTTPS and certificate validity; test `/`, `/reports`, `/outcomes`, a saved
  token's detail/history and direct reload of `/admin/settings`. Missing assets and
  unknown API routes must stay 404, not return the app shell.
- Compare scores, reports and outcomes with the bot for the same cutoff. Missing
  fields stay unknown and old alerts retain their original evidence. Repeat
  `python scripts/benchmark_website.py` on a staging copy, and extend the workload
  to production data size before launch; the checked-in benchmark is synthetic.
- Verify anonymous `/api/v1/admin/status`, settings and command requests are
  rejected. Sign in over HTTPS, check Secure/HttpOnly/SameSite cookies, then preview
  and confirm a reversible settings change. Check shared Telegram/web revision and
  the next scan's active revision; restore the prior setting through the controller.
- Check Scan Now non-overlap, pause/resume, stale-preview rejection and logout.
  Send a synthetic Test Alert only when explicitly authorized. Never exercise
  message delivery by replaying an unknown command result.
- Inspect the worker heartbeat (available only when enabled and <=30 seconds old),
  scan completions, database size/disk space, rate-limit responses and generic 503s.
  Monitor `journalctl -u revival-radar-web` and the scanner's existing logs. Public
  browsing must add zero GMGN/Helius requests. Stop the worker briefly in staging
  and confirm the owner UI reports it unavailable; restart it once.
- Record the deployed HTTPS URL and results in the release validation document.
  Close #16 only after these live checks and the remaining release criteria pass.

## Backup, restore and rollback

Use SQLite's backup API, never copy an active `.db` alone while omitting its WAL.
The migration test exercises a backed-up schema-4 copy and preserves old signals,
settings revision and Telegram offset. New sessions/previews/commands are additive.
The old scanner refuses schema 5, so rollback requires restoring both the prior
database backup and the corresponding controls file **with both processes stopped**,
then running the previous image/commit. Backups contain private state: keep them
restricted. Restoring an older backup may lose later observations/delivery receipts;
reconcile any messages sent since the backup before re-enabling live delivery to
avoid duplicates. Preserve the failed-upgrade database separately for diagnosis.

Restart the scanner once, then the API. Verify `PRAGMA integrity_check`, schema,
cooldowns and polling offset on the restored copy before resuming live delivery.
Do not downgrade schema by editing its version number. Production backup/restore
and proxy configuration must be exercised on the actual host before public launch.

Owner credential rotation and session revocation: [owner authentication guide](website-owner-auth.md).
