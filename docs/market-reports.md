# Solana market reports

**📈 Market report** in `/menu`, or `/market` and `/report`, first asks for a
timeframe: **1h, 4h, 12h, 24h, 72h or 7d**. The reply shows up to ten distinct
Solana contract addresses observed by this bot during that window. No minimum
alert score or eligibility requirement applies. Empty windows still return a report.

## Ranking and evidence

Each ticker ranks by its **highest saved overall score in the selected window**.
Ties use peak confirmation score, trigger score, observation time and contract
address, in that order. Missing historical dimension scores stay unknown.
The summary also shows the **latest saved score in that same window**, its stage,
observation age, sample count, latest setup/trigger/confirmation scores, market cap
and liquidity when available. Latest does not mean a new live quote.
Scheduled observation ages are measured when the report is created, so a late
startup report cannot present an older window's observations as fresh.

Known risks at the peak or latest observation remain visible even when a ticker has
a high score. Blocker and missing-field counts remain visible; space permitting,
the summary includes a blocker, a missing field and a data warning. **Peak evidence**
and **Latest evidence** open the corresponding saved evaluation's complete recorded
blocker, missing-field and warning lists with page navigation. **Full details** opens
the saved token and scoring view when its payload is available; legacy evaluations
can still expose their recorded evidence lists.
The GMGN button opens the exact Solana contract, so duplicate symbols remain distinct.

Only evaluations belonging to scans completed by the report cutoff are included.
The window is **(start, cutoff]**; an evaluation exactly at the start belongs to the
preceding period. Running scans and scans finishing after the cutoff are excluded
and reported as incomplete coverage. A scan starting before the window can still
contribute evaluations collected within the window.

Reports describe the bot's tracked universe, rather than the entire Solana market.
Partial windows, sparse observations, stale latest observations and mixed scoring
configurations are labeled. A score is a heuristic, not a return probability;
narratives and catalysts are unassessed. These reports help manual review and do
not change revival eligibility, signal alerts or trading behavior.

## Automatic delivery

With `MARKET_REPORT_ENABLED=true` (the default), the running service attempts a
report at **00:00, 04:00, 08:00, 12:00, 16:00 and 20:00** in `REPORT_TIMEZONE`.
The default `Asia/Bangkok` is UTC+7. Every scheduled report covers the preceding
four actual hours ending at that boundary. The background scheduler checks every
30 seconds independently of scan completion and the optional daily health summary.

Scheduled reports go to the existing `TELEGRAM_CHAT_ID`. Manual reports require
the existing owner authorization and use the owner's private control chat.
`ALERTS_PAUSED` and signal score thresholds do not suppress market reports.
`DRY_RUN=true`, missing Telegram delivery credentials, or
`MARKET_REPORT_ENABLED=false` suppress automatic delivery. Manual reports remain
available. Changing the environment flag requires container recreation.

On startup the service attempts only the latest due period, then follows the
schedule. It does not replay an outage's entire backlog. Each period is durably
reserved before sending, and its evidence snapshot is persisted. Restarts or an
uncertain Telegram response cannot send that period again. A failed or uncertain
attempt is recorded and is not retried automatically; the next period remains
scheduled. Telegram delivery therefore depends on service availability and successful
delivery, rather than a guaranteed arrival time.

## Storage and API use

Both manual and automatic reports read the existing SQLite evaluations and saved
presentation payloads. They make **no GMGN or Helius requests** and do not start a
scan, reserve a trading alert, modify thresholds or update the runtime revision.
Seven days is the maximum manual lookback because evaluation diagnostics and report
snapshots follow the existing seven-day retention. Legacy evaluations without valid
saved details can retain their historical score, with evidence marked unavailable.

The feature adds an evaluation lookup index to schema 4; it does not change the
database schema version. Scheduled delivery status is stored in
`state.market_report_delivery`; period claims and snapshots use `market_report:*`
state keys. Continue using consistent SQLite backups when upgrading the VPS.
