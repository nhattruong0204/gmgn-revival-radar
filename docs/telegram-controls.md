# Telegram controls and strategy tuning

Version 0.2 adds private Telegram menus, persistent runtime settings, asset exclusions,
and scanner diagnostics. The existing strict thresholds remain in effect after an
upgrade until you choose a preset or edit settings. No alert count or trading outcome
is promised.

## Upgrade an existing VPS

From `/opt/gmgn-revival-radar`, run these commands in order. Keep your existing `.env`
and `data` directory. Do not copy `.env.example` over your configured file.

```bash
git pull --ff-only
# Use the currently installed image to make a consistent backup, including WAL data.
docker compose run --rm --no-deps -T radar python - < scripts/backup_database.py
docker compose build
docker compose up -d --force-recreate radar
docker compose logs --tail=80 radar
```

If a command fails, resolve it before proceeding. The new image migrates the database
to schema 2 on startup, preserving snapshots and alert cooldowns. The backup stays in
`data/`; an older image needs a restored pre-upgrade database rather than schema 2.
Keep an off-server copy of important backups.

With alerts already going to your own positive numeric `TELEGRAM_CHAT_ID`, no extra
credential is needed. Send **/menu** to your bot after the new container starts.
For a group/channel alert destination, set `TELEGRAM_OWNER_ID` to your personal numeric
Telegram user ID and recreate the container. Controls operate only in the owner's
private chat; other users and group messages cannot view or change settings.

## Menu actions

- **⚙️ Advanced configuration:** all 20 preset settings plus the scan interval, each with
  its current value, across three short pages: Market & eligibility, Activity & chart
  structure, and Delivery & discovery. Use **Next / Previous** to move between pages.
  Tap a setting, send a number, review the old/new values, then
  **Confirm**. No code change, `.env` edit, or restart is needed for subsequent tuning.
  These edits customize the running configuration; they do not rewrite the named
  presets. Selecting a preset later replaces the values that preset controls.
- **Strategy presets:** preview Strict, Balanced, or Broad, then confirm.
- **Token filters:** age, market cap, liquidity, holders, drawdown, volume, price change.
- **Base / ratios:** base duration/range, acceleration, holder retention, concentration.
- **Alerts:** score threshold, cooldown, score increase, dry-run, pause, daily summary.
- **Discovery:** source limits, watchlist limits/lifetime, API pacing, scan/history intervals.
- **Chains:** enable or disable individual supported chains, retaining at least one.
- **Asset exclusions:** stocks, stablecoins, and wrapped assets.
- **Status / Health:** current configuration, active scan revision, and recent diagnostics.
- **Reset overrides:** preview restoring all runtime changes to startup `.env` values.

The main menu puts delivery mode, strategy, score threshold and scan target above the
buttons. Icons always have text labels; enabled chains use a checkmark. Navigation
updates the existing menu where Telegram allows it, keeping the chat quieter. Value
previews and saved confirmations are separate messages. After saving, **Continue editing**
returns to the settings page you were using. **Reset custom settings** is on the final
Advanced page and still requires confirmation.

All mutations require a preview and **Confirm**. A changed revision or expired preview
cannot overwrite newer settings. Custom number entry accepts full numbers such as
`15000`, USD values such as `$15,000` or `15k`, percentages such as `60%`, multipliers
such as `1.4×`, and durations such as `150s` or `24h`, where appropriate for that field.
For ratio settings, `60%` and `0.60` are equivalent; a bare `60` is not silently converted
to a ratio. Price-change settings accept `40` or `40%` for 40%. Each prompt explains
its units. Base ranges and history intervals must remain consistent. Credentials,
owners and filesystem paths are not editable through Telegram.

Use `/start`, `/menu`, `/settings`, `/status`, `/health`, or `/cancel`. Menus sent before
a restart expire: open `/menu` again. `DRY_RUN=true` suppresses scanner alerts and daily
summaries; the bot can still answer the owner's explicit control commands.

## Suggested initial tuning

Start with **Strategy presets → Balanced → Confirm**, keep asset exclusions on, then
inspect `/health` over several days before changing more thresholds.

| Setting | Strict | Balanced | Broad |
|---|---:|---:|---:|
| Alert score | 75 | 65 | 60 |
| Minimum age | 48h | 24h | 12h |
| Market cap | $100K–$10M | $50K–$15M | $50K–$15M |
| Liquidity | $30K | $15K | $10K |
| Holders | 300 | 100 | 75 |
| ATH drawdown | 65–95% | 60–95% | 60–95% |
| 1h volume | $50K | $20K | $10K |
| Minimum base | 24h | 12h | 8h |
| Base duration bonus | 72h | 48h | 48h |
| Volume / TX acceleration | 1.5× | 1.4× | 1.4× |
| Maximum 5m / 1h price change | 30% / 75% | 40% / 100% | 40% / 100% |

All presets keep 20 discovery results per source, a 24-hour watchlist with a 100-token
limit per chain, a six-hour alert cooldown and +10 score re-alert increase. They do not
change your enabled chains, delivery mode or exclusion switches. A detected base,
returning activity, available essential metrics and no known dangerous flag remain
required. Higher lows, higher highs and breakouts are score bonuses.

Scores 60–69 are labeled `EARLY_WATCH`, 70–79 `REVIVING`, 80–89 `STRONG_REVIVAL`,
and 90–100 `HIGH_CONVICTION_REVIVAL`. Labels do not override eligibility or delivery
thresholds. Missing metrics still block the corresponding essential gates.

Increasing discovery to 50 and watchlists to 300 across five chains can produce
thousands of requests in a cycle. Request spacing is shared across chains and defaults
to 1.5 seconds. The configured scan interval is a target; a longer cycle delays the next
one. `/health` reports actual duration and missing history. Establish sufficient API
capacity before expanding the universe; the presets intentionally keep existing limits.

## Persistence and timing

Validated nonsecret overrides, revision, and Telegram update offset are stored atomically
in `data/radar-controls.json` beside the database. Effective precedence is runtime
overrides, then startup environment/`.env`, then application defaults. Only explicitly
changed values are overridden. Reset overrides before expecting a changed `.env` value
to replace a Telegram override; environment changes also require container recreation.

Each scan keeps one immutable strategy snapshot. Strategy changes and resuming alerts
apply at the next scan. Pausing alerts or enabling dry-run suppresses further alert
attempts during the current scan, though an HTTP send already in progress may complete.
Scanning and history collection continue while paused. `/status` shows configured and
active scan revisions so pending changes are visible.

Only `revival-radar run` polls Telegram. Do not run another `getUpdates` client or another
copy of this worker with the same bot token. A 409 conflict stops controls visibly while
scanning continues; it does not delete webhooks or take over another bot application.
Fix the conflicting process/webhook and restart the container. Offsets are saved before
handling updates so a crash cannot replay a settings mutation; an interrupted command
or reply may need to be sent again.

## Health reports and classification limits

Alerts lead with token, score and a readable status, followed by Market, Activity and
Structure. Score and ranking detail uses an expandable section; known risks and missing
security assessments remain visible. The contract stays copyable and links open GMGN
or the verified chain explorer. Health reports similarly lead with delivery/scan totals
and blockers, with technical source counts in expandable details. Telegram controls
fonts, spacing and theme; these native layouts work without a web app or custom CSS.

`/health` summarizes the past 24 hours: completed/unfinished scans, durations, source
successes/failures, discovery counts, unique chain/address pairs, score buckets,
rejection reasons, missing data, delivery outcomes, and candidates scoring at least 50.
Discovery observations are deduplicated within each scan; source snapshots can overlap.
The same token can have multiple rejection reasons and multiple evaluations. These are
not mutually exclusive population counts. Failed first-pass tokens have scores capped
at 39, so the aggregate rejection counters matter even if no near miss reaches 50.

New diagnostic records are kept for seven days. Existing historical snapshots are not
backfilled into scan reports, and snapshot/alert history is not automatically purged.

Daily summaries are opt-in under **Alerts → Daily summary**. Default delivery time is
09:00 `Asia/Bangkok` (UTC+7), configurable with `DAILY_SUMMARY_HOUR` and `REPORT_TIMEZONE`.
If enabled after that hour, the first report may send immediately. Delivery is attempted
at most once per local calendar day, including failed or uncertain attempts, and is
suppressed during dry-run or pause. You can still request `/health` manually.

Exclusions use recognized asset metadata when returned, exact known contract identities,
and narrow name/platform evidence. A ticker by itself is never enough. The reported
GOOGL contract on Robinhood is explicitly classified as a tokenized stock; Robinhood
native projects remain eligible. GMGN does not guarantee a full asset taxonomy on these
routes, so unknown assets remain eligible and unrecognized stock wrappers can still
appear. Classification is a screening hint, not verified financial metadata.

Read-only CLI checks:

```bash
docker compose run --rm --no-deps radar revival-radar settings
docker compose run --rm --no-deps radar revival-radar health --hours 24
docker compose run --rm --no-deps radar revival-radar health --hours 24 --json
```

The GMGN [Telegram interface reference](https://docs.gmgn.ai/index/tg-wallet-import-export-private-key-deposit-withdraw)
informed the button navigation. Radar controls manage monitoring parameters only; they
do not import wallets or handle trading keys.
