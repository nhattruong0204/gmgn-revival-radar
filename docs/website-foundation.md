# Website foundation (#17)

The foundation builds on merged PR #27. This milestone defines public/admin
contracts and makes all planned design states reviewable without VPS access,
provider credentials, production data or a running scanner. HTTPS launch belongs
to the separate #16/#26 release work.

## Separate startup and reproducible builds

Use Python 3.12 and Node 22.12+ (validated with Node 24). From the checkout root:

```bash
bash scripts/bootstrap.sh
source .venv/bin/activate
revival-radar-web --host 127.0.0.1 --port 8000
```

The API factory and startup do not create/migrate storage or start scanner/provider
clients. With an absent database, metadata remains available but Radar returns a
safe 503. For an HTTP 200 data smoke check, create a NEW synthetic database with
`python scripts/seed_website_demo.py /tmp/new-radar-foundation.db`, then restart
only the API with `DATABASE_PATH=/tmp/new-radar-foundation.db`. `GET /api/v1/radar`
returns the synthetic REVIVE score 90. The seed explicitly initializes test data;
that operation is separate from API startup and never modifies production storage.

In another terminal:

```bash
cd web
npm ci
npm run typecheck
npm test
npm run build
npm run dev -- --host 127.0.0.1 --port 5173
```

Normal development forwards `/api` to the separate API on port 8000. `npm ci`
uses the committed lockfile. For a completely offline design review, stop the
normal Vite server and run this instead; no API or SQLite database is necessary:

```bash
VITE_DEMO_MODE=true npm run dev -- --host 127.0.0.1 --port 5173
```

Every page clearly labels synthetic data. Owner controls are disabled in this mode.
The default production build uses real API requests; demo mode is opt-in and must
not be published as real market observations. Preview controls exist only in demo
mode and cannot mutate scanner settings or provider/delivery behavior.

## Review the six design states

The demo banner's **Synthetic preview state** selector updates the URL's
`preview_state` parameter. Open Radar or any demo token, then choose a state.
A token preview path is `/tokens/sol/Demo11111111111111111111111111111111111111`.
For example append `?preview_state=partial` to that path.

| State | Expected display |
| --- | --- |
| `ready` | Sample candidates and saved evidence, text stage labels and observation ages |
| `loading` | Accessible loading status for 1.5 seconds before data appears |
| `empty` | No Radar candidates / no saved token evaluation; no fabricated zero values |
| `partial` | Known active mint authority blocks eligibility, liquidity and confirmation unknown, missing freeze/candle coverage; charts never refresh providers |
| `stale` | Observations and completed-scan times two hours older, independently from current response cutoff/page refresh |
| `error` | Explicit synthetic service failure with retry controls; no fallback to live data |

All states support desktop tables and mobile cards, token risk/detail panels and
navy/green branding using the existing radar image. Stage and risk meaning appears
in text as well as color. Unknown values remain unknown. Selecting a new state
remounts the page's queries so token detail/history changes alongside Radar.
Fixtures are cloned before transformation so changing states cannot poison later
ready previews.

## Contracts and architectural boundaries

[API contract](website-api.md) defines pagination, errors, identities, observation
cutoffs, chart availability and owner session/command results. Its tracked
[public](examples/website-public.json) and [owner](examples/website-admin.json)
examples are synthetic and checked against the corresponding Python models.
Public JSON never contains credentials, raw configuration, Telegram/owner IDs or
command records. Private session/commands require server-side authentication and
origin/CSRF checks; submission and confirmed execution are distinct.

The [agreed plan](website-plan.md) documents per-request SQLite connection
ownership, scanner-owned migrations, API startup isolation, same-host WAL storage,
shared command execution and scope boundaries. Public browsing reads stored
observations and does not call GMGN/Helius or create another scanner. The remaining
release requirements are in [deployment](website-deployment.md).

## Verification on 12 October 2026

- 11 frontend tests passed, including four deterministic preview-state contract
  checks and the prior rendering/refresh regression checks. TypeScript and the
  default production build passed.
- 27 relevant Python contract/public/static tests passed. The new example check
  parses the public response and both owner command examples using actual models.
- Chromium exercised Radar and token details in all six states at 1440px and
  390px: **24 scenarios passed**, no JavaScript exceptions, no horizontal overflow
  and zero provider requests. Loading status appeared before its delayed response.
- Separate API startup against an absent path returned 503 and created neither
  the database nor its parent directory. A second API process against a synthetic
  database returned HTTP 200 and REVIVE score 90; database bytes were unchanged.

This satisfies #17's local foundation criteria. It does not claim deployment,
production data/load validation, or live Telegram delivery.
