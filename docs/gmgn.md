# Official GMGN integration research

Checked 2026-10-06 against the official
[GMGNAI/gmgn-skills](https://github.com/GMGNAI/gmgn-skills) repository at
[`4575ef539e6a3115fa0481d41285cb79e77970bf`](https://github.com/GMGNAI/gmgn-skills/tree/4575ef539e6a3115fa0481d41285cb79e77970bf).
The docs website was initially blocked by the cloud network policy; the official
source repository supplied the endpoint, authentication and response documentation.

Primary sources:

- [Readme / API key setup](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/Readme.md)
- [Official OpenApiClient](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/src/client/OpenApiClient.ts)
- [Host configuration](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/src/config.ts)
- [Authentication timestamp/UUID](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/src/client/signer.ts)
- [Market interface and fields](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/skills/gmgn-market/SKILL.md)
- [Token/security fields](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/skills/gmgn-token/SKILL.md)
- [Field caveats](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/skills/gmgn-token-buy/references/fields.md)

These were read as interface references; this project implements no trading commands
and does not install or run the official trading skills. The two direct dependencies
are `httpx` and `pydantic-settings` (plus their transitive dependencies).

## Requests

Host: `https://openapi.gmgn.ai`. Each call has header `X-APIKEY` and query parameters
`timestamp` (Unix seconds, fresh for every retry) and `client_id` (new UUID per attempt).
Read-only routes do not require Ed25519 signing or a wallet private key.

| Endpoint | Parameters / body | Used for |
|---|---|---|
| `POST /v1/market/hot_searches` | `{"params":[{"chain":"sol","interval":"1h","limit":20}]}` | Search-interest discovery/ranks |
| `GET /v1/market/rank` | `chain,interval,limit` | Optional trading-activity discovery/ranks; disabled by default |
| `GET /v1/token/info` | `chain,address` | Current price, supply, liquidity, holders, activity |
| `GET /v1/token/security` | `chain,address` | Optional security/supply aggregates |
| `GET /v1/market/token_kline` | `chain,address,resolution=1h,from,to` | Closed hourly candles |

K-line `from`/`to` are **milliseconds on the HTTP API**. The official CLI accepts
seconds and multiplies by 1000 before making its request. We do the same conversion.

As of 2026-10-09, live discovery and token inspection use Hot Search only by default.
`TRENDING_DISCOVERY_ENABLED=true` explicitly restores Trending after a restart.
Legacy Trending fields remain readable for historical records. Off-list watch tokens
are refreshed without stale current ranking scores, and the selected discovery mode
is included in the recorded configuration context.

The official validator and command docs include `sol,bsc,base,robinhood,arc` for
these operations. Other GMGN-supported chains are deliberately outside this MVP.
Discovery intervals: `1m,5m,1h,6h,24h`. Token-info metrics may expose all five windows.
The candle interface supports `1s` (Pro), `30s,1m,5m,15m,1h,4h,1d`; V1 uses only `1h`.

## Response mapping

| Stored field | Official field / calculation |
|---|---|
| Address and identity | rank `address,chain,symbol,name` |
| Hot Search rank | `data[]` block `tokens[].rank`, within chain and interval |
| Trending rank | `data.rank[].rank`; row position if rank missing |
| Market cap | rank `market_cap`; token info `price.price × circulating_supply` |
| ATH market cap | rank `history_highest_market_cap` |
| Drawdown | `1 − current_market_cap / ath_market_cap` |
| Liquidity | rank/info `liquidity` (reported total pool liquidity, not executable depth) |
| Age | current time minus info/rank `creation_timestamp` in seconds; zero means unknown |
| Holders | `holder_count` |
| Window volume | info `price.volume_{window}`; rank `volume` for its requested interval |
| Window transactions | info `price.swaps_{window}`; rank `swaps` for its interval |
| Buys/sells | info `price.buys_{window}`, `price.sells_{window}` |
| Price change | `(price.price / price.price_{window} − 1) × 100` in percentage points |
| Candle USD volume | `amount`, **not** `volume` (the latter is base-token units) |
| Top10 concentration | security `top_10_holder_rate`, info `stat.top_10_holder_rate` |
| Developer holdings | info `stat.creator_hold_rate`, security `creator_balance_rate` |
| Insider holdings | security `suspected_insider_hold_rate` |
| Sniper holdings | rank/info-stat `top70_sniper_hold_rate` |
| Bundler aggregate | security `bundler_trader_amount_rate`, rank `bundler_rate` |
| Smart money holders | info `wallet_tags_stat.smart_wallets`, rank `smart_degen_count` |
| Mint/freeze authorities | security `renounced_mint`, `renounced_freeze_account`, **Solana only** |
| LP lock/burn | `lock_summary.lock_percent`, explicit `burn_status=burn` |
| Dangerous flags | explicit honeypot, cannot-sell, wash-trading or active Solana authorities |

The optional aggregates are not a complete holder census. The official API also offers
`/v1/market/token_top_holders` and `/v1/market/token_top_traders` (weight 5 each), but V1
does not call them: the existing aggregate fields keep the project and quota use small.
Insider-volume fractions are not mislabeled as insider-holding fractions. A lower
developer/insider holding ratio is only a distribution proxy; it does not prove selling.

`ath_price × current_supply` is **not** used as a replacement for historical ATH cap.
Ranking price-change fields have conflicting ratio/percentage wording across official
references, so the adapter computes percentage points from token-info starting prices.
Zero starting prices, zero creation times, null/empty/nonfinite/negative measurements
are treated as unavailable. Unknown concentration, authorities, and security flags
are never converted to reassuring zero/false values. EVM mint/freeze placeholders
are ignored. An absent lock ratio is not assumed to mean burned or safely locked.

## Live findings and compatibility

Live read-only probes using GMGN's documented public demo key confirmed both discovery
sources on all five chains. Token-info and security responses supplied usable market
metrics. Two differences from the reference tables required compatibility handling:

1. Trending can return a **second `{code,data,...}` envelope** within the outer `data`.
   The adapter validates both success codes and handles documented single envelopes too.
2. Candle `time` was **Unix milliseconds**, while the table describes seconds. The
   parser handles both, normalizes to seconds, and excludes the current incomplete bar.

Both cases have offline regression tests. Very new tokens may have only one or two
candles; this is insufficient for a base and must not be represented as a long base.
These live checks demonstrate interface access, not complete coverage on every token,
long-term reliability, or a guarantee that a particular account has the same entitlement.
Personal GMGN credentials and a real Telegram destination remain user configuration.

## Rate limits and future adapters

The official free-tier bucket is documented as rate/capacity 5/5; token info/security
weight 1, standard candles 2, and rankings 3. A shared 1.5-second request gate limits
bursts across all chains. Retries use fresh authentication, bounded backoff, and shared
cooldowns from `Retry-After`, `X-RateLimit-Reset`, or `reset_at`. Long cooldowns defer
work rather than sleeping through the entire scan or repeatedly extending a ban.
401/403 and malformed envelopes are not retried unchanged.

Adapters implement `MarketDataSource.discover`, `.enrich`, and `.candles`. Future
documented providers can fill gaps without adding browser endpoint dependencies.
Required filter gaps suppress alerts; optional security gaps produce warnings.

## Insider holdings audit (2026-10-07)

Re-fetched the official pinned [token/security documentation](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/skills/gmgn-token/SKILL.md)
and [market documentation](https://github.com/GMGNAI/gmgn-skills/blob/4575ef539e6a3115fa0481d41285cb79e77970bf/skills/gmgn-market/SKILL.md).
The security field `suspected_insider_hold_rate` is documented as the ratio **held**
by suspected insider wallets. `rat_trader_amount_rate` and token-info
`stat.top_rat_trader_percentage` describe **trading volume**, not holdings.
They cannot substitute for an unavailable holding ratio. Official docs also describe
[insider traders and snipers](https://docs.gmgn.ai/index/insider-traders-snipers-first-70-buyers).

The adapter accepts only an explicitly supplied finite holding fraction in [0,1].
Missing values remain NULL and no insider-decrease penalty is inferred without known
current/prior holdings. Health → Data Quality reports Insider availability alongside
Top10, Dev, Sniper and Bundler. A 0% coverage reading means no evaluated observation
supplied the field; it does **not** mean tokens had zero insider holdings. Compact and
full alert watchouts omit the repetitive missing-insider note; known risk deductions
and unavailable security assessments remain visible. Legacy alert payloads are unchanged.
