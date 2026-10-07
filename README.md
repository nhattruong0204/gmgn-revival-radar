# GMGN Revival Radar

A small Python 3.12 scanner that watches **GMGN Hot Searches and Trending**, stores
SQLite snapshots, and alerts on previously dumped tokens showing a base and returning
volume/transactions. No frontend, broker, wallet, or trading system.

**This tool produces heuristic market alerts. It does not guarantee profit.
It does not execute trades.** A high score is a rule match, not a calibrated probability.

## Quick start

```bash
git clone https://github.com/nhattruong0204/gmgn-revival-radar.git
cd gmgn-revival-radar
bash scripts/bootstrap.sh
source .venv/bin/activate
revival-radar demo
cp -n .env.example .env
chmod 600 .env
```

The demo needs no credentials or Internet. It runs three synthetic scenarios through
the real analysis, scanner, SQLite, and alert formatting code. Expected results:
REVIVE **90**, DEAD **0**, PUMPED **39**; one potential alert, **zero messages sent**.
Demo always forces dry-run and uses `data/demo.db`, separate from the live database.
Fixture addresses are illustrative; the synthetic numbers are not real market claims.

Fill in `.env` securely, then:

```bash
revival-radar scan-once  # live data; dry-run defaults to true
revival-radar run        # continuously scan, default interval 300 seconds
# Equivalent continuous entry point:
python -m revival_radar.main
```

Keep one scanner per database. A local advisory lock prevents duplicate workers.
`scan-once` exits 1 on partial scan/delivery failures and 2 on configuration errors.
Continuous mode isolates failures and tries again next cycle. Ctrl-C stops it cleanly.

## GMGN setup and verified interfaces

Create your own **read-only** API key at <https://gmgn.ai/ai> and set `GMGN_API_KEY`.
If GMGN's registration form requests an Ed25519 public key, follow its registration
instructions. This scanner never reads a signing key, wallet key, or wallet secret.
The official published demo key was used only for development smoke checks; it is
not embedded in the application or a substitute for your own production credentials.

The async `httpx` adapter calls the official Agent OpenAPI host
`https://openapi.gmgn.ai` using `X-APIKEY`, Unix-second `timestamp`, and a fresh UUID
`client_id` on every attempt. It implements exactly these read-only operations:

| Purpose | Method and path | Official CLI equivalent |
|---|---|---|
| Hot Searches | `POST /v1/market/hot_searches` | `gmgn-cli market hot-searches` |
| Trending | `GET /v1/market/rank` | `gmgn-cli market trending` |
| Market/holder/activity data | `GET /v1/token/info` | `gmgn-cli token info` |
| Security/concentration | `GET /v1/token/security` | `gmgn-cli token security` |
| Hourly OHLC candles | `GET /v1/market/token_kline` | `gmgn-cli market kline` |

No undocumented browser routes or scraping. See [GMGN research and field mappings](docs/gmgn.md)
for the official source revision, exact units, observed API differences, and coverage.

| Chain setting | Network | Adapter and live discovery checks |
|---|---|---|
| `sol` | Solana | Supported |
| `bsc` | BNB Smart Chain | Supported |
| `base` | Base | Supported |
| `robinhood` | Robinhood Chain | Supported; metric availability varies |
| `arc` | Arc | Supported; metric availability varies |

Start with **`ENABLED_CHAINS=sol`**, the runtime and example default. Add other chains
only after reviewing API quota and measured scan duration. `ENABLED_CHAINS=sol,bsc`
opts into two chains. Discovery windows are
`1m,5m,1h,6h,24h` via `DISCOVERY_INTERVAL`; token info supplies those metric windows
when available. Base analysis deliberately uses **closed 1h candles**, up to seven days.
GMGN also documents finer candle resolutions, but those are outside this small V1.

## Telegram setup

The owner-only **/menu** opens Status, Strategy, Health, Settings, Near Misses,
Scan Now, Pause/Resume Alerts, and Test Alert. **Settings** groups Chains, Thresholds,
Structure, Activity, Alerts, and Reset, with Advanced configuration, discovery timing,
and exclusions also available. Current enabled chains appear above the buttons;
opening a menu never changes them.

Choose **Settings → Advanced configuration** for all preset settings and the scan
interval across three short pages. Tap a value, send its replacement (for example
`15k`, `60%` or `150s`), and confirm to save it without editing code or restarting.
Navigation updates the current menu, while confirmations remain separate messages.

Alerts lead with a compact token/chain, score/stage, market, trigger and structure
summary. **GMGN**, **Explorer**, **Why this alert**, and **Full details** buttons keep
secondary information out of the first view. Historical detail uses the saved alert
snapshot and preset/revision, never newer metrics. Setup/trigger/confirmation sub-scores
are each shown out of 100 for new signals. Legacy observations keep unknown dimensions;
the overall score combines dimension weights before risk deductions.

**/health** opens a compact overview; timing, discovery/delivery, coverage, configuration,
near misses, outcomes, and full diagnostics have separate pages. Performance includes
endpoint attempts, operation times and security/candle cache hit rates. Funnel shows
per-chain discovery, prefilter, market, baseline, activity, base, eligibility and delivery
counts. Near misses show all three scores, blockers and missing data, with GMGN and
saved-inspection buttons. Data Quality reports availability of Top10, Dev, Sniper,
Bundler and Insider fields instead of repeating missing insider notes in every alert. Scan Now requests the next scan
without overlapping a running scan. Test Alert sends an explicitly synthetic example
to the owner chat without any GMGN requests, including when live alerts are suppressed.
Both actions require confirmation. See [Telegram controls and the VPS upgrade guide](docs/telegram-controls.md)
for details, profile setup and the original [square bot icon](assets/revival-radar.png).

1. Create a bot through Telegram **@BotFather**; put its token in `TELEGRAM_BOT_TOKEN`.
2. Add it to your channel as an administrator with permission to post.
3. Set `TELEGRAM_CHAT_ID` to the channel's `@username` or numeric `-100…` ID.
4. Set `DRY_RUN=false` only when you want real messages.
5. Run `revival-radar test-telegram` (or `python scripts/telegram_test.py`).

Never paste credentials into issues, commits, or logs. `.env`, databases, logs, and
key files are ignored. In a cloud environment use secure environment settings.
Telegram HTTP logs are suppressed because its token is part of the request URL.
Alerts use escaped HTML and disable link previews. A failed send never terminates a scan.

Example (abbreviated):

```text
♻️ REVIVAL RADAR
$XYZ · Solana
84/100 · Strong revival

💰 Cap $820K · ATH −86.8%
💧 Liquidity $126K · Holders 1,842

⚡ Trigger
Vol 5m $47K · +124% vs baseline
TX 5m 318 · +92%
Trending #14

📊 Base 91h · confirmed
Higher low ✓ · Higher high ✓
Breakout — · Retest —

<copyable contract address>
Preset: Balanced · r3
Heuristic signal · no trades executed.

[📈 GMGN] [🔎 Explorer]
[🧠 Why this alert]
[📊 Full details]
```

## Configuration

All thresholds and weights live in typed [Settings](src/revival_radar/config.py).
Environment variables override `.env`. Invalid ranges fail at startup without dumping inputs.

| Environment variable | Default | Meaning |
|---|---:|---|
| `GMGN_API_KEY` | empty | Required for live market requests |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Required for live delivery |
| `ENABLED_CHAINS` | `sol` | Comma-separated chain IDs; start with Solana only |
| `SCAN_INTERVAL_SECONDS` | 300 | Target cadence; cycles never overlap |
| `DATABASE_PATH` | `data/revival_radar.db` | Persistent SQLite database |
| `TOKEN_MIN_AGE_HOURS` | 48 | Minimum token age |
| `MIN_MARKET_CAP`, `MAX_MARKET_CAP` | 100000 / 10000000 | USD market-cap bounds |
| `MIN_LIQUIDITY`, `MIN_HOLDERS` | 30000 / 300 | Survival thresholds |
| `MIN_ATH_DRAWDOWN`, `MAX_ATH_DRAWDOWN` | 0.65 / 0.95 | Fractions, not percentages |
| `MIN_VOLUME_1H` | 50000 | USD hourly volume |
| `MAX_PRICE_CHANGE_5M`, `MAX_PRICE_CHANGE_1H` | 30 / 75 | Percentage points |
| `ALERT_SCORE_THRESHOLD` | 75 | Minimum alert score |
| `ALERT_COOLDOWN_HOURS`, `REALERT_SCORE_INCREASE` | 6 / 10 | Cooldown / material increase |
| `DRY_RUN`, `LOG_LEVEL` | true / INFO | Delivery switch and logging |
| `DISCOVERY_INTERVAL`, `DISCOVERY_LIMIT` | 1h / 20 | Window and top-N **per source/chain** |
| `WATCHLIST_HOURS`, `WATCHLIST_LIMIT` | 24 / 100 | Recheck tokens that disappeared |
| `HISTORY_OBSERVATIONS`, `MINIMUM_HISTORY_OBSERVATIONS` | 6 / 3 | Moving volume baseline |
| `HISTORY_MAX_GAP_SECONDS` | 900 | Reject stale/discontinuous history |
| `ACCELERATION_CAP` | 20 | Maximum displayed/scored ratio |
| `VOLUME_ACCELERATION_THRESHOLD`, `TX_ACCELERATION_THRESHOLD` | 1.5 / 1.5 | Activity ratios |
| `HOT_RANK_IMPROVEMENT` | 10 | Minimum rank climb for bonus |
| `BASE_MIN_HOURS`, `BASE_SUFFICIENT_HOURS` | 24 / 72 | Base and duration bonus |
| `BASE_MAX_RANGE_RATIO`, `KLINE_LOOKBACK_HOURS` | 0.25 / 168 | Base width and candle window |
| `HTTP_TIMEOUT_SECONDS`, `HTTP_ATTEMPTS` | 20 / 3 | Bounded read retries |
| `REQUEST_SPACING_SECONDS`, `RETRY_MAX_WAIT_SECONDS` | 1.5 / 10 | Shared quota pacing |
| `MIN_TX_5M_FOR_ACCELERATION`, `MIN_TX_1H_FOR_ACCELERATION` | 10 / 60 | Matching-window transaction floors |
| `MIN_VOLUME_5M_FOR_ACCELERATION` | 2000 | USD 5m volume floor |
| `MIN_VOLUME_5M_LIQUIDITY_RATIO`, `MIN_VOLUME_1H_LIQUIDITY_RATIO` | 0.01 / 0.06 | Matching-window volume/liquidity floors |
| `VOLATILITY_COMPRESSION_THRESHOLD` | 0.25 | Small Setup bonus threshold |
| `BASE_MATURITY_HOURS` | `[6,12,24,48,72]` | Progressive maturity boundaries |
| `BASE_MATURITY_FRACTIONS` | `[0.3333333333333333,0.5333333333333333,0.8,1]` | Fractions of the base weight |
| `STRONG_BASE_MIN_HOURS`, `CONFIRMED_BASE_MIN_HOURS` | 48 / 72 | Maturity for top stages |
| `WATCH_SCORE_THRESHOLD`, `EARLY_REVIVAL_SCORE_THRESHOLD`, `REVIVING_SCORE_THRESHOLD` | 40 / 60 / 70 | Stage gates, with structural requirements |
| `STRONG_REVIVAL_SCORE_THRESHOLD`, `CONFIRMED_REVIVAL_SCORE_THRESHOLD` | 80 / 85 | Top-stage score gates |
| `SECURITY_CACHE_TTL_SECONDS`, `KLINE_CACHE_TTL_SECONDS` | 1800 / 900 | Persistent slow-data cache settings |
| `MAX_MARKET_ENRICH_PER_SCAN`, `MAX_SECURITY_ENRICH_PER_SCAN`, `MAX_KLINE_FETCH_PER_SCAN` | 40 / 8 / 12 | Operation budgets; retries still use shared HTTP pacing |
| `WATCHLIST_HIGH_SCORE_INTERVAL_SECONDS`, `WATCHLIST_NORMAL_INTERVAL_SECONDS` | 150 / 600 | Due polling tiers |
| `WATCHLIST_EXPIRE_HOURS` | unset | Uses `WATCHLIST_HOURS` when unset |

Advanced knobs are in `config.py`: holder retention (0.9), new-low tolerance (0.03),
breakout margin (0.01), retest tolerance (0.02), concentration (0.5), insider/dev
holding-ratio decreases (0.05/0.02), liquidity decrease (0.2), sniper/bundler thresholds
(0.3/0.3), and SQLite busy timeout (5000 ms). Their uppercase names are environment variables.
If increasing the scan interval, also increase `HISTORY_MAX_GAP_SECONDS` accordingly.

## How the scanner decides

```text
Official GMGN Hot Searches + Trending
                 ↓ merge by (chain, address)
Cheap prefilter (known failures only) → Token info → Market filter
                 ↓ history / returning activity
Closed candles when needed → Security for serious candidates
Deterministic Revival Score + explanations
                 ↓ eligible + threshold + durable cooldown
Telegram (or dry-run log)
```

1. Fetch both rankings independently for each chain. A failed source/chain does not
   cancel other work. Solana addresses preserve case; EVM addresses normalize to lowercase.
2. Revisit recent discoveries for up to 24 hours even after they leave rankings, using
   high/medium/low polling tiers and scan-wide enrichment budgets. Deferred candidates
   remain due for later scans. Reset
   live ranks/metrics; only historical ATH cap and identity survive until refreshed.
3. Skip enrichment for known discovery values that fail existing thresholds. Missing
   discovery fields pass this cheap gate. Fetch token info for survivors, retain nullable
   values, and save market snapshots. Required market filter data missing means
   **ineligible**, not a zero measurement.
4. Compute current/prior volume and transaction ratios for 5m/1h. The 5m volume score
   uses the mean of up to six previous observations, requiring at least three. Nulls
   are excluded; a zero baseline yields unknown, not infinity. Ratios cap at 20.
   Duplicate, too-close, stale and discontinuous snapshots do not warm up the baseline.
5. For market filter survivors with returning activity, use hourly closed candles.
   Walk backward through a contiguous
   narrow range, reserving two final candles for breakout/retest checks. Count actual
   duration; gaps above 90 minutes break the base. A major new low invalidates it.
   Three-candle swing points identify higher lows/highs; volatility compression compares
   normalized candle ranges in the two halves. This is a deliberately approximate pattern.
6. Fetch security for candidates whose base, activity and optimistic score can qualify
   for an alert, then compute all scoring dimensions with fresh security. Retain warnings
   when optional security is unavailable. Award points and explain every contribution.
   **Alerts also require a detected base,
   returning volume or transactions, and no known dangerous security flag.** First
   Hot Search appearance is reported without inventing a prior rank or awarding points.
7. Reserve alerts transactionally before sending. Successful alerts enforce cooldown
   across restarts; a +10 increase over the highest recent sent score permits re-alerting.
   Pending/uncertain deliveries block re-alerting for the cooldown even at higher scores.
   Rejected sends may retry next scan; transport/5xx uncertainty is not immediately retried
   because Telegram has no idempotency key. A crash can therefore miss an alert instead of
   duplicating one. Dry-run stores snapshots but creates no alert reservations.
8. Persist each alert's price, market cap, scores, stage and preset/revision atomically.
   Once delivery is confirmed, observe **+1h, +6h, +24h and +72h** outcomes. Reuse
   fresh market polls; any extra checkpoint polls share the market request budget.
   Tracking continues after watchlist expiry on enabled chains. See [outcome tracking](docs/outcomes.md).

Each completed scan records operation timings, endpoint attempts including retries,
and candidate counts. Open **Health → Performance / Funnel** to inspect the latest
scan. See [scanner performance and measured benchmarks](docs/scanner-performance.md)
for timing definitions, limitations and reproducible Solana dry runs.
[Cache, budget and watchlist settings](docs/scanner-caching.md) describe persistent
security/candle reuse, candidate priority, the schema migration and repeat-scan benchmarks.

### Post-alert learning

**Health → Outcomes** summarizes the last seven days of confirmed deliveries at each
checkpoint: observations, available returns, median return, pending and missed checks.
The first usable snapshot within one hour after a checkpoint wins; actual due and
observation times are stored. Price return is `(later_price / alert_price − 1) × 100`.
Missing or zero initial prices produce unknown returns, never fabricated zero returns.
Unique alert/horizon keys prevent duplicates across scans and restarts. These are
sampled market observations, not executable returns or a trading/backtest engine.

### Final Solana benchmark

[Final measured validation](docs/final-validation.md) records a fresh Solana-only
live dry run and repeated public-response replay with endpoint counts, stage timing,
cache reuse, candidate counts and limitations. Both use isolated temporary databases
and disable Telegram delivery. To reproduce, use the benchmark commands documented
there; do not copy credentials into command lines or artifacts.

### Score rules

Setup, Trigger and Confirmation are each normalized to 0–100. Setup measures
resurrection fit and progressive base quality; Trigger measures meaningful activity
and current attention; Confirmation measures higher lows/highs, breakout and retest.
The overall score combines them with configurable default weights **30:40:30**, then
subtracts existing risk penalties. Failed essential filters still cap it at 39.

Volume/TX acceleration bonuses require matching-window absolute floors. A 2 → 4 TX
surge cannot earn the transaction bonus. Volume is also measured relative to liquidity;
missing or zero liquidity does not become a fabricated zero ratio. Detected bases earn
progressive credit at 6/12/24/48 hours once `BASE_MIN_HOURS` (24h by default)
passes, with a maturity bonus at 72 hours by default.
Compression adds a small Setup component.

New stages are `IGNORE`, `WATCH`, `EARLY_REVIVAL`, `REVIVING`, `STRONG_REVIVAL`, and
`CONFIRMED_REVIVAL`. Numeric thresholds are necessary but insufficient: strong stages
require a mature base, meaningful activity and chart confirmation. Off-ranking top
stages additionally require both volume and TX acceleration plus HL + HH and breakout
or retest. Existing stored stages and scores remain unchanged.

See [scoring dimensions, evidence gates and configuration](docs/scoring.md) for the
formula, defaults, synthetic examples and compatibility details. Override component
or dimension weights with `WEIGHTS__BASE=20`, `WEIGHTS__SETUP_DIMENSION=30`, etc.
The alert threshold remains 75 (Balanced 65, Broad 60); alert delivery also requires
core filters, a detected base, meaningful returning activity, and no known danger.

## Commands

```bash
revival-radar run
revival-radar scan-once
revival-radar test-telegram
revival-radar settings
revival-radar health --hours 24
revival-radar inspect sol <contract-address>
revival-radar demo --database data/demo.db
```

`inspect` prints normalized metrics and score explanations as JSON without alerting.
If the token is outside the current discovery sample, ATH cap may be unavailable;
it is not synthesized from current supply. All operations are read-only on-chain.

## Docker

After creating `.env` and setting your GMGN key:

```bash
mkdir -p data
# Linux: use your host identity for the bind-mounted SQLite directory.
export RADAR_UID="$(id -u)" RADAR_GID="$(id -g)"
docker compose config --quiet
docker compose up -d --build
docker compose logs -f radar
docker compose down
```

The image runs as UID 10001 by default; the Compose override above makes `./data`
writable without broad permissions. Alternatively provision ownership for UID 10001.
SQLite persists in `./data:/app/data`. Restart policy is `unless-stopped`.
Once built, `docker compose up -d` starts the scanner. No ports are exposed.

Test the packaged application without network or credentials:

```bash
docker build -t gmgn-revival-radar .
docker run --rm --network none gmgn-revival-radar revival-radar demo
```

For cloud Docker daemons without working DNS, cache verified wheels from the working
Python environment, then build the **same Dockerfile** offline:

```bash
bash scripts/cache_docker_wheels.sh
docker build --build-arg PIP_NO_INDEX=1 -t gmgn-revival-radar .
```

The ignored cache is platform-specific. Neither TLS nor package hash checks are disabled.
If Docker cannot write its client state in a managed cloud home, set
`DOCKER_CONFIG=/workspace/.docker-radar` for those commands. Network/proxy access inside
runtime containers depends on the host; local Python remains usable when Docker egress is absent.

## Development, Dev Containers, and CI

Open this repository in VS Code and choose **Dev Containers: Reopen in Container**, or
create a GitHub Codespace. The first development file was `.devcontainer/devcontainer.json`:
Python 3.12, Git, curl, SQLite CLI, GitHub CLI, Python and Ruff extensions, pytest integration,
and dependency installation. No secrets are baked into the image.

```bash
source .venv/bin/activate
ruff check .
ruff format --check .
pytest -q
```

Tests use `httpx.MockTransport`, never live credentials: documented and observed API
shapes, nullable values, address deduplication, retry/cooldown, filters, score penalties
and boundaries, sparse candles, base/swing/retest logic, SQLite locking/restarts,
Telegram escaping/failures, CLI and end-to-end dry-run scenarios. CI runs those checks
on Python 3.12, the offline demo, and a Docker build. It does not use live API keys.

Runtime, development, and build requirements are locked with hashes in
`requirements*.lock`; `uv.lock` records project dependency resolution. To refresh:

```bash
uv lock
uv export --frozen --no-emit-project -o requirements.lock
uv export --frozen --all-extras --no-emit-project -o requirements-dev.lock
uv pip compile requirements-build.in --generate-hashes -o requirements-build.lock
```

`clients/gmgn.py` exposes a small `MarketDataSource` protocol so another documented
provider can supply discovery/enrichment/candles later without replacing scoring or storage.
SQLite initializes schema version 4 automatically, enables WAL and busy timeout,
and refuses a newer unknown schema. Version 0.2 safely migrates older schemas while preserving
snapshots, alerts, presentation payloads and cooldowns; back up with `scripts/backup_database.py` inside the existing
container before upgrading (see the upgrade guide). Diagnostic evaluations are retained
for seven days, while `data/radar-controls.json` stores nonsecret Telegram overrides.
Keep database/WAL files together when backing up
an active database, or use SQLite's backup API. Snapshot history currently has no automatic purge.

## Limitations and troubleshooting

- **401/403:** verify your read-only GMGN key, account access, clock synchronization
  (GMGN allows about ±5 seconds), and network policy. Required runtime destinations:
  `openapi.gmgn.ai` and `api.telegram.org`. Do not disable TLS verification.
- **429:** one shared request gate paces all chains and respects `Retry-After`,
  `X-RateLimit-Reset`, and `reset_at`. Long bans defer further requests to later cycles.
  Reduce discovery/watchlist limits or enable fewer chains. Slow scans exceed the target
  five minutes but never overlap; free-tier quota is finite.
- **No alerts:** check filter reasons with `inspect`. At least three prior usable volume
  observations are needed for that bonus; candles must show a ≥24h base. Missing ATH,
  age, or anti-chase price data blocks eligibility. Many trending tokens are simply too new.
- **ATH reliability:** GMGN may have incomplete or implausible ATH market caps; no supply
  adjustment or historical peak reconstruction is attempted. A score is only as good as its inputs.
- **Security:** optional metrics vary by chain. Unknown is explicitly reported and never
  presented as safe. EVM tokens are not penalized for Solana-only mint/freeze fields.
  Insider/dev decreases may reflect transfers or denominator changes, not sales.
- **Discovery bias:** only the configured top-N sample and recent watchlist are covered,
  not every token. Rank positions are within the chosen chain/window.
- **New chain explorers:** Robinhood/Arc GMGN links are provided, but unverified explorer
  URLs are omitted rather than guessing a mainnet/testnet destination.
- **Telegram rejected:** check bot channel privileges and chat ID. `test-telegram` requires
  `DRY_RUN=false`. Uncertain deliveries remain held for cooldown to avoid duplicate messages.
- **SQLite locked/permission denied:** stop duplicate workers, use a local filesystem,
  and ensure the data directory is writable by the configured Docker UID/GID.
- **Research scope:** no ML, social feeds, holder-wallet crawling, or trading. Smart-money,
  sniper and bundler aggregates are preserved when officially supplied; no wallet clustering.

For cloud onboarding, dependencies/files survive snapshots; processes do not. Restart with
`revival-radar run` after configuring credentials. An offline demo validates development
readiness, not live Telegram delivery or profit.
