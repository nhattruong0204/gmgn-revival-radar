# Performance metrics

Authoritative definitions for the numbers OnchainFlow shows after an alert. All are
research observations derived from saved scanner snapshots (about one per scan,
roughly every five minutes). None is an executable return, realized P&L, backtest,
forecast or recommendation: they ignore fees, slippage, liquidity depth, latency and
position sizing. UI labels come from one constant set, `web/src/labels.ts`.

## Market cap as stored

`TokenSnapshot.market_cap` and `ath_market_cap` come from two GMGN sources
(`src/revival_radar/clients/normalization.py`):

| Stored value | Source |
|---|---|
| `market_cap` (rank row) | GMGN rank `market_cap`, parsed as-is (`normalization.py`, `"market_cap": number(raw.get("market_cap"))`) |
| `market_cap` (after token-info enrichment) | **overwritten** with `price.price x circulating_supply` from token info (`enriched_token`), so it is a circulating-supply market cap |
| `ath_market_cap` | GMGN rank `history_highest_market_cap`, provider value; never recomputed from price |

Circulating vs fully diluted (FDV):

- Enriched snapshots: circulating, by construction (`circulating_supply`). The code never
  reads a total or max supply field.
- Rank-only snapshots: **unverified.** GMGN's documentation and the repository do not
  say whether the rank `market_cap` uses circulating or total supply. The only saved
  payload, `tests/fixtures/gmgn.json`, is synthetic, and in it rank `market_cap`
  (820000) equals price (0.00082) x `circulating_supply` (1,000,000,000), which is
  consistent with circulating but proves nothing for live data.
- Evidence that would settle it: for real saved tokens whose rank row and token-info
  row were observed together, compare rank `market_cap` against `price x
  circulating_supply` and against `price x total_supply`. This needs saved raw GMGN
  payloads (not retained today) or an explicitly approved provider read.
- Consequence: `ath_market_cap` and the drawdown from it use the provider's basis,
  which is unverified. A snapshot may also switch basis if enrichment succeeds on one
  scan and not the next.

## Headline: market-cap change since call

`(latest_market_cap / call_market_cap - 1) * 100`, from the alert-time saved market cap
and the latest saved observation. Shown as **Market cap change since call**. Unknown
when either market cap is missing or the call value is not positive (never 0%).
Computed server-side by issue #62.

## Checkpoint: +1h / +6h / +24h / +72h price change

`(later_price / initial_price - 1) * 100`, using the first usable saved observation in
`[due_at, due_at + 1h]` (see [outcome tracking](outcomes.md) and
[checkpoint cohorts](track-record.md)). Labelled **price change**, for example
"+6h price change". The CSV columns `return_pct` and `reported_return_pct` keep their
names for contract stability; the export metadata `price_units` states that they are
price change in percent, not market-cap change. Price quote currency is unknown.

## Why the two can differ

- Circulating supply can change between call and observation (unlocks, burns,
  provider revisions), moving market cap without moving price.
- Basis mixing: rank-sourced and enriched market caps may use different supply
  figures (see above).
- Provider rounding and decimal precision on price, supply and market cap.
- Different observation times: the latest market-cap observation is generally later
  than a checkpoint snapshot, and each checkpoint is the first snapshot inside its
  window, not the exact due time.

Differences of one or two percentage points are therefore expected and are not a
data error. Compare like with like: market cap against market cap, price against price.

## MFE / MAE

Maximum favorable / adverse excursion are **observed** extremes. The headline MFE/MAE
(implemented in #62 / PR #82) is computed on **market cap** relative to the call market
cap, from saved snapshots strictly after the call time. Snapshots are about five
minutes apart, so true intrabar highs and lows are not seen and real extremes may be
larger. Gaps in scanning (downtime, budget exhaustion) widen this.

The MFE can be negative when the token only fell after the call; it is shown as
observed and is not clamped to zero. Because market cap may mix supply bases across
snapshots (rank-sourced vs enriched), an excursion can partly reflect a basis change;
this is tracked in #81.

## Unknown handling

Missing, zero or non-finite inputs give an unknown value, shown as "Unknown" or a dash
and excluded from medians and counts of available values; never treated as 0%. A
checkpoint without usable price arithmetic is unavailable or unresolved. Pending and
missed checkpoints are reported separately. Aggregates withhold values for small or
incompatible samples as described in the track-record documentation.
